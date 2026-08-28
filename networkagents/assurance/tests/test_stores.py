from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from a2a.types import Task, TaskState, TaskStatus

from telco_assurance_agent.stores import (
    DuckDbPendingConfirmationStore,
    DuckDbTaskStore,
    PROCESSING_RECOVERY_RETENTION,
    PendingConfirmationCapacityError,
    PendingConfirmationConflictError,
    PendingConfirmationExpiredError,
    PendingConfirmationRecord,
    initialize_assurance_database,
)


NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _claim(
    store: DuckDbPendingConfirmationStore,
    preview_message_id: str = "preview-1",
    fingerprint: str = "f" * 64,
    *,
    now: datetime = NOW,
    decision: str = "CONFIRM",
) -> PendingConfirmationRecord:
    return asyncio.run(
        store.claim(
            preview_message_id,
            fingerprint,
            candidate_id="candidate-1",
            idempotency_key="confirm-key-1",
            decision=decision,
            now=now,
        )
    )


def _record(challenge_id: str = "challenge-" + "x" * 32) -> PendingConfirmationRecord:
    return PendingConfirmationRecord.create(
        preview_message_id="preview-1",
        request_message_id="scan-1",
        task_id="task-1",
        context_id="context-1",
        workflow_id="workflow-1",
        trace_id="trace-1",
        challenge_id=challenge_id,
        snapshot_sha256="a" * 64,
        candidate_ids=("candidate-1",),
        effective_window_start=NOW - timedelta(hours=1),
        effective_window_end=NOW,
        resource_ids=("lte:enodeb:1:cell:1",),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def test_runtime_stores_require_explicit_bootstrap(tmp_path: Path) -> None:
    database = tmp_path / "runtime.duckdb"
    duckdb.connect(str(database)).close()

    with pytest.raises(RuntimeError, match="not initialized"):
        DuckDbPendingConfirmationStore(database)
    with pytest.raises(RuntimeError, match="not initialized"):
        DuckDbTaskStore(database)

    initialize_assurance_database(database)
    DuckDbPendingConfirmationStore(database)
    DuckDbTaskStore(database)


def test_pending_confirmation_is_persistent_and_challenge_is_hashed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.duckdb"
    initialize_assurance_database(database)
    challenge = "challenge-" + "x" * 32
    first = DuckDbPendingConfirmationStore(database, capacity=4)
    asyncio.run(first.create(_record(challenge)))

    reopened = DuckDbPendingConfirmationStore(database, capacity=4)
    loaded = asyncio.run(reopened.get("preview-1"))
    assert loaded is not None
    assert loaded.challenge_matches(challenge)
    assert challenge not in database.read_bytes().decode("latin-1")


def test_claim_is_linearizable_idempotent_and_expires(tmp_path: Path) -> None:
    database = tmp_path / "runtime.duckdb"
    initialize_assurance_database(database)
    store = DuckDbPendingConfirmationStore(database, capacity=4)
    asyncio.run(store.create(_record()))

    claimed = _claim(store)
    replay = _claim(store)
    assert claimed.state == replay.state == "processing"
    with pytest.raises(PendingConfirmationConflictError):
        _claim(store, fingerprint="e" * 64)

    second = _record("challenge-" + "y" * 32).model_copy(
        update={
            "preview_message_id": "preview-expired",
            "created_at": NOW - timedelta(minutes=20),
            "expires_at": NOW - timedelta(minutes=10),
        }
    )
    asyncio.run(store.create(second))
    with pytest.raises(PendingConfirmationExpiredError):
        _claim(store, "preview-expired", "d" * 64)


def test_task_store_round_trips_alias_json_across_reopen(tmp_path: Path) -> None:
    database = tmp_path / "runtime.duckdb"
    initialize_assurance_database(database)
    task = Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.input_required),
    )

    first = DuckDbTaskStore(database, capacity=4)
    asyncio.run(first.save(task))
    reopened = DuckDbTaskStore(database, capacity=4)
    assert asyncio.run(reopened.get("task-1")) == task
    asyncio.run(reopened.delete("task-1"))
    assert asyncio.run(first.get("task-1")) is None


def test_expired_pending_and_input_required_task_are_reclaimed_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.duckdb"
    initialize_assurance_database(database)
    pending = DuckDbPendingConfirmationStore(database, capacity=1)
    asyncio.run(pending.create(_record()))
    task_one = Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.input_required),
    )
    asyncio.run(
        DuckDbTaskStore(database, capacity=1, clock=lambda: NOW).save(task_one)
    )

    after_expiry = NOW + timedelta(minutes=11)
    task_two = Task(
        id="task-2",
        context_id="context-2",
        status=TaskStatus(state=TaskState.submitted),
    )
    restarted_tasks = DuckDbTaskStore(
        database, capacity=1, clock=lambda: after_expiry
    )
    asyncio.run(restarted_tasks.save(task_two))
    assert asyncio.run(restarted_tasks.get("task-1")) is None
    assert asyncio.run(restarted_tasks.get("task-2")) == task_two
    stale = asyncio.run(pending.get("preview-1"))
    assert stale is not None and stale.state == "expired"

    replacement = _record("challenge-" + "z" * 32).model_copy(
        update={
            "preview_message_id": "preview-2",
            "task_id": "task-2",
            "context_id": "context-2",
            "created_at": after_expiry,
            "expires_at": after_expiry + timedelta(minutes=10),
            "updated_at": after_expiry,
        }
    )
    restarted_pending = DuckDbPendingConfirmationStore(database, capacity=1)
    asyncio.run(restarted_pending.create(replacement))
    assert asyncio.run(restarted_pending.get("preview-1")) is None
    assert asyncio.run(restarted_pending.get("preview-2")) == replacement


