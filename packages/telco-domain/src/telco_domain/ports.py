"""Framework-neutral ports for the canonical incident workflow.

Implementations may use DuckDB, Spanner, A2A, MCP, or an in-process fake, but
domain and orchestration code only depends on the protocols in this module.
All I/O methods are asynchronous to match the surrounding agent call chain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from .contracts import (
    ApprovalRequest,
    ApprovalResult,
    ContractMessage,
    NetworkChangeRequest,
    RcaRequest,
    RcaResult,
    VerificationRequest,
    VerificationResult,
)
from .models import (
    ActionRun,
    ApprovalDecision,
    EvidenceReference,
    Incident,
    IncidentAuditEvent,
    IncidentStatus,
    KpiObservation,
    SourceEventAssociation,
    Technology,
)


MAX_REPOSITORY_PAGE_SIZE = 1_000
MAX_REPOSITORY_OFFSET = 100_000
MAX_REPOSITORY_BATCH_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IncidentSnapshotImportResult:
    """Atomic outcome of the narrow one-time snapshot import boundary."""

    incident: Incident
    replayed: bool


class IncidentRepositoryError(RuntimeError):
    """Base class for deterministic incident persistence errors."""


class IncidentNotFoundError(IncidentRepositoryError):
    """The requested incident does not exist."""

    def __init__(self, incident_id: str) -> None:
        self.incident_id = incident_id
        super().__init__(f"incident {incident_id!r} was not found")


class IncidentAlreadyExistsError(IncidentRepositoryError):
    """A create attempted to reuse an existing canonical incident ID."""

    def __init__(self, incident_id: str) -> None:
        self.incident_id = incident_id
        super().__init__(f"incident {incident_id!r} already exists")


class ActiveIncidentConflictError(IncidentRepositoryError):
    """A naked create attempted to bypass active correlation deduplication."""

    def __init__(self, incident_id: str, existing_incident_id: str) -> None:
        self.incident_id = incident_id
        self.existing_incident_id = existing_incident_id
        super().__init__(
            f"incident {incident_id!r} correlates with active incident "
            f"{existing_incident_id!r}; use create_or_correlate"
        )


class SourceEventOwnershipConflictError(IncidentRepositoryError):
    """A source event was already bound to a different Incident forever."""

    def __init__(
        self,
        source_event_id: str,
        owner_incident_id: str,
        requested_incident_id: str,
    ) -> None:
        self.source_event_id = source_event_id
        self.owner_incident_id = owner_incident_id
        self.requested_incident_id = requested_incident_id
        super().__init__("source event is already owned by another incident")


class IncidentCorrelationConflictError(IncidentRepositoryError):
    """Correlation and source selectors resolve to different active Incidents."""

    def __init__(
        self,
        requested_incident_id: str,
        conflicting_incident_ids: Sequence[str],
    ) -> None:
        self.requested_incident_id = requested_incident_id
        self.conflicting_incident_ids = tuple(sorted(set(conflicting_incident_ids)))
        if len(self.conflicting_incident_ids) < 2:
            raise ValueError("at least two conflicting incidents are required")
        super().__init__("incident correlation selectors have multiple active owners")


class RevisionConflictError(IncidentRepositoryError):
    """The expected revision did not match the persisted incident snapshot."""

    def __init__(
        self,
        incident_id: str,
        *,
        expected_revision: int,
        actual_revision: int,
        candidate_revision: int | None = None,
    ) -> None:
        self.incident_id = incident_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.candidate_revision = candidate_revision
        detail = (
            ""
            if candidate_revision is None
            else f", candidate revision was {candidate_revision}"
        )
        super().__init__(
            f"revision conflict for incident {incident_id!r}: expected "
            f"{expected_revision}, persisted revision is {actual_revision}{detail}"
        )


class IdempotencyConflictError(IncidentRepositoryError):
    """An idempotency key was replayed with a different request fingerprint."""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"idempotency key {idempotency_key!r} was already used for another request"
        )


class UnsafeIncidentWriteError(IncidentRepositoryError):
    """A supplied snapshot could not be reproduced by the domain state machine."""

    def __init__(self, incident_id: str, reason: str) -> None:
        self.incident_id = incident_id
        self.reason = reason
        # The structured attribute remains available to trusted callers, but
        # the printable error must never reflect an identifier that failed the
        # privacy boundary itself.
        super().__init__(f"unsafe incident write rejected: {reason}")


@runtime_checkable
class IncidentRepository(Protocol):
    """Canonical incident storage with atomic CAS and idempotent writes."""

    async def create(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        """Create once; an identical key and request returns the first result."""

        ...

    async def create_or_correlate(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        """Atomically return a matching active incident or create the candidate."""

        ...

    async def get(self, incident_id: str) -> Incident | None:
        """Return a detached incident snapshot, or ``None`` when absent."""

        ...

    async def save(
        self,
        incident: Incident,
        *,
        expected_revision: int,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        """Safely delegate a candidate snapshot through the domain state machine."""

        ...

    async def compare_and_swap(
        self,
        incident: Incident,
        *,
        expected_revision: int,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        """Explicit CAS spelling of :meth:`save` for transition/audit adapters."""

        ...

    async def transition(
        self,
        incident_id: str,
        target_status: IncidentStatus,
        *,
        expected_revision: int,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
        updates: Mapping[str, object] | None = None,
    ) -> Incident:
        """Atomically run a state transition and append its audit event."""

        ...

    async def find_by_idempotency_key(
        self,
        incident_id: str,
        idempotency_key: str,
        *,
        operation: str,
    ) -> Incident | None:
        """Return a result scoped by operation, requested incident, and key."""

        ...

    async def find_active(
        self,
        *,
        correlation_key: str | None = None,
        source_event_id: str | None = None,
    ) -> Incident | None:
        """Find an active incident matching either correlation selector."""

        ...

    async def list(
        self,
        *,
        status: IncidentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Incident]:
        """Return deterministic, detached snapshots for discovery screens."""

        ...

    async def history(
        self,
        incident_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[IncidentAuditEvent]:
        """Return a deterministic audit page committed with successful writes.

        ``None`` preserves the small, in-process compatibility view. Network
        boundaries must always supply a finite limit so adapters can enforce
        database-side pagination rather than materializing an unbounded log.
        """

        ...

    async def source_event_associations(
        self,
        incident_id: str,
        *,
        limit: int = MAX_REPOSITORY_PAGE_SIZE,
        offset: int = 0,
    ) -> Sequence[SourceEventAssociation]:
        """Return immutable source-event provenance without mutating revision."""

        ...


@runtime_checkable
class TelemetryRepository(Protocol):
    """Read-only evidence access bounded by incident, time window, and size."""

    async def collect_evidence(
        self,
        incident: Incident,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int = 1_000,
    ) -> Sequence[EvidenceReference]:
        ...


@runtime_checkable
class MetricRepository(Protocol):
    """Query bounded, privacy-safe KPI observations before Incident creation."""

    async def query_kpis(
        self,
        *,
        kpi_names: Sequence[str],
        technology: Technology,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        resource_ids: Sequence[str] = (),
        limit: int = 50_000,
    ) -> Sequence[KpiObservation]:
        """Return ordered observations without applying detection thresholds."""

        ...


@runtime_checkable
class RuleRepository(Protocol):
    """Retrieve versioned RCA rules applicable to an incident."""

    async def match(self, incident: Incident) -> Sequence[Mapping[str, object]]:
        ...


@runtime_checkable
class DocumentRepository(Protocol):
    """Search approved documentation without coupling to a search provider."""

    async def search(
        self,
        query: str,
        *,
        technology: str | None = None,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]:
        ...


@runtime_checkable
class RcaGateway(Protocol):
    """Run the resolver pipeline behind a stable request/result contract."""

    async def analyze(self, request: RcaRequest) -> RcaResult:
        ...


@runtime_checkable
class ApprovalGateway(Protocol):
    """Obtain and persist a scoped human approval decision."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        ...

    async def resolve_for_execution(
        self, request: NetworkChangeRequest
    ) -> ApprovalDecision:
        """Resolve the latest event using gateway time; never trust wire status."""

        ...


