"""Strict, SDK-neutral helpers for the Supervisor A2A boundary.

The Supervisor runs in an older ADK environment while specialist agents run in
separate environments.  This module therefore deliberately does not import the
A2A SDK, ADK, or the telco packages.  It accepts both raw wire dictionaries and
the duck-typed Pydantic objects exposed by ``a2a-sdk==0.3.11``.

Structured data is authoritative.  Text is bounded presentation content only
and is never parsed into a command.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from collections.abc import AsyncIterable, Iterable
from typing import Any, Mapping, Sequence
from uuid import uuid4


MAX_CANONICAL_BYTES = 256_000
MAX_CANONICAL_DEPTH = 24
MAX_TEXT_CHARS = 4_096
MAX_ID_CHARS = 256

_COMMON_FIELDS = (
    "schema_version",
    "message_type",
    "message_id",
    "workflow_id",
    "trace_id",
    "idempotency_key",
    "sent_at",
)
_ASSURANCE_MESSAGE_TYPES = frozenset(
    {
        "assurance_scan_request",
        "assurance_candidate_page",
        "assurance_confirmation_request",
        "assurance_confirmation_result",
        "assurance_analyze_request",
        "assurance_error",
    }
)
_MESSAGE_FIELDS = {
    "assurance_scan_request": frozenset(
        (*_COMMON_FIELDS, "window_start", "window_end", "resource_ids", "page_size", "page_offset")
    ),
    "assurance_candidate_page": frozenset(
        (
            *_COMMON_FIELDS,
            "request_message_id",
            "candidates",
            "page_size",
            "page_offset",
            "total_candidates",
            "has_more",
            "challenge_id",
            "snapshot_sha256",
            "challenge_expires_at",
            "effective_window_start",
            "effective_window_end",
            "summary_zh",
        )
    ),
    "assurance_confirmation_request": frozenset(
        (
            *_COMMON_FIELDS,
            "preview_message_id",
            "candidate_id",
            "challenge_id",
            "snapshot_sha256",
            "decision",
            "reason",
        )
    ),
    "assurance_confirmation_result": frozenset(
        (
            *_COMMON_FIELDS,
            "request_message_id",
            "preview_message_id",
            "candidate_id",
            "decision",
            "actor",
            "outcome",
            "incident",
            "summary_zh",
        )
    ),
    "assurance_analyze_request": frozenset(
        (*_COMMON_FIELDS, "incident_id", "requested_report_version")
    ),
    "assurance_error": frozenset(
        (
            "schema_version",
            "message_type",
            "message_id",
            "error_code",
            "summary_zh",
            "sent_at",
        )
    ),
}
_CANDIDATE_FIELDS = frozenset(
    {
        "candidate_id",
        "title",
        "technology",
        "window_start",
        "window_end",
        "affected_resources",
        "violated_kpis",
        "summary_zh",
    }
)
_CANDIDATE_RESOURCE_FIELDS = frozenset(
    {"resource_id", "resource_type", "technology"}
)
_CANDIDATE_KPI_FIELDS = frozenset(
    {
        "kpi_name",
        "observed_value",
        "threshold_value",
        "comparator",
        "unit",
        "sample_count",
    }
)
_INCIDENT_FIELDS = frozenset(
    {
        "schema_version",
        "incident_id",
        "correlation_key",
        "source_event_ids",
        "technology",
        "vendor_profile",
        "status",
        "severity",
        "title",
        "description",
        "affected_resources",
        "detected_at",
        "window_start",
        "window_end",
        "violated_kpis",
        "evidence_refs",
        "hypotheses",
        "root_cause",
        "rca_reports",
        "recommendations",
        "approvals",
        "action_runs",
        "verification_runs",
        "model_metadata",
        "rule_versions",
        "trace_id",
        "duplicate_of",
        "created_at",
        "updated_at",
        "revision",
    }
)
_RESOURCE_REFERENCE_FIELDS = frozenset(
    {
        "schema_version",
        "resource_id",
        "resource_type",
        "name",
        "technology",
        "vendor_profile",
        "parent_resource_id",
        "location_id",
        "external_ids",
        "attributes",
    }
)
_KPI_VIOLATION_FIELDS = frozenset(
    {
        "schema_version",
        "violation_id",
        "kpi_name",
        "observed_value",
        "threshold_value",
        "comparator",
        "unit",
        "window_start",
        "window_end",
        "rule_id",
        "rule_version",
        "resource_ids",
        "dimensions",
    }
)
_EVIDENCE_REFERENCE_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_id",
        "evidence_type",
        "uri",
        "source",
        "summary",
        "collected_at",
        "content_type",
        "checksum_sha256",
        "attributes",
    }
)
_RCA_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "incident_id",
        "version",
        "status",
        "title",
        "summary",
        "hypotheses",
        "root_cause",
        "conclusion",
        "confidence",
        "evidence_refs",
        "recommendations",
        "generated_by",
        "model_metadata",
        "created_at",
    }
)
_REMEDIATION_FIELDS = frozenset(
    {
        "schema_version",
        "action_id",
        "action_type",
        "description",
        "target_resources",
        "parameters",
        "risk_level",
        "requires_approval",
        "reversible",
        "rollback_plan",
        "expected_outcome",
        "idempotency_key",
        "created_at",
    }
)
_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "approval_id",
        "request_id",
        "sequence",
        "incident_id",
        "report_id",
        "report_version",
        "subject_id",
        "status",
        "approval_type",
        "action_hash",
        "scope",
        "requested_by",
        "decided_by",
        "reason",
        "requested_at",
        "decided_at",
        "expires_at",
        "idempotency_key",
    }
)
_ACTION_RUN_FIELDS = frozenset(
    {
        "schema_version",
        "action_run_id",
        "incident_id",
        "action_id",
        "action_hash",
        "status",
        "idempotency_key",
        "attempt",
        "started_at",
        "finished_at",
        "output_summary",
        "error",
        "metadata",
    }
)
_VERIFICATION_RUN_FIELDS = frozenset(
    {
        "schema_version",
        "verification_id",
        "incident_id",
        "action_run_ids",
        "status",
        "checks",
        "observed_kpis",
        "evidence_refs",
        "summary",
        "error",
        "started_at",
        "finished_at",
        "metadata",
    }
)
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "imsi",
        "msisdn",
        "imei",
        "imeisv",
        "supi",
        "suci",
        "subscriberid",
        "subscriberidentity",
        "subscriberpermanentidentifier",
        "subscriptionconcealedidentifier",
    }
)
_LABELED_SUBSCRIBER_ID = re.compile(
    r"(?i)\b(?:imsi|msisdn|imei|imeisv|supi|suci)\b"
    r"(?:\s*[-:=]\s*|\s+)(?:imsi-)?[+a-z0-9_-]{6,}"
)
_TERMINAL_STREAM_STATES = frozenset(
    {"input_required", "completed", "failed", "canceled", "rejected"}
)
_KNOWN_STREAM_STATES = _TERMINAL_STREAM_STATES | {"submitted", "working"}
_MISSING = object()


class A2AContentError(ValueError):
    """A message or artifact does not contain one unambiguous safe payload."""


class A2AStreamProtocolError(RuntimeError):
    """A remote event stream violated its bound task lifecycle."""


@dataclass(frozen=True, slots=True)
class CanonicalContent:
    """One authoritative DataPart plus optional presentation text."""

    data: dict[str, Any]
    text: str | None
    message_id: str


@dataclass(frozen=True, slots=True)
class RemoteStreamOutcome:
    """A stream outcome that exists only after an explicit interrupt/terminal."""

    state: str
    task_id: str
    context_id: str
    content: CanonicalContent | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class AssuranceScanRequest:
    """Transport binding and canonical payload for one new scan task."""

    data: dict[str, Any]
    a2a_context_id: str


def _field(value: object, *names: str, default: object = _MISSING) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    if default is not _MISSING:
        return default
    raise A2AContentError(f"missing required field {names[0]}")


def _optional_string(value: object, *names: str) -> str | None:
    result = _field(value, *names, default=None)
    if result is None:
        return None
    if not isinstance(result, str) or not result or len(result) > MAX_ID_CHARS:
        raise A2AContentError(f"invalid {names[0]}")
    return result


def _parts(value: object) -> list[object]:
    raw = value if isinstance(value, (list, tuple)) else _field(value, "parts")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise A2AContentError("parts must be a sequence")
    return list(raw)


def _part_root(part: object) -> object:
    return _field(part, "root", default=part)


def _part_kind(root: object) -> str:
    kind = _field(root, "kind", "type", default=None)
    if not isinstance(kind, str) or not kind:
        raise A2AContentError("part kind is missing")
    return kind.lower().replace("-", "_")


def _payload_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        if depth > MAX_CANONICAL_DEPTH:
            return depth
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                raise A2AContentError("canonical payload contains a cycle")
            seen.add(identity)
            stack.extend((nested, depth + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                raise A2AContentError("canonical payload contains a cycle")
            seen.add(identity)
            stack.extend((nested, depth + 1) for nested in current)
    return maximum


def _strict_object(
    value: object, expected_fields: frozenset[str], *, label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise A2AContentError(f"{label} has invalid fields")
    return value


def _allowed_object(
    value: object,
    allowed_fields: frozenset[str],
    required_fields: frozenset[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise A2AContentError(f"{label} has invalid fields")
    actual_fields = set(value)
    if not actual_fields <= allowed_fields or not required_fields <= actual_fields:
        raise A2AContentError(f"{label} has invalid fields")
    return value


def _versioned_object(
    value: object,
    allowed_fields: frozenset[str],
    required_fields: frozenset[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    result = _allowed_object(
        value, allowed_fields, required_fields, label=label
    )
    if result.get("schema_version", "1.0") != "1.0":
        raise A2AContentError(f"{label} has invalid schema_version")
    return result


def _identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ID_CHARS:
        raise A2AContentError(f"{label} is invalid")
    return value


def _optional_identifier(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, label=label)


def _text_value(
    value: object,
    *,
    label: str,
    maximum: int = MAX_TEXT_CHARS,
    allow_empty: bool = True,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not allow_empty and not value)
    ):
        raise A2AContentError(f"{label} is invalid")
    return value


def _optional_text(
    value: object, *, label: str, maximum: int = MAX_TEXT_CHARS
) -> str | None:
    if value is None:
        return None
    return _text_value(value, label=label, maximum=maximum)


def _list_value(value: object, *, label: str, maximum: int | None = None) -> list[Any]:
    if not isinstance(value, list) or (maximum is not None and len(value) > maximum):
        raise A2AContentError(f"{label} is invalid")
    return value


def _integer(
    value: object, *, label: str, minimum: int, maximum: int | None = None
) -> int:
    if (
        type(value) is not int
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise A2AContentError(f"{label} is invalid")
    return value


def _number(value: object, *, label: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise A2AContentError(f"{label} is invalid")
    return value


def _sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise A2AContentError(f"{label} is invalid")
    return value


def _optional_sha256(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, label=label)


def _string_list(
    value: object,
    *,
    label: str,
    maximum: int | None = None,
    identifiers: bool = False,
) -> list[str]:
    result = _list_value(value, label=label, maximum=maximum)
    checked = [
        _identifier(item, label=label)
        if identifiers
        else _text_value(item, label=label)
        for item in result
    ]
    if len(checked) != len(set(checked)):
        raise A2AContentError(f"{label} contains duplicates")
    return checked


def _string_mapping(value: object, *, label: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise A2AContentError(f"{label} is invalid")
    for key, nested in value.items():
        _text_value(key, label=f"{label} key", maximum=MAX_ID_CHARS)
        _text_value(nested, label=f"{label} value")
    return value


def _json_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise A2AContentError(f"{label} is invalid")
    return value


def _reject_sensitive_or_oversized_strings(value: object) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, nested in current.items():
                if not isinstance(key, str) or len(key) > MAX_ID_CHARS:
                    raise A2AContentError("canonical payload has invalid field name")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if _LABELED_SUBSCRIBER_ID.search(key) or (
                    normalized in _SENSITIVE_KEY_NAMES
                    and nested not in (None, "", [], {})
                ):
                    raise A2AContentError("sensitive subscriber data is not allowed")
                stack.append(nested)
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            if len(current) > MAX_TEXT_CHARS:
                raise A2AContentError("canonical string exceeds the limit")
            if _LABELED_SUBSCRIBER_ID.search(current):
                raise A2AContentError("sensitive subscriber data is not allowed")


def _utc_pair(
    start_value: object,
    end_value: object,
    *,
    label: str,
    maximum_days: int | None = None,
) -> None:
    if (start_value is None) != (end_value is None):
        raise A2AContentError(f"{label} window must be provided together")
    if start_value is None:
        return
    start = _parse_utc_iso(start_value, field_name=f"{label}_start")
    end = _parse_utc_iso(end_value, field_name=f"{label}_end")
    if end < start:
        raise A2AContentError(f"{label} window has invalid order")
    if maximum_days is not None and end - start > timedelta(days=maximum_days):
        raise A2AContentError(f"{label} window exceeds the limit")


def _validate_candidate_resource(value: object) -> None:
    resource = dict(
        _allowed_object(
            value,
            _CANDIDATE_RESOURCE_FIELDS,
            frozenset({"resource_id", "resource_type"}),
            label="candidate resource",
        )
    )
    resource.setdefault("technology", None)
    _identifier(resource.get("resource_id"), label="candidate resource_id")
    _text_value(
        resource.get("resource_type"),
        label="candidate resource_type",
        maximum=64,
        allow_empty=False,
    )
    if resource.get("technology") is not None:
        _text_value(
            resource.get("technology"),
            label="candidate resource technology",
            maximum=64,
            allow_empty=False,
        )


def _validate_candidate_kpi(value: object) -> None:
    kpi = dict(
        _allowed_object(
            value,
            _CANDIDATE_KPI_FIELDS,
            frozenset(
                {
                    "kpi_name",
                    "observed_value",
                    "threshold_value",
                    "comparator",
                    "sample_count",
                }
            ),
            label="candidate KPI",
        )
    )
    kpi.setdefault("unit", None)
    _text_value(kpi.get("kpi_name"), label="candidate KPI name", maximum=1_024)
    _number(kpi.get("observed_value"), label="candidate observed_value")
    _number(kpi.get("threshold_value"), label="candidate threshold_value")
    _text_value(kpi.get("comparator"), label="candidate comparator", maximum=64)
    _optional_text(kpi.get("unit"), label="candidate unit", maximum=256)
    _integer(kpi.get("sample_count"), label="candidate sample_count", minimum=0)


def _validate_candidate(value: object) -> None:
    candidate = dict(
        _allowed_object(
            value,
            _CANDIDATE_FIELDS,
            frozenset(
                {
                    "candidate_id",
                    "technology",
                    "window_start",
                    "window_end",
                }
            ),
            label="candidate",
        )
    )
    for field_name, default in (
        ("title", ""),
        ("affected_resources", []),
        ("violated_kpis", []),
        ("summary_zh", ""),
    ):
        candidate.setdefault(field_name, default)
    _identifier(candidate.get("candidate_id"), label="candidate_id")
    _text_value(candidate.get("title"), label="candidate title", maximum=1_024)
    _text_value(
        candidate.get("technology"),
        label="candidate technology",
        maximum=64,
        allow_empty=False,
    )
    _utc_pair(
        candidate.get("window_start"),
        candidate.get("window_end"),
        label="candidate",
        maximum_days=31,
    )
    resources = _list_value(
        candidate.get("affected_resources"),
        label="candidate affected_resources",
        maximum=100,
    )
    for resource in resources:
        _validate_candidate_resource(resource)
    kpis = _list_value(
        candidate.get("violated_kpis"), label="candidate violated_kpis", maximum=32
    )
    for kpi in kpis:
        _validate_candidate_kpi(kpi)
    _text_value(candidate.get("summary_zh"), label="candidate summary_zh")


def _validate_resource_reference(value: object) -> None:
    resource = dict(
        _versioned_object(
            value,
            _RESOURCE_REFERENCE_FIELDS,
            frozenset({"resource_id", "resource_type"}),
            label="resource",
        )
    )
    for field_name, default in (
        ("name", None),
        ("technology", None),
        ("vendor_profile", None),
        ("parent_resource_id", None),
        ("location_id", None),
        ("external_ids", {}),
        ("attributes", {}),
    ):
        resource.setdefault(field_name, default)
    _identifier(resource.get("resource_id"), label="resource_id")
    _text_value(resource.get("resource_type"), label="resource_type", maximum=64)
    for field_name in (
        "name",
        "technology",
        "vendor_profile",
        "parent_resource_id",
        "location_id",
    ):
        _optional_text(resource.get(field_name), label=f"resource {field_name}", maximum=1_024)
    _string_mapping(resource.get("external_ids"), label="resource external_ids")
    _json_mapping(resource.get("attributes"), label="resource attributes")


def _validate_kpi_violation(value: object) -> None:
    kpi = dict(
        _versioned_object(
            value,
            _KPI_VIOLATION_FIELDS,
            frozenset(
                {"kpi_name", "observed_value", "threshold_value", "comparator"}
            ),
            label="KPI violation",
        )
    )
    for field_name, default in (
        ("violation_id", None),
        ("unit", None),
        ("window_start", None),
        ("window_end", None),
        ("rule_id", None),
        ("rule_version", None),
        ("resource_ids", []),
        ("dimensions", {}),
    ):
        kpi.setdefault(field_name, default)
    _optional_identifier(kpi.get("violation_id"), label="violation_id")
    _text_value(kpi.get("kpi_name"), label="KPI name", maximum=1_024, allow_empty=False)
    _number(kpi.get("observed_value"), label="KPI observed_value")
    _number(kpi.get("threshold_value"), label="KPI threshold_value")
    if kpi.get("comparator") not in {"LT", "LTE", "GT", "GTE", "EQ", "NE"}:
        raise A2AContentError("KPI comparator is invalid")
    _optional_text(kpi.get("unit"), label="KPI unit", maximum=256)
    _utc_pair(kpi.get("window_start"), kpi.get("window_end"), label="KPI")
    _optional_identifier(kpi.get("rule_id"), label="KPI rule_id")
    _optional_text(kpi.get("rule_version"), label="KPI rule_version", maximum=1_024)
    _string_list(kpi.get("resource_ids"), label="KPI resource_ids", identifiers=True)
    _string_mapping(kpi.get("dimensions"), label="KPI dimensions")


def _validate_evidence_reference(value: object) -> None:
    evidence = dict(
        _versioned_object(
            value,
            _EVIDENCE_REFERENCE_FIELDS,
            frozenset({"evidence_id", "evidence_type", "uri"}),
            label="evidence reference",
        )
    )
    for field_name, default in (
        ("source", None),
        ("summary", None),
        ("collected_at", None),
        ("content_type", None),
        ("checksum_sha256", None),
        ("attributes", {}),
    ):
        evidence.setdefault(field_name, default)
    _identifier(evidence.get("evidence_id"), label="evidence_id")
    _text_value(evidence.get("evidence_type"), label="evidence_type", maximum=64)
    _text_value(evidence.get("uri"), label="evidence uri", maximum=1_024, allow_empty=False)
    for field_name in ("source", "summary", "content_type"):
        _optional_text(evidence.get(field_name), label=f"evidence {field_name}", maximum=1_024)
    if evidence.get("collected_at") is not None:
        _parse_utc_iso(evidence.get("collected_at"), field_name="evidence collected_at")
    _optional_sha256(evidence.get("checksum_sha256"), label="evidence checksum")
    _json_mapping(evidence.get("attributes"), label="evidence attributes")


def _validate_remediation(value: object) -> None:
    action = dict(
        _versioned_object(
            value,
            _REMEDIATION_FIELDS,
            frozenset({"action_id", "target_resources"}),
            label="remediation",
        )
    )
    for field_name, default in (
        ("action_type", "CUSTOM"),
        ("description", None),
        ("parameters", {}),
        ("risk_level", "MEDIUM"),
        ("requires_approval", True),
        ("reversible", False),
        ("rollback_plan", None),
        ("expected_outcome", None),
        ("idempotency_key", None),
    ):
        action.setdefault(field_name, default)
    _identifier(action.get("action_id"), label="remediation action_id")
    _text_value(action.get("action_type"), label="remediation action_type", maximum=1_024, allow_empty=False)
    for field_name in ("description", "rollback_plan", "expected_outcome"):
        _optional_text(action.get(field_name), label=f"remediation {field_name}")
    targets = _list_value(action.get("target_resources"), label="remediation targets")
    if not targets:
        raise A2AContentError("remediation targets are invalid")
    for target in targets:
        _validate_resource_reference(target)
    _json_mapping(action.get("parameters"), label="remediation parameters")
    if action.get("risk_level") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        raise A2AContentError("remediation risk_level is invalid")
    if action.get("requires_approval") is not True or type(action.get("reversible")) is not bool:
        raise A2AContentError("remediation approval fields are invalid")
    _optional_identifier(action.get("idempotency_key"), label="remediation idempotency_key")
    if "created_at" in action:
        _parse_utc_iso(action.get("created_at"), field_name="remediation created_at")


def _validate_rca_report(value: object) -> None:
    report = dict(
        _versioned_object(
            value,
            _RCA_REPORT_FIELDS,
            frozenset({"report_id", "incident_id"}),
            label="RCA report",
        )
    )
    for field_name, default in (
        ("version", 1),
        ("status", "DRAFT"),
        ("title", None),
        ("summary", ""),
        ("hypotheses", []),
        ("root_cause", None),
        ("conclusion", "CONCLUSIVE"),
        ("confidence", None),
        ("evidence_refs", []),
        ("recommendations", []),
        ("generated_by", None),
        ("model_metadata", {}),
    ):
        report.setdefault(field_name, default)
    _identifier(report.get("report_id"), label="RCA report_id")
    _identifier(report.get("incident_id"), label="RCA incident_id")
    _integer(report.get("version"), label="RCA version", minimum=1)
    _text_value(report.get("status"), label="RCA status", maximum=64)
    _optional_text(report.get("title"), label="RCA title")
    _text_value(report.get("summary"), label="RCA summary")
    _string_list(report.get("hypotheses"), label="RCA hypotheses")
    _optional_text(report.get("root_cause"), label="RCA root_cause")
    _text_value(report.get("conclusion"), label="RCA conclusion", maximum=64)
    confidence = report.get("confidence")
    if confidence is not None and not 0 <= _number(confidence, label="RCA confidence") <= 1:
        raise A2AContentError("RCA confidence is invalid")
    for evidence in _list_value(report.get("evidence_refs"), label="RCA evidence_refs"):
        _validate_evidence_reference(evidence)
    for action in _list_value(report.get("recommendations"), label="RCA recommendations"):
        _validate_remediation(action)
    _optional_text(report.get("generated_by"), label="RCA generated_by")
    _json_mapping(report.get("model_metadata"), label="RCA model_metadata")
    if "created_at" in report:
        _parse_utc_iso(report.get("created_at"), field_name="RCA created_at")


def _validate_approval(value: object) -> None:
    approval = dict(
        _versioned_object(
            value,
            _APPROVAL_FIELDS,
            frozenset(
                {
                    "approval_id",
                    "request_id",
                    "sequence",
                    "incident_id",
                    "report_id",
                    "report_version",
                    "subject_id",
                    "idempotency_key",
                }
            ),
            label="approval",
        )
    )
    for field_name, default in (
        ("status", "PENDING"),
        ("approval_type", "NETWORK_ACTION"),
        ("action_hash", None),
        ("scope", []),
        ("requested_by", None),
        ("decided_by", None),
        ("reason", None),
        ("decided_at", None),
        ("expires_at", None),
    ):
        approval.setdefault(field_name, default)
    for field_name in (
        "approval_id",
        "request_id",
        "incident_id",
        "report_id",
        "subject_id",
        "idempotency_key",
    ):
        _identifier(approval.get(field_name), label=f"approval {field_name}")
    _integer(approval.get("sequence"), label="approval sequence", minimum=0)
    _integer(approval.get("report_version"), label="approval report_version", minimum=1)
    _text_value(approval.get("status"), label="approval status", maximum=64)
    _text_value(approval.get("approval_type"), label="approval type", maximum=64)
    _optional_sha256(approval.get("action_hash"), label="approval action_hash")
    for resource in _list_value(approval.get("scope"), label="approval scope"):
        _validate_resource_reference(resource)
    for field_name in ("requested_by", "decided_by"):
        _optional_identifier(approval.get(field_name), label=f"approval {field_name}")
    _optional_text(approval.get("reason"), label="approval reason")
    if "requested_at" in approval:
        _parse_utc_iso(approval.get("requested_at"), field_name="approval requested_at")
    for field_name in ("decided_at", "expires_at"):
        if approval.get(field_name) is not None:
            _parse_utc_iso(approval.get(field_name), field_name=f"approval {field_name}")


def _validate_action_run(value: object) -> None:
    run = dict(
        _versioned_object(
            value,
            _ACTION_RUN_FIELDS,
            frozenset({"action_run_id", "action_id"}),
            label="action run",
        )
    )
    for field_name, default in (
        ("incident_id", None),
        ("action_hash", None),
        ("status", "PENDING"),
        ("idempotency_key", None),
        ("attempt", 1),
        ("started_at", None),
        ("finished_at", None),
        ("output_summary", None),
        ("error", None),
        ("metadata", {}),
    ):
        run.setdefault(field_name, default)
    _identifier(run.get("action_run_id"), label="action_run_id")
    _optional_identifier(run.get("incident_id"), label="action run incident_id")
    _identifier(run.get("action_id"), label="action run action_id")
    _optional_sha256(run.get("action_hash"), label="action run action_hash")
    _text_value(run.get("status"), label="action run status", maximum=64)
    _optional_identifier(run.get("idempotency_key"), label="action run idempotency_key")
    _integer(run.get("attempt"), label="action run attempt", minimum=1)
    for field_name in ("started_at", "finished_at"):
        if run.get(field_name) is not None:
            _parse_utc_iso(run.get(field_name), field_name=f"action run {field_name}")
    for field_name in ("output_summary", "error"):
        _optional_text(run.get(field_name), label=f"action run {field_name}")
    _json_mapping(run.get("metadata"), label="action run metadata")


def _validate_verification_run(value: object) -> None:
    run = dict(
        _versioned_object(
            value,
            _VERIFICATION_RUN_FIELDS,
            frozenset({"verification_id"}),
            label="verification run",
        )
    )
    for field_name, default in (
        ("incident_id", None),
        ("action_run_ids", []),
        ("status", "PENDING"),
        ("checks", []),
        ("observed_kpis", []),
        ("evidence_refs", []),
        ("summary", None),
        ("error", None),
        ("started_at", None),
        ("finished_at", None),
        ("metadata", {}),
    ):
        run.setdefault(field_name, default)
    _identifier(run.get("verification_id"), label="verification_id")
    _optional_identifier(run.get("incident_id"), label="verification incident_id")
    _string_list(run.get("action_run_ids"), label="verification action_run_ids", identifiers=True)
    _text_value(run.get("status"), label="verification status", maximum=64)
    _string_list(run.get("checks"), label="verification checks")
    for kpi in _list_value(run.get("observed_kpis"), label="verification observed_kpis"):
        _validate_kpi_violation(kpi)
    for evidence in _list_value(run.get("evidence_refs"), label="verification evidence_refs"):
        _validate_evidence_reference(evidence)
    for field_name in ("summary", "error"):
        _optional_text(run.get(field_name), label=f"verification {field_name}")
    for field_name in ("started_at", "finished_at"):
        if run.get(field_name) is not None:
            _parse_utc_iso(run.get(field_name), field_name=f"verification {field_name}")
    _json_mapping(run.get("metadata"), label="verification metadata")


def _validate_incident(value: object) -> None:
    incident = dict(
        _versioned_object(
            value,
            _INCIDENT_FIELDS,
            frozenset({"incident_id", "trace_id"}),
            label="incident",
        )
    )
    for field_name, default in (
        ("correlation_key", None),
        ("source_event_ids", []),
        ("technology", "UNKNOWN"),
        ("vendor_profile", None),
        ("status", "DETECTED"),
        ("severity", "UNKNOWN"),
        ("title", ""),
        ("description", ""),
        ("affected_resources", []),
        ("window_start", None),
        ("window_end", None),
        ("violated_kpis", []),
        ("evidence_refs", []),
        ("hypotheses", []),
        ("root_cause", None),
        ("rca_reports", []),
        ("recommendations", []),
        ("approvals", []),
        ("action_runs", []),
        ("verification_runs", []),
        ("model_metadata", {}),
        ("rule_versions", {}),
        ("duplicate_of", None),
        ("revision", 0),
    ):
        incident.setdefault(field_name, default)
    _identifier(incident.get("incident_id"), label="incident_id")
    _optional_identifier(incident.get("correlation_key"), label="incident correlation_key")
    _string_list(incident.get("source_event_ids"), label="incident source_event_ids", identifiers=True)
    _text_value(incident.get("technology"), label="incident technology", maximum=64)
    _optional_text(incident.get("vendor_profile"), label="incident vendor_profile", maximum=1_024)
    _text_value(incident.get("status"), label="incident status", maximum=64)
    _text_value(incident.get("severity"), label="incident severity", maximum=64)
    _text_value(incident.get("title"), label="incident title")
    _text_value(incident.get("description"), label="incident description")
    for resource in _list_value(incident.get("affected_resources"), label="incident resources"):
        _validate_resource_reference(resource)
    detected_at = (
        _parse_utc_iso(incident.get("detected_at"), field_name="incident detected_at")
        if "detected_at" in incident
        else None
    )
    _utc_pair(incident.get("window_start"), incident.get("window_end"), label="incident")
    if detected_at is not None and incident.get("window_end") is not None and _parse_utc_iso(
        incident.get("window_end"), field_name="incident window_end"
    ) > detected_at:
        raise A2AContentError("incident window_end is invalid")
    for kpi in _list_value(incident.get("violated_kpis"), label="incident violated_kpis"):
        _validate_kpi_violation(kpi)
    for evidence in _list_value(incident.get("evidence_refs"), label="incident evidence_refs"):
        _validate_evidence_reference(evidence)
    _string_list(incident.get("hypotheses"), label="incident hypotheses")
    _optional_text(incident.get("root_cause"), label="incident root_cause")
    for report in _list_value(incident.get("rca_reports"), label="incident rca_reports"):
        _validate_rca_report(report)
    for action in _list_value(incident.get("recommendations"), label="incident recommendations"):
        _validate_remediation(action)
    for approval in _list_value(incident.get("approvals"), label="incident approvals"):
        _validate_approval(approval)
    for run in _list_value(incident.get("action_runs"), label="incident action_runs"):
        _validate_action_run(run)
    for run in _list_value(incident.get("verification_runs"), label="incident verification_runs"):
        _validate_verification_run(run)
    _json_mapping(incident.get("model_metadata"), label="incident model_metadata")
    _string_mapping(incident.get("rule_versions"), label="incident rule_versions")
    _identifier(incident.get("trace_id"), label="incident trace_id")
    _optional_identifier(incident.get("duplicate_of"), label="incident duplicate_of")
    created_at = (
        _parse_utc_iso(incident.get("created_at"), field_name="incident created_at")
        if "created_at" in incident
        else None
    )
    updated_at = (
        _parse_utc_iso(incident.get("updated_at"), field_name="incident updated_at")
        if "updated_at" in incident
        else None
    )
    if created_at is not None and updated_at is not None and updated_at < created_at:
        raise A2AContentError("incident timeline is invalid")
    _integer(incident.get("revision"), label="incident revision", minimum=0)


def _validate_assurance_payload(decoded: Mapping[str, Any]) -> None:
    message_type = decoded["message_type"]
    if message_type == "assurance_scan_request":
        _utc_pair(
            decoded.get("window_start"),
            decoded.get("window_end"),
            label="scan",
            maximum_days=31,
        )
        resources = _string_list(
            decoded.get("resource_ids"),
            label="scan resource_ids",
            maximum=100,
            identifiers=True,
        )
        for resource_id in resources:
            segments = resource_id.split(":")
            if (
                len(segments) not in {3, 5}
                or segments[:2] != ["lte", "enodeb"]
                or (len(segments) == 5 and segments[3] != "cell")
            ):
                raise A2AContentError("scan resource_id is not canonical")
            for component in (segments[2], *(segments[4:5])):
                if (
                    not component.isdecimal()
                    or (len(component) > 1 and component.startswith("0"))
                    or int(component) > 268_435_455
                ):
                    raise A2AContentError("scan resource_id is not canonical")
        _integer(decoded.get("page_size"), label="scan page_size", minimum=1, maximum=20)
        _integer(decoded.get("page_offset"), label="scan page_offset", minimum=0, maximum=100)
    elif message_type == "assurance_candidate_page":
        _identifier(decoded.get("request_message_id"), label="candidate request_message_id")
        candidates = _list_value(decoded.get("candidates"), label="candidates", maximum=20)
        for candidate in candidates:
            _validate_candidate(candidate)
        _integer(decoded.get("page_size"), label="candidate page_size", minimum=1, maximum=20)
        _integer(decoded.get("page_offset"), label="candidate page_offset", minimum=0, maximum=100)
        _integer(decoded.get("total_candidates"), label="candidate total_candidates", minimum=0, maximum=100)
        if type(decoded.get("has_more")) is not bool:
            raise A2AContentError("candidate has_more is invalid")
        challenge = decoded.get("challenge_id")
        if challenge is not None and (
            not isinstance(challenge, str) or not 32 <= len(challenge) <= MAX_ID_CHARS
        ):
            raise A2AContentError("candidate challenge_id is invalid")
        _sha256(decoded.get("snapshot_sha256"), label="candidate snapshot_sha256")
        expires = decoded.get("challenge_expires_at")
        if expires is not None:
            _parse_utc_iso(expires, field_name="challenge_expires_at")
        if bool(candidates) != bool(challenge) or bool(candidates) != bool(expires):
            raise A2AContentError("candidate challenge presence is invalid")
        _utc_pair(
            decoded.get("effective_window_start"),
            decoded.get("effective_window_end"),
            label="effective_window",
            maximum_days=31,
        )
        _text_value(decoded.get("summary_zh"), label="candidate page summary_zh")
    elif message_type == "assurance_confirmation_request":
        business_ids = [
            _identifier(decoded.get("preview_message_id"), label="preview_message_id"),
            _identifier(decoded.get("candidate_id"), label="candidate_id"),
            _identifier(decoded.get("challenge_id"), label="challenge_id"),
            _sha256(decoded.get("snapshot_sha256"), label="snapshot_sha256"),
        ]
        if len(set((*business_ids, *(decoded[name] for name in _COMMON_FIELDS[2:6])))) != 8:
            raise A2AContentError("confirmation identifiers must remain independent")
        if decoded.get("decision") not in {"CONFIRM", "REJECT"}:
            raise A2AContentError("confirmation decision is invalid")
        _text_value(decoded.get("reason"), label="confirmation reason", allow_empty=False)
    elif message_type == "assurance_confirmation_result":
        for field_name in ("request_message_id", "preview_message_id", "candidate_id", "actor"):
            _identifier(decoded.get(field_name), label=f"confirmation result {field_name}")
        decision = decoded.get("decision")
        outcome = decoded.get("outcome")
        if decision not in {"CONFIRM", "REJECT"} or outcome not in {
            "created",
            "correlated",
            "replayed",
            "rejected",
        }:
            raise A2AContentError("confirmation result outcome is invalid")
        incident = decoded.get("incident")
        if decision == "REJECT":
            if outcome != "rejected" or incident is not None:
                raise A2AContentError("rejected confirmation result is invalid")
        else:
            if outcome == "rejected" or incident is None:
                raise A2AContentError("confirmed confirmation result is invalid")
            _validate_incident(incident)
        _text_value(decoded.get("summary_zh"), label="confirmation result summary_zh")
    elif message_type == "assurance_analyze_request":
        _identifier(decoded.get("incident_id"), label="analyze incident_id")
        _integer(
            decoded.get("requested_report_version"),
            label="requested_report_version",
            minimum=1,
            maximum=1_000,
        )


def _canonical_data(value: object) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, Mapping):
        raise A2AContentError("DataPart data must be an object")
    if _payload_depth(value) > MAX_CANONICAL_DEPTH:
        raise A2AContentError("canonical payload depth exceeds the limit")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise A2AContentError("canonical payload is not JSON-safe") from None
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise A2AContentError("canonical payload size exceeds the limit")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - guarded by Mapping
        raise A2AContentError("canonical payload must be an object")
    message_type = decoded.get("message_type")
    if message_type not in _ASSURANCE_MESSAGE_TYPES:
        raise A2AContentError("unsupported assurance message_type")
    _strict_object(decoded, _MESSAGE_FIELDS[message_type], label=message_type)
    _reject_sensitive_or_oversized_strings(decoded)
    if message_type == "assurance_error":
        error_code = decoded.get("error_code")
        if (
            not isinstance(error_code, str)
            or not 3 <= len(error_code) <= 64
            or not error_code[0].isupper()
            or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in error_code)
        ):
            raise A2AContentError("assurance_error has invalid error_code")
        _text_value(
            decoded.get("summary_zh"),
            label="assurance_error summary_zh",
            maximum=512,
        )
        required_fields = ("schema_version", "message_type", "message_id", "sent_at")
    else:
        required_fields = _COMMON_FIELDS
    for name in required_fields:
        field_value = decoded.get(name)
        if not isinstance(field_value, str) or not field_value:
            raise A2AContentError(f"canonical payload has invalid {name}")
        if len(field_value) > (MAX_TEXT_CHARS if name == "sent_at" else MAX_ID_CHARS):
            raise A2AContentError(f"canonical payload has oversized {name}")
    if decoded["schema_version"] != "1.0":
        raise A2AContentError("unsupported schema_version")
    _parse_utc_iso(decoded.get("sent_at"), field_name="sent_at")
    if message_type != "assurance_error":
        correlation_ids = {
            decoded["message_id"],
            decoded["workflow_id"],
            decoded["trace_id"],
            decoded["idempotency_key"],
        }
        if len(correlation_ids) != 4:
            raise A2AContentError(
                "canonical correlation identifiers must remain independent"
            )
        _validate_assurance_payload(decoded)
    return decoded, encoded


def _bounded_text(value: object) -> str:
    if not isinstance(value, str):
        raise A2AContentError("TextPart text must be a string")
    if not value or len(value) > MAX_TEXT_CHARS:
        raise A2AContentError("TextPart length exceeds the limit")
    _reject_sensitive_or_oversized_strings(value)
    return value


def _assert_container_ids(
    content: object,
    data_message_id: str,
    *,
    expected_message_id: str | None,
    expected_task_id: str | None,
    expected_context_id: str | None,
) -> None:
    transport_message_id = _optional_string(content, "message_id", "messageId")
    if transport_message_id is not None and transport_message_id != data_message_id:
        raise A2AContentError("message identifier mismatch")
    if expected_message_id is not None and expected_message_id != data_message_id:
        raise A2AContentError("message identifier mismatch")

    transport_task_id = _optional_string(content, "task_id", "taskId")
    if (
        expected_task_id is not None
        and transport_task_id is not None
        and transport_task_id != expected_task_id
    ):
        raise A2AContentError("task identifier mismatch")
    transport_context_id = _optional_string(content, "context_id", "contextId")
    if (
        expected_context_id is not None
        and transport_context_id is not None
        and transport_context_id != expected_context_id
    ):
        raise A2AContentError("context identifier mismatch")


def decode_canonical_parts(
    content: object,
    *,
    expected_message_id: str | None = None,
    expected_task_id: str | None = None,
    expected_context_id: str | None = None,
) -> CanonicalContent:
    """Decode exactly one DataPart and at most one TextPart, in any order."""

    raw_parts = _parts(content)
    if not 1 <= len(raw_parts) <= 2:
        raise A2AContentError("canonical content must contain one or two parts")
    data_values: list[object] = []
    texts: list[str] = []
    for part in raw_parts:
        root = _part_root(part)
        kind = _part_kind(root)
        if kind == "data":
            data_values.append(_field(root, "data"))
        elif kind == "text":
            texts.append(_bounded_text(_field(root, "text")))
        else:
            raise A2AContentError(f"unsupported part kind: {kind}")
    if len(data_values) != 1 or len(texts) > 1:
        raise A2AContentError(
            "canonical content requires exactly one DataPart and at most one TextPart"
        )
    data, _ = _canonical_data(data_values[0])
    message_id = data["message_id"]
    _assert_container_ids(
        content,
        message_id,
        expected_message_id=expected_message_id,
        expected_task_id=expected_task_id,
        expected_context_id=expected_context_id,
    )
    return CanonicalContent(
        data=data,
        text=texts[0] if texts else None,
        message_id=message_id,
    )


def decode_display_parts(
    content: object,
    *,
    expected_task_id: str | None = None,
    expected_context_id: str | None = None,
) -> str:
    """Decode one bounded TextPart; structured data is never display fallback."""

    raw_parts = _parts(content)
    if len(raw_parts) != 1:
        raise A2AContentError("display content must contain exactly one TextPart")
    root = _part_root(raw_parts[0])
    if _part_kind(root) != "text":
        raise A2AContentError("display content must be TextPart-only")
    transport_task_id = _optional_string(content, "task_id", "taskId")
    if (
        expected_task_id is not None
        and transport_task_id is not None
        and transport_task_id != expected_task_id
    ):
        raise A2AContentError("task identifier mismatch")
    transport_context_id = _optional_string(content, "context_id", "contextId")
    if (
        expected_context_id is not None
        and transport_context_id is not None
        and transport_context_id != expected_context_id
    ):
        raise A2AContentError("context identifier mismatch")
    return _bounded_text(_field(root, "text"))


def _normalized_state(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise A2AStreamProtocolError("unknown task state")
    normalized = raw.lower().replace("-", "_")
    if normalized.startswith("taskstate."):
        normalized = normalized.split(".", 1)[1]
    if normalized not in _KNOWN_STREAM_STATES:
        raise A2AStreamProtocolError("unknown task state")
    return normalized


def _stream_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ID_CHARS:
        raise A2AStreamProtocolError(f"invalid {name} identifier")
    return value


class RemoteStreamStateMachine:
    """Fail-closed lifecycle tracker for one A2A streaming request."""

    def __init__(
        self,
        *,
        expected_task_id: str | None = None,
        expected_context_id: str | None = None,
        starting_state: str | None = None,
    ) -> None:
        self._task_id = (
            _stream_id("task", expected_task_id)
            if expected_task_id is not None
            else None
        )
        self._context_id = (
            _stream_id("context", expected_context_id)
            if expected_context_id is not None
            else None
        )
        self._state = _normalized_state(starting_state) if starting_state else None
        self._content: CanonicalContent | None = None
        self._finished = False

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def context_id(self) -> str | None:
        return self._context_id

    def _bind(self, task_id: object, context_id: object) -> None:
        task = _stream_id("task", task_id)
        context = _stream_id("context", context_id)
        if self._task_id is not None and task != self._task_id:
            raise A2AStreamProtocolError("task identifier mismatch")
        if self._context_id is not None and context != self._context_id:
            raise A2AStreamProtocolError("context identifier mismatch")
        self._task_id = task
        self._context_id = context

    def observe_task(self, task_id: object, context_id: object, state: object) -> None:
        if self._finished or self._state is not None:
            raise A2AStreamProtocolError("duplicate or out-of-order Task")
        self._bind(task_id, context_id)
        normalized = _normalized_state(state)
        if normalized != "submitted":
            raise A2AStreamProtocolError("initial Task must be submitted")
        self._state = normalized

    def observe_artifact(
        self,
        task_id: object,
        context_id: object,
        content: CanonicalContent | None,
    ) -> None:
        if self._finished or self._state is None:
            raise A2AStreamProtocolError("artifact arrived outside an active task")
        self._bind(task_id, context_id)
        if self._state not in {"working", "input_required"}:
            raise A2AStreamProtocolError("artifact arrived in an invalid task state")
        if content is not None:
            self._content = content

    def observe_status(
        self,
        task_id: object,
        context_id: object,
        state: object,
        *,
        final: bool,
        content: CanonicalContent | None = None,
    ) -> None:
        if self._finished or self._state is None:
            raise A2AStreamProtocolError("status arrived outside an active task")
        self._bind(task_id, context_id)
        normalized = _normalized_state(state)
        if normalized == "submitted":
            raise A2AStreamProtocolError("duplicate submitted status")
        if normalized == "working":
            if final or self._state not in {"submitted", "working", "input_required"}:
                raise A2AStreamProtocolError("invalid working status transition")
        elif normalized in _TERMINAL_STREAM_STATES:
            if not final:
                raise A2AStreamProtocolError("terminal or interrupt status must be final")
            if self._state not in {"submitted", "working", "input_required"}:
                raise A2AStreamProtocolError("invalid terminal status transition")
            self._finished = True
        if content is not None:
            self._content = content
        self._state = normalized

    def finish(self, *, text: str | None = None) -> RemoteStreamOutcome:
        if (
            not self._finished
            or self._state not in _TERMINAL_STREAM_STATES
            or self._task_id is None
            or self._context_id is None
        ):
            raise A2AStreamProtocolError("unexpected EOF before an explicit final status")
        return RemoteStreamOutcome(
            state=self._state,
            task_id=self._task_id,
            context_id=self._context_id,
            content=self._content,
            text=text,
        )


class UiThreadOwnership:
    """Thread-to-socket ownership with deterministic anti-confusion checks."""

    def __init__(self) -> None:
        self._thread_to_sid: dict[str, str] = {}
        self._sid_to_threads: dict[str, set[str]] = {}
        self._lock = RLock()

    @staticmethod
    def _identifier(name: str, value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_ID_CHARS:
            raise PermissionError(f"invalid {name}")
        return value

    def bind(self, thread_id: str, sid: str) -> None:
        thread = self._identifier("thread_id", thread_id)
        socket_id = self._identifier("socket session", sid)
        with self._lock:
            owner = self._thread_to_sid.get(thread)
            if owner is not None and owner != socket_id:
                raise PermissionError("thread is owned by another socket session")
            self._thread_to_sid[thread] = socket_id
            self._sid_to_threads.setdefault(socket_id, set()).add(thread)

    def require_owner(self, thread_id: str, sid: str) -> None:
        thread = self._identifier("thread_id", thread_id)
        socket_id = self._identifier("socket session", sid)
        with self._lock:
            if self._thread_to_sid.get(thread) != socket_id:
                raise PermissionError("socket session does not own the thread")

    def sid_for(self, thread_id: str) -> str:
        thread = self._identifier("thread_id", thread_id)
        with self._lock:
            sid = self._thread_to_sid.get(thread)
            if sid is None:
                raise PermissionError("thread has no socket owner")
            return sid

    def remove_sid(self, sid: str) -> tuple[str, ...]:
        socket_id = self._identifier("socket session", sid)
        with self._lock:
            threads = tuple(sorted(self._sid_to_threads.pop(socket_id, set())))
            for thread in threads:
                if self._thread_to_sid.get(thread) == socket_id:
                    del self._thread_to_sid[thread]
            return threads


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > MAX_ID_CHARS:
        raise A2AContentError(f"{field_name} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise A2AContentError(f"{field_name} must be a UTC timestamp") from None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != timedelta(0)
    ):
        raise A2AContentError(f"{field_name} must be a UTC timestamp")
    return parsed.astimezone(UTC)


def _validate_window(
    window_start: object,
    window_end: object,
    *,
    prefix: str,
) -> None:
    start = _parse_utc_iso(window_start, field_name=f"{prefix}_window_start")
    end = _parse_utc_iso(window_end, field_name=f"{prefix}_window_end")
    if end < start:
        raise A2AContentError(f"{prefix} effective_window has invalid order")
    if end - start > timedelta(days=31):
        raise A2AContentError(f"{prefix} window must not exceed 31 days")


def build_assurance_scan_request(
    *,
    sent_at: datetime | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> AssuranceScanRequest:
    """Create a fixed Local-profile scan; free-form user text is not parsed."""

    if (window_start is None) != (window_end is None):
        raise ValueError("window_start and window_end must both be null or both set")
    if window_start is not None and window_end is not None:
        try:
            _validate_window(window_start, window_end, prefix="scan")
        except A2AContentError as exc:
            raise ValueError(str(exc)) from None
    data = {
        "schema_version": "1.0",
        "message_type": "assurance_scan_request",
        "message_id": _new_id("message"),
        "workflow_id": _new_id("workflow"),
        "trace_id": _new_id("trace"),
        "idempotency_key": _new_id("scan"),
        "sent_at": _utc_text(sent_at),
        "window_start": window_start,
        "window_end": window_end,
        "resource_ids": [],
        "page_size": 1,
        "page_offset": 0,
    }
    context_id = _new_id("context")
    if len(
        {
            data["message_id"],
            data["workflow_id"],
            data["trace_id"],
            data["idempotency_key"],
            context_id,
        }
    ) != 5:  # pragma: no cover - UUID collision guard
        raise RuntimeError("generated identifiers are not independent")
    return AssuranceScanRequest(data=data, a2a_context_id=context_id)


def build_assurance_confirmation_request(
    candidate_page: Mapping[str, object],
    *,
    approved: bool,
    reason: str,
    sent_at: datetime | None = None,
    message_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Build confirmation solely from a validated server-held candidate page."""

    if type(approved) is not bool:
        raise TypeError("approved must be a boolean")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1_024:
        raise A2AContentError("confirmation reason is invalid")
    validated_page, _ = _canonical_data(candidate_page)
    if validated_page.get("message_type") != "assurance_candidate_page":
        raise A2AContentError("pending content is not an assurance candidate page")
    candidate_page = validated_page
    candidates = candidate_page.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise A2AContentError("pending candidate page must contain exactly one candidate")
    candidate = candidates[0]
    if not isinstance(candidate, Mapping):
        raise A2AContentError("pending candidate is invalid")
    required = {
        "message_id": candidate_page.get("message_id"),
        "request_message_id": candidate_page.get("request_message_id"),
        "workflow_id": candidate_page.get("workflow_id"),
        "trace_id": candidate_page.get("trace_id"),
        "challenge_id": candidate_page.get("challenge_id"),
        "snapshot_sha256": candidate_page.get("snapshot_sha256"),
        "candidate_id": candidate.get("candidate_id"),
        "effective_window_start": candidate_page.get("effective_window_start"),
        "effective_window_end": candidate_page.get("effective_window_end"),
        "challenge_expires_at": candidate_page.get("challenge_expires_at"),
    }
    for name, value in required.items():
        if not isinstance(value, str) or not value or len(value) > MAX_ID_CHARS:
            raise A2AContentError(f"pending candidate page has invalid {name}")
    if len(required["snapshot_sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in required["snapshot_sha256"]
    ):
        raise A2AContentError("pending candidate page has invalid snapshot_sha256")
    if len(required["challenge_id"]) < 32:
        raise A2AContentError("pending candidate page has invalid challenge_id")
    _validate_window(
        required["effective_window_start"],
        required["effective_window_end"],
        prefix="effective",
    )
    expires_at = _parse_utc_iso(
        required["challenge_expires_at"],
        field_name="challenge_expires_at",
    )
    sent_at_value = _parse_utc_iso(
        candidate_page.get("sent_at"),
        field_name="sent_at",
    )
    if expires_at <= sent_at_value:
        raise A2AContentError("pending candidate challenge is already expired")
    if (
        candidate_page.get("page_size") != 1
        or not isinstance(candidate_page.get("page_offset"), int)
        or not isinstance(candidate_page.get("total_candidates"), int)
        or candidate_page.get("total_candidates", 0) < 1
        or type(candidate_page.get("has_more")) is not bool
    ):
        raise A2AContentError("pending candidate page metadata is invalid")
    result = {
        "schema_version": "1.0",
        "message_type": "assurance_confirmation_request",
        "message_id": message_id or _new_id("message"),
        "workflow_id": required["workflow_id"],
        "trace_id": required["trace_id"],
        "idempotency_key": idempotency_key or _new_id("confirm"),
        "sent_at": _utc_text(sent_at),
        "preview_message_id": required["message_id"],
        "candidate_id": required["candidate_id"],
        "challenge_id": required["challenge_id"],
        "snapshot_sha256": required["snapshot_sha256"],
        "decision": "CONFIRM" if approved else "REJECT",
        "reason": reason.strip(),
    }
    if result["message_id"] == required["message_id"]:
        raise A2AContentError("confirmation message_id must differ from preview")
    if result["idempotency_key"] == candidate_page.get("idempotency_key"):
        raise A2AContentError("confirmation idempotency key must differ from preview")
    if len(
        {
            result["message_id"],
            result["workflow_id"],
            result["trace_id"],
            result["idempotency_key"],
        }
    ) != 4:
        raise A2AContentError(
            "confirmation correlation identifiers must remain independent"
        )
    validated_result, _ = _canonical_data(result)
    return validated_result


def validate_empty_assurance_candidate_page(
    candidate_page: Mapping[str, object],
) -> dict[str, Any]:
    """Validate the zero-candidate artifact that explicitly completes a scan."""

    decoded, _ = _canonical_data(candidate_page)
    if decoded.get("message_type") != "assurance_candidate_page":
        raise A2AContentError("completed scan artifact is not a candidate page")
    request_message_id = decoded.get("request_message_id")
    if (
        not isinstance(request_message_id, str)
        or not request_message_id
        or len(request_message_id) > MAX_ID_CHARS
    ):
        raise A2AContentError("empty candidate page has invalid request_message_id")
    if (
        decoded.get("candidates") != []
        or decoded.get("challenge_id") is not None
        or decoded.get("challenge_expires_at") is not None
    ):
        raise A2AContentError("empty candidate page must not contain a challenge")
    if (
        decoded.get("page_size") != 1
        or decoded.get("page_offset") != 0
        or decoded.get("total_candidates") != 0
        or decoded.get("has_more") is not False
    ):
        raise A2AContentError("empty candidate page metadata is invalid")
    snapshot = decoded.get("snapshot_sha256")
    if (
        not isinstance(snapshot, str)
        or len(snapshot) != 64
        or any(character not in "0123456789abcdef" for character in snapshot)
    ):
        raise A2AContentError("empty candidate page has invalid snapshot_sha256")
    _validate_window(
        decoded.get("effective_window_start"),
        decoded.get("effective_window_end"),
        prefix="effective",
    )
    return decoded


def parse_trusted_approval(content: object) -> bool:
    """Validate the exact bounded payload emitted by the approval UI widget."""

    if not isinstance(content, str) or len(content.encode("utf-8")) > 16_384:
        raise A2AContentError("approval result is invalid")
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, RecursionError):
        raise A2AContentError("approval result is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {
        "approved",
        "timestamp",
        "tasks",
    }:
        raise A2AContentError("approval result shape is invalid")
    approved = payload["approved"]
    timestamp = payload["timestamp"]
    tasks = payload["tasks"]
    if type(approved) is not bool:
        raise A2AContentError("approval decision must be a boolean")
    if not isinstance(timestamp, str) or not timestamp or len(timestamp) > 128:
        raise A2AContentError("approval timestamp is invalid")
    if not isinstance(tasks, list) or len(tasks) > 20:
        raise A2AContentError("approval task list is invalid")
    if any(not isinstance(item, Mapping) for item in tasks):
        raise A2AContentError("approval task list is invalid")
    return approved


