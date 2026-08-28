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

from .contracts import MAX_CONTRACT_DEPTH, MAX_CONTRACT_SERIALIZED_BYTES
from .models import (
    Incident,
    IncidentAuditEvent,
    IncidentStatus,
    MAX_INCIDENT_SOURCE_EVENTS,
    SourceEventAssociation,
)
from .ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentAlreadyExistsError,
    IncidentCorrelationConflictError,
    IncidentNotFoundError,
    IncidentRepositoryError,
    IncidentSnapshotImportResult,
    MAX_REPOSITORY_BATCH_BYTES,
    MAX_REPOSITORY_OFFSET,
    MAX_REPOSITORY_PAGE_SIZE,
    RevisionConflictError,
    SourceEventOwnershipConflictError,
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
    _assert_repository_safe(
        {
            "idempotency_key": idempotency_key,
            "actor": actor,
            "reason": reason,
            "trace_id": trace_id,
        },
        incident_id="<audit-metadata>",
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
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((item, depth + 1) for item in current)
    return maximum


def _assert_repository_safe(value: object, *, incident_id: str) -> int:
    try:
        plain = _json_safe(value)
        assert_model_safe(plain)
        if _payload_depth(plain) > MAX_CONTRACT_DEPTH:
            raise UnsafeIncidentWriteError(
                incident_id, "payload depth exceeds canonical limit"
            )
        encoded = json.dumps(
            plain,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except UnsafeIncidentWriteError:
        raise
    except (SensitiveDataError, TypeError, ValueError, UnicodeError, RecursionError):
        raise UnsafeIncidentWriteError(
            incident_id, "privacy or JSON policy violation"
        ) from None
    if len(encoded) > MAX_CONTRACT_SERIALIZED_BYTES:
        raise UnsafeIncidentWriteError(
            incident_id, "serialized payload exceeds canonical size limit"
        )
    return len(encoded)


def _bounded_page(values: Sequence[object]) -> tuple[object, ...]:
    total_bytes = 0
    result: list[object] = []
    for value in values:
        total_bytes += _assert_repository_safe(
            value, incident_id="<repository-page>"
        )
        if total_bytes > MAX_REPOSITORY_BATCH_BYTES:
            raise UnsafeIncidentWriteError(
                "<repository-page>", "repository batch exceeds size limit"
            )
        model_copy = getattr(value, "model_copy", None)
        result.append(model_copy(deep=True) if callable(model_copy) else value)
    return tuple(result)


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
        self._source_events: dict[
            tuple[str, str], SourceEventAssociation
        ] = {}
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
                requested_incident_id=committed.incident_id,
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
                requested_incident_id=candidate.incident_id,
            )
            if existing is not None:
                self._register_source_events_locked(
                    incident_id=existing.incident_id,
                    source_event_ids=candidate.source_event_ids,
                    registered_at=trusted_now,
                    actor=actor,
                    reason=reason,
                    idempotency_key=request_scope[2],
                    trace_id=trace_id,
                )
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

    async def import_detected_snapshot(
        self,
        incident: Incident,
        associations: Sequence[SourceEventAssociation],
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> IncidentSnapshotImportResult:
        """Import one DETECTED snapshot while preserving source provenance.

        This deliberately narrow extension is used by the one-time canonical
        migration contract.  It is not part of the normal write path: source
        association timestamps and audit metadata come from the verified
        snapshot instead of being reconstructed from the migration clock.
        """

        _require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        candidate = Incident.model_validate(
            incident.model_dump(mode="python", round_trip=True)
        )
        if candidate.status is not IncidentStatus.DETECTED or candidate.revision != 0:
            raise ValueError("migration accepts only DETECTED revision-0 incidents")
        if candidate.trace_id != trace_id:
            raise ValueError("trace_id must match the imported incident")
        _assert_repository_safe(candidate, incident_id=candidate.incident_id)
        if not isinstance(associations, Sequence) or isinstance(
            associations, (str, bytes, bytearray)
        ):
            raise ValueError("migration source associations must be bounded")
        if len(associations) > MAX_INCIDENT_SOURCE_EVENTS:
            raise ValueError("migration source association capacity exceeded")
        normalized_values: list[SourceEventAssociation] = []
        association_bytes = 0
        for index, item in enumerate(associations):
            if index >= MAX_INCIDENT_SOURCE_EVENTS:
                raise ValueError("migration source association capacity exceeded")
            normalized = SourceEventAssociation.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
            _assert_repository_safe(normalized, incident_id=candidate.incident_id)
            association_bytes += len(
                json.dumps(
                    _json_safe(normalized),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ) + 1
            if association_bytes > MAX_REPOSITORY_BATCH_BYTES:
                raise UnsafeIncidentWriteError(
                    candidate.incident_id,
                    "cumulative serialized payload exceeds repository batch budget",
                )
            normalized_values.append(normalized)
        normalized_associations = tuple(normalized_values)
        association_ids = tuple(
            item.source_event_id for item in normalized_associations
        )
        if len(association_ids) != len(set(association_ids)):
            raise ValueError("migration source associations must be unique")
        if not set(candidate.source_event_ids).issubset(association_ids):
            raise ValueError("migration source provenance is incomplete")
        if any(
            item.incident_id != candidate.incident_id
            for item in normalized_associations
        ):
            raise ValueError("migration source association binding mismatch")
        operation = "import_detected_snapshot"
        request_scope = _scope(operation, candidate.incident_id, idempotency_key)
        request_fingerprint = _fingerprint(
            operation,
            {
                "incident": candidate,
                "associations": normalized_associations,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

        async with self._lock:
            replay = self._replay(request_scope, request_fingerprint)
            if replay is not None:
                if replay != candidate:
                    raise IncidentRepositoryError(
                        "persisted migration replay snapshot is inconsistent"
                    )
                current = self._incidents.get(candidate.incident_id)
                if current is None:
                    raise IncidentRepositoryError(
                        "persisted migration Incident is missing"
                    )
                for association in normalized_associations:
                    if self._source_events.get(
                        (association.incident_id, association.source_event_id)
                    ) != association:
                        raise IncidentRepositoryError(
                            "persisted migration source provenance is inconsistent"
                        )
                audit = next(
                    (
                        event
                        for event in self._history.get(candidate.incident_id, ())
                        if event.revision == 0
                    ),
                    None,
                )
                if (
                    audit is None
                    or audit.from_status is not None
                    or audit.to_status is not IncidentStatus.DETECTED
                    or audit.idempotency_key != request_scope[2]
                    or audit.actor != actor
                    or audit.reason != reason
                    or audit.trace_id != trace_id
                ):
                    raise IncidentRepositoryError(
                        "persisted migration audit is inconsistent"
                    )
                if (
                    current.status not in SETTLED_STATUSES
                    and (
                        candidate.correlation_key is not None
                        or bool(association_ids)
                    )
                ):
                    active = self._find_active_locked(
                        correlation_key=candidate.correlation_key,
                        source_event_ids=frozenset(association_ids),
                        requested_incident_id=candidate.incident_id,
                    )
                    if active is None or active.incident_id != candidate.incident_id:
                        raise IncidentRepositoryError(
                            "persisted migration active keys are inconsistent"
                        )
                return IncidentSnapshotImportResult(
                    incident=replay,
                    replayed=True,
                )
            if candidate.incident_id in self._incidents:
                raise IncidentAlreadyExistsError(candidate.incident_id)
            for source_event_id in association_ids:
                owner = self._source_event_owner_locked(source_event_id)
                if owner is not None and owner != candidate.incident_id:
                    raise SourceEventOwnershipConflictError(
                        source_event_id,
                        owner,
                        candidate.incident_id,
                    )
            active = self._find_active_locked(
                correlation_key=candidate.correlation_key,
                source_event_ids=frozenset(association_ids),
                requested_incident_id=candidate.incident_id,
            )
            if active is not None:
                raise ActiveIncidentConflictError(
                    candidate.incident_id, active.incident_id
                )

            trusted_now = self._now()
            event = self._audit_event(
                candidate,
                from_status=None,
                idempotency_key=request_scope[2],
                actor=actor,
                reason=reason,
                trace_id=trace_id,
                fingerprint=request_fingerprint,
                occurred_at=trusted_now,
            )
            for association in normalized_associations:
                self._source_events[
                    (association.incident_id, association.source_event_id)
                ] = association
            self._incidents[candidate.incident_id] = candidate
            self._idempotency[request_scope] = _IdempotencyRecord(
                fingerprint=request_fingerprint,
                result=_clone(candidate),
            )
            self._history[candidate.incident_id] = [event]
            return IncidentSnapshotImportResult(
                incident=_clone(candidate),
                replayed=False,
            )

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
            self._require_reactivation_keys_locked(current, successor)
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
            self._require_reactivation_keys_locked(current, successor)
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
                requested_incident_id="<lookup>",
            )
            return None if incident is None else _clone(incident)

    async def list(
        self,
        *,
        status: IncidentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Incident, ...]:
        if limit < 1 or limit > MAX_REPOSITORY_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0 or offset > MAX_REPOSITORY_OFFSET:
            raise ValueError("offset must be between 0 and 100000")
        normalized_status = None if status is None else IncidentStatus(status)

        async with self._lock:
            incidents = sorted(
                self._incidents.values(), key=lambda item: item.incident_id
            )
            if normalized_status is not None:
                incidents = [
                    item for item in incidents
                    if item.status is normalized_status
                ]
            page = incidents[offset : offset + limit]
            return _bounded_page(page)  # type: ignore[return-value]

    async def history(
        self,
        incident_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[IncidentAuditEvent, ...]:
        _require_non_empty("incident_id", incident_id, max_length=256)
        if limit is not None and (limit < 1 or limit > MAX_REPOSITORY_PAGE_SIZE):
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0 or offset > MAX_REPOSITORY_OFFSET:
            raise ValueError("offset must be between 0 and 100000")
        if limit is None and offset:
            raise ValueError("offset requires an explicit limit")
        async with self._lock:
            history = tuple(self._history.get(incident_id, ()))
            page = history[offset:] if limit is None else history[offset : offset + limit]
            return _bounded_page(page)  # type: ignore[return-value]

    async def source_event_associations(
        self,
        incident_id: str,
        *,
        limit: int = MAX_REPOSITORY_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SourceEventAssociation, ...]:
        _require_non_empty("incident_id", incident_id, max_length=256)
        if limit < 1 or limit > MAX_REPOSITORY_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0 or offset > MAX_REPOSITORY_OFFSET:
            raise ValueError("offset must be between 0 and 100000")
        async with self._lock:
            matches = sorted(
                (
                    association
                    for (linked_incident_id, _), association in self._source_events.items()
                    if linked_incident_id == incident_id
                ),
                key=lambda item: item.source_event_id,
            )
            return _bounded_page(
                matches[offset : offset + limit]
            )  # type: ignore[return-value]

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
        # Establish immutable provenance first so an ownership conflict leaves
        # no partial Incident, audit, or idempotency record in memory.
        self._register_source_events_locked(
            incident_id=incident.incident_id,
            source_event_ids=incident.source_event_ids,
            registered_at=occurred_at,
            actor=actor,
            reason=reason,
            idempotency_key=scope[2],
            trace_id=trace_id,
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
        self._register_source_events_locked(
            incident_id=successor.incident_id,
            source_event_ids=successor.source_event_ids,
            registered_at=occurred_at,
            actor=actor,
            reason=reason,
            idempotency_key=scope[2],
            trace_id=trace_id,
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
        exclude_incident_id: str | None = None,
        requested_incident_id: str = "<candidate>",
    ) -> Incident | None:
        matches: dict[str, Incident] = {}
        for incident in sorted(
            self._incidents.values(), key=lambda item: item.incident_id
        ):
            if incident.incident_id == exclude_incident_id:
                continue
            if incident.status in SETTLED_STATUSES:
                continue
            correlation_match = (
                correlation_key is not None
                and incident.correlation_key == correlation_key
            )
            associated_ids = self._associated_source_ids_locked(
                incident.incident_id
            )
            source_match = bool(source_event_ids.intersection(associated_ids))
            if correlation_match or source_match:
                matches[incident.incident_id] = incident
        if len(matches) > 1:
            raise IncidentCorrelationConflictError(
                requested_incident_id,
                tuple(matches),
            )
        return next(iter(matches.values()), None)

    def _associated_source_ids_locked(self, incident_id: str) -> frozenset[str]:
        return frozenset(
            source_event_id
            for linked_incident_id, source_event_id in self._source_events
            if linked_incident_id == incident_id
        )

    def _source_event_owner_locked(self, source_event_id: str) -> str | None:
        owners = {
            linked_incident_id
            for linked_incident_id, linked_source_event_id in self._source_events
            if linked_source_event_id == source_event_id
        }
        if len(owners) > 1:
            raise RuntimeError("source-event ownership invariant is corrupted")
        return next(iter(owners), None)

    def _register_source_events_locked(
        self,
        *,
        incident_id: str,
        source_event_ids: Sequence[str],
        registered_at: datetime,
        actor: str,
        reason: str,
        idempotency_key: str,
        trace_id: str,
    ) -> None:
        incoming = frozenset(source_event_ids)
        existing = self._associated_source_ids_locked(incident_id)
        if len(existing | incoming) > MAX_INCIDENT_SOURCE_EVENTS:
            raise ValueError(
                "incident source-event association capacity exceeded 1000"
            )
        for source_event_id in sorted(incoming):
            owner = self._source_event_owner_locked(source_event_id)
            if owner is not None and owner != incident_id:
                raise SourceEventOwnershipConflictError(
                    source_event_id,
                    owner,
                    incident_id,
                )
        for source_event_id in sorted(incoming):
            key = (incident_id, source_event_id)
            if key in self._source_events:
                continue
            association = SourceEventAssociation(
                incident_id=incident_id,
                source_event_id=source_event_id,
                registered_at=registered_at,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
            )
            _assert_repository_safe(association, incident_id=incident_id)
            self._source_events[key] = association

    def _require_reactivation_keys_locked(
        self, current: Incident, successor: Incident
    ) -> None:
        if (
            current.status not in SETTLED_STATUSES
            or successor.status in SETTLED_STATUSES
        ):
            return
        active = self._find_active_locked(
            correlation_key=successor.correlation_key,
            source_event_ids=self._associated_source_ids_locked(
                successor.incident_id
            ),
            exclude_incident_id=successor.incident_id,
            requested_incident_id=successor.incident_id,
        )
        if active is not None:
            raise ActiveIncidentConflictError(
                successor.incident_id, active.incident_id
            )

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
