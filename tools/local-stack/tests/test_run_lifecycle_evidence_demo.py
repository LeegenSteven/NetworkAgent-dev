from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import stat
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "run_lifecycle_evidence_demo.py"
SPEC = importlib.util.spec_from_file_location(
    "networkagent_lifecycle_evidence_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
lifecycle_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle_demo)

DEFENSE_TEST_PATH = Path(__file__).with_name("test_run_defense_demo.py")
DEFENSE_TEST_SPEC = importlib.util.spec_from_file_location(
    "networkagent_lifecycle_fake_defense", DEFENSE_TEST_PATH
)
assert DEFENSE_TEST_SPEC is not None and DEFENSE_TEST_SPEC.loader is not None
fake_defense = importlib.util.module_from_spec(DEFENSE_TEST_SPEC)
DEFENSE_TEST_SPEC.loader.exec_module(fake_defense)


def _invoke(
    tmp_path: Path,
    runner: object,
    *arguments: str,
    random_token=lambda: "1a2b3c4d5e6f",
) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = lifecycle_demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        process_runner=runner,
        repository_root=tmp_path,
        utc_now=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
        random_token=random_token,
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def _defense_result() -> dict[str, object]:
    retry = {
        "approval_command_reused": True,
        "terminal_unchanged": True,
        "verification_unchanged": True,
    }
    return {
        "cleanup": {
            "failure": {"workspace_removed": True},
            "success": {"workspace_removed": True},
        },
        "ok": True,
        "results": {
            "failure": {
                "exact_retry": dict(retry),
                "terminal": {
                    "closed_loop": False,
                    "state": "REOPENED",
                    "verification": "FAILED",
                },
            },
            "success": {
                "exact_retry": dict(retry),
                "terminal": {
                    "closed_loop": True,
                    "state": "RESOLVED",
                    "verification": "PASSED",
                },
            },
        },
        "schema": "networkagent-native-defense-demo/1.0",
        "source": {
            "binding_stable": True,
            "commit_bound": True,
            "commit_sha": "a" * 40,
            "git_available": True,
            "tracked_clean": True,
        },
    }


def _cleanup_generated_run_directories(
    defense_root: Path, candidates: set[Path]
) -> None:
    allowed_files = {
        "defense-demo-report.json",
        "local-lifecycle-report.json",
    }
    for candidate in candidates:
        try:
            is_junction = getattr(candidate, "is_junction", None)
            if (
                candidate.parent != defense_root
                or candidate.is_symlink()
                or (callable(is_junction) and is_junction())
                or not candidate.is_dir()
            ):
                continue
            children = tuple(candidate.iterdir())
            if not children or any(
                child.name not in allowed_files
                or child.is_symlink()
                or not stat.S_ISREG(os.lstat(child).st_mode)
                for child in children
            ):
                continue
            for child in children:
                child.unlink()
            candidate.rmdir()
        except OSError:
            pass


