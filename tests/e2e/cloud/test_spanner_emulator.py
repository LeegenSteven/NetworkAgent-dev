"""Real Cloud Spanner emulator gates for Canonical v2 transactions.

Real integration cases are skipped unless ``SPANNER_EMULATOR_HOST`` is set;
the pure fixture-contract regression remains runnable locally. CI can point
that variable at a Spanner emulator service container, and no test silently
starts Docker or contacts a production endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest


try:
    import google.cloud.spanner as spanner
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.spanner_v1.data_types import JsonObject
except ImportError:  # The pure helper contract remains runnable without the SDK.
    spanner = None
    AnonymousCredentials = None
    JsonObject = dict
from telco_domain.contracts import IncidentTrigger
from telco_domain.models import (
    ActionRun,
    ActionRunStatus,
    ApprovalDecision,
    ApprovalStatus,
    EvidenceReference,
    Incident,
    IncidentStatus,
    RemediationAction,
    ReportStatus,
    ResourceReference,
    RcaReport,
    SourceEventAssociation,
    VerificationRun,
    VerificationStatus,
)
from telco_domain.ports import (
    ActiveIncidentConflictError,
    RevisionConflictError,
    SourceEventOwnershipConflictError,
)
from tests.contracts.repository.repository_contract import (
    ContractClock,
    assert_incident_repository_contract,
)

from telco_cloud import (
    IngestDisposition,
    OutboxStatus,
    SourceEventEnvelope,
    SpannerEventIngestRepository,
    SpannerIncidentRepository,
    SpannerOutboxRepository,
    apply_object_schema,
)


BASE = datetime.now(UTC).replace(microsecond=0)
PROJECT_ID = "network-agent-emulator"


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, minutes: int = 1) -> None:
        self.value += timedelta(minutes=minutes)


def _incident(
    incident_id: str,
    source_event_id: str | None = None,
    *,
    correlation_key: str,
    now: datetime = BASE,
    source_event_ids: tuple[str, ...] | None = None,
) -> Incident:
    if source_event_ids is None:
        if source_event_id is None:
            raise ValueError("source_event_id or source_event_ids is required")
        normalized_source_event_ids = (source_event_id,)
    else:
        if source_event_id is not None:
            raise ValueError(
                "source_event_id and source_event_ids are mutually exclusive"
            )
        normalized_source_event_ids = source_event_ids
    return Incident(
        incident_id=incident_id,
        correlation_key=correlation_key,
        source_event_ids=normalized_source_event_ids,
        title="Emulator network fault",
        trace_id=f"trace-{incident_id}",
        detected_at=now,
        created_at=now,
        updated_at=now,
    )


def test_incident_helper_accepts_explicit_source_event_ids_without_emulator() -> None:
    source_event_ids = ("helper-source-a", "helper-source-b")

    incident = _incident(
        "helper-capacity-incident",
        correlation_key="lte:helper-capacity:availability",
        source_event_ids=source_event_ids,
    )

    assert incident.source_event_ids == source_event_ids
    assert _incident(
        "helper-default-incident",
        "helper-default-source",
        correlation_key="lte:helper-default:availability",
    ).source_event_ids == ("helper-default-source",)


def _envelope(
    index: int,
    *,
    correlation_key: str = "lte:emulator-cell:availability",
    now: datetime = BASE,
) -> SourceEventEnvelope:
    source_event_id = f"emulator-source-{index:03d}"
    incident = _incident(
        f"emulator-incident-{index:03d}",
        source_event_id,
        correlation_key=correlation_key,
        now=now,
    )
    return SourceEventEnvelope(
        source_event_id=source_event_id,
        source="//pubsub.googleapis.com/projects/emulator/topics/network-fault",
        event_type="com.google.cloud.logging.fault.v1",
        occurred_at=now,
        received_at=now,
        payload_sha256=hashlib.sha256(source_event_id.encode()).hexdigest(),
        trace_id=incident.trace_id,
        incident=incident,
        attributes={"logger": "EMULATOR_HEALTH"},
    )


def _count(database, table: str) -> int:
    with database.snapshot() as snapshot:
        row = next(iter(snapshot.execute_sql(f"SELECT COUNT(*) FROM {table}")))
    return int(row[0])


async def _advance_to_closed(
    repository: SpannerIncidentRepository,
    clock: _MutableClock,
    triaged: Incident,
) -> Incident:
    """Drive the aggregate through the public, guarded lifecycle to CLOSED."""

    target = ResourceReference(
        resource_id="emulator-upf-1",
        resource_type="NETWORK_NODE",
    )
    action = RemediationAction(
        action_id="emulator-restart-upf",
        action_type="restart",
        target_resources=(target,),
        created_at=BASE + timedelta(minutes=2),
    )
    evidence = EvidenceReference(
        evidence_id="emulator-rca-evidence",
        evidence_type="LOG",
        uri="log://emulator/safe-rca-evidence",
        collected_at=BASE + timedelta(minutes=2),
    )
    report = RcaReport(
        report_id="emulator-rca-report",
        incident_id=triaged.incident_id,
        status=ReportStatus.PROPOSED,
        root_cause="UPF health process unavailable",
        evidence_refs=(evidence,),
        recommendations=(action,),
        created_at=BASE + timedelta(minutes=2),
    )
    approval_request = ApprovalDecision(
        approval_id="emulator-approval-request",
        request_id="emulator-approval",
        sequence=0,
        incident_id=triaged.incident_id,
        report_id=report.report_id,
        report_version=report.version,
        subject_id=action.action_id,
        action_hash=action.action_hash,
        scope=action.target_resources,
        status=ApprovalStatus.PENDING,
        requested_at=BASE + timedelta(minutes=3),
        expires_at=BASE + timedelta(hours=1),
        idempotency_key="emulator-request-approval",
    )
    approval = approval_request.model_copy(
        update={
            "approval_id": "emulator-approval-decision",
            "sequence": 1,
            "status": ApprovalStatus.APPROVED,
            "decided_by": "emulator-operator",
            "decided_at": BASE + timedelta(minutes=4),
            "idempotency_key": "emulator-decide-approval",
        }
    )
    action_run = ActionRun(
        action_run_id="emulator-action-run",
        incident_id=triaged.incident_id,
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="emulator-execute-action",
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE + timedelta(minutes=5),
        finished_at=BASE + timedelta(minutes=6),
    )
    verification = VerificationRun(
        verification_id="emulator-verification",
        incident_id=triaged.incident_id,
        action_run_ids=(action_run.action_run_id,),
        status=VerificationStatus.PASSED,
        checks=("end-to-end traffic restored",),
        evidence_refs=(
            EvidenceReference(
                evidence_id="emulator-verification-evidence",
                evidence_type="TEST_RESULT",
                uri="test://emulator/verification",
                collected_at=BASE + timedelta(minutes=7),
            ),
        ),
        started_at=BASE + timedelta(minutes=6),
        finished_at=BASE + timedelta(minutes=7),
    )
    steps = (
        (IncidentStatus.INVESTIGATING, None),
        (
            IncidentStatus.RCA_COMPLETE,
            {
                "root_cause": report.root_cause,
                "rca_reports": (report,),
                "recommendations": (action,),
            },
        ),
        (IncidentStatus.AWAITING_APPROVAL, None),
        (
            IncidentStatus.REMEDIATING,
            {"approvals": (approval_request, approval)},
        ),
        (IncidentStatus.VERIFYING, {"action_runs": (action_run,)}),
        (
            IncidentStatus.RESOLVED,
            {"verification_runs": (verification,)},
        ),
        (IncidentStatus.CLOSED, None),
    )
    current = triaged
    for target_status, updates in steps:
        clock.advance()
        current = await repository.transition(
            current.incident_id,
            target_status,
            expected_revision=current.revision,
            idempotency_key=f"emulator-lifecycle-{target_status.value.lower()}",
            actor="emulator-resolver",
            reason=f"advance lifecycle to {target_status.value}",
            trace_id=current.trace_id,
            updates=updates,
        )
    return current


@pytest.fixture
def emulator_database():
    if not os.environ.get("SPANNER_EMULATOR_HOST"):
        pytest.skip("SPANNER_EMULATOR_HOST is required for real Spanner E2E")
    if spanner is None or AnonymousCredentials is None:
        pytest.fail(
            "google-cloud-spanner is required when SPANNER_EMULATOR_HOST is set"
        )
    suffix = uuid.uuid4().hex[:12]
    instance_id = f"p3test-{suffix}"
    database_id = f"p3db-{suffix}"
    assert instance_id.startswith("p3test-") and database_id.startswith("p3db-")

    client = spanner.Client(
        project=PROJECT_ID,
        credentials=AnonymousCredentials(),
    )
    instance = client.instance(
        instance_id,
        configuration_name="emulator-config",
        node_count=1,
        display_name=instance_id,
    )
    database = instance.database(database_id)
    instance_created = False
    database_created = False
    try:
        instance.create().result(timeout=120)
        instance_created = True
        database.create().result(timeout=120)
        database_created = True
        # The official emulator does not implement IAM/database roles.  Object
        # transactions run here; production apply_schema keeps FGAC mandatory.
        apply_object_schema(database)
        yield database
    finally:
        # Only exact, per-test resources with the guarded prefix are removed.
        if database_created and database_id.startswith("p3db-"):
            database.drop()
        if instance_created and instance_id.startswith("p3test-"):
            instance.delete()


def test_real_spanner_runs_shared_incident_repository_contract(
    emulator_database,
) -> None:
    clock = ContractClock()
    repository = SpannerIncidentRepository(emulator_database, clock=clock)
    asyncio.run(assert_incident_repository_contract(repository, clock))


def test_real_spanner_snapshot_import_preserves_correlated_provenance(
    emulator_database,
) -> None:
    async def scenario() -> None:
        repository = SpannerIncidentRepository(
            emulator_database, clock=lambda: BASE
        )
        incident = _incident(
            "emulator-migration",
            "emulator-migration-source-01",
            correlation_key="lte:migration-cell:availability",
        )
        associations = (
            SourceEventAssociation(
                incident_id=incident.incident_id,
                source_event_id="emulator-migration-source-01",
                registered_at=BASE - timedelta(minutes=2),
                actor="local-detector",
                reason="original source",
                idempotency_key="migration-source-01",
                trace_id=incident.trace_id,
            ),
            SourceEventAssociation(
                incident_id=incident.incident_id,
                source_event_id="emulator-migration-source-02",
                registered_at=BASE - timedelta(minutes=1),
                actor="fault-ingress",
                reason="correlated source",
                idempotency_key="migration-source-02",
                trace_id="trace-migration-correlated",
            ),
        )
        kwargs = {
            "idempotency_key": "emulator-migration-import",
            "actor": "canonical-migration",
            "reason": "one-time canonical incident import",
            "trace_id": incident.trace_id,
        }

        outcomes = await asyncio.gather(
            *(
                repository.import_detected_snapshot(
                    incident,
                    associations,
                    **kwargs,
                )
                for _ in range(50)
            )
        )

        assert all(outcome.incident == incident for outcome in outcomes)
        assert sum(not outcome.replayed for outcome in outcomes) == 1
        assert sum(outcome.replayed for outcome in outcomes) == 49
        assert _count(emulator_database, "CanonicalIncidentsV2") == 1
        assert _count(
            emulator_database,
            "CanonicalIncidentSourceEventsV2",
        ) == 2
        assert _count(emulator_database, "CanonicalIncidentAuditV2") == 1
        assert _count(
            emulator_database,
            "CanonicalIncidentIdempotencyV2",
        ) == 1
        assert _count(
            emulator_database,
            "CanonicalIncidentActiveKeysV2",
        ) == 3
        assert tuple(
            await repository.source_event_associations(incident.incident_id)
        ) == associations
        assert await repository.find_active(
            source_event_id="emulator-migration-source-02"
        ) == incident
        assert len(await repository.history(incident.incident_id)) == 1

    asyncio.run(scenario())


def test_real_spanner_snapshot_import_1000_association_capacity(
    emulator_database,
) -> None:
    """Prove the published migration maximum on the real Emulator backend."""

    async def scenario() -> None:
        repository = SpannerIncidentRepository(
            emulator_database,
            clock=lambda: BASE,
        )
        incident = _incident(
            "emulator-migration-capacity",
            correlation_key="lte:migration-capacity:availability",
            source_event_ids=tuple(
                f"emulator-capacity-source-{index:04d}"
                for index in range(1000)
            ),
        )
        associations = tuple(
            SourceEventAssociation(
                incident_id=incident.incident_id,
                source_event_id=f"emulator-capacity-source-{index:04d}",
                registered_at=BASE - timedelta(minutes=1),
                actor="canonical-migration",
                reason="capacity boundary fixture",
                idempotency_key=f"capacity-source-{index:04d}",
                trace_id=incident.trace_id,
            )
            for index in range(1000)
        )
        kwargs = {
            "idempotency_key": "emulator-migration-capacity",
            "actor": "canonical-migration",
            "reason": "one-time canonical incident import",
            "trace_id": incident.trace_id,
        }

        imported = await repository.import_detected_snapshot(
            incident,
            associations,
            **kwargs,
        )
        replayed = await repository.import_detected_snapshot(
            incident,
            associations,
            **kwargs,
        )

        assert imported.incident == incident
        assert imported.replayed is False
        assert replayed.incident == incident
        assert replayed.replayed is True
        assert _count(emulator_database, "CanonicalIncidentsV2") == 1
        assert _count(
            emulator_database,
            "CanonicalIncidentSourceEventsV2",
        ) == 1000
        assert _count(emulator_database, "CanonicalIncidentAuditV2") == 1
        assert _count(
            emulator_database,
            "CanonicalIncidentIdempotencyV2",
        ) == 1
        assert _count(
            emulator_database,
            "CanonicalIncidentActiveKeysV2",
        ) == 1001
        assert len(
            await repository.source_event_associations(
                incident.incident_id,
                limit=1000,
            )
        ) == 1000

        transitioned = await repository.transition(
            incident.incident_id,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="emulator-capacity-transition",
            actor="canonical-migration",
            reason="capacity lifecycle verification",
            trace_id=incident.trace_id,
        )
        assert transitioned.status is IncidentStatus.TRIAGED
        assert _count(
            emulator_database,
            "CanonicalIncidentSourceEventsV2",
        ) == 1000
        assert _count(emulator_database, "CanonicalIncidentAuditV2") == 2
        assert _count(
            emulator_database,
            "CanonicalIncidentIdempotencyV2",
        ) == 2
        assert _count(
            emulator_database,
            "CanonicalIncidentActiveKeysV2",
        ) == 1001

    asyncio.run(scenario())


def test_real_spanner_transactions_json_concurrency_reopen_and_outbox(
    emulator_database,
) -> None:
    database = emulator_database

    async def scenario() -> None:
        event_repository = SpannerEventIngestRepository(
            database, clock=lambda: BASE
        )
        envelopes = tuple(_envelope(index) for index in range(50))
        results = await asyncio.gather(
            *(event_repository.ingest(envelope) for envelope in envelopes)
        )

        assert sum(
            result.disposition is IngestDisposition.CREATED for result in results
        ) == 1
        assert sum(
            result.disposition is IngestDisposition.CORRELATED for result in results
        ) == 49
        assert _count(database, "CanonicalIncidentsV2") == 1
        assert _count(database, "CanonicalSourceEventInboxV2") == 50
        assert _count(database, "CanonicalIncidentSourceEventsV2") == 50
        assert _count(database, "CanonicalIncidentOutboxV2") == 1

        created_result = next(
            result
            for result in results
            if result.disposition is IngestDisposition.CREATED
        )
        assert created_result.incident is not None
        assert created_result.outbox_event_id is not None
        winner = created_result.incident

        # Fifty exact Pub/Sub replays are read-only: no Inbox, provenance, audit,
        # idempotency, Incident, or Outbox row is duplicated.
        replay_counts = {
            table: _count(database, table)
            for table in (
                "CanonicalIncidentsV2",
                "CanonicalSourceEventInboxV2",
                "CanonicalIncidentSourceEventsV2",
                "CanonicalIncidentAuditV2",
                "CanonicalIncidentIdempotencyV2",
                "CanonicalIncidentOutboxV2",
            )
        }
        winner_index = int(created_result.source_event_id.rsplit("-", 1)[-1])
        exact_replays = await asyncio.gather(
            *(event_repository.ingest(_envelope(winner_index)) for _ in range(50))
        )
        assert all(
            replay.disposition is IngestDisposition.REPLAYED
            for replay in exact_replays
        )
        assert replay_counts == {
            table: _count(database, table) for table in replay_counts
        }

        # A transport redelivery timestamp is not part of the durable event identity.
        replay_payload = _envelope(winner_index).model_dump(mode="python")
        replay_payload["received_at"] = BASE + timedelta(seconds=30)
        replay = await event_repository.ingest(
            SourceEventEnvelope.model_validate(replay_payload)
        )
        assert replay.disposition is IngestDisposition.REPLAYED

        # The audit's committed_at column must contain the server placeholder result.
        with database.snapshot() as snapshot:
            audit_row = next(
                iter(
                    snapshot.execute_sql(
                        "SELECT occurred_at, committed_at "
                        "FROM CanonicalIncidentAuditV2 LIMIT 1"
                    )
                )
            )
        assert isinstance(audit_row[1], datetime)
        assert audit_row[1].tzinfo is not None

        lifecycle_clock = _MutableClock(BASE + timedelta(minutes=1))
        incident_repository = SpannerIncidentRepository(
            database, clock=lifecycle_clock
        )
        transitions = await asyncio.gather(
            incident_repository.transition(
                winner.incident_id,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                idempotency_key="emulator-cas-a",
                actor="emulator-resolver",
                reason="CAS contender A",
                trace_id=winner.trace_id,
            ),
            incident_repository.transition(
                winner.incident_id,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                idempotency_key="emulator-cas-b",
                actor="emulator-resolver",
                reason="CAS contender B",
                trace_id=winner.trace_id,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, Incident) for item in transitions) == 1
        assert sum(
            isinstance(item, RevisionConflictError) for item in transitions
        ) == 1
        triaged = next(item for item in transitions if isinstance(item, Incident))

        # Exercise every guarded public transition through CLOSED.  This must
        # yield one immutable audit event per revision and release every key.
        closed = await _advance_to_closed(
            incident_repository,
            lifecycle_clock,
            triaged,
        )
        assert closed.status is IncidentStatus.CLOSED
        assert closed.revision == 8
        closed_history = tuple(
            await incident_repository.history(closed.incident_id)
        )
        assert tuple(event.revision for event in closed_history) == tuple(range(9))

        counts_before_conflict = {
            table: _count(database, table)
            for table in (
                "CanonicalIncidentsV2",
                "CanonicalIncidentSourceEventsV2",
                "CanonicalIncidentAuditV2",
                "CanonicalIncidentIdempotencyV2",
                "CanonicalIncidentActiveKeysV2",
            )
        }
        reused_source = results[-1].source_event_id
        conflicting = _incident(
            "emulator-illegal-owner",
            reused_source,
            correlation_key="lte:different-cell:availability",
            now=BASE + timedelta(minutes=2),
        )
        with pytest.raises(SourceEventOwnershipConflictError):
            await incident_repository.create(
                conflicting,
                idempotency_key="emulator-illegal-owner",
                actor="emulator-test",
                reason="global ownership regression",
                trace_id=conflicting.trace_id,
            )
        assert counts_before_conflict == {
            table: _count(database, table) for table in counts_before_conflict
        }

        # While A is CLOSED, B can own its released correlation.  Reopening A
        # must then fail atomically: no revision, audit, idempotency, or key row
        # may change until B settles and releases the correlation.
        lifecycle_clock.advance()
        blocker = _incident(
            "emulator-correlation-blocker",
            "emulator-blocker-source",
            correlation_key=closed.correlation_key or "unreachable",
            now=lifecycle_clock.value,
        )
        blocker = await incident_repository.create(
            blocker,
            idempotency_key="emulator-create-correlation-blocker",
            actor="emulator-test",
            reason="occupy released correlation",
            trace_id=blocker.trace_id,
        )
        before_failed_reopen = {
            table: _count(database, table)
            for table in (
                "CanonicalIncidentsV2",
                "CanonicalIncidentAuditV2",
                "CanonicalIncidentIdempotencyV2",
                "CanonicalIncidentActiveKeysV2",
            )
        }
        lifecycle_clock.advance()
        with pytest.raises(ActiveIncidentConflictError):
            await incident_repository.transition(
                closed.incident_id,
                IncidentStatus.REOPENED,
                expected_revision=8,
                idempotency_key="emulator-reopen-while-blocked",
                actor="emulator-resolver",
                reason="correlation is still occupied",
                trace_id=closed.trace_id,
            )
        assert await incident_repository.get(closed.incident_id) == closed
        assert (
            tuple(await incident_repository.history(closed.incident_id))
            == closed_history
        )
        assert before_failed_reopen == {
            table: _count(database, table) for table in before_failed_reopen
        }

        lifecycle_clock.advance()
        cancelled_blocker = await incident_repository.transition(
            blocker.incident_id,
            IncidentStatus.CANCELLED,
            expected_revision=0,
            idempotency_key="emulator-cancel-correlation-blocker",
            actor="emulator-resolver",
            reason="release correlation for reopen",
            trace_id=blocker.trace_id,
        )
        assert cancelled_blocker.status is IncidentStatus.CANCELLED

        lifecycle_clock.advance()
        reopened = await incident_repository.transition(
            closed.incident_id,
            IncidentStatus.REOPENED,
            expected_revision=8,
            idempotency_key="emulator-reopen",
            actor="emulator-resolver",
            reason="new evidence",
            trace_id=closed.trace_id,
        )
        assert reopened.status is IncidentStatus.REOPENED
        assert (
            await incident_repository.find_active(source_event_id=reused_source)
        ) == reopened
        assert tuple(
            event.revision
            for event in await incident_repository.history(reopened.incident_id)
        ) == tuple(range(10))

        # Fifty different create-only callers racing on one source produce one
        # durable owner and 49 stable domain conflicts after Spanner retries.
        shared_source = "emulator-global-owner-race"
        owner_candidates = tuple(
            _incident(
                f"emulator-owner-{index:03d}",
                shared_source,
                correlation_key=f"lte:owner-{index:03d}:availability",
                now=lifecycle_clock.value,
            )
            for index in range(50)
        )
        ownership_results = await asyncio.gather(
            *(
                incident_repository.create(
                    candidate,
                    idempotency_key=f"owner-{candidate.incident_id}",
                    actor="emulator-test",
                    reason="concurrent global owner",
                    trace_id=candidate.trace_id,
                )
                for candidate in owner_candidates
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, Incident) for item in ownership_results) == 1
        assert sum(
            isinstance(item, SourceEventOwnershipConflictError)
            for item in ownership_results
        ) == 49
        with database.snapshot() as snapshot:
            ownership_rows = tuple(
                snapshot.execute_sql(
                    "SELECT incident_id FROM CanonicalIncidentSourceEventsV2 "
                    "WHERE source_event_id = @source_event_id",
                    params={"source_event_id": shared_source},
                    param_types={
                        "source_event_id": spanner.param_types.STRING,
                    },
                )
            )
        assert len(ownership_rows) == 1

        # Exercise the real StreamedResultSet claim path, retry delay, expired
        # lease recovery, successful delivery, and terminal DEAD state.
        outbox_a = SpannerOutboxRepository(database, clock=lambda: BASE)
        first_claim = await outbox_a.claim(
            lease_owner="emulator-dispatcher-a", lease_seconds=10
        )
        assert len(first_claim) == 1 and first_claim[0].attempts == 1
        pending = await outbox_a.retry(
            first_claim[0].event_id,
            lease_owner="emulator-dispatcher-a",
            expected_attempt=1,
            delay_seconds=1,
            error_code="resolver_unavailable",
        )
        assert pending.status is OutboxStatus.PENDING

        outbox_b = SpannerOutboxRepository(
            database, clock=lambda: BASE + timedelta(seconds=2)
        )
        second_claim = await outbox_b.claim(
            lease_owner="emulator-dispatcher-b", lease_seconds=1
        )
        assert second_claim[0].attempts == 2
        outbox_c = SpannerOutboxRepository(
            database, clock=lambda: BASE + timedelta(seconds=4)
        )
        recovered = await outbox_c.claim(
            lease_owner="emulator-dispatcher-c", lease_seconds=10
        )
        assert recovered[0].attempts == 3
        delivered = await outbox_c.mark_delivered(
            recovered[0].event_id,
            lease_owner="emulator-dispatcher-c",
            expected_attempt=3,
        )
        assert delivered.status is OutboxStatus.DELIVERED

        second_envelope = _envelope(
            900,
            correlation_key="lte:second-outbox:availability",
            now=BASE + timedelta(minutes=5),
        )
        second_created = await SpannerEventIngestRepository(
            database, clock=lambda: BASE + timedelta(minutes=5)
        ).ingest(second_envelope)
        assert second_created.disposition is IngestDisposition.CREATED
        outbox_dead = SpannerOutboxRepository(
            database, clock=lambda: BASE + timedelta(minutes=6)
        )
        dead_claim = await outbox_dead.claim(
            lease_owner="emulator-dispatcher-dead"
        )
        assert len(dead_claim) == 1
        dead = await outbox_dead.mark_dead(
            dead_claim[0].event_id,
            lease_owner="emulator-dispatcher-dead",
            expected_attempt=1,
            error_code="contract_rejected",
        )
        assert dead.status is OutboxStatus.DEAD

        # A real JSON mutation must deserialize as a canonical IncidentTrigger.
        with database.snapshot() as snapshot:
            payload = next(
                iter(
                    snapshot.execute_sql(
                        "SELECT payload FROM CanonicalIncidentOutboxV2 "
                        "WHERE event_id = @event_id",
                        params={"event_id": delivered.event_id},
                        param_types={
                            "event_id": spanner.param_types.STRING,
                        },
                    )
                )
            )[0]
        assert isinstance(payload, (dict, JsonObject))
        assert IncidentTrigger.model_validate(payload).incident == winner

    asyncio.run(scenario())