def build_trusted_assurance_decision(
    candidate_page: Mapping[str, object],
    *,
    tool_call_id: object,
    tool_name: object,
    content: object,
) -> dict[str, Any]:
    """Bind an approval-widget boolean to the server-held candidate only."""

    if tool_name != "requestTaskApproval":
        raise PermissionError("Assurance decision is not from the approval tool")
    if (
        not isinstance(tool_call_id, str)
        or not tool_call_id
        or len(tool_call_id) > MAX_ID_CHARS * 2 + 2
    ):
        raise PermissionError("approval tool call identifier is invalid")
    approved = parse_trusted_approval(content)
    confirmation = build_assurance_confirmation_request(
        candidate_page,
        approved=approved,
        reason=(
            "用户已在受信任审批组件中确认。"
            if approved
            else "用户已在受信任审批组件中拒绝。"
        ),
    )
    return {
        "tool_call_id": tool_call_id,
        "approved": approved,
        "confirmation_data": confirmation,
    }


def tool_call_thread_id(tool_call_id: object, *, expected_thread_id: str) -> str:
    """Return the encoded UI thread only when it matches the current owner."""

    if (
        not isinstance(tool_call_id, str)
        or len(tool_call_id) > MAX_ID_CHARS * 2 + 2
        or tool_call_id.count("::") != 1
    ):
        raise PermissionError("tool call is not bound to exactly one thread")
    original_id, thread_id = tool_call_id.split("::", 1)
    if not original_id or not thread_id or thread_id != expected_thread_id:
        raise PermissionError("tool call is bound to another thread")
    return thread_id


