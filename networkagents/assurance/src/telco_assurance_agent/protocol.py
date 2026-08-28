"""Strict A2A DataPart contracts for the Local Assurance boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, TypeAlias, cast

from a2a.types import DataPart, FilePart, Message, Role, TextPart
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from telco_domain import (
    Incident,
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_SERIALIZED_BYTES,
    SensitiveDataError,
    assert_model_safe,
)
from telco_local.lte_identifiers import (
    canonical_lte_resource_id,
    parse_lte_resource_id,
)


MAX_DISPLAY_TEXT = 4_096
MAX_SCAN_DAYS = 31
MAX_SCAN_RESOURCES = 100
MAX_PAGE_SIZE = 20
MAX_PAGE_OFFSET = 100

OpaqueId: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
ShortText: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
Sha256Digest: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class AssuranceProtocolError(ValueError):
    """A safe, non-reflective protocol boundary failure."""


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value.astimezone(UTC)


def _depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, level = stack.pop()
        maximum = max(maximum, level)
        if level > MAX_CONTRACT_DEPTH:
            return level
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((nested, level + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((nested, level + 1) for nested in current)
    return maximum


def _assert_budget(value: object) -> None:
    if _depth(value) > MAX_CONTRACT_DEPTH:
        raise AssuranceProtocolError("canonical payload exceeds depth budget")
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            default=lambda item: item.isoformat()
            if isinstance(item, datetime)
            else (_ for _ in ()).throw(TypeError()),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise AssuranceProtocolError("canonical payload must be safe JSON") from None
    if len(encoded) > MAX_CONTRACT_SERIALIZED_BYTES:
        raise AssuranceProtocolError("canonical payload exceeds size budget")


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class AssuranceMessage(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    message_type: str
    message_id: OpaqueId
    workflow_id: OpaqueId
    trace_id: OpaqueId
    idempotency_key: OpaqueId
    sent_at: AwareDatetime

    @field_validator("sent_at")
    @classmethod
    def _sent_at_is_utc(cls, value: datetime) -> datetime:
        return _utc(value, field_name="sent_at")

    @model_validator(mode="after")
    def _correlation_ids_are_independent(self) -> "AssuranceMessage":
        values = (
            self.message_id,
            self.workflow_id,
            self.trace_id,
            self.idempotency_key,
        )
        if len(set(values)) != len(values):
            raise ValueError("correlation identifiers must remain independent")
        return self

    def to_data_part(self) -> dict[str, Any]:
        payload = cast(
            dict[str, Any], self.model_dump(mode="json", round_trip=True)
        )
        _assert_budget(payload)
        assert_model_safe(payload)
        return payload


class AssuranceScanRequest(AssuranceMessage):
    message_type: Literal["assurance_scan_request"] = "assurance_scan_request"
    window_start: AwareDatetime | None
    window_end: AwareDatetime | None
    resource_ids: tuple[str, ...] = Field(default=(), max_length=MAX_SCAN_RESOURCES)
    page_size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)
    page_offset: int = Field(default=0, ge=0, le=MAX_PAGE_OFFSET)

    @field_validator("window_start", "window_end")
    @classmethod
    def _window_values_are_utc(
        cls, value: datetime | None, info: Any
    ) -> datetime | None:
        return None if value is None else _utc(value, field_name=info.field_name)

    @field_validator("resource_ids")
    @classmethod
    def _resources_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            enodeb_id, cell_id = parse_lte_resource_id(value)
            resource_id = canonical_lte_resource_id(enodeb_id, cell_id)
            if resource_id in seen:
                raise ValueError("resource_ids must be unique")
            seen.add(resource_id)
            normalized.append(resource_id)
        return tuple(normalized)

    @model_validator(mode="after")
    def _window_is_bounded(self) -> "AssuranceScanRequest":
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        if self.window_start is not None and self.window_end is not None:
            if self.window_end < self.window_start:
                raise ValueError("window_end must not precede window_start")
            if self.window_end - self.window_start > timedelta(days=MAX_SCAN_DAYS):
                raise ValueError("scan window must not exceed 31 days")
        return self


class AssuranceConfirmationRequest(AssuranceMessage):
    message_type: Literal["assurance_confirmation_request"] = (
        "assurance_confirmation_request"
    )
    preview_message_id: OpaqueId
    candidate_id: OpaqueId
    challenge_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=32, max_length=256),
    ]
    snapshot_sha256: Sha256Digest
    decision: Literal["CONFIRM", "REJECT"]
    reason: ShortText


class AssuranceAnalyzeRequest(AssuranceMessage):
    message_type: Literal["assurance_analyze_request"] = "assurance_analyze_request"
    incident_id: OpaqueId
    requested_report_version: int = Field(default=1, ge=1, le=1_000)


class CandidateResourceSummary(_StrictModel):
    resource_id: OpaqueId
    resource_type: str
    technology: str | None = None


class CandidateKpiSummary(_StrictModel):
    kpi_name: str
    observed_value: float
    threshold_value: float
    comparator: str
    unit: str | None = None
    sample_count: int = Field(ge=0)


class AssuranceCandidateSummary(_StrictModel):
    candidate_id: OpaqueId
    title: Annotated[str, StringConstraints(max_length=1_024)] = ""
    technology: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    affected_resources: tuple[CandidateResourceSummary, ...] = Field(
        default=(), max_length=MAX_SCAN_RESOURCES
    )
    violated_kpis: tuple[CandidateKpiSummary, ...] = Field(
        default=(), max_length=32
    )
    summary_zh: Annotated[str, StringConstraints(max_length=4_096)] = ""

    @field_validator("window_start", "window_end")
    @classmethod
    def _candidate_window_is_utc(cls, value: datetime, info: Any) -> datetime:
        return _utc(value, field_name=info.field_name)


class AssuranceCandidatePage(AssuranceMessage):
    message_type: Literal["assurance_candidate_page"] = "assurance_candidate_page"
    request_message_id: OpaqueId
    candidates: tuple[AssuranceCandidateSummary, ...] = Field(
        default=(), max_length=MAX_PAGE_SIZE
    )
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    page_offset: int = Field(ge=0, le=MAX_PAGE_OFFSET)
    total_candidates: int = Field(ge=0, le=MAX_PAGE_OFFSET)
    has_more: bool
    challenge_id: str | None = Field(default=None, min_length=32, max_length=256)
    snapshot_sha256: Sha256Digest
    challenge_expires_at: AwareDatetime | None = None
    effective_window_start: AwareDatetime
    effective_window_end: AwareDatetime
    summary_zh: Annotated[str, StringConstraints(max_length=4_096)] = ""

    @field_validator(
        "challenge_expires_at", "effective_window_start", "effective_window_end"
    )
    @classmethod
    def _page_times_are_utc(
        cls, value: datetime | None, info: Any
    ) -> datetime | None:
        return None if value is None else _utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _challenge_matches_candidate_presence(self) -> "AssuranceCandidatePage":
        if bool(self.candidates) != bool(self.challenge_id):
            raise ValueError("candidate pages require exactly one challenge")
        if bool(self.candidates) != bool(self.challenge_expires_at):
            raise ValueError("candidate pages require a challenge expiry")
        if self.effective_window_end < self.effective_window_start:
            raise ValueError("effective window is invalid")
        return self


class AssuranceConfirmationResult(AssuranceMessage):
    message_type: Literal["assurance_confirmation_result"] = (
        "assurance_confirmation_result"
    )
    request_message_id: OpaqueId
    preview_message_id: OpaqueId
    candidate_id: OpaqueId
    decision: Literal["CONFIRM", "REJECT"]
    actor: OpaqueId
    outcome: Literal["created", "correlated", "replayed", "rejected"]
    incident: Incident | None = None
    summary_zh: Annotated[str, StringConstraints(max_length=4_096)] = ""

    @model_validator(mode="after")
    def _result_matches_decision(self) -> "AssuranceConfirmationResult":
        if self.decision == "REJECT":
            if self.outcome != "rejected" or self.incident is not None:
                raise ValueError("rejected confirmations cannot contain an Incident")
        elif self.outcome == "rejected" or self.incident is None:
            raise ValueError("confirmed results require an Incident")
        return self


class AssuranceError(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    message_type: Literal["assurance_error"] = "assurance_error"
    message_id: OpaqueId
    error_code: Annotated[
        str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    ]
    summary_zh: Annotated[str, StringConstraints(max_length=512)]
    sent_at: AwareDatetime

    @field_validator("sent_at")
    @classmethod
    def _error_time_is_utc(cls, value: datetime) -> datetime:
        return _utc(value, field_name="sent_at")

    def to_data_part(self) -> dict[str, Any]:
        payload = cast(dict[str, Any], self.model_dump(mode="json"))
        _assert_budget(payload)
        assert_model_safe(payload)
        return payload


AssuranceRequest = (
    AssuranceScanRequest
    | AssuranceConfirmationRequest
    | AssuranceAnalyzeRequest
)

_REQUEST_TYPES: dict[str, type[AssuranceRequest]] = {
    "assurance_scan_request": AssuranceScanRequest,
    "assurance_confirmation_request": AssuranceConfirmationRequest,
    "assurance_analyze_request": AssuranceAnalyzeRequest,
}

# Kept as an import-only compatibility alias; the wire discriminator remains
# the frozen ``assurance_analyze_request`` value above.
AssuranceAnalysisRequest = AssuranceAnalyzeRequest


def parse_request_message(message: Message) -> AssuranceRequest:
    """Parse one canonical DataPart; display text never drives an operation."""

    if message.role is not Role.user:
        raise AssuranceProtocolError("canonical request must have user role")
    data_parts: list[DataPart] = []
    text_parts: list[TextPart] = []
    for part in message.parts:
        root = part.root
        if isinstance(root, DataPart):
            data_parts.append(root)
        elif isinstance(root, TextPart):
            text_parts.append(root)
        elif isinstance(root, FilePart):
            raise AssuranceProtocolError("FilePart is not accepted")
        else:
            raise AssuranceProtocolError("unsupported A2A Part")
    if len(data_parts) != 1:
        raise AssuranceProtocolError("exactly one DataPart is required")
    if len(text_parts) > 1:
        raise AssuranceProtocolError("at most one TextPart is accepted")
    if text_parts:
        if len(text_parts[0].text) > MAX_DISPLAY_TEXT:
            raise AssuranceProtocolError("TextPart exceeds 4096 characters")
        try:
            assert_model_safe(text_parts[0].text)
        except SensitiveDataError:
            raise AssuranceProtocolError("display text rejected") from None

    payload = data_parts[0].data
    if not isinstance(payload, Mapping):
        raise AssuranceProtocolError("canonical DataPart must be an object")
    _assert_budget(payload)
    try:
        assert_model_safe(payload)
    except SensitiveDataError:
        raise AssuranceProtocolError("canonical request rejected") from None
    if payload.get("message_id") != message.message_id:
        raise AssuranceProtocolError("message identifier mismatch")
    message_type = payload.get("message_type")
    model = _REQUEST_TYPES.get(message_type) if isinstance(message_type, str) else None
    if model is None:
        raise AssuranceProtocolError("unsupported canonical request")
    try:
        request = model.model_validate(payload)
    except (ValidationError, ValueError, TypeError):
        raise AssuranceProtocolError("invalid canonical request") from None
    identifiers = [
        request.message_id,
        request.workflow_id,
        request.trace_id,
        request.idempotency_key,
    ]
    identifiers.extend(
        value for value in (message.task_id, message.context_id) if value is not None
    )
    if isinstance(request, AssuranceConfirmationRequest):
        identifiers.extend(
            (
                request.preview_message_id,
                request.candidate_id,
                request.challenge_id,
                request.snapshot_sha256,
            )
        )
    elif isinstance(request, AssuranceAnalyzeRequest):
        identifiers.append(request.incident_id)
    if len(identifiers) != len(set(identifiers)):
        raise AssuranceProtocolError("transport and business identifiers must be independent")
    return request


__all__ = [
    "AssuranceAnalysisRequest",
    "AssuranceAnalyzeRequest",
    "AssuranceCandidatePage",
    "AssuranceCandidateSummary",
    "AssuranceConfirmationRequest",
    "AssuranceConfirmationResult",
    "AssuranceError",
    "AssuranceProtocolError",
    "AssuranceRequest",
    "AssuranceScanRequest",
    "CandidateKpiSummary",
    "CandidateResourceSummary",
    "MAX_DISPLAY_TEXT",
    "parse_request_message",
]