@runtime_checkable
class ActionGateway(Protocol):
    """Execute only after trusted latest-decision resolution.

    Implementations must not interpret ``approval_reference`` as a grant.  They
    must resolve its request through ``ApprovalGateway.resolve_for_execution``
    immediately before applying side effects.
    """

    async def execute(self, request: NetworkChangeRequest) -> ActionRun:
        ...


@runtime_checkable
class VerificationGateway(Protocol):
    """Run deterministic post-change checks."""

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        ...


@runtime_checkable
class EventPublisher(Protocol):
    """Publish a validated domain message to any event transport."""

    async def publish(self, event: ContractMessage) -> None:
        ...


__all__ = [
    "ActionGateway",
    "ActiveIncidentConflictError",
    "ApprovalGateway",
    "DocumentRepository",
    "EventPublisher",
    "IdempotencyConflictError",
    "IncidentAuditEvent",
    "IncidentAlreadyExistsError",
    "IncidentCorrelationConflictError",
    "IncidentNotFoundError",
    "IncidentRepository",
    "IncidentRepositoryError",
    "IncidentSnapshotImportResult",
    "MetricRepository",
    "MAX_REPOSITORY_OFFSET",
    "MAX_REPOSITORY_BATCH_BYTES",
    "MAX_REPOSITORY_PAGE_SIZE",
    "RcaGateway",
    "RevisionConflictError",
    "RuleRepository",
    "SourceEventAssociation",
    "SourceEventOwnershipConflictError",
    "TelemetryRepository",
    "UnsafeIncidentWriteError",
    "VerificationGateway",
]
