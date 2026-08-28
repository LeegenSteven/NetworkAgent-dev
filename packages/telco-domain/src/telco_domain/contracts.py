"""Strict, versioned, and framework-neutral inter-agent contracts.

These models are the canonical JSON payloads placed inside transport-specific
structured data parts.  This module deliberately imports no ADK, A2A, MCP, or
cloud SDK.  Free-form text is presentation-only and is never parsed back into a
command that can advance state or perform a write.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeAlias, cast

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

from .models import (
    ActionRun,
    ActionRunStatus,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalType,
    Incident,
    RcaReport,
    RemediationAction,
    ResourceReference,
    SCHEMA_VERSION,
    VerificationRun,
)
from .privacy import SensitiveDataError, assert_model_safe


CONTRACT_SCHEMA_VERSION = SCHEMA_VERSION
MAX_CONTRACT_SERIALIZED_BYTES = 256_000
MAX_CONTRACT_DEPTH = 24

# IDs remain opaque: current services use UUIDs, 32-character hex values, and
# legacy identifiers.  The boundary constrains resource consumption, not format.
OpaqueId: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
ShortText: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=4_096),
]
Sha256Digest: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class ContractDecodeError(ValueError):
    """A wire payload is not a supported canonical contract."""


class ContractPayloadLimitError(ContractDecodeError):
    """A structured payload exceeded the canonical size or depth budget."""


class ContractEncodeError(ValueError):
    """A validated model cannot safely cross the outbound contract boundary."""


class ApprovalAuthorizationError(ValueError):
    """An approval reference cannot authorize execution at the trusted instant."""


def _payload_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        if depth > MAX_CONTRACT_DEPTH:
            return depth
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((nested, depth + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((nested, depth + 1) for nested in current)
    return maximum


def _assert_payload_budget(value: object) -> None:
    depth = _payload_depth(value)
    if depth > MAX_CONTRACT_DEPTH:
        raise ContractPayloadLimitError(
            f"canonical payload depth exceeds {MAX_CONTRACT_DEPTH}"
        )
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ContractDecodeError("canonical payload must be JSON-safe") from None
    if len(encoded) > MAX_CONTRACT_SERIALIZED_BYTES:
        raise ContractPayloadLimitError(
            "canonical payload serialized size exceeds "
            f"{MAX_CONTRACT_SERIALIZED_BYTES} bytes"
        )


def _normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _normalize_optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)


class ContractModel(BaseModel):
    """Strict immutable base for nested contract-only proof objects."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class ApprovalReference(ContractModel):
    """Reference to a decision that an execution gateway must resolve afresh.

    This is not an authorization grant.  ``validated_at`` records the last
    lookup for audit purposes, but an ActionGateway must resolve ``request_id``
    again, choose its latest append-only event, and evaluate it with the
    gateway's own clock immediately before execution.
    """

    approval_id: OpaqueId
    request_id: OpaqueId
    decision_sequence: int = Field(ge=1)
    incident_id: OpaqueId
    report_id: OpaqueId
    report_version: int = Field(ge=1)
    subject_id: OpaqueId
    action_hash: Sha256Digest
    based_on_revision: int = Field(ge=0)
    validated_at: AwareDatetime
    validator_id: OpaqueId

    _utc_validated_at = field_validator("validated_at")(_normalize_utc)


# "Proof" is retained as an explicit semantic alias for callers that use that
# terminology.  The class documentation still makes the trust boundary clear.
ApprovalProof = ApprovalReference


