from __future__ import annotations

from dataclasses import replace

import pytest

from fake_spanner import FakeDatabase
from telco_cloud import SpannerEventIngestRepository
from telco_domain import Incident, IncidentTrigger
from telco_fault_ingress.boundary import parse_pubsub_push
from telco_fault_ingress.config import FaultPipelineMode
from telco_fault_ingress.service import FaultIngressService

from .conftest import log_entry, push_body


def _service(config, database, fixed_now, mode):
    repository = SpannerEventIngestRepository(
        database, clock=lambda: fixed_now
    )
    return FaultIngressService(
        replace(config, mode=mode),
        repository,
        clock=lambda: fixed_now,
    )


@pytest.mark.asyncio
async def test_two_correlated_faults_ack_and_commit_exact_outbox_snapshot(
    config, fixed_now
) -> None:
    database = FakeDatabase()
    service = _service(config, database, fixed_now, FaultPipelineMode.CANONICAL)
    first = parse_pubsub_push(
        push_body(log_entry(insert_id="insert-correlated-1")), config
    )
    second = parse_pubsub_push(
        push_body(log_entry(insert_id="insert-correlated-2")), config
    )

    first_decision = await service.process(first)
    second_decision = await service.process(second)

    assert (first_decision.http_status, first_decision.code) == (
        204,
        "FAULT_CREATED",
    )
    assert (second_decision.http_status, second_decision.code) == (
        204,
        "FAULT_CORRELATED",
    )
    assert database.count("CanonicalIncidentsV2") == 1
    assert database.count("CanonicalSourceEventInboxV2") == 2
    assert database.count("CanonicalIncidentSourceEventsV2") == 2
    assert database.count("CanonicalIncidentOutboxV2") == 1

    incident_row = next(iter(database.tables["CanonicalIncidentsV2"].values()))
    outbox_row = next(iter(database.tables["CanonicalIncidentOutboxV2"].values()))
    persisted = Incident.model_validate(dict(incident_row["payload"]))
    trigger = IncidentTrigger.model_validate(dict(outbox_row["payload"]))
    assert trigger.incident == persisted == first_decision.result.incident


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_mode", "second_mode", "expected_counts"),
    [
        (
            FaultPipelineMode.SHADOW,
            FaultPipelineMode.CANONICAL,
            (0, 1, 0, 0),
        ),
        (
            FaultPipelineMode.CANONICAL,
            FaultPipelineMode.SHADOW,
            (1, 1, 1, 1),
        ),
    ],
)
async def test_mode_cutover_replay_acks_without_promoting_or_double_writing(
    config, fixed_now, first_mode, second_mode, expected_counts
) -> None:
    database = FakeDatabase()
    push = parse_pubsub_push(push_body(), config)
    first = await _service(config, database, fixed_now, first_mode).process(push)
    before = (
        database.count("CanonicalIncidentsV2"),
        database.count("CanonicalSourceEventInboxV2"),
        database.count("CanonicalIncidentSourceEventsV2"),
        database.count("CanonicalIncidentOutboxV2"),
    )
    replay = await _service(config, database, fixed_now, second_mode).process(push)
    after = (
        database.count("CanonicalIncidentsV2"),
        database.count("CanonicalSourceEventInboxV2"),
        database.count("CanonicalIncidentSourceEventsV2"),
        database.count("CanonicalIncidentOutboxV2"),
    )

    assert first.http_status == 204
    assert (replay.http_status, replay.code) == (204, "FAULT_REPLAYED")
    assert before == after == expected_counts
