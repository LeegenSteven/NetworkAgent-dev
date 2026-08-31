import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "telco-lab.yml"


def test_workflow_keeps_push_hermetic_and_gates_the_upstream_slice() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    dispatch_gate = (
        "github.event_name == 'workflow_dispatch' && "
        "inputs.run_rcaeval_upstream && matrix.python-version == '3.12'"
    )

    assert "run_rcaeval_upstream:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    assert "timeout-minutes: 20" in workflow
    assert workflow.count(dispatch_gate) == 1
    assert (
        'telco-lab --workspace "$rcaeval_workspace" run '
        "rcaeval-re2ob-multisource-rca --accept-license MIT"
    ) in workflow
    assert (
        'telco-lab --workspace "$rcaeval_workspace" evaluate '
        "rcaeval-re2ob-multisource-rca"
    ) in workflow
    assert 'assert run_payload["result"] == offline_payload["result"]' in workflow
    assert "PINNED_UPSTREAM_RCAEVAL_RE2OB_SLICE" in workflow
    assert "RCAEVAL_TOTAL_BYTES" not in workflow
    assert (
        workflow.count(
            "python -m pytest packages/telco-lab/tests -q -p no:cacheprovider"
        )
        == 2
    )
    assert workflow.count("python -m pytest tests/e2e/lab -q -p no:cacheprovider") == 2
    assert "packages/telco-lab/tests tests/e2e/lab" not in workflow
    assert '"catalog_id": "networkagent-open-data"' in workflow
    assert '"catalog_version": "1.1.0"' in workflow
    assert '"dataset_id": "rcaeval-re2ob-evaluation-slice"' in workflow
    assert '"dataset_version": "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e"' in workflow
    assert (
        "c99ced28f1cb56464820a9570ead783de753c31ad36f5d7d29de594115101fb1" in workflow
    )
    assert 'protocol["sealed_ranking_count"] == 5' in workflow
    assert "networkagent-multisource-shift-v1" in workflow
    assert 'protocol["externally_timestamped"] is False' in workflow
    assert 'r"[0-9a-f]{64}", protocol["batch_commitment_sha256"]' in workflow
    assert "expected_not_claimed = [" in workflow
    assert 'assert result["not_claimed"] == expected_not_claimed' in workflow
    assert "assert persisted == summary" in workflow
    assert "assert summary_path.read_bytes() == reconstructed" in workflow
    assert 'persisted["result"]["not_claimed"] == expected_not_claimed' in workflow
    for boundary in (
        "UPSTREAM_RCAEVAL_IMPLEMENTATION_PARITY",
        "INDEPENDENT_EVIDENCE_LABEL_ANNOTATIONS",
        "MALICIOUS_IN_PROCESS_RANKER_ISOLATION",
        "STATISTICAL_SIGNIFICANCE_OR_GENERALIZATION",
    ):
        assert workflow.count(f'"{boundary}"') == 1
    assert workflow.count('test "$(git rev-parse --verify HEAD)" = "$GITHUB_SHA"') == 2
    assert workflow.count("git diff --quiet") == 2
    assert workflow.count("git diff --cached --quiet") == 2
    assert '"commit_bound": True' in workflow
    assert '"tracked_clean": True' in workflow
    assert 'assert persisted["source"] == {' in workflow
    assert 'assert "/" not in rendered' in workflow
    assert 'assert "\\\\" not in rendered' in workflow
    assert 'assert "rcaeval.re2ob." not in rendered' in workflow
    assert "for private_candidate in (" in workflow


