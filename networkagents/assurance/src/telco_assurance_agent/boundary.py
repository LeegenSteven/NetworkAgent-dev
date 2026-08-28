"""Non-reflective ASGI gate in front of the A2A SDK request parser."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from a2a.types import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetAuthenticatedExtendedCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    JSONRPCRequest,
    ListTaskPushNotificationConfigRequest,
    SendMessageRequest,
    SendStreamingMessageRequest,
    SetTaskPushNotificationConfigRequest,
    TaskResubscriptionRequest,
)
from starlette.responses import JSONResponse
from telco_domain import SensitiveDataError, assert_model_safe


MAX_A2A_REQUEST_BYTES = 300_000
MAX_A2A_JSON_DEPTH = 32

_METHOD_MODELS = {
    model.model_fields["method"].default: model
    for model in (
        SendMessageRequest,
        SendStreamingMessageRequest,
        GetTaskRequest,
        CancelTaskRequest,
        SetTaskPushNotificationConfigRequest,
        GetTaskPushNotificationConfigRequest,
        ListTaskPushNotificationConfigRequest,
        DeleteTaskPushNotificationConfigRequest,
        TaskResubscriptionRequest,
        GetAuthenticatedExtendedCardRequest,
    )
}


class _UnsafeRequest(ValueError):
    pass


def _reject_constant(_: str) -> None:
    raise _UnsafeRequest("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _UnsafeRequest("duplicate JSON object key")
        result[key] = value
    return result


def _depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, level = stack.pop()
        maximum = max(maximum, level)
        if maximum > MAX_A2A_JSON_DEPTH:
            return maximum
        if isinstance(current, Mapping):
            stack.extend((nested, level + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            stack.extend((nested, level + 1) for nested in current)
    return maximum


def _validate_jsonrpc_body(raw: bytes) -> None:
    if not raw or len(raw) > MAX_A2A_REQUEST_BYTES:
        raise _UnsafeRequest("invalid request size")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, _UnsafeRequest):
        raise _UnsafeRequest("invalid JSON") from None
    if _depth(value) > MAX_A2A_JSON_DEPTH:
        raise _UnsafeRequest("JSON depth exceeded")
    try:
        assert_model_safe(value)
    except SensitiveDataError:
        raise _UnsafeRequest("unsafe request") from None
    try:
        base = JSONRPCRequest.model_validate(value)
        model = _METHOD_MODELS.get(base.method)
        if model is None:
            raise _UnsafeRequest("unsupported JSON-RPC method")
        model.model_validate(value)
    except _UnsafeRequest:
        raise
    except Exception:
        # Pydantic errors retain the rejected input internally. They are
        # intentionally discarded here, before the SDK can log or serialize it.
        raise _UnsafeRequest("invalid A2A request") from None


class SafeA2ARequestBoundary:
    """Bound and prevalidate the JSON-RPC body before A2A SDK logging."""

    def __init__(self, application: Any) -> None:
        self.application = application

    @staticmethod
    async def _invalid(scope: Any, receive: Any, send: Any) -> None:
        response = JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32602,
                    "message": "Invalid parameters",
                },
            },
            status_code=200,
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if not (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/"
        ):
            await self.application(scope, receive, send)
            return

        declared_length = next(
            (
                value
                for key, value in scope.get("headers", ())
                if key.lower() == b"content-length"
            ),
            None,
        )
        if declared_length is not None:
            try:
                if int(declared_length) > MAX_A2A_REQUEST_BYTES:
                    await self._invalid(scope, receive, send)
                    return
            except ValueError:
                await self._invalid(scope, receive, send)
                return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                await self._invalid(scope, receive, send)
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await self._invalid(scope, receive, send)
                return
            size += len(chunk)
            if size > MAX_A2A_REQUEST_BYTES:
                await self._invalid(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)
        try:
            _validate_jsonrpc_body(raw)
        except _UnsafeRequest:
            await self._invalid(scope, receive, send)
            return

        replayed = False

        async def replay_receive() -> Any:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        await self.application(scope, replay_receive, send)


__all__ = [
    "MAX_A2A_JSON_DEPTH",
    "MAX_A2A_REQUEST_BYTES",
    "SafeA2ARequestBoundary",
]
