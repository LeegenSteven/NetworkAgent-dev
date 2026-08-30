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

from .business_boundary import (
    LocalBusinessOperationBoundary,
    LocalBusinessOperationBusy,
    LocalHttpRequestAdmission,
    LocalHttpRequestBodyTimedOut,
)


MAX_A2A_REQUEST_BYTES = 300_000
MAX_A2A_JSON_DEPTH = 32
A2A_BOUNDARY_BUSY_CODE = -32099
A2A_BOUNDARY_TIMEOUT_CODE = -32098

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
    except (UnicodeDecodeError, ValueError, RecursionError):
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

    def __init__(
        self,
        application: Any,
        *,
        request_admission: LocalHttpRequestAdmission | None = None,
        operation_boundary: LocalBusinessOperationBoundary | None = None,
    ) -> None:
        self.application = application
        self.request_admission = request_admission or LocalHttpRequestAdmission()
        self.operation_boundary = operation_boundary

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
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)

    @staticmethod
    async def _method_not_allowed(scope: Any, receive: Any, send: Any) -> None:
        response = JSONResponse(
            {
                "ok": False,
                "error": {
                    "code": "A2A_HTTP_METHOD_NOT_ALLOWED",
                    "message": "Method not allowed.",
                },
            },
            status_code=405,
            headers={
                "Allow": "POST",
                "Cache-Control": "no-store",
                "Connection": "close",
            },
        )
        await response(scope, receive, send)

    @staticmethod
    async def _server_error(
        scope: Any,
        receive: Any,
        send: Any,
        *,
        code: int,
        message: str,
        busy: bool = False,
    ) -> None:
        headers = {"Connection": "close"}
        if busy:
            headers["Retry-After"] = "1"
        response = JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": code, "message": message},
            },
            status_code=200,
            headers=headers,
        )
        await response(scope, receive, send)

    @staticmethod
    async def _read_and_validate(receive: Any) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                raise _UnsafeRequest("invalid ASGI request message")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise _UnsafeRequest("invalid ASGI request body")
            size += len(chunk)
            if size > MAX_A2A_REQUEST_BYTES:
                raise _UnsafeRequest("invalid request size")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)
        _validate_jsonrpc_body(raw)
        return raw

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/":
            await self.application(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await self._method_not_allowed(scope, receive, send)
            return

        declared_lengths = tuple(
            value
            for key, value in scope.get("headers", ())
            if key.lower() == b"content-length"
        )
        if len(declared_lengths) > 1:
            await self._invalid(scope, receive, send)
            return
        if declared_lengths:
            try:
                declared_length = declared_lengths[0]
                if (
                    not declared_length
                    or any(value < 48 or value > 57 for value in declared_length)
                    or int(declared_length) > MAX_A2A_REQUEST_BYTES
                ):
                    await self._invalid(scope, receive, send)
                    return
            except (TypeError, ValueError):
                await self._invalid(scope, receive, send)
                return

        try:
            admission = self.request_admission.try_acquire(
                operation_boundary=self.operation_boundary
            )
        except LocalBusinessOperationBusy:
            await self._server_error(
                scope,
                receive,
                send,
                code=A2A_BOUNDARY_BUSY_CODE,
                message="Server busy",
                busy=True,
            )
            return

        try:
            try:
                raw = await self.request_admission.read_body(
                    lambda: self._read_and_validate(receive)
                )
            except LocalHttpRequestBodyTimedOut:
                await self._server_error(
                    scope,
                    receive,
                    send,
                    code=A2A_BOUNDARY_TIMEOUT_CODE,
                    message="Request timeout",
                )
                return
            except _UnsafeRequest:
                await self._invalid(scope, receive, send)
                return

            try:
                # Keep this explicit assertion at the trust transition into
                # the SDK, even though _read_and_validate returns bytes by
                # contract.
                if not isinstance(raw, bytes):
                    raise _UnsafeRequest("invalid validated request")
            except _UnsafeRequest:
                await self._invalid(scope, receive, send)
                return

            replayed = False

            async def replay_receive() -> Any:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {
                        "type": "http.request",
                        "body": raw,
                        "more_body": False,
                    }
                return await receive()

            # The admission lease deliberately spans the complete downstream
            # call. The body deadline ends at this trust transition and must
            # never cancel an A2A operation that has already been submitted.
            await self.application(scope, replay_receive, send)
        finally:
            admission.release()


__all__ = [
    "A2A_BOUNDARY_BUSY_CODE",
    "A2A_BOUNDARY_TIMEOUT_CODE",
    "MAX_A2A_JSON_DEPTH",
    "MAX_A2A_REQUEST_BYTES",
    "SafeA2ARequestBoundary",
]
