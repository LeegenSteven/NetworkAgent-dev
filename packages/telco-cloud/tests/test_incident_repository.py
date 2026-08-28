from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from telco_domain.models import Incident, IncidentStatus, SourceEventAssociation
from telco_domain.ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentCorrelationConflictError,
    IncidentRepositoryError,
    RevisionConflictError,
    SourceEventOwnershipConflictError,
    UnsafeIncidentWriteError,
)
from telco_domain.state_machine import transition_incident

from telco_cloud import SpannerIncidentRepository
import telco_cloud.incident_repository as incident_repository_module

from fake_spanner import FakeDatabase, NOW, RetryingFakeDatabase


def _run(awaitable):
    return asyncio.run(awaitable)


def _incident(
    incident_id: str = "incident-01",
    *,
    correlation_key: str = "lte:cell-01:availability",
    source_event_ids: tuple[str, ...] = ("source-01",),
) -> Incident:
    return Incident(
        incident_id=incident_id,
        correlation_key=correlation_key,
        source_event_ids=source_event_ids,
        title="Cell availability degraded",
        trace_id=f"trace-{incident_id}",
        detected_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


async def _create(repository, incident, key="create-01"):
    return await repository.create(
        incident,
        idempotency_key=key,
        actor="fault-ingress",
        reason="normalized fault",
        trace_id=incident.trace_id,
    )


def test_create_get_list_history_and_exact_replay() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident()

        created = await _create(repository, candidate)
        replay = await _create(repository, candidate)

        assert replay == created
        assert await repository.get(created.incident_id) == created
        assert tuple(await repository.list()) == (created,)
        history = tuple(await repository.history(created.incident_id))
        assert [(event.revision, event.to_status) for event in history] == [
            (0, IncidentStatus.DETECTED)
        ]
        assert database.count("CanonicalIncidentsV2") == 1
        assert database.count("CanonicalIncidentAuditV2") == 1
        assert database.count("CanonicalIncidentIdempotencyV2") == 1

        with pytest.raises(IdempotencyConflictError):
            await _create(
                repository,
                candidate.model_copy(update={"title": "changed"}),
            )

    _run(scenario())


def test_snapshot_import_preserves_incident_and_complete_source_provenance() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident(source_event_ids=("source-01",))
        associations = (
            SourceEventAssociation(
                incident_id=candidate.incident_id,
                source_event_id="source-01",
                registered_at=NOW - timedelta(minutes=2),
                actor="local-detector",
                reason="original detection",
                idempotency_key="source-association-01",
                trace_id=candidate.trace_id,
            ),
            SourceEventAssociation(
                incident_id=candidate.incident_id,
                source_event_id="source-02",
                registered_at=NOW - timedelta(minutes=1),
                actor="fault-ingress",
                reason="correlated fault event",
                idempotency_key="source-association-02",
                trace_id="trace-correlated-source",
            ),
        )
        kwargs = {
            "idempotency_key": "migration-import-01",
            "actor": "canonical-migration",
            "reason": "one-time canonical incident import",
            "trace_id": candidate.trace_id,
        }

        imported = await repository.import_detected_snapshot(
            candidate, associations, **kwargs
        )
        replayed = await repository.import_detected_snapshot(
            candidate, associations, **kwargs
        )

        assert imported.incident == candidate
        assert imported.replayed is False
        assert replayed.incident == candidate
        assert replayed.replayed is True
        assert await repository.get(candidate.incident_id) == candidate
        assert tuple(
            await repository.source_event_associations(candidate.incident_id)
        ) == associations
        assert len(await repository.history(candidate.incident_id)) == 1
        assert await repository.find_active(source_event_id="source-02") == candidate

        changed_associations = (
            associations[0],
            associations[1].model_copy(update={"reason": "changed provenance"}),
        )
        with pytest.raises(IdempotencyConflictError):
            await repository.import_detected_snapshot(
                candidate, changed_associations, **kwargs
            )
        assert tuple(
            await repository.source_event_associations(candidate.incident_id)
        ) == associations

    _run(scenario())


@pytest.mark.parametrize(
    "corrupted_table",
    (
        "CanonicalIncidentsV2",
        "CanonicalIncidentSourceEventsV2",
        "CanonicalIncidentAuditV2",
        "CanonicalIncidentActiveKeysV2",
    ),
)
def test_snapshot_import_replay_cross_checks_every_durable_component(
    corrupted_table: str,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident()
        associations = (
            SourceEventAssociation(
                incident_id=candidate.incident_id,
                source_event_id="source-01",
                registered_at=NOW,
                actor="local-detector",
                reason="original detection",
                idempotency_key="source-association-01",
                trace_id=candidate.trace_id,
            ),
        )
        kwargs = {
            "idempotency_key": "migration-integrity",
            "actor": "canonical-migration",
            "reason": "one-time canonical incident import",
            "trace_id": candidate.trace_id,
        }
        await repository.import_detected_snapshot(candidate, associations, **kwargs)

        if corrupted_table == "CanonicalIncidentActiveKeysV2":
            database.tables[corrupted_table].clear()
        else:
            database.tables[corrupted_table].pop(
                next(iter(database.tables[corrupted_table]))
            )

        with pytest.raises(IncidentRepositoryError, match="migration"):
            await repository.import_detected_snapshot(
                candidate, associations, **kwargs
            )

    _run(scenario())


def test_snapshot_import_rejects_association_overflow_before_transaction() -> None:
    class NoIoDatabase(FakeDatabase):
        def __init__(self) -> None:
            super().__init__()
            self.transaction_calls = 0

        def run_in_transaction(self, callback):
            self.transaction_calls += 1
            return super().run_in_transaction(callback)

    async def scenario() -> None:
        database = NoIoDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident(source_event_ids=())
        repeated = SourceEventAssociation(
            incident_id=candidate.incident_id,
            source_event_id="source-overflow",
            registered_at=NOW,
            actor="local-detector",
            reason="bounded migration input",
            idempotency_key="source-overflow",
            trace_id=candidate.trace_id,
        )

        with pytest.raises(ValueError, match="exceed"):
            await repository.import_detected_snapshot(
                candidate,
                (repeated,) * 1001,
                idempotency_key="migration-overflow",
                actor="canonical-migration",
                reason="one-time canonical incident import",
                trace_id=candidate.trace_id,
            )
        assert database.transaction_calls == 0
        assert all(not rows for rows in database.tables.values())

    _run(scenario())


def test_snapshot_import_supports_1000_associations_and_exact_replay() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident(
            "migration-capacity",
            correlation_key="lte:migration-capacity:availability",
            source_event_ids=tuple(
                f"capacity-source-{index:04d}" for index in range(1000)
            ),
        )
        associations = tuple(
            SourceEventAssociation(
                incident_id=candidate.incident_id,
                source_event_id=f"capacity-source-{index:04d}",
                registered_at=NOW,
                actor="canonical-migration",
                reason="capacity boundary fixture",
                idempotency_key=f"capacity-source-{index:04d}",
                trace_id=candidate.trace_id,
            )
            for index in range(1000)
        )
        kwargs = {
            "idempotency_key": "migration-capacity",
            "actor": "canonical-migration",
            "reason": "one-time canonical incident import",
            "trace_id": candidate.trace_id,
        }

        imported = await repository.import_detected_snapshot(
            candidate,
            associations,
            **kwargs,
        )
        replayed = await repository.import_detected_snapshot(
            candidate,
            associations,
            **kwargs,
        )

        assert imported.incident == candidate
        assert imported.replayed is False
        assert replayed.incident == candidate
        assert replayed.replayed is True
        assert database.count("CanonicalIncidentsV2") == 1
        assert database.count("CanonicalIncidentSourceEventsV2") == 1000
        assert database.count("CanonicalIncidentAuditV2") == 1
        assert database.count("CanonicalIncidentIdempotencyV2") == 1
        assert database.count("CanonicalIncidentActiveKeysV2") == 1001

        transitioned = await repository.transition(
            candidate.incident_id,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="migration-capacity-transition",
            actor="canonical-migration",
            reason="capacity lifecycle verification",
            trace_id=candidate.trace_id,
        )
        assert transitioned.status is IncidentStatus.TRIAGED
        assert database.count("CanonicalIncidentSourceEventsV2") == 1000
        assert database.count("CanonicalIncidentAuditV2") == 2
        assert database.count("CanonicalIncidentIdempotencyV2") == 2
        assert database.count("CanonicalIncidentActiveKeysV2") == 1001

    _run(scenario())


def test_regular_create_and_transition_support_1000_source_events() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        source_ids = tuple(f"regular-source-{index:04d}" for index in range(1000))
        candidate = _incident(
            "regular-capacity",
            correlation_key="lte:regular-capacity:availability",
            source_event_ids=source_ids,
        )

        created = await _create(repository, candidate, "regular-capacity-create")
        transitioned = await repository.transition(
            created.incident_id,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="regular-capacity-transition",
            actor="fault-ingress",
            reason="capacity lifecycle verification",
            trace_id=created.trace_id,
        )

        assert transitioned.status is IncidentStatus.TRIAGED
        assert database.count("CanonicalIncidentsV2") == 1
        assert database.count("CanonicalIncidentSourceEventsV2") == 1000
        assert database.count("CanonicalIncidentActiveKeysV2") == 1001
        assert database.count("CanonicalIncidentAuditV2") == 2
        assert database.count("CanonicalIncidentIdempotencyV2") == 2

    _run(scenario())


@pytest.mark.parametrize(
    "fail_table",
    (
        "CanonicalIncidentsV2",
        "CanonicalIncidentSourceEventsV2",
        "CanonicalIncidentActiveKeysV2",
        "CanonicalIncidentAuditV2",
        "CanonicalIncidentIdempotencyV2",
    ),
)
def test_snapshot_import_failure_rolls_back_every_table(fail_table: str) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        database.fail_table = fail_table
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident()
        association = SourceEventAssociation(
            incident_id=candidate.incident_id,
            source_event_id="source-01",
            registered_at=NOW,
            actor="local-detector",
            reason="rollback fixture",
            idempotency_key="source-rollback",
            trace_id=candidate.trace_id,
        )

        with pytest.raises(RuntimeError):
            await repository.import_detected_snapshot(
                candidate,
                (association,),
                idempotency_key="migration-rollback",
                actor="canonical-migration",
                reason="one-time canonical incident import",
                trace_id=candidate.trace_id,
            )
        assert all(not rows for rows in database.tables.values())

    _run(scenario())


def test_list_uses_per_item_policy_and_separate_streamed_batch_budget(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        for index in range(3):
            candidate = _incident(
                f"incident-batch-{index}",
                correlation_key=f"lte:batch-{index}:availability",
                source_event_ids=(f"source-batch-{index}",),
            ).model_copy(update={"model_metadata": {"safe_blob": "x" * 90_000}})
            await _create(repository, candidate, f"create-batch-{index}")

        # The repository is allowed to return a page larger than the MCP wire
        # budget; the MCP adapter owns its independent 256 KiB response cap.
        assert len(await repository.list()) == 3

        monkeypatch.setattr(
            incident_repository_module,
            "MAX_REPOSITORY_BATCH_BYTES",
            200_000,
        )
        with pytest.raises(UnsafeIncidentWriteError, match="cumulative"):
            await repository.list()

    _run(scenario())


def test_active_conflict_and_cross_source_correlation_are_durable() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        first_repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        second_repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        original = await _create(first_repository, _incident())

        with pytest.raises(ActiveIncidentConflictError):
            await _create(
                second_repository,
                _incident("incident-conflict", source_event_ids=("other",)),
                "conflict",
            )

        correlated = await second_repository.create_or_correlate(
            _incident("incident-02", source_event_ids=("source-02",)),
            idempotency_key="correlate-02",
            actor="kpi-detector",
            reason="same fault signature",
            trace_id="trace-incident-02",
        )
        assert correlated == original
        assert (
            await first_repository.find_active(source_event_id="source-02")
        ) == original
        assert database.count("CanonicalIncidentsV2") == 1
        assert database.count("CanonicalIncidentSourceEventsV2") == 2
        associations = tuple(
            await first_repository.source_event_associations(original.incident_id)
        )
        assert [item.source_event_id for item in associations] == [
            "source-01",
            "source-02",
        ]
        assert associations[1].actor == "kpi-detector"
        assert associations[1].reason == "same fault signature"
        active_rows = tuple(
            database.tables["CanonicalIncidentActiveKeysV2"].values()
        )
        assert active_rows
        assert all(
            set(row) == {"key_hash", "key_kind", "incident_id", "registered_at"}
            for row in active_rows
        )
        assert all("source-01" not in str(row) for row in active_rows)
        assert all(original.correlation_key not in str(row) for row in active_rows)

    _run(scenario())


def test_conflicting_active_selectors_fail_closed_without_writes() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        first = await _create(
            repository,
            _incident(
                "incident-a",
                correlation_key="lte:cell-a:availability",
                source_event_ids=("source-a",),
            ),
            "create-a",
        )
        second = await _create(
            repository,
            _incident(
                "incident-b",
                correlation_key="lte:cell-b:availability",
                source_event_ids=("source-b",),
            ),
            "create-b",
        )
        before = {
            table: database.count(table)
            for table in (
                "CanonicalIncidentsV2",
                "CanonicalIncidentAuditV2",
                "CanonicalIncidentIdempotencyV2",
                "CanonicalIncidentSourceEventsV2",
                "CanonicalIncidentActiveKeysV2",
            )
        }

        with pytest.raises(IncidentCorrelationConflictError) as captured:
            await repository.create_or_correlate(
                _incident(
                    "incident-c",
                    correlation_key=first.correlation_key,
                    source_event_ids=(second.source_event_ids[0],),
                ),
                idempotency_key="split-selectors",
                actor="fault-ingress",
                reason="conflicting selectors",
                trace_id="trace-incident-c",
            )

        assert captured.value.requested_incident_id == "incident-c"
        assert captured.value.conflicting_incident_ids == (
            "incident-a",
            "incident-b",
        )
        assert {
            table: database.count(table) for table in before
        } == before

    _run(scenario())


def test_transition_is_cas_audited_and_stale_write_fails() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        original = await _create(repository, _incident())
        candidate = transition_incident(
            original,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            transitioned_at=NOW,
            now=NOW,
        )
        committed = await repository.save(
            candidate,
            expected_revision=0,
            idempotency_key="triage-01",
            actor="resolver",
            reason="triaged",
            trace_id=original.trace_id,
        )
        assert committed.status is IncidentStatus.TRIAGED
        assert [event.revision for event in await repository.history(original.incident_id)] == [0, 1]
        assert [
            event.revision
            for event in await repository.history(
                original.incident_id, limit=1, offset=1
            )
        ] == [1]

        with pytest.raises(RevisionConflictError):
            await repository.transition(
                original.incident_id,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                idempotency_key="stale",
                actor="resolver",
                reason="stale",
                trace_id=original.trace_id,
            )

    _run(scenario())


def test_transition_atomically_registers_appended_source_provenance_and_key() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        created = await _create(repository, _incident())

        triaged = await repository.transition(
            created.incident_id,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="append-source",
            actor="resolver",
            reason="new source evidence",
            trace_id=created.trace_id,
            updates={"source_event_ids": ("source-01", "source-02")},
        )

        assert triaged.source_event_ids == ("source-01", "source-02")
        associations = tuple(
            await repository.source_event_associations(created.incident_id)
        )
        assert tuple(item.source_event_id for item in associations) == (
            "source-01",
            "source-02",
        )
        assert await repository.find_active(source_event_id="source-02") == triaged

        other = await _create(
            repository,
            _incident(
                "incident-other-owner",
                correlation_key="lte:other:availability",
                source_event_ids=("source-03",),
            ),
            "create-other-owner",
        )
        before = {
            table: database.count(table)
            for table in (
                "CanonicalIncidentsV2",
                "CanonicalIncidentAuditV2",
                "CanonicalIncidentIdempotencyV2",
                "CanonicalIncidentSourceEventsV2",
                "CanonicalIncidentActiveKeysV2",
            )
        }
        with pytest.raises(SourceEventOwnershipConflictError):
            await repository.transition(
                triaged.incident_id,
                IncidentStatus.INVESTIGATING,
                expected_revision=1,
                idempotency_key="append-owned-source",
                actor="resolver",
                reason="ownership rollback regression",
                trace_id=triaged.trace_id,
                updates={
                    "source_event_ids": (
                        "source-01",
                        "source-02",
                        other.source_event_ids[0],
                    )
                },
            )

        assert await repository.get(triaged.incident_id) == triaged
        assert tuple(
            item.source_event_id
            for item in await repository.source_event_associations(
                triaged.incident_id
            )
        ) == ("source-01", "source-02")
        assert {table: database.count(table) for table in before} == before

    _run(scenario())


def test_transaction_retries_reuse_one_trusted_timestamp() -> None:
    async def scenario() -> None:
        database = RetryingFakeDatabase()
        calls = 0

        def advancing_clock():
            nonlocal calls
            calls += 1
            return NOW + timedelta(seconds=calls)

        repository = SpannerIncidentRepository(database, clock=advancing_clock)
        created = await _create(repository, _incident())

        assert calls == 1
        assert database.attempt_results == (created, created)
        audit = tuple(await repository.history(created.incident_id))
        assert audit[0].occurred_at == NOW + timedelta(seconds=1)

    _run(scenario())


def test_reopen_reacquires_every_provenance_source_key_not_only_aggregate_ids() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        created = await _create(repository, _incident())
        correlated = await repository.create_or_correlate(
            _incident("incident-02", source_event_ids=("source-02",)),
            idempotency_key="correlate-02",
            actor="fault-ingress",
            reason="same fault signature",
            trace_id="trace-incident-02",
        )
        assert correlated.source_event_ids == ("source-01",)

        # Model a legally completed lifecycle without coupling this repository
        # regression to the RCA/action fixtures required by intermediate states.
        closed_payload = created.model_dump(mode="python", round_trip=True)
        closed_payload.update(status=IncidentStatus.CLOSED, revision=8)
        closed = Incident.model_validate(closed_payload)
        incident_row = database.tables["CanonicalIncidentsV2"][(created.incident_id,)]
        incident_row.update(
            status=closed.status.value,
            revision=closed.revision,
            payload=closed.model_dump(mode="json", round_trip=True),
        )
        database.tables["CanonicalIncidentActiveKeysV2"].clear()

        reopened = await repository.transition(
            closed.incident_id,
            IncidentStatus.REOPENED,
            expected_revision=8,
            idempotency_key="reopen-01",
            actor="resolver",
            reason="new evidence arrived",
            trace_id=closed.trace_id,
        )

        assert reopened.status is IncidentStatus.REOPENED
        assert reopened.source_event_ids == ("source-01",)
        assert await repository.find_active(source_event_id="source-01") == reopened
        assert await repository.find_active(source_event_id="source-02") == reopened
        with pytest.raises(SourceEventOwnershipConflictError):
            await _create(
                repository,
                _incident(
                    "incident-conflict-after-reopen",
                    correlation_key="different-correlation",
                    source_event_ids=("source-02",),
                ),
                "conflict-after-reopen",
            )

    _run(scenario())


def test_all_public_write_callbacks_are_retry_deterministic() -> None:
    async def scenario() -> None:
        async def assert_create_or_correlate() -> None:
            database = RetryingFakeDatabase()
            calls = 0

            def clock():
                nonlocal calls
                calls += 1
                return NOW + timedelta(seconds=calls)

            repository = SpannerIncidentRepository(database, clock=clock)
            result = await repository.create_or_correlate(
                _incident(),
                idempotency_key="retry-correlate",
                actor="detector",
                reason="retry contract",
                trace_id="trace-incident-01",
            )
            assert calls == 1
            assert database.attempt_results == (result, result)

        async def assert_save() -> None:
            database = RetryingFakeDatabase()
            setup = SpannerIncidentRepository(database, clock=lambda: NOW)
            created = await _create(setup, _incident())
            candidate = transition_incident(
                created,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                transitioned_at=NOW + timedelta(seconds=1),
                now=NOW + timedelta(seconds=1),
            )
            calls = 0

            def clock():
                nonlocal calls
                calls += 1
                return NOW + timedelta(seconds=calls)

            repository = SpannerIncidentRepository(database, clock=clock)
            result = await repository.save(
                candidate,
                expected_revision=0,
                idempotency_key="retry-save",
                actor="resolver",
                reason="retry contract",
                trace_id=created.trace_id,
            )
            assert calls == 1
            assert database.attempt_results == (result, result)

        async def assert_transition_and_replay_precedes_stale() -> None:
            database = RetryingFakeDatabase()
            setup = SpannerIncidentRepository(database, clock=lambda: NOW)
            created = await _create(setup, _incident())
            calls = 0

            def clock():
                nonlocal calls
                calls += 1
                return NOW + timedelta(seconds=calls)

            repository = SpannerIncidentRepository(database, clock=clock)
            kwargs = {
                "expected_revision": 0,
                "idempotency_key": "retry-transition",
                "actor": "resolver",
                "reason": "retry contract",
                "trace_id": created.trace_id,
            }
            result = await repository.transition(
                created.incident_id,
                IncidentStatus.TRIAGED,
                **kwargs,
            )
            assert calls == 1
            assert database.attempt_results == (result, result)

            replay = await repository.transition(
                created.incident_id,
                IncidentStatus.TRIAGED,
                **kwargs,
            )
            assert replay == result

        async def assert_snapshot_import() -> None:
            database = RetryingFakeDatabase()
            calls = 0

            def clock():
                nonlocal calls
                calls += 1
                return NOW + timedelta(seconds=calls)

            repository = SpannerIncidentRepository(database, clock=clock)
            candidate = _incident("migration-retry")
            association = SourceEventAssociation(
                incident_id=candidate.incident_id,
                source_event_id="source-01",
                registered_at=NOW,
                actor="local-detector",
                reason="retry fixture",
                idempotency_key="migration-retry-source",
                trace_id=candidate.trace_id,
            )
            result = await repository.import_detected_snapshot(
                candidate,
                (association,),
                idempotency_key="migration-retry",
                actor="canonical-migration",
                reason="one-time canonical incident import",
                trace_id=candidate.trace_id,
            )
            assert calls == 1
            assert database.attempt_results == (result, result)

        await assert_create_or_correlate()
        await assert_save()
        await assert_transition_and_replay_precedes_stale()
        await assert_snapshot_import()

    _run(scenario())


def test_settled_transition_releases_all_correlated_active_keys() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        created = await _create(repository, _incident())
        await repository.create_or_correlate(
            _incident("incident-02", source_event_ids=("source-02",)),
            idempotency_key="correlate-before-settle",
            actor="fault-ingress",
            reason="same fault signature",
            trace_id="trace-incident-02",
        )

        settled = await repository.transition(
            created.incident_id,
            IncidentStatus.CANCELLED,
            expected_revision=0,
            idempotency_key="cancel-01",
            actor="operator",
            reason="test settlement",
            trace_id=created.trace_id,
        )

        assert settled.status is IncidentStatus.CANCELLED
        assert await repository.find_active(
            correlation_key=created.correlation_key
        ) is None
        assert await repository.find_active(source_event_id="source-01") is None
        assert await repository.find_active(source_event_id="source-02") is None
        assert database.count("CanonicalIncidentActiveKeysV2") == 0

    _run(scenario())


def test_source_event_owner_is_immutable_after_incident_settles() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        created = await _create(repository, _incident())
        await repository.transition(
            created.incident_id,
            IncidentStatus.CANCELLED,
            expected_revision=0,
            idempotency_key="cancel-owner",
            actor="operator",
            reason="settled owner regression",
            trace_id=created.trace_id,
        )
        before = {name: database.count(name) for name in database.tables}

        with pytest.raises(SourceEventOwnershipConflictError) as exc_info:
            await _create(
                repository,
                _incident(
                    "incident-new-owner",
                    correlation_key="different-correlation",
                    source_event_ids=("source-01",),
                ),
                "new-owner",
            )

        assert exc_info.value.source_event_id == "source-01"
        assert exc_info.value.owner_incident_id == created.incident_id
        assert exc_info.value.requested_incident_id == "incident-new-owner"
        assert {name: database.count(name) for name in database.tables} == before

    _run(scenario())


@pytest.mark.parametrize(
    "table,column,value,read_kind",
    [
        ("CanonicalIncidentsV2", "status", "CLOSED", "incident"),
        ("CanonicalIncidentAuditV2", "event_id", "audit-corrupt", "history"),
        (
            "CanonicalIncidentSourceEventsV2",
            "source_event_id",
            "source-corrupt",
            "association",
        ),
        (
            "CanonicalIncidentIdempotencyV2",
            "result_incident_id",
            "incident-corrupt",
            "idempotency",
        ),
    ],
)
def test_denormalized_storage_bindings_fail_closed_on_corruption(
    table: str,
    column: str,
    value: object,
    read_kind: str,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        created = await _create(repository, _incident())
        row = next(iter(database.tables[table].values()))
        row[column] = value

        with pytest.raises(IncidentRepositoryError, match="persisted"):
            if read_kind == "incident":
                await repository.get(created.incident_id)
            elif read_kind == "history":
                await repository.history(created.incident_id)
            elif read_kind == "association":
                await repository.source_event_associations(created.incident_id)
            else:
                await repository.find_by_idempotency_key(
                    created.incident_id,
                    "create-01",
                    operation="create",
                )

    _run(scenario())


@pytest.mark.parametrize("read_kind", ["get", "find-idempotency", "replay"])
def test_persisted_incident_privacy_is_enforced_on_every_read_path(
    read_kind: str,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerIncidentRepository(database, clock=lambda: NOW)
        candidate = _incident()
        created = await _create(repository, candidate)
        unsafe = created.model_copy(
            update={"model_metadata": {"note": "imsi-001010123456789"}}
        ).model_dump(mode="json", round_trip=True)
        if read_kind == "get":
            database.tables["CanonicalIncidentsV2"][(created.incident_id,)][
                "payload"
            ] = unsafe
        else:
            row = next(
                iter(database.tables["CanonicalIncidentIdempotencyV2"].values())
            )
            row["result_payload"] = unsafe

        with pytest.raises(UnsafeIncidentWriteError):
            if read_kind == "get":
                await repository.get(created.incident_id)
            elif read_kind == "find-idempotency":
                await repository.find_by_idempotency_key(
                    created.incident_id,
                    "create-01",
                    operation="create",
                )
            else:
                await _create(repository, candidate)

    _run(scenario())
