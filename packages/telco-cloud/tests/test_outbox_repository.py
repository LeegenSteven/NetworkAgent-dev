from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from telco_domain.ports import UnsafeIncidentWriteError

from telco_cloud import (
    OutboxLeaseConflictError,
    OutboxStatus,
    SpannerEventIngestRepository,
    SpannerOutboxRepository,
)

from fake_spanner import FakeDatabase, NOW, RetryingFakeDatabase
from test_event_ingest import _envelope


def _run(awaitable):
    return asyncio.run(awaitable)


async def _seed(database) -> str:
    result = await SpannerEventIngestRepository(
        database, clock=lambda: NOW
    ).ingest(_envelope())
    assert result.outbox_event_id is not None
    return result.outbox_event_id


def test_concurrent_claim_has_one_owner_and_retry_clock_is_frozen() -> None:
    async def scenario() -> None:
        database = RetryingFakeDatabase()
        event_id = await _seed(database)
        calls = 0

        def clock():
            nonlocal calls
            calls += 1
            return NOW + timedelta(seconds=calls)

        first = SpannerOutboxRepository(database, clock=clock)
        claimed = await first.claim(
            lease_owner="dispatcher-a", lease_seconds=30
        )
        assert len(claimed) == 1
        assert claimed[0].event_id == event_id
        assert claimed[0].status is OutboxStatus.LEASED
        assert claimed[0].attempts == 1
        assert calls == 1
        assert database.attempt_results == (claimed, claimed)

        second = SpannerOutboxRepository(
            database, clock=lambda: NOW + timedelta(seconds=2)
        )
        left, right = await asyncio.gather(
            second.claim(lease_owner="dispatcher-b"),
            second.claim(lease_owner="dispatcher-c"),
        )
        assert left == right == ()

    _run(scenario())


def test_expired_lease_is_recovered_and_stale_owner_cannot_ack() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        event_id = await _seed(database)
        first = SpannerOutboxRepository(database, clock=lambda: NOW)
        initial = await first.claim(
            lease_owner="dispatcher-a", lease_seconds=10
        )
        assert initial[0].attempts == 1

        recovered_at = NOW + timedelta(seconds=11)
        second = SpannerOutboxRepository(database, clock=lambda: recovered_at)
        recovered = await second.claim(
            lease_owner="dispatcher-b", lease_seconds=10
        )
        assert recovered[0].attempts == 2
        assert recovered[0].lease_owner == "dispatcher-b"

        with pytest.raises(OutboxLeaseConflictError):
            await second.mark_delivered(
                event_id,
                lease_owner="dispatcher-a",
                expected_attempt=1,
            )
        delivered = await second.mark_delivered(
            event_id,
            lease_owner="dispatcher-b",
            expected_attempt=2,
        )
        assert delivered.status is OutboxStatus.DELIVERED
        assert delivered.published_at == recovered_at

    _run(scenario())


def test_retry_delay_and_explicit_dead_are_durable() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        event_id = await _seed(database)
        repository = SpannerOutboxRepository(database, clock=lambda: NOW)
        await repository.claim(lease_owner="dispatcher-a")
        pending = await repository.retry(
            event_id,
            lease_owner="dispatcher-a",
            expected_attempt=1,
            delay_seconds=60,
            error_code="resolver_unavailable",
        )
        assert pending.status is OutboxStatus.PENDING
        assert pending.available_at == NOW + timedelta(seconds=60)
        assert pending.last_error_code == "resolver_unavailable"
        assert await repository.claim(lease_owner="dispatcher-b") == ()

        later = SpannerOutboxRepository(
            database, clock=lambda: NOW + timedelta(seconds=61)
        )
        claimed = await later.claim(lease_owner="dispatcher-b")
        dead = await later.mark_dead(
            event_id,
            lease_owner="dispatcher-b",
            expected_attempt=2,
            error_code="contract_rejected",
        )
        assert claimed[0].attempts == 2
        assert dead.status is OutboxStatus.DEAD
        assert dead.last_error_code == "contract_rejected"
        assert await later.claim(lease_owner="dispatcher-c") == ()

    _run(scenario())


def test_claim_marks_exhausted_message_dead_without_returning_it() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        event_id = await _seed(database)
        row = database.tables["CanonicalIncidentOutboxV2"][(event_id,)]
        row.update(
            status="LEASED",
            attempts=3,
            lease_owner="old-worker",
            lease_expires_at=NOW - timedelta(seconds=1),
        )
        repository = SpannerOutboxRepository(database, clock=lambda: NOW)

        assert await repository.claim(
            lease_owner="dispatcher", max_attempts=3
        ) == ()
        stored = await repository.get(event_id)
        assert stored is not None
        assert stored.status is OutboxStatus.DEAD
        assert stored.last_error_code == "max_attempts_exceeded"

    _run(scenario())


def test_pending_message_can_only_be_claimed_once_concurrently() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        await _seed(database)
        first = SpannerOutboxRepository(database, clock=lambda: NOW)
        second = SpannerOutboxRepository(database, clock=lambda: NOW)

        results = await asyncio.gather(
            first.claim(lease_owner="dispatcher-a"),
            second.claim(lease_owner="dispatcher-b"),
        )

        claimed = [item for result in results for item in result]
        assert len(claimed) == 1
        assert claimed[0].attempts == 1

    _run(scenario())


def test_sensitive_lease_owner_is_rejected_before_database_access() -> None:
    database = FakeDatabase()
    repository = SpannerOutboxRepository(database, clock=lambda: NOW)

    with pytest.raises(UnsafeIncidentWriteError):
        _run(repository.claim(lease_owner="dispatcher-imsi-001010123456789"))

    assert all(not rows for rows in database.tables.values())


def test_dispatcher_mutation_cannot_rewrite_immutable_message_columns() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        event_id = await _seed(database)
        repository = SpannerOutboxRepository(database, clock=lambda: NOW)
        record = await repository.get(event_id)
        assert record is not None
        captured: dict[str, object] = {}

        class Transaction:
            def update(self, table, *, columns, values):
                captured.update(table=table, columns=columns, values=values)

        repository._update_tx(Transaction(), record)

        assert captured["table"] == "CanonicalIncidentOutboxV2"
        assert captured["columns"] == (
            "event_id",
            "status",
            "attempts",
            "available_at",
            "published_at",
            "lease_owner",
            "lease_expires_at",
            "last_error_code",
        )
        immutable = {
            "incident_id",
            "source_event_id",
            "event_type",
            "payload",
            "created_at",
        }
        assert immutable.isdisjoint(captured["columns"])

    _run(scenario())
