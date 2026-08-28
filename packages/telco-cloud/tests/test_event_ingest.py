from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta

import pytest
from telco_domain.contracts import IncidentTrigger

from telco_cloud import (
    IngestDisposition,
    IngestResult,
    SourceEventEnvelope,
    SpannerEventIngestRepository,
)

from fake_spanner import FakeDatabase, NOW, RetryingFakeDatabase
from test_incident_repository import _incident


def _run(awaitable):
    return asyncio.run(awaitable)


def _envelope(
    event_id="source-01",
    *,
    incident=True,
    incident_id="incident-01",
    payload="safe",
    occurred_at=NOW,
    received_at=NOW,
):
    candidate = (
        _incident(incident_id, source_event_ids=(event_id,)) if incident else None
    )
    return SourceEventEnvelope(
        source_event_id=event_id,
        source="//pubsub.googleapis.com/projects/test/topics/network-fault",
        event_type="com.google.cloud.logging.fault.v1",
        occurred_at=occurred_at,
        received_at=received_at,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        trace_id=f"trace-{incident_id}",
        incident=candidate,
        attributes={"logger": "UERANSIMHEALTH"},
    )


def test_ingest_atomically_creates_inbox_incident_audit_idempotency_and_outbox() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)

        result = await repository.ingest(_envelope())

        assert result.disposition is IngestDisposition.CREATED
        assert result.incident is not None
        assert result.outbox_event_id is not None
        assert database.count("CanonicalSourceEventInboxV2") == 1
        assert database.count("CanonicalIncidentsV2") == 1
        assert database.count("CanonicalIncidentAuditV2") == 1
        assert database.count("CanonicalIncidentIdempotencyV2") == 1
        assert database.count("CanonicalIncidentOutboxV2") == 1
        outbox_row = next(
            iter(database.tables["CanonicalIncidentOutboxV2"].values())
        )
        trigger = IncidentTrigger.model_validate(outbox_row["payload"])
        assert trigger.incident == result.incident
        assert result.source_association is not None
        assert result.source_association.source_event_id == "source-01"
        assert result.source_association.trace_id == result.trace_id

        replay = await repository.ingest(_envelope())
        assert replay.disposition is IngestDisposition.REPLAYED
        assert replay.incident == result.incident
        assert database.count("CanonicalIncidentOutboxV2") == 1

    _run(scenario())


def test_ingest_rolls_back_every_table_when_outbox_insert_fails() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        database.fail_table = "CanonicalIncidentOutboxV2"
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)

        with pytest.raises(RuntimeError, match="injected failure"):
            await repository.ingest(_envelope())

        assert all(not rows for rows in database.tables.values())

    _run(scenario())


def test_shadow_ingest_writes_only_inbox() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)

        result = await repository.ingest(
            _envelope(incident=False),
            shadow=True,
        )

        assert result.disposition is IngestDisposition.SHADOW_RECORDED
        assert result.incident is None
        assert result.outbox_event_id is None
        assert database.count("CanonicalSourceEventInboxV2") == 1
        assert database.count("CanonicalIncidentsV2") == 0
        assert database.count("CanonicalIncidentOutboxV2") == 0

    _run(scenario())


def test_same_source_id_with_changed_payload_is_rejected() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        await repository.ingest(_envelope())

        with pytest.raises(ValueError, match="different payload"):
            await repository.ingest(_envelope(payload="tampered"))

    _run(scenario())


def test_correlated_and_replayed_events_do_not_duplicate_assurance_outbox() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)

        created = await repository.ingest(_envelope())
        correlated = await repository.ingest(
            _envelope("source-02", incident_id="incident-02")
        )

        assert created.disposition is IngestDisposition.CREATED
        assert correlated.disposition is IngestDisposition.CORRELATED
        assert correlated.incident == created.incident
        assert correlated.trace_id == "trace-incident-02"
        assert correlated.source_association is not None
        assert correlated.source_association.incident_id == created.incident.incident_id
        assert correlated.source_association.source_event_id == "source-02"
        assert correlated.source_association.trace_id == "trace-incident-02"
        assert correlated.outbox_event_id is None
        assert database.count("CanonicalSourceEventInboxV2") == 2
        assert database.count("CanonicalIncidentSourceEventsV2") == 2
        assert database.count("CanonicalIncidentOutboxV2") == 1

        replay = await repository.ingest(
            _envelope("source-02", incident_id="incident-02")
        )
        assert replay.disposition is IngestDisposition.REPLAYED
        assert replay.original_disposition is IngestDisposition.CORRELATED
        assert replay.outbox_event_id is None
        assert database.count("CanonicalIncidentOutboxV2") == 1

    _run(scenario())


