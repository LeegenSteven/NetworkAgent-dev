from __future__ import annotations

from datetime import timedelta

import pytest

from telco_cloud_mcp.models import CloudMcpInputError
from telco_cloud_mcp.service import CloudMcpService, MAX_RESPONSE_BYTES
from telco_domain import EvidenceType, IncidentStatus, Technology

from .conftest import (
    FakeIncidentRepository,
    FakeTelemetryRepository,
    NOW,
    RESOURCE,
    incident,
)


@pytest.mark.asyncio
async def test_get_list_and_history_are_structured_and_bounded(repositories) -> None:
    incidents, telemetry = repositories
    service = CloudMcpService(incidents, telemetry)
    result = await service.get_incident("incident-1")
    assert result["ok"] is True
    assert result["data"]["incident"]["incident_id"] == "incident-1"

    result = await service.list_incidents(
        status="DETECTED", limit=10, offset=0
    )
    assert result["ok"] is True
    assert incidents.calls[-1] == (
        "list",
        (IncidentStatus.DETECTED, 10, 0),
    )

    result = await service.get_history("incident-1", limit=10, offset=0)
    assert result["data"]["events"][0]["event_id"] == "audit-1"
    assert incidents.calls[-1] == ("history", ("incident-1", 10, 0))


@pytest.mark.asyncio
async def test_evidence_and_kpi_queries_forward_exact_safe_scope(repositories) -> None:
    incidents, telemetry = repositories
    service = CloudMcpService(incidents, telemetry)
    start = "2026-08-28T00:00:00Z"
    end = "2026-08-28T01:00:00Z"

    result = await service.collect_evidence(
        "incident-1",
        window_start=start,
        window_end=end,
        resource_ids=("resource-1",),
        evidence_types=("LOG",),
        limit=25,
    )
    assert result["ok"] is True
    evidence_call = telemetry.calls[-1][1]
    assert evidence_call[0].affected_resources[0].resource_id == "resource-1"
    assert evidence_call[3] == 25

    result = await service.query_kpis(
        kpi_names=("host_cpu_utilization",),
        technology="5G_SA",
        window_start=start,
        window_end=end,
        resource_ids=("resource-1",),
        limit=50,
    )
    assert result["ok"] is True
    kwargs = telemetry.calls[-1][1]
    assert kwargs["technology"] is Technology.FIVE_G_SA
    assert kwargs["resource_ids"] == ("resource-1",)
    assert kwargs["limit"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_types",
    (
        None,
        "LOG",
        ("LOG",) * (len(EvidenceType) + 1),
    ),
)
async def test_evidence_type_budget_rejects_before_repository_call(
    invalid_types,
) -> None:
    incidents = FakeIncidentRepository()
    telemetry = FakeTelemetryRepository()
    service = CloudMcpService(incidents, telemetry)

    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start="2026-08-28T00:00:00Z",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=("resource-1",),
            evidence_types=invalid_types,  # type: ignore[arg-type]
            limit=10,
        )

    assert captured.value.code == "MCP_EVIDENCE_TYPE_INVALID"
    assert incidents.calls == []
    assert telemetry.calls == []


@pytest.mark.asyncio
async def test_evidence_type_budget_does_not_consume_custom_iterable() -> None:
    class CountingIterable:
        def __init__(self) -> None:
            self.count = 0

        def __iter__(self):
            for _ in range(100_000):
                self.count += 1
                yield "LOG"

    invalid_types = CountingIterable()
    incidents = FakeIncidentRepository()
    telemetry = FakeTelemetryRepository()
    service = CloudMcpService(incidents, telemetry)

    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start="2026-08-28T00:00:00Z",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=("resource-1",),
            evidence_types=invalid_types,  # type: ignore[arg-type]
            limit=10,
        )

    assert captured.value.code == "MCP_EVIDENCE_TYPE_INVALID"
    assert invalid_types.count == 0
    assert incidents.calls == []
    assert telemetry.calls == []


