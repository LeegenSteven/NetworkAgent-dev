"""Transactional Cloud Spanner implementation of the IncidentRepository port."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

from telco_domain.models import (
    Incident,
    IncidentAuditEvent,
    IncidentStatus,
    MAX_INCIDENT_SOURCE_EVENTS,
    SourceEventAssociation,
)
from telco_domain.ports import (
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
from telco_domain.state_machine import SETTLED_STATUSES, transition_incident

from ._common import (
    Clock,
    assert_safe,
    canonical_json,
    fingerprint,
    json_safe,
    parse_json_model,
    require_non_empty,
    require_write_metadata,
    utc_now,
)
from ._spanner import commit_timestamp, execute_sql, json_object, keyset, read_one


_INCIDENT_COLUMNS = (
    "incident_id",
    "correlation_key",
    "schema_version",
    "technology",
    "status",
    "severity",
    "revision",
    "trace_id",
    "detected_at",
    "created_at",
    "updated_at",
    "payload",
)
_SOURCE_COLUMNS = (
    "incident_id",
    "source_event_id",
    "registered_at",
    "actor",
    "reason",
    "idempotency_key",
    "trace_id",
    "payload",
)
_AUDIT_COLUMNS = (
    "incident_id",
    "revision",
    "event_id",
    "from_status",
    "to_status",
    "trace_id",
    "occurred_at",
    "committed_at",
    "payload",
)
_IDEMPOTENCY_COLUMNS = (
    "operation",
    "requested_incident_id",
    "idempotency_key",
    "request_fingerprint",
    "result_incident_id",
    "result_payload",
    "created_at",
)
_ACTIVE_KEY_COLUMNS = (
    "key_hash",
    "key_kind",
    "incident_id",
    "registered_at",
)
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
_IdempotencyScope = tuple[str, str, str]
_ActiveKey = tuple[str, str]
_PageItem = TypeVar("_PageItem")


def _scope(operation: str, incident_id: str, idempotency_key: str) -> _IdempotencyScope:
    return (
        require_non_empty("operation", operation, max_length=64).lower(),
        require_non_empty("incident_id", incident_id, max_length=256),
        require_non_empty("idempotency_key", idempotency_key, max_length=256),
    )


def _clone(incident: Incident) -> Incident:
    return incident.model_copy(deep=True)


def _bounded_page(
    rows: Iterable[Sequence[object]],
    parser: Callable[[Sequence[object]], _PageItem],
    *,
    boundary: str,
) -> tuple[_PageItem, ...]:
    """Parse a StreamedResultSet incrementally under an internal page cap."""

    values: list[_PageItem] = []
    serialized_bytes = 0
    for row in rows:
        # Every parser validates the independent 256 KiB/depth/privacy boundary
        # before this item is retained in the internal page.
        value = parser(row)
        serialized_bytes += len(canonical_json(value).encode("utf-8")) + 1
        if serialized_bytes > MAX_REPOSITORY_BATCH_BYTES:
            raise UnsafeIncidentWriteError(
                boundary,
                f"cumulative serialized payload exceeds "
                f"{MAX_REPOSITORY_BATCH_BYTES} bytes",
            )
        values.append(value)
    return tuple(values)


def _active_key(kind: str, value: str) -> _ActiveKey:
    digest = hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()
    return digest, kind


def _candidate_active_keys(incident: Incident) -> tuple[_ActiveKey, ...]:
    values: list[_ActiveKey] = []
    if incident.correlation_key is not None:
        values.append(_active_key("correlation", incident.correlation_key))
    values.extend(_active_key("source", item) for item in incident.source_event_ids)
    return tuple(sorted(set(values)))


class SpannerIncidentRepository:
    """Canonical Incident persistence using injected Spanner transactions.

    Construction is intentionally inert. Every mutation is one retry-safe
    Spanner callback containing only database work and deterministic domain
    validation; no external event or HTTP call occurs inside a callback.
    """

    def __init__(self, database: Any, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return utc_now(self._clock)

    async def _transaction(self, callback):
        return await asyncio.to_thread(self._database.run_in_transaction, callback)

    async def create(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        assert_safe(incident, boundary=incident.incident_id)
        operation = "create"
        request_scope = _scope(operation, incident.incident_id, idempotency_key)
        request_fingerprint = fingerprint(
            operation,
            {
                "incident": incident,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )
        trusted_now = self._now()

        def callback(transaction):
            replay = self._replay(transaction, request_scope, request_fingerprint)
            if replay is not None:
                return replay
            if self._get_tx(transaction, incident.incident_id) is not None:
                raise IncidentAlreadyExistsError(incident.incident_id)
            committed = self._validate_new_incident(
                incident, trace_id=trace_id, trusted_now=trusted_now
            )
            self._require_source_event_owners_tx(
                transaction,
                incident_id=committed.incident_id,
                source_event_ids=committed.source_event_ids,
            )
            active = self._find_active_for_keys_tx(
                transaction,
                _candidate_active_keys(committed),
                requested_incident_id=committed.incident_id,
            )
            if active is not None:
                raise ActiveIncidentConflictError(
                    committed.incident_id, active.incident_id
                )
            self._commit_create_tx(
                transaction,
                committed,
                scope=request_scope,
                request_fingerprint=request_fingerprint,
                actor=actor,
                reason=reason,
                trace_id=trace_id,
                occurred_at=trusted_now,
            )
            return _clone(committed)

        return await self._transaction(callback)

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
        """Atomically import one canonical DETECTED snapshot and provenance.

        This is a one-time migration boundary, not a general writer.  Unlike
        ``create``, it preserves the source Incident timestamps and every
        immutable SourceEventAssociation field while creating a new migration
        audit/idempotency record in the target database.
        """

        require_write_metadata(
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
        assert_safe(candidate, boundary="migration-incident")
        if not isinstance(associations, Sequence) or isinstance(
            associations, (str, bytes, bytearray)
        ):
            raise ValueError("migration source associations must be bounded") from None
        try:
            association_count = len(associations)
        except (TypeError, OverflowError):
            raise ValueError("migration source associations must be bounded") from None
        if association_count > MAX_INCIDENT_SOURCE_EVENTS:
            raise ValueError("migration source associations exceed 1000")
        normalized_values: list[SourceEventAssociation] = []
        association_bytes = 0
        for index, item in enumerate(associations):
            if index >= MAX_INCIDENT_SOURCE_EVENTS:
                raise ValueError("migration source associations exceed 1000")
            normalized = SourceEventAssociation.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
            assert_safe(normalized, boundary="migration-source-association")
            association_bytes += len(canonical_json(normalized).encode("utf-8")) + 1
            if association_bytes > MAX_REPOSITORY_BATCH_BYTES:
                raise UnsafeIncidentWriteError(
                    "migration-source-association",
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
        request_fingerprint = fingerprint(
            operation,
            {
                "incident": candidate,
                "associations": normalized_associations,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )
        migration_active_keys = tuple(
            sorted(
                set(_candidate_active_keys(candidate))
                | {
                    _active_key("source", source_event_id)
                    for source_event_id in association_ids
                }
            )
        )
        trusted_now = self._now()

        def callback(transaction):
            replay = self._replay(
                transaction, request_scope, request_fingerprint
            )
            if replay is not None:
                self._validate_import_replay_tx(
                    transaction,
                    replay=replay,
                    candidate=candidate,
                    associations=normalized_associations,
                    active_keys=migration_active_keys,
                    scope=request_scope,
                    actor=actor,
                    reason=reason,
                    trace_id=trace_id,
                )
                return IncidentSnapshotImportResult(
                    incident=replay,
                    replayed=True,
                )
            if self._get_tx(transaction, candidate.incident_id) is not None:
                raise IncidentAlreadyExistsError(candidate.incident_id)
            self._require_source_event_owners_tx(
                transaction,
                incident_id=candidate.incident_id,
                source_event_ids=association_ids,
            )
            active = self._find_active_for_keys_tx(
                transaction,
                migration_active_keys,
                requested_incident_id=candidate.incident_id,
            )
            if active is not None:
                raise ActiveIncidentConflictError(
                    candidate.incident_id, active.incident_id
                )
            event = self._audit_event(
                candidate,
                from_status=None,
                idempotency_key=request_scope[2],
                actor=actor,
                reason=reason,
                trace_id=trace_id,
                request_fingerprint=request_fingerprint,
                occurred_at=trusted_now,
            )
            self._insert_incident_tx(transaction, candidate)
            self._insert_imported_source_events_tx(
                transaction,
                incident_id=candidate.incident_id,
                associations=normalized_associations,
            )
            self._acquire_active_keys_tx(
                transaction,
                incident_id=candidate.incident_id,
                keys=migration_active_keys,
                registered_at=trusted_now,
            )
            self._insert_audit_tx(transaction, event)
            self._insert_idempotency_tx(
                transaction,
                scope=request_scope,
                request_fingerprint=request_fingerprint,
                result=candidate,
                created_at=trusted_now,
            )
            return IncidentSnapshotImportResult(
                incident=_clone(candidate),
                replayed=False,
            )

        return await self._transaction(callback)

    async def create_or_correlate(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> Incident:
        require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        assert_safe(incident, boundary=incident.incident_id)
        operation = "create_or_correlate"
        request_scope = _scope(operation, incident.incident_id, idempotency_key)
        request_fingerprint = fingerprint(
            operation,
            {
                "incident": incident,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )
        trusted_now = self._now()

        def callback(transaction):
            result, _, _ = self._create_or_correlate_tx(
                transaction,
                incident,
                scope=request_scope,
                request_fingerprint=request_fingerprint,
                actor=actor,
                reason=reason,
                trace_id=trace_id,
                trusted_now=trusted_now,
            )
            return result

        return await self._transaction(callback)

    def _create_or_correlate_tx(
        self,
        transaction: Any,
        incident: Incident,
        *,
        scope: _IdempotencyScope,
        request_fingerprint: str,
        actor: str,
        reason: str,
        trace_id: str,
        trusted_now: datetime,
    ) -> tuple[Incident, bool, tuple[SourceEventAssociation, ...]]:
        replay = self._replay(transaction, scope, request_fingerprint)
        if replay is not None:
            return replay, False, ()
        candidate = self._validate_new_incident(
            incident, trace_id=trace_id, trusted_now=trusted_now
        )
        keys = _candidate_active_keys(candidate)
        existing = self._find_active_for_keys_tx(
            transaction,
            keys,
            requested_incident_id=candidate.incident_id,
        )
        if existing is not None:
            associations = self._register_source_events_tx(
                transaction,
                incident_id=existing.incident_id,
                source_event_ids=candidate.source_event_ids,
                registered_at=trusted_now,
                idempotency_key=scope[2],
                actor=actor,
                reason=reason,
                trace_id=trace_id,
            )
            self._acquire_active_keys_tx(
                transaction,
                incident_id=existing.incident_id,
                keys=tuple(
                    _active_key("source", item) for item in candidate.source_event_ids
                ),
                registered_at=trusted_now,
            )
            self._insert_idempotency_tx(
                transaction,
                scope=scope,
                request_fingerprint=request_fingerprint,
                result=existing,
                created_at=trusted_now,
            )
            return _clone(existing), False, associations
        if self._get_tx(transaction, candidate.incident_id) is not None:
            raise IncidentAlreadyExistsError(candidate.incident_id)
        associations = self._commit_create_tx(
            transaction,
            candidate,
            scope=scope,
            request_fingerprint=request_fingerprint,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
            occurred_at=trusted_now,
        )
        return _clone(candidate), True, associations

    async def get(self, incident_id: str) -> Incident | None:
        require_non_empty("incident_id", incident_id, max_length=256)

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                value = self._get_tx(snapshot, incident_id)
                return None if value is None else _clone(value)

        return await asyncio.to_thread(read)

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
        require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        assert_safe(incident, boundary=incident.incident_id)
        if expected_revision < 0:
            raise ValueError("expected_revision must not be negative")
        operation = "save"
        request_scope = _scope(operation, incident.incident_id, idempotency_key)
        request_fingerprint = fingerprint(
            operation,
            {
                "incident": incident,
                "expected_revision": expected_revision,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )
        trusted_now = self._now()

        def callback(transaction):
            replay = self._replay(transaction, request_scope, request_fingerprint)
            if replay is not None:
                return replay
            current = self._require_current_tx(transaction, incident.incident_id)
            self._require_revision(current, incident, expected_revision)
            self._require_trace(current, trace_id)
            validated = Incident.model_validate(
                incident.model_dump(mode="python", round_trip=True)
            )
            current_payload = current.model_dump(mode="python", round_trip=True)
            candidate_payload = validated.model_dump(mode="python", round_trip=True)
            updates = {
                field: candidate_payload[field]
                for field in type(current).model_fields
                if field not in _SAVE_EXCLUDED_FIELDS
                and candidate_payload[field] != current_payload[field]
            }
            assert_safe(updates, boundary=incident.incident_id)
            successor = transition_incident(
                current,
                validated.status,
                expected_revision,
                transitioned_at=trusted_now,
                updates=updates,
                now=trusted_now,
            )
            if successor.model_dump(
                mode="python", round_trip=True, exclude={"updated_at"}
            ) != validated.model_dump(
                mode="python", round_trip=True, exclude={"updated_at"}
            ):
                raise UnsafeIncidentWriteError(
                    incident.incident_id,
                    "candidate is not the exact output of the domain state machine",
                )
            self._commit_transition_tx(
                transaction,
                current,
                successor,
                scope=request_scope,
                request_fingerprint=request_fingerprint,
                actor=actor,
                reason=reason,
                trace_id=trace_id,
                occurred_at=trusted_now,
            )
            return _clone(successor)

        return await self._transaction(callback)

    async def compare_and_swap(self, incident: Incident, **kwargs) -> Incident:
        return await self.save(incident, **kwargs)

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
        require_write_metadata(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            trace_id=trace_id,
        )
        require_non_empty("incident_id", incident_id, max_length=256)
        if expected_revision < 0:
            raise ValueError("expected_revision must not be negative")
        normalized_updates = dict(updates or {})
        assert_safe(normalized_updates, boundary=incident_id)
        normalized_target = IncidentStatus(target_status)
        operation = "transition"
        request_scope = _scope(operation, incident_id, idempotency_key)
        request_fingerprint = fingerprint(
            operation,
            {
                "target_status": normalized_target,
                "expected_revision": expected_revision,
                "updates": normalized_updates,
                "actor": actor,
                "reason": reason,
                "trace_id": trace_id,
            },
        )
        trusted_now = self._now()

        def callback(transaction):
            replay = self._replay(transaction, request_scope, request_fingerprint)
            if replay is not None:
                return replay
            current = self._require_current_tx(transaction, incident_id)
            if current.revision != expected_revision:
                raise RevisionConflictError(
                    incident_id,
                    expected_revision=expected_revision,
                    actual_revision=current.revision,
                )
            self._require_trace(current, trace_id)
            successor = transition_incident(
                current,
                normalized_target,
                expected_revision,
                transitioned_at=trusted_now,
                updates=normalized_updates,
                now=trusted_now,
            )
            assert_safe(successor, boundary=incident_id)
            self._commit_transition_tx(
                transaction,
                current,
                successor,
                scope=request_scope,
                request_fingerprint=request_fingerprint,
                actor=actor,
                reason=reason,
                trace_id=trace_id,
                occurred_at=trusted_now,
            )
            return _clone(successor)

        return await self._transaction(callback)

    async def find_by_idempotency_key(
        self, incident_id: str, idempotency_key: str, *, operation: str
    ) -> Incident | None:
        request_scope = _scope(operation, incident_id, idempotency_key)

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                row = read_one(
                    snapshot,
                    "CanonicalIncidentIdempotencyV2",
                    (
                        "operation",
                        "requested_incident_id",
                        "idempotency_key",
                        "result_incident_id",
                        "result_payload",
                    ),
                    request_scope,
                )
                if row is None:
                    return None
                if tuple(row[:3]) != request_scope:
                    raise IncidentRepositoryError(
                        "persisted idempotency scope mismatch"
                    )
                result = parse_json_model(Incident, row[4])
                if result.incident_id != row[3]:
                    raise IncidentRepositoryError(
                        "persisted idempotency result mismatch"
                    )
                assert_safe(result, boundary="incident-idempotency-result")
                return result

        return await asyncio.to_thread(read)

    async def find_active(
        self,
        *,
        correlation_key: str | None = None,
        source_event_id: str | None = None,
    ) -> Incident | None:
        if correlation_key is None and source_event_id is None:
            raise ValueError("correlation_key or source_event_id is required")
        keys: list[_ActiveKey] = []
        if correlation_key is not None:
            keys.append(
                _active_key(
                    "correlation",
                    require_non_empty(
                        "correlation_key", correlation_key, max_length=256
                    ),
                )
            )
        if source_event_id is not None:
            keys.append(
                _active_key(
                    "source",
                    require_non_empty(
                        "source_event_id", source_event_id, max_length=256
                    ),
                )
            )

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                value = self._find_active_for_keys_tx(
                    snapshot,
                    tuple(keys),
                    requested_incident_id="<lookup>",
                )
                return None if value is None else _clone(value)

        return await asyncio.to_thread(read)

    async def list(
        self,
        *,
        status: IncidentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Incident]:
        if limit < 1 or limit > MAX_REPOSITORY_PAGE_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_REPOSITORY_PAGE_SIZE}"
            )
        if offset < 0 or offset > MAX_REPOSITORY_OFFSET:
            raise ValueError(
                f"offset must be between 0 and {MAX_REPOSITORY_OFFSET}"
            )
        normalized_status = None if status is None else IncidentStatus(status)

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                params: dict[str, object] = {"limit": limit, "offset": offset}
                type_spec = {"limit": "INT64", "offset": "INT64"}
                where = ""
                if normalized_status is not None:
                    where = "WHERE status = @status"
                    params["status"] = normalized_status.value
                    type_spec["status"] = "STRING"
                rows = execute_sql(
                    snapshot,
                    f"""-- telco-cloud:list-incidents
                    SELECT incident_id, correlation_key, schema_version,
                           technology, status, severity, revision, trace_id,
                           detected_at, created_at, updated_at, payload
                    FROM CanonicalIncidentsV2
                    {where}
                    ORDER BY incident_id ASC
                    LIMIT @limit OFFSET @offset""",
                    params=params,
                    type_spec=type_spec,
                )
                return _bounded_page(
                    rows,
                    self._parse_incident_row,
                    boundary="incident-list",
                )

        return await asyncio.to_thread(read)

    async def history(
        self,
        incident_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[IncidentAuditEvent]:
        require_non_empty("incident_id", incident_id, max_length=256)
        if limit is not None and (
            limit < 1 or limit > MAX_REPOSITORY_PAGE_SIZE
        ):
            raise ValueError(
                f"limit must be between 1 and {MAX_REPOSITORY_PAGE_SIZE}"
            )
        if offset < 0 or offset > MAX_REPOSITORY_OFFSET:
            raise ValueError(
                f"offset must be between 0 and {MAX_REPOSITORY_OFFSET}"
            )

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                page = ""
                params: dict[str, object] = {"incident_id": incident_id}
                type_spec = {"incident_id": "STRING"}
                if limit is not None:
                    page = "LIMIT @limit OFFSET @offset"
                    params.update(limit=limit, offset=offset)
                    type_spec.update(limit="INT64", offset="INT64")
                elif offset:
                    # An offset without a hard limit would still force an
                    # unbounded database result and is therefore rejected.
                    raise ValueError("offset requires an explicit history limit")
                rows = execute_sql(
                    snapshot,
                    f"""-- telco-cloud:history
                    SELECT incident_id, revision, event_id, from_status,
                           to_status, trace_id, occurred_at, payload
                    FROM CanonicalIncidentAuditV2
                    WHERE incident_id = @incident_id
                    ORDER BY revision, event_id
                    {page}""",
                    params=params,
                    type_spec=type_spec,
                )
                return _bounded_page(
                    rows,
                    lambda row: self._parse_audit_row(
                        row, expected_incident_id=incident_id
                    ),
                    boundary="incident-history",
                )

        return await asyncio.to_thread(read)

    async def source_event_associations(
        self,
        incident_id: str,
        *,
        limit: int = MAX_REPOSITORY_PAGE_SIZE,
        offset: int = 0,
    ) -> Sequence[SourceEventAssociation]:
        require_non_empty("incident_id", incident_id, max_length=256)
        if limit < 1 or limit > MAX_REPOSITORY_PAGE_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_REPOSITORY_PAGE_SIZE}"
            )
        if offset < 0 or offset > MAX_REPOSITORY_OFFSET:
            raise ValueError(
                f"offset must be between 0 and {MAX_REPOSITORY_OFFSET}"
            )

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                rows = execute_sql(
                    snapshot,
                    """-- telco-cloud:source-associations
                    SELECT incident_id, source_event_id, registered_at, actor,
                           reason, idempotency_key, trace_id, payload
                    FROM CanonicalIncidentSourceEventsV2
                    WHERE incident_id = @incident_id
                    ORDER BY source_event_id
                    LIMIT @limit OFFSET @offset""",
                    params={
                        "incident_id": incident_id,
                        "limit": limit,
                        "offset": offset,
                    },
                    type_spec={
                        "incident_id": "STRING",
                        "limit": "INT64",
                        "offset": "INT64",
                    },
                )
                return _bounded_page(
                    rows,
                    lambda row: self._parse_source_association_row(
                        row, expected_incident_id=incident_id
                    ),
                    boundary="source-event-associations",
                )

        return await asyncio.to_thread(read)

    @staticmethod
    def _validate_new_incident(
        incident: Incident, *, trace_id: str, trusted_now: datetime
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
        assert_safe(committed, boundary=incident.incident_id)
        return committed

    @staticmethod
    def _get_tx(reader: Any, incident_id: str) -> Incident | None:
        row = read_one(
            reader,
            "CanonicalIncidentsV2",
            _INCIDENT_COLUMNS,
            (incident_id,),
        )
        return (
            None
            if row is None
            else SpannerIncidentRepository._parse_incident_row(
                row, expected_incident_id=incident_id
            )
        )

    @staticmethod
    def _parse_incident_row(
        row: Sequence[object], *, expected_incident_id: str | None = None
    ) -> Incident:
        if len(row) != len(_INCIDENT_COLUMNS):
            raise IncidentRepositoryError("persisted Incident row shape mismatch")
        incident = parse_json_model(Incident, row[-1])
        binding = (
            incident.incident_id,
            incident.correlation_key,
            incident.schema_version,
            incident.technology.value,
            incident.status.value,
            incident.severity.value,
            incident.revision,
            incident.trace_id,
            incident.detected_at,
            incident.created_at,
            incident.updated_at,
        )
        if tuple(row[:-1]) != binding or (
            expected_incident_id is not None
            and incident.incident_id != expected_incident_id
        ):
            raise IncidentRepositoryError("persisted Incident binding mismatch")
        assert_safe(incident, boundary="persisted-incident")
        return incident

    @staticmethod
    def _parse_audit_row(
        row: Sequence[object], *, expected_incident_id: str
    ) -> IncidentAuditEvent:
        if len(row) != 8:
            raise IncidentRepositoryError("persisted audit row shape mismatch")
        event = parse_json_model(IncidentAuditEvent, row[-1])
        binding = (
            event.incident_id,
            event.revision,
            event.event_id,
            None if event.from_status is None else event.from_status.value,
            event.to_status.value,
            event.trace_id,
            event.occurred_at,
        )
        if tuple(row[:-1]) != binding or event.incident_id != expected_incident_id:
            raise IncidentRepositoryError("persisted audit binding mismatch")
        assert_safe(event, boundary="persisted-audit")
        return event

    @staticmethod
    def _parse_source_association_row(
        row: Sequence[object], *, expected_incident_id: str
    ) -> SourceEventAssociation:
        if len(row) != len(_SOURCE_COLUMNS):
            raise IncidentRepositoryError("persisted source association row shape mismatch")
        association = parse_json_model(SourceEventAssociation, row[-1])
        binding = (
            association.incident_id,
            association.source_event_id,
            association.registered_at,
            association.actor,
            association.reason,
            association.idempotency_key,
            association.trace_id,
        )
        if tuple(row[:-1]) != binding or association.incident_id != expected_incident_id:
            raise IncidentRepositoryError("persisted source association binding mismatch")
        assert_safe(association, boundary="persisted-source-association")
        return association

    @staticmethod
    def _get_source_association_tx(
        reader: Any, incident_id: str, source_event_id: str
    ) -> SourceEventAssociation | None:
        row = read_one(
            reader,
            "CanonicalIncidentSourceEventsV2",
            _SOURCE_COLUMNS,
            (incident_id, source_event_id),
        )
        if row is None:
            return None
        return SpannerIncidentRepository._parse_source_association_row(
            row, expected_incident_id=incident_id
        )

    def _validate_import_replay_tx(
        self,
        reader: Any,
        *,
        replay: Incident,
        candidate: Incident,
        associations: Sequence[SourceEventAssociation],
        active_keys: tuple[_ActiveKey, ...],
        scope: _IdempotencyScope,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> None:
        """Cross-check every durable component before acknowledging a replay."""

        if replay != candidate:
            raise IncidentRepositoryError(
                "persisted migration replay snapshot is inconsistent"
            )
        current = self._get_tx(reader, candidate.incident_id)
        if current is None:
            raise IncidentRepositoryError(
                "persisted migration Incident is missing"
            )
        immutable_fields = (
            "schema_version",
            "incident_id",
            "correlation_key",
            "technology",
            "trace_id",
            "detected_at",
            "created_at",
        )
        if any(
            getattr(current, field) != getattr(candidate, field)
            for field in immutable_fields
        ) or not set(candidate.source_event_ids).issubset(current.source_event_ids):
            raise IncidentRepositoryError(
                "persisted migration Incident lineage is inconsistent"
            )

        source_rows = execute_sql(
            reader,
            """-- telco-cloud:source-associations
            SELECT incident_id, source_event_id, registered_at, actor,
                   reason, idempotency_key, trace_id, payload
            FROM CanonicalIncidentSourceEventsV2
            WHERE incident_id = @incident_id
            ORDER BY source_event_id
            LIMIT @limit OFFSET @offset""",
            params={
                "incident_id": candidate.incident_id,
                "limit": MAX_INCIDENT_SOURCE_EVENTS + 1,
                "offset": 0,
            },
            type_spec={
                "incident_id": "STRING",
                "limit": "INT64",
                "offset": "INT64",
            },
        )
        persisted_associations = _bounded_page(
            source_rows,
            lambda row: self._parse_source_association_row(
                row,
                expected_incident_id=candidate.incident_id,
            ),
            boundary="migration-source-replay",
        )
        expected_associations = tuple(
            sorted(associations, key=lambda item: item.source_event_id)
        )
        if persisted_associations != expected_associations:
            raise IncidentRepositoryError(
                "persisted migration source provenance is inconsistent"
            )

        audit_row = read_one(
            reader,
            "CanonicalIncidentAuditV2",
            _AUDIT_COLUMNS,
            (candidate.incident_id, 0),
        )
        if audit_row is None:
            raise IncidentRepositoryError("persisted migration audit is missing")
        audit = self._parse_audit_row(
            tuple(audit_row[:7]) + (audit_row[-1],),
            expected_incident_id=candidate.incident_id,
        )
        if (
            audit.from_status is not None
            or audit.to_status is not IncidentStatus.DETECTED
            or audit.revision != 0
            or audit.idempotency_key != scope[2]
            or audit.actor != actor
            or audit.reason != reason
            or audit.trace_id != trace_id
        ):
            raise IncidentRepositoryError(
                "persisted migration audit is inconsistent"
            )

        active_rows = tuple(
            execute_sql(
                reader,
                """-- telco-cloud:migration-active-keys-for-incident
                SELECT key_hash, key_kind
                FROM CanonicalIncidentActiveKeysV2
                WHERE incident_id = @incident_id
                LIMIT @active_key_limit""",
                params={
                    "incident_id": candidate.incident_id,
                    "active_key_limit": MAX_INCIDENT_SOURCE_EVENTS + 2,
                },
                type_spec={
                    "incident_id": "STRING",
                    "active_key_limit": "INT64",
                },
            )
        )
        if len(active_rows) > MAX_INCIDENT_SOURCE_EVENTS + 1:
            raise IncidentRepositoryError(
                "persisted migration active-key capacity exceeded"
            )
        persisted_active_keys = tuple(
            sorted((str(row[0]), str(row[1])) for row in active_rows)
        )
        expected_active_keys = (
            () if current.status in SETTLED_STATUSES else active_keys
        )
        if persisted_active_keys != expected_active_keys:
            raise IncidentRepositoryError(
                "persisted migration active key is inconsistent"
            )

    def _require_current_tx(self, reader: Any, incident_id: str) -> Incident:
        current = self._get_tx(reader, incident_id)
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

    def _find_active_for_keys_tx(
        self,
        reader: Any,
        keys: tuple[_ActiveKey, ...],
        *,
        requested_incident_id: str,
    ) -> Incident | None:
        if not keys:
            return None
        expected_kinds = dict(keys)
        rows = execute_sql(
            reader,
            """-- telco-cloud:active-keys-by-hash
            SELECT key_hash, key_kind, incident_id
            FROM CanonicalIncidentActiveKeysV2
            WHERE key_hash IN UNNEST(@key_hashes)
            ORDER BY key_hash""",
            params={"key_hashes": tuple(expected_kinds)},
            type_spec={"key_hashes": "STRING_ARRAY"},
        )
        incident_ids: set[str] = set()
        seen_hashes: set[str] = set()
        for row in rows:
            key_hash = str(row[0])
            if key_hash in seen_hashes or key_hash not in expected_kinds:
                raise IncidentRepositoryError("active-key index is inconsistent")
            seen_hashes.add(key_hash)
            if str(row[1]) != expected_kinds[key_hash]:
                raise IncidentRepositoryError("active-key hash collision detected")
            incident_ids.add(str(row[2]))
        if not incident_ids:
            return None
        if len(incident_ids) != 1:
            raise IncidentCorrelationConflictError(
                requested_incident_id,
                tuple(incident_ids),
            )
        incident_id = next(iter(incident_ids))
        incident = self._get_tx(reader, incident_id)
        if incident is None or incident.status in SETTLED_STATUSES:
            raise IncidentRepositoryError("active-key index is inconsistent")
        return incident

    @staticmethod
    def _replay(
        reader: Any, scope: _IdempotencyScope, request_fingerprint: str
    ) -> Incident | None:
        row = read_one(
            reader,
            "CanonicalIncidentIdempotencyV2",
            (
                "operation",
                "requested_incident_id",
                "idempotency_key",
                "request_fingerprint",
                "result_incident_id",
                "result_payload",
            ),
            scope,
        )
        if row is None:
            return None
        if tuple(row[:3]) != scope:
            raise IncidentRepositoryError("persisted idempotency scope mismatch")
        if row[3] != request_fingerprint:
            raise IdempotencyConflictError(scope[2])
        result = parse_json_model(Incident, row[5])
        if result.incident_id != row[4]:
            raise IncidentRepositoryError("persisted idempotency result mismatch")
        assert_safe(result, boundary="incident-idempotency-replay")
        return result

    @staticmethod
    def _audit_event(
        incident: Incident,
        *,
        from_status: IncidentStatus | None,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> IncidentAuditEvent:
        digest = hashlib.sha256(
            f"{incident.incident_id}\0{idempotency_key}\0{request_fingerprint}".encode(
                "utf-8"
            )
        ).hexdigest()
        return IncidentAuditEvent(
            event_id=f"audit-{digest[:32]}",
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
    def _incident_values(incident: Incident) -> tuple[object, ...]:
        return (
            incident.incident_id,
            incident.correlation_key,
            incident.schema_version,
            incident.technology.value,
            incident.status.value,
            incident.severity.value,
            incident.revision,
            incident.trace_id,
            incident.detected_at,
            incident.created_at,
            incident.updated_at,
            json_object(json_safe(incident)),
        )

    def _insert_incident_tx(self, transaction: Any, incident: Incident) -> None:
        transaction.insert(
            "CanonicalIncidentsV2",
            columns=_INCIDENT_COLUMNS,
            values=(self._incident_values(incident),),
        )

    def _update_incident_tx(self, transaction: Any, incident: Incident) -> None:
        transaction.update(
            "CanonicalIncidentsV2",
            columns=_INCIDENT_COLUMNS,
            values=(self._incident_values(incident),),
        )

    def _register_source_events_tx(
        self,
        transaction: Any,
        *,
        incident_id: str,
        source_event_ids: Sequence[str],
        registered_at: datetime,
        actor: str,
        reason: str,
        idempotency_key: str,
        trace_id: str,
    ) -> tuple[SourceEventAssociation, ...]:
        incoming = tuple(sorted(set(source_event_ids)))
        self._require_source_event_owners_tx(
            transaction,
            incident_id=incident_id,
            source_event_ids=incoming,
        )
        source_rows = execute_sql(
            transaction,
            """-- telco-cloud:source-associations
            SELECT incident_id, source_event_id, registered_at, actor,
                   reason, idempotency_key, trace_id, payload
            FROM CanonicalIncidentSourceEventsV2
            WHERE incident_id = @incident_id
            ORDER BY source_event_id
            LIMIT @limit OFFSET @offset""",
            params={
                "incident_id": incident_id,
                "limit": MAX_INCIDENT_SOURCE_EVENTS + 1,
                "offset": 0,
            },
            type_spec={
                "incident_id": "STRING",
                "limit": "INT64",
                "offset": "INT64",
            },
        )
        persisted = _bounded_page(
            source_rows,
            lambda row: self._parse_source_association_row(
                row,
                expected_incident_id=incident_id,
            ),
            boundary="source-association-registration",
        )
        if len(persisted) > MAX_INCIDENT_SOURCE_EVENTS:
            raise IncidentRepositoryError(
                "persisted source association capacity exceeded"
            )
        existing_by_id = {
            association.source_event_id: association
            for association in persisted
        }
        if len(set(existing_by_id) | set(incoming)) > MAX_INCIDENT_SOURCE_EVENTS:
            raise ValueError(
                "incident source-event association capacity exceeded 1000"
            )
        associations: list[SourceEventAssociation] = []
        insert_values: list[tuple[object, ...]] = []
        for source_event_id in sorted(incoming):
            existing = existing_by_id.get(source_event_id)
            if existing is not None:
                associations.append(existing)
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
            assert_safe(association, boundary=incident_id)
            insert_values.append(
                (
                    incident_id,
                    source_event_id,
                    registered_at,
                    actor,
                    reason,
                    idempotency_key,
                    trace_id,
                    json_object(json_safe(association)),
                )
            )
            associations.append(association)
        if insert_values:
            transaction.insert(
                "CanonicalIncidentSourceEventsV2",
                columns=_SOURCE_COLUMNS,
                values=tuple(insert_values),
            )
        return tuple(associations)

    def _insert_imported_source_events_tx(
        self,
        transaction: Any,
        *,
        incident_id: str,
        associations: Sequence[SourceEventAssociation],
    ) -> None:
        if len(associations) > MAX_INCIDENT_SOURCE_EVENTS:
            raise ValueError("migration source association capacity exceeded")
        values = tuple(
            (
                incident_id,
                association.source_event_id,
                association.registered_at,
                association.actor,
                association.reason,
                association.idempotency_key,
                association.trace_id,
                json_object(json_safe(association)),
            )
            for association in sorted(
                associations,
                key=lambda item: item.source_event_id,
            )
        )
        if values:
            transaction.insert(
                "CanonicalIncidentSourceEventsV2",
                columns=_SOURCE_COLUMNS,
                values=values,
            )

    @staticmethod
    def _source_event_owner_tx(
        reader: Any, source_event_id: str
    ) -> str | None:
        rows = tuple(
            execute_sql(
                reader,
                """-- telco-cloud:source-event-owner
                SELECT incident_id FROM CanonicalIncidentSourceEventsV2
                WHERE source_event_id = @source_event_id
                ORDER BY incident_id LIMIT 2""",
                params={"source_event_id": source_event_id},
                type_spec={"source_event_id": "STRING"},
            )
        )
        if len(rows) > 1:
            raise IncidentRepositoryError(
                "persisted source event has multiple Incident owners"
            )
        return None if not rows else str(rows[0][0])

    def _require_source_event_owners_tx(
        self,
        reader: Any,
        *,
        incident_id: str,
        source_event_ids: Sequence[str],
    ) -> None:
        requested = tuple(sorted(set(source_event_ids)))
        if not requested:
            return
        rows = execute_sql(
            reader,
            """-- telco-cloud:source-event-owners
            SELECT source_event_id, incident_id
            FROM CanonicalIncidentSourceEventsV2
            WHERE source_event_id IN UNNEST(@source_event_ids)
            ORDER BY source_event_id, incident_id""",
            params={"source_event_ids": requested},
            type_spec={"source_event_ids": "STRING_ARRAY"},
        )
        owners: dict[str, str] = {}
        for row in rows:
            source_event_id = str(row[0])
            owner_incident_id = str(row[1])
            if source_event_id not in requested:
                raise IncidentRepositoryError(
                    "source-event owner query returned an unexpected key"
                )
            previous = owners.setdefault(source_event_id, owner_incident_id)
            if previous != owner_incident_id:
                raise IncidentRepositoryError(
                    "persisted source event has multiple Incident owners"
                )
            if owner_incident_id != incident_id:
                raise SourceEventOwnershipConflictError(
                    source_event_id,
                    owner_incident_id,
                    incident_id,
                )

    def _acquire_active_keys_tx(
        self,
        transaction: Any,
        *,
        incident_id: str,
        keys: tuple[_ActiveKey, ...],
        registered_at: datetime,
    ) -> None:
        if not keys:
            return
        expected = dict(keys)
        rows = execute_sql(
            transaction,
            """-- telco-cloud:active-keys-by-hash
            SELECT key_hash, key_kind, incident_id
            FROM CanonicalIncidentActiveKeysV2
            WHERE key_hash IN UNNEST(@key_hashes)
            ORDER BY key_hash""",
            params={"key_hashes": tuple(expected)},
            type_spec={"key_hashes": "STRING_ARRAY"},
        )
        existing: set[str] = set()
        for row in rows:
            key_hash = str(row[0])
            if key_hash in existing or key_hash not in expected:
                raise IncidentRepositoryError("active-key index is inconsistent")
            existing.add(key_hash)
            if str(row[1]) != expected[key_hash]:
                raise IncidentRepositoryError("active-key hash collision detected")
            if str(row[2]) != incident_id:
                raise ActiveIncidentConflictError(incident_id, str(row[2]))
        missing = tuple(
            (key_hash, key_kind, incident_id, registered_at)
            for key_hash, key_kind in keys
            if key_hash not in existing
        )
        if missing:
            transaction.insert(
                "CanonicalIncidentActiveKeysV2",
                columns=_ACTIVE_KEY_COLUMNS,
                values=missing,
            )

    @staticmethod
    def _insert_audit_tx(transaction: Any, event: IncidentAuditEvent) -> None:
        transaction.insert(
            "CanonicalIncidentAuditV2",
            columns=_AUDIT_COLUMNS,
            values=(
                (
                    event.incident_id,
                    event.revision,
                    event.event_id,
                    None if event.from_status is None else event.from_status.value,
                    event.to_status.value,
                    event.trace_id,
                    event.occurred_at,
                    commit_timestamp(),
                    json_object(json_safe(event)),
                ),
            ),
        )

    @staticmethod
    def _insert_idempotency_tx(
        transaction: Any,
        *,
        scope: _IdempotencyScope,
        request_fingerprint: str,
        result: Incident,
        created_at: datetime,
    ) -> None:
        transaction.insert(
            "CanonicalIncidentIdempotencyV2",
            columns=_IDEMPOTENCY_COLUMNS,
            values=(
                (
                    scope[0],
                    scope[1],
                    scope[2],
                    request_fingerprint,
                    result.incident_id,
                    json_object(json_safe(result)),
                    created_at,
                ),
            ),
        )

    def _commit_create_tx(
        self,
        transaction: Any,
        incident: Incident,
        *,
        scope: _IdempotencyScope,
        request_fingerprint: str,
        actor: str,
        reason: str,
        trace_id: str,
        occurred_at: datetime,
    ) -> tuple[SourceEventAssociation, ...]:
        event = self._audit_event(
            incident,
            from_status=None,
            idempotency_key=scope[2],
            actor=actor,
            reason=reason,
            trace_id=trace_id,
            request_fingerprint=request_fingerprint,
            occurred_at=occurred_at,
        )
        self._insert_incident_tx(transaction, incident)
        associations = self._register_source_events_tx(
            transaction,
            incident_id=incident.incident_id,
            source_event_ids=incident.source_event_ids,
            registered_at=occurred_at,
            actor=actor,
            reason=reason,
            idempotency_key=scope[2],
            trace_id=trace_id,
        )
        self._acquire_active_keys_tx(
            transaction,
            incident_id=incident.incident_id,
            keys=_candidate_active_keys(incident),
            registered_at=occurred_at,
        )
        self._insert_audit_tx(transaction, event)
        self._insert_idempotency_tx(
            transaction,
            scope=scope,
            request_fingerprint=request_fingerprint,
            result=incident,
            created_at=occurred_at,
        )
        return associations

    def _associated_source_ids_tx(
        self, reader: Any, incident_id: str
    ) -> tuple[str, ...]:
        rows = execute_sql(
            reader,
            """-- telco-cloud:source-events-for-incident
            SELECT source_event_id FROM CanonicalIncidentSourceEventsV2
            WHERE incident_id = @incident_id ORDER BY source_event_id
            LIMIT @source_event_limit""",
            params={
                "incident_id": incident_id,
                "source_event_limit": MAX_INCIDENT_SOURCE_EVENTS + 1,
            },
            type_spec={
                "incident_id": "STRING",
                "source_event_limit": "INT64",
            },
        )
        result = tuple(str(row[0]) for row in rows)
        if len(result) > MAX_INCIDENT_SOURCE_EVENTS:
            raise IncidentRepositoryError(
                "persisted source association capacity exceeded"
            )
        return result

    def _release_active_keys_tx(self, transaction: Any, incident_id: str) -> None:
        rows = execute_sql(
            transaction,
            """-- telco-cloud:active-keys-for-incident
            SELECT key_hash FROM CanonicalIncidentActiveKeysV2
            WHERE incident_id = @incident_id
            LIMIT @active_key_limit""",
            params={
                "incident_id": incident_id,
                "active_key_limit": MAX_INCIDENT_SOURCE_EVENTS + 2,
            },
            type_spec={
                "incident_id": "STRING",
                "active_key_limit": "INT64",
            },
        )
        keys = tuple((str(row[0]),) for row in rows)
        if len(keys) > MAX_INCIDENT_SOURCE_EVENTS + 1:
            raise IncidentRepositoryError("persisted active-key capacity exceeded")
        if keys:
            transaction.delete("CanonicalIncidentActiveKeysV2", keyset(*keys))

    def _commit_transition_tx(
        self,
        transaction: Any,
        current: Incident,
        successor: Incident,
        *,
        scope: _IdempotencyScope,
        request_fingerprint: str,
        actor: str,
        reason: str,
        trace_id: str,
        occurred_at: datetime,
    ) -> None:
        was_settled = current.status in SETTLED_STATUSES
        is_settled = successor.status in SETTLED_STATUSES
        # Aggregate source_event_ids are append-only domain state. Persist their
        # immutable provenance in the same transaction before any Incident,
        # audit, idempotency, or active-key mutation can commit.
        self._register_source_events_tx(
            transaction,
            incident_id=successor.incident_id,
            source_event_ids=successor.source_event_ids,
            registered_at=occurred_at,
            actor=actor,
            reason=reason,
            idempotency_key=scope[2],
            trace_id=trace_id,
        )
        if was_settled and not is_settled:
            # Spanner mutations are buffered until commit, so explicitly union
            # incoming aggregate IDs with associations read from storage.
            source_ids = tuple(
                sorted(
                    set(
                        self._associated_source_ids_tx(
                            transaction, successor.incident_id
                        )
                    )
                    | set(successor.source_event_ids)
                )
            )
            keys = list(_active_key("source", item) for item in source_ids)
            if successor.correlation_key is not None:
                keys.append(_active_key("correlation", successor.correlation_key))
            active = self._find_active_for_keys_tx(
                transaction,
                tuple(keys),
                requested_incident_id=successor.incident_id,
            )
            if active is not None and active.incident_id != successor.incident_id:
                raise ActiveIncidentConflictError(
                    successor.incident_id, active.incident_id
                )
            self._acquire_active_keys_tx(
                transaction,
                incident_id=successor.incident_id,
                keys=tuple(keys),
                registered_at=occurred_at,
            )
        elif not was_settled and is_settled:
            self._release_active_keys_tx(transaction, successor.incident_id)
        elif not is_settled:
            self._acquire_active_keys_tx(
                transaction,
                incident_id=successor.incident_id,
                keys=tuple(
                    _active_key("source", item)
                    for item in successor.source_event_ids
                ),
                registered_at=occurred_at,
            )

        event = self._audit_event(
            successor,
            from_status=current.status,
            idempotency_key=scope[2],
            actor=actor,
            reason=reason,
            trace_id=trace_id,
            request_fingerprint=request_fingerprint,
            occurred_at=occurred_at,
        )
        self._update_incident_tx(transaction, successor)
        self._insert_audit_tx(transaction, event)
        self._insert_idempotency_tx(
            transaction,
            scope=scope,
            request_fingerprint=request_fingerprint,
            result=successor,
            created_at=occurred_at,
        )


__all__ = ["MAX_REPOSITORY_BATCH_BYTES", "SpannerIncidentRepository"]
