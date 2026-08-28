"""Privacy-safe DuckDB telemetry adapter tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from telco_domain import (
    EvidenceType,
    Incident,
    KpiComparator,
    KpiViolation,
    MetricRepository,
    ResourceReference,
    ResourceType,
    SensitiveDataError,
    Technology,
    TelemetryRepository,
)
from telco_local.lte_identifiers import LTE_IDENTIFIER_MAX
from telco_local.telemetry import DuckDbTelemetryRepository


BASE_TIME = datetime(2025, 11, 20, tzinfo=UTC)


def _run(coroutine):
    return asyncio.run(coroutine)


def _resources(
    enodeb_id: str = "1", cell_id: str = "12314"
) -> tuple[ResourceReference, ResourceReference]:
    enodeb_resource_id = f"lte:enodeb:{enodeb_id}"
    return (
        ResourceReference(
            resource_id=enodeb_resource_id,
            resource_type=ResourceType.ENODEB,
            technology=Technology.LTE,
            external_ids={"enodeb_id": enodeb_id},
        ),
        ResourceReference(
            resource_id=f"{enodeb_resource_id}:cell:{cell_id}",
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
            parent_resource_id=enodeb_resource_id,
            external_ids={"enodeb_id": enodeb_id, "cell_id": cell_id},
        ),
    )


def _incident() -> Incident:
    resources = _resources()
    violation = KpiViolation(
        violation_id="violation-erab",
        kpi_name="erab_success_rate",
        observed_value=96.0,
        threshold_value=97.0,
        comparator=KpiComparator.LT,
        unit="%",
        window_start=BASE_TIME,
        window_end=BASE_TIME + timedelta(minutes=15),
        rule_id="lte.erab.security-setup",
        rule_version="1.0.0",
        resource_ids=(resources[1].resource_id,),
    )
    return Incident(
        incident_id="incident-telemetry",
        technology=Technology.LTE,
        affected_resources=resources,
        detected_at=BASE_TIME + timedelta(minutes=15),
        window_start=BASE_TIME,
        window_end=BASE_TIME + timedelta(minutes=15),
        violated_kpis=(violation,),
        trace_id="trace-telemetry",
        created_at=BASE_TIME + timedelta(minutes=15),
        updated_at=BASE_TIME + timedelta(minutes=15),
    )


def test_query_kpis_is_rule_neutral_ordered_bounded_and_utc(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbTelemetryRepository(
            initialized_config,
        )
        assert isinstance(repository, MetricRepository)
        assert isinstance(repository, TelemetryRepository)

        observations = tuple(
            await repository.query_kpis(
                kpi_names=(
                    "erab_success_rate",
                    "retainability",
                    "uplink_rssi_avg",
                ),
                technology=Technology.LTE,
            )
        )
        replay = tuple(
            await repository.query_kpis(
                kpi_names=(
                    "uplink_rssi_avg",
                    "retainability",
                    "erab_success_rate",
                ),
                technology=Technology.LTE,
            )
        )

        assert replay == observations
        assert len(observations) == 6
        assert [
            (item.observed_at, item.resources[-1].resource_id, item.kpi_name)
            for item in observations
        ] == sorted(
            (item.observed_at, item.resources[-1].resource_id, item.kpi_name)
            for item in observations
        )
        assert all(item.observed_at.tzinfo is UTC for item in observations)
        assert all(item.resources[-1].technology is Technology.LTE for item in observations)
        assert {
            item.kpi_name: item.unit for item in observations[:3]
        } == {
            "erab_success_rate": "%",
            "retainability": "releases/hour",
            "uplink_rssi_avg": "dBm",
        }
        assert [
            item.observed_value
            for item in observations
            if item.kpi_name == "uplink_rssi_avg"
        ] == [-100.0, -121.0]
        assert all(
            "SOURCE_TIMEZONE_ASSUMED_UTC" in item.quality_flags
            for item in observations
        )
        assert all(item.source_uri.startswith("duckdb://performance_kpi/") for item in observations)
        assert not hasattr(observations[0], "threshold")

        cell_id = observations[0].resources[-1].resource_id
        filtered = tuple(
            await repository.query_kpis(
                kpi_names=(
                    "erab_success_rate",
                    "retainability",
                    "uplink_rssi_avg",
                ),
                technology=Technology.LTE,
                resource_ids=(cell_id,),
            )
        )
        assert len(filtered) == 3
        assert all(item.resources[-1].resource_id == cell_id for item in filtered)

        assert await repository.query_kpis(
            kpi_names=("erab_success_rate",),
            technology=Technology.FIVE_G_SA,
        ) == ()
        with pytest.raises(ValueError, match="unsupported KPI"):
            await repository.query_kpis(
                kpi_names=("erab_success_rate; DROP TABLE performance",),
                technology=Technology.LTE,
            )
        with pytest.raises(ValueError, match="limit"):
            await repository.query_kpis(
                kpi_names=("erab_success_rate",),
                technology=Technology.LTE,
                limit=0,
            )

    _run(scenario())


def test_collect_evidence_returns_only_metric_and_safe_trace_aggregates(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbTelemetryRepository(
            initialized_config,
        )
        evidence = tuple(await repository.collect_evidence(_incident()))
        replay = tuple(await repository.collect_evidence(_incident()))

        assert replay == evidence
        assert all(
            item.collected_at == BASE_TIME + timedelta(minutes=15)
            for item in evidence
        )
        assert {item.evidence_type for item in evidence} == {
            EvidenceType.METRIC,
            EvidenceType.TRACE,
        }
        metric = next(item for item in evidence if item.evidence_type is EvidenceType.METRIC)
        trace = next(item for item in evidence if item.evidence_type is EvidenceType.TRACE)

        expected_scope = [
            resource.stable_identity()
            for resource in sorted(
                _incident().affected_resources,
                key=lambda item: item.resource_id,
            )
        ]
        for item in evidence:
            assert item.attributes["resource_scope"] == expected_scope
            assert item.attributes["window_start"] == BASE_TIME.isoformat()
            assert item.attributes["window_end"] == (
                BASE_TIME + timedelta(minutes=15)
            ).isoformat()

        assert metric.attributes["facts"]["kpi.erab_success_rate"] == pytest.approx(
            430 * 100 / 431
        )
        assert metric.attributes["sample_count"] == 1
        assert metric.attributes["total_sample_count"] == 1
        assert metric.attributes["valid_sample_count"] == 1
        assert metric.attributes["invalid_sample_count"] == 0
        assert metric.attributes["facts"]["uplink_rssi_avg"] == -100.0
        assert trace.attributes["facts"] == {
            "failed_security_setup_count": 1,
            "failure_count": 1,
            "failed_security_setup_ratio": 1.0,
        }
        assert trace.attributes["outcome_counts"] == {
            "FAILED_SECURITY_SETUP": 1,
            "SUCCESS": 1,
        }

        serialized = json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            ensure_ascii=False,
        ).lower()
        for forbidden in ("imsi", "msisdn", "imeisv", "310410000000001"):
            assert forbidden not in serialized

    _run(scenario())


def test_collect_evidence_requires_a_bounded_aware_window(initialized_config) -> None:
    async def scenario() -> None:
        repository = DuckDbTelemetryRepository(initialized_config)
        without_window = Incident(
            incident_id="incident-unbounded",
            technology=Technology.LTE,
            affected_resources=_resources(),
            trace_id="trace-unbounded",
        )
        with pytest.raises(ValueError, match="window"):
            await repository.collect_evidence(without_window)

        with pytest.raises(ValueError, match="timezone"):
            await repository.collect_evidence(
                _incident(),
                window_start=datetime(2025, 11, 20),
                window_end=datetime(2025, 11, 20, 0, 15),
            )

    _run(scenario())


def test_query_kpis_does_not_multiply_duplicate_source_keys(
    initialized_config,
) -> None:
    async def scenario() -> None:
        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO performance
                SELECT * FROM performance
                WHERE CAST(enodeb_id AS VARCHAR) = '1'
                  AND CAST(cell_id AS VARCHAR) = '12314'
                """
            )

        repository = DuckDbTelemetryRepository(initialized_config)
        observations = await repository.query_kpis(
            kpi_names=("erab_success_rate",),
            technology=Technology.LTE,
            resource_ids=("lte:enodeb:1:cell:12314",),
        )
        assert len(observations) == 2
        assert len({item.observation_id for item in observations}) == 2
        assert len(
            {item.dimensions["source_row_id"] for item in observations}
        ) == 2

    _run(scenario())


