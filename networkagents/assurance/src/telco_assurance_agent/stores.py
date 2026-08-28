"""Durable, bounded stores for Assurance A2A tasks and confirmations."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import duckdb
from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types import Task
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from telco_domain import SensitiveDataError, assert_model_safe


ASSURANCE_SCHEMA_VERSION = "1.1"
_MIGRATABLE_SCHEMA_VERSIONS = frozenset({"1.0"})
DEFAULT_PENDING_CAPACITY = 1_000
DEFAULT_TASK_CAPACITY = 1_000
MAX_TASK_JSON_BYTES = 1_048_576
PROCESSING_RECOVERY_RETENTION = timedelta(minutes=15)
StoreClock = Callable[[], datetime]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assurance_schema_metadata (
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS assurance_pending_confirmations (
    preview_message_id VARCHAR PRIMARY KEY,
    request_message_id VARCHAR NOT NULL,
    task_id VARCHAR NOT NULL,
    context_id VARCHAR NOT NULL,
    workflow_id VARCHAR NOT NULL,
    trace_id VARCHAR NOT NULL,
    challenge_sha256 VARCHAR NOT NULL,
    snapshot_sha256 VARCHAR NOT NULL,
    candidate_ids JSON NOT NULL,
    effective_window_start TIMESTAMPTZ NOT NULL,
    effective_window_end TIMESTAMPTZ NOT NULL,
    resource_ids JSON NOT NULL,
    state VARCHAR NOT NULL,
    confirmation_fingerprint VARCHAR,
    confirmation_candidate_id VARCHAR,
    confirmation_idempotency_key VARCHAR,
    confirmation_decision VARCHAR,
    result_payload JSON,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE assurance_pending_confirmations
    ADD COLUMN IF NOT EXISTS confirmation_candidate_id VARCHAR;
ALTER TABLE assurance_pending_confirmations
    ADD COLUMN IF NOT EXISTS confirmation_idempotency_key VARCHAR;
ALTER TABLE assurance_pending_confirmations
    ADD COLUMN IF NOT EXISTS confirmation_decision VARCHAR;

CREATE INDEX IF NOT EXISTS assurance_pending_task_idx
    ON assurance_pending_confirmations(task_id, context_id);

CREATE TABLE IF NOT EXISTS assurance_a2a_tasks (
    task_id VARCHAR PRIMARY KEY,
    context_id VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    task_json JSON NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS assurance_a2a_tasks_context_idx
    ON assurance_a2a_tasks(context_id);
"""

_REQUIRED_TABLES = frozenset(
    {
        "assurance_schema_metadata",
        "assurance_pending_confirmations",
        "assurance_a2a_tasks",
    }
)

_LOCK_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


def _database_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _path_lock(path: Path) -> threading.RLock:
    with _LOCK_GUARD:
        return _PATH_LOCKS.setdefault(path, threading.RLock())


def _connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=read_only)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must use UTC")
    return value.astimezone(UTC)