@pytest.mark.asyncio
async def test_resource_resolution_is_exact_and_bounded(repositories) -> None:
    incidents, telemetry = repositories
    service = CloudMcpService(incidents, telemetry)
    result = await service.resolve_resources(
        resource_ids=("resource-1",), technology="5G_SA", limit=10
    )
    assert result["ok"] is True
    kwargs = telemetry.calls[-1][1]
    assert kwargs == {
        "resource_ids": ("resource-1",),
        "technology": Technology.FIVE_G_SA,
        "limit": 10,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.list_incidents(status="UNKNOWN_STATUS", limit=1, offset=0),
        lambda service: service.list_incidents(status=None, limit=101, offset=0),
        lambda service: service.get_history("incident-1", limit=1, offset=100_001),
        lambda service: service.query_kpis(
            kpi_names=("cpu",),
            technology="UNKNOWN",
            window_start="2026-08-01T00:00:00Z",
            window_end="2026-09-02T00:00:00Z",
            resource_ids=(),
            limit=1,
        ),
        lambda service: service.query_kpis(
            kpi_names=("cpu",),
            technology="UNKNOWN",
            window_start="2026-08-28T00:00:00",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=(),
            limit=1,
        ),
        lambda service: service.resolve_resources(
            resource_ids=tuple(f"resource-{index}" for index in range(101)),
            technology=None,
            limit=100,
        ),
        lambda service: service.query_kpis(
            kpi_names=tuple(f"kpi-{index}" for index in range(17)),
            technology="5G_SA",
            window_start="2026-08-28T00:00:00Z",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=(),
            limit=1,
        ),
    ],
)
async def test_invalid_scopes_fail_closed(repositories, operation) -> None:
    service = CloudMcpService(*repositories)
    with pytest.raises(CloudMcpInputError):
        await operation(service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        (
            lambda service: service.get_incident("\ud800"),
            "MCP_IDENTIFIER_INVALID",
        ),
        (
            lambda service: service.query_kpis(
                kpi_names=("\ud800",),
                technology="5G_SA",
                window_start="2026-08-28T00:00:00Z",
                window_end="2026-08-28T01:00:00Z",
                resource_ids=(),
                limit=1,
            ),
            "MCP_KPI_SCOPE_INVALID",
        ),
        (
            lambda service: service.resolve_resources(
                resource_ids=("\ud800",), technology=None, limit=1
            ),
            "MCP_RESOURCE_SCOPE_INVALID",
        ),
    ],
)
async def test_unpaired_surrogate_identifiers_fail_before_repository_call(
    repositories, operation, expected_code
) -> None:
    incidents, telemetry = repositories
    service = CloudMcpService(incidents, telemetry)

    with pytest.raises(CloudMcpInputError) as captured:
        await operation(service)

    assert captured.value.code == expected_code
    assert incidents.calls == []
    assert telemetry.calls == []