def test_workflow_pins_binary_pyarrow_boundaries_and_wheel_contract() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert '--only-binary=:all: "pyarrow==25.0.0"' in workflow
    assert '--only-binary=:all: "pyarrow==21.0.0"' in workflow
    assert "assert version('pyarrow') == '25.0.0'" in workflow
    assert "assert version('pyarrow') == '21.0.0'" in workflow
    assert 'pyarrow_metadata.get("License-Expression") == "Apache-2.0"' in workflow
    assert 'pyarrow_metadata.get("License-Expression") is None' in workflow
    assert 'pyarrow_metadata.get("License") == "Apache Software License"' in workflow
    assert 'in pyarrow_metadata.get_all("Classifier", ())' in workflow
    assert 'if requirement.name == "pyarrow"' in workflow
    assert 'str(requirement.specifier) == "<26,>=21"' in workflow
    assert 'if item["normalized_name"] == "pyarrow"' in workflow
    assert 'pyarrow_package["scope"] == "runtime"' in workflow
    assert 'pyarrow_package["license_expression"] == "Apache-2.0"' in workflow
    release_target = workflow.split('--target "$release_env"', 1)[1]
    release_target = release_target.split("dist/domain/telco_domain-*.whl", 1)[0]
    assert '"pyarrow==25.0.0"' in release_target
    assert 'assert pyarrow["version"] == "25.0.0"' in workflow
    assert 'assert pyarrow["bom-ref"] in telco_lab_dependency["dependsOn"]' in workflow
    assert "sbom_path.write_text" not in workflow
    assert (
        re.search(
            r'^\s*telco_lab_dependency\["dependsOn"\]\s*=(?!=)',
            workflow,
            flags=re.MULTILINE,
        )
        is None
    )
    for suffix in (
        '".parquet"',
        '".arrow"',
        '".feather"',
        '".ipc"',
        '".orc"',
    ):
        assert suffix in workflow


def test_upstream_summary_is_conditional_supplemental_without_raw_data() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    summary = "release-evidence/rcaeval-upstream-summary.json"
    supplemental = f"--supplemental-evidence {summary}"

    assert workflow.count(supplemental) == 1
    assert "rcaeval_supplemental=()" in workflow
    assert "if [ -f release-evidence/rcaeval-upstream-summary.json ]; then" in workflow
    assert workflow.count('"${rcaeval_supplemental[@]}"') == 2
    assert 'assert not list(Path("release-evidence").rglob("*.parquet"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.arrow"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.feather"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.ipc"))' in workflow
    assert 'assert not list(Path("release-evidence").rglob("*.orc"))' in workflow

    upload = workflow.split("- name: Upload release or diagnostic evidence", 1)[1]
    upload = upload.split("- name: Summarize uploaded artifact", 1)[0]
    assert "rcaeval_workspace" not in upload
    assert ".parquet" not in upload
    assert "artifacts/" not in upload


def test_upload_paths_exclude_unverified_wheels_and_keep_verified_wheels() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    selector = workflow.split(
        "- name: Select fixed release or diagnostic upload paths", 1
    )[1]
    selector = selector.split("- name: Upload release or diagnostic evidence", 1)[0]
    upload = workflow.split("- name: Upload release or diagnostic evidence", 1)[1]
    upload = upload.split("- name: Summarize uploaded artifact", 1)[0]

    manifest_gate = (
        'if [ "$EVIDENCE_OUTCOME" = "success" ] && python -c '
        '"import json; from pathlib import Path; raise SystemExit(0 if '
        "json.loads(Path('release-evidence/release-manifest.json').read_text())"
        "['status'] == 'PASS' else 1)\"; then"
    )
    assert "if: ${{ always() && matrix.python-version == '3.12' }}" in selector
    assert "EVIDENCE_OUTCOME: ${{ steps.release_evidence.outcome }}" in selector
    assert manifest_gate in selector

    verified_only = selector.split(manifest_gate, 1)[1].split("fi", 1)[0]
    diagnostic_only = selector.split("fi", 1)[1]
    assert 'echo "dist/domain/*.whl"' in verified_only
    assert 'echo "dist/lab/*.whl"' in verified_only
    assert ".arrow" not in verified_only
    assert ".parquet" not in verified_only
    assert ".whl" not in diagnostic_only
    assert 'echo "release-evidence/*.json"' in diagnostic_only
    assert 'echo "release-evidence/*.txt"' in diagnostic_only

    assert "path: ${{ steps.release_upload_paths.outputs.paths }}" in upload
    assert "dist/" not in upload
    assert ".whl" not in upload
    assert ".arrow" not in upload
    assert ".parquet" not in upload
