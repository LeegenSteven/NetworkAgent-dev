from __future__ import annotations

from dataclasses import replace
import pytest

from telco_cloud import IngestDisposition, IngestResult
from telco_domain import SourceEventAssociation
from telco_fault_ingress.boundary import parse_pubsub_push
from telco_fault_ingress.config import FaultPipelineMode
from telco_fault_ingress.normalizer import build_incident_trigger
from telco_fault_ingress.service import FaultIngressService

from .conftest import push_body


class FakeRepository:
    def __init__(self, disposition: str = "created", failure: Exception | None = None):
        self.disposition = disposition
        self.failure = failure
        self.calls = []

    async def ingest(self, envelope, **kwargs):
        self.calls.append((envelope, kwargs))
        if self.failure is not None:
            raise self.failure
        canonical = not kwargs["shadow"]
        original = (
            IngestDisposition.CREATED
            if self.disposition == "replayed"
            else IngestDisposition(self.disposition)
        )
        association = (
            SourceEventAssociation(
                incident_id=envelope.incident.incident_id,
                source_event_id=envelope.source_event_id,
                registered_at=envelope.received_at,
                actor=kwargs["actor"],
                reason=kwargs["reason"],
                idempotency_key=kwargs["idempotency_key"],
                trace_id=envelope.trace_id,
            )
            if canonical
            else None
        )
        return IngestResult(
            disposition=IngestDisposition(self.disposition),
            original_disposition=original,
            source_event_id=envelope.source_event_id,
            trace_id=envelope.trace_id,
            incident=(None if not canonical else envelope.incident),
            source_association=association,
            outbox_event_id=(
                "outbox-1" if self.disposition in {"created", "replayed"} else None
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disposition", ["created", "correlated", "replayed", "shadow_recorded"]
)
async def test_only_durable_dispositions_are_acked(
    config, fixed_now, disposition
) -> None:
    mode = (
        FaultPipelineMode.SHADOW
        if disposition == "shadow_recorded"
        else FaultPipelineMode.CANONICAL
    )
    repository = FakeRepository(disposition)
    service = FaultIngressService(
        replace(config, mode=mode), repository, clock=lambda: fixed_now
    )
    decision = await service.process(parse_pubsub_push(push_body(), config))
    assert decision.http_status == 204
    _, call = repository.calls[0]
    assert call["shadow"] is (mode is FaultPipelineMode.SHADOW)
    assert (call["outbox_payload"] is None) is (mode is FaultPipelineMode.SHADOW)


@pytest.mark.asyncio
async def test_paused_and_transient_dependency_failures_are_retried(
    config, fixed_now
) -> None:
    push = parse_pubsub_push(push_body(), config)
    paused_repo = FakeRepository()
    paused = FaultIngressService(
        replace(config, mode=FaultPipelineMode.PAUSED), paused_repo
    )
    assert (await paused.process(push)).http_status == 503
    assert paused_repo.calls == []

    failing = FaultIngressService(
        replace(config, mode=FaultPipelineMode.CANONICAL),
        FakeRepository(failure=RuntimeError("secret backend detail")),
        clock=lambda: fixed_now,
    )
    decision = await failing.process(push)
    assert decision.http_status == 503
    assert decision.code == "FAULT_DEPENDENCY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_legacy_mode_requires_explicit_handler(config) -> None:
    service = FaultIngressService(
        replace(config, mode=FaultPipelineMode.LEGACY), FakeRepository()
    )
    decision = await service.process(parse_pubsub_push(push_body(), config))
    assert decision.http_status == 503
    assert decision.code == "FAULT_LEGACY_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "disposition", "source_matches", "incident", "outbox"),
    [
        (FaultPipelineMode.CANONICAL, "shadow_recorded", True, False, False),
        (FaultPipelineMode.SHADOW, "created", True, True, True),
        (FaultPipelineMode.CANONICAL, "created", True, True, False),
        (FaultPipelineMode.CANONICAL, "correlated", True, True, True),
        (FaultPipelineMode.CANONICAL, "replayed", True, False, False),
        (FaultPipelineMode.CANONICAL, "created", False, True, True),
    ],
)
async def test_impossible_ingest_results_are_never_acked(
    config,
    fixed_now,
    mode,
    disposition,
    source_matches,
    incident,
    outbox,
) -> None:
    class MaliciousRepository:
        async def ingest(self, envelope, **kwargs):
            del kwargs
            # Return an untrusted adapter-shaped object so invalid result
            # combinations reach the service's own validation boundary.
            return {
                "disposition": disposition,
                "source_event_id": (
                    envelope.source_event_id if source_matches else "event-other"
                ),
                "trace_id": envelope.trace_id,
                "incident": envelope.incident if incident else None,
                "source_association": (
                    SourceEventAssociation(
                        incident_id=envelope.incident.incident_id,
                        source_event_id=(
                            envelope.source_event_id
                            if source_matches
                            else "event-other"
                        ),
                        registered_at=envelope.received_at,
                        actor="fault-ingress",
                        reason="canonical source event ingestion",
                        idempotency_key="idempotency-malicious",
                        trace_id=envelope.trace_id,
                    )
                    if incident and disposition != "shadow_recorded"
                    else None
                ),
                "outbox_event_id": "outbox-1" if outbox else None,
            }

    service = FaultIngressService(
        replace(config, mode=mode),
        MaliciousRepository(),
        clock=lambda: fixed_now,
    )
    decision = await service.process(parse_pubsub_push(push_body(), config))
    assert decision.http_status == 503
    assert decision.code == "FAULT_INGEST_RESULT_INVALID"