def test_ingest_transaction_retry_reuses_one_timestamp_and_result() -> None:
    async def scenario() -> None:
        database = RetryingFakeDatabase()
        calls = 0

        def advancing_clock():
            nonlocal calls
            calls += 1
            return NOW + timedelta(seconds=calls)

        repository = SpannerEventIngestRepository(database, clock=advancing_clock)
        result = await repository.ingest(_envelope())

        assert calls == 1
        assert database.attempt_results == (result, result)
        inbox = next(iter(database.tables["CanonicalSourceEventInboxV2"].values()))
        assert inbox["processed_at"] == NOW + timedelta(seconds=1)

    _run(scenario())


def test_source_event_future_skew_boundary_is_fixed_at_300_seconds() -> None:
    accepted = _envelope(
        occurred_at=NOW + timedelta(seconds=300),
        received_at=NOW,
    )
    assert accepted.occurred_at == NOW + timedelta(seconds=300)

    with pytest.raises(ValueError, match="300 seconds"):
        _envelope(
            occurred_at=NOW + timedelta(seconds=301),
            received_at=NOW,
        )


def test_ingest_result_rejects_impossible_disposition_shapes() -> None:
    with pytest.raises(ValueError, match="CREATED requires"):
        IngestResult(
            disposition=IngestDisposition.CREATED,
            source_event_id="source-impossible",
            trace_id="trace-impossible",
        )
    with pytest.raises(ValueError, match="CORRELATED requires"):
        IngestResult(
            disposition=IngestDisposition.CORRELATED,
            source_event_id="source-impossible",
            trace_id="trace-impossible",
            incident=_incident(),
            outbox_event_id="outbox-impossible",
        )
    with pytest.raises(ValueError, match="SHADOW_RECORDED"):
        IngestResult(
            disposition=IngestDisposition.SHADOW_RECORDED,
            source_event_id="source-impossible",
            trace_id="trace-impossible",
            incident=_incident(),
        )
    with pytest.raises(ValueError):
        SourceEventEnvelope.model_validate(
            {
                **_envelope().model_dump(mode="python"),
                "schema_version": "2.0",
            }
        )


def test_custom_trigger_is_strictly_bound_and_rebuilt_from_committed_incident() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        envelope = _envelope()
        assert envelope.incident is not None
        trigger = IncidentTrigger(
            message_id="message-source-01",
            workflow_id="workflow-source-01",
            incident_id=envelope.incident.incident_id,
            trace_id=envelope.trace_id,
            idempotency_key="custom-ingest-key",
            sent_at=envelope.received_at,
            incident=envelope.incident,
            summary_zh="实时故障触发。",
        )
        repository = SpannerEventIngestRepository(
            database,
            clock=lambda: NOW + timedelta(seconds=30),
        )

        result = await repository.ingest(
            envelope,
            idempotency_key=trigger.idempotency_key,
            outbox_payload=trigger.to_data_part(),
        )

        outbox_row = next(
            iter(database.tables["CanonicalIncidentOutboxV2"].values())
        )
        stored = IncidentTrigger.model_validate(outbox_row["payload"])
        assert result.incident is not None
        assert result.incident.created_at == NOW + timedelta(seconds=30)
        assert stored.incident == result.incident
        assert stored.incident != trigger.incident
        assert stored.message_id == trigger.message_id
        assert stored.workflow_id == trigger.workflow_id

    _run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message_type", "unsafe_trigger"),
        ("trace_id", "trace-wrong"),
        ("incident_id", "incident-wrong"),
        ("idempotency_key", "idempotency-wrong"),
        ("unexpected", "extra"),
    ],
)
def test_hostile_custom_trigger_binding_is_rejected(field: str, value: str) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        envelope = _envelope()
        assert envelope.incident is not None
        trigger = IncidentTrigger(
            message_id="message-source-01",
            workflow_id="workflow-source-01",
            incident_id=envelope.incident.incident_id,
            trace_id=envelope.trace_id,
            idempotency_key="custom-ingest-key",
            sent_at=envelope.received_at,
            incident=envelope.incident,
        ).to_data_part()
        trigger[field] = value
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)

        with pytest.raises(ValueError):
            await repository.ingest(
                envelope,
                idempotency_key="custom-ingest-key",
                outbox_payload=trigger,
            )
        assert all(not rows for rows in database.tables.values())

    _run(scenario())


