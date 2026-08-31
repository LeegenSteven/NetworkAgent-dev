from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPOSITORY_ROOT / "tools" / "local-stack" / "run_bubbleran_defense_demo.py"
)
SPEC = importlib.util.spec_from_file_location(
    "networkagent_real_bubbleran_defense_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo
SPEC.loader.exec_module(demo)


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def test_real_bubbleran_vertical_chain_is_private_and_cleans_ephemeral_state(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    code = demo.main(
        ["--offline", "--approve-local-simulation"],
        stdout=stdout,
        stderr=stderr,
        repository_root=tmp_path,
        asset_root=REPOSITORY_ROOT,
        utc_now=lambda: datetime(2026, 8, 31, 4, 5, 6, tzinfo=UTC),
        random_token=lambda: "123456abcdef",
    )

    assert code == 0, stderr.getvalue()
    assert stderr.getvalue() == ""
    summary = json.loads(stdout.getvalue())
    assert summary["schema"] == ("networkagent-local-bubbleran-defense-evidence/1.0")
    assert summary["classification"] == ("LOCAL_BUBBLERAN_VERTICAL_DEFENSE_EVIDENCE")
    assert summary["ok"] is True
    assert summary["fixture"] == {
        "origin": "CODE_GENERATED_SCHEMA_FIXTURE",
        "record_count": 4,
    }
    assert summary["proof"] == demo.expected_proof()
    assert summary["proof"]["canonical_cases"] == {
        "count": 4,
        "independent": True,
        "source_associations": 4,
    }
    assert summary["proof"]["checkpoint"] == {
        "first": {"attempted": 4, "delivered": 4, "selected": 4},
        "reopened": {"attempted": 0, "delivered": 0, "selected": 0},
        "settled": True,
    }
    assert summary["proof"]["governance"]["action_runs"] == 2
    assert summary["proof"]["governance"]["verification_runs"] == 2
    assert summary["proof"]["governance"]["action_contract"] == {
        "side_effects": False,
        "type": "LOCAL_SIMULATION",
    }
    assert summary["proof"]["settled_bypass"] == {
        "business_record_delta": {
            "audit": 0,
            "cases": 0,
            "idempotency": 0,
            "source_associations": 0,
        },
        "delivered": 4,
    }
    assert summary["coverage"]["not_claimed"] == [
        "COMPLETE_UPSTREAM_BENCHMARK",
        "RCA_EVAL_MULTI_SOURCE",
        "CROSS_EVENT_AGGREGATION",
        "PRODUCTION_ACCURACY",
        "REAL_NETWORK_REMEDIATION",
        "CLOUD_OR_GCP_DEPLOYMENT",
        "OPEN_TELEMETRY_OR_DISTRIBUTED_TRACE",
        "UNIFIED_DASHBOARD",
        "GATE_E_OR_G5_CLOSURE",
        "P3E_OR_S7_OVERALL_CLOSURE",
    ]
    assert summary["privacy"] == {
        "absolute_locations_recorded": False,
        "raw_records_recorded": False,
        "sensitive_identifiers_recorded": False,
        "source_locations_recorded": False,
        "status": "PASS",
    }

    forbidden_keys = {
        "body",
        "event_id",
        "events",
        "ground_truth",
        "incident_id",
        "label",
        "labels",
        "path",
        "ran_ue_id",
        "raw",
        "row",
        "rows",
        "source_event_id",
        "source_url",
        "trace_id",
        "ue",
        "ue_id",
    }
    assert _nested_keys(summary).isdisjoint(forbidden_keys)
    serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True).lower()
    assert "schema-fixture-private-value" not in serialized
    assert "fixtures.example.test" not in serialized

    run_root = tmp_path / ".local" / "networkagent-bubbleran-defense"
    run_directories = tuple(run_root.iterdir())
    assert len(run_directories) == 1
    run_directory = run_directories[0]
    children = tuple(run_directory.iterdir())
    assert {item.name for item in children} == {demo.REPORT_NAME}
    assert not list(run_directory.rglob("*.csv"))
    assert not list(run_directory.rglob("*.duckdb"))
    assert not list(run_directory.rglob("*.jsonl"))
    assert not list(run_directory.rglob("*checkpoint*"))

    report = (run_directory / demo.REPORT_NAME).read_bytes()
    reconstructed = (
        json.dumps(
            {key: value for key, value in summary.items() if key != "report"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    assert report == reconstructed
    assert len(report) == summary["report"]["bytes"]
    assert demo.hashlib.sha256(report).hexdigest() == summary["report"]["sha256"]
    assert json.loads(report) == {
        key: value for key, value in summary.items() if key != "report"
    }
