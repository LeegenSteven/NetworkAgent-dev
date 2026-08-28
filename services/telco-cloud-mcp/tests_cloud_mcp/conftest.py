from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telco_domain import (
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentAuditEvent,
    IncidentSeverity,
    IncidentStatus,
    KpiObservation,
    ResourceReference,
    ResourceType,
    Technology,
)


NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
RESOURCE = ResourceReference(
    resource_id="resource-1",
    resource_type=ResourceType.NETWORK_NODE,
    technology=Technology.FIVE_G_SA,
)


def incident(*, description: str = "safe") -> Incident:
    return Incident(
        incident_id="incident-1",
        correlation_key="correlation-1",
        source_event_ids=("event-1",),
        technology=Technology.FIVE_G_SA,
        severity=IncidentSeverity.HIGH,
        title="test incident",
        description=description,
        affected_resources=(RESOURCE,),
        detected_at=NOW,
        trace_id="trace-1",
    )


class FakeIncidentRepository:
    def __init__(self, value: Incident | None = None) -> None:
        self.value = incident() if value is None else value
        self.calls: list[tuple[str, object]] = []
        self.failure: Exception | None = None

    async def get(self, incident_id: str):
        self.calls.append(("get", incident_id))
        if self.failure:
            raise self.failure
        return self.value

    async def list(self, *, status=None, limit=100, offset=0):
        self.calls.append(("list", (status, limit, offset)))
        return () if self.value is None else (self.value,)

    async def history(self, incident_id: str, *, limit: int, offset: int):
        self.calls.append(("history", (incident_id, limit, offset)))
        if self.value is None:
            return ()
        result = (
            IncidentAuditEvent(
                event_id="audit-1",
                incident_id=incident_id,
                from_status=None,
                to_status=IncidentStatus.DETECTED,
                revision=0,
                actor="fault-ingress",
                reason="source event ingestion",
                idempotency_key="idempotency-1",
                trace_id="trace-1",
                occurred_at=NOW,
            ),
        )
        return result[offset : offset + limit]


class FakeTelemetryRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def collect_evidence(
        self, incident, *, window_start=None, window_end=None, limit=1000
    ):
        self.calls.append(
            ("evidence", (incident, window_start, window_end, limit))
        )
        return (
            EvidenceReference(
                evidence_id="evidence-1",
                evidence_type=EvidenceType.LOG,
                uri="spanner://logs/evidence-1",
                source="cloud-logging",
                summary="bounded safe summary",
                collected_at=NOW,
                attributes={
                    "window_start": "2026-08-28T00:00:00Z",
                    "window_end": "2026-08-28T01:00:00Z",
                    "resource_scope": [RESOURCE.stable_identity()],
                },
            ),
        )

    async def query_kpis(self, **kwargs):
        self.calls.append(("kpis", kwargs))
        return (
            KpiObservation(
                observation_id="observation-1",
                kpi_name="host_cpu_utilization",
                observed_value=42.0,
                observed_at=NOW,
                resources=(RESOURCE,),
                unit="percent",
                source_uri="spanner://metrics/observation-1",
            ),
        )

    async def resolve_resource_references(self, **kwargs):
        self.calls.append(("resources", kwargs))
        return (RESOURCE,)


@pytest.fixture
def repositories():
    return FakeIncidentRepository(), FakeTelemetryRepository()