class ContractMessage(ContractModel):
    """Correlation fields whose meanings remain independent of transport tasks."""

    schema_version: Literal["1.0"] = CONTRACT_SCHEMA_VERSION
    message_type: str
    message_id: OpaqueId
    workflow_id: OpaqueId
    incident_id: OpaqueId
    trace_id: OpaqueId
    idempotency_key: OpaqueId
    sent_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    _utc_sent_at = field_validator("sent_at")(_normalize_utc)

    def to_data_part(self) -> dict[str, Any]:
        """Return privacy-checked, budgeted, JSON-safe structured data."""

        payload = cast(
            dict[str, Any], self.model_dump(mode="json", round_trip=True)
        )
        _assert_payload_budget(payload)
        try:
            assert_model_safe(payload)
        except SensitiveDataError:
            raise ContractEncodeError(
                "canonical payload violates the privacy policy"
            ) from None
        return payload

    def to_display_text(self) -> str:
        """Return one-way display text containing correlation metadata only."""

        return (
            f"[{self.schema_version}] {self.message_type} "
            f"incident_id={self.incident_id} workflow_id={self.workflow_id} "
            f"trace_id={self.trace_id}"
        )


class IncidentTrigger(ContractMessage):
    """Submit one normalized candidate incident."""

    message_type: Literal["incident_trigger"] = "incident_trigger"
    incident: Incident
    summary_zh: ShortText = ""

    @model_validator(mode="after")
    def _identity_matches(self) -> "IncidentTrigger":
        if self.incident.incident_id != self.incident_id:
            raise ValueError("incident.incident_id must match envelope incident_id")
        if self.incident.trace_id != self.trace_id:
            raise ValueError("incident.trace_id must match envelope trace_id")
        return self


class RcaRequest(ContractMessage):
    """Request RCA for an exact incident revision and report version."""

    message_type: Literal["rca_request"] = "rca_request"
    incident: Incident
    based_on_revision: int = Field(ge=0)
    requested_report_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _snapshot_matches(self) -> "RcaRequest":
        if self.incident.incident_id != self.incident_id:
            raise ValueError("incident.incident_id must match envelope incident_id")
        if self.incident.trace_id != self.trace_id:
            raise ValueError("incident.trace_id must match envelope trace_id")
        if self.incident.revision != self.based_on_revision:
            raise ValueError("based_on_revision must match incident.revision")
        return self


class RcaResult(ContractMessage):
    """Return an RCA report bound to the revision/version requested."""

    message_type: Literal["rca_result"] = "rca_result"
    request_message_id: OpaqueId
    report: RcaReport
    based_on_revision: int = Field(ge=0)
    requested_report_version: int = Field(ge=1)
    summary_zh: ShortText = ""

    @model_validator(mode="after")
    def _result_binding_matches(self) -> "RcaResult":
        if self.report.incident_id != self.incident_id:
            raise ValueError("report.incident_id must match envelope incident_id")
        if self.report.version != self.requested_report_version:
            raise ValueError("report.version must match requested_report_version")
        return self


class ApprovalRequest(ContractMessage):
    """Request one append-only approval lifecycle for an immutable subject."""

    message_type: Literal["approval_request"] = "approval_request"
    request_id: OpaqueId
    approval_type: ApprovalType
    report: RcaReport
    action: RemediationAction | None = None
    scope: tuple[ResourceReference, ...] = ()
    based_on_revision: int = Field(ge=0)
    expires_at: AwareDatetime | None = None
    summary_zh: ShortText = ""

    _utc_expires_at = field_validator("expires_at")(_normalize_optional_utc)

    @model_validator(mode="after")
    def _approval_binding_is_complete(self) -> "ApprovalRequest":
        if self.report.incident_id != self.incident_id:
            raise ValueError("report.incident_id must match envelope incident_id")
        if self.approval_type is ApprovalType.NETWORK_ACTION:
            if self.action is None:
                raise ValueError("network action approval requires an action")
            if self.expires_at is None:
                raise ValueError("network action approval requires expires_at")
            if not self.scope:
                raise ValueError("network action approval requires non-empty scope")
            if _resource_identities(self.scope) != _resource_identities(
                self.action.target_resources
            ):
                raise ValueError("approval scope must exactly match action targets")
            if not _report_contains_action(self.report, self.action):
                raise ValueError("action must be an immutable recommendation in report")
        return self