def test_abandoned_processing_without_incident_write_releases_capacity_after_expiry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.duckdb"
    initialize_assurance_database(database)
    pending = DuckDbPendingConfirmationStore(database, capacity=1)
    asyncio.run(pending.create(_record()))
    _claim(pending)
    task_one = Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.input_required),
    )
    asyncio.run(DuckDbTaskStore(database, capacity=1, clock=lambda: NOW).save(task_one))

    after_expiry = NOW + timedelta(minutes=11)
    task_two = Task(
        id="task-2",
        context_id="context-2",
        status=TaskStatus(state=TaskState.submitted),
    )
    restarted_tasks = DuckDbTaskStore(
        database, capacity=1, clock=lambda: after_expiry
    )
    asyncio.run(restarted_tasks.save(task_two))
    assert asyncio.run(restarted_tasks.get("task-1")) is None
    stale = asyncio.run(pending.get("preview-1"))
    assert stale is not None and stale.state == "expired"

    replacement = _record("challenge-" + "z" * 32).model_copy(
        update={
            "preview_message_id": "preview-2",
            "task_id": "task-2",
            "context_id": "context-2",
            "created_at": after_expiry,
            "expires_at": after_expiry + timedelta(minutes=10),
            "updated_at": after_expiry,
        }
    )
    restarted_pending = DuckDbPendingConfirmationStore(database, capacity=1)
    asyncio.run(restarted_pending.create(replacement))
    assert asyncio.run(restarted_pending.get("preview-2")) == replacement


def test_durable_processing_replay_is_retained_then_bounded_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.duckdb"
    initialize_assurance_database(database)
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "CREATE TABLE canonical_incident_idempotency ("
            "operation VARCHAR NOT NULL, requested_incident_id VARCHAR NOT NULL, "
            "idempotency_key VARCHAR NOT NULL, request_fingerprint VARCHAR NOT NULL, "
            "result_payload JSON NOT NULL, created_at TIMESTAMPTZ NOT NULL, "
            "PRIMARY KEY (operation, requested_incident_id, idempotency_key))"
        )
        connection.execute(
            "INSERT INTO canonical_incident_idempotency VALUES (?, ?, ?, ?, ?, ?)",
            [
                "create_or_correlate",
                "candidate-1",
                "confirm-key-1",
                "1" * 64,
                "{}",
                NOW,
            ],
        )
    finally:
        connection.close()

    pending = DuckDbPendingConfirmationStore(database, capacity=1)
    asyncio.run(pending.create(_record()))
    _claim(pending)
    task_one = Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.input_required),
    )
    asyncio.run(DuckDbTaskStore(database, capacity=1, clock=lambda: NOW).save(task_one))

    within_replay_window = NOW + timedelta(minutes=11)
    task_two = Task(
        id="task-2",
        context_id="context-2",
        status=TaskStatus(state=TaskState.submitted),
    )
    with pytest.raises(RuntimeError, match="capacity reached"):
        asyncio.run(
            DuckDbTaskStore(
                database, capacity=1, clock=lambda: within_replay_window
            ).save(task_two)
        )
    replacement = _record("challenge-" + "z" * 32).model_copy(
        update={
            "preview_message_id": "preview-2",
            "task_id": "task-2",
            "context_id": "context-2",
            "created_at": within_replay_window,
            "expires_at": within_replay_window + timedelta(minutes=10),
            "updated_at": within_replay_window,
        }
    )
    with pytest.raises(PendingConfirmationCapacityError):
        asyncio.run(
            DuckDbPendingConfirmationStore(database, capacity=1).create(replacement)
        )

    after_replay_window = (
        NOW + timedelta(minutes=10) + PROCESSING_RECOVERY_RETENTION + timedelta(seconds=1)
    )
    restarted_tasks = DuckDbTaskStore(
        database, capacity=1, clock=lambda: after_replay_window
    )
    asyncio.run(restarted_tasks.save(task_two))
    assert asyncio.run(restarted_tasks.get("task-1")) is None
    stale = asyncio.run(pending.get("preview-1"))
    assert stale is not None and stale.state == "expired"

    replacement = replacement.model_copy(
        update={
            "created_at": after_replay_window,
            "expires_at": after_replay_window + timedelta(minutes=10),
            "updated_at": after_replay_window,
        }
    )
    restarted_pending = DuckDbPendingConfirmationStore(database, capacity=1)
    asyncio.run(restarted_pending.create(replacement))
    assert asyncio.run(restarted_pending.get("preview-2")) == replacement