async def emit_agui_events(
    events: Iterable[object] | AsyncIterable[object],
    sio: object,
    sid: str,
) -> None:
    """Emit continuation events only to one already-authorized Socket.IO room."""

    if not isinstance(sid, str) or not sid or len(sid) > MAX_ID_CHARS:
        raise PermissionError("invalid socket session")

    async def emit_one(event: object) -> None:
        if isinstance(event, Mapping):
            payload = dict(event)
        elif hasattr(event, "model_dump"):
            payload = event.model_dump()
        else:
            raise TypeError("AG-UI event must be a mapping or model")
        await sio.emit("agui_event", payload, room=sid)

    if isinstance(events, AsyncIterable):
        async for event in events:
            await emit_one(event)
    else:
        for event in events:
            await emit_one(event)


__all__ = [
    "A2AContentError",
    "A2AStreamProtocolError",
    "AssuranceScanRequest",
    "CanonicalContent",
    "MAX_CANONICAL_BYTES",
    "MAX_CANONICAL_DEPTH",
    "MAX_TEXT_CHARS",
    "RemoteStreamOutcome",
    "RemoteStreamStateMachine",
    "UiThreadOwnership",
    "build_assurance_confirmation_request",
    "build_assurance_scan_request",
    "build_trusted_assurance_decision",
    "decode_canonical_parts",
    "decode_display_parts",
    "emit_agui_events",
    "parse_trusted_approval",
    "tool_call_thread_id",
    "validate_empty_assurance_candidate_page",
]
