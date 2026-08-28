from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest

from telco_cloud import SpannerIncidentRepository, SpannerTelemetryRepository
from telco_cloud_mcp.models import CloudMcpInputError
from telco_cloud_mcp.service import CloudMcpService
from telco_domain import (
    EvidenceReference,
    EvidenceType,
    KpiObservation,
    ResourceReference,
    ResourceType,
    Technology,
)

from .conftest import (
    FakeIncidentRepository,
    FakeTelemetryRepository,
    NOW,
    RESOURCE,
    incident,
)


class _Reader:
    def __init__(self, observation: KpiObservation, evidence: EvidenceReference):
        self.observation = observation
        self.evidence = evidence

    def execute_sql(self, sql, params=None, **kwargs):
        del kwargs
        params = params or {}
        if "telco-cloud:query-kpis" in sql:
            assert self.observation.resources[-1].resource_id in params["resource_ids"]
            return [
                (
                    self.observation.observation_id,
                    self.observation.kpi_name,
                    Technology.FIVE_G_SA.value,
                    self.observation.resources[-1].resource_id,
                    self.observation.observed_at,
                    self.observation.model_dump(mode="json", round_trip=True),
                )
            ]
        if "telco-cloud:collect-evidence" in sql:
            return [
                (
                    params["incident_id"],
                    self.evidence.evidence_id,
                    self.evidence.evidence_type.value,
                    self.evidence.collected_at,
                    self.evidence.model_dump(mode="json", round_trip=True),
                )
            ]
        raise AssertionError("unexpected bounded telemetry query")


class _Database:
    def __init__(self, observation: KpiObservation, evidence: EvidenceReference):
        self.reader = _Reader(observation, evidence)

    @contextmanager
    def snapshot(self, **kwargs):
        del kwargs
        yield self.reader


class _IncidentReader:
    def __init__(self, incidents):
        self.incidents = incidents

    def execute_sql(self, sql, params=None, **kwargs):
        del kwargs
        assert "telco-cloud:list-incidents" in sql
        params = params or {}
        rows = []
        for item in self.incidents[: int(params["limit"])]:
            rows.append(
                (
                    item.incident_id,
                    item.correlation_key,
                    item.schema_version,
                    item.technology.value,
                    item.status.value,
                    item.severity.value,
                    item.revision,
                    item.trace_id,
                    item.detected_at,
                    item.created_at,
                    item.updated_at,
                    item.model_dump(mode="json", round_trip=True),
                )
            )
        return rows


class _IncidentDatabase:
    def __init__(self, incidents):
        self.reader = _IncidentReader(incidents)

    @contextmanager
    def snapshot(self, **kwargs):
        del kwargs
        yield self.reader


@pytest.mark.asyncio
async def test_spanner_adapter_and_mcp_share_canonical_scope_semantics() -> None:
    window_start = NOW - timedelta(minutes=5)
    window_end = NOW + timedelta(minutes=5)
    parent = ResourceReference(
        resource_id="site-parent",
        resource_type=ResourceType.NETWORK_NODE,
        technology=Technology.FIVE_G_SA,
    )
    observation = KpiObservation(
        observation_id="observation-composed",
        kpi_name="host_cpu_utilization",
        observed_value=42.0,
        observed_at=NOW,
        # Canonical primary resource is the last entry. Related same-technology
        # ancestors are allowed to precede it and are not authorization keys.
        resources=(parent, RESOURCE),
        unit="percent",
        source_uri="spanner://metrics/observation-composed",
    )
    evidence = EvidenceReference(
        evidence_id="evidence-composed",
        evidence_type=EvidenceType.LOG,
        uri="spanner://evidence/evidence-composed",
        collected_at=NOW,
        attributes={
            "resource_scope": [RESOURCE.stable_identity()],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
    )
    telemetry = SpannerTelemetryRepository(
        _Database(observation, evidence), clock=lambda: NOW
    )
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    start = window_start.isoformat()
    end = window_end.isoformat()

    kpis = await service.query_kpis(
        kpi_names=("host_cpu_utilization",),
        technology="5G_SA",
        window_start=start,
        window_end=end,
        resource_ids=(RESOURCE.resource_id,),
        limit=10,
    )
    assert kpis["ok"] is True
    assert len(kpis["data"]["observations"][0]["resources"]) == 2

    evidence_result = await service.collect_evidence(
        "incident-1",
        window_start=start,
        window_end=end,
        resource_ids=(RESOURCE.resource_id,),
        evidence_types=("LOG",),
        limit=10,
    )
    assert evidence_result["ok"] is True
    assert evidence_result["data"]["evidence"][0]["evidence_id"] == (
        "evidence-composed"
    )


@pytest.mark.asyncio
async def test_spanner_page_reaches_mcp_cumulative_response_budget() -> None:
    rows = tuple(
        incident(description="x" * 90_000).model_copy(
            update={
                "incident_id": f"incident-large-{index}",
                "correlation_key": f"correlation-large-{index}",
                "source_event_ids": (f"event-large-{index}",),
                "trace_id": f"trace-large-{index}",
            }
        )
        for index in range(3)
    )
    incidents = SpannerIncidentRepository(_IncidentDatabase(rows), clock=lambda: NOW)
    service = CloudMcpService(incidents, FakeTelemetryRepository())

    with pytest.raises(CloudMcpInputError) as captured:
        await service.list_incidents(status=None, limit=3, offset=0)

    assert captured.value.code == "MCP_RESPONSE_TOO_LARGE"
