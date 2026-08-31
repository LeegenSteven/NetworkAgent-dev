from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools/local-stack/run_runtime_trace_demo.py"
SPEC = importlib.util.spec_from_file_location(
    "networkagent_runtime_trace_e2e", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
runtime_demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime_demo
SPEC.loader.exec_module(runtime_demo)


def test_fixed_runtime_chain_crosses_real_loopback_and_cleans_work_state(
    tmp_path: Path,
) -> None:
    token = "f1e2d3c4b5a6"
    instant = datetime(2026, 8, 31, 6, 7, 8, tzinfo=UTC)
    run_directory = (
        tmp_path
        / ".local"
        / "networkagent-runtime-trace"
        / f"{instant.strftime('%Y%m%dT%H%M%SZ')}-{token}"
    )
    assert not run_directory.exists()
    output = runtime_demo.StringIO()
    errors = runtime_demo.StringIO()
    code = runtime_demo.main(
        ["--approve-local-simulation"],
        stdout=output,
        stderr=errors,
        repository_root=tmp_path,
        asset_root=ROOT,
        utc_now=lambda: instant,
        random_token=lambda: token,
    )
    assert code == 0, errors.getvalue()
    payload = json.loads(output.getvalue())
    assert payload["proof"]["event_count"] == 6
    assert payload["proof"]["component_count"] == 4
    assert payload["proof"]["single_correlation"] is True
    assert payload["proof"]["all_outcomes_ok"] is True
    assert payload["proof"]["expected_order"] is True
    assert payload["proof"]["binding_checks"] == 6
    assert payload["proof"]["write_semantics"] == {
        "canonical_domain_unchanged": True,
        "changed_table_count": 1,
        "transport_state_changed": True,
        "unchanged_table_count": 9,
        "whole_database_read_only_claimed": False,
    }
    assert payload["proof"]["governance_zero_delta"] == {
        "actions": 0,
        "approvals": 0,
        "executions": 0,
        "verifications": 0,
    }
    assert payload["release"] == {
        "eligible": False,
        "source_state": "WORKTREE_ONLY",
    }
    assert {child.name for child in run_directory.iterdir()} == {
        "local-runtime-events.jsonl",
        "local-runtime-trace-report.json",
    }
    lines = (
        (run_directory / "local-runtime-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 6
    assert len({json.loads(line)["trace_id"] for line in lines}) == 1
    assert {json.loads(line)["outcome"] for line in lines} == {"OK"}