class ApprovalResult(ContractMessage):
    """Return one append-only event in an approval request lifecycle."""

    message_type: Literal["approval_result"] = "approval_result"
    request_id: OpaqueId
    decision: ApprovalDecision
    summary_zh: ShortText = ""

    @model_validator(mode="after")
    def _decision_binding_matches(self) -> "ApprovalResult":
        if self.decision.request_id != self.request_id:
            raise ValueError("decision.request_id must match request_id")
        if self.decision.incident_id != self.incident_id:
            raise ValueError("decision.incident_id must match envelope incident_id")
        return self


class NetworkChangeRequest(ContractMessage):
    """Request execution using a reference that must be revalidated server-side."""

    message_type: Literal["network_change_request"] = "network_change_request"
    action: RemediationAction
    report: RcaReport
    based_on_revision: int = Field(ge=0)
    approval_reference: ApprovalReference

    @model_validator(mode="after")
    def _immutable_bindings_match(self) -> "NetworkChangeRequest":
        reference = self.approval_reference
        if self.report.incident_id != self.incident_id:
            raise ValueError("report.incident_id must match envelope incident_id")
        if reference.incident_id != self.incident_id:
            raise ValueError("approval reference is bound to another incident")
        if reference.report_id != self.report.report_id:
            raise ValueError("approval reference report_id does not match report")
        if reference.report_version != self.report.version:
            raise ValueError("approval reference report_version does not match report")
        if reference.subject_id != self.action.action_id:
            raise ValueError("approval reference subject_id does not match action")
        if reference.action_hash != self.action.compute_action_hash():
            raise ValueError("approval reference action_hash does not match action")
        if reference.based_on_revision != self.based_on_revision:
            raise ValueError("approval reference revision does not match request")
        if not _report_contains_action(self.report, self.action):
            raise ValueError("action must be an immutable recommendation in report")
        return self


class VerificationRequest(ContractMessage):
    """Request verification bound to a successful, immutable ActionRun."""

    message_type: Literal["verification_request"] = "verification_request"
    action_run: ActionRun
    verification: VerificationRun

    @model_validator(mode="after")
    def _action_run_binding_matches(self) -> "VerificationRequest":
        if self.action_run.status is not ActionRunStatus.SUCCEEDED:
            raise ValueError("verification requires a SUCCEEDED ActionRun")
        if self.action_run.incident_id != self.incident_id:
            raise ValueError("action_run.incident_id must match envelope incident_id")
        if self.verification.incident_id != self.incident_id:
            raise ValueError("verification.incident_id must match envelope incident_id")
        if self.action_run.action_run_id not in self.verification.action_run_ids:
            raise ValueError("verification must include action_run.action_run_id")
        return self


class VerificationResult(ContractMessage):
    """Return the current or terminal state of a verification run."""

    message_type: Literal["verification_result"] = "verification_result"
    verification: VerificationRun
    summary_zh: ShortText = ""

    @model_validator(mode="after")
    def _verification_identity_matches(self) -> "VerificationResult":
        if self.verification.incident_id != self.incident_id:
            raise ValueError("verification.incident_id must match envelope incident_id")
        return self


def _resource_identities(resources: Sequence[ResourceReference]) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(
                resource.stable_identity(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for resource in resources
        )
    )


def _report_contains_action(report: RcaReport, action: RemediationAction) -> bool:
    return any(
        recommendation.action_id == action.action_id
        and recommendation.action_hash == action.action_hash
        for recommendation in report.recommendations
    )