@pytest.mark.asyncio
async def test_privacy_and_size_are_rechecked_at_mcp_egress() -> None:
    telemetry = FakeTelemetryRepository()
    privacy_service = CloudMcpService(
        FakeIncidentRepository(incident(description="IMSI: 001010000000001")),
        telemetry,
    )
    with pytest.raises(CloudMcpInputError) as captured:
        await privacy_service.get_incident("incident-1")
    assert captured.value.code == "MCP_RESPONSE_PRIVACY_REJECTED"

    oversized = incident(description="x" * (MAX_RESPONSE_BYTES + 1))
    size_service = CloudMcpService(FakeIncidentRepository(oversized), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await size_service.get_incident("incident-1")
    assert captured.value.code == "MCP_RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_repository_stream_stops_at_cumulative_response_budget() -> None:
    repository = FakeIncidentRepository()
    consumed = 0
    large = incident(description="x" * 100_000)

    def results():
        nonlocal consumed
        for index in range(100):
            consumed += 1
            yield large.model_copy(update={"incident_id": f"incident-{index}"})

    async def large_list(*, status, limit, offset):
        return results()

    repository.list = large_list  # type: ignore[method-assign]
    service = CloudMcpService(repository, FakeTelemetryRepository())

    with pytest.raises(CloudMcpInputError) as captured:
        await service.list_incidents(status=None, limit=100, offset=0)

    assert captured.value.code == "MCP_RESPONSE_TOO_LARGE"
    assert consumed == 3


@pytest.mark.asyncio
async def test_repository_stream_stops_after_limit_plus_one() -> None:
    repository = FakeIncidentRepository()
    consumed = 0

    def results():
        nonlocal consumed
        for index in range(100):
            consumed += 1
            yield incident().model_copy(update={"incident_id": f"incident-{index}"})

    async def excessive_list(*, status, limit, offset):
        return results()

    repository.list = excessive_list  # type: ignore[method-assign]
    service = CloudMcpService(repository, FakeTelemetryRepository())

    with pytest.raises(CloudMcpInputError) as captured:
        await service.list_incidents(status=None, limit=2, offset=0)

    assert captured.value.code == "MCP_RESPONSE_LIMIT_VIOLATED"
    assert consumed == 3


@pytest.mark.asyncio
async def test_not_found_is_a_fixed_safe_response() -> None:
    repository = FakeIncidentRepository()
    repository.value = None
    service = CloudMcpService(repository, FakeTelemetryRepository())
    result = await service.get_incident("incident-not-found")
    assert result == {
        "schema_version": "1.0",
        "ok": False,
        "data": None,
        "error": {"code": "MCP_INCIDENT_NOT_FOUND"},
    }


@pytest.mark.asyncio
async def test_repository_results_are_revalidated_against_request_scope() -> None:
    start = "2026-08-28T00:00:00Z"
    end = "2026-08-28T01:00:00Z"

    telemetry = FakeTelemetryRepository()
    observation = (await telemetry.query_kpis())[0]

    async def out_of_scope_kpis(**kwargs):
        return (observation.model_copy(update={"kpi_name": "other-kpi"}),)

    telemetry.query_kpis = out_of_scope_kpis  # type: ignore[method-assign]
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.query_kpis(
            kpi_names=("host_cpu_utilization",),
            technology="5G_SA",
            window_start=start,
            window_end=end,
            resource_ids=("resource-1",),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_SCOPE_VIOLATED"


@pytest.mark.asyncio
async def test_evidence_fails_closed_on_incident_identity_or_type_drift() -> None:
    start = "2026-08-28T00:00:00Z"
    end = "2026-08-28T01:00:00Z"
    wrong_incident = incident().model_copy(update={"incident_id": "incident-other"})
    service = CloudMcpService(
        FakeIncidentRepository(wrong_incident), FakeTelemetryRepository()
    )
    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start=start,
            window_end=end,
            resource_ids=("resource-1",),
            evidence_types=("LOG",),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_SCOPE_VIOLATED"


@pytest.mark.asyncio
async def test_evidence_window_metadata_cannot_expand_requested_scope() -> None:
    telemetry = FakeTelemetryRepository()
    evidence = (await telemetry.collect_evidence(None))[0]
    attributes = dict(evidence.attributes)
    attributes["window_start"] = "2026-08-27T00:00:00Z"

    async def expanded_window(*args, **kwargs):
        return (evidence.model_copy(update={"attributes": attributes}),)

    telemetry.collect_evidence = expanded_window  # type: ignore[method-assign]
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start="2026-08-28T00:00:00Z",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=("resource-1",),
            evidence_types=("LOG",),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_SCOPE_VIOLATED"


@pytest.mark.asyncio
async def test_all_repository_domain_models_are_revalidated() -> None:
    start = "2026-08-28T00:00:00Z"
    end = "2026-08-28T01:00:00Z"
    incidents = FakeIncidentRepository(
        incident().model_copy(update={"incident_id": ""})
    )
    service = CloudMcpService(incidents, FakeTelemetryRepository())
    with pytest.raises(CloudMcpInputError) as captured:
        await service.get_incident("incident-1")
    assert captured.value.code == "MCP_RESPONSE_INVALID"

    incidents = FakeIncidentRepository()
    audit = (await incidents.history("incident-1", limit=1, offset=0))[0]

    async def invalid_history(*args, **kwargs):
        return (audit.model_copy(update={"event_id": ""}),)

    incidents.history = invalid_history  # type: ignore[method-assign]
    service = CloudMcpService(incidents, FakeTelemetryRepository())
    with pytest.raises(CloudMcpInputError) as captured:
        await service.get_history("incident-1", limit=1, offset=0)
    assert captured.value.code == "MCP_RESPONSE_INVALID"

    telemetry = FakeTelemetryRepository()
    evidence = (await telemetry.collect_evidence(None))[0]

    async def invalid_evidence(*args, **kwargs):
        return (evidence.model_copy(update={"evidence_id": ""}),)

    telemetry.collect_evidence = invalid_evidence  # type: ignore[method-assign]
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start="2026-08-28T00:00:00Z",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=("resource-1",),
            evidence_types=("LOG",),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_INVALID"

    telemetry = FakeTelemetryRepository()
    observation = (await telemetry.query_kpis())[0]

    async def invalid_kpi(**kwargs):
        return (observation.model_copy(update={"observation_id": ""}),)

    telemetry.query_kpis = invalid_kpi  # type: ignore[method-assign]
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.query_kpis(
            kpi_names=("host_cpu_utilization",),
            technology="5G_SA",
            window_start="2026-08-28T00:00:00Z",
            window_end="2026-08-28T01:00:00Z",
            resource_ids=("resource-1",),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_INVALID"

    telemetry = FakeTelemetryRepository()

    async def invalid_resource(**kwargs):
        return (RESOURCE.model_copy(update={"resource_id": ""}),)

    telemetry.resolve_resource_references = (  # type: ignore[method-assign]
        invalid_resource
    )
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.resolve_resources(
            resource_ids=("resource-1",), technology="5G_SA", limit=10
        )
    assert captured.value.code == "MCP_RESPONSE_INVALID"

    telemetry = FakeTelemetryRepository()
    trace = (await telemetry.collect_evidence(None))[0].model_copy(
        update={"evidence_type": EvidenceType.TRACE}
    )

    async def mixed_type(*args, **kwargs):
        return (trace,)

    telemetry.collect_evidence = mixed_type  # type: ignore[method-assign]
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start=start,
            window_end=end,
            resource_ids=("resource-1",),
            evidence_types=("LOG",),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_SCOPE_VIOLATED"

    telemetry = FakeTelemetryRepository()
    evidence = (await telemetry.collect_evidence(None))[0]

    async def out_of_scope_evidence(*args, **kwargs):
        return (
            evidence.model_copy(
                update={
                    "attributes": {
                        "resource_scope": [
                            RESOURCE.model_copy(
                                update={"resource_id": "resource-other"}
                            ).stable_identity()
                        ]
                    }
                }
            ),
        )

    telemetry.collect_evidence = out_of_scope_evidence  # type: ignore[method-assign]
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.collect_evidence(
            "incident-1",
            window_start=start,
            window_end=end,
            resource_ids=("resource-1",),
            evidence_types=(),
            limit=10,
        )
    assert captured.value.code == "MCP_RESPONSE_SCOPE_VIOLATED"

    telemetry = FakeTelemetryRepository()

    async def out_of_scope_resource(**kwargs):
        return (RESOURCE.model_copy(update={"resource_id": "resource-other"}),)

    telemetry.resolve_resource_references = (  # type: ignore[method-assign]
        out_of_scope_resource
    )
    service = CloudMcpService(FakeIncidentRepository(), telemetry)
    with pytest.raises(CloudMcpInputError) as captured:
        await service.resolve_resources(
            resource_ids=("resource-1",), technology="5G_SA", limit=10
        )
    assert captured.value.code == "MCP_RESPONSE_SCOPE_VIOLATED"
