"""Framework-independent domain models for autonomous network operations.

The models in this module deliberately contain no persistence, agent-framework,
or cloud-SDK concepts.  They are safe to share between the local and cloud
profiles of the platform.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)


SCHEMA_VERSION = "1.0"
SchemaVersion = Literal["1.0"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Reject naive datetimes and normalize aware datetimes to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include a timezone")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
]
Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]


class IncidentStatus(StrEnum):
    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    RCA_COMPLETE = "RCA_COMPLETE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REOPENED = "REOPENED"


class IncidentSeverity(StrEnum):
    UNKNOWN = "UNKNOWN"
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# A short alias is convenient at call sites and preserves a single enum type.
Severity = IncidentSeverity


class Technology(StrEnum):
    UNKNOWN = "UNKNOWN"
    LTE = "LTE"
    FIVE_G_NSA = "5G_NSA"
    FIVE_G_SA = "5G_SA"


class ResourceType(StrEnum):
    LOCATION = "LOCATION"
    NETWORK_SERVICE = "NETWORK_SERVICE"
    NETWORK_NODE = "NETWORK_NODE"
    ENODEB = "ENODEB"
    CELL = "CELL"
    GNB = "GNB"
    NR_CELL = "NR_CELL"
    NETWORK_SLICE = "NETWORK_SLICE"
    OTHER = "OTHER"


ResourceKind = ResourceType


class KpiComparator(StrEnum):
    LESS_THAN = "LT"
    LESS_THAN_OR_EQUAL = "LTE"
    GREATER_THAN = "GT"
    GREATER_THAN_OR_EQUAL = "GTE"
    EQUAL = "EQ"
    NOT_EQUAL = "NE"

    # Concise aliases are useful when translating rule expressions.
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    EQ = "EQ"
    NE = "NE"


class EvidenceType(StrEnum):
    METRIC = "METRIC"
    TRACE = "TRACE"
    LOG = "LOG"
    RULE = "RULE"
    DOCUMENT = "DOCUMENT"
    PRIOR_INCIDENT = "PRIOR_INCIDENT"
    TEST_RESULT = "TEST_RESULT"
    TOPOLOGY = "TOPOLOGY"
    OTHER = "OTHER"


EvidenceKind = EvidenceType


class ReportStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    PERSISTED = "PERSISTED"
    SUPERSEDED = "SUPERSEDED"


class RcaConclusion(StrEnum):
    CONCLUSIVE = "CONCLUSIVE"
    INCONCLUSIVE = "INCONCLUSIVE"


class ApprovalType(StrEnum):
    REPORT_PERSISTENCE = "REPORT_PERSISTENCE"
    NETWORK_ACTION = "NETWORK_ACTION"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionRunStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


ActionStatus = ActionRunStatus


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"


class DomainModel(BaseModel):
    """Base configuration shared by all public domain models."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        protected_namespaces=(),
        revalidate_instances="always",
        str_strip_whitespace=True,
        validate_default=True,
    )


class ResourceReference(DomainModel):
    """A versioned, technology-neutral reference to an affected resource."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    resource_id: Identifier
    resource_type: ResourceType = Field(
        validation_alias=AliasChoices("resource_type", "kind")
    )
    name: NonEmptyStr | None = None
    technology: Technology | None = None
    vendor_profile: NonEmptyStr | None = None
    parent_resource_id: Identifier | None = None
    location_id: Identifier | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def kind(self) -> ResourceType:
        """Backward-friendly synonym for ``resource_type``."""

        return self.resource_type

    def stable_identity(self) -> dict[str, JsonValue]:
        """Return every stable field used to authorize this exact resource."""

        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type.value,
            "technology": self.technology.value if self.technology else None,
            "vendor_profile": self.vendor_profile,
            "location_id": self.location_id,
            "parent_resource_id": self.parent_resource_id,
            "external_ids": dict(self.external_ids),
        }


class KpiViolation(DomainModel):
    """A summarized rule violation; raw samples remain in their data source."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    violation_id: Identifier | None = None
    kpi_name: NonEmptyStr
    observed_value: float
    threshold_value: float = Field(
        validation_alias=AliasChoices("threshold_value", "threshold")
    )
    comparator: KpiComparator
    unit: str | None = None
    window_start: UtcDatetime | None = None
    window_end: UtcDatetime | None = None
    rule_id: Identifier | None = None
    rule_version: NonEmptyStr | None = None
    resource_ids: tuple[Identifier, ...] = ()
    dimensions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> KpiViolation:
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        if self.window_start is not None and self.window_end < self.window_start:
            raise ValueError("window_end must not be earlier than window_start")
        return self

    @property
    def threshold(self) -> float:
        return self.threshold_value


