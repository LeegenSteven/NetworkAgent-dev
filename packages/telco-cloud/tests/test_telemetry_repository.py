from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from telco_domain.models import (
    EvidenceReference,
    EvidenceType,
    KpiObservation,
    ResourceReference,
    ResourceType,
    Technology,
)
from telco_domain.ports import UnsafeIncidentWriteError

from telco_cloud import SpannerTelemetryRepository
import telco_cloud.telemetry_repository as telemetry_repository

from fake_spanner import FakeDatabase, NOW
from test_incident_repository import _incident


def _run(awaitable):
    return asyncio.run(awaitable)


def test_query_kpis_returns_only_validated_domain_models_with_bounded_window() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        observation = KpiObservation(
            observation_id="observation-01",
            kpi_name="availability",
            observed_value=97.5,
            observed_at=NOW,
            resources=(
                ResourceReference(
                    resource_id="cell-01",
                    resource_type=ResourceType.CELL,
                    technology=Technology.LTE,
                ),
            ),
            unit="percent",
            source_uri="spanner://RadioKpiObservationsV1/observation-01",
        )
        database.seed(
            "RadioKpiObservationsV1",
            {
                "observation_id": observation.observation_id,
                "kpi_name": observation.kpi_name,
                "technology": Technology.LTE.value,
                "primary_resource_id": "cell-01",
                "observed_at": NOW,
                "payload": observation.model_dump(mode="json", round_trip=True),
            },
        )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)

        result = await repository.query_kpis(
            kpi_names=("availability",),
            technology=Technology.LTE,
            window_start=NOW - timedelta(minutes=5),
            window_end=NOW + timedelta(minutes=5),
            resource_ids=("cell-01",),
            limit=10,
        )
        assert result == (observation,)

        with pytest.raises(ValueError, match="31 days"):
            await repository.query_kpis(
                kpi_names=("availability",),
                technology=Technology.LTE,
                window_start=NOW - timedelta(days=32),
                window_end=NOW,
            )

    _run(scenario())


def test_selector_budgets_reject_before_consuming_or_querying_spanner() -> None:
    class CountingIterable:
        def __init__(self) -> None:
            self.count = 0

        def __iter__(self):
            for index in range(100_000):
                self.count += 1
                yield f"selector-{index}"

    class NoIoDatabase(FakeDatabase):
        def __init__(self) -> None:
            super().__init__()
            self.snapshot_calls = 0

        def snapshot(self, **kwargs):
            self.snapshot_calls += 1
            return super().snapshot(**kwargs)

    async def scenario() -> None:
        database = NoIoDatabase()
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)
        counting = CountingIterable()

        with pytest.raises(ValueError, match="sequence"):
            await repository.query_kpis(
                kpi_names=counting,  # type: ignore[arg-type]
                technology=Technology.LTE,
            )
        assert counting.count == 0

        with pytest.raises(ValueError, match="sequence"):
            await repository.query_kpis(
                kpi_names=("availability",),
                technology=Technology.LTE,
                resource_ids=None,  # type: ignore[arg-type]
            )

        with pytest.raises(ValueError, match="at most"):
            await repository.resolve_resource_references(
                resource_ids=tuple(
                    f"resource-{index}" for index in range(101)
                ),
                technology=Technology.LTE,
                limit=10,
            )
        assert database.snapshot_calls == 0

    _run(scenario())


def test_collect_evidence_returns_references_not_raw_content() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        resource = ResourceReference(
            resource_id="cell-01",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        evidence = EvidenceReference(
            evidence_id="evidence-01",
            evidence_type=EvidenceType.LOG,
            uri="spanner://SafeEvidenceReferencesV1/evidence-01",
            source="cloud-logging-normalizer",
            summary="Health check failed for the affected cell",
            collected_at=NOW,
            attributes={
                "resource_scope": [resource.stable_identity()],
                "window_start": (NOW - timedelta(minutes=10)).isoformat(),
                "window_end": NOW.isoformat(),
            },
        )
        database.seed(
            "SafeEvidenceReferencesV1",
            {
                "evidence_id": evidence.evidence_id,
                "incident_id": "incident-01",
                "evidence_type": EvidenceType.LOG.value,
                "collected_at": NOW,
                "payload": evidence.model_dump(mode="json", round_trip=True),
            },
        )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)
        incident = _incident().model_copy(
            update={
                "technology": Technology.LTE,
                "affected_resources": (resource,),
                "window_start": NOW - timedelta(minutes=10),
                "window_end": NOW,
            }
        )

        result = await repository.collect_evidence(incident, limit=10)

        assert result == (evidence,)
        assert all(isinstance(item, EvidenceReference) for item in result)

    _run(scenario())


