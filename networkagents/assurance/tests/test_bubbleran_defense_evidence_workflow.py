from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "telco-assurance.yml"


def test_assurance_workflow_runs_fixed_bubbleran_demo_on_both_pythons() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    command = (
        "python tools/local-stack/run_bubbleran_defense_demo.py "
        "--offline --approve-local-simulation"
    )

    assert 'matrix:\n        python-version: ["3.12", "3.13"]' in workflow
    assert "timeout-minutes: 20" in workflow
    assert workflow.count(command) == 1
    assert (
        "python tools/local-stack/tests/test_run_bubbleran_defense_demo.py" in workflow
    )
    assert workflow.count("python -m pytest networkagents/assurance/tests -q") == 1
    assert "if: matrix.python-version == '3.12'" in workflow
    assert "release-evidence/local-bubbleran-defense-summary.json" in workflow


def test_bubbleran_summary_is_exact_supplemental_without_ephemeral_state() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    supplemental = (
        "--supplemental-evidence "
        "release-evidence/local-bubbleran-defense-summary.json"
    )

    assert workflow.count(supplemental) == 2
    assert 'summary["schema"] == (' in workflow
    assert '"networkagent-local-bubbleran-defense-evidence/1.0"' in workflow
    assert 'summary["proof"]["canonical_cases"] == {' in workflow
    assert 'summary["proof"]["checkpoint"]["first"] == {' in workflow
    assert 'summary["proof"]["checkpoint"]["reopened"] == {' in workflow
    assert 'summary["proof"]["governance"]["action_runs"] == 2' in workflow
    assert 'summary["proof"]["governance"]["verification_runs"] == 2' in workflow
    assert 'summary["source"]["commit_sha"] == os.environ["GITHUB_SHA"]' in workflow
    assert "assert report_bytes == reconstructed" in workflow
    assert "nested_keys(persisted).isdisjoint(forbidden_keys)" in workflow

    upload = workflow.split("- name: Upload release or diagnostic evidence", 1)[1]
    upload = upload.split("- name: Summarize uploaded artifact", 1)[0]
    for forbidden in (
        ".csv",
        ".duckdb",
        ".jsonl",
        "checkpoint",
        "networkagent-bubbleran-defense",
    ):
        assert forbidden not in upload.lower()
    assert 'assert not list(Path("release-evidence").rglob("*.csv"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.duckdb"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.jsonl"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*checkpoint*"))' in workflow
