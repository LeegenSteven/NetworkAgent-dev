from __future__ import annotations

import asyncio
import builtins
import json
from datetime import timedelta

import pytest
from telco_domain.models import (
    KpiObservation,
    ResourceReference,
    ResourceType,
    Technology,
)

from telco_cloud import CloudKpiDetectionService, SpannerIncidentRepository

from fake_spanner import FakeDatabase, NOW


RULE = {
    "schema_version": "1.0",
    "rule_id": "lte.erab.security-setup",
    "version": "1.0.0",
    "technology": "LTE",
    "is_current": True,
    "description_zh": "检测 ERAB 建立成功率异常。",
    "detection": {
        "kpi_name": "erab_success_rate",
        "comparator": "LT",
        "threshold": 97.0,
        "unit": "%",
        "max_gap_minutes": 30,
    },
    "analysis": {
        "evidence_types": ["TRACE"],
        "when": {
            "operator": "ALL",
            "predicates": [
                {
                    "fact": "failed_security_setup_ratio",
                    "comparator": "GTE",
                    "value": 0.5,
                }
            ],
        },
        "hypothesis_zh": "安全配置异常可能导致建立失败。",
        "root_cause_zh": "安全配置异常。",
    },
    "severity": {"cases": [], "default": "LOW"},
}


class Telemetry:
    async def query_kpis(self, **kwargs):
        return (
            KpiObservation(
                observation_id="observation-cloud-01",
                kpi_name="erab_success_rate",
                observed_value=90.0,
                observed_at=NOW,
                resources=(
                    ResourceReference(
                        resource_id="lte:enodeb:7:cell:700",
                        resource_type=ResourceType.CELL,
                        technology=Technology.LTE,
                    ),
                ),
                unit="%",
                source_uri="spanner://RadioKpiObservationsV1/observation-cloud-01",
            ),
        )

    async def collect_evidence(self, incident, **kwargs):
        return ()


def test_scan_is_read_only_and_explicit_correlate_is_bounded_idempotent(tmp_path) -> None:
    pytest.importorskip("duckdb", reason="requires telco-cloud[detector]")
    (tmp_path / "rule.json").write_text(json.dumps(RULE), encoding="utf-8")
    database = FakeDatabase()
    incidents = SpannerIncidentRepository(database, clock=lambda: NOW)
    service = CloudKpiDetectionService(
        tmp_path,
        incident_repository=incidents,
        telemetry_repository=Telemetry(),
        clock=lambda: NOW,
        max_candidates=5,
        max_writes=5,
    )

    async def scenario() -> None:
        scope = {
            "window_start": NOW - timedelta(minutes=5),
            "window_end": NOW,
            "resource_ids": ("lte:enodeb:7:cell:700",),
            "trace_id": "trace-cloud-scan",
            "workflow_id": "workflow-cloud-scan",
        }
        candidates = await service.scan(**scope)
        assert len(candidates) == 1
        assert database.count("CanonicalIncidentsV2") == 0

        first = await service.scan_and_correlate(**scope)
        replay = await service.scan_and_correlate(**scope)
        assert replay == first
        assert len(first) == 1
        assert database.count("CanonicalIncidentsV2") == 1
        assert database.count("CanonicalIncidentAuditV2") == 1

    asyncio.run(scenario())


def test_detector_extra_missing_fails_with_safe_actionable_error(
    tmp_path, monkeypatch
) -> None:
    original_import = builtins.__import__

    def reject_optional_local(name, *args, **kwargs):
        if name.startswith("telco_local"):
            raise ImportError("simulated optional dependency absence")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_optional_local)

    try:
        CloudKpiDetectionService(
            tmp_path,
            incident_repository=object(),
            telemetry_repository=object(),
        )
    except RuntimeError as error:
        assert str(error) == (
            "Cloud KPI detection requires the telco-cloud[detector] extra"
        )
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("missing detector extra was not rejected")