def test_fake_dual_branch_projection_is_strict_private_and_atomically_persisted(
    tmp_path: Path,
) -> None:
    runner = fake_defense.FakeRunner()

    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
    )

    assert code == 0 and error is None, (error, runner.calls)
    assert payload["schema"] == "networkagent-local-lifecycle-evidence/1.0"
    assert payload["ok"] is True
    assert payload["classification"] == "LOCAL_CANONICAL_LIFECYCLE_EVIDENCE"
    assert payload["source"] == {
        "binding_stable": True,
        "commit_bound": True,
        "commit_sha": "a" * 40,
        "git_available": True,
        "tracked_clean": True,
    }
    assert set(payload["branches"]) == {"success", "failure"}
    assert payload["branches"]["success"]["terminal_status"] == "RESOLVED"
    assert payload["branches"]["failure"]["terminal_status"] == "REOPENED"
    assert payload["branches"]["success"]["scenario"] == ("LOCAL_SIMULATION_RESOLVED")
    assert payload["branches"]["failure"]["scenario"] == ("LOCAL_SIMULATION_REOPENED")
    for projection in payload["branches"].values():
        assert projection["schema"] == ("networkagent-local-lifecycle-projection/1.0")
        assert projection["classification"] == (
            "DERIVED_FROM_DURABLE_CANONICAL_RECORDS"
        )
        assert projection["read_only"] is True
        assert projection["distributed_trace"] is False
        assert projection["ordering"] == "REVISION_GROUPED_ATOMIC_PROJECTION"
        assert [group["revision"] for group in projection["revision_groups"]] == (
            list(range(8))
        )
        events = [
            event
            for group in projection["revision_groups"]
            for event in group["events"]
        ]
        assert len(events) == 14
        assert [event["sequence"] for event in events] == list(range(1, 15))
        assert all(
            set(event)
            == {
                "component",
                "occurred_at",
                "operation",
                "outcome",
                "record_type",
                "sequence",
            }
            for event in events
        )
    assert payload["proof"] == {
        "branch_count": 2,
        "cleanup": {"failure": True, "success": True},
        "exact_retry": {"failure": True, "success": True},
        "projected_event_count": 28,
        "revision_group_count": 16,
        "terminal": {
            "failure": {
                "closed_loop": False,
                "expected_business_result": True,
                "state": "REOPENED",
                "verification": "FAILED",
            },
            "success": {
                "closed_loop": True,
                "expected_business_result": True,
                "state": "RESOLVED",
                "verification": "PASSED",
            },
        },
    }
    assert payload["privacy"] == {
        "absolute_paths_recorded": False,
        "domain_hashes_recorded": False,
        "domain_identifiers_recorded": False,
        "pseudonymous_correlation_recorded": False,
        "raw_records_recorded": False,
        "status": "PASS",
        "workspace_identifiers_recorded": False,
    }
    assert payload["coverage"]["not_claimed"] == lifecycle_demo._NOT_CLAIMED
    forbidden = {
        "action_hash",
        "action_id",
        "approval_id",
        "correlation_id",
        "event_id",
        "incident_id",
        "idempotency_key",
        "report_id",
        "request_id",
        "resource_id",
        "trace_id",
        "verification_id",
        "workspace_id",
    }
    persisted_body = {key: value for key, value in payload.items() if key != "report"}
    assert _nested_keys(persisted_body).isdisjoint(forbidden)
    assert str(tmp_path) not in json.dumps(payload)

    assert payload["report"]["filename"] == "local-lifecycle-report.json"
    reports = list(
        (tmp_path / ".local" / "networkagent-defense").glob(
            "*/local-lifecycle-report.json"
        )
    )
    assert len(reports) == 1
    report_path = reports[0]
    assert report_path.name == payload["report"]["filename"]
    assert not report_path.is_symlink()
    assert stat.S_ISREG(os.lstat(report_path).st_mode)
    report_bytes = report_path.read_bytes()
    assert len(report_bytes) == payload["report"]["bytes"]
    assert hashlib.sha256(report_bytes).hexdigest() == payload["report"]["sha256"]
    assert json.loads(report_bytes) == persisted_body
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    projection_calls = [call for call in local_calls if "demo-events" in call]
    assert len(projection_calls) == 2
    assert [call[-2:] for call in local_calls[-2:]] == [
        ("reset", "--yes"),
        ("reset", "--yes"),
    ]


@pytest.mark.parametrize("arguments", [(), ("--approve-local-simulation", "extra")])
def test_cli_requires_exact_confirmation(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    runner = fake_defense.FakeRunner()
    code, payload, error = _invoke(tmp_path, runner, *arguments)
    assert (code, payload) == (2, None)
    assert error["error"]["code"] in {
        "confirmation_required",
        "invalid_arguments",
    }
    assert not runner.calls


@pytest.mark.parametrize(
    ("section", "field", "forged"),
    (
        ("record_counts", "action_runs", True),
        ("invariants", "side_effects", 0),
    ),
)
def test_projection_rejects_nested_boolean_integer_confusion(
    section: str, field: str, forged: object
) -> None:
    projection = fake_defense._lifecycle_projection("success")
    nested = projection[section]
    assert isinstance(nested, dict)
    nested[field] = forged

    with pytest.raises(lifecycle_demo.LifecycleEvidenceError) as caught:
        lifecycle_demo._validate_projection("success", projection)

    assert caught.value.code == "lifecycle_contract_failed"


@pytest.mark.parametrize(
    ("section", "branch", "field", "forged"),
    (
        ("terminal", "success", "closed_loop", 1),
        ("terminal", "failure", "closed_loop", 0),
        ("exact_retry", "success", "approval_command_reused", 1),
        ("cleanup", "failure", "workspace_removed", 1),
    ),
)
def test_defense_proof_rejects_nested_boolean_integer_confusion(
    section: str, branch: str, field: str, forged: object
) -> None:
    result = deepcopy(_defense_result())
    if section == "cleanup":
        cleanup = result["cleanup"]
        assert isinstance(cleanup, dict)
        branch_value = cleanup[branch]
    else:
        results = result["results"]
        assert isinstance(results, dict)
        branch_result = results[branch]
        assert isinstance(branch_result, dict)
        branch_value = branch_result[section]
    assert isinstance(branch_value, dict)
    branch_value[field] = forged

    with pytest.raises(lifecycle_demo.LifecycleEvidenceError) as caught:
        lifecycle_demo._validate_defense_result(result)

    assert caught.value.code == "evidence_contract_failed"


def test_projection_extra_identifier_fails_after_other_branch_and_cleanup(
    tmp_path: Path,
) -> None:
    class UnsafeProjection(fake_defense.FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if "demo-events" in arguments:
                payload = json.loads(completed.stdout)
                workspace = Path(arguments[arguments.index("--workspace") + 1])
                if workspace.name == "success":
                    payload["result"]["incident_id"] = "private-domain-id"
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        fake_defense._document(payload),
                        b"",
                    )
            return completed

    runner = UnsafeProjection()
    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
    )
    assert (code, payload) == (2, None)
    assert error == {
        "error": {
            "code": "evidence_contract_failed",
            "message": "local lifecycle demo detected contract drift",
        },
        "ok": False,
        "schema": "networkagent-local-lifecycle-evidence/1.0",
    }
    assert "private" not in json.dumps(error)
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert len([call for call in local_calls if "demo-events" in call]) == 2
    assert [call[-2:] for call in local_calls[-2:]] == [
        ("reset", "--yes"),
        ("reset", "--yes"),
    ]


