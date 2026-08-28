"""One-time, replay-safe Canonical Incident migration primitives.

The migration path is intentionally not a dual writer.  It exports one checksummed
snapshot from an existing IncidentRepository and imports each eligible
Incident with a stable idempotency key.  Ambiguous legacy lifecycle or source
ownership is quarantined for an operator instead of being guessed.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from telco_domain import (
    ACTIVE_STATUSES,
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_SERIALIZED_BYTES,
    Incident,
    IncidentSnapshotImportResult,
    IncidentStatus,
    SourceEventAssociation,
    assert_model_safe,
)
from telco_domain.ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentAlreadyExistsError,
    IncidentCorrelationConflictError,
    IncidentRepository,
    SourceEventOwnershipConflictError,
    UnsafeIncidentWriteError,
)


MIGRATION_SCHEMA_VERSION = "1.0"
MAX_MIGRATION_INCIDENTS = 1_000
MAX_MIGRATION_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_MIGRATION_DEPTH = MAX_CONTRACT_DEPTH + 6
_CHECKSUM_PATTERN = r"^[0-9a-f]{64}$"


class MigrationError(RuntimeError):
    """Base class for safe, deterministic migration failures."""


class MigrationBundleError(MigrationError):
    """The bundle is malformed, unsafe, oversized, or has been changed."""


class MigrationDependencyError(MigrationError):
    """A target dependency failed and the batch should be retried."""


class QuarantineCode(str, Enum):
    UNSUPPORTED_LIFECYCLE = "UNSUPPORTED_LIFECYCLE"
    LEGACY_REQUIRES_MAPPING = "LEGACY_REQUIRES_MAPPING"
    AMBIGUOUS_SOURCE_OWNERSHIP = "AMBIGUOUS_SOURCE_OWNERSHIP"
    AMBIGUOUS_CORRELATION_OWNERSHIP = "AMBIGUOUS_CORRELATION_OWNERSHIP"
    MISSING_SOURCE_PROVENANCE = "MISSING_SOURCE_PROVENANCE"
    TARGET_CONFLICT = "TARGET_CONFLICT"


class IncidentSnapshotImporter(Protocol):
    async def import_detected_snapshot(
        self,
        incident: Incident,
        associations: Sequence[SourceEventAssociation],
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        trace_id: str,
    ) -> IncidentSnapshotImportResult: ...


class MigrationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
    )


class MigrationEntry(MigrationModel):
    incident: Incident
    associations: Annotated[
        tuple[SourceEventAssociation, ...],
        Field(max_length=MAX_MIGRATION_INCIDENTS),
    ] = ()

    @model_validator(mode="after")
    def validate_associations(self) -> "MigrationEntry":
        source_ids: set[str] = set()
        for association in self.associations:
            if association.incident_id != self.incident.incident_id:
                raise ValueError("association incident binding mismatch")
            if association.source_event_id in source_ids:
                raise ValueError("duplicate source association")
            source_ids.add(association.source_event_id)
        ordered_ids = tuple(item.source_event_id for item in self.associations)
        if ordered_ids != tuple(sorted(ordered_ids)):
            raise ValueError("source associations must be sorted by source_event_id")
        return self


class MigrationBundle(MigrationModel):
    schema_version: Literal["1.0"] = MIGRATION_SCHEMA_VERSION
    source_profile: Annotated[str, Field(min_length=1, max_length=128)]
    exported_at: datetime
    entries: Annotated[
        tuple[MigrationEntry, ...],
        Field(max_length=MAX_MIGRATION_INCIDENTS),
    ]
    checksum_sha256: Annotated[str, Field(pattern=_CHECKSUM_PATTERN)]

    @field_validator("source_profile")
    @classmethod
    def normalize_source_profile(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_profile must not be blank")
        return normalized

    @field_validator("exported_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exported_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def unique_incidents(self) -> "MigrationBundle":
        identifiers = tuple(entry.incident.incident_id for entry in self.entries)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate incident entry")
        if any(entry.incident.updated_at > self.exported_at for entry in self.entries):
            raise ValueError("incident snapshot is newer than exported_at")
        if any(
            association.registered_at > self.exported_at
            for entry in self.entries
            for association in entry.associations
        ):
            raise ValueError("source association is newer than exported_at")
        return self


class QuarantineItem(MigrationModel):
    entry_index: Annotated[int, Field(ge=0, lt=MAX_MIGRATION_INCIDENTS)]
    incident_id: Annotated[str, Field(min_length=1, max_length=256)]
    code: QuarantineCode


class MigrationReport(MigrationModel):
    dry_run: bool
    total: int
    eligible: int
    imported: int
    replayed: int
    quarantined: int
    quarantine_counts: Mapping[str, int] = Field(default_factory=dict)
    quarantine_items: tuple[QuarantineItem, ...] = ()


def _json_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        if maximum > MAX_MIGRATION_DEPTH:
            return maximum
        if isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            stack.extend((item, depth + 1) for item in current)
    return maximum


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MigrationBundleError("migration payload is not canonical JSON") from None
    return encoded


def _validate_canonical_item(value: object) -> None:
    model_dump = getattr(value, "model_dump", None)
    payload = (
        model_dump(mode="json", round_trip=True)
        if callable(model_dump)
        else value
    )
    try:
        assert_model_safe(payload)
        if _json_depth(payload) > MAX_CONTRACT_DEPTH:
            raise MigrationBundleError("migration entry exceeds canonical depth")
        if len(_canonical_bytes(payload)) > MAX_CONTRACT_SERIALIZED_BYTES:
            raise MigrationBundleError("migration entry exceeds canonical size")
    except MigrationBundleError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MigrationBundleError("migration entry is invalid or unsafe") from None


def _unsigned_payload(
    *,
    source_profile: str,
    exported_at: datetime,
    entries: Sequence[MigrationEntry],
) -> dict[str, object]:
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "source_profile": source_profile,
        "exported_at": exported_at.astimezone(UTC).isoformat(),
        "entries": [entry.model_dump(mode="json", round_trip=True) for entry in entries],
    }


def _checksum(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        b"telco-canonical-migration-v1\0" + _canonical_bytes(payload)
    ).hexdigest()


def create_migration_bundle(
    *,
    source_profile: str,
    exported_at: datetime,
    entries: Sequence[MigrationEntry],
) -> MigrationBundle:
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise MigrationBundleError("exported_at must be timezone-aware")
    normalized_time = exported_at.astimezone(UTC)
    if not isinstance(source_profile, str) or not source_profile.strip():
        raise MigrationBundleError("source_profile must not be blank")
    normalized_profile = source_profile.strip()
    if not isinstance(entries, Sequence) or isinstance(
        entries, (str, bytes, bytearray)
    ):
        raise MigrationBundleError("migration entries must be a bounded sequence")
    try:
        entry_count = len(entries)
    except (TypeError, OverflowError):
        raise MigrationBundleError(
            "migration entries must be a bounded sequence"
        ) from None
    if entry_count > MAX_MIGRATION_INCIDENTS:
        raise MigrationBundleError("migration bundle exceeds 1000 incidents")
    try:
        normalized_entries: list[MigrationEntry] = []
        cumulative_entry_bytes = 0
        for index in range(entry_count):
            entry = MigrationEntry.model_validate(entries[index])
            _validate_canonical_item(entry.incident)
            for association in entry.associations:
                _validate_canonical_item(association)
            cumulative_entry_bytes += len(
                _canonical_bytes(entry.model_dump(mode="json", round_trip=True))
            ) + 1
            if cumulative_entry_bytes > MAX_MIGRATION_BUNDLE_BYTES:
                raise MigrationBundleError("migration bundle exceeds 16 MiB")
            normalized_entries.append(entry)
        entry_tuple = tuple(normalized_entries)
        payload = _unsigned_payload(
            source_profile=normalized_profile,
            exported_at=normalized_time,
            entries=entry_tuple,
        )
        assert_model_safe(payload)
        if _json_depth(payload) > MAX_MIGRATION_DEPTH:
            raise MigrationBundleError("migration bundle exceeds depth limit")
        bundle = MigrationBundle(
            source_profile=normalized_profile,
            exported_at=normalized_time,
            entries=entry_tuple,
            checksum_sha256=_checksum(payload),
        )
        encoded = _canonical_bytes(bundle.model_dump(mode="json", round_trip=True))
    except MigrationBundleError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MigrationBundleError("migration bundle is invalid or unsafe") from None
    if len(encoded) + 1 > MAX_MIGRATION_BUNDLE_BYTES:
        raise MigrationBundleError("migration bundle exceeds 16 MiB")
    return bundle


def validate_migration_bundle(bundle: MigrationBundle) -> None:
    payload = _unsigned_payload(
        source_profile=bundle.source_profile,
        exported_at=bundle.exported_at,
        entries=bundle.entries,
    )
    try:
        for entry in bundle.entries:
            _validate_canonical_item(entry.incident)
            for association in entry.associations:
                _validate_canonical_item(association)
        assert_model_safe(payload)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MigrationBundleError("migration bundle is invalid or unsafe") from None
    if _json_depth(payload) > MAX_MIGRATION_DEPTH:
        raise MigrationBundleError("migration bundle exceeds depth limit")
    if _checksum(payload) != bundle.checksum_sha256:
        raise MigrationBundleError("migration bundle checksum mismatch")
    if len(dump_migration_bundle(bundle, validate=False)) > MAX_MIGRATION_BUNDLE_BYTES:
        raise MigrationBundleError("migration bundle exceeds 16 MiB")


def dump_migration_bundle(
    bundle: MigrationBundle,
    *,
    validate: bool = True,
) -> bytes:
    if validate:
        validate_migration_bundle(bundle)
    return _canonical_bytes(bundle.model_dump(mode="json", round_trip=True)) + b"\n"


def _reject_constant(value: str) -> None:
    del value
    raise ValueError("non-finite JSON is forbidden")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON is forbidden")
    return parsed


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_migration_bundle(data: bytes) -> MigrationBundle:
    if len(data) > MAX_MIGRATION_BUNDLE_BYTES:
        raise MigrationBundleError("migration bundle exceeds 16 MiB")
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
        )
        if _json_depth(payload) > MAX_MIGRATION_DEPTH:
            raise MigrationBundleError("migration bundle exceeds depth limit")
        assert_model_safe(payload)
        bundle = MigrationBundle.model_validate(payload)
        validate_migration_bundle(bundle)
        return bundle
    except MigrationBundleError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MigrationBundleError("migration bundle is invalid or unsafe") from None


async def export_migration_bundle(
    repository: IncidentRepository,
    *,
    source_profile: str,
    exported_at: datetime,
) -> MigrationBundle:
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise MigrationBundleError("exported_at must be timezone-aware")
    if not isinstance(source_profile, str) or not source_profile.strip():
        raise MigrationBundleError("source_profile must not be blank")
    normalized_profile = source_profile.strip()
    normalized_time = exported_at.astimezone(UTC)
    empty_bundle_payload = {
        **_unsigned_payload(
            source_profile=normalized_profile,
            exported_at=normalized_time,
            entries=(),
        ),
        "checksum_sha256": "0" * 64,
    }
    running_bytes = len(_canonical_bytes(empty_bundle_payload)) + 1
    entries: list[MigrationEntry] = []
    offset = 0
    while offset < MAX_MIGRATION_INCIDENTS:
        page = await repository.list(
            limit=1,
            offset=offset,
        )
        if not page:
            break
        if len(page) != 1:
            raise MigrationBundleError("migration source violated page limit")
        incident = page[0]
        _validate_canonical_item(incident)

        associations: list[SourceEventAssociation] = []
        association_offset = 0
        seen_source_ids: set[str] = set()
        empty_entry = MigrationEntry(incident=incident)
        projected_entry_bytes = len(
            _canonical_bytes(empty_entry.model_dump(mode="json", round_trip=True))
        )
        projected_bundle_bytes = (
            running_bytes
            + projected_entry_bytes
            + (1 if entries else 0)
        )
        if projected_bundle_bytes > MAX_MIGRATION_BUNDLE_BYTES:
            raise MigrationBundleError("migration export exceeds 16 MiB")

        while association_offset < MAX_MIGRATION_INCIDENTS:
            association_limit = min(
                25,
                MAX_MIGRATION_INCIDENTS - association_offset,
            )
            association_page = await repository.source_event_associations(
                incident.incident_id,
                limit=association_limit,
                offset=association_offset,
            )
            if len(association_page) > association_limit:
                raise MigrationBundleError(
                    "migration source violated association page limit"
                )
            if not association_page:
                break
            for association in association_page:
                _validate_canonical_item(association)
                if association.source_event_id in seen_source_ids:
                    raise MigrationBundleError(
                        "migration source returned duplicate association"
                    )
                if (
                    associations
                    and association.source_event_id
                    <= associations[-1].source_event_id
                ):
                    raise MigrationBundleError(
                        "migration source returned unordered associations"
                    )
                seen_source_ids.add(association.source_event_id)
                association_bytes = len(
                    _canonical_bytes(
                        association.model_dump(mode="json", round_trip=True)
                    )
                )
                projected_entry_bytes += association_bytes
                if associations:
                    projected_entry_bytes += 1
                projected_bundle_bytes = (
                    running_bytes
                    + projected_entry_bytes
                    + (1 if entries else 0)
                )
                if projected_bundle_bytes > MAX_MIGRATION_BUNDLE_BYTES:
                    raise MigrationBundleError("migration export exceeds 16 MiB")
                associations.append(association)
            association_offset += len(association_page)
            if len(association_page) < association_limit:
                break
        if association_offset == MAX_MIGRATION_INCIDENTS:
            extra_association = await repository.source_event_associations(
                incident.incident_id,
                limit=1,
                offset=MAX_MIGRATION_INCIDENTS,
            )
            if extra_association:
                raise MigrationBundleError(
                    "migration export exceeds 1000 source associations"
                )

        entry = MigrationEntry(
            incident=incident,
            associations=tuple(associations),
        )
        actual_entry_bytes = len(
            _canonical_bytes(entry.model_dump(mode="json", round_trip=True))
        )
        running_bytes += actual_entry_bytes + (1 if entries else 0)
        if running_bytes > MAX_MIGRATION_BUNDLE_BYTES:
            raise MigrationBundleError("migration export exceeds 16 MiB")
        entries.append(entry)
        offset += 1

    if len(entries) == MAX_MIGRATION_INCIDENTS:
        extra = await repository.list(limit=1, offset=MAX_MIGRATION_INCIDENTS)
        if extra:
            raise MigrationBundleError("migration export exceeds 1000 incidents")
    return create_migration_bundle(
        source_profile=normalized_profile,
        exported_at=normalized_time,
        entries=entries,
    )


def _legacy_marker(incident: Incident) -> bool:
    metadata = incident.model_metadata
    return any(key in metadata for key in ("legacy_source", "legacy_id"))


def _quarantine_plan(bundle: MigrationBundle) -> dict[str, QuarantineCode]:
    plan: dict[str, QuarantineCode] = {}
    owners: dict[str, set[str]] = {}
    for entry in bundle.entries:
        incident_id = entry.incident.incident_id
        source_ids = set(entry.incident.source_event_ids)
        source_ids.update(item.source_event_id for item in entry.associations)
        for source_event_id in source_ids:
            owners.setdefault(source_event_id, set()).add(incident_id)
    ambiguous_incidents = {
        incident_id
        for incident_ids in owners.values()
        if len(incident_ids) > 1
        for incident_id in incident_ids
    }
    correlation_owners: dict[str, set[str]] = {}
    for entry in bundle.entries:
        incident = entry.incident
        if (
            incident.status in ACTIVE_STATUSES
            and incident.correlation_key is not None
        ):
            correlation_owners.setdefault(incident.correlation_key, set()).add(
                incident.incident_id
            )
    ambiguous_correlations = {
        incident_id
        for incident_ids in correlation_owners.values()
        if len(incident_ids) > 1
        for incident_id in incident_ids
    }
    for entry in bundle.entries:
        incident = entry.incident
        if incident.incident_id in ambiguous_incidents:
            plan[incident.incident_id] = QuarantineCode.AMBIGUOUS_SOURCE_OWNERSHIP
        elif incident.incident_id in ambiguous_correlations:
            plan[incident.incident_id] = (
                QuarantineCode.AMBIGUOUS_CORRELATION_OWNERSHIP
            )
        elif _legacy_marker(incident):
            plan[incident.incident_id] = QuarantineCode.LEGACY_REQUIRES_MAPPING
        elif incident.status is not IncidentStatus.DETECTED or incident.revision != 0:
            plan[incident.incident_id] = QuarantineCode.UNSUPPORTED_LIFECYCLE
        elif not set(incident.source_event_ids).issubset(
            {item.source_event_id for item in entry.associations}
        ):
            plan[incident.incident_id] = QuarantineCode.MISSING_SOURCE_PROVENANCE
    return plan


def _candidate(entry: MigrationEntry) -> Incident:
    candidate = Incident.model_validate(
        entry.incident.model_dump(mode="python", round_trip=True)
    )
    _validate_canonical_item(candidate)
    return candidate


def _idempotency_key(source_profile: str, candidate: Incident) -> str:
    digest = hashlib.sha256(
        b"telco-canonical-import-v1\0"
        + source_profile.encode("utf-8")
        + b"\0"
        + _canonical_bytes(candidate.model_dump(mode="json", round_trip=True))
    ).hexdigest()
    return f"migration-v1-{digest}"


async def import_migration_bundle(
    bundle: MigrationBundle,
    repository: IncidentSnapshotImporter | None,
    *,
    dry_run: bool,
) -> MigrationReport:
    validate_migration_bundle(bundle)
    if not dry_run and repository is None:
        raise ValueError("repository is required for a live import")
    if not dry_run and not callable(
        getattr(repository, "import_detected_snapshot", None)
    ):
        raise MigrationDependencyError(
            "migration target does not support provenance-preserving import"
        )
    quarantine = _quarantine_plan(bundle)
    imported = 0
    replayed = 0

    for entry in bundle.entries:
        incident_id = entry.incident.incident_id
        if incident_id in quarantine:
            continue
        candidate = _candidate(entry)
        if dry_run:
            continue
        assert repository is not None
        key = _idempotency_key(bundle.source_profile, candidate)
        try:
            outcome = await repository.import_detected_snapshot(
                candidate,
                entry.associations,
                idempotency_key=key,
                actor="canonical-migration",
                reason="one-time canonical incident import",
                trace_id=candidate.trace_id,
            )
            if not isinstance(outcome, IncidentSnapshotImportResult):
                raise MigrationBundleError("migration target outcome is invalid")
            if type(outcome.replayed) is not bool:
                raise MigrationBundleError("migration target outcome is invalid")
            try:
                committed = Incident.model_validate(outcome.incident)
                _validate_canonical_item(committed)
            except MigrationBundleError:
                raise
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise MigrationBundleError(
                    "migration target outcome is invalid"
                ) from None
            if committed != candidate:
                raise MigrationBundleError("migration target snapshot mismatch")
            if outcome.replayed:
                replayed += 1
            else:
                imported += 1
        except (
            ActiveIncidentConflictError,
            IdempotencyConflictError,
            IncidentAlreadyExistsError,
            IncidentCorrelationConflictError,
            SourceEventOwnershipConflictError,
        ):
            quarantine[incident_id] = QuarantineCode.TARGET_CONFLICT
        except UnsafeIncidentWriteError:
            raise MigrationBundleError(
                "migration target rejected canonical safety boundary"
            ) from None
        except MigrationBundleError:
            raise
        except Exception:
            raise MigrationDependencyError("migration target dependency failed") from None

    quarantine_items = tuple(
        QuarantineItem(
            entry_index=index,
            incident_id=entry.incident.incident_id,
            code=quarantine[entry.incident.incident_id],
        )
        for index, entry in enumerate(bundle.entries)
        if entry.incident.incident_id in quarantine
    )
    counts: Counter[str] = Counter(item.code.value for item in quarantine_items)
    return MigrationReport(
        dry_run=dry_run,
        total=len(bundle.entries),
        eligible=len(bundle.entries) - len(quarantine),
        imported=imported,
        replayed=replayed,
        quarantined=len(quarantine),
        quarantine_counts=dict(sorted(counts.items())),
        quarantine_items=quarantine_items,
    )


__all__ = [
    "MAX_MIGRATION_BUNDLE_BYTES",
    "MAX_MIGRATION_INCIDENTS",
    "MIGRATION_SCHEMA_VERSION",
    "MigrationBundle",
    "MigrationBundleError",
    "MigrationDependencyError",
    "MigrationEntry",
    "MigrationError",
    "MigrationReport",
    "QuarantineItem",
    "QuarantineCode",
    "create_migration_bundle",
    "dump_migration_bundle",
    "export_migration_bundle",
    "import_migration_bundle",
    "load_migration_bundle",
    "validate_migration_bundle",
]
