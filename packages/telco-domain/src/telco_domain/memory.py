"""Concurrency-safe in-memory adapters for offline workflows and tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .models import Incident, IncidentAuditEvent, IncidentStatus
from .ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentAlreadyExistsError,
    IncidentNotFoundError,
    RevisionConflictError,
    UnsafeIncidentWriteError,
)
from .privacy import SensitiveDataError, assert_model_safe
from .state_machine import SETTLED_STATUSES, transition_incident


_IdempotencyScope = tuple[str, str, str]
Clock = Callable[[], datetime]
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


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    fingerprint: str
    result: Incident


def _clone(incident: Incident) -> Incident:
    # No fields are updated here; the deep copy only detaches mutable JSON maps.
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
    try:
        assert_model_safe({"actor": actor, "reason": reason})
    except SensitiveDataError:
        raise UnsafeIncidentWriteError(
            "<audit-metadata>", "privacy policy violation"
        ) from None


def _assert_repository_safe(value: object, *, incident_id: str) -> None:
    try:
        assert_model_safe(value)
    except SensitiveDataError:
        raise UnsafeIncidentWriteError(
            incident_id, "privacy policy violation"
        ) from None


def _scope(operation: str, incident_id: str, idempotency_key: str) -> _IdempotencyScope:
    _require_non_empty("operation", operation, max_length=64)
    _require_non_empty("incident_id", incident_id, max_length=256)
    _require_non_empty("idempotency_key", idempotency_key, max_length=256)
    return operation.strip().lower(), incident_id, idempotency_key


class InMemoryIncidentRepository:
    """Atomic repository with state-machine, correlation, and replay guards.

    Idempotency records are scoped by ``(operation, requested incident ID,
    idempotency key)`` and store a request fingerprint.  Thus an exact retry
    returns the original snapshot, while an unrelated incident may safely use
    the same client-generated key.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._incidents: dict[str, Incident] = {}
        self._idempotency: dict[_IdempotencyScope, _IdempotencyRecord] = {}
        self._history: dict[str, list[IncidentAuditEvent]] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(UTC))

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

        async with self._lock:
            replay = self._replay(request_scope, fingerprint)
            if replay is not None:
                return replay
            if incident.incident_id in self._incidents:
                raise IncidentAlreadyExistsError(incident.incident_id)

            trusted_now = self._now()
            committed = self._validate_new_incident(
                incident, trace_id=trace_id, trusted_now=trusted_now
            )
            active = self._find_active_locked(
                correlation_key=committed.correlation_key,
                source_event_ids=frozenset(committed.source_event_ids),
            )
            if active is not None:
                raise ActiveIncidentConflictError(
                    committed.incident_id, active.incident_id
                )
            self._commit_create_locked(
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
        """Atomically deduplicate active incidents by correlation or source event."""

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

        async with self._lock:
            replay = self._replay(request_scope, fingerprint)
            if replay is not None:
                return replay

            trusted_now = self._now()
            candidate = self._validate_new_incident(
                incident, trace_id=trace_id, trusted_now=trusted_now
            )
            existing = self._find_active_locked(
                correlation_key=candidate.correlation_key,
                source_event_ids=frozenset(candidate.source_event_ids),
            )
            if existing is not None:
                self._idempotency[request_scope] = _IdempotencyRecord(
                    fingerprint=fingerprint,
                    result=_clone(existing),
                )
                return _clone(existing)

            if candidate.incident_id in self._incidents:
                raise IncidentAlreadyExistsError(candidate.incident_id)
            self._commit_create_locked(
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
        async with self._lock:
            incident = self._incidents.get(incident_id)
            return None if incident is None else _clone(incident)

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
        """Validate a supplied successor by rebuilding it through the state machine."""

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

        async with self._lock:
            replay = self._replay(request_scope, fingerprint)
            if replay is not None:
                return replay
            current = self._require_current_locked(incident.incident_id)
            self._require_revision(current, incident, expected_revision)
            self._require_trace(current, trace_id)

            validated_candidate = Incident.model_validate(
                incident.model_dump(mode="python", round_trip=True)
            )
            current_payload = current.model_dump(mode="python", round_trip=True)
            candidate_payload = validated_candidate.model_dump(
                mode="python", round_trip=True
            )
            updates = {
                field: candidate_payload[field]
                for field in type(current).model_fields
                if field not in _SAVE_EXCLUDED_FIELDS
                and candidate_payload[field] != current_payload[field]
            }
            _assert_repository_safe(updates, incident_id=incident.incident_id)
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
                mode="python", round_trip=True, exclude={"updated_at"}
            )
            candidate_comparable = validated_candidate.model_dump(
                mode="python", round_trip=True, exclude={"updated_at"}
            )
            if successor_comparable != candidate_comparable:
                raise UnsafeIncidentWriteError(
                    incident.incident_id,
                    "candidate is not the exact output of the domain state machine",
                )
            _assert_repository_safe(successor, incident_id=incident.incident_id)
            self._commit_transition_locked(
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
        """Run and commit a domain transition under one lock."""

        _require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        if expected_revision < 0:
            raise ValueError("expected_revision must not be negative")
        _assert_repository_safe(
            dict(updates or {}), incident_id=incident_id
        )
        operation = "transition"
        request_scope = _scope(operation, incident_id, idempotency_key)
        fingerprint = _fingerprint(
            operation,
            {
                "target_status": target_status,
                "expected_revision": expected_revision,
                "updates": dict(updates or {}),
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

        async with self._lock:
            replay = self._replay(request_scope, fingerprint)
            if replay is not None:
                return replay
            current = self._require_current_locked(incident_id)
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
                updates=updates,
                now=trusted_now,
            )
            _assert_repository_safe(successor, incident_id=incident_id)
            self._commit_transition_locked(
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
        async with self._lock:
            record = self._idempotency.get(request_scope)
            return None if record is None else _clone(record.result)

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
        async with self._lock:
            incident = self._find_active_locked(
                correlation_key=correlation_key,
                source_event_ids=source_ids,
            )
            return None if incident is None else _clone(incident)

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

        async with self._lock:
            incidents = sorted(
                self._incidents.values(), key=lambda item: item.incident_id
            )
            if status is not None:
                incidents = [item for item in incidents if item.status is status]
            page = incidents[offset : offset + limit]
            return tuple(_clone(item) for item in page)

    async def history(self, incident_id: str) -> tuple[IncidentAuditEvent, ...]:
        async with self._lock:
            return tuple(self._history.get(incident_id, ()))

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

    def _commit_create_locked(
        self,
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
        self._incidents[incident.incident_id] = incident
        self._idempotency[scope] = _IdempotencyRecord(
            fingerprint=fingerprint,
            result=_clone(incident),
        )
        self._history[incident.incident_id] = [event]

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("repository clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _commit_transition_locked(
        self,
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
        self._incidents[successor.incident_id] = successor
        self._idempotency[scope] = _IdempotencyRecord(
            fingerprint=fingerprint,
            result=_clone(successor),
        )
        self._history.setdefault(successor.incident_id, []).append(event)

    def _find_active_locked(
        self,
        *,
        correlation_key: str | None,
        source_event_ids: frozenset[str],
    ) -> Incident | None:
        for incident in sorted(
            self._incidents.values(), key=lambda item: item.incident_id
        ):
            if incident.status in SETTLED_STATUSES:
                continue
            correlation_match = (
                correlation_key is not None
                and incident.correlation_key == correlation_key
            )
            source_match = bool(source_event_ids.intersection(incident.source_event_ids))
            if correlation_match or source_match:
                return incident
        return None

    def _require_current_locked(self, incident_id: str) -> Incident:
        current = self._incidents.get(incident_id)
        if current is None:
            raise IncidentNotFoundError(incident_id)
        return current

    @staticmethod
    def _require_revision(
        current: Incident, candidate: Incident, expected_revision: int
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

    def _replay(
        self, scope: _IdempotencyScope, fingerprint: str
    ) -> Incident | None:
        record = self._idempotency.get(scope)
        if record is None:
            return None
        if record.fingerprint != fingerprint:
            raise IdempotencyConflictError(scope[2])
        return _clone(record.result)

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
            f"{incident.incident_id}\0{idempotency_key}\0{fingerprint}".encode("utf-8")
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


__all__ = ["InMemoryIncidentRepository"]