def test_query_kpis_rejects_payload_whose_primary_resource_disagrees_with_row() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        row_resource = ResourceReference(
            resource_id="cell-row",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        payload_primary = ResourceReference(
            resource_id="cell-payload-primary",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        observation = KpiObservation(
            observation_id="observation-mismatch",
            kpi_name="availability",
            observed_value=97.5,
            observed_at=NOW,
            resources=(row_resource, payload_primary),
            source_uri="spanner://RadioKpiObservationsV1/observation-mismatch",
        )
        database.seed(
            "RadioKpiObservationsV1",
            {
                "observation_id": observation.observation_id,
                "kpi_name": observation.kpi_name,
                "technology": Technology.LTE.value,
                "primary_resource_id": row_resource.resource_id,
                "observed_at": NOW,
                "payload": observation.model_dump(mode="json", round_trip=True),
            },
        )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)

        with pytest.raises(RuntimeError, match="primary resource"):
            await repository.query_kpis(
                kpi_names=("availability",),
                technology=Technology.LTE,
                window_start=NOW - timedelta(minutes=5),
                window_end=NOW + timedelta(minutes=5),
                resource_ids=(row_resource.resource_id,),
                limit=10,
            )

    _run(scenario())


@pytest.mark.parametrize("scope_kind", ["missing", "sibling", "cross-technology"])
def test_collect_evidence_rejects_unverifiable_or_external_resource_scope(
    scope_kind: str,
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        requested = ResourceReference(
            resource_id="cell-requested",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        external = ResourceReference(
            resource_id="cell-sibling",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        cross_technology = ResourceReference(
            resource_id="cell-requested",
            resource_type=ResourceType.CELL,
            technology=Technology.FIVE_G_SA,
        )
        attributes = {
            "window_start": (NOW - timedelta(minutes=10)).isoformat(),
            "window_end": NOW.isoformat(),
        }
        if scope_kind == "sibling":
            attributes["resource_scope"] = [external.stable_identity()]
        elif scope_kind == "cross-technology":
            attributes["resource_scope"] = [cross_technology.stable_identity()]
        evidence = EvidenceReference(
            evidence_id=f"evidence-{scope_kind}",
            evidence_type=EvidenceType.LOG,
            uri=f"spanner://SafeEvidenceReferencesV1/evidence-{scope_kind}",
            collected_at=NOW,
            attributes=attributes,
        )
        database.seed(
            "SafeEvidenceReferencesV1",
            {
                "evidence_id": evidence.evidence_id,
                "incident_id": "incident-01",
                "evidence_type": EvidenceType.LOG.value,
                "collected_at": NOW,
                "payload": evidence.model_dump(mode="json", round_trip=True),
            },
        )
        incident = _incident().model_copy(
            update={
                "technology": Technology.LTE,
                "affected_resources": (requested,),
                "window_start": NOW - timedelta(minutes=10),
                "window_end": NOW,
            }
        )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)

        with pytest.raises(RuntimeError, match="resource scope"):
            await repository.collect_evidence(incident, limit=10)

    _run(scenario())


@pytest.mark.parametrize(
    "time_scope",
    [
        {},
        {"window_start": "not-a-time", "window_end": NOW.isoformat()},
        {
            "window_start": (NOW - timedelta(minutes=11)).isoformat(),
            "window_end": NOW.isoformat(),
        },
        {
            "window_start": NOW.isoformat(),
            "window_end": (NOW - timedelta(minutes=1)).isoformat(),
        },
    ],
)
def test_collect_evidence_rejects_missing_invalid_or_expanded_time_scope(
    time_scope: dict[str, str],
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        resource = ResourceReference(
            resource_id="cell-requested",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        evidence = EvidenceReference(
            evidence_id="evidence-time-scope",
            evidence_type=EvidenceType.LOG,
            uri="spanner://SafeEvidenceReferencesV1/evidence-time-scope",
            collected_at=NOW,
            attributes={
                "resource_scope": [resource.stable_identity()],
                **time_scope,
            },
        )
        database.seed(
            "SafeEvidenceReferencesV1",
            {
                "evidence_id": evidence.evidence_id,
                "incident_id": "incident-01",
                "evidence_type": EvidenceType.LOG.value,
                "collected_at": NOW,
                "payload": evidence.model_dump(mode="json", round_trip=True),
            },
        )
        incident = _incident().model_copy(
            update={
                "technology": Technology.LTE,
                "affected_resources": (resource,),
                "window_start": NOW - timedelta(minutes=10),
                "window_end": NOW,
            }
        )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)

        with pytest.raises(RuntimeError, match="evidence time scope"):
            await repository.collect_evidence(incident, limit=10)

    _run(scenario())


@pytest.mark.parametrize("padding_size,count", [(270_000, 1), (140_000, 2)])
def test_query_kpis_streams_with_single_and_cumulative_payload_budget(
    padding_size: int, count: int, monkeypatch
) -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        if count > 1:
            monkeypatch.setattr(
                telemetry_repository,
                "MAX_TELEMETRY_BATCH_BYTES",
                200_000,
            )
        resource = ResourceReference(
            resource_id="cell-budget",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        for index in range(count):
            observation = KpiObservation(
                observation_id=f"observation-budget-{index}",
                kpi_name="availability",
                observed_value=97.5,
                observed_at=NOW + timedelta(seconds=index),
                resources=(resource,),
                source_uri=f"spanner://budget/{index}",
                dimensions={"padding": "x" * padding_size},
            )
            database.seed(
                "RadioKpiObservationsV1",
                {
                    "observation_id": observation.observation_id,
                    "kpi_name": observation.kpi_name,
                    "technology": Technology.LTE.value,
                    "primary_resource_id": resource.resource_id,
                    "observed_at": observation.observed_at,
                    "payload": observation.model_dump(
                        mode="json", round_trip=True
                    ),
                },
            )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)

        with pytest.raises(UnsafeIncidentWriteError, match="serialized"):
            await repository.query_kpis(
                kpi_names=("availability",),
                technology=Technology.LTE,
                window_start=NOW - timedelta(minutes=1),
                window_end=NOW + timedelta(minutes=1),
                resource_ids=(resource.resource_id,),
                limit=10,
            )

    _run(scenario())


def test_collect_evidence_rejects_oversized_single_streamed_reference() -> None:
    async def scenario() -> None:
        database = FakeDatabase()
        resource = ResourceReference(
            resource_id="cell-budget",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
        )
        evidence = EvidenceReference(
            evidence_id="evidence-budget",
            evidence_type=EvidenceType.LOG,
            uri="spanner://SafeEvidenceReferencesV1/evidence-budget",
            summary="x" * 270_000,
            collected_at=NOW,
            attributes={
                "resource_scope": [resource.stable_identity()],
                "window_start": (NOW - timedelta(minutes=10)).isoformat(),
                "window_end": NOW.isoformat(),
            },
        )
        database.seed(
            "SafeEvidenceReferencesV1",
            {
                "evidence_id": evidence.evidence_id,
                "incident_id": "incident-01",
                "evidence_type": evidence.evidence_type.value,
                "collected_at": NOW,
                "payload": evidence.model_dump(mode="json", round_trip=True),
            },
        )
        incident = _incident().model_copy(
            update={
                "technology": Technology.LTE,
                "affected_resources": (resource,),
                "window_start": NOW - timedelta(minutes=10),
                "window_end": NOW,
            }
        )
        repository = SpannerTelemetryRepository(database, clock=lambda: NOW)

        with pytest.raises(UnsafeIncidentWriteError, match="serialized"):
            await repository.collect_evidence(incident, limit=10)

    _run(scenario())
