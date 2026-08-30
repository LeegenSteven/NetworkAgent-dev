"""Loopback-only liveness, readiness, and version endpoints.

These probes deliberately stay outside the A2A JSON-RPC surface.  Liveness
does not touch a dependency, readiness performs one bounded repository read,
and version returns only fixed allowlisted contract metadata.  None of the
three endpoints authorizes an action or attests a Cloud deployment.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from telco_domain import SCHEMA_VERSION, SensitiveDataError, assert_model_safe
from telco_lab import REPLAY_SCHEMA_VERSION

from .governance_http import _BoundaryFailure, _require_loopback_host
from .version import LOCAL_HTTP_API_VERSION, PACKAGE_VERSION


MAX_LOCAL_STATUS_RESPONSE_BYTES = 4 * 1024
LOCAL_READINESS_TIMEOUT_SECONDS = 1.0
_STATUS_HTTP_METHODS = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
)


class _ReadinessRepository(Protocol):
    async def list(
        self,
        *,
        status: object | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[object]: ...


_ERRORS: dict[str, tuple[int, str]] = {
    "LOCAL_SERVICE_BAD_HOST": (403, "A loopback Host is required."),
    "LOCAL_SERVICE_METHOD_NOT_ALLOWED": (405, "Only GET is supported."),
    "LOCAL_SERVICE_INVALID_REQUEST": (422, "The request is invalid."),
    "LOCAL_SERVICE_NOT_READY": (503, "The local service is not ready."),
    "LOCAL_SERVICE_INTERNAL": (500, "The local service probe failed."),
}


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        assert_model_safe(payload)
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (SensitiveDataError, TypeError, UnicodeError, ValueError):
        raise RuntimeError("unsafe status response") from None
    if len(body) > MAX_LOCAL_STATUS_RESPONSE_BYTES:
        raise RuntimeError("oversized status response")
    return body


def _response(payload: Mapping[str, object], *, status_code: int) -> Response:
    return Response(
        _json_bytes(payload),
        status_code=status_code,
        media_type="application/json",
    )


def _error(code: str) -> Response:
    status_code, message = _ERRORS[code]
    return _response(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=status_code,
    )


def _request_boundary(request: Request) -> Response | None:
    try:
        _require_loopback_host(request)
    except _BoundaryFailure:
        return _error("LOCAL_SERVICE_BAD_HOST")
    if request.method != "GET":
        return _error("LOCAL_SERVICE_METHOD_NOT_ALLOWED")
    if request.scope.get("query_string", b""):
        return _error("LOCAL_SERVICE_INVALID_REQUEST")
    return None


class LocalServiceStatusApi:
    """Read-only operational probes for the supported direct loopback runner."""

    def __init__(self, repository: _ReadinessRepository) -> None:
        if not callable(getattr(repository, "list", None)):
            raise TypeError("repository must expose a bounded list operation")
        self._repository = repository
        self._readiness_task: asyncio.Task[Sequence[object]] | None = None

    @staticmethod
    def _blocking_read(repository: _ReadinessRepository) -> Sequence[object]:
        # The Local DuckDB adapter deliberately exposes an async repository
        # contract but performs its bounded read synchronously.  Running that
        # coroutine in a worker thread keeps the HTTP event loop responsive.
        return asyncio.run(repository.list(status=None, limit=1, offset=0))

    def _readiness_finished(self, task: asyncio.Task[Sequence[object]]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
        if self._readiness_task is task:
            self._readiness_task = None

    async def _readiness_snapshot(self) -> Sequence[object]:
        active = self._readiness_task
        if active is not None and not active.done():
            raise TimeoutError
        task = asyncio.create_task(
            asyncio.to_thread(self._blocking_read, self._repository)
        )
        self._readiness_task = task
        task.add_done_callback(self._readiness_finished)
        return await asyncio.wait_for(
            asyncio.shield(task),
            timeout=LOCAL_READINESS_TIMEOUT_SECONDS,
        )

    async def healthz(self, request: Request) -> Response:
        failure = _request_boundary(request)
        if failure is not None:
            return failure
        return _response(
            {
                "ok": True,
                "data": {
                    "service": "telco-assurance-agent",
                    "status": "alive",
                },
            },
            status_code=200,
        )

    async def readyz(self, request: Request) -> Response:
        failure = _request_boundary(request)
        if failure is not None:
            return failure
        try:
            result = await self._readiness_snapshot()
            if (
                not isinstance(result, Sequence)
                or isinstance(result, (str, bytes, bytearray))
                or len(result) > 1
            ):
                raise RuntimeError("invalid readiness result")
        except Exception:
            return _error("LOCAL_SERVICE_NOT_READY")
        return _response(
            {
                "ok": True,
                "data": {
                    "service": "telco-assurance-agent",
                    "status": "ready",
                    "profile": "local",
                    "repository": "ready",
                },
            },
            status_code=200,
        )

    async def version(self, request: Request) -> Response:
        failure = _request_boundary(request)
        if failure is not None:
            return failure
        return _response(
            {
                "ok": True,
                "data": {
                    "service": "telco-assurance-agent",
                    "package_version": PACKAGE_VERSION,
                    "local_http_api_version": LOCAL_HTTP_API_VERSION,
                    "replay_schema_version": REPLAY_SCHEMA_VERSION,
                    "domain_schema_version": SCHEMA_VERSION,
                },
            },
            status_code=200,
        )


def status_routes(repository: _ReadinessRepository) -> tuple[Route, ...]:
    api = LocalServiceStatusApi(repository)
    return (
        Route(
            "/local/v1/healthz",
            endpoint=api.healthz,
            methods=_STATUS_HTTP_METHODS,
            name="local-service-health",
        ),
        Route(
            "/local/v1/readyz",
            endpoint=api.readyz,
            methods=_STATUS_HTTP_METHODS,
            name="local-service-readiness",
        ),
        Route(
            "/local/v1/version",
            endpoint=api.version,
            methods=_STATUS_HTTP_METHODS,
            name="local-service-version",
        ),
    )


__all__ = [
    "LOCAL_READINESS_TIMEOUT_SECONDS",
    "MAX_LOCAL_STATUS_RESPONSE_BYTES",
    "LocalServiceStatusApi",
    "status_routes",
]