@pytest.mark.parametrize("first_shadow", [True, False])
def test_mode_cutover_exact_replay_is_ackable_without_promotion(
    first_shadow: bool,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        envelope = _envelope()

        first = await repository.ingest(envelope, shadow=first_shadow)
        replay = await repository.ingest(envelope, shadow=not first_shadow)

        assert replay.disposition is IngestDisposition.REPLAYED
        assert replay.original_disposition is first.disposition
        assert replay.incident == first.incident
        assert replay.source_association == first.source_association
        assert replay.outbox_event_id == first.outbox_event_id
        assert database.count("CanonicalSourceEventInboxV2") == 1
        assert database.count("CanonicalIncidentsV2") == (0 if first_shadow else 1)
        assert database.count("CanonicalIncidentOutboxV2") == (
            0 if first_shadow else 1
        )

    _run(scenario())


@pytest.mark.parametrize("first_shadow", [True, False])
def test_redelivery_received_at_is_not_part_of_event_identity(
    first_shadow: bool,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        first_envelope = _envelope(received_at=NOW)
        redelivery = _envelope(received_at=NOW + timedelta(seconds=30))

        first = await repository.ingest(first_envelope, shadow=first_shadow)
        replay = await repository.ingest(redelivery, shadow=not first_shadow)

        assert replay.disposition is IngestDisposition.REPLAYED
        assert replay.original_disposition is first.disposition
        assert database.count("CanonicalSourceEventInboxV2") == 1
        assert database.count("CanonicalIncidentsV2") == (0 if first_shadow else 1)
        assert database.count("CanonicalIncidentOutboxV2") == (
            0 if first_shadow else 1
        )
        if not first_shadow:
            outbox = next(
                iter(database.tables["CanonicalIncidentOutboxV2"].values())
            )
            assert IncidentTrigger.model_validate(outbox["payload"]).sent_at == NOW

    _run(scenario())


def test_inbox_replay_binding_corruption_fails_closed() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        await repository.ingest(_envelope())
        inbox = next(
            iter(database.tables["CanonicalSourceEventInboxV2"].values())
        )
        inbox["incident_id"] = "incident-corrupt"

        with pytest.raises(RuntimeError, match="Inbox binding"):
            await repository.ingest(_envelope())

    _run(scenario())


@pytest.mark.parametrize(
    "corruption",
    ["delete_outbox", "outbox_incident", "inbox_incident_snapshot"],
)
def test_created_replay_requires_exact_incident_and_outbox_binding(
    corruption: str,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        result = await repository.ingest(_envelope())
        assert result.outbox_event_id is not None
        if corruption == "delete_outbox":
            database.tables["CanonicalIncidentOutboxV2"].clear()
        elif corruption == "outbox_incident":
            row = database.tables["CanonicalIncidentOutboxV2"][
                (result.outbox_event_id,)
            ]
            row["incident_id"] = "incident-corrupt"
        else:
            inbox = database.tables["CanonicalSourceEventInboxV2"][
                ("source-01",)
            ]
            inbox["result_payload"]["incident"]["title"] = "corrupt snapshot"

        with pytest.raises(RuntimeError, match="persisted"):
            await repository.ingest(_envelope())

    _run(scenario())


def test_correlated_replay_requires_persisted_incident() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        await repository.ingest(_envelope())
        envelope = _envelope("source-02", incident_id="incident-02")
        correlated = await repository.ingest(envelope)
        assert correlated.disposition is IngestDisposition.CORRELATED
        database.tables["CanonicalIncidentsV2"].clear()

        with pytest.raises(RuntimeError, match="Incident result mismatch"):
            await repository.ingest(envelope)

    _run(scenario())


def test_correlated_replay_rejects_inbox_incident_snapshot_mutation() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        repository = SpannerEventIngestRepository(database, clock=lambda: NOW)
        await repository.ingest(_envelope())
        envelope = _envelope("source-02", incident_id="incident-02")
        await repository.ingest(envelope)
        inbox = database.tables["CanonicalSourceEventInboxV2"][("source-02",)]
        inbox["result_payload"]["incident"]["title"] = "corrupt snapshot"

        with pytest.raises(RuntimeError, match="idempotency result mismatch"):
            await repository.ingest(envelope)

    _run(scenario())
