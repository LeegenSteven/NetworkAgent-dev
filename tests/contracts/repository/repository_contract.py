"""One reusable behavioral contract for every IncidentRepository adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telco_domain import Incident, IncidentStatus
from telco_domain.ports import (
    ActiveIncidentConflictError,
    IdempotencyConflictError,
    IncidentCorrelationConflictError,
    RevisionConflictError,
    UnsafeIncidentWriteError,
)


class ContractClock:
    """Deterministic trusted clock shared by all repository factories."""

    def __init__(self) -> None:
        self.value = datetime(2040, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


async def assert_incident_repository_contract(repository, clock: ContractClock) -> None:
    """Exercise replaceable create/correlate/CAS/page/provenance semantics."""

    first = Incident(
        incident_id="contract-incident-a",
        correlation_key="contract-correlation",
        source_event_ids=("contract-event-a",),
        trace_id="contract-trace-a",
    )
    created = await repository.create_or_correlate(
        first,
        idempotency_key="contract-create-a",
        actor="contract-detector",
        reason="repository contract create",
        trace_id=first.trace_id,
    )
    clock.advance()
    replayed = await repository.create_or_correlate(
        first,
        idempotency_key="contract-create-a",
        actor="contract-detector",
        reason="repository contract create",
        trace_id=first.trace_id,
    )
    assert replayed == created

    with pytest.raises(IdempotencyConflictError):
        await repository.create_or_correlate(
            first.model_copy(update={"title": "changed request"}),
            idempotency_key="contract-create-a",
            actor="contract-detector",
            reason="repository contract create",
            trace_id=first.trace_id,
        )

    clock.advance()
    second = Incident(
        incident_id="contract-incident-b",
        correlation_key="contract-correlation",
        source_event_ids=("contract-event-b",),
        trace_id="contract-trace-b",
    )
    correlated = await repository.create_or_correlate(
        second,
        idempotency_key="contract-correlate-b",
        actor="contract-detector",
        reason="repository contract correlate",
        trace_id=second.trace_id,
    )
    assert correlated == created
    assert len(await repository.list()) == 1
    assert len(await repository.history(created.incident_id)) == 1
    associations = await repository.source_event_associations(created.incident_id)
    assert tuple(item.source_event_id for item in associations) == (
        "contract-event-a",
        "contract-event-b",
    )
    assert all(item.incident_id == created.incident_id for item in associations)

    naked = Incident(
        incident_id="contract-incident-c",
        correlation_key="contract-correlation",
        source_event_ids=("contract-event-c",),
        trace_id="contract-trace-c",
    )
    with pytest.raises(ActiveIncidentConflictError):
        await repository.create(
            naked,
            idempotency_key="contract-naked-c",
            actor="contract-detector",
            reason="repository contract naked create",
            trace_id=naked.trace_id,
        )
    assert tuple(item.source_event_id for item in (
        await repository.source_event_associations(created.incident_id)
    )) == ("contract-event-a", "contract-event-b")

    clock.advance()
    triaged = await repository.transition(
        created.incident_id,
        IncidentStatus.TRIAGED,
        expected_revision=0,
        idempotency_key="contract-triage",
        actor="contract-resolver",
        reason="repository contract transition",
        trace_id=created.trace_id,
        updates={
            "source_event_ids": created.source_event_ids
            + ("contract-event-transition",),
        },
    )
    assert triaged.revision == 1
    assert triaged.status is IncidentStatus.TRIAGED
    assert len(await repository.history(created.incident_id)) == 2
    assert tuple(
        item.source_event_id
        for item in await repository.source_event_associations(created.incident_id)
    ) == (
        "contract-event-a",
        "contract-event-b",
        "contract-event-transition",
    )
    assert (
        await repository.find_active(source_event_id="contract-event-transition")
    ) == triaged
    assert await repository.list(status="TRIAGED") == (triaged,)
    assert await repository.list(limit=1, offset=0) == (triaged,)

    with pytest.raises(RevisionConflictError):
        await repository.transition(
            created.incident_id,
            IncidentStatus.INVESTIGATING,
            expected_revision=0,
            idempotency_key="contract-stale",
            actor="contract-resolver",
            reason="repository contract stale transition",
            trace_id=created.trace_id,
        )
    with pytest.raises(ValueError):
        await repository.list(limit=1_001)
    with pytest.raises(ValueError):
        await repository.list(offset=100_001)
    with pytest.raises(ValueError):
        await repository.history(created.incident_id, offset=1)

    other_owner = Incident(
        incident_id="contract-incident-z",
        correlation_key="contract-other-correlation",
        source_event_ids=("contract-event-z",),
        trace_id="contract-trace-z",
    )
    await repository.create(
        other_owner,
        idempotency_key="contract-create-z",
        actor="contract-detector",
        reason="repository contract second owner",
        trace_id=other_owner.trace_id,
    )
    split_selector = Incident(
        incident_id="contract-incident-split",
        correlation_key=first.correlation_key,
        source_event_ids=("contract-event-z",),
        trace_id="contract-trace-split",
    )
    with pytest.raises(IncidentCorrelationConflictError):
        await repository.create_or_correlate(
            split_selector,
            idempotency_key="contract-split-selector",
            actor="contract-detector",
            reason="repository contract split selector",
            trace_id=split_selector.trace_id,
        )
    assert await repository.get(split_selector.incident_id) is None
    assert tuple(item.incident_id for item in await repository.list()) == (
        "contract-incident-a",
        "contract-incident-z",
    )

    baseline_ids = tuple(item.incident_id for item in await repository.list())
    oversized = Incident(
        incident_id="contract-oversized",
        trace_id="contract-trace-oversized",
        model_metadata={"safe_blob": "x" * 300_000},
    )
    with pytest.raises(UnsafeIncidentWriteError):
        await repository.create(
            oversized,
            idempotency_key="contract-oversized-create",
            actor="contract-detector",
            reason="repository contract size boundary",
            trace_id=oversized.trace_id,
        )

    nested: dict[str, object] = {"value": "safe"}
    for index in range(30):
        nested = {f"level_{index}": nested}
    too_deep = Incident(
        incident_id="contract-too-deep",
        trace_id="contract-trace-too-deep",
        model_metadata=nested,
    )
    with pytest.raises(UnsafeIncidentWriteError):
        await repository.create(
            too_deep,
            idempotency_key="contract-depth-create",
            actor="contract-detector",
            reason="repository contract depth boundary",
            trace_id=too_deep.trace_id,
        )

    for field in ("idempotency_key", "trace_id"):
        metadata = {
            "idempotency_key": "contract-safe-key",
            "actor": "contract-detector",
            "reason": "repository contract privacy boundary",
            "trace_id": "contract-safe-trace",
        }
        metadata[field] = "IMSI:310410000000001"
        with pytest.raises(UnsafeIncidentWriteError) as error:
            await repository.create(
                Incident(
                    incident_id=f"contract-private-{field}",
                    trace_id="contract-safe-trace",
                ),
                **metadata,
            )
        assert "310410000000001" not in str(error.value)

    assert tuple(item.incident_id for item in await repository.list()) == baseline_ids


__all__ = ["ContractClock", "assert_incident_repository_contract"]
