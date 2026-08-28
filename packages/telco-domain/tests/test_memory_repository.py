"""Repository contract tests for atomic revision and idempotency semantics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import telco_domain.memory as memory_module
from telco_domain.memory import InMemoryIncidentRepository
from telco_domain.models import (
    Incident,
    IncidentStatus,
    SourceEventAssociation,
)
from telco_domain.ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentAlreadyExistsError,
    IncidentRepository,
    RevisionConflictError,
    SourceEventOwnershipConflictError,
    UnsafeIncidentWriteError,
)
from telco_domain.state_machine import transition_incident


def _run(coro):
    return asyncio.run(coro)


def _incident(incident_id: str = "incident-1", *, title: str = "") -> Incident:
    return Incident(
        incident_id=incident_id,
        title=title,
        trace_id=f"trace-{incident_id}",
    )


def test_snapshot_import_rejects_unbounded_or_oversized_associations_without_consuming() -> None:
    class CountingIterable:
        def __init__(self, value) -> None:
            self.value = value
            self.consumed = 0

        def __iter__(self):
            while True:
                self.consumed += 1
                yield self.value

    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        incident = _incident("migration-bounded")
        association = SourceEventAssociation(
            incident_id=incident.incident_id,
            source_event_id="source-bounded",
            registered_at=datetime(2030, 1, 1, tzinfo=UTC),
            actor="canonical-migration",
            reason="bounded migration input",
            idempotency_key="association-bounded",
            trace_id=incident.trace_id,
        )
        unbounded = CountingIterable(association)
        kwargs = {
            "idempotency_key": "migration-bounded",
            "actor": "canonical-migration",
            "reason": "one-time canonical incident import",
            "trace_id": incident.trace_id,
        }

        with pytest.raises(ValueError, match="bounded"):
            await repository.import_detected_snapshot(
                incident,
                unbounded,
                **kwargs,
            )
        assert unbounded.consumed == 0

        with pytest.raises(ValueError, match="capacity"):
            await repository.import_detected_snapshot(
                incident,
                (association,) * 1001,
                **kwargs,
            )
        assert await repository.list() == ()

    _run(scenario())


async def _create(
    repository: InMemoryIncidentRepository,
    incident: Incident,
    idempotency_key: str,
) -> Incident:
    return await repository.create(
        incident,
        idempotency_key=idempotency_key,
        actor="detector-agent",
        reason="test incident detection",
        trace_id=incident.trace_id,
    )


def test_repository_page_has_a_cumulative_byte_budget(monkeypatch) -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        first = await _create(
            repository,
            Incident(
                incident_id="batch-a",
                trace_id="trace-batch-a",
                model_metadata={"safe_blob": "a" * 2_000},
            ),
            "batch-create-a",
        )
        second = await _create(
            repository,
            Incident(
                incident_id="batch-b",
                trace_id="trace-batch-b",
                model_metadata={"safe_blob": "b" * 2_000},
            ),
            "batch-create-b",
        )
        budget = sum(
            len(item.model_dump_json(round_trip=True).encode("utf-8"))
            for item in (first, second)
        ) - 1
        monkeypatch.setattr(memory_module, "MAX_REPOSITORY_BATCH_BYTES", budget)

        assert len(await repository.list(limit=1)) == 1
        with pytest.raises(UnsafeIncidentWriteError, match="batch"):
            await repository.list(limit=2)

    _run(scenario())


async def _save(
    repository: InMemoryIncidentRepository,
    incident: Incident,
    *,
    expected_revision: int,
    idempotency_key: str,
) -> Incident:
    return await repository.save(
        incident,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        actor="resolver-agent",
        reason="test state transition",
        trace_id=incident.trace_id,
    )


async def _compare_and_swap(
    repository: InMemoryIncidentRepository,
    incident: Incident,
    *,
    expected_revision: int,
    idempotency_key: str,
) -> Incident:
    return await repository.compare_and_swap(
        incident,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        actor="resolver-agent",
        reason="test compare and swap",
        trace_id=incident.trace_id,
    )


def _successor(incident: Incident, *, title: str) -> Incident:
    return transition_incident(
        incident,
        IncidentStatus.TRIAGED,
        expected_revision=incident.revision,
        updates={"title": title},
    )


def test_repository_satisfies_framework_neutral_protocol() -> None:
    assert isinstance(InMemoryIncidentRepository(), IncidentRepository)


def test_create_get_list_and_idempotency_lookup() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        first = _incident("incident-b")
        second = _incident("incident-a")

        created = await _create(repository, first, "create-b")
        second_created = await _create(repository, second, "create-a")
        second_triaged = _successor(second_created, title="triaged")
        second_committed = await _save(
            repository,
            second_triaged,
            expected_revision=0,
            idempotency_key="triage-a",
        )

        assert created.incident_id == first.incident_id
        assert await repository.get(first.incident_id) == created
        assert await repository.get("missing") is None
        assert (
            await repository.find_by_idempotency_key(
                first.incident_id, "create-b", operation="create"
            )
            == created
        )
        assert [item.incident_id for item in await repository.list()] == [
            "incident-a",
            "incident-b",
        ]
        assert await repository.list(status=IncidentStatus.TRIAGED) == (
            second_committed,
        )
        assert await repository.list(limit=1, offset=1) == (created,)
        with pytest.raises(ValueError, match="limit"):
            await repository.list(limit=1_001)
        with pytest.raises(ValueError, match="offset"):
            await repository.list(offset=100_001)
        with pytest.raises(ValueError, match="explicit limit"):
            await repository.history(first.incident_id, offset=1)
        with pytest.raises(ValueError, match="offset"):
            await repository.source_event_associations(
                first.incident_id, offset=100_001
            )

    _run(scenario())


def test_exact_create_retry_returns_first_result_without_duplication() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        incident = _incident()

        first = await _create(repository, incident, "create-1")
        replay = await _create(repository, incident, "create-1")

        assert replay == first
        assert replay.revision == 0
        assert len(await repository.list()) == 1

    _run(scenario())


def test_create_rejects_reused_key_with_different_fingerprint() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        await _create(repository, _incident("incident-1"), "same-key")

        with pytest.raises(IdempotencyConflictError):
            changed = _incident("incident-1", title="different request")
            await _create(repository, changed, "same-key")

    _run(scenario())


def test_create_rejects_duplicate_incident_id_with_new_key() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        await _create(repository, _incident(), "first-key")

        with pytest.raises(IncidentAlreadyExistsError):
            await _create(repository, _incident(), "second-key")

    _run(scenario())


def test_save_is_atomic_cas_and_exact_retry_does_not_increment_twice() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        original = await _create(repository, _incident(), "create")
        candidate = _successor(original, title="triaged")

        committed = await _save(
            repository,
            candidate,
            expected_revision=0,
            idempotency_key="save-1",
        )
        replay = await _save(
            repository,
            candidate,
            expected_revision=0,
            idempotency_key="save-1",
        )

        assert committed.revision == 1
        assert replay == committed
        assert (await repository.get(original.incident_id)).revision == 1
        assert (
            await repository.find_by_idempotency_key(
                original.incident_id, "save-1", operation="save"
            )
            == committed
        )

    _run(scenario())


def test_save_checks_idempotency_before_stale_revision() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        original = await _create(repository, _incident(), "create")
        candidate = _successor(original, title="updated")
        await _compare_and_swap(
            repository,
            candidate,
            expected_revision=0,
            idempotency_key="transition-1",
        )

        # The candidate is now stale, but this is an exact replay of its successful
        # request and must return the first result instead of reporting a conflict.
        replay = await _compare_and_swap(
            repository,
            candidate,
            expected_revision=0,
            idempotency_key="transition-1",
        )
        assert replay.revision == 1

    _run(scenario())


def test_stale_save_and_changed_idempotent_replay_are_rejected() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        original = await _create(repository, _incident(), "create")
        candidate = _successor(original, title="first update")
        await _save(
            repository,
            candidate,
            expected_revision=0,
            idempotency_key="save-1",
        )

        with pytest.raises(RevisionConflictError) as stale:
            await _save(
                repository,
                _successor(original, title="stale writer"),
                expected_revision=0,
                idempotency_key="save-2",
            )
        assert stale.value.actual_revision == 1

        changed_data = candidate.model_dump(mode="python", round_trip=True)
        changed_data["title"] = "changed replay"
        changed = Incident.model_validate(changed_data)
        with pytest.raises(IdempotencyConflictError):
            await _save(
                repository,
                changed,
                expected_revision=0,
                idempotency_key="save-1",
            )

    _run(scenario())


def test_concurrent_compare_and_swap_allows_only_one_writer() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        original = await _create(repository, _incident(), "create")
        candidates = (
            _successor(original, title="writer-a"),
            _successor(original, title="writer-b"),
        )

        results = await asyncio.gather(
            _compare_and_swap(
                repository,
                candidates[0], expected_revision=0, idempotency_key="writer-a"
            ),
            _compare_and_swap(
                repository,
                candidates[1], expected_revision=0, idempotency_key="writer-b"
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(item, Incident) for item in results) == 1
        assert sum(isinstance(item, RevisionConflictError) for item in results) == 1
        persisted = await repository.get(original.incident_id)
        assert persisted is not None
        assert persisted.revision == 1

    _run(scenario())


def test_create_requires_revision_zero_and_writes_atomic_audit_history() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        invalid_data = _incident().model_dump(mode="python", round_trip=True)
        invalid_data["revision"] = 1
        invalid = Incident.model_validate(invalid_data)
        with pytest.raises(ValueError, match="revision 0"):
            await _create(repository, invalid, "invalid-create")

        original = await _create(repository, _incident(), "create")
        successor = _successor(original, title="committed")
        await _save(
            repository,
            successor,
            expected_revision=0,
            idempotency_key="save",
        )
        # Exact retry must not append another audit event.
        await _save(
            repository,
            successor,
            expected_revision=0,
            idempotency_key="save",
        )

        history = await repository.history(original.incident_id)
        assert [event.from_status for event in history] == [
            None,
            IncidentStatus.DETECTED,
        ]
        assert [event.to_status for event in history] == [
            IncidentStatus.DETECTED,
            IncidentStatus.TRIAGED,
        ]
        assert [event.revision for event in history] == [0, 1]
        assert all(event.trace_id == original.trace_id for event in history)
        assert [event.revision for event in await repository.history(
            original.incident_id, limit=1, offset=1
        )] == [1]
        assert await repository.history(original.incident_id, limit=1, offset=2) == ()
        with pytest.raises(ValueError, match="limit"):
            await repository.history(original.incident_id, limit=0)
        with pytest.raises(ValueError, match="offset"):
            await repository.history(original.incident_id, offset=-1)

    _run(scenario())


@pytest.mark.parametrize("key", ("", "   "))
def test_empty_idempotency_key_is_rejected(key: str) -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        with pytest.raises(ValueError, match="idempotency_key"):
            await _create(repository, _incident(), key)

    _run(scenario())


def test_atomic_transition_rejects_illegal_jump_and_guard_bypass() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        incident = _incident()
        created = await repository.create(
            incident,
            idempotency_key="create",
            actor="detector-agent",
            reason="threshold violation",
            trace_id=incident.trace_id,
        )

        with pytest.raises(ValueError, match="cannot transition"):
            await repository.transition(
                incident.incident_id,
                IncidentStatus.CLOSED,
                expected_revision=0,
                idempotency_key="illegal-jump",
                actor="resolver-agent",
                reason="attempted shortcut",
                trace_id=incident.trace_id,
            )

        with pytest.raises(ValueError, match="duplicate_of"):
            await repository.transition(
                incident.incident_id,
                IncidentStatus.DUPLICATE,
                expected_revision=0,
                idempotency_key="guard-bypass",
                actor="resolver-agent",
                reason="missing duplicate target",
                trace_id=incident.trace_id,
            )

        persisted = await repository.get(incident.incident_id)
        assert persisted == created
        assert len(await repository.history(incident.incident_id)) == 1

    _run(scenario())


def test_save_cannot_commit_a_crafted_illegal_or_unguarded_successor() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        incident = _incident()
        created = await _create(repository, incident, "create")

        for status, expected_message in (
            (IncidentStatus.CLOSED, "cannot transition"),
            (IncidentStatus.DUPLICATE, "duplicate_of"),
        ):
            payload = created.model_dump(mode="python", round_trip=True)
            payload.update(status=status, revision=1)
            crafted = Incident.model_validate(payload)
            with pytest.raises(ValueError, match=expected_message):
                await _save(
                    repository,
                    crafted,
                    expected_revision=0,
                    idempotency_key=f"crafted-{status.value}",
                )

        assert await repository.get(incident.incident_id) == created
        assert len(await repository.history(incident.incident_id)) == 1

    _run(scenario())


def test_transition_audit_requires_and_persists_actor_reason_and_trace() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        incident = _incident()
        await repository.create(
            incident,
            idempotency_key="create",
            actor="detector-agent",
            reason="detected KPI breach",
            trace_id=incident.trace_id,
        )

        transitioned = await repository.transition(
            incident.incident_id,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="triage",
            actor="supervisor-agent",
            reason="accepted detector evidence",
            trace_id=incident.trace_id,
        )
        assert transitioned.status is IncidentStatus.TRIAGED
        event = (await repository.history(incident.incident_id))[-1]
        assert event.actor == "supervisor-agent"
        assert event.reason == "accepted detector evidence"
        assert event.trace_id == incident.trace_id

        with pytest.raises(ValueError, match="actor"):
            await repository.transition(
                incident.incident_id,
                IncidentStatus.INVESTIGATING,
                expected_revision=1,
                idempotency_key="missing-actor",
                actor="",
                reason="begin investigation",
                trace_id=incident.trace_id,
            )

    _run(scenario())


def test_create_or_correlate_is_atomic_for_correlation_and_source_event() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        first = Incident(
            incident_id="incident-a",
            correlation_key="cell-1:kpi:bucket-1",
            source_event_ids=("event-shared",),
            trace_id="trace-a",
        )
        second = Incident(
            incident_id="incident-b",
            correlation_key="cell-1:kpi:bucket-1",
            source_event_ids=("event-other",),
            trace_id="trace-b",
        )

        results = await asyncio.gather(
            repository.create_or_correlate(
                first,
                idempotency_key="detect-a",
                actor="detector-agent",
                reason="candidate a",
                trace_id=first.trace_id,
            ),
            repository.create_or_correlate(
                second,
                idempotency_key="detect-b",
                actor="detector-agent",
                reason="candidate b",
                trace_id=second.trace_id,
            ),
        )
        assert results[0].incident_id == results[1].incident_id
        assert len(await repository.list()) == 1

        by_source = Incident(
            incident_id="incident-c",
            correlation_key="different-correlation",
            source_event_ids=("event-shared",),
            trace_id="trace-c",
        )
        correlated = await repository.create_or_correlate(
            by_source,
            idempotency_key="detect-c",
            actor="detector-agent",
            reason="same source event",
            trace_id=by_source.trace_id,
        )
        assert correlated.incident_id == results[0].incident_id
        assert (
            await repository.find_active(correlation_key="cell-1:kpi:bucket-1")
        ).incident_id == results[0].incident_id
        assert (
            await repository.find_active(source_event_id="event-shared")
        ).incident_id == results[0].incident_id

    _run(scenario())


def test_correlation_records_immutable_source_event_provenance() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        first = Incident(
            incident_id="incident-source-a",
            correlation_key="shared-fault",
            source_event_ids=("event-a",),
            trace_id="trace-source-a",
        )
        created = await repository.create_or_correlate(
            first,
            idempotency_key="source-a",
            actor="fault-ingress",
            reason="first delivery",
            trace_id=first.trace_id,
        )
        second = Incident(
            incident_id="incident-source-b",
            correlation_key="shared-fault",
            source_event_ids=("event-b",),
            trace_id="trace-source-b",
        )
        correlated = await repository.create_or_correlate(
            second,
            idempotency_key="source-b",
            actor="test-ingress",
            reason="correlated delivery",
            trace_id=second.trace_id,
        )

        assert correlated == created
        assert correlated.revision == 0
        assert len(await repository.history(created.incident_id)) == 1
        associations = await repository.source_event_associations(
            created.incident_id
        )
        assert [item.source_event_id for item in associations] == [
            "event-a",
            "event-b",
        ]
        assert associations[0].actor == "fault-ingress"
        assert associations[1].actor == "test-ingress"
        assert associations[1].reason == "correlated delivery"
        assert (
            await repository.find_active(source_event_id="event-b")
        ).incident_id == created.incident_id

    _run(scenario())


def test_reopen_reacquires_active_correlation_keys_atomically() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        old = Incident(
            incident_id="incident-old",
            correlation_key="same-active-key",
            source_event_ids=("event-old",),
            trace_id="trace-old",
        )
        created = await _create(repository, old, "create-old")

        # Seed a previously completed legal lifecycle without coupling this
        # repository contract test to every RCA/action fixture.
        closed_payload = created.model_dump(mode="python", round_trip=True)
        closed_payload.update(status=IncidentStatus.CLOSED, revision=8)
        repository._incidents[created.incident_id] = Incident.model_validate(
            closed_payload
        )

        current = Incident(
            incident_id="incident-current",
            correlation_key="same-active-key",
            source_event_ids=("event-current",),
            trace_id="trace-current",
        )
        await _create(repository, current, "create-current")

        with pytest.raises(ActiveIncidentConflictError) as conflict:
            await repository.transition(
                created.incident_id,
                IncidentStatus.REOPENED,
                expected_revision=8,
                idempotency_key="reopen-old",
                actor="resolver-agent",
                reason="new evidence arrived",
                trace_id=created.trace_id,
            )
        assert conflict.value.existing_incident_id == current.incident_id
        assert (await repository.get(created.incident_id)).status is IncidentStatus.CLOSED

    _run(scenario())


def test_settled_incident_retains_global_source_event_ownership() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        old = Incident(
            incident_id="incident-owner",
            correlation_key="old-fault",
            source_event_ids=("event-forever-owned",),
            trace_id="trace-owner",
        )
        created = await _create(repository, old, "create-owner")
        closed_payload = created.model_dump(mode="python", round_trip=True)
        closed_payload.update(status=IncidentStatus.CLOSED, revision=8)
        repository._incidents[created.incident_id] = Incident.model_validate(
            closed_payload
        )

        reuse = Incident(
            incident_id="incident-reuse",
            correlation_key="unrelated-new-fault",
            source_event_ids=("event-forever-owned",),
            trace_id="trace-reuse",
        )
        with pytest.raises(SourceEventOwnershipConflictError) as conflict:
            await repository.create_or_correlate(
                reuse,
                idempotency_key="reuse-source",
                actor="fault-ingress",
                reason="new delivery",
                trace_id=reuse.trace_id,
            )

        assert conflict.value.owner_incident_id == created.incident_id
        assert conflict.value.requested_incident_id == reuse.incident_id
        assert await repository.get(reuse.incident_id) is None
        assert len(await repository.list()) == 1

    _run(scenario())


def test_idempotency_key_is_scoped_by_operation_and_requested_incident() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        incidents = (
            Incident(incident_id="incident-a", trace_id="trace-a"),
            Incident(incident_id="incident-b", trace_id="trace-b"),
        )
        for incident in incidents:
            await repository.create(
                incident,
                idempotency_key="same-client-key",
                actor="detector-agent",
                reason="independent candidate",
                trace_id=incident.trace_id,
            )

        assert len(await repository.list()) == 2
        assert (
            await repository.find_by_idempotency_key(
                "incident-a", "same-client-key", operation="create"
            )
        ).incident_id == "incident-a"
        assert (
            await repository.find_by_idempotency_key(
                "incident-b", "same-client-key", operation="create"
            )
        ).incident_id == "incident-b"

        transitioned = await repository.transition(
            "incident-a",
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="same-client-key",
            actor="supervisor-agent",
            reason="operation-scoped key",
            trace_id="trace-a",
        )
        assert transitioned.revision == 1

    _run(scenario())


def test_naked_create_cannot_bypass_active_correlation_deduplication() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        first = Incident(
            incident_id="incident-a",
            correlation_key="same-active-fault",
            source_event_ids=("source-a",),
            trace_id="trace-a",
        )
        await _create(repository, first, "create-a")

        for second in (
            Incident(
                incident_id="incident-by-key",
                correlation_key="same-active-fault",
                trace_id="trace-key",
            ),
            Incident(
                incident_id="incident-by-source",
                correlation_key="different-key",
                source_event_ids=("source-a",),
                trace_id="trace-source",
            ),
        ):
            with pytest.raises(ActiveIncidentConflictError):
                await _create(repository, second, f"create-{second.incident_id}")

        assert len(await repository.list()) == 1

    _run(scenario())


def test_direct_repository_writes_enforce_privacy_without_echoing_paths() -> None:
    async def scenario() -> None:
        repository = InMemoryIncidentRepository()
        raw_value = "208930000000001"
        unsafe = Incident(
            incident_id="incident-private",
            trace_id="trace-private",
            model_metadata={"imsi": raw_value},
        )
        with pytest.raises(UnsafeIncidentWriteError) as create_error:
            await _create(repository, unsafe, "unsafe-create")
        assert raw_value not in str(create_error.value)
        assert "imsi" not in str(create_error.value).lower()

        sensitive_id = "IMSI:310410000000001"
        with pytest.raises(UnsafeIncidentWriteError) as identifier_error:
            await repository.create(
                Incident(incident_id=sensitive_id, trace_id="safe-trace"),
                idempotency_key="safe-idempotency",
                actor="detector-agent",
                reason="privacy boundary test",
                trace_id="safe-trace",
            )
        assert sensitive_id not in str(identifier_error.value)
        assert "310410000000001" not in str(identifier_error.value)

        safe = _incident("incident-safe")
        created = await _create(repository, safe, "safe-create")
        with pytest.raises(UnsafeIncidentWriteError) as transition_error:
            await repository.transition(
                created.incident_id,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                idempotency_key="unsafe-transition",
                actor="resolver-agent",
                reason="privacy boundary test",
                trace_id=created.trace_id,
                updates={"model_metadata": {"imsi": raw_value}},
            )
        assert raw_value not in str(transition_error.value)
        assert "imsi" not in str(transition_error.value).lower()

    _run(scenario())


def test_injected_clock_owns_incident_and_audit_timestamps() -> None:
    async def scenario() -> None:
        clock_value = [datetime(2030, 1, 1, 12, 0, tzinfo=UTC)]
        repository = InMemoryIncidentRepository(clock=lambda: clock_value[0])
        candidate = Incident(
            incident_id="incident-clock",
            trace_id="trace-clock",
            created_at=clock_value[0] - timedelta(days=1),
            updated_at=clock_value[0] - timedelta(days=1),
        )
        created = await _create(repository, candidate, "clock-create")
        create_event = (await repository.history(created.incident_id))[0]
        assert created.created_at == clock_value[0]
        assert created.updated_at == clock_value[0]
        assert create_event.occurred_at == clock_value[0]

        clock_value[0] += timedelta(minutes=5)
        caller_future = transition_incident(
            created,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            transitioned_at=clock_value[0] + timedelta(days=30),
            now=clock_value[0],
        )
        committed = await _save(
            repository,
            caller_future,
            expected_revision=0,
            idempotency_key="clock-transition",
        )
        transition_event = (await repository.history(created.incident_id))[-1]
        assert committed.updated_at == clock_value[0]
        assert transition_event.occurred_at == clock_value[0]

    _run(scenario())
