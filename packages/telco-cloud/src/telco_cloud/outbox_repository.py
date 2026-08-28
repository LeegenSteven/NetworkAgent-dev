"""Framework-neutral Spanner outbox claiming and delivery state machine."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)
from telco_domain.contracts import IncidentTrigger

from ._common import (
    Clock,
    assert_safe,
    parse_json_model,
    require_non_empty,
    utc_now,
)
from ._spanner import execute_sql, read_one


MAX_OUTBOX_CLAIM = 100
MAX_OUTBOX_ATTEMPTS = 1_000_000
MAX_LEASE_SECONDS = 3_600
MAX_RETRY_DELAY_SECONDS = 86_400
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]

_OUTBOX_COLUMNS = (
    "event_id",
    "incident_id",
    "source_event_id",
    "event_type",
    "payload",
    "status",
    "attempts",
    "available_at",
    "created_at",
    "published_at",
    "lease_owner",
    "lease_expires_at",
    "last_error_code",
)
_OUTBOX_MUTABLE_COLUMNS = (
    "event_id",
    "status",
    "attempts",
    "available_at",
    "published_at",
    "lease_owner",
    "lease_expires_at",
    "last_error_code",
)


def _utc(name: str, value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _integer(name: str, value: int, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _error_code(value: str) -> str:
    normalized = require_non_empty("error_code", value, max_length=128).lower()
    if not _ERROR_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("error_code must be a bounded machine-readable code")
    return normalized


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    DELIVERED = "DELIVERED"
    DEAD = "DEAD"


class OutboxLeaseConflictError(RuntimeError):
    """A stale or different worker attempted to mutate a leased message."""


class OutboxRecord(BaseModel):
    """Canonical IncidentTrigger plus durable delivery bookkeeping."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )

    event_id: _Identifier
    incident_id: _Identifier
    source_event_id: _Identifier
    event_type: _Identifier
    payload: IncidentTrigger
    status: OutboxStatus
    attempts: int
    available_at: datetime
    created_at: datetime
    published_at: datetime | None = None
    lease_owner: _Identifier | None = None
    lease_expires_at: datetime | None = None
    last_error_code: str | None = None

    @field_validator("attempts")
    @classmethod
    def validate_attempts(cls, value: int) -> int:
        return _integer(
            "attempts", value, minimum=0, maximum=MAX_OUTBOX_ATTEMPTS
        )

    @model_validator(mode="after")
    def validate_binding_and_state(self) -> "OutboxRecord":
        object.__setattr__(
            self, "available_at", _utc("available_at", self.available_at)
        )
        object.__setattr__(
            self, "created_at", _utc("created_at", self.created_at)
        )
        object.__setattr__(
            self, "published_at", _utc("published_at", self.published_at)
        )
        object.__setattr__(
            self,
            "lease_expires_at",
            _utc("lease_expires_at", self.lease_expires_at),
        )
        if self.last_error_code is not None:
            object.__setattr__(
                self, "last_error_code", _error_code(self.last_error_code)
            )
        if (
            self.event_type != self.payload.message_type
            or self.incident_id != self.payload.incident_id
            or self.incident_id != self.payload.incident.incident_id
            or self.source_event_id not in self.payload.incident.source_event_ids
        ):
            raise ValueError("outbox trigger binding mismatch")
        if self.status is OutboxStatus.LEASED:
            if self.lease_owner is None or self.lease_expires_at is None:
                raise ValueError("LEASED outbox record requires an owner and expiry")
            if self.published_at is not None:
                raise ValueError("LEASED outbox record cannot be published")
        elif self.lease_owner is not None or self.lease_expires_at is not None:
            raise ValueError("only LEASED outbox records can carry lease metadata")
        if self.status is OutboxStatus.DELIVERED:
            if self.published_at is None:
                raise ValueError("DELIVERED outbox record requires published_at")
        elif self.published_at is not None:
            raise ValueError("only DELIVERED outbox records can be published")
        assert_safe(self, boundary="outbox-record")
        return self


