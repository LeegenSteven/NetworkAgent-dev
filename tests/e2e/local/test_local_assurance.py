"""Full-data Local Profile assurance slice: detect, confirm, and read-only RCA."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from telco_domain import (
    EvidenceType,
    IncidentStatus,
    RcaConclusion,
    RcaRequest,
    RcaResult,
    assert_model_safe,
)
from telco_local import LocalProfile, LocalProfileConfig


ROOT = Path(__file__).resolve().parents[3]


def _run(coroutine):
    return asyncio.run(coroutine)


def test_full_lte_assets_complete_local_assurance_without_actions(tmp_path) -> None:
    async def scenario() -> None:
        config = LocalProfileConfig(
            database_path=tmp_path / "local-assurance.duckdb",
            performance_csv_path=ROOT / "data/samples/lte-demo/performance.csv",
            safe_trace_csv_path=ROOT / "data/samples/lte-demo/safe-cell-traces.csv",
            rules_dir=ROOT / "data/rca-rules/lte",
            documents_dir=ROOT / "data/docs/lte",
            source_timezone="UTC",
        )
        profile = LocalProfile.initialize(config, reset=True)
        assert profile.database_summary.performance_rows == 13_440
        assert profile.database_summary.trace_rows == 579
        assert profile.database_summary.incident_rows == 0

        candidates = await profile.detector.scan(
            "trace-e2e-detect",
            workflow_id="workflow-e2e-detect",
        )
        assert len(candidates) == 15
        assert len({item.incident_id for item in candidates}) == 15
        assert all(item.workflow_id == "workflow-e2e-detect" for item in candidates)
        assert all(
            len(
                {
                    item.message_id,
                    item.workflow_id,
                    item.incident_id,
                    item.trace_id,
                    item.idempotency_key,
                }
            )
            == 5
            for item in candidates
        )
        assert await profile.incident_repository.list() == ()

        candidate = next(
            item
            for item in candidates
            if item.incident.affected_resources[-1].resource_id
            == "lte:enodeb:1:cell:12314"
        )
        created = await profile.detector.confirm(
            candidate.incident_id,
            trace_id="trace-e2e-confirm",
            idempotency_key="confirm-e2e-1",
            actor="operator",
            reason="用户确认将候选写入 Canonical Incident",
        )
        replay = await profile.detector.confirm(
            candidate.incident_id,
            trace_id="trace-e2e-confirm",
            idempotency_key="confirm-e2e-1",
            actor="operator",
            reason="用户确认将候选写入 Canonical Incident",
        )
        assert replay == created
        assert len(await profile.incident_repository.list()) == 1
        assert len(await profile.incident_repository.history(created.incident_id)) == 1

        request = RcaRequest(
            message_id="message-e2e-rca",
            workflow_id="workflow-e2e-rca",
            incident_id=created.incident_id,
            trace_id=created.trace_id,
            idempotency_key="request-e2e-rca",
            incident=created,
            based_on_revision=created.revision,
            requested_report_version=1,
        )
        result = await profile.rca_gateway.analyze(request)
        assert isinstance(result, RcaResult)
        assert result.report.conclusion is RcaConclusion.CONCLUSIVE
        assert result.report.root_cause
        assert result.report.model_metadata["rule_resolution"] == "EXACT"
        evidence_types = {
            evidence.evidence_type for evidence in result.report.evidence_refs
        }
        assert {EvidenceType.TRACE, EvidenceType.RULE}.issubset(evidence_types)
        assert len(
            [
                line
                for line in result.summary_zh.splitlines()
                if line.startswith("## ") and line[3:4].isdigit()
            ]
        ) == 8
        assert result.report.recommendations == ()
        assert result.report.summary == result.summary_zh

        persisted = await profile.incident_repository.get(created.incident_id)
        assert persisted is not None
        assert persisted.status is IncidentStatus.DETECTED
        assert persisted.revision == 0
        assert persisted.rca_reports == ()
        assert persisted.recommendations == ()
        assert persisted.action_runs == ()
        assert len(await profile.incident_repository.history(created.incident_id)) == 1

        for value in (*candidates, result):
            assert_model_safe(value)
        serialized = json.dumps(
            {
                "candidates": [item.to_data_part() for item in candidates],
                "result": result.to_data_part(),
            },
            ensure_ascii=False,
        ).lower()
        for forbidden in (
            "imsi",
            "msisdn",
            "imeisv",
            "google.cloud",
            "google.adk",
            "gemini",
            "deepseek",
        ):
            assert forbidden not in serialized

    _run(scenario())
