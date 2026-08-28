"""Transactional DuckDB implementation of the canonical IncidentRepository."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from telco_domain.models import Incident, IncidentAuditEvent, IncidentStatus
from telco_domain.contracts import (
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_SERIALIZED_BYTES,
)
from telco_domain.ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentAlreadyExistsError,
    IncidentNotFoundError,
    RevisionConflictError,
    UnsafeIncidentWriteError,
)
from telco_domain.privacy import SensitiveDataError, assert_model_safe
from telco_domain.state_machine import SETTLED_STATUSES, transition_incident

from .config import LocalProfileConfig
from .database import _connect, _ensure_repository_schema


Clock = Callable[[], datetime]
_IdempotencyScope = tuple[str, str, str]
_SAVE_EXCLUDED_FIELDS = frozenset(
    {
        "schema_version",
        "incident_id",
        "correlation_key",
        "technology",
        "status",
        "revision",
        "created_at",
        "detected_at",
        "trace_id",
        "updated_at",
    }
)

_LOCKS_GUARD = threading.Lock()
_DATABASE_LOCKS: dict[Path, threading.RLock] = {}


def _database_lock(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _DATABASE_LOCKS.setdefault(path, threading.RLock())


def _clone(incident: Incident) -> Incident:
    return incident.model_copy(deep=True)


def _json_safe(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", round_trip=True)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_json_safe(item) for item in value]
    return value


def _fingerprint(operation: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": _json_safe(payload)},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_non_empty(name: str, value: str, *, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters")


def _require_write_metadata(
    *, idempotency_key: str, actor: str, reason: str, trace_id: str
) -> None:
    _require_non_empty("idempotency_key", idempotency_key, max_length=256)
    _require_non_empty("actor", actor, max_length=256)
    _require_non_empty("reason", reason, max_length=4_096)
    _require_non_empty("trace_id", trace_id, max_length=256)
    _assert_repository_safe(
        {
            "idempotency_key": idempotency_key,
            "actor": actor,
            "reason": reason,
            "trace_id": trace_id,
        },
        incident_id="<write-metadata>",
    )


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


def _assert_repository_safe(value: object, *, incident_id: str) -> None:
    model_dump = getattr(value, "model_dump", None)
    try:
        plain = (
            model_dump(mode="json", round_trip=True)
            if callable(model_dump)
            else value
        )
    except (TypeError, ValueError, RecursionError):
        raise UnsafeIncidentWriteError(
            "<canonical-incident>", "canonical payload must be JSON-safe"
        ) from None

    if _payload_depth(plain) > MAX_CONTRACT_DEPTH:
        raise UnsafeIncidentWriteError(
            "<canonical-incident>",
            f"canonical payload depth exceeds {MAX_CONTRACT_DEPTH}",
        )
    try:
        assert_model_safe(plain)
    except SensitiveDataError:
        raise UnsafeIncidentWriteError(
            "<canonical-incident>", "privacy policy violation"
        ) from None
    try:
        encoded = json.dumps(
            _json_safe(plain),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise UnsafeIncidentWriteError(
            "<canonical-incident>", "canonical payload must be JSON-safe"
        ) from None
    if len(encoded) > MAX_CONTRACT_SERIALIZED_BYTES:
        raise UnsafeIncidentWriteError(
            "<canonical-incident>",
            "canonical payload serialized size exceeds "
            f"{MAX_CONTRACT_SERIALIZED_BYTES} bytes",
        )


def _scope(
    operation: str, incident_id: str, idempotency_key: str
) -> _IdempotencyScope:
    _require_non_empty("operation", operation, max_length=64)
    _require_non_empty("incident_id", incident_id, max_length=256)
    _require_non_empty("idempotency_key", idempotency_key, max_length=256)
    return operation.strip().lower(), incident_id, idempotency_key


def _incident_json(incident: Incident) -> str:
    return incident.model_dump_json(round_trip=True)


def _parse_incident(payload: object) -> Incident:
    if isinstance(payload, str):
        return Incident.model_validate_json(payload)
    return Incident.model_validate(payload)


def _event_json(event: IncidentAuditEvent) -> str:
    return event.model_dump_json(round_trip=True)


def _parse_event(payload: object) -> IncidentAuditEvent:
    if isinstance(payload, str):
        return IncidentAuditEvent.model_validate_json(payload)
    return IncidentAuditEvent.model_validate(payload)


class DuckDbIncidentRepository:
    """Durable local repository with CAS, replay, correlation, and audit guards.

    Each mutation validates through the framework-neutral state machine and
    commits the aggregate, append-only audit event, and idempotency result in a
    single DuckDB transaction.  A process-wide path lock serializes independent
    repository instances that point at the same Local Profile database.
    """

    def __init__(
        self,
        config_or_path: LocalProfileConfig | str | Path,
        *,
        clock: Clock | None = None,
    ) -> None:
        configured_path = (
            config_or_path.database_path
            if isinstance(config_or_path, LocalProfileConfig)
            else Path(config_or_path)
        )
        self._database_path = configured_path.expanduser().resolve(strict=False)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._async_lock = asyncio.Lock()
        self._path_lock = _database_lock(self._database_path)

        with self._path_lock, self._transaction() as connection:
            _ensure_repository_schema(connection)

    @property
    def database_path(self) -> Path:
        return self._database_path

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = _connect(self._database_path)
        try:
            connection.execute("BEGIN TRANSACTION")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def create(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        _require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        _assert_repository_safe(incident, incident_id=incident.incident_id)
        operation = "create"
        request_scope = _scope(operation, incident.incident_id, idempotency_key)
        fingerprint = _fingerprint(
            operation,
            {
                "incident": incident,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

        async with self._async_lock:
            with self._path_lock, self._transaction() as connection:
                replay = self._replay(connection, request_scope, fingerprint)
                if replay is not None:
                    return replay
                if self._get_locked(connection, incident.incident_id) is not None:
                    raise IncidentAlreadyExistsError(incident.incident_id)

                trusted_now = self._now()
                committed = self._validate_new_incident(
                    incident,
                    trace_id=trace_id,
                    trusted_now=trusted_now,
                )
                active = self._find_active_locked(
                    connection,
                    correlation_key=committed.correlation_key,
                    source_event_ids=frozenset(committed.source_event_ids),
                )
                if active is not None:
                    raise ActiveIncidentConflictError(
                        committed.incident_id, active.incident_id
                    )
                self._commit_create_locked(
                    connection,
                    committed,
                    scope=request_scope,
                    fingerprint=fingerprint,
                    actor=actor,
                    reason=reason,
                    trace_id=trace_id,
                    occurred_at=trusted_now,
                )
                return _clone(committed)

    async def create_or_correlate(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        _require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        _assert_repository_safe(incident, incident_id=incident.incident_id)
        operation = "create_or_correlate"
        request_scope = _scope(operation, incident.incident_id, idempotency_key)
        fingerprint = _fingerprint(
            operation,
            {
                "incident": incident,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

        async with self._async_lock:
            with self._path_lock, self._transaction() as connection:
                replay = self._replay(connection, request_scope, fingerprint)
                if replay is not None:
                    return replay

                trusted_now = self._now()
                candidate = self._validate_new_incident(
                    incident,
                    trace_id=trace_id,
                    trusted_now=trusted_now,
                )
                existing = self._find_active_locked(
                    connection,
                    correlation_key=candidate.correlation_key,
                    source_event_ids=frozenset(candidate.source_event_ids),
                )
                if existing is not None:
                    self._insert_source_event_associations_locked(
                        connection,
                        incident_id=existing.incident_id,
                        source_event_ids=candidate.source_event_ids,
                        registered_at=trusted_now,
                        idempotency_key=request_scope[2],
                        actor=actor,
                        reason=reason,
                        trace_id=trace_id,
                    )
                    self._insert_idempotency_locked(
                        connection,
                        scope=request_scope,
                        fingerprint=fingerprint,
                        result=existing,
                        created_at=trusted_now,
                    )
                    return _clone(existing)
                if self._get_locked(connection, candidate.incident_id) is not None:
                    raise IncidentAlreadyExistsError(candidate.incident_id)

                self._commit_create_locked(
                    connection,
                    candidate,
                    scope=request_scope,
                    fingerprint=fingerprint,
                    actor=actor,
                    reason=reason,
                    trace_id=trace_id,
                    occurred_at=trusted_now,
                )
                return _clone(candidate)

    async def get(self, incident_id: str) -> Incident | None:
        _require_non_empty("incident_id", incident_id, max_length=256)
        with self._path_lock:
            connection = _connect(self._database_path, read_only=True)
            try:
                incident = self._get_locked(connection, incident_id)
                return None if incident is None else _clone(incident)
            finally:
                connection.close()

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
        _require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        _assert_repository_safe(incident, incident_id=incident.incident_id)
        if expected_revision < 0:
            raise ValueError("expected_revision must not be negative")
        operation = "save"
        request_scope = _scope(operation, incident.incident_id, idempotency_key)
        fingerprint = _fingerprint(
            operation,
            {
                "incident": incident,
                "expected_revision": expected_revision,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

        async with self._async_lock:
            with self._path_lock, self._transaction() as connection:
                replay = self._replay(connection, request_scope, fingerprint)
                if replay is not None:
                    return replay
                current = self._require_current_locked(
                    connection, incident.incident_id
                )
                self._require_revision(current, incident, expected_revision)
                self._require_trace(current, trace_id)

                validated_candidate = Incident.model_validate(
                    incident.model_dump(mode="python", round_trip=True)
                )
                current_payload = current.model_dump(
                    mode="python", round_trip=True
                )
                candidate_payload = validated_candidate.model_dump(
                    mode="python", round_trip=True
                )
                updates = {
                    field: candidate_payload[field]
                    for field in type(current).model_fields
                    if field not in _SAVE_EXCLUDED_FIELDS
                    and candidate_payload[field] != current_payload[field]
                }
                _assert_repository_safe(
                    updates, incident_id=incident.incident_id
                )
                trusted_now = self._now()
                successor = transition_incident(
                    current,
                    validated_candidate.status,
                    expected_revision,
                    transitioned_at=trusted_now,
                    updates=updates,
                    now=trusted_now,
                )
                successor_comparable = successor.model_dump(
                    mode="python",
                    round_trip=True,
                    exclude={"updated_at"},
                )
                candidate_comparable = validated_candidate.model_dump(
                    mode="python",
                    round_trip=True,
                    exclude={"updated_at"},
                )
                if successor_comparable != candidate_comparable:
                    raise UnsafeIncidentWriteError(
                        incident.incident_id,
                        "candidate is not the exact output of the domain state machine",
                    )
                _assert_repository_safe(
                    successor, incident_id=incident.incident_id
                )
                self._commit_transition_locked(
                    connection,
                    current,
                    successor,
                    scope=request_scope,
                    fingerprint=fingerprint,
                    actor=actor,
                    reason=reason,
                    trace_id=trace_id,
                    occurred_at=trusted_now,
                )
                return _clone(successor)

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
        return await self.save(
            incident,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )

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
        _require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        _assert_repository_safe(
            {"incident_id": incident_id}, incident_id=incident_id
        )
        if expected_revision < 0:
            raise ValueError("expected_revision must not be negative")
        normalized_updates = dict(updates or {})
        _assert_repository_safe(normalized_updates, incident_id=incident_id)
        operation = "transition"
        request_scope = _scope(operation, incident_id, idempotency_key)
        fingerprint = _fingerprint(
            operation,
            {
                "target_status": target_status,
                "expected_revision": expected_revision,
                "updates": normalized_updates,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

        async with self._async_lock:
            with self._path_lock, self._transaction() as connection:
                replay = self._replay(connection, request_scope, fingerprint)
                if replay is not None:
                    return replay
                current = self._require_current_locked(connection, incident_id)
                if current.revision != expected_revision:
                    raise RevisionConflictError(
                        incident_id,
                        expected_revision=expected_revision,
                        actual_revision=current.revision,
                    )
                self._require_trace(current, trace_id)
                trusted_now = self._now()
                successor = transition_incident(
                    current,
                    target_status,
                    expected_revision,
                    transitioned_at=trusted_now,
                    updates=normalized_updates,
                    now=trusted_now,
                )
                _assert_repository_safe(successor, incident_id=incident_id)
                self._commit_transition_locked(
                    connection,
                    current,
                    successor,
                    scope=request_scope,
                    fingerprint=fingerprint,
                    actor=actor,
                    reason=reason,
                    trace_id=trace_id,
                    occurred_at=trusted_now,
                )
                return _clone(successor)

    async def find_by_idempotency_key(
        self,
        incident_id: str,
        idempotency_key: str,
        *,
        operation: str,
    ) -> Incident | None:
        request_scope = _scope(operation, incident_id, idempotency_key)
        with self._path_lock:
            connection = _connect(self._database_path, read_only=True)
            try:
                row = connection.execute(
                    """
                    SELECT result_payload
                    FROM canonical_incident_idempotency
                    WHERE operation = ?
                      AND requested_incident_id = ?
                      AND idempotency_key = ?
                    """,
                    list(request_scope),
                ).fetchone()
                return None if row is None else _parse_incident(row[0])
            finally:
                connection.close()

    async def find_active(
        self,
        *,
        correlation_key: str | None = None,
        source_event_id: str | None = None,
    ) -> Incident | None:
        if correlation_key is None and source_event_id is None:
            raise ValueError("correlation_key or source_event_id is required")
        source_ids = (
            frozenset()
            if source_event_id is None
            else frozenset((source_event_id,))
        )
        with self._path_lock:
            connection = _connect(self._database_path, read_only=True)
            try:
                incident = self._find_active_locked(
                    connection,
                    correlation_key=correlation_key,
                    source_event_ids=source_ids,
                )
                return None if incident is None else _clone(incident)
            finally:
                connection.close()

    async def list(
        self,
        *,
        status: IncidentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Incident, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset must not be negative")

        parameters: list[object] = []
        where = ""
        if status is not None:
            normalized_status = IncidentStatus(status)
            where = "WHERE status = ?"
            parameters.append(normalized_status.value)
        parameters.extend((limit, offset))

        with self._path_lock:
            connection = _connect(self._database_path, read_only=True)
            try:
                rows = connection.execute(
                    f"""
                    SELECT payload
                    FROM canonical_incidents
                    {where}
                    ORDER BY incident_id
                    LIMIT ? OFFSET ?
                    """,
                    parameters,
                ).fetchall()
                return tuple(_parse_incident(row[0]) for row in rows)
            finally:
                connection.close()

    async def history(
        self, incident_id: str
    ) -> tuple[IncidentAuditEvent, ...]:
        _require_non_empty("incident_id", incident_id, max_length=256)
        with self._path_lock:
            connection = _connect(self._database_path, read_only=True)
            try:
                rows = connection.execute(
                    """
                    SELECT payload
                    FROM canonical_incident_audit
                    WHERE incident_id = ?
                    ORDER BY revision, occurred_at, event_id
                    """,
                    [incident_id],
                ).fetchall()
                return tuple(_parse_event(row[0]) for row in rows)
            finally:
                connection.close()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("repository clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _validate_new_incident(
        self,
        incident: Incident,
        *,
        trace_id: str,
        trusted_now: datetime,
    ) -> Incident:
        if incident.revision != 0:
            raise ValueError("a newly created incident must have revision 0")
        if incident.status is not IncidentStatus.DETECTED:
            raise ValueError("a newly created incident must have DETECTED status")
        if incident.trace_id != trace_id:
            raise ValueError("trace_id must match incident.trace_id")
        payload = incident.model_dump(mode="python", round_trip=True)
        payload.update(created_at=trusted_now, updated_at=trusted_now)
        committed = Incident.model_validate(payload)
        _assert_repository_safe(committed, incident_id=incident.incident_id)
        return committed

    @staticmethod
    def _get_locked(connection: Any, incident_id: str) -> Incident | None:
        row = connection.execute(
            "SELECT payload FROM canonical_incidents WHERE incident_id = ?",
            [incident_id],
        ).fetchone()
        return None if row is None else _parse_incident(row[0])

    def _require_current_locked(self, connection: Any, incident_id: str) -> Incident:
        current = self._get_locked(connection, incident_id)
        if current is None:
            raise IncidentNotFoundError(incident_id)
        return current

    @staticmethod
    def _require_revision(
        current: Incident,
        candidate: Incident,
        expected_revision: int,
    ) -> None:
        if (
            current.revision != expected_revision
            or candidate.revision != expected_revision + 1
        ):
            raise RevisionConflictError(
                candidate.incident_id,
                expected_revision=expected_revision,
                actual_revision=current.revision,
                candidate_revision=candidate.revision,
            )

    @staticmethod
    def _require_trace(incident: Incident, trace_id: str) -> None:
        if incident.trace_id != trace_id:
            raise ValueError("trace_id must match the persisted incident trace_id")

    @staticmethod
    def _find_active_locked(
        connection: Any,
        *,
        correlation_key: str | None,
        source_event_ids: frozenset[str],
    ) -> Incident | None:
        if source_event_ids:
            ordered_source_ids = sorted(source_event_ids)
            placeholders = ", ".join("?" for _ in ordered_source_ids)
            association_rows = connection.execute(
                f"""
                SELECT DISTINCT incident.payload
                FROM canonical_incident_source_events AS association
                JOIN canonical_incidents AS incident
                  ON incident.incident_id = association.incident_id
                WHERE association.source_event_id IN ({placeholders})
                ORDER BY incident.incident_id
                """,
                ordered_source_ids,
            ).fetchall()
            for row in association_rows:
                incident = _parse_incident(row[0])
                if incident.status not in SETTLED_STATUSES:
                    return incident

        # Payload scanning keeps databases created before the association table
        # migration readable.  Any subsequent correlation transactionally
        # materializes the missing source association.
        rows = connection.execute(
            "SELECT payload FROM canonical_incidents ORDER BY incident_id"
        ).fetchall()
        for row in rows:
            incident = _parse_incident(row[0])
            if incident.status in SETTLED_STATUSES:
                continue
            correlation_match = (
                correlation_key is not None
                and incident.correlation_key == correlation_key
            )
            source_match = bool(
                source_event_ids.intersection(incident.source_event_ids)
            )
            if correlation_match or source_match:
                return incident
        return None

    @staticmethod
    def _replay(
        connection: Any,
        scope: _IdempotencyScope,
        fingerprint: str,
    ) -> Incident | None:
        row = connection.execute(
            """
            SELECT request_fingerprint, result_payload
            FROM canonical_incident_idempotency
            WHERE operation = ?
              AND requested_incident_id = ?
              AND idempotency_key = ?
            """,
            list(scope),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise IdempotencyConflictError(scope[2])
        return _parse_incident(row[1])

    @staticmethod
    def _audit_event(
        incident: Incident,
        *,
        from_status: IncidentStatus | None,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
        fingerprint: str,
        occurred_at: datetime,
    ) -> IncidentAuditEvent:
        event_digest = hashlib.sha256(
            f"{incident.incident_id}\0{idempotency_key}\0{fingerprint}".encode(
                "utf-8"
            )
        ).hexdigest()
        return IncidentAuditEvent(
            event_id=f"audit-{event_digest[:32]}",
            incident_id=incident.incident_id,
            from_status=from_status,
            to_status=incident.status,
            revision=incident.revision,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _insert_incident_locked(connection: Any, incident: Incident) -> None:
        connection.execute(
            """
            INSERT INTO canonical_incidents (
                incident_id, correlation_key, source_event_ids, status,
                revision, trace_id, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                incident.incident_id,
                incident.correlation_key,
                json.dumps(list(incident.source_event_ids)),
                incident.status.value,
                incident.revision,
                incident.trace_id,
                _incident_json(incident),
                incident.created_at,
                incident.updated_at,
            ],
        )

    @staticmethod
    def _insert_source_event_associations_locked(
        connection: Any,
        *,
        incident_id: str,
        source_event_ids: Sequence[str],
        registered_at: datetime,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> None:
        rows = [
            (
                incident_id,
                source_event_id,
                registered_at,
                idempotency_key,
                actor,
                reason,
                trace_id,
            )
            for source_event_id in sorted(set(source_event_ids))
        ]
        if not rows:
            return
        connection.executemany(
            """
            INSERT INTO canonical_incident_source_events (
                incident_id, source_event_id, registered_at,
                idempotency_key, actor, reason, trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )

    @staticmethod
    def _update_incident_locked(
        connection: Any,
        current: Incident,
        successor: Incident,
    ) -> None:
        connection.execute(
            """
            UPDATE canonical_incidents
            SET source_event_ids = ?, status = ?, revision = ?, payload = ?,
                updated_at = ?
            WHERE incident_id = ? AND revision = ?
            """,
            [
                json.dumps(list(successor.source_event_ids)),
                successor.status.value,
                successor.revision,
                _incident_json(successor),
                successor.updated_at,
                successor.incident_id,
                current.revision,
            ],
        )
        row = connection.execute(
            "SELECT revision FROM canonical_incidents WHERE incident_id = ?",
            [successor.incident_id],
        ).fetchone()
        if row is None or int(row[0]) != successor.revision:
            actual = -1 if row is None else int(row[0])
            raise RevisionConflictError(
                successor.incident_id,
                expected_revision=current.revision,
                actual_revision=actual,
                candidate_revision=successor.revision,
            )

    @staticmethod
    def _insert_audit_locked(connection: Any, event: IncidentAuditEvent) -> None:
        connection.execute(
            """
            INSERT INTO canonical_incident_audit (
                event_id, incident_id, revision, from_status, to_status,
                trace_id, occurred_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event.event_id,
                event.incident_id,
                event.revision,
                None if event.from_status is None else event.from_status.value,
                event.to_status.value,
                event.trace_id,
                event.occurred_at,
                _event_json(event),
            ],
        )

    @staticmethod
    def _insert_idempotency_locked(
        connection: Any,
        *,
        scope: _IdempotencyScope,
        fingerprint: str,
        result: Incident,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO canonical_incident_idempotency (
                operation, requested_incident_id, idempotency_key,
                request_fingerprint, result_payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                scope[0],
                scope[1],
                scope[2],
                fingerprint,
                _incident_json(result),
                created_at,
            ],
        )

    def _commit_create_locked(
        self,
        connection: Any,
        incident: Incident,
        *,
        scope: _IdempotencyScope,
        fingerprint: str,
        actor: str,
        reason: str,
        trace_id: str,
        occurred_at: datetime,
    ) -> None:
        event = self._audit_event(
            incident,
            from_status=None,
            idempotency_key=scope[2],
            actor=actor,
            reason=reason,
            trace_id=trace_id,
            fingerprint=fingerprint,
            occurred_at=occurred_at,
        )
        self._insert_incident_locked(connection, incident)
        self._insert_source_event_associations_locked(
            connection,
            incident_id=incident.incident_id,
            source_event_ids=incident.source_event_ids,
            registered_at=occurred_at,
            idempotency_key=scope[2],
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        self._insert_audit_locked(connection, event)
        self._insert_idempotency_locked(
            connection,
            scope=scope,
            fingerprint=fingerprint,
            result=incident,
            created_at=occurred_at,
        )

    def _commit_transition_locked(
        self,
        connection: Any,
        current: Incident,
        successor: Incident,
        *,
        scope: _IdempotencyScope,
        fingerprint: str,
        actor: str,
        reason: str,
        trace_id: str,
        occurred_at: datetime,
    ) -> None:
        event = self._audit_event(
            successor,
            from_status=current.status,
            idempotency_key=scope[2],
            actor=actor,
            reason=reason,
            trace_id=trace_id,
            fingerprint=fingerprint,
            occurred_at=occurred_at,
        )
        self._update_incident_locked(connection, current, successor)
        self._insert_source_event_associations_locked(
            connection,
            incident_id=successor.incident_id,
            source_event_ids=successor.source_event_ids,
            registered_at=occurred_at,
            idempotency_key=scope[2],
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        self._insert_audit_locked(connection, event)
        self._insert_idempotency_locked(
            connection,
            scope=scope,
            fingerprint=fingerprint,
            result=successor,
            created_at=occurred_at,
        )


__all__ = ["DuckDbIncidentRepository"]