class KpiObservation(DomainModel):
    """One privacy-safe KPI observation returned by a telemetry adapter.

    Detection rules deliberately live above this model: repositories expose the
    measured value and quality flags, while Detector services own thresholds and
    episode grouping. Raw counter rows remain in the telemetry store.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION
    observation_id: Identifier
    kpi_name: NonEmptyStr
    observed_value: float
    observed_at: UtcDatetime
    resources: tuple[ResourceReference, ...] = Field(min_length=1)
    unit: NonEmptyStr | None = None
    source_uri: NonEmptyStr
    quality_flags: tuple[NonEmptyStr, ...] = ()
    dimensions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self) -> KpiObservation:
        resource_identities = tuple(
            (
                resource.resource_type.value,
                resource.resource_id,
                resource.parent_resource_id,
            )
            for resource in self.resources
        )
        if len(resource_identities) != len(set(resource_identities)):
            raise ValueError("resources must not contain duplicates")
        if len(self.quality_flags) != len(set(self.quality_flags)):
            raise ValueError("quality_flags must not contain duplicates")
        return self


class EvidenceReference(DomainModel):
    """A pointer to evidence, intentionally excluding the raw evidence payload."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    evidence_id: Identifier
    evidence_type: EvidenceType = Field(
        validation_alias=AliasChoices("evidence_type", "kind")
    )
    uri: NonEmptyStr
    source: NonEmptyStr | None = None
    summary: str | None = None
    collected_at: UtcDatetime | None = None
    content_type: NonEmptyStr | None = None
    checksum_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{64}$"
    )
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def kind(self) -> EvidenceType:
        return self.evidence_type


