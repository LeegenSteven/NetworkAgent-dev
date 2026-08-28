from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from telco_domain.models import Incident, IncidentStatus
from telco_domain.ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentAlreadyExistsError,
    IncidentRepository,
    RevisionConflictError,
    UnsafeIncidentWriteError,
)
from telco_domain.state_machine import transition_incident
from telco_local.incident_repository import DuckDbIncidentRepository


def _run(coroutine):
    return asyncio.run(coroutine)


def _incident(incident_id: str = "incident-1", **updates) -> Incident:
    values = {
        "incident_id": incident_id,
        "trace_id": f"trace-{incident_id}",
    }
    values.update(updates)
    return Incident(**values)


async def _create(repository, incident: Incident, key: str = "create") -> Incident:
    return await repository.create(
        incident,
        idempotency_key=key,
        actor="detector-agent",
        reason="confirmed LTE KPI candidate",
        trace_id=incident.trace_id,
    )


def test_repository_satisfies_protocol_and_persists_across_instances(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        assert isinstance(repository, IncidentRepository)
        created = await _create(repository, _incident("incident-persist"))

        reopened = DuckDbIncidentRepository(initialized_config.database_path)
        assert await reopened.get(created.incident_id) == created
        assert tuple(await reopened.list()) == (created,)
        assert tuple(await reopened.history(created.incident_id))[0].revision == 0

    _run(scenario())


def test_create_uses_trusted_clock_and_exact_retry_is_single_commit(
    initialized_config,
) -> None:
    async def scenario() -> None:
        trusted = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
        repository = DuckDbIncidentRepository(
            initialized_config,
            clock=lambda: trusted,
        )
        caller_time = trusted - timedelta(days=3)
        candidate = _incident(
            created_at=caller_time,
            updated_at=caller_time,
        )
        first = await _create(repository, candidate)
        replay = await _create(repository, candidate)

        assert replay == first
        assert first.created_at == trusted
        assert first.updated_at == trusted
        assert len(await repository.list()) == 1
        history = tuple(await repository.history(first.incident_id))
        assert len(history) == 1
        assert history[0].occurred_at == trusted
        assert (
            await repository.find_by_idempotency_key(
                first.incident_id, "create", operation="create"
            )
            == first
        )

    _run(scenario())


def test_create_rejects_changed_replay_duplicate_id_and_active_conflict(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        first = _incident(
            "incident-a",
            correlation_key="lte:cell-1:erab:bucket-1",
            source_event_ids=("source-shared",),
        )
        await _create(repository, first, "key-a")

        with pytest.raises(IdempotencyConflictError):
            await _create(
                repository,
                _incident(
                    "incident-a",
                    title="changed request",
                    correlation_key=first.correlation_key,
                    source_event_ids=first.source_event_ids,
                ),
                "key-a",
            )
        with pytest.raises(IncidentAlreadyExistsError):
            await _create(repository, first, "new-key")
        with pytest.raises(ActiveIncidentConflictError):
            await _create(
                repository,
                _incident("incident-b", correlation_key=first.correlation_key),
                "key-b",
            )
        with pytest.raises(ActiveIncidentConflictError):
            await _create(
                repository,
                _incident(
                    "incident-c",
                    correlation_key="different",
                    source_event_ids=("source-shared",),
                ),
                "key-c",
            )

    _run(scenario())


def test_save_is_state_machine_cas_and_replay_precedes_stale_check(
    initialized_config,
) -> None:
    async def scenario() -> None:
        clock_value = [datetime(2030, 1, 1, tzinfo=UTC)]
        repository = DuckDbIncidentRepository(
            initialized_config,
            clock=lambda: clock_value[0],
        )
        original = await _create(repository, _incident())
        clock_value[0] += timedelta(minutes=1)
        candidate = transition_incident(
            original,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            transitioned_at=clock_value[0] + timedelta(days=10),
            updates={"title": "triaged"},
        )
        committed = await repository.save(
            candidate,
            expected_revision=0,
            idempotency_key="save",
            actor="resolver-agent",
            reason="triage",
            trace_id=original.trace_id,
        )
        replay = await repository.compare_and_swap(
            candidate,
            expected_revision=0,
            idempotency_key="save",
            actor="resolver-agent",
            reason="triage",
            trace_id=original.trace_id,
        )

        assert replay == committed
        assert committed.updated_at == clock_value[0]
        assert committed.revision == 1
        assert [event.revision for event in await repository.history(original.incident_id)] == [0, 1]

        stale = transition_incident(
            original,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            updates={"title": "stale"},
        )
        with pytest.raises(RevisionConflictError):
            await repository.save(
                stale,
                expected_revision=0,
                idempotency_key="stale",
                actor="resolver-agent",
                reason="stale writer",
                trace_id=original.trace_id,
            )

    _run(scenario())


def test_atomic_transition_runs_domain_guards_and_rolls_back_failures(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        original = await _create(repository, _incident())

        with pytest.raises(ValueError, match="cannot transition"):
            await repository.transition(
                original.incident_id,
                IncidentStatus.CLOSED,
                expected_revision=0,
                idempotency_key="illegal",
                actor="resolver-agent",
                reason="illegal shortcut",
                trace_id=original.trace_id,
            )
        with pytest.raises(ValueError, match="duplicate_of"):
            await repository.transition(
                original.incident_id,
                IncidentStatus.DUPLICATE,
                expected_revision=0,
                idempotency_key="unguarded",
                actor="resolver-agent",
                reason="missing duplicate target",
                trace_id=original.trace_id,
            )

        assert await repository.get(original.incident_id) == original
        assert len(await repository.history(original.incident_id)) == 1
        with duckdb.connect(str(initialized_config.database_path), read_only=True) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM canonical_incident_idempotency"
            ).fetchone()[0] == 1

    _run(scenario())


def test_create_or_correlate_is_atomic_and_scopes_idempotency(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        first = _incident(
            "incident-a",
            correlation_key="same-fault",
            source_event_ids=("event-a",),
        )
        second = _incident(
            "incident-b",
            correlation_key="same-fault",
            source_event_ids=("event-b",),
        )
        results = await asyncio.gather(
            repository.create_or_correlate(
                first,
                idempotency_key="same-client-key",
                actor="detector-agent",
                reason="candidate a",
                trace_id=first.trace_id,
            ),
            repository.create_or_correlate(
                second,
                idempotency_key="same-client-key",
                actor="detector-agent",
                reason="candidate b",
                trace_id=second.trace_id,
            ),
        )
        assert results[0].incident_id == results[1].incident_id
        assert len(await repository.list()) == 1
        assert (
            await repository.find_active(correlation_key="same-fault")
        ).incident_id == results[0].incident_id
        assert (
            await repository.find_active(source_event_id="event-a")
        ).incident_id == results[0].incident_id
        assert (
            await repository.find_by_idempotency_key(
                "incident-a", "same-client-key", operation="create_or_correlate"
            )
        ).incident_id == results[0].incident_id
        assert (
            await repository.find_by_idempotency_key(
                "incident-b", "same-client-key", operation="create_or_correlate"
            )
        ).incident_id == results[0].incident_id

    _run(scenario())


def test_correlation_persists_source_event_associations_without_revision_change(
    initialized_config,
) -> None:
    async def scenario() -> None:
        clock_value = [datetime(2030, 1, 1, tzinfo=UTC)]
        repository = DuckDbIncidentRepository(
            initialized_config, clock=lambda: clock_value[0]
        )
        first = _incident(
            "incident-a",
            correlation_key="same-fault",
            source_event_ids=("event-a",),
        )
        created = await repository.create_or_correlate(
            first,
            idempotency_key="create-a",
            actor="detector-agent",
            reason="first candidate",
            trace_id=first.trace_id,
        )
        clock_value[0] += timedelta(minutes=1)
        second = _incident(
            "incident-b",
            correlation_key="same-fault",
            source_event_ids=("event-b",),
        )
        correlated = await repository.create_or_correlate(
            second,
            idempotency_key="correlate-b",
            actor="detector-agent",
            reason="same fault, new source event",
            trace_id=second.trace_id,
        )
        with duckdb.connect(
            str(initialized_config.database_path), read_only=True
        ) as connection:
            association_before_replay = connection.execute(
                """
                SELECT registered_at, idempotency_key, actor, reason, trace_id
                FROM canonical_incident_source_events
                WHERE incident_id = ? AND source_event_id = ?
                """,
                [created.incident_id, "event-b"],
            ).fetchone()
        assert association_before_replay == (
            clock_value[0],
            "correlate-b",
            "detector-agent",
            "same fault, new source event",
            second.trace_id,
        )

        clock_value[0] += timedelta(minutes=1)
        replay = await repository.create_or_correlate(
            second,
            idempotency_key="correlate-b",
            actor="detector-agent",
            reason="same fault, new source event",
            trace_id=second.trace_id,
        )
        with duckdb.connect(
            str(initialized_config.database_path), read_only=True
        ) as connection:
            association_after_replay = connection.execute(
                """
                SELECT registered_at, idempotency_key, actor, reason, trace_id
                FROM canonical_incident_source_events
                WHERE incident_id = ? AND source_event_id = ?
                """,
                [created.incident_id, "event-b"],
            ).fetchone()
        assert association_after_replay == association_before_replay

        assert correlated == replay == created
        assert correlated.revision == 0
        assert len(await repository.history(created.incident_id)) == 1
        assert (
            await repository.find_active(source_event_id="event-b")
        ).incident_id == created.incident_id

        changed_correlation = _incident(
            "incident-c",
            correlation_key="different-fault",
            source_event_ids=("event-b",),
        )
        third = await repository.create_or_correlate(
            changed_correlation,
            idempotency_key="correlate-c",
            actor="detector-agent",
            reason="event identity is authoritative",
            trace_id=changed_correlation.trace_id,
        )
        assert third.incident_id == created.incident_id
        assert len(await repository.list()) == 1
        assert len(await repository.history(created.incident_id)) == 1
        assert (
            await repository.find_by_idempotency_key(
                "incident-c",
                "correlate-c",
                operation="create_or_correlate",
            )
        ).incident_id == created.incident_id

    _run(scenario())


def test_source_event_provenance_columns_migrate_before_new_writes(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        first = _incident(
            "migration-a",
            correlation_key="migration-fault",
            source_event_ids=("migration-event-a",),
        )
        created = await repository.create_or_correlate(
            first,
            idempotency_key="migration-create",
            actor="detector-agent",
            reason="pre-migration association",
            trace_id=first.trace_id,
        )

        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.execute(
                "DROP INDEX canonical_incident_source_events_source_idx"
            )
            for column in ("idempotency_key", "actor", "reason", "trace_id"):
                connection.execute(
                    f"""ALTER TABLE canonical_incident_source_events
                         DROP COLUMN {column}"""
                )

        migrated = DuckDbIncidentRepository(initialized_config)
        second = _incident(
            "migration-b",
            correlation_key="migration-fault",
            source_event_ids=("migration-event-b",),
        )
        result = await migrated.create_or_correlate(
            second,
            idempotency_key="migration-correlate",
            actor="detector-agent",
            reason="post-migration association",
            trace_id=second.trace_id,
        )
        assert result.incident_id == created.incident_id

        with duckdb.connect(
            str(initialized_config.database_path), read_only=True
        ) as connection:
            assert connection.execute(
                """
                SELECT idempotency_key, actor, reason, trace_id
                FROM canonical_incident_source_events
                WHERE source_event_id = 'migration-event-b'
                """
            ).fetchone() == (
                "migration-correlate",
                "detector-agent",
                "post-migration association",
                second.trace_id,
            )

    _run(scenario())


def test_repository_enforces_privacy_and_does_not_echo_sensitive_values(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        raw_value = "208930000000001"
        unsafe = _incident(model_metadata={"imsi": raw_value})
        with pytest.raises(UnsafeIncidentWriteError) as error:
            await _create(repository, unsafe)
        assert raw_value not in str(error.value)
        assert "imsi" not in str(error.value).lower()
        assert await repository.list() == ()

        safe = await _create(repository, _incident("safe"), "safe-create")
        with pytest.raises(UnsafeIncidentWriteError):
            await repository.transition(
                safe.incident_id,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                idempotency_key="unsafe-update",
                actor="resolver-agent",
                reason="privacy test",
                trace_id=safe.trace_id,
                updates={"model_metadata": {"msisdn": raw_value}},
            )
        assert (await repository.get(safe.incident_id)).revision == 0

    _run(scenario())


@pytest.mark.parametrize(
    "field_name",
    ("idempotency_key", "actor", "reason", "trace_id"),
)
def test_every_write_metadata_field_has_privacy_and_non_echo_guards(
    initialized_config,
    field_name: str,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        sensitive = "IMSI:310410000000001"
        incident = _incident("metadata-privacy")
        metadata = {
            "idempotency_key": "safe-key",
            "actor": "detector-agent",
            "reason": "privacy boundary test",
            "trace_id": incident.trace_id,
        }
        metadata[field_name] = sensitive

        with pytest.raises(UnsafeIncidentWriteError) as error:
            await repository.create(incident, **metadata)

        assert sensitive not in str(error.value)
        assert "310410000000001" not in str(error.value)
        assert await repository.list() == ()

    _run(scenario())


def test_transition_rejects_sensitive_incident_id_without_echo(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        sensitive = "IMSI:310410000000001"
        with pytest.raises(UnsafeIncidentWriteError) as error:
            await repository.transition(
                sensitive,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                idempotency_key="safe-key",
                actor="resolver-agent",
                reason="privacy boundary test",
                trace_id="safe-trace",
            )
        assert sensitive not in str(error.value)
        assert "310410000000001" not in str(error.value)

    _run(scenario())


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("idempotency_key", "k" * 257),
        ("actor", "a" * 257),
        ("reason", "r" * 4_097),
        ("trace_id", "t" * 257),
    ),
)
def test_every_write_metadata_field_has_a_length_budget(
    initialized_config,
    field_name: str,
    value: str,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        incident = _incident("metadata-length")
        metadata = {
            "idempotency_key": "safe-key",
            "actor": "detector-agent",
            "reason": "length boundary test",
            "trace_id": incident.trace_id,
        }
        metadata[field_name] = value

        with pytest.raises(ValueError) as error:
            await repository.create(incident, **metadata)
        assert value not in str(error.value)
        assert await repository.list() == ()

    _run(scenario())


def test_incident_persistence_matches_contract_size_and_depth_budgets(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)

        oversized = _incident(
            "oversized",
            model_metadata={"summary": "x" * 256_000},
        )
        with pytest.raises(UnsafeIncidentWriteError, match="size"):
            await _create(repository, oversized, "oversized-create")

        nested: dict[str, object] = {"leaf": "safe"}
        for index in range(30):
            nested = {f"level_{index}": nested}
        too_deep = _incident("too-deep", model_metadata=nested)
        with pytest.raises(UnsafeIncidentWriteError, match="depth"):
            await _create(repository, too_deep, "deep-create")

        assert await repository.list() == ()
        with duckdb.connect(
            str(initialized_config.database_path), read_only=True
        ) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM canonical_incident_idempotency"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM canonical_incident_audit"
            ).fetchone()[0] == 0

    _run(scenario())


def test_query_validation_and_detached_results(initialized_config) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        created = await _create(
            repository,
            _incident("detached", model_metadata={"nested": {"value": "safe"}}),
        )
        fetched = await repository.get(created.incident_id)
        assert fetched is not None
        fetched.model_metadata["nested"]["value"] = "caller mutation"
        assert (await repository.get(created.incident_id)).model_metadata["nested"]["value"] == "safe"

        with pytest.raises(ValueError, match="required"):
            await repository.find_active()
        with pytest.raises(ValueError, match="limit"):
            await repository.list(limit=0)
        with pytest.raises(ValueError, match="offset"):
            await repository.list(offset=-1)

    _run(scenario())


def test_create_validation_list_filter_and_pagination(initialized_config) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        invalid_payload = _incident("invalid").model_dump(
            mode="python", round_trip=True
        )
        invalid_payload["revision"] = 1
        with pytest.raises(ValueError, match="revision 0"):
            await _create(
                repository,
                Incident.model_validate(invalid_payload),
                "invalid-create",
            )

        incident_b = await _create(repository, _incident("incident-b"), "create-b")
        incident_a = await _create(repository, _incident("incident-a"), "create-a")
        triaged = await repository.transition(
            incident_a.incident_id,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="triage-a",
            actor="resolver-agent",
            reason="triage",
            trace_id=incident_a.trace_id,
        )
        assert [item.incident_id for item in await repository.list()] == [
            "incident-a",
            "incident-b",
        ]
        assert await repository.list(status=IncidentStatus.TRIAGED) == (triaged,)
        assert await repository.list(limit=1, offset=1) == (incident_b,)

    _run(scenario())


def test_changed_save_replay_and_crafted_successors_are_rejected(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        original = await _create(repository, _incident())
        candidate = transition_incident(
            original,
            IncidentStatus.TRIAGED,
            expected_revision=0,
            updates={"title": "first"},
        )
        await repository.save(
            candidate,
            expected_revision=0,
            idempotency_key="save",
            actor="resolver-agent",
            reason="triage",
            trace_id=original.trace_id,
        )

        changed_payload = candidate.model_dump(mode="python", round_trip=True)
        changed_payload["title"] = "changed replay"
        with pytest.raises(IdempotencyConflictError):
            await repository.save(
                Incident.model_validate(changed_payload),
                expected_revision=0,
                idempotency_key="save",
                actor="resolver-agent",
                reason="triage",
                trace_id=original.trace_id,
            )

        other_repository = DuckDbIncidentRepository(initialized_config)
        other = await _create(other_repository, _incident("crafted"), "crafted-create")
        for status, message in (
            (IncidentStatus.CLOSED, "cannot transition"),
            (IncidentStatus.DUPLICATE, "duplicate_of"),
        ):
            payload = other.model_dump(mode="python", round_trip=True)
            payload.update(status=status, revision=1)
            with pytest.raises(ValueError, match=message):
                await other_repository.save(
                    Incident.model_validate(payload),
                    expected_revision=0,
                    idempotency_key=f"crafted-{status.value}",
                    actor="resolver-agent",
                    reason="crafted successor",
                    trace_id=other.trace_id,
                )
        assert (await other_repository.get(other.incident_id)).revision == 0

    _run(scenario())


def test_concurrent_cas_allows_one_writer_and_metadata_is_mandatory(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        original = await _create(repository, _incident())
        candidates = tuple(
            transition_incident(
                original,
                IncidentStatus.TRIAGED,
                expected_revision=0,
                updates={"title": title},
            )
            for title in ("writer-a", "writer-b")
        )
        results = await asyncio.gather(
            repository.compare_and_swap(
                candidates[0],
                expected_revision=0,
                idempotency_key="writer-a",
                actor="resolver-agent",
                reason="concurrent a",
                trace_id=original.trace_id,
            ),
            repository.compare_and_swap(
                candidates[1],
                expected_revision=0,
                idempotency_key="writer-b",
                actor="resolver-agent",
                reason="concurrent b",
                trace_id=original.trace_id,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, Incident) for item in results) == 1
        assert sum(isinstance(item, RevisionConflictError) for item in results) == 1
        assert (await repository.get(original.incident_id)).revision == 1

        with pytest.raises(ValueError, match="actor"):
            await repository.transition(
                original.incident_id,
                IncidentStatus.INVESTIGATING,
                expected_revision=1,
                idempotency_key="missing-actor",
                actor="",
                reason="investigate",
                trace_id=original.trace_id,
            )

    _run(scenario())


def test_idempotency_is_scoped_by_operation_and_requested_incident(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbIncidentRepository(initialized_config)
        for incident_id in ("incident-a", "incident-b"):
            incident = _incident(incident_id)
            await repository.create(
                incident,
                idempotency_key="shared-key",
                actor="detector-agent",
                reason="independent candidate",
                trace_id=incident.trace_id,
            )

        transitioned = await repository.transition(
            "incident-a",
            IncidentStatus.TRIAGED,
            expected_revision=0,
            idempotency_key="shared-key",
            actor="resolver-agent",
            reason="operation-scoped transition",
            trace_id="trace-incident-a",
        )
        assert transitioned.revision == 1
        assert (
            await repository.find_by_idempotency_key(
                "incident-a", "shared-key", operation="create"
            )
        ).incident_id == "incident-a"
        assert (
            await repository.find_by_idempotency_key(
                "incident-b", "shared-key", operation="create"
            )
        ).incident_id == "incident-b"

    _run(scenario())