class SpannerOutboxRepository:
    """Lease outbox rows with retry-safe transactions and exact ownership CAS."""

    def __init__(self, database: Any, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return utc_now(self._clock)

    async def get(self, event_id: str) -> OutboxRecord | None:
        normalized = require_non_empty("event_id", event_id, max_length=256)
        assert_safe({"event_id": normalized}, boundary="outbox-read")

        def read() -> OutboxRecord | None:
            with self._database.snapshot(multi_use=True) as snapshot:
                return self._get_tx(snapshot, normalized)

        return await asyncio.to_thread(read)

    async def claim(
        self,
        *,
        lease_owner: str,
        limit: int = MAX_OUTBOX_CLAIM,
        lease_seconds: int = 60,
        max_attempts: int = 10,
    ) -> tuple[OutboxRecord, ...]:
        owner = require_non_empty("lease_owner", lease_owner, max_length=256)
        assert_safe({"lease_owner": owner}, boundary="outbox-claim")
        _integer("limit", limit, minimum=1, maximum=MAX_OUTBOX_CLAIM)
        _integer(
            "lease_seconds",
            lease_seconds,
            minimum=1,
            maximum=MAX_LEASE_SECONDS,
        )
        _integer(
            "max_attempts",
            max_attempts,
            minimum=1,
            maximum=MAX_OUTBOX_ATTEMPTS,
        )
        trusted_now = self._now()
        lease_expiry = trusted_now + timedelta(seconds=lease_seconds)

        def callback(transaction: Any) -> tuple[OutboxRecord, ...]:
            rows = tuple(
                execute_sql(
                    transaction,
                    """-- telco-cloud:claim-outbox
                    SELECT event_id, incident_id, source_event_id, event_type,
                           payload, status, attempts, available_at, created_at,
                           published_at, lease_owner, lease_expires_at,
                           last_error_code
                    FROM CanonicalIncidentOutboxV2
                    WHERE (status = 'PENDING' AND available_at <= @trusted_now)
                       OR (status = 'LEASED' AND lease_expires_at <= @trusted_now)
                    ORDER BY available_at, event_id
                    LIMIT @limit""",
                    params={"trusted_now": trusted_now, "limit": limit},
                    type_spec={
                        "trusted_now": "TIMESTAMP",
                        "limit": "INT64",
                    },
                )
            )
            if len(rows) > limit:
                raise RuntimeError("Spanner returned too many outbox rows")
            claimed: list[OutboxRecord] = []
            for row in rows:
                current = self._parse_row(row)
                eligible = (
                    current.status is OutboxStatus.PENDING
                    and current.available_at <= trusted_now
                ) or (
                    current.status is OutboxStatus.LEASED
                    and current.lease_expires_at is not None
                    and current.lease_expires_at <= trusted_now
                )
                if not eligible:
                    raise RuntimeError("Spanner returned an ineligible outbox row")
                if current.attempts >= max_attempts:
                    dead = current.model_copy(
                        update={
                            "status": OutboxStatus.DEAD,
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "last_error_code": "max_attempts_exceeded",
                        }
                    )
                    dead = OutboxRecord.model_validate(dead)
                    self._update_tx(transaction, dead)
                    continue
                leased = current.model_copy(
                    update={
                        "status": OutboxStatus.LEASED,
                        "attempts": current.attempts + 1,
                        "lease_owner": owner,
                        "lease_expires_at": lease_expiry,
                    }
                )
                leased = OutboxRecord.model_validate(leased)
                self._update_tx(transaction, leased)
                claimed.append(leased)
            return tuple(claimed)

        return await asyncio.to_thread(
            self._database.run_in_transaction, callback
        )

    async def mark_delivered(
        self,
        event_id: str,
        *,
        lease_owner: str,
        expected_attempt: int,
    ) -> OutboxRecord:
        return await self._finish_lease(
            event_id,
            lease_owner=lease_owner,
            expected_attempt=expected_attempt,
            action="delivered",
        )

    async def retry(
        self,
        event_id: str,
        *,
        lease_owner: str,
        expected_attempt: int,
        delay_seconds: int,
        error_code: str,
    ) -> OutboxRecord:
        _integer(
            "delay_seconds",
            delay_seconds,
            minimum=0,
            maximum=MAX_RETRY_DELAY_SECONDS,
        )
        return await self._finish_lease(
            event_id,
            lease_owner=lease_owner,
            expected_attempt=expected_attempt,
            action="retry",
            delay_seconds=delay_seconds,
            error_code=_error_code(error_code),
        )

    async def mark_dead(
        self,
        event_id: str,
        *,
        lease_owner: str,
        expected_attempt: int,
        error_code: str,
    ) -> OutboxRecord:
        return await self._finish_lease(
            event_id,
            lease_owner=lease_owner,
            expected_attempt=expected_attempt,
            action="dead",
            error_code=_error_code(error_code),
        )

    async def _finish_lease(
        self,
        event_id: str,
        *,
        lease_owner: str,
        expected_attempt: int,
        action: str,
        delay_seconds: int = 0,
        error_code: str | None = None,
    ) -> OutboxRecord:
        normalized_event_id = require_non_empty(
            "event_id", event_id, max_length=256
        )
        owner = require_non_empty("lease_owner", lease_owner, max_length=256)
        assert_safe(
            {"event_id": normalized_event_id, "lease_owner": owner},
            boundary="outbox-lease",
        )
        _integer(
            "expected_attempt",
            expected_attempt,
            minimum=1,
            maximum=MAX_OUTBOX_ATTEMPTS,
        )
        trusted_now = self._now()

        def callback(transaction: Any) -> OutboxRecord:
            current = self._get_tx(transaction, normalized_event_id)
            if current is None:
                raise OutboxLeaseConflictError("outbox event does not exist")
            if (
                current.status is not OutboxStatus.LEASED
                or current.lease_owner != owner
                or current.attempts != expected_attempt
                or current.lease_expires_at is None
                or current.lease_expires_at <= trusted_now
            ):
                raise OutboxLeaseConflictError(
                    "outbox lease is stale or owned by another worker"
                )
            updates: dict[str, object] = {
                "lease_owner": None,
                "lease_expires_at": None,
            }
            if action == "delivered":
                updates.update(
                    status=OutboxStatus.DELIVERED,
                    published_at=trusted_now,
                    last_error_code=None,
                )
            elif action == "retry":
                updates.update(
                    status=OutboxStatus.PENDING,
                    available_at=trusted_now + timedelta(seconds=delay_seconds),
                    last_error_code=error_code,
                )
            elif action == "dead":
                updates.update(
                    status=OutboxStatus.DEAD,
                    last_error_code=error_code,
                )
            else:  # pragma: no cover - private programming guard
                raise AssertionError(f"unknown outbox action {action!r}")
            successor = OutboxRecord.model_validate(
                current.model_copy(update=updates)
            )
            self._update_tx(transaction, successor)
            return successor

        return await asyncio.to_thread(
            self._database.run_in_transaction, callback
        )

    @staticmethod
    def _parse_row(row: object) -> OutboxRecord:
        values = tuple(row)
        if len(values) != len(_OUTBOX_COLUMNS):
            raise RuntimeError("Spanner returned a malformed outbox row")
        try:
            record = OutboxRecord(
                event_id=str(values[0]),
                incident_id=str(values[1]),
                source_event_id=str(values[2]),
                event_type=str(values[3]),
                payload=parse_json_model(IncidentTrigger, values[4]),
                status=values[5],
                attempts=values[6],
                available_at=values[7],
                created_at=values[8],
                published_at=values[9],
                lease_owner=values[10],
                lease_expires_at=values[11],
                last_error_code=values[12],
            )
        except (TypeError, ValueError):
            raise RuntimeError("persisted outbox row binding mismatch") from None
        return record

    @classmethod
    def _get_tx(cls, reader: Any, event_id: str) -> OutboxRecord | None:
        row = read_one(
            reader,
            "CanonicalIncidentOutboxV2",
            _OUTBOX_COLUMNS,
            (event_id,),
        )
        if row is None:
            return None
        record = cls._parse_row(row)
        if record.event_id != event_id:
            raise RuntimeError("persisted outbox identity mismatch")
        return record

    @staticmethod
    def _update_tx(transaction: Any, record: OutboxRecord) -> None:
        transaction.update(
            "CanonicalIncidentOutboxV2",
            columns=_OUTBOX_MUTABLE_COLUMNS,
            values=(
                (
                    record.event_id,
                    record.status.value,
                    record.attempts,
                    record.available_at,
                    record.published_at,
                    record.lease_owner,
                    record.lease_expires_at,
                    record.last_error_code,
                ),
            ),
        )


__all__ = [
    "MAX_OUTBOX_CLAIM",
    "OutboxLeaseConflictError",
    "OutboxRecord",
    "OutboxStatus",
    "SpannerOutboxRepository",
]