def _canonical_json(value: JsonValue | dict[str, Any]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _stable_hash(value: JsonValue | dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_resource_identity(resource: ResourceReference) -> str:
    return _canonical_json(resource.stable_identity())


def _resource_scope_identity(
    resources: tuple[ResourceReference, ...],
) -> tuple[str, ...]:
    return tuple(sorted(_canonical_resource_identity(item) for item in resources))


class RemediationAction(DomainModel):
    """A proposed, not-yet-executed network change."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    action_id: Identifier
    action_type: NonEmptyStr = "CUSTOM"
    description: str | None = None
    target_resources: tuple[ResourceReference, ...] = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_approval: Literal[True] = True
    reversible: bool = False
    rollback_plan: str | None = None
    expected_outcome: str | None = None
    idempotency_key: Identifier | None = None
    created_at: UtcDatetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_rollback_plan(self) -> RemediationAction:
        identities = tuple(
            _canonical_resource_identity(target)
            for target in self.target_resources
        )
        if len(identities) != len(set(identities)):
            raise ValueError("target_resources must not contain duplicates")
        if self.reversible and not (self.rollback_plan or "").strip():
            raise ValueError("reversible actions must include a rollback_plan")
        return self

    @property
    def parameter_hash(self) -> str:
        """SHA-256 of canonical JSON parameters, independent of key ordering."""

        return _stable_hash(self.parameters)

    @property
    def parameters_hash(self) -> str:
        """Plural spelling retained as an ergonomic synonym."""

        return self.parameter_hash

    def compute_action_hash(self) -> str:
        """Hash the semantic action without volatile IDs or timestamps."""

        targets = sorted(
            (target.stable_identity() for target in self.target_resources),
            key=_canonical_json,
        )
        return _stable_hash(
            {
                "action_type": self.action_type,
                "description": self.description,
                "risk_level": self.risk_level.value,
                "reversible": self.reversible,
                "rollback_plan": self.rollback_plan,
                "expected_outcome": self.expected_outcome,
                "parameter_hash": self.parameter_hash,
                "targets": targets,
            }
        )

    @property
    def action_hash(self) -> str:
        return self.compute_action_hash()


class RcaReport(DomainModel):
    """A versioned RCA artifact that can be approved independently of actions."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    report_id: Identifier
    incident_id: Identifier
    version: int = Field(default=1, ge=1)
    status: ReportStatus = ReportStatus.DRAFT
    title: str | None = None
    summary: str = ""
    hypotheses: tuple[str, ...] = ()
    root_cause: str | None = None
    conclusion: RcaConclusion = RcaConclusion.CONCLUSIVE
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_refs: tuple[EvidenceReference, ...] = ()
    recommendations: tuple[RemediationAction, ...] = ()
    generated_by: str | None = None
    model_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: UtcDatetime = Field(default_factory=_utc_now)


class ApprovalDecision(DomainModel):
    """A durable approval decision for one report or one network action."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    approval_id: Identifier
    request_id: Identifier
    sequence: int = Field(ge=0)
    incident_id: Identifier
    report_id: Identifier
    report_version: int = Field(ge=1)
    subject_id: Identifier
    status: ApprovalStatus = ApprovalStatus.PENDING
    approval_type: ApprovalType = ApprovalType.NETWORK_ACTION
    action_hash: Sha256Digest | None = None
    scope: tuple[ResourceReference, ...] = ()
    requested_by: Identifier | None = None
    decided_by: Identifier | None = None
    reason: str | None = None
    requested_at: UtcDatetime = Field(default_factory=_utc_now)
    decided_at: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    idempotency_key: Identifier

    @model_validator(mode="after")
    def validate_timeline(self) -> ApprovalDecision:
        if self.decided_at is not None and self.decided_at < self.requested_at:
            raise ValueError("decided_at must not be earlier than requested_at")
        if self.expires_at is not None and self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be later than requested_at")
        if (
            self.status is ApprovalStatus.APPROVED
            and self.decided_at is not None
            and self.expires_at is not None
            and self.expires_at <= self.decided_at
        ):
            raise ValueError("approved decisions must precede expires_at")
        if self.status is ApprovalStatus.EXPIRED:
            if self.expires_at is None:
                raise ValueError("EXPIRED decisions require expires_at")
            if self.decided_at is not None and self.decided_at < self.expires_at:
                raise ValueError("EXPIRED decided_at must be at or after expires_at")
        if self.status is not ApprovalStatus.PENDING:
            missing = []
            if self.decided_by is None:
                missing.append("decided_by")
            if self.decided_at is None:
                missing.append("decided_at")
            if missing:
                raise ValueError(
                    "terminal approval decisions require " + ", ".join(missing)
                )
            if self.sequence == 0:
                raise ValueError("terminal approval decisions require sequence >= 1")
        elif self.sequence != 0:
            raise ValueError("pending approval requests must use sequence 0")

        if self.approval_type is ApprovalType.NETWORK_ACTION:
            missing = []
            if self.action_hash is None:
                missing.append("action_hash")
            if not self.scope:
                missing.append("scope")
            if self.expires_at is None:
                missing.append("expires_at")
            if missing:
                raise ValueError(
                    "network action approvals require " + ", ".join(missing)
                )
            identities = tuple(
                _canonical_resource_identity(resource) for resource in self.scope
            )
            if len(identities) != len(set(identities)):
                raise ValueError("approval scope must not contain duplicates")
        elif self.subject_id != self.report_id:
            raise ValueError("report approval subject_id must match report_id")
        return self

    def is_effective(self, at: datetime | None = None) -> bool:
        """Evaluate this decision at an injectable, timezone-aware instant."""

        instant = _utc_now() if at is None else _as_utc(at)
        if self.status is not ApprovalStatus.APPROVED:
            return False
        if self.decided_at is None or self.decided_at > instant:
            return False
        return self.expires_at is None or instant < self.expires_at

    def covers_action(
        self,
        action: RemediationAction,
        incident_id: str,
        report: RcaReport,
    ) -> bool:
        """Check immutable approval bindings, excluding time/status validity."""

        return (
            self.approval_type is ApprovalType.NETWORK_ACTION
            and self.incident_id == incident_id
            and self.report_id == report.report_id
            and self.report_version == report.version
            and report.incident_id == incident_id
            and self.subject_id == action.action_id
            and self.action_hash == action.action_hash
            and _resource_scope_identity(self.scope)
            == _resource_scope_identity(action.target_resources)
        )

    @property
    def subject_hash(self) -> str | None:
        """Read-only compatibility name; new payloads use ``action_hash``."""

        return self.action_hash


class ActionRun(DomainModel):
    """An auditable execution attempt for a proposed remediation action."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    action_run_id: Identifier
    incident_id: Identifier | None = None
    action_id: Identifier
    action_hash: Sha256Digest | None = None
    status: ActionRunStatus = ActionRunStatus.PENDING
    idempotency_key: Identifier | None = None
    attempt: int = Field(default=1, ge=1)
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    output_summary: str | None = None
    error: NonEmptyStr | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeline(self) -> ActionRun:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at must not be earlier than started_at")
        bound_statuses = {
            ActionRunStatus.RUNNING,
            ActionRunStatus.SUCCEEDED,
            ActionRunStatus.FAILED,
            ActionRunStatus.CANCELLED,
            ActionRunStatus.SKIPPED,
        }
        terminal_statuses = bound_statuses - {ActionRunStatus.RUNNING}
        if self.status in bound_statuses:
            missing = []
            if self.incident_id is None:
                missing.append("incident_id")
            if self.action_hash is None:
                missing.append("action_hash")
            if self.idempotency_key is None:
                missing.append("idempotency_key")
            if self.started_at is None:
                missing.append("started_at")
            if missing:
                raise ValueError(
                    f"{self.status.value} ActionRun requires " + ", ".join(missing)
                )
        if self.status in terminal_statuses and self.finished_at is None:
            raise ValueError(f"{self.status.value} ActionRun requires finished_at")
        if self.status is ActionRunStatus.FAILED and self.error is None:
            raise ValueError("FAILED ActionRun requires a safe error summary")
        return self

    @property
    def run_id(self) -> str:
        return self.action_run_id


class VerificationRun(DomainModel):
    """The outcome of verifying network health after remediation."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    verification_id: Identifier
    incident_id: Identifier | None = None
    action_run_ids: tuple[Identifier, ...] = ()
    status: VerificationStatus = VerificationStatus.PENDING
    checks: tuple[str, ...] = ()
    observed_kpis: tuple[KpiViolation, ...] = ()
    evidence_refs: tuple[EvidenceReference, ...] = ()
    summary: str | None = None
    error: NonEmptyStr | None = None
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeline(self) -> VerificationRun:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at must not be earlier than started_at")
        if self.status is VerificationStatus.RUNNING:
            missing = []
            if self.incident_id is None:
                missing.append("incident_id")
            if self.started_at is None:
                missing.append("started_at")
            if missing:
                raise ValueError(
                    "RUNNING VerificationRun requires " + ", ".join(missing)
                )
        if self.status in {VerificationStatus.PASSED, VerificationStatus.FAILED}:
            missing = []
            if self.incident_id is None:
                missing.append("incident_id")
            if not self.action_run_ids:
                missing.append("action_run_ids")
            if not any(check.strip() for check in self.checks):
                missing.append("checks")
            if not self.evidence_refs:
                missing.append("result evidence")
            if self.started_at is None:
                missing.append("started_at")
            if self.finished_at is None:
                missing.append("finished_at")
            if missing:
                raise ValueError(
                    f"{self.status.value} VerificationRun requires "
                    + ", ".join(missing)
                )
        if self.status is VerificationStatus.FAILED and self.error is None:
            raise ValueError("FAILED VerificationRun requires a safe error summary")
        if len(self.action_run_ids) != len(set(self.action_run_ids)):
            raise ValueError("action_run_ids must not contain duplicates")
        return self


class IncidentAuditEvent(DomainModel):
    """An immutable state-change record committed atomically with an Incident."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    event_id: Identifier
    incident_id: Identifier
    from_status: IncidentStatus | None = None
    to_status: IncidentStatus
    revision: int = Field(ge=0)
    occurred_at: UtcDatetime = Field(default_factory=_utc_now)
    actor: Identifier
    reason: NonEmptyStr
    idempotency_key: Identifier
    trace_id: Identifier


class Incident(DomainModel):
    """The canonical incident aggregate shared by all runtime profiles."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    incident_id: Identifier
    correlation_key: Identifier | None = None
    source_event_ids: tuple[Identifier, ...] = ()
    technology: Technology = Technology.UNKNOWN
    vendor_profile: NonEmptyStr | None = None
    status: IncidentStatus = IncidentStatus.DETECTED
    severity: IncidentSeverity = IncidentSeverity.UNKNOWN
    title: str = ""
    description: str = ""
    affected_resources: tuple[ResourceReference, ...] = ()
    detected_at: UtcDatetime = Field(default_factory=_utc_now)
    window_start: UtcDatetime | None = None
    window_end: UtcDatetime | None = None
    violated_kpis: tuple[KpiViolation, ...] = ()
    evidence_refs: tuple[EvidenceReference, ...] = ()
    hypotheses: tuple[str, ...] = ()
    root_cause: str | None = None
    rca_reports: tuple[RcaReport, ...] = ()
    recommendations: tuple[RemediationAction, ...] = ()
    approvals: tuple[ApprovalDecision, ...] = ()
    action_runs: tuple[ActionRun, ...] = ()
    verification_runs: tuple[VerificationRun, ...] = ()
    model_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    rule_versions: dict[str, str] = Field(default_factory=dict)
    trace_id: Identifier
    duplicate_of: Identifier | None = None
    created_at: UtcDatetime = Field(default_factory=_utc_now)
    updated_at: UtcDatetime = Field(default_factory=_utc_now)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_aggregate(self) -> Incident:
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        if self.window_start is not None and self.window_end < self.window_start:
            raise ValueError("window_end must not be earlier than window_start")
        if self.window_end is not None and self.window_end > self.detected_at:
            raise ValueError("window_end must not be later than detected_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.duplicate_of == self.incident_id:
            raise ValueError("duplicate_of must reference a different incident")

        self._ensure_unique("source_event_ids", self.source_event_ids)
        self._ensure_unique(
            "affected_resources",
            (
                (resource.resource_type.value, resource.resource_id)
                for resource in self.affected_resources
            ),
        )
        self._ensure_unique(
            "approvals", (approval.approval_id for approval in self.approvals)
        )
        self._ensure_unique(
            "approval lifecycle sequences",
            ((approval.request_id, approval.sequence) for approval in self.approvals),
        )
        self._ensure_unique(
            "approval idempotency keys",
            (approval.idempotency_key for approval in self.approvals),
        )
        self._ensure_unique(
            "rca_reports",
            ((report.report_id, report.version) for report in self.rca_reports),
        )
        self._ensure_unique(
            "RCA report versions", (report.version for report in self.rca_reports)
        )
        self._ensure_unique(
            "evidence_refs", (evidence.evidence_id for evidence in self.evidence_refs)
        )
        self._ensure_unique(
            "recommendations",
            (action.action_id for action in self.recommendations),
        )
        for report in self.rca_reports:
            if report.incident_id != self.incident_id:
                raise ValueError("RCA report incident_id must match its Incident")
        self._validate_approval_lifecycles()
        for run in (*self.action_runs, *self.verification_runs):
            if run.incident_id is not None and run.incident_id != self.incident_id:
                raise ValueError("run incident_id must match its Incident")
        self._ensure_unique(
            "action_runs", (run.action_run_id for run in self.action_runs)
        )
        self._ensure_unique(
            "action run attempts",
            ((run.action_id, run.attempt) for run in self.action_runs),
        )
        self._ensure_unique(
            "action run idempotency keys",
            (
                run.idempotency_key
                for run in self.action_runs
                if run.idempotency_key is not None
            ),
        )
        self._ensure_unique(
            "verification_runs",
            (run.verification_id for run in self.verification_runs),
        )
        action_run_ids = {run.action_run_id for run in self.action_runs}
        for verification in self.verification_runs:
            if not set(verification.action_run_ids).issubset(action_run_ids):
                raise ValueError(
                    "VerificationRun action_run_ids must reference this Incident"
                )
        return self

    def _validate_approval_lifecycles(self) -> None:
        reports = {
            (report.report_id, report.version): report for report in self.rca_reports
        }
        actions = {action.action_id: action for action in self.recommendations}
        grouped: dict[str, list[ApprovalDecision]] = {}
        for approval in self.approvals:
            if approval.incident_id != self.incident_id:
                raise ValueError("approval incident_id must match its Incident")
            report = reports.get((approval.report_id, approval.report_version))
            if report is None:
                raise ValueError("approval must bind an RCA report on its Incident")
            action = actions.get(approval.subject_id)
            if (
                approval.approval_type is ApprovalType.NETWORK_ACTION
                and (
                    action is None
                    or not approval.covers_action(action, self.incident_id, report)
                )
            ):
                raise ValueError("approval must exactly bind a recommended action")
            if (
                approval.approval_type is ApprovalType.REPORT_PERSISTENCE
                and approval.subject_id != report.report_id
            ):
                raise ValueError("report approval subject_id must match report_id")
            grouped.setdefault(approval.request_id, []).append(approval)

        for request_id, events in grouped.items():
            ordered = sorted(events, key=lambda event: event.sequence)
            if [event.sequence for event in ordered] != list(range(len(ordered))):
                raise ValueError(
                    f"approval request {request_id!r} must have contiguous sequences"
                )
            if ordered[0].status is not ApprovalStatus.PENDING:
                raise ValueError(
                    f"approval request {request_id!r} must begin with PENDING"
                )
            binding = self._approval_binding(ordered[0])
            if any(self._approval_binding(event) != binding for event in ordered[1:]):
                raise ValueError(
                    f"approval request {request_id!r} cannot change its binding"
                )

    @staticmethod
    def _approval_binding(approval: ApprovalDecision) -> tuple[Any, ...]:
        return (
            approval.approval_type,
            approval.incident_id,
            approval.report_id,
            approval.report_version,
            approval.subject_id,
            approval.action_hash,
            _resource_scope_identity(approval.scope),
            approval.requested_at,
            approval.expires_at,
        )

    @staticmethod
    def _ensure_unique(name: str, values: Any) -> None:
        materialized = tuple(values)
        if len(materialized) != len(set(materialized)):
            raise ValueError(f"{name} must not contain duplicates")

    def latest_approval_decision(
        self, request_id: str
    ) -> ApprovalDecision | None:
        """Return the maximum-sequence event for one immutable request."""

        matching = tuple(
            decision
            for decision in self.approvals
            if decision.request_id == request_id
        )
        return max(matching, key=lambda item: item.sequence, default=None)

    def effective_action_approval(
        self,
        action: RemediationAction,
        report: RcaReport,
        at: datetime,
    ) -> ApprovalDecision | None:
        """Resolve an effective grant using only each request's latest event."""

        request_ids = {
            decision.request_id for decision in self.approvals
        }
        latest = tuple(
            self.latest_approval_decision(request_id)
            for request_id in request_ids
        )
        return next(
            (
                decision
                for decision in latest
                if decision is not None
                and decision.covers_action(action, self.incident_id, report)
                and decision.is_effective(at)
            ),
            None,
        )
