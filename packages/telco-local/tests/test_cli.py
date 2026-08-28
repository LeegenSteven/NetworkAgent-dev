"""Safe JSON CLI tests for preview, explicit confirmation, and read-only RCA."""

from __future__ import annotations

import asyncio
import json
from io import StringIO

from telco_domain import Incident, IncidentStatus, RcaResult, parse_contract_message
from telco_local.cli import main
from telco_local.incident_repository import DuckDbIncidentRepository


def _write_rule(local_config) -> None:
    for path in local_config.rules_dir.glob("*.json"):
        path.unlink()
    rule = {
        "schema_version": "1.0",
        "rule_id": "lte.erab.security-setup",
        "version": "1.0.0",
        "technology": "LTE",
        "is_current": True,
        "description_zh": "ERAB 建立成功率异常。",
        "detection": {
            "kpi_name": "erab_success_rate",
            "comparator": "LT",
            "threshold": 100.0,
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
            "hypothesis_zh": "S1 安全配置失败可能影响 ERAB 建立。",
            "root_cause_zh": "S1 安全配置失败占主要失败事件。",
        },
        "severity": {"cases": [], "default": "LOW"},
    }
    (local_config.rules_dir / "erab.json").write_text(
        json.dumps(rule, ensure_ascii=False), encoding="utf-8"
    )


def _base_args(local_config) -> list[str]:
    return [
        "--database-path",
        str(local_config.database_path),
        "--performance-csv-path",
        str(local_config.performance_csv_path),
        "--safe-trace-csv-path",
        str(local_config.safe_trace_csv_path),
        "--rules-dir",
        str(local_config.rules_dir),
        "--source-timezone",
        "UTC",
    ]


def _invoke(arguments: list[str]) -> tuple[int, object, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    return code, payload, stderr.getvalue()


def test_cli_preview_is_read_only_and_confirm_is_the_only_incident_write(
    local_config,
) -> None:
    _write_rule(local_config)
    base = _base_args(local_config)

    code, initialized, error = _invoke([*base, "init", "--reset"])
    assert (code, error) == (0, "")
    assert initialized == {
        "schema_version": "1.1",
        "performance_rows": 2,
        "trace_rows": 2,
        "incident_rows": 0,
    }

    code, preview, error = _invoke(
        [
            *base,
            "detect",
            "--trace-id",
            "trace-preview",
            "--workflow-id",
            "workflow-preview",
        ]
    )
    assert (code, error) == (0, "")
    triggers = tuple(parse_contract_message(item) for item in preview)
    assert len(triggers) == 2
    assert all(item.message_type == "incident_trigger" for item in triggers)
    assert asyncio.run(DuckDbIncidentRepository(local_config).list()) == ()

    candidate_id = triggers[0].incident_id
    confirm_args = [
        *base,
        "confirm",
        candidate_id,
        "--trace-id",
        "trace-confirm",
        "--idempotency-key",
        "confirm-cli-1",
        "--actor",
        "operator",
        "--reason",
        "用户明确确认创建 Incident",
    ]
    code, created_payload, error = _invoke(confirm_args)
    assert (code, error) == (0, "")
    created = Incident.model_validate(created_payload)
    assert created.incident_id == candidate_id
    assert created.status is IncidentStatus.DETECTED
    assert created.revision == 0

    code, replay_payload, error = _invoke(confirm_args)
    assert (code, error) == (0, "")
    assert replay_payload == created_payload
    assert len(asyncio.run(DuckDbIncidentRepository(local_config).list())) == 1


def test_cli_analyze_returns_p1_contract_without_mutating_incident(
    local_config,
) -> None:
    _write_rule(local_config)
    base = _base_args(local_config)
    _invoke([*base, "init", "--reset"])
    _, preview, _ = _invoke(
        [*base, "detect", "--trace-id", "trace-detect", "--workflow-id", "wf-detect"]
    )
    candidate_id = preview[0]["incident_id"]
    _, created_payload, _ = _invoke(
        [
            *base,
            "confirm",
            candidate_id,
            "--trace-id",
            "trace-confirm",
            "--idempotency-key",
            "confirm-for-rca",
            "--actor",
            "operator",
            "--reason",
            "用户确认后执行只读 RCA",
        ]
    )

    code, result_payload, error = _invoke(
        [
            *base,
            "analyze",
            candidate_id,
            "--trace-id",
            "trace-confirm",
            "--workflow-id",
            "workflow-rca",
            "--message-id",
            "message-rca",
            "--idempotency-key",
            "request-rca",
            "--report-version",
            "1",
        ]
    )
    assert (code, error) == (0, "")
    result = parse_contract_message(result_payload)
    assert isinstance(result, RcaResult)
    assert result.report.incident_id == candidate_id
    assert result.report.recommendations == ()
    assert result.summary_zh.count("## ") == 8

    stored = asyncio.run(DuckDbIncidentRepository(local_config).get(candidate_id))
    assert stored == Incident.model_validate(created_payload)
    assert stored is not None
    assert stored.status is IncidentStatus.DETECTED
    assert stored.revision == 0
    assert stored.rca_reports == ()
