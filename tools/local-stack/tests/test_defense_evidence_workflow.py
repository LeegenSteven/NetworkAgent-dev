from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "telco-local.yml"


def test_defense_demo_is_strictly_validated_in_both_python_jobs() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'matrix:\n        python-version: ["3.12", "3.13"]' in workflow
    assert "mkdir -p release-evidence" in workflow
    assert (
        "python tools/local-stack/run_defense_demo.py "
        "--approve-local-simulation > "
        "release-evidence/defense-demo-summary.json"
    ) in workflow
    assert 'report["source"]["commit_bound"] is True' in workflow
    assert 'report["source"]["commit_sha"] == os.environ["GITHUB_SHA"]' in workflow
    assert (
        'report["results"]["success"]["terminal"] '
        '== {"closed_loop": True, "state": "RESOLVED", '
        '"verification": "PASSED"}'
    ) in workflow
    assert (
        'report["results"]["failure"]["terminal"] '
        '== {"closed_loop": False, "state": "REOPENED", '
        '"verification": "FAILED"}'
    ) in workflow
    for field in (
        "approval_command_reused",
        "terminal_unchanged",
        "verification_unchanged",
    ):
        assert f'item["exact_retry"]["{field}"] is True' in workflow
    assert 'set(report["cleanup"]) == {"success", "failure"}' in workflow
    assert 'item["workspace_removed"] is True' in workflow
    assert 'report["coverage"]["not_claimed"] == [' in workflow
    for boundary in (
        "CLOUD_EXECUTION",
        "CONTAINER_EXECUTION",
        "FULL_G2_SECURITY_CLOSURE",
        "G4_CLOUD_REHEARSAL",
        "G5_FINAL_ACCEPTANCE",
        "REAL_NETWORK_REMEDIATION",
        "REJECTION_OR_EXPIRY_BRANCHES",
    ):
        assert f'"{boundary}"' in workflow


def test_only_python_312_adds_demo_to_hashed_release_closure() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    supplemental_argument = (
        "--supplemental-evidence " "release-evidence/defense-demo-summary.json"
    )

    assert workflow.count(supplemental_argument) == 2
    assert "if: matrix.python-version == '3.12'" in workflow
    assert "release-evidence/*.json" in workflow
    assert "retention-days: 14" in workflow
    assert "defense-evidence" not in workflow


def test_observability_demo_enters_manifest_closure() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    command = (
        "python tools/local-stack/run_observability_demo.py "
        "--approve-local-simulation > "
        "release-evidence/local-observability-summary.json"
    )
    supplemental = (
        "--supplemental-evidence " "release-evidence/local-observability-summary.json"
    )

    assert command in workflow
    assert workflow.count(command) == 1
    assert workflow.count(supplemental) == 2
    assert 'observation["schema"] == "networkagent-local-observability/1.0"' in workflow
    assert 'observation["run"]["event_count"] == 22' in workflow
    assert 'observation["run"]["diagnostic_only"] is True' in workflow
    assert 'len(observation["events"]) == 22' in workflow
    assert "set(item) == expected_event_keys" in workflow
    assert 'item["duration_ms"] >= 0' in workflow
    assert 'observation["business_outcomes"] == {' in workflow
    assert 'len(observation["local_alerts"]) == 4' in workflow
    assert 'item["state"] == "OK"' in workflow
    assert 'metrics["high_cardinality_labels_present"] is False' in workflow
    assert 'set(series["labels"]) == allowed_label_keys' in workflow
    assert 'observation["coverage"]["not_claimed"] == [' in workflow
    for boundary in (
        "OPEN_TELEMETRY_EXPORT",
        "CROSS_HTTP_REPLAY_A2A_MCP_TRACE",
        "PROMETHEUS_METRICS",
        "EXTERNAL_ALERT_DELIVERY",
        "SERVICE_LEVEL_OBJECTIVES",
        "COLLECTOR_FAILURE_TOLERANCE",
        "GATE_E_OR_G5_CLOSURE",
        "CLOUD_OR_PRODUCTION_OBSERVABILITY",
    ):
        assert f'"{boundary}"' in workflow