@pytest.mark.asyncio
async def test_correlated_result_binds_new_event_by_association_not_aggregate_trace(
    config, fixed_now
) -> None:
    class CorrelatedRepository:
        async def ingest(self, envelope, **kwargs):
            aggregate = envelope.incident.model_copy(
                update={
                    "trace_id": "trace-existing-aggregate",
                    "source_event_ids": ("event-existing",),
                }
            )
            association = SourceEventAssociation(
                incident_id=aggregate.incident_id,
                source_event_id=envelope.source_event_id,
                registered_at=envelope.received_at,
                actor=kwargs["actor"],
                reason=kwargs["reason"],
                idempotency_key=kwargs["idempotency_key"],
                trace_id=envelope.trace_id,
            )
            return IngestResult(
                disposition=IngestDisposition.CORRELATED,
                source_event_id=envelope.source_event_id,
                trace_id=envelope.trace_id,
                incident=aggregate,
                source_association=association,
            )

    service = FaultIngressService(
        replace(config, mode=FaultPipelineMode.CANONICAL),
        CorrelatedRepository(),
        clock=lambda: fixed_now,
    )
    decision = await service.process(parse_pubsub_push(push_body(), config))
    assert decision.http_status == 204
    assert decision.code == "FAULT_CORRELATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "original"),
    [
        (FaultPipelineMode.CANONICAL, IngestDisposition.SHADOW_RECORDED),
        (FaultPipelineMode.SHADOW, IngestDisposition.CREATED),
    ],
)
async def test_mode_cutover_acks_valid_historical_replay_without_promotion(
    config, fixed_now, mode, original
) -> None:
    class ReplayRepository:
        async def ingest(self, envelope, **kwargs):
            canonical = original is IngestDisposition.CREATED
            association = (
                SourceEventAssociation(
                    incident_id=envelope.incident.incident_id,
                    source_event_id=envelope.source_event_id,
                    registered_at=envelope.received_at,
                    actor=kwargs["actor"],
                    reason="canonical source event ingestion",
                    idempotency_key=(
                        kwargs["idempotency_key"]
                        or build_incident_trigger(envelope).idempotency_key
                    ),
                    trace_id=envelope.trace_id,
                )
                if canonical
                else None
            )
            return IngestResult(
                disposition=IngestDisposition.REPLAYED,
                original_disposition=original,
                source_event_id=envelope.source_event_id,
                trace_id=envelope.trace_id,
                incident=envelope.incident if canonical else None,
                source_association=association,
                outbox_event_id="outbox-1" if canonical else None,
            )

    service = FaultIngressService(
        replace(config, mode=mode),
        ReplayRepository(),
        clock=lambda: fixed_now,
    )
    decision = await service.process(parse_pubsub_push(push_body(), config))
    assert decision.http_status == 204
    assert decision.code == "FAULT_REPLAYED"
