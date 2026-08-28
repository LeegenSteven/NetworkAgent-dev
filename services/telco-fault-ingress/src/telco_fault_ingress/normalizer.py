"""Allowlisted Cloud Logging fault normalization into canonical contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import re
from typing import TYPE_CHECKING, Any

from telco_domain import (
    Incident,
    IncidentSeverity,
    IncidentTrigger,
    ResourceReference,
    ResourceType,
    Technology,
)

from .models import ParsedPubSubPush, PermanentIngressError

if TYPE_CHECKING:
    from telco_cloud import SourceEventEnvelope


_SAFE_RESOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ALLOWED_EVENTS = frozenset({"UERANSIMHEALTH", "CRITICALSERVICEERROR"})


def _derived_id(namespace: str, *values: str) -> str:
    material = "\0".join((namespace, *values)).encode("utf-8")
    return f"{namespace}-{hashlib.sha256(material).hexdigest()}"


def _bounded_text(value: object, *, maximum: int = 1024) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _event_timestamp(payload: Mapping[str, Any]) -> datetime:
    raw = _bounded_text(payload.get("timestamp"), maximum=64)
    if raw is None:
        raise PermanentIngressError("FAULT_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise PermanentIngressError("FAULT_TIMESTAMP_INVALID") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PermanentIngressError("FAULT_TIMESTAMP_INVALID")
    return parsed.astimezone(UTC)


def _fault_identity(
    payload: Mapping[str, Any], event_type: str
) -> tuple[str, ResourceReference]:
    json_payload = _mapping(payload.get("jsonPayload"))
    if event_type == "UERANSIMHEALTH":
        process_name = _bounded_text(json_payload.get("process_name"), maximum=128)
        hostname = _bounded_text(json_payload.get("hostname"), maximum=128)
        if (
            process_name is None
            or hostname is None
            or _SAFE_RESOURCE.fullmatch(process_name) is None
            or _SAFE_RESOURCE.fullmatch(hostname) is None
        ):
            raise PermanentIngressError("FAULT_RESOURCE_INVALID")
        semantic_resource = f"{hostname.lower()}:{process_name.lower()}"
    else:
        # userid is intentionally neither read nor retained. It may represent a
        # subscriber and is not needed to correlate a node-level service fault.
        node = _bounded_text(json_payload.get("node"), maximum=128)
        if node is None or _SAFE_RESOURCE.fullmatch(node) is None:
            raise PermanentIngressError("FAULT_RESOURCE_INVALID")
        semantic_resource = node.lower()

    resource_id = _derived_id("resource", event_type, semantic_resource)
    return semantic_resource, ResourceReference(
        resource_id=resource_id,
        resource_type=ResourceType.NETWORK_NODE,
        technology=Technology.FIVE_G_SA,
    )


def normalize_fault_event(
    push: ParsedPubSubPush,
    *,
    received_at: datetime | None = None,
    max_event_age_seconds: int = 7 * 24 * 60 * 60,
    max_future_skew_seconds: int = 5 * 60,
) -> "SourceEventEnvelope":
    """Normalize a known fault without carrying the raw payload downstream."""

    labels = _mapping(push.payload.get("labels"))
    event_type = _bounded_text(labels.get("python_logger"), maximum=64)
    if event_type not in _ALLOWED_EVENTS:
        raise PermanentIngressError("FAULT_EVENT_TYPE_REJECTED")

    occurred_at = _event_timestamp(push.payload)
    trusted_received_at = received_at or datetime.now(UTC)
    if trusted_received_at.tzinfo is None or trusted_received_at.utcoffset() is None:
        raise ValueError("received_at must include a timezone")
    trusted_received_at = trusted_received_at.astimezone(UTC)
    if not 1 <= max_event_age_seconds <= 7 * 24 * 60 * 60:
        raise ValueError("max_event_age_seconds is outside the safe range")
    if not 1 <= max_future_skew_seconds <= 5 * 60:
        raise ValueError("max_future_skew_seconds is outside the safe range")
    if occurred_at > trusted_received_at + timedelta(
        seconds=max_future_skew_seconds
    ):
        raise PermanentIngressError("FAULT_TIMESTAMP_FUTURE")
    if occurred_at < trusted_received_at - timedelta(
        seconds=max_event_age_seconds
    ):
        raise PermanentIngressError("FAULT_TIMESTAMP_STALE")

    semantic_resource, resource = _fault_identity(push.payload, event_type)
    insert_id = _bounded_text(push.payload.get("insertId"), maximum=256)
    log_name = _bounded_text(push.payload.get("logName"), maximum=1024) or "unknown"
    source_seed = (
        f"{log_name}\0{insert_id}"
        if insert_id is not None
        else f"{event_type}\0{occurred_at.isoformat()}\0{push.payload_sha256}"
    )
    source_event_id = _derived_id("event", source_seed)
    correlation_key = _derived_id("correlation", event_type, semantic_resource)
    incident_id = _derived_id("incident", source_event_id, correlation_key)
    trace_id = _derived_id("trace", source_event_id)

    severity = (
        IncidentSeverity.HIGH
        if event_type == "UERANSIMHEALTH"
        else IncidentSeverity.CRITICAL
    )
    title = (
        "5G 仿真进程健康异常"
        if event_type == "UERANSIMHEALTH"
        else "5G 关键服务异常"
    )
    incident = Incident(
        incident_id=incident_id,
        correlation_key=correlation_key,
        source_event_ids=(source_event_id,),
        technology=Technology.FIVE_G_SA,
        severity=severity,
        title=title,
        description="检测到经过白名单验证的实时网络故障事件。",
        affected_resources=(resource,),
        detected_at=occurred_at,
        # These candidate timestamps must be derived from the source event,
        # not from wall-clock defaults.  A Pub/Sub redelivery can arrive at a
        # different time; changing the candidate Incident would then turn an
        # otherwise identical replay into an idempotency conflict.  The
        # repository still replaces them with its trusted transaction time
        # when it persists a newly-created incident.
        created_at=occurred_at,
        updated_at=occurred_at,
        trace_id=trace_id,
        model_metadata={
            "event_source": "cloud-logging",
            "event_type": event_type,
        },
    )

    # Importing the cloud adapter is deliberately delayed until normalization.
    # Merely importing or testing the HTTP boundary never initializes a cloud
    # SDK client or reads credentials.
    from telco_cloud import SourceEventEnvelope

    return SourceEventEnvelope(
        source_event_id=source_event_id,
        source="cloud-logging",
        event_type=event_type,
        occurred_at=occurred_at,
        received_at=trusted_received_at,
        payload_sha256=push.payload_sha256,
        trace_id=trace_id,
        incident=incident,
        attributes={
            "correlation_key": correlation_key,
            "resource_id": resource.resource_id,
        },
    )


def build_incident_trigger(envelope: "SourceEventEnvelope") -> IncidentTrigger:
    """Build a stable, replay-safe outbox payload for one normalized event."""

    if envelope.incident is None:
        raise PermanentIngressError("FAULT_INCIDENT_MISSING")
    source_id = envelope.source_event_id
    incident = envelope.incident
    return IncidentTrigger(
        message_id=_derived_id("message", source_id),
        workflow_id=_derived_id("workflow", source_id),
        incident_id=incident.incident_id,
        trace_id=incident.trace_id,
        idempotency_key=_derived_id("idempotency", source_id),
        sent_at=envelope.received_at,
        incident=incident,
        summary_zh="检测到实时故障并提交 Canonical Incident。",
    )


__all__ = ["build_incident_trigger", "normalize_fault_event"]
