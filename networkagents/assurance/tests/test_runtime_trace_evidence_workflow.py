from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "telco-assurance.yml"


def test_assurance_workflow_runs_fixed_demo_on_both_pythons() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    command = (
        "python tools/local-stack/run_runtime_trace_demo.py "
        "--approve-local-simulation"
    )

    assert 'matrix:\n        python-version: ["3.12", "3.13"]' in workflow
    assert "timeout-minutes: 20" in workflow
    assert workflow.count(command) == 1
    assert workflow.count("python -m pytest networkagents/assurance/tests -q") == 1
    assert "python -m pytest tests/e2e/local -q" in workflow
    assert "if: matrix.python-version == '3.12'" in workflow
    assert "release-evidence/local-runtime-trace-summary.json" in workflow


def test_runtime_summary_is_supplemental_and_raw_stream_is_not_uploaded() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    supplemental = (
        "--supplemental-evidence " "release-evidence/local-runtime-trace-summary.json"
    )

    assert workflow.count(supplemental) == 2
    assert "release-evidence/*.json" in workflow
    assert "retention-days: 14" in workflow
    assert "release-evidence/*.jsonl" not in workflow
    upload = workflow.split("- name: Upload release or diagnostic evidence", 1)[1]
    upload = upload.split("- name: Summarize uploaded artifact", 1)[0]
    assert ".local/networkagent-runtime-trace" not in upload
    assert "local-runtime-events.jsonl" not in upload
    assert 'assert not list(Path("release-evidence").rglob("*.jsonl"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.duckdb"))' in workflow
    assert (
        'summary["schema"] == "networkagent-local-runtime-trace-evidence/1.0"'
        in workflow
    )
    assert 'summary["proof"]["event_count"] == 6' in workflow
    assert 'summary["proof"]["component_count"] == 4' in workflow
    assert 'summary["proof"]["single_correlation"] is True' in workflow
    assert 'summary["proof"]["all_outcomes_ok"] is True' in workflow
    assert 'summary["proof"]["expected_order"] is True' in workflow
    assert 'summary["source"]["commit_sha"] == os.environ["GITHUB_SHA"]' in workflow
    assert "nested_keys(persisted_body).isdisjoint(forbidden_keys)" in workflow