def test_query_kpis_uses_the_most_specific_resource_selector(
    initialized_config,
) -> None:
    async def scenario() -> None:
        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO performance
                SELECT * REPLACE ('99999' AS cell_id)
                FROM performance
                WHERE CAST(enodeb_id AS VARCHAR) = '1'
                  AND CAST(cell_id AS VARCHAR) = '12314'
                """
            )

        observations = await DuckDbTelemetryRepository(
            initialized_config
        ).query_kpis(
            kpi_names=("erab_success_rate",),
            technology=Technology.LTE,
            resource_ids=(
                "lte:enodeb:1",
                "lte:enodeb:1:cell:12314",
            ),
        )

        assert len(observations) == 1
        assert {
            item.resources[-1].resource_id for item in observations
        } == {"lte:enodeb:1:cell:12314"}

    _run(scenario())


def test_query_kpis_rejects_unbounded_selector_cardinality(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbTelemetryRepository(initialized_config)
        with pytest.raises(ValueError, match="kpi_names"):
            await repository.query_kpis(
                kpi_names=("erab_success_rate",) * 17,
                technology=Technology.LTE,
            )
        with pytest.raises(ValueError, match="resource_ids"):
            await repository.query_kpis(
                kpi_names=("erab_success_rate",),
                technology=Technology.LTE,
                resource_ids=tuple(
                    f"lte:enodeb:{index}" for index in range(1_001)
                ),
            )

    _run(scenario())


def test_trace_evidence_collapses_untrusted_high_cardinality_outcomes(
    initialized_config,
) -> None:
    async def scenario() -> None:
        rows = [
            (
                "UNTRUSTED",
                BASE_TIME,
                BASE_TIME + timedelta(seconds=1),
                "1",
                "12314",
                f"IMSI-3104100000{index:05d}",
            )
            for index in range(200)
        ]
        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.executemany(
                "INSERT INTO cell_traces VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

        evidence = await DuckDbTelemetryRepository(
            initialized_config
        ).collect_evidence(_incident())
        trace = next(
            item for item in evidence if item.evidence_type is EvidenceType.TRACE
        )
        assert set(trace.attributes["outcome_counts"]) <= {
            "SUCCESS",
            "FAILURE",
            "FAILED_SECURITY_SETUP",
            "OTHER",
        }
        assert trace.attributes["outcome_counts"]["OTHER"] == 200
        serialized = json.dumps(trace.model_dump(mode="json")).lower()
        assert "imsi" not in serialized
        assert "3104100000" not in serialized
        assert "procedure_type" not in serialized

    _run(scenario())


def test_public_telemetry_ports_fail_closed_on_sensitive_resource_values(
    initialized_config,
) -> None:
    async def scenario() -> None:
        sensitive = "IMSI:310410000000001"
        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.execute(
                "UPDATE performance SET enodeb_id = ? WHERE enodeb_id = '1'",
                [sensitive],
            )
            connection.execute(
                """
                UPDATE cell_traces
                SET start_enodeb_id = ?
                WHERE start_enodeb_id = '1'
                """,
                [sensitive],
            )

        repository = DuckDbTelemetryRepository(initialized_config)
        with pytest.raises(SensitiveDataError) as query_error:
            await repository.query_kpis(
                kpi_names=("erab_success_rate",),
                technology=Technology.LTE,
            )
        assert sensitive not in str(query_error.value)

        unsafe_payload = _incident().model_dump(mode="python", round_trip=True)
        unsafe_payload["affected_resources"] = _resources(sensitive, "12314")
        unsafe_incident = Incident.model_validate(unsafe_payload)
        with pytest.raises(SensitiveDataError) as evidence_error:
            await repository.collect_evidence(unsafe_incident)
        assert sensitive not in str(evidence_error.value)

    _run(scenario())


def test_query_kpis_rejects_unlabeled_subscriber_id_after_database_tamper(
    initialized_config,
) -> None:
    async def scenario() -> None:
        disguised_subscriber_id = "310410000000001"
        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.execute(
                "UPDATE performance SET enodeb_id = ? WHERE enodeb_id = '1'",
                [disguised_subscriber_id],
            )

        with pytest.raises(ValueError) as error:
            await DuckDbTelemetryRepository(
                initialized_config
            ).query_kpis(
                kpi_names=("erab_success_rate",),
                technology=Technology.LTE,
            )
        assert disguised_subscriber_id not in str(error.value)

    _run(scenario())


@pytest.mark.parametrize(
    "invalid_component",
    (
        "310410000000001",
        "000000000000001",
        "-1",
        "1e3",
        str(LTE_IDENTIFIER_MAX + 1),
    ),
)
def test_query_kpis_rejects_invalid_canonical_resource_selectors_without_echo(
    initialized_config,
    invalid_component: str,
) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError) as error:
            await DuckDbTelemetryRepository(
                initialized_config
            ).query_kpis(
                kpi_names=("erab_success_rate",),
                technology=Technology.LTE,
                resource_ids=(f"lte:enodeb:{invalid_component}",),
            )
        assert invalid_component not in str(error.value)

    _run(scenario())


def test_query_kpis_normalizes_database_rows_and_resource_selectors(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbTelemetryRepository(initialized_config)
        selected = await repository.query_kpis(
            kpi_names=("erab_success_rate",),
            technology=Technology.LTE,
            resource_ids=("lte:enodeb:0001:cell:00012314",),
        )
        assert len(selected) == 1
        assert selected[0].resources[-1].resource_id == (
            "lte:enodeb:1:cell:12314"
        )

        with duckdb.connect(str(initialized_config.database_path)) as connection:
            connection.execute(
                """
                UPDATE performance
                SET enodeb_id = '0001', cell_id = '00012314'
                WHERE enodeb_id = '1' AND cell_id = '12314'
                """
            )
        observations = await repository.query_kpis(
            kpi_names=("erab_success_rate",),
            technology=Technology.LTE,
        )
        normalized = next(
            item
            for item in observations
            if item.observed_at == BASE_TIME
        )
        assert normalized.resources[-1].resource_id == (
            "lte:enodeb:1:cell:12314"
        )
        assert normalized.dimensions["enodeb_id"] == "1"
        assert normalized.dimensions["cell_id"] == "12314"

    _run(scenario())


def test_collect_evidence_validates_and_normalizes_incident_resource_scope(
    initialized_config,
) -> None:
    async def scenario() -> None:
        repository = DuckDbTelemetryRepository(initialized_config)
        payload = _incident().model_dump(mode="python", round_trip=True)
        payload["affected_resources"] = _resources("0001", "00012314")
        normalized_evidence = await repository.collect_evidence(
            Incident.model_validate(payload)
        )
        for reference in normalized_evidence:
            resource_ids = {
                item["resource_id"]
                for item in reference.attributes["resource_scope"]
            }
            assert resource_ids == {
                "lte:enodeb:1",
                "lte:enodeb:1:cell:12314",
            }

        disguised_subscriber_id = "310410000000001"
        payload["affected_resources"] = _resources(
            disguised_subscriber_id,
            "12314",
        )
        with pytest.raises(ValueError) as error:
            await repository.collect_evidence(Incident.model_validate(payload))
        assert disguised_subscriber_id not in str(error.value)

    _run(scenario())