def _json_object(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise RuntimeError("stored result payload is invalid")
    return cast(dict[str, Any], parsed)


def _json_tuple(value: object) -> tuple[str, ...]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise RuntimeError("stored identifier list is invalid")
    return tuple(parsed)


def _require_initialized(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError("Assurance database is not initialized")
    try:
        connection = _connect(path, read_only=True)
        try:
            rows = connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
            tables = {str(row[0]) for row in rows}
            if not _REQUIRED_TABLES.issubset(tables):
                raise RuntimeError("Assurance database is not initialized")
            row = connection.execute(
                "SELECT value FROM assurance_schema_metadata "
                "WHERE key = 'schema_version'"
            ).fetchone()
            if row is None or str(row[0]) != ASSURANCE_SCHEMA_VERSION:
                raise RuntimeError("unsupported Assurance database schema")
        finally:
            connection.close()
    except duckdb.Error:
        raise RuntimeError("Assurance database is not initialized") from None


def _reconcile_expired_confirmations(
    connection: duckdb.DuckDBPyConnection, *, now: datetime
) -> None:
    """Bound abandoned claims while preserving a finite durable replay window."""

    connection.execute(
        "UPDATE assurance_pending_confirmations SET state = 'expired', "
        "updated_at = ? WHERE state = 'pending' AND expires_at <= ?",
        [now, now],
    )
    has_incident_idempotency = (
        connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'main' "
            "AND table_name = 'canonical_incident_idempotency'"
        ).fetchone()
        is not None
    )
    if has_incident_idempotency:
        # A claimed CONFIRM with a durable idempotency row may have crashed
        # after the only Incident write but before finishing the A2A Task.
        # Keep exactly those records recoverable for a bounded grace period.
        connection.execute(
            "UPDATE assurance_pending_confirmations AS pending "
            "SET state = 'expired', updated_at = ? "
            "WHERE pending.state = 'processing' AND pending.expires_at <= ? "
            "AND NOT ("
            "COALESCE(pending.confirmation_decision, '') = 'CONFIRM' "
            "AND pending.confirmation_candidate_id IS NOT NULL "
            "AND pending.confirmation_idempotency_key IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM canonical_incident_idempotency AS durable "
            "WHERE durable.operation = 'create_or_correlate' "
            "AND durable.requested_incident_id = pending.confirmation_candidate_id "
            "AND durable.idempotency_key = pending.confirmation_idempotency_key))",
            [now, now],
        )
    else:
        connection.execute(
            "UPDATE assurance_pending_confirmations SET state = 'expired', "
            "updated_at = ? WHERE state = 'processing' AND expires_at <= ?",
            [now, now],
        )

    recovery_cutoff = now - PROCESSING_RECOVERY_RETENTION
    connection.execute(
        "UPDATE assurance_pending_confirmations SET state = 'expired', "
        "updated_at = ? WHERE state = 'processing' AND expires_at <= ?",
        [now, recovery_cutoff],
    )


def initialize_assurance_database(
    database_path: str | Path, *, reset: bool = False
) -> None:
    """Create only the Assurance runtime tables during an explicit init step."""

    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        connection = _connect(path)
        try:
            connection.execute("BEGIN TRANSACTION")
            if reset:
                connection.execute(
                    "DROP TABLE IF EXISTS assurance_pending_confirmations"
                )
                connection.execute("DROP TABLE IF EXISTS assurance_a2a_tasks")
                connection.execute("DROP TABLE IF EXISTS assurance_schema_metadata")
            connection.execute(_SCHEMA)
            row = connection.execute(
                "SELECT value FROM assurance_schema_metadata "
                "WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO assurance_schema_metadata VALUES "
                    "('schema_version', ?)",
                    [ASSURANCE_SCHEMA_VERSION],
                )
            elif str(row[0]) in _MIGRATABLE_SCHEMA_VERSIONS:
                connection.execute(
                    "UPDATE assurance_schema_metadata SET value = ? "
                    "WHERE key = 'schema_version'",
                    [ASSURANCE_SCHEMA_VERSION],
                )
            elif str(row[0]) != ASSURANCE_SCHEMA_VERSION:
                raise RuntimeError("unsupported Assurance database schema")
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise
        finally:
            connection.close()


# A semantic alias for callers that call the phase an A2A-store bootstrap.
initialize_assurance_store = initialize_assurance_database


PendingState = Literal[
    "pending", "processing", "completed", "rejected", "cancelled", "expired"
]


class PendingConfirmationError(RuntimeError):
    """Base class for safe pending-confirmation failures."""


class PendingConfirmationNotFoundError(PendingConfirmationError):
    pass


class PendingConfirmationConflictError(PendingConfirmationError):
    pass


class PendingConfirmationExpiredError(PendingConfirmationError):
    pass


class PendingConfirmationCapacityError(PendingConfirmationError):
    pass


class PendingConfirmationRecord(BaseModel):
    """Server-owned confirmation binding; the plaintext challenge is never stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preview_message_id: str = Field(min_length=1, max_length=256)
    request_message_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=256)
    context_id: str = Field(min_length=1, max_length=256)
    workflow_id: str = Field(min_length=1, max_length=256)
    trace_id: str = Field(min_length=1, max_length=256)
    challenge_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    effective_window_start: AwareDatetime
    effective_window_end: AwareDatetime
    resource_ids: tuple[str, ...] = Field(default=(), max_length=100)
    state: PendingState = "pending"
    confirmation_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    confirmation_candidate_id: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    confirmation_idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    confirmation_decision: Literal["CONFIRM", "REJECT"] | None = None
    result_payload: dict[str, Any] | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator(
        "effective_window_start",
        "effective_window_end",
        "created_at",
        "expires_at",
        "updated_at",
    )
    @classmethod
    def _times_are_utc(cls, value: datetime, info: Any) -> datetime:
        return _utc(value, info.field_name)

    @classmethod
    def create(
        cls,
        *,
        challenge_id: str,
        created_at: datetime,
        **values: Any,
    ) -> "PendingConfirmationRecord":
        normalized_created = _utc(created_at, "created_at")
        return cls(
            **values,
            challenge_sha256=hashlib.sha256(challenge_id.encode("utf-8")).hexdigest(),
            state="pending",
            created_at=normalized_created,
            updated_at=normalized_created,
        )

    def challenge_matches(self, challenge_id: str) -> bool:
        supplied = hashlib.sha256(challenge_id.encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.challenge_sha256, supplied)


class DuckDbPendingConfirmationStore:
    """Atomic confirmation claims persisted in the Local Profile DuckDB file."""

    def __init__(self, database_path: str | Path, *, capacity: int = DEFAULT_PENDING_CAPACITY):
        if not 1 <= capacity <= 100_000:
            raise ValueError("pending confirmation capacity must be between 1 and 100000")
        self.database_path = _database_path(database_path)
        self.capacity = capacity
        self._lock = _path_lock(self.database_path)
        _require_initialized(self.database_path)

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> PendingConfirmationRecord:
        # DuckDB renders TIMESTAMPTZ values in the process timezone; normalize
        # the same instant back to the canonical UTC wire representation.
        stored_times = {
            index: cast(datetime, row[index]).astimezone(UTC)
            for index in (9, 10, 18, 19, 20)
        }
        return PendingConfirmationRecord(
            preview_message_id=str(row[0]),
            request_message_id=str(row[1]),
            task_id=str(row[2]),
            context_id=str(row[3]),
            workflow_id=str(row[4]),
            trace_id=str(row[5]),
            challenge_sha256=str(row[6]),
            snapshot_sha256=str(row[7]),
            candidate_ids=_json_tuple(row[8]),
            effective_window_start=stored_times[9],
            effective_window_end=stored_times[10],
            resource_ids=_json_tuple(row[11]),
            state=str(row[12]),
            confirmation_fingerprint=(None if row[13] is None else str(row[13])),
            confirmation_candidate_id=(
                None if row[14] is None else str(row[14])
            ),
            confirmation_idempotency_key=(
                None if row[15] is None else str(row[15])
            ),
            confirmation_decision=(None if row[16] is None else str(row[16])),
            result_payload=_json_object(row[17]),
            created_at=stored_times[18],
            expires_at=stored_times[19],
            updated_at=stored_times[20],
        )

    @staticmethod
    def _select(connection: duckdb.DuckDBPyConnection, preview_message_id: str):
        return connection.execute(
            "SELECT preview_message_id, request_message_id, task_id, context_id, "
            "workflow_id, trace_id, challenge_sha256, snapshot_sha256, candidate_ids, "
            "effective_window_start, effective_window_end, resource_ids, state, "
            "confirmation_fingerprint, confirmation_candidate_id, "
            "confirmation_idempotency_key, confirmation_decision, result_payload, "
            "created_at, expires_at, updated_at "
            "FROM assurance_pending_confirmations WHERE preview_message_id = ?",
            [preview_message_id],
        ).fetchone()

    async def create(self, record: PendingConfirmationRecord) -> None:
        if record.state != "pending" or any(
            value is not None
            for value in (
                record.confirmation_fingerprint,
                record.confirmation_candidate_id,
                record.confirmation_idempotency_key,
                record.confirmation_decision,
            )
        ):
            raise ValueError("new confirmation records must be pending and unclaimed")
        if record.expires_at <= record.created_at:
            raise ValueError("confirmation expiry must follow creation")
        try:
            assert_model_safe(record)
        except SensitiveDataError:
            raise PendingConfirmationError("pending confirmation rejected") from None
        with self._lock:
            connection = _connect(self.database_path)
            try:
                connection.execute("BEGIN TRANSACTION")
                _reconcile_expired_confirmations(
                    connection, now=record.created_at
                )
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assurance_pending_confirmations"
                    ).fetchone()[0]
                )
                if count >= self.capacity:
                    connection.execute(
                        "DELETE FROM assurance_pending_confirmations WHERE preview_message_id IN ("
                        "SELECT preview_message_id FROM assurance_pending_confirmations "
                        "WHERE state IN ('completed','rejected','cancelled','expired') "
                        "ORDER BY updated_at LIMIT ?)",
                        [count - self.capacity + 1],
                    )
                    remaining = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM assurance_pending_confirmations"
                        ).fetchone()[0]
                    )
                    if remaining >= self.capacity:
                        raise PendingConfirmationCapacityError(
                            "pending confirmation capacity reached"
                        )
                try:
                    connection.execute(
                        "INSERT INTO assurance_pending_confirmations ("
                        "preview_message_id, request_message_id, task_id, context_id, "
                        "workflow_id, trace_id, challenge_sha256, snapshot_sha256, "
                        "candidate_ids, effective_window_start, effective_window_end, "
                        "resource_ids, state, confirmation_fingerprint, "
                        "confirmation_candidate_id, confirmation_idempotency_key, "
                        "confirmation_decision, result_payload, created_at, expires_at, "
                        "updated_at) VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            record.preview_message_id,
                            record.request_message_id,
                            record.task_id,
                            record.context_id,
                            record.workflow_id,
                            record.trace_id,
                            record.challenge_sha256,
                            record.snapshot_sha256,
                            json.dumps(record.candidate_ids),
                            record.effective_window_start,
                            record.effective_window_end,
                            json.dumps(record.resource_ids),
                            record.state,
                            None,
                            None,
                            None,
                            None,
                            None,
                            record.created_at,
                            record.expires_at,
                            record.updated_at,
                        ],
                    )
                except duckdb.ConstraintException:
                    raise PendingConfirmationConflictError(
                        "preview confirmation already exists"
                    ) from None
                connection.execute("COMMIT")
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except duckdb.Error:
                    pass
                raise
            finally:
                connection.close()

    async def get(self, preview_message_id: str) -> PendingConfirmationRecord | None:
        with self._lock:
            connection = _connect(self.database_path, read_only=True)
            try:
                row = self._select(connection, preview_message_id)
                return None if row is None else self._from_row(row)
            finally:
                connection.close()

    async def claim(
        self,
        preview_message_id: str,
        confirmation_fingerprint: str,
        *,
        candidate_id: str,
        idempotency_key: str,
        decision: Literal["CONFIRM", "REJECT"],
        now: datetime,
        allow_expired_processing: bool = False,
    ) -> PendingConfirmationRecord:
        instant = _utc(now, "now")
        if len(confirmation_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in confirmation_fingerprint
        ):
            raise ValueError("confirmation_fingerprint must be lowercase SHA-256")
        if (
            not candidate_id
            or len(candidate_id) > 256
            or not idempotency_key
            or len(idempotency_key) > 256
            or decision not in {"CONFIRM", "REJECT"}
        ):
            raise ValueError("confirmation claim metadata is invalid")
        try:
            assert_model_safe(
                {
                    "candidate_id": candidate_id,
                    "idempotency_key": idempotency_key,
                    "decision": decision,
                }
            )
        except SensitiveDataError:
            raise PendingConfirmationError("confirmation claim rejected") from None
        with self._lock:
            connection = _connect(self.database_path)
            try:
                connection.execute("BEGIN TRANSACTION")
                row = self._select(connection, preview_message_id)
                if row is None:
                    raise PendingConfirmationNotFoundError(
                        "pending confirmation was not found"
                    )
                record = self._from_row(row)
                if candidate_id not in record.candidate_ids:
                    raise PendingConfirmationConflictError(
                        "confirmation candidate does not match"
                    )
                if record.expires_at <= instant and record.state in {
                    "pending",
                    "processing",
                } and not (
                    record.state == "processing" and allow_expired_processing
                ):
                    connection.execute(
                        "UPDATE assurance_pending_confirmations SET state = 'expired', "
                        "updated_at = ? WHERE preview_message_id = ?",
                        [instant, preview_message_id],
                    )
                    connection.execute("COMMIT")
                    raise PendingConfirmationExpiredError(
                        "pending confirmation has expired"
                    )
                if record.state == "pending":
                    connection.execute(
                        "UPDATE assurance_pending_confirmations SET state = 'processing', "
                        "confirmation_fingerprint = ?, confirmation_candidate_id = ?, "
                        "confirmation_idempotency_key = ?, confirmation_decision = ?, "
                        "updated_at = ? "
                        "WHERE preview_message_id = ?",
                        [
                            confirmation_fingerprint,
                            candidate_id,
                            idempotency_key,
                            decision,
                            instant,
                            preview_message_id,
                        ],
                    )
                elif not hmac.compare_digest(
                    record.confirmation_fingerprint or "", confirmation_fingerprint
                ) or any(
                    stored != supplied
                    for stored, supplied in (
                        (record.confirmation_candidate_id, candidate_id),
                        (record.confirmation_idempotency_key, idempotency_key),
                        (record.confirmation_decision, decision),
                    )
                ):
                    raise PendingConfirmationConflictError(
                        "confirmation has already been decided"
                    )
                if record.state in {"cancelled", "expired"}:
                    raise PendingConfirmationConflictError(
                        "confirmation is no longer available"
                    )
                row = self._select(connection, preview_message_id)
                connection.execute("COMMIT")
                assert row is not None
                return self._from_row(row)
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except duckdb.Error:
                    pass
                raise
            finally:
                connection.close()

    async def finish(
        self,
        preview_message_id: str,
        confirmation_fingerprint: str,
        *,
        state: Literal["completed", "rejected"],
        result_payload: dict[str, Any],
        now: datetime,
    ) -> PendingConfirmationRecord:
        instant = _utc(now, "now")
        try:
            assert_model_safe(result_payload)
        except SensitiveDataError:
            raise PendingConfirmationError("confirmation result rejected") from None
        with self._lock:
            connection = _connect(self.database_path)
            try:
                row = self._select(connection, preview_message_id)
                if row is None:
                    raise PendingConfirmationNotFoundError(
                        "pending confirmation was not found"
                    )
                current = self._from_row(row)
                if not hmac.compare_digest(
                    current.confirmation_fingerprint or "", confirmation_fingerprint
                ):
                    raise PendingConfirmationConflictError(
                        "confirmation claim does not match"
                    )
                if current.state in {"completed", "rejected"}:
                    return current
                if current.state != "processing":
                    raise PendingConfirmationConflictError(
                        "confirmation is not being processed"
                    )
                connection.execute(
                    "UPDATE assurance_pending_confirmations SET state = ?, result_payload = ?, "
                    "updated_at = ? WHERE preview_message_id = ?",
                    [state, json.dumps(result_payload, ensure_ascii=False), instant, preview_message_id],
                )
                updated = self._select(connection, preview_message_id)
                assert updated is not None
                return self._from_row(updated)
            finally:
                connection.close()

    async def cancel(self, task_id: str, context_id: str, *, now: datetime) -> bool:
        instant = _utc(now, "now")
        with self._lock:
            connection = _connect(self.database_path)
            try:
                changed = connection.execute(
                    "UPDATE assurance_pending_confirmations SET state = 'cancelled', "
                    "updated_at = ? WHERE task_id = ? AND context_id = ? AND state = 'pending' "
                    "RETURNING preview_message_id",
                    [instant, task_id, context_id],
                ).fetchall()
                return bool(changed)
            finally:
                connection.close()


class DuckDbTaskStore(TaskStore):
    """A2A 0.3.11 TaskStore using alias-preserving Pydantic JSON."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        capacity: int = DEFAULT_TASK_CAPACITY,
        clock: StoreClock | None = None,
    ):
        if not 1 <= capacity <= 100_000:
            raise ValueError("task capacity must be between 1 and 100000")
        self.database_path = _database_path(database_path)
        self.capacity = capacity
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = _path_lock(self.database_path)
        _require_initialized(self.database_path)

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        del context
        encoded = task.model_dump_json(by_alias=True)
        if len(encoded.encode("utf-8")) > MAX_TASK_JSON_BYTES:
            raise ValueError("A2A Task exceeds the persistence budget")
        # Validating before persistence also rejects non-JSON extension objects.
        Task.model_validate_json(encoded)
        try:
            assert_model_safe(json.loads(encoded))
        except SensitiveDataError:
            raise ValueError("A2A Task rejected by privacy policy") from None
        now = _utc(self._clock(), "clock")
        with self._lock:
            connection = _connect(self.database_path)
            try:
                connection.execute("BEGIN TRANSACTION")
                _reconcile_expired_confirmations(connection, now=now)
                exists = connection.execute(
                    "SELECT 1 FROM assurance_a2a_tasks WHERE task_id = ?", [task.id]
                ).fetchone()
                if exists is None:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM assurance_a2a_tasks"
                        ).fetchone()[0]
                    )
                    if count >= self.capacity:
                        connection.execute(
                            "DELETE FROM assurance_a2a_tasks WHERE task_id IN ("
                            "SELECT task_id FROM assurance_a2a_tasks "
                            "WHERE state IN ('completed','canceled','rejected','failed') "
                            "OR task_id IN (SELECT task_id FROM "
                            "assurance_pending_confirmations WHERE state = 'expired') "
                            "ORDER BY updated_at LIMIT ?)",
                            [count - self.capacity + 1],
                        )
                        remaining = int(
                            connection.execute(
                                "SELECT COUNT(*) FROM assurance_a2a_tasks"
                            ).fetchone()[0]
                        )
                        if remaining >= self.capacity:
                            raise RuntimeError("A2A TaskStore capacity reached")
                connection.execute(
                    "INSERT INTO assurance_a2a_tasks VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (task_id) DO UPDATE SET context_id = excluded.context_id, "
                    "state = excluded.state, task_json = excluded.task_json, "
                    "updated_at = excluded.updated_at",
                    [task.id, task.context_id, task.status.state.value, encoded, now],
                )
                connection.execute("COMMIT")
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except duckdb.Error:
                    pass
                raise
            finally:
                connection.close()

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        del context
        with self._lock:
            connection = _connect(self.database_path, read_only=True)
            try:
                row = connection.execute(
                    "SELECT task_json FROM assurance_a2a_tasks WHERE task_id = ?",
                    [task_id],
                ).fetchone()
                if row is None:
                    return None
                encoded = row[0] if isinstance(row[0], str) else json.dumps(row[0])
                return Task.model_validate_json(encoded)
            finally:
                connection.close()

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        del context
        with self._lock:
            connection = _connect(self.database_path)
            try:
                connection.execute(
                    "DELETE FROM assurance_a2a_tasks WHERE task_id = ?", [task_id]
                )
            finally:
                connection.close()


__all__ = [
    "ASSURANCE_SCHEMA_VERSION",
    "DuckDbPendingConfirmationStore",
    "DuckDbTaskStore",
    "PROCESSING_RECOVERY_RETENTION",
    "PendingConfirmationCapacityError",
    "PendingConfirmationConflictError",
    "PendingConfirmationError",
    "PendingConfirmationExpiredError",
    "PendingConfirmationNotFoundError",
    "PendingConfirmationRecord",
    "initialize_assurance_database",
    "initialize_assurance_store",
]
