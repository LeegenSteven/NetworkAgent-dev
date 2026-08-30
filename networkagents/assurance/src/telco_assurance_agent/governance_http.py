"""Strict loopback HTTP boundary for the Local Governance simulation.

The routes in this module are intentionally separate from the A2A JSON-RPC
surface.  They expose only a compact, privacy-checked projection of the
canonical aggregate and can invoke only :class:`LocalGovernanceEngine`, whose
sole action gateway is the side-effect-free ``LOCAL_SIMULATION`` gateway.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from ipaddress import ip_address
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    model_validator,
)
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.routing import Route
from telco_domain import SensitiveDataError, assert_model_safe
from telco_local import (
    GovernanceAuthorizationError,
    GovernanceClockError,
    GovernanceIdempotencyConflictError,
    GovernanceNotFoundError,
    GovernanceResult,
    GovernanceStateError,
    LOCAL_SIMULATION_ACTION_TYPE,
    LocalGovernanceEngine,
)

from .business_boundary import (
    LocalBusinessOperationBoundary,
    LocalBusinessOperationBusy,
    LocalBusinessOperationTimedOut,
    LocalHttpRequestAdmission,
    LocalHttpRequestBodyTimedOut,
)


MAX_GOVERNANCE_REQUEST_BYTES = 64 * 1024
MAX_GOVERNANCE_RESPONSE_BYTES = 256 * 1024
MAX_GOVERNANCE_JSON_DEPTH = 16
LOCAL_OPERATION_HEADER = "x-networkagent-local-operation"
LOCAL_OPERATION_VALUE = "governance-v1"
_STANDARD_HTTP_METHODS = (
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

OpaqueId = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
ShortText = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
Sha256Digest = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class _GovernanceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class _SafeRequestModel(_GovernanceModel):
    @model_validator(mode="after")
    def _request_is_safe(self) -> "_SafeRequestModel":
        assert_model_safe(self.model_dump(mode="json", round_trip=True))
        return self


class PrepareRequest(_SafeRequestModel):
    idempotency_key: OpaqueId
    actor: OpaqueId


class DecideRequest(_SafeRequestModel):
    idempotency_key: OpaqueId
    actor: OpaqueId
    reason: ShortText
    approve: StrictBool
    expected_action_hash: Sha256Digest
    expected_revision: NonNegativeInt


class ExecuteRequest(_SafeRequestModel):
    idempotency_key: OpaqueId
    actor: OpaqueId
    verification_passed: StrictBool


class ResourceScopeView(_GovernanceModel):
    resource_id: str
    resource_type: str
    technology: str | None = None
    parent_resource_id: str | None = None


class IncidentSummaryView(_GovernanceModel):
    incident_id: str
    status: str
    severity: str
    technology: str
    title: str
    revision: int
    scope: tuple[ResourceScopeView, ...] = ()


class RcaConclusionView(_GovernanceModel):
    report_id: str
    version: int
    status: str
    conclusion: str
    summary: str
    root_cause: str | None = None


class ActionView(_GovernanceModel):
    action_id: str
    action_type: Literal["LOCAL_SIMULATION"]
    action_hash: Sha256Digest
    risk_level: str
    scope: tuple[ResourceScopeView, ...]


class ApprovalStatusView(_GovernanceModel):
    approval_id: str
    request_id: str
    sequence: int
    status: str
    action_hash: Sha256Digest
    scope: tuple[ResourceScopeView, ...]
    requested_at: str
    expires_at: str
    decided_at: str | None = None


class ActionRunStatusView(_GovernanceModel):
    action_run_id: str
    action_hash: Sha256Digest
    status: str


class VerificationStatusView(_GovernanceModel):
    verification_id: str
    status: str
    action_run_ids: tuple[str, ...]


class GovernanceView(_GovernanceModel):
    incident: IncidentSummaryView
    rca: RcaConclusionView | None = None
    action: ActionView | None = None
    approval: ApprovalStatusView | None = None
    action_runs: tuple[ActionRunStatusView, ...] = ()
    verification: VerificationStatusView | None = None
    replayed: bool = False


class _BoundaryFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        status_code: int,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.safe_message = message
        self.headers = dict(headers or {})


_ERRORS: dict[str, tuple[int, str]] = {
    "LOCAL_GOVERNANCE_BAD_HOST": (403, "A loopback Host is required."),
    "LOCAL_GOVERNANCE_METHOD_NOT_ALLOWED": (
        405,
        "The HTTP method is not supported for this governance route.",
    ),
    "LOCAL_GOVERNANCE_OPERATION_REQUIRED": (
        403,
        "The local governance operation header is required.",
    ),
    "LOCAL_GOVERNANCE_UNSUPPORTED_MEDIA_TYPE": (
        415,
        "Content-Type must be application/json.",
    ),
    "LOCAL_GOVERNANCE_REQUEST_TOO_LARGE": (413, "The request is too large."),
    "LOCAL_GOVERNANCE_REQUEST_TIMEOUT": (
        408,
        "The governance request body timed out.",
    ),
    "LOCAL_GOVERNANCE_INVALID_REQUEST": (422, "The request is invalid."),
    "LOCAL_GOVERNANCE_NOT_FOUND": (404, "The Incident was not found."),
    "LOCAL_GOVERNANCE_STATE_CONFLICT": (
        409,
        "The Incident is not eligible for this operation.",
    ),
    "LOCAL_GOVERNANCE_AUTHORIZATION_FAILED": (
        403,
        "The exact local simulation is not authorized.",
    ),
    "LOCAL_GOVERNANCE_IDEMPOTENCY_CONFLICT": (
        409,
        "The idempotency key conflicts with an earlier request.",
    ),
    "LOCAL_GOVERNANCE_OPERATION_BUSY": (
        503,
        "The local governance operation worker is busy.",
    ),
    "LOCAL_GOVERNANCE_OPERATION_UNCERTAIN": (
        503,
        "The local governance operation is still completing.",
    ),
    "LOCAL_GOVERNANCE_INTERNAL": (500, "The local governance request failed."),
    "LOCAL_GOVERNANCE_RESPONSE_TOO_LARGE": (
        500,
        "The local governance response exceeded its safe budget.",
    ),
}


def _failure(
    code: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> _BoundaryFailure:
    status_code, message = _ERRORS[code]
    return _BoundaryFailure(
        code,
        status_code,
        message,
        headers=headers,
    )


def _enum_value(value: object) -> str:
    member = getattr(value, "value", value)
    if not isinstance(member, str):
        raise _failure("LOCAL_GOVERNANCE_INTERNAL")
    return member


def _scope(resources: Sequence[object]) -> tuple[ResourceScopeView, ...]:
    result: list[ResourceScopeView] = []
    for resource in resources:
        resource_id = getattr(resource, "resource_id", None)
        resource_type = getattr(resource, "resource_type", None)
        technology = getattr(resource, "technology", None)
        parent_resource_id = getattr(resource, "parent_resource_id", None)
        if not isinstance(resource_id, str):
            raise _failure("LOCAL_GOVERNANCE_INTERNAL")
        result.append(
            ResourceScopeView(
                resource_id=resource_id,
                resource_type=_enum_value(resource_type),
                technology=_enum_value(technology) if technology is not None else None,
                parent_resource_id=parent_resource_id,
            )
        )
    return tuple(result)


def governance_view(result: GovernanceResult) -> GovernanceView:
    """Project one aggregate into the only fields authorized for this API."""

    incident = result.incident
    report = result.report
    action = result.action
    approval = result.approval
    verification = result.verification

    if action is not None and action.action_type != LOCAL_SIMULATION_ACTION_TYPE:
        raise _failure("LOCAL_GOVERNANCE_INTERNAL")
    if approval is not None and approval.action_hash is None:
        raise _failure("LOCAL_GOVERNANCE_INTERNAL")
    if any(run.action_hash is None for run in result.action_runs):
        raise _failure("LOCAL_GOVERNANCE_INTERNAL")

    view = GovernanceView(
        incident=IncidentSummaryView(
            incident_id=incident.incident_id,
            status=_enum_value(incident.status),
            severity=_enum_value(incident.severity),
            technology=_enum_value(incident.technology),
            title=incident.title,
            revision=incident.revision,
            scope=_scope(incident.affected_resources),
        ),
        rca=(
            RcaConclusionView(
                report_id=report.report_id,
                version=report.version,
                status=_enum_value(report.status),
                conclusion=_enum_value(report.conclusion),
                summary=report.summary,
                root_cause=report.root_cause,
            )
            if report is not None
            else None
        ),
        action=(
            ActionView(
                action_id=action.action_id,
                action_type="LOCAL_SIMULATION",
                action_hash=action.action_hash,
                risk_level=_enum_value(action.risk_level),
                scope=_scope(action.target_resources),
            )
            if action is not None
            else None
        ),
        approval=(
            ApprovalStatusView(
                approval_id=approval.approval_id,
                request_id=approval.request_id,
                sequence=approval.sequence,
                status=_enum_value(approval.status),
                action_hash=approval.action_hash,
                scope=_scope(approval.scope),
                requested_at=approval.requested_at.isoformat(),
                expires_at=approval.expires_at.isoformat(),
                decided_at=(
                    approval.decided_at.isoformat()
                    if approval.decided_at is not None
                    else None
                ),
            )
            if approval is not None
            else None
        ),
        action_runs=tuple(
            ActionRunStatusView(
                action_run_id=run.action_run_id,
                action_hash=run.action_hash,
                status=_enum_value(run.status),
            )
            for run in result.action_runs
        ),
        verification=(
            VerificationStatusView(
                verification_id=verification.verification_id,
                status=_enum_value(verification.status),
                action_run_ids=verification.action_run_ids,
            )
            if verification is not None
            else None
        ),
        replayed=result.replayed,
    )
    assert_model_safe(view.model_dump(mode="json", round_trip=True))
    return view


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise _failure("LOCAL_GOVERNANCE_INTERNAL") from None


def _response(payload: Mapping[str, object], *, status_code: int) -> Response:
    try:
        assert_model_safe(payload)
    except SensitiveDataError:
        raise _failure("LOCAL_GOVERNANCE_INTERNAL") from None
    body = _json_bytes(payload)
    if len(body) > MAX_GOVERNANCE_RESPONSE_BYTES:
        raise _failure("LOCAL_GOVERNANCE_RESPONSE_TOO_LARGE")
    return Response(body, status_code=status_code, media_type="application/json")


def _error_response(failure: _BoundaryFailure) -> Response:
    payload = {
        "ok": False,
        "error": {"code": failure.code, "message": failure.safe_message},
    }
    body = _json_bytes(payload)
    headers = dict(failure.headers)
    if failure.status_code == 503:
        headers["Retry-After"] = "5"
    return Response(
        body,
        status_code=failure.status_code,
        media_type="application/json",
        headers=headers or None,
    )


def _header_values(request: Request, name: str) -> tuple[bytes, ...]:
    wanted = name.encode("ascii").lower()
    return tuple(
        value
        for key, value in request.scope.get("headers", ())
        if key.lower() == wanted
    )


def _require_loopback_host(request: Request) -> None:
    values = _header_values(request, "host")
    if len(values) != 1:
        raise _failure("LOCAL_GOVERNANCE_BAD_HOST")
    try:
        raw = values[0].decode("ascii")
        if not raw or raw != raw.strip() or len(raw) > 255:
            raise ValueError
        parsed = urlsplit("//" + raw)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise ValueError
        hostname = parsed.hostname
        if hostname is None:
            raise ValueError
        normalized = hostname.lower()
        if normalized != "localhost" and not ip_address(normalized).is_loopback:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65_535:
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        raise _failure("LOCAL_GOVERNANCE_BAD_HOST") from None

    try:
        client = request.client
        if client is None:
            raise ValueError
        peer_host = client.host
        if (
            not isinstance(peer_host, str)
            or not peer_host
            or peer_host != peer_host.strip()
            or len(peer_host) > 255
        ):
            raise ValueError
        normalized_peer = peer_host.lower()
        if (
            normalized_peer != "localhost"
            and not ip_address(normalized_peer).is_loopback
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise _failure("LOCAL_GOVERNANCE_BAD_HOST") from None


def _require_post_headers(request: Request) -> None:
    operation = _header_values(request, LOCAL_OPERATION_HEADER)
    if len(operation) != 1 or operation[0] != LOCAL_OPERATION_VALUE.encode("ascii"):
        raise _failure("LOCAL_GOVERNANCE_OPERATION_REQUIRED")
    content_types = _header_values(request, "content-type")
    if len(content_types) != 1:
        raise _failure("LOCAL_GOVERNANCE_UNSUPPORTED_MEDIA_TYPE")
    try:
        media_type = content_types[0].decode("ascii").split(";", 1)[0].strip().lower()
    except UnicodeDecodeError:
        raise _failure("LOCAL_GOVERNANCE_UNSUPPORTED_MEDIA_TYPE") from None
    if media_type != "application/json":
        raise _failure("LOCAL_GOVERNANCE_UNSUPPORTED_MEDIA_TYPE")


def _depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, level = stack.pop()
        maximum = max(maximum, level)
        if maximum > MAX_GOVERNANCE_JSON_DEPTH:
            return maximum
        if isinstance(current, Mapping):
            stack.extend((nested, level + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            stack.extend((nested, level + 1) for nested in current)
    return maximum


def _require_valid_unicode(value: object) -> None:
    """Reject decoded lone surrogates before models or hashing can see them."""

    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise ValueError from None
        elif isinstance(current, Mapping):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            stack.extend(current)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError


async def _read_request_model(
    request: Request, model: type[_SafeRequestModel]
) -> _SafeRequestModel:
    declared_lengths = _header_values(request, "content-length")
    if len(declared_lengths) > 1:
        raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST")
    if declared_lengths:
        try:
            declared = int(declared_lengths[0])
        except (TypeError, ValueError):
            raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST") from None
        if declared < 0:
            raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST")
        if declared > MAX_GOVERNANCE_REQUEST_BYTES:
            raise _failure("LOCAL_GOVERNANCE_REQUEST_TOO_LARGE")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        if not isinstance(chunk, bytes):
            raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST")
        size += len(chunk)
        if size > MAX_GOVERNANCE_REQUEST_BYTES:
            raise _failure("LOCAL_GOVERNANCE_REQUEST_TOO_LARGE")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(value, Mapping):
            raise ValueError
        if _depth(value) > MAX_GOVERNANCE_JSON_DEPTH:
            raise ValueError
        _require_valid_unicode(value)
        assert_model_safe(value)
        return model.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        SensitiveDataError,
        ValidationError,
        ValueError,
    ):
        raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST") from None


class LocalGovernanceHttpApi:
    """Small Starlette adapter around one injected LocalGovernanceEngine."""

    def __init__(
        self,
        engine: LocalGovernanceEngine,
        *,
        operation_boundary: LocalBusinessOperationBoundary | None = None,
        request_admission: LocalHttpRequestAdmission | None = None,
    ) -> None:
        if not isinstance(engine, LocalGovernanceEngine):
            raise TypeError("engine must be a LocalGovernanceEngine")
        self.engine = engine
        self.operation_boundary = operation_boundary or LocalBusinessOperationBoundary()
        self.request_admission = request_admission or LocalHttpRequestAdmission()

    def _admit_request(self):
        return self.request_admission.try_acquire(
            operation_boundary=self.operation_boundary
        )

    @staticmethod
    def _busy_response() -> Response:
        return _error_response(
            _failure(
                "LOCAL_GOVERNANCE_OPERATION_BUSY",
                headers={"Connection": "close"},
            )
        )

    @staticmethod
    def _body_timeout_response() -> Response:
        return _error_response(
            _failure(
                "LOCAL_GOVERNANCE_REQUEST_TIMEOUT",
                headers={"Connection": "close"},
            )
        )

    @staticmethod
    def _require_method(request: Request, expected: str) -> None:
        if request.method != expected:
            raise _failure(
                "LOCAL_GOVERNANCE_METHOD_NOT_ALLOWED",
                headers={"Allow": expected},
            )

    @staticmethod
    def _incident_id(request: Request) -> str:
        value = request.path_params.get("incident_id")
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST")
        normalized = value.strip()
        try:
            _require_valid_unicode(normalized)
            assert_model_safe({"incident_id": normalized})
        except (SensitiveDataError, ValueError):
            raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST") from None
        if request.url.query:
            raise _failure("LOCAL_GOVERNANCE_INVALID_REQUEST")
        return normalized

    async def _dispatch(self, operation: Any) -> Response:
        try:
            result = await self.operation_boundary.run(operation)
            view = governance_view(result)
            return _response(
                {"ok": True, "data": view.model_dump(mode="json", round_trip=True)},
                status_code=200,
            )
        except _BoundaryFailure as failure:
            return _error_response(failure)
        except LocalBusinessOperationBusy:
            return _error_response(_failure("LOCAL_GOVERNANCE_OPERATION_BUSY"))
        except LocalBusinessOperationTimedOut:
            return _error_response(_failure("LOCAL_GOVERNANCE_OPERATION_UNCERTAIN"))
        except GovernanceNotFoundError:
            return _error_response(_failure("LOCAL_GOVERNANCE_NOT_FOUND"))
        except GovernanceIdempotencyConflictError:
            return _error_response(_failure("LOCAL_GOVERNANCE_IDEMPOTENCY_CONFLICT"))
        except GovernanceAuthorizationError:
            return _error_response(_failure("LOCAL_GOVERNANCE_AUTHORIZATION_FAILED"))
        except GovernanceStateError:
            return _error_response(_failure("LOCAL_GOVERNANCE_STATE_CONFLICT"))
        except (GovernanceClockError, SensitiveDataError):
            return _error_response(_failure("LOCAL_GOVERNANCE_INTERNAL"))
        except (ValidationError, ValueError, TypeError):
            # Request decoding and validation have already been converted to a
            # _BoundaryFailure.  Errors of these types after that point come
            # from the engine, repository, or output projection and must not be
            # mislabeled as a caller mistake.
            return _error_response(_failure("LOCAL_GOVERNANCE_INTERNAL"))
        except Exception:
            # Never log or reflect an exception here: dependency exceptions may
            # retain rejected payloads, paths, or database details.
            return _error_response(_failure("LOCAL_GOVERNANCE_INTERNAL"))

    async def get_incident(self, request: Request) -> Response:
        admission = None
        try:
            _require_loopback_host(request)
            self._require_method(request, "GET")
            incident_id = self._incident_id(request)
            admission = self._admit_request()
        except _BoundaryFailure as failure:
            return _error_response(failure)
        except LocalBusinessOperationBusy:
            return self._busy_response()

        try:

            async def operation() -> GovernanceResult:
                incident = await self.engine.repository.get(incident_id)
                if incident is None:
                    raise GovernanceNotFoundError("incident")
                return GovernanceResult(incident)

            return await self._dispatch(operation)
        finally:
            admission.release()

    async def prepare(self, request: Request) -> Response:
        admission = None
        try:
            _require_loopback_host(request)
            self._require_method(request, "POST")
            _require_post_headers(request)
            incident_id = self._incident_id(request)
            admission = self._admit_request()
        except _BoundaryFailure as failure:
            return _error_response(failure)
        except LocalBusinessOperationBusy:
            return self._busy_response()

        try:
            try:
                body = await self.request_admission.read_body(
                    lambda: _read_request_model(request, PrepareRequest)
                )
                assert isinstance(body, PrepareRequest)
            except LocalHttpRequestBodyTimedOut:
                return self._body_timeout_response()
            except ClientDisconnect:
                return _error_response(
                    _failure(
                        "LOCAL_GOVERNANCE_INVALID_REQUEST",
                        headers={"Connection": "close"},
                    )
                )
            except _BoundaryFailure as failure:
                return _error_response(failure)

            async def operation() -> GovernanceResult:
                return await self.engine.prepare(
                    incident_id,
                    idempotency_key=body.idempotency_key,
                    actor=body.actor,
                )

            return await self._dispatch(operation)
        finally:
            admission.release()

    async def decide(self, request: Request) -> Response:
        admission = None
        try:
            _require_loopback_host(request)
            self._require_method(request, "POST")
            _require_post_headers(request)
            incident_id = self._incident_id(request)
            admission = self._admit_request()
        except _BoundaryFailure as failure:
            return _error_response(failure)
        except LocalBusinessOperationBusy:
            return self._busy_response()

        try:
            try:
                body = await self.request_admission.read_body(
                    lambda: _read_request_model(request, DecideRequest)
                )
                assert isinstance(body, DecideRequest)
            except LocalHttpRequestBodyTimedOut:
                return self._body_timeout_response()
            except ClientDisconnect:
                return _error_response(
                    _failure(
                        "LOCAL_GOVERNANCE_INVALID_REQUEST",
                        headers={"Connection": "close"},
                    )
                )
            except _BoundaryFailure as failure:
                return _error_response(failure)

            async def operation() -> GovernanceResult:
                return await self.engine.decide(
                    incident_id,
                    approve=body.approve,
                    expected_action_hash=body.expected_action_hash,
                    expected_revision=body.expected_revision,
                    actor=body.actor,
                    reason=body.reason,
                    idempotency_key=body.idempotency_key,
                )

            return await self._dispatch(operation)
        finally:
            admission.release()

    async def execute(self, request: Request) -> Response:
        admission = None
        try:
            _require_loopback_host(request)
            self._require_method(request, "POST")
            _require_post_headers(request)
            incident_id = self._incident_id(request)
            admission = self._admit_request()
        except _BoundaryFailure as failure:
            return _error_response(failure)
        except LocalBusinessOperationBusy:
            return self._busy_response()

        try:
            try:
                body = await self.request_admission.read_body(
                    lambda: _read_request_model(request, ExecuteRequest)
                )
                assert isinstance(body, ExecuteRequest)
            except LocalHttpRequestBodyTimedOut:
                return self._body_timeout_response()
            except ClientDisconnect:
                return _error_response(
                    _failure(
                        "LOCAL_GOVERNANCE_INVALID_REQUEST",
                        headers={"Connection": "close"},
                    )
                )
            except _BoundaryFailure as failure:
                return _error_response(failure)

            async def operation() -> GovernanceResult:
                return await self.engine.execute(
                    incident_id,
                    idempotency_key=body.idempotency_key,
                    actor=body.actor,
                    verification_passed=body.verification_passed,
                )

            return await self._dispatch(operation)
        finally:
            admission.release()


def governance_routes(
    engine: LocalGovernanceEngine,
    *,
    operation_boundary: LocalBusinessOperationBoundary | None = None,
    request_admission: LocalHttpRequestAdmission | None = None,
) -> tuple[Route, ...]:
    api = LocalGovernanceHttpApi(
        engine,
        operation_boundary=operation_boundary,
        request_admission=request_admission,
    )
    return (
        Route(
            "/local/v1/incidents/{incident_id}",
            endpoint=api.get_incident,
            methods=_STANDARD_HTTP_METHODS,
            name="local-governance-incident",
        ),
        Route(
            "/local/v1/incidents/{incident_id}/prepare",
            endpoint=api.prepare,
            methods=_STANDARD_HTTP_METHODS,
            name="local-governance-prepare",
        ),
        Route(
            "/local/v1/incidents/{incident_id}/decide",
            endpoint=api.decide,
            methods=_STANDARD_HTTP_METHODS,
            name="local-governance-decide",
        ),
        Route(
            "/local/v1/incidents/{incident_id}/execute",
            endpoint=api.execute,
            methods=_STANDARD_HTTP_METHODS,
            name="local-governance-execute",
        ),
    )


__all__ = [
    "GovernanceView",
    "LOCAL_OPERATION_HEADER",
    "LOCAL_OPERATION_VALUE",
    "MAX_GOVERNANCE_REQUEST_BYTES",
    "MAX_GOVERNANCE_RESPONSE_BYTES",
    "LocalGovernanceHttpApi",
    "governance_routes",
    "governance_view",
]
