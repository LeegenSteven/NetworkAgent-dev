"""Loopback-only receiver for the public telco-lab replay wire contract.

This adapter performs only the fixed BubbleRAN local replay rule evaluation;
it performs no cross-event aggregation, approval, or network action.  One
validated source event maps to one deterministic Canonical Incident and is
acknowledged only after the canonical Repository has committed and the
immutable source-event association has been read back.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from telco_domain import (
    IdempotencyConflictError,
    Incident,
    IncidentRepository,
    IncidentStatus,
    KpiComparator,
    KpiViolation,
    ResourceReference,
    ResourceType,
    SensitiveDataError,
    Technology,
    assert_model_safe,
)
from telco_lab import (
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    MAX_REPLAY_HTTP_REQUEST_BYTES,
    MAX_REPLAY_HTTP_RESPONSE_BYTES,
    ReplayError,
    ReplayWirePayload,
    validate_replay_wire_payload,
)
from telco_local import (
    BUBBLERAN_REPLAY_DETECTOR_ALGORITHM,
    BUBBLERAN_REPLAY_RULE_ID,
    JsonRuleRepository,
    RcaRule,
    rule_content_sha256,
)


LOCAL_FAULT_ROUTE = "/local/v1/faults/replay"
LOCAL_FAULT_OPERATION_HEADER = "x-networkagent-local-operation"
LOCAL_FAULT_OPERATION_VALUE = "replay-v1"
LOCAL_FAULT_IDEMPOTENCY_HEADER = "idempotency-key"
MAX_FAULT_REQUEST_BYTES = MAX_REPLAY_HTTP_REQUEST_BYTES
MAX_FAULT_RESPONSE_BYTES = MAX_REPLAY_HTTP_RESPONSE_BYTES
MAX_FAULT_JSON_DEPTH = 16
_INGEST_REASON = "local replay source event ingestion"
_BUBBLERAN_SCENARIO = "bubbleran-persistent-interference"
_BUBBLERAN_GNB_RESOURCE = re.compile(r"^lab:5g-sa:gnb:[0-9a-f]{24}$")
_BUBBLERAN_RULE_VERSION = "1.0.0"
_BUBBLERAN_UL_BLER_KPI = "ran.mac.ul_bler"
_BUBBLERAN_UL_BLER_THRESHOLD = 0.15
_BUBBLERAN_UL_BLER_UNIT = "ratio"


class _FaultViewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class FaultResourceView(_FaultViewModel):
    resource_id: str
    resource_type: str
    technology: str


class FaultReceiptView(_FaultViewModel):
    accepted: Literal["DURABLE"] = "DURABLE"
    source_event_id: str
    payload_sha256: str
    incident_id: str
    status: str
    revision: int
    technology: str
    scope: tuple[FaultResourceView, ...]


class _FaultBoundaryFailure(RuntimeError):
    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.safe_message = message


_ERRORS: dict[str, tuple[int, str]] = {
    "LOCAL_FAULT_BAD_HOST": (403, "A loopback Host is required."),
    "LOCAL_FAULT_OPERATION_REQUIRED": (
        403,
        "The local replay operation header is required.",
    ),
    "LOCAL_FAULT_IDEMPOTENCY_REQUIRED": (
        403,
        "The replay idempotency header is required.",
    ),
    "LOCAL_FAULT_IDEMPOTENCY_CONFLICT": (
        409,
        "The idempotency key conflicts with this replay event.",
    ),
    "LOCAL_FAULT_UNSUPPORTED_MEDIA_TYPE": (
        415,
        "Content-Type must be application/json.",
    ),
    "LOCAL_FAULT_REQUEST_TOO_LARGE": (413, "The request is too large."),
    "LOCAL_FAULT_INVALID_REQUEST": (422, "The replay event is invalid."),
    "LOCAL_FAULT_UNAVAILABLE": (
        503,
        "The local fault receiver is temporarily unavailable.",
    ),
    "LOCAL_FAULT_RESPONSE_TOO_LARGE": (
        500,
        "The local fault response exceeded its safe budget.",
    ),
}


def _failure(code: str) -> _FaultBoundaryFailure:
    status_code, message = _ERRORS[code]
    return _FaultBoundaryFailure(code, status_code, message)


def _header_values(request: Request, name: str) -> tuple[bytes, ...]:
    wanted = name.encode("ascii").lower()
    return tuple(
        value
        for key, value in request.scope.get("headers", ())
        if key.lower() == wanted
    )


def _require_loopback(request: Request) -> None:
    values = _header_values(request, "host")
    if len(values) != 1:
        raise _failure("LOCAL_FAULT_BAD_HOST")
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
            or parsed.hostname is None
        ):
            raise ValueError
        hostname = parsed.hostname.lower()
        if hostname != "localhost" and not ip_address(hostname).is_loopback:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65_535:
            raise ValueError

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
    except (AttributeError, TypeError, UnicodeDecodeError, ValueError):
        raise _failure("LOCAL_FAULT_BAD_HOST") from None


def _require_headers(request: Request) -> bytes:
    operation = _header_values(request, LOCAL_FAULT_OPERATION_HEADER)
    if len(operation) != 1 or operation[0] != LOCAL_FAULT_OPERATION_VALUE.encode(
        "ascii"
    ):
        raise _failure("LOCAL_FAULT_OPERATION_REQUIRED")

    idempotency = _header_values(request, LOCAL_FAULT_IDEMPOTENCY_HEADER)
    if len(idempotency) != 1 or not idempotency[0] or len(idempotency[0]) > 256:
        raise _failure("LOCAL_FAULT_IDEMPOTENCY_REQUIRED")
    try:
        idempotency[0].decode("ascii")
    except UnicodeDecodeError:
        raise _failure("LOCAL_FAULT_IDEMPOTENCY_REQUIRED") from None

    content_types = _header_values(request, "content-type")
    if len(content_types) != 1:
        raise _failure("LOCAL_FAULT_UNSUPPORTED_MEDIA_TYPE")
    try:
        media_type = content_types[0].decode("ascii").split(";", 1)[0]
    except UnicodeDecodeError:
        raise _failure("LOCAL_FAULT_UNSUPPORTED_MEDIA_TYPE") from None
    if media_type.strip().lower() != "application/json":
        raise _failure("LOCAL_FAULT_UNSUPPORTED_MEDIA_TYPE")
    return idempotency[0]


def _depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, level = stack.pop()
        maximum = max(maximum, level)
        if maximum > MAX_FAULT_JSON_DEPTH:
            return maximum
        if isinstance(current, Mapping):
            stack.extend((nested, level + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            stack.extend((nested, level + 1) for nested in current)
    return maximum


def _require_valid_unicode(value: object) -> None:
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            current.encode("utf-8", errors="strict")
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


async def _read_wire(request: Request) -> ReplayWirePayload:
    declared_lengths = _header_values(request, "content-length")
    if len(declared_lengths) > 1:
        raise _failure("LOCAL_FAULT_INVALID_REQUEST")
    if declared_lengths:
        raw_length = declared_lengths[0]
        if not raw_length or any(
            character < 48 or character > 57 for character in raw_length
        ):
            raise _failure("LOCAL_FAULT_INVALID_REQUEST")
        declared = int(raw_length)
        if declared > MAX_FAULT_REQUEST_BYTES:
            raise _failure("LOCAL_FAULT_REQUEST_TOO_LARGE")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        if not isinstance(chunk, bytes):
            raise _failure("LOCAL_FAULT_INVALID_REQUEST")
        size += len(chunk)
        if size > MAX_FAULT_REQUEST_BYTES:
            raise _failure("LOCAL_FAULT_REQUEST_TOO_LARGE")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise _failure("LOCAL_FAULT_INVALID_REQUEST")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if type(value) is not dict or _depth(value) > MAX_FAULT_JSON_DEPTH:
            raise ValueError
        _require_valid_unicode(value)
        assert_model_safe(value)
    except (
        UnicodeDecodeError,
        UnicodeEncodeError,
        json.JSONDecodeError,
        RecursionError,
        SensitiveDataError,
        ValueError,
    ):
        raise _failure("LOCAL_FAULT_INVALID_REQUEST") from None
    try:
        return validate_replay_wire_payload(value)
    except (ReplayError, SensitiveDataError, ValidationError):
        raise _failure("LOCAL_FAULT_INVALID_REQUEST") from None


def _stable_id(prefix: str, source_event_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"telco-assurance:{prefix}:v1".encode("ascii"))
    digest.update(b"\0")
    digest.update(source_event_id.encode("ascii"))
    return f"{prefix}-{digest.hexdigest()}"


def _resource_type(technology: Technology) -> ResourceType:
    if technology is Technology.LTE:
        return ResourceType.CELL
    if technology in {Technology.FIVE_G_NSA, Technology.FIVE_G_SA}:
        return ResourceType.GNB
    return ResourceType.OTHER


def _controlled_violation(
    wire: ReplayWirePayload,
    rule_repository: JsonRuleRepository,
) -> tuple[tuple[KpiViolation, ...], dict[str, str], dict[str, object]]:
    observed = wire.metrics.get(_BUBBLERAN_UL_BLER_KPI)
    if observed is None or observed <= _BUBBLERAN_UL_BLER_THRESHOLD:
        return (), {}, {}

    rule = rule_repository.get_version(
        BUBBLERAN_REPLAY_RULE_ID,
        _BUBBLERAN_RULE_VERSION,
    )
    if (
        not isinstance(rule, RcaRule)
        or rule.rule_id != BUBBLERAN_REPLAY_RULE_ID
        or rule.version != _BUBBLERAN_RULE_VERSION
        or rule.technology != Technology.FIVE_G_SA.value
        or not rule.is_current
        or rule.detection.kpi_name != _BUBBLERAN_UL_BLER_KPI
        or rule.detection.comparator is not KpiComparator.GT
        or rule.detection.threshold != _BUBBLERAN_UL_BLER_THRESHOLD
        or rule.detection.unit != _BUBBLERAN_UL_BLER_UNIT
        or wire.units.get(_BUBBLERAN_UL_BLER_KPI) != _BUBBLERAN_UL_BLER_UNIT
    ):
        raise _failure("LOCAL_FAULT_UNAVAILABLE")

    violation = KpiViolation(
        violation_id=_stable_id(
            "local-replay-violation",
            wire.source_event_id,
        ),
        kpi_name=rule.detection.kpi_name,
        observed_value=observed,
        threshold_value=rule.detection.threshold,
        comparator=rule.detection.comparator,
        unit=rule.detection.unit,
        window_start=wire.replay_observed_at,
        window_end=wire.replay_observed_at,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        resource_ids=(wire.resource_id,),
    )
    return (
        (violation,),
        {rule.rule_id: rule.version},
        {
            "detector_algorithm": BUBBLERAN_REPLAY_DETECTOR_ALGORITHM,
            "rule_content_hashes": {
                rule.rule_id: rule_content_sha256(rule),
            },
        },
    )


def _candidate(
    wire: ReplayWirePayload,
    rule_repository: JsonRuleRepository,
) -> Incident:
    if (
        wire.dataset_id != BUBBLERAN_DATASET_ID
        or wire.dataset_version != BUBBLERAN_DATASET_VERSION
        or wire.scenario != _BUBBLERAN_SCENARIO
        or wire.technology is not Technology.FIVE_G_SA
        or _BUBBLERAN_GNB_RESOURCE.fullmatch(wire.resource_id) is None
    ):
        raise _failure("LOCAL_FAULT_INVALID_REQUEST")
    correlation_key = _stable_id("local-replay-correlation", wire.source_event_id)
    incident_id = _stable_id("local-replay-incident", wire.source_event_id)
    trace_id = _stable_id("local-replay-trace", wire.source_event_id)
    resource = ResourceReference(
        resource_id=wire.resource_id,
        resource_type=_resource_type(wire.technology),
        technology=wire.technology,
    )
    violated_kpis, rule_versions, controlled_metadata = _controlled_violation(
        wire,
        rule_repository,
    )
    model_metadata: dict[str, object] = {
        "aggregation_mode": "PER_SOURCE_EVENT",
        "dataset_id": wire.dataset_id,
        "dataset_version": wire.dataset_version,
        "event_source": "telco-lab-replay",
        "payload_sha256": wire.payload_sha256,
        "quality_flags": list(wire.quality_flags),
        "replay_metrics": {name: wire.metrics[name] for name in sorted(wire.metrics)},
        "replay_scenario": wire.scenario,
        "replay_units": {name: wire.units[name] for name in sorted(wire.units)},
        "wire_request_fingerprint_sha256": (wire.request_fingerprint_sha256),
        "wire_schema_version": wire.schema_version,
    }
    model_metadata.update(controlled_metadata)
    incident = Incident(
        incident_id=incident_id,
        correlation_key=correlation_key,
        source_event_ids=(wire.source_event_id,),
        technology=wire.technology,
        title="Validated local replay KPI fault",
        description=("A strict, label-free local replay event was durably received."),
        affected_resources=(resource,),
        detected_at=wire.replay_observed_at,
        window_start=wire.replay_observed_at,
        window_end=wire.replay_observed_at,
        violated_kpis=violated_kpis,
        model_metadata=model_metadata,
        rule_versions=rule_versions,
        trace_id=trace_id,
        created_at=wire.replay_observed_at,
        updated_at=wire.replay_observed_at,
    )
    assert_model_safe(incident)
    return incident


def _candidate_identity_matches(result: Incident, candidate: Incident) -> bool:
    return (
        result.incident_id == candidate.incident_id
        and result.correlation_key == candidate.correlation_key
        and result.source_event_ids == candidate.source_event_ids
        and result.technology is candidate.technology
        and result.title == candidate.title
        and result.description == candidate.description
        and result.affected_resources == candidate.affected_resources
        and result.detected_at == candidate.detected_at
        and result.window_start == candidate.window_start
        and result.window_end == candidate.window_end
        and result.violated_kpis == candidate.violated_kpis
        and result.rule_versions == candidate.rule_versions
        and result.model_metadata == candidate.model_metadata
        and result.trace_id == candidate.trace_id
    )


def _persisted_identity_matches(result: Incident, candidate: Incident) -> bool:
    """Compare immutable ingest facts while allowing governance state to advance."""

    return (
        result.incident_id == candidate.incident_id
        and result.correlation_key == candidate.correlation_key
        and result.source_event_ids == candidate.source_event_ids
        and result.technology is candidate.technology
        and result.title == candidate.title
        and result.description == candidate.description
        and result.affected_resources == candidate.affected_resources
        and result.detected_at == candidate.detected_at
        and result.window_start == candidate.window_start
        and result.window_end == candidate.window_end
        and result.violated_kpis == candidate.violated_kpis
        and result.rule_versions == candidate.rule_versions
        and result.trace_id == candidate.trace_id
        and all(
            result.model_metadata.get(key) == value
            for key, value in candidate.model_metadata.items()
        )
    )


def _enum_value(value: object) -> str:
    normalized = getattr(value, "value", value)
    if not isinstance(normalized, str):
        raise _failure("LOCAL_FAULT_UNAVAILABLE")
    return normalized


def _receipt(incident: Incident, wire: ReplayWirePayload) -> FaultReceiptView:
    view = FaultReceiptView(
        source_event_id=wire.source_event_id,
        payload_sha256=wire.payload_sha256,
        incident_id=incident.incident_id,
        status=_enum_value(incident.status),
        revision=incident.revision,
        technology=_enum_value(incident.technology),
        scope=tuple(
            FaultResourceView(
                resource_id=resource.resource_id,
                resource_type=_enum_value(resource.resource_type),
                technology=_enum_value(resource.technology),
            )
            for resource in incident.affected_resources
            if resource.technology is not None
        ),
    )
    if len(view.scope) != len(incident.affected_resources):
        raise _failure("LOCAL_FAULT_UNAVAILABLE")
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
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise _failure("LOCAL_FAULT_UNAVAILABLE") from None


def _response(payload: Mapping[str, object], *, status_code: int) -> Response:
    try:
        assert_model_safe(payload)
    except SensitiveDataError:
        raise _failure("LOCAL_FAULT_UNAVAILABLE") from None
    body = _json_bytes(payload)
    if len(body) > MAX_FAULT_RESPONSE_BYTES:
        raise _failure("LOCAL_FAULT_RESPONSE_TOO_LARGE")
    return Response(body, status_code=status_code, media_type="application/json")


def _error_response(failure: _FaultBoundaryFailure) -> Response:
    headers = {"Retry-After": "5"} if failure.status_code == 503 else None
    return Response(
        _json_bytes(
            {
                "ok": False,
                "error": {
                    "code": failure.code,
                    "message": failure.safe_message,
                },
            }
        ),
        status_code=failure.status_code,
        media_type="application/json",
        headers=headers,
    )


class LocalReplayFaultReceiver:
    """Durable bridge from one public replay wire payload to one Incident."""

    def __init__(
        self,
        repository: IncidentRepository,
        rule_repository: JsonRuleRepository,
        *,
        actor: str,
    ) -> None:
        self.repository = repository
        self.rule_repository = rule_repository
        self.actor = actor

    async def ingest(self, wire: ReplayWirePayload) -> FaultReceiptView:
        candidate = _candidate(wire, self.rule_repository)
        incident = await self.repository.create_or_correlate(
            candidate,
            idempotency_key=wire.idempotency_key,
            actor=self.actor,
            reason=_INGEST_REASON,
            trace_id=candidate.trace_id,
        )
        if not isinstance(incident, Incident) or not _candidate_identity_matches(
            incident, candidate
        ):
            raise _failure("LOCAL_FAULT_UNAVAILABLE")

        current = await self.repository.get(incident.incident_id)
        if not isinstance(current, Incident) or not _persisted_identity_matches(
            current, candidate
        ):
            raise _failure("LOCAL_FAULT_UNAVAILABLE")

        associations = await self.repository.source_event_associations(
            incident.incident_id,
            limit=1000,
        )
        expected = tuple(
            association
            for association in associations
            if association.source_event_id == wire.source_event_id
        )
        if len(expected) != 1:
            raise _failure("LOCAL_FAULT_UNAVAILABLE")
        association = expected[0]
        if (
            association.incident_id != incident.incident_id
            or association.idempotency_key != wire.idempotency_key
            or association.actor != self.actor
            or association.reason != _INGEST_REASON
            or association.trace_id != candidate.trace_id
        ):
            raise _failure("LOCAL_FAULT_UNAVAILABLE")

        audit_page = await self.repository.history(
            incident.incident_id,
            limit=2,
            offset=0,
        )
        initial_events = tuple(
            event
            for event in audit_page
            if event.revision == 0 or event.from_status is None
        )
        if len(initial_events) != 1:
            raise _failure("LOCAL_FAULT_UNAVAILABLE")
        initial = initial_events[0]
        if (
            initial.incident_id != incident.incident_id
            or initial.from_status is not None
            or initial.to_status is not IncidentStatus.DETECTED
            or initial.revision != 0
            or initial.actor != self.actor
            or initial.reason != _INGEST_REASON
            or initial.idempotency_key != wire.idempotency_key
            or initial.trace_id != candidate.trace_id
        ):
            raise _failure("LOCAL_FAULT_UNAVAILABLE")
        return _receipt(incident, wire)


class LocalReplayFaultHttpApi:
    def __init__(self, receiver: LocalReplayFaultReceiver) -> None:
        self.receiver = receiver

    async def receive(self, request: Request) -> Response:
        try:
            _require_loopback(request)
            if request.url.query:
                raise _failure("LOCAL_FAULT_INVALID_REQUEST")
            header_idempotency = _require_headers(request)
            wire = await _read_wire(request)
            if header_idempotency != wire.idempotency_key.encode("ascii"):
                raise _failure("LOCAL_FAULT_IDEMPOTENCY_CONFLICT")
            receipt = await self.receiver.ingest(wire)
            return _response(
                {
                    "ok": True,
                    "data": receipt.model_dump(mode="json", round_trip=True),
                },
                status_code=202,
            )
        except _FaultBoundaryFailure as failure:
            return _error_response(failure)
        except IdempotencyConflictError:
            return _error_response(_failure("LOCAL_FAULT_IDEMPOTENCY_CONFLICT"))
        except Exception:
            return _error_response(_failure("LOCAL_FAULT_UNAVAILABLE"))


def fault_receiver_routes(
    receiver: LocalReplayFaultReceiver,
) -> tuple[Route, ...]:
    api = LocalReplayFaultHttpApi(receiver)
    return (
        Route(
            LOCAL_FAULT_ROUTE,
            endpoint=api.receive,
            methods=["POST"],
            name="local-replay-fault-receiver",
        ),
    )


__all__ = [
    "FaultReceiptView",
    "LOCAL_FAULT_IDEMPOTENCY_HEADER",
    "LOCAL_FAULT_OPERATION_HEADER",
    "LOCAL_FAULT_OPERATION_VALUE",
    "LOCAL_FAULT_ROUTE",
    "LocalReplayFaultHttpApi",
    "LocalReplayFaultReceiver",
    "MAX_FAULT_REQUEST_BYTES",
    "MAX_FAULT_RESPONSE_BYTES",
    "fault_receiver_routes",
]