def validate_approval_reference(
    reference: ApprovalReference,
    decisions: Sequence[ApprovalDecision],
    *,
    action: RemediationAction,
    report: RcaReport,
    trusted_now: datetime,
) -> ApprovalDecision:
    """Resolve a wire reference against append-only decisions and a trusted clock.

    Gateways must supply their own current time.  Caller-controlled ``sent_at``
    and ``reference.validated_at`` are intentionally not used to decide whether
    the grant is still effective.
    """

    if trusted_now.tzinfo is None or trusted_now.utcoffset() is None:
        raise ApprovalAuthorizationError("trusted_now must include a timezone")
    matching = sorted(
        (
            decision
            for decision in decisions
            if decision.request_id == reference.request_id
        ),
        key=lambda decision: decision.sequence,
    )
    if not matching:
        raise ApprovalAuthorizationError("approval request was not found")
    if [decision.sequence for decision in matching] != list(range(len(matching))):
        raise ApprovalAuthorizationError("approval lifecycle is not contiguous")

    latest = matching[-1]
    if (
        latest.approval_id != reference.approval_id
        or latest.sequence != reference.decision_sequence
    ):
        raise ApprovalAuthorizationError("approval reference is not the latest decision")
    if (
        latest.incident_id != reference.incident_id
        or latest.report_id != reference.report_id
        or latest.report_version != reference.report_version
        or latest.subject_id != reference.subject_id
        or latest.action_hash != reference.action_hash
    ):
        raise ApprovalAuthorizationError("approval reference binding does not match")
    if not latest.covers_action(action, reference.incident_id, report):
        raise ApprovalAuthorizationError("latest decision does not cover the action")
    if not latest.is_effective(trusted_now):
        raise ApprovalAuthorizationError("latest decision is not effective")
    return latest


ContractPayload: TypeAlias = (
    IncidentTrigger
    | RcaRequest
    | RcaResult
    | ApprovalRequest
    | ApprovalResult
    | NetworkChangeRequest
    | VerificationRequest
    | VerificationResult
)

_MESSAGE_TYPES: dict[str, type[ContractMessage]] = {
    "incident_trigger": IncidentTrigger,
    "rca_request": RcaRequest,
    "rca_result": RcaResult,
    "approval_request": ApprovalRequest,
    "approval_result": ApprovalResult,
    "network_change_request": NetworkChangeRequest,
    "verification_request": VerificationRequest,
    "verification_result": VerificationResult,
}


def parse_contract_message(data: Mapping[str, object]) -> ContractPayload:
    """Strictly parse a privacy-safe canonical 1.0 structured payload."""

    if not isinstance(data, Mapping):
        raise ContractDecodeError("canonical payload must be a structured mapping")
    _assert_payload_budget(data)
    try:
        assert_model_safe(data)
    except SensitiveDataError:
        raise ContractDecodeError(
            "canonical payload violates the privacy policy"
        ) from None

    if data.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ContractDecodeError("unsupported or missing schema_version")
    message_type = data.get("message_type")
    if not isinstance(message_type, str) or message_type not in _MESSAGE_TYPES:
        raise ContractDecodeError("unsupported or missing message_type")

    message_model = _MESSAGE_TYPES[message_type]
    try:
        parsed = message_model.model_validate(data)
    except ValidationError as exc:
        # Unknown paths are masked because an attacker controls extra keys.  Raw
        # inputs, error context, and chained Pydantic exceptions never escape.
        safe_field_names = set(message_model.model_fields)
        for nested_model in (
            Incident,
            RcaReport,
            RemediationAction,
            ApprovalDecision,
            ApprovalReference,
            ActionRun,
            VerificationRun,
            ResourceReference,
        ):
            safe_field_names.update(nested_model.model_fields)
        safe_errors = [
            {
                "location": ".".join(
                    str(part)
                    if isinstance(part, int) or str(part) in safe_field_names
                    else "<unknown_field>"
                    for part in error["loc"]
                ),
                "type": error["type"],
            }
            for error in exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        ]
        raise ContractDecodeError(
            f"invalid canonical payload fields: {safe_errors!r}"
        ) from None
    return cast(ContractPayload, parsed)


__all__ = [
    "ApprovalAuthorizationError",
    "ApprovalProof",
    "ApprovalReference",
    "ApprovalRequest",
    "ApprovalResult",
    "CONTRACT_SCHEMA_VERSION",
    "ContractDecodeError",
    "ContractEncodeError",
    "ContractMessage",
    "ContractPayload",
    "ContractPayloadLimitError",
    "IncidentTrigger",
    "MAX_CONTRACT_DEPTH",
    "MAX_CONTRACT_SERIALIZED_BYTES",
    "NetworkChangeRequest",
    "RcaRequest",
    "RcaResult",
    "VerificationRequest",
    "VerificationResult",
    "parse_contract_message",
    "validate_approval_reference",
]