def test_source_drift_downgrades_classification_without_false_commit_binding(
    tmp_path: Path,
) -> None:
    runner = fake_defense.FakeRunner(heads=("a" * 40, "b" * 40))
    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
    )
    assert code == 0 and error is None
    assert payload["classification"] == ("LOCAL_WORKTREE_CANONICAL_LIFECYCLE_EVIDENCE")
    assert payload["source"]["commit_bound"] is False
    assert payload["source"]["binding_stable"] is False


def test_invalid_report_token_is_safe_and_never_publishes_partial_report(
    tmp_path: Path,
) -> None:
    runner = fake_defense.FakeRunner()
    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
        random_token=lambda: "invalid-private-token",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "report_write_failed"
    assert "private" not in json.dumps(error)
    assert not list(tmp_path.rglob("local-lifecycle-report.json"))


def test_report_size_is_strictly_bounded() -> None:
    with pytest.raises(lifecycle_demo.LifecycleEvidenceError) as caught:
        lifecycle_demo._canonical_bytes({"value": "x" * (64 * 1024)})
    assert caught.value.code == "lifecycle_contract_failed"


@pytest.mark.skipif(
    importlib.util.find_spec("duckdb") is None,
    reason="real Local Profile dependencies are not installed",
)
def test_real_runtime_projects_both_terminal_branches_without_domain_ids() -> None:
    defense_root = MODULE_PATH.parents[2] / ".local" / "networkagent-defense"
    moment = datetime.now(UTC).replace(microsecond=0)
    token = secrets.token_hex(6)
    run_directory = defense_root / f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{token}"
    assert not os.path.lexists(run_directory)
    created = {run_directory}
    stdout = StringIO()
    stderr = StringIO()
    try:
        code = lifecycle_demo.main(
            ["--approve-local-simulation"],
            stdout=stdout,
            stderr=stderr,
            repository_root=MODULE_PATH.parents[2],
            utc_now=lambda: moment,
            random_token=lambda: token,
        )
        assert code == 0, stderr.getvalue()
        assert stderr.getvalue() == ""
        assert run_directory.is_dir()
        payload = json.loads(stdout.getvalue())
        assert payload["branches"]["success"]["terminal_status"] == "RESOLVED"
        assert payload["branches"]["failure"]["terminal_status"] == "REOPENED"
        assert payload["proof"]["cleanup"] == {"failure": True, "success": True}
        assert payload["proof"]["exact_retry"] == {
            "failure": True,
            "success": True,
        }
        assert payload["proof"]["projected_event_count"] == 28
        assert payload["report"]["filename"] == "local-lifecycle-report.json"
        report_path = run_directory / payload["report"]["filename"]
        assert not report_path.is_symlink()
        assert stat.S_ISREG(os.lstat(report_path).st_mode)
        report_bytes = report_path.read_bytes()
        assert len(report_bytes) == payload["report"]["bytes"]
        assert hashlib.sha256(report_bytes).hexdigest() == payload["report"]["sha256"]
        persisted = {key: value for key, value in payload.items() if key != "report"}
        assert json.loads(report_bytes) == persisted
        assert _nested_keys(persisted).isdisjoint(
            {"incident_id", "workspace_id", "trace_id", "correlation_id"}
        )
    finally:
        _cleanup_generated_run_directories(defense_root, created)
    assert created and all(not candidate.exists() for candidate in created)
