from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "run_defense_demo.py"
SPEC = importlib.util.spec_from_file_location("networkagent_defense_demo", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
defense_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(defense_demo)

INCIDENT_ID = "incident-" + "c" * 64
RESOURCES = [
    {
        "resource_id": "lte:enodeb:2",
        "resource_type": "ENODEB",
        "technology": "LTE",
    },
    {
        "resource_id": "lte:enodeb:2:cell:22414",
        "resource_type": "CELL",
        "technology": "LTE",
    },
]
SELECTED_CANDIDATE = {
    "incident_id": INCIDENT_ID,
    "resource_count": 2,
    "severity": "UNKNOWN",
    "technology": "LTE",
}


def _document(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode() + b"\n"


def _changed(
    completed: subprocess.CompletedProcess[bytes],
    change,  # type: ignore[no-untyped-def]
) -> subprocess.CompletedProcess[bytes]:
    payload = json.loads(completed.stdout)
    change(payload)
    return subprocess.CompletedProcess(
        completed.args, completed.returncode, _document(payload), completed.stderr
    )


class FakeRunner:
    def __init__(
        self,
        *,
        heads: tuple[str, str] = ("a" * 40, "a" * 40),
        statuses: tuple[bytes, bytes] = (b"", b""),
        cleanup_failure: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self._heads = iter(heads)
        self._statuses = iter(statuses)
        self.cleanup_failure = cleanup_failure
        self.approval_calls = {"success": 0, "failure": 0}

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd
        self.calls.append(arguments)
        self.environments.append(dict(env))
        assert timeout == 60
        if arguments[:3] == ("git", "rev-parse", "--verify"):
            return subprocess.CompletedProcess(
                arguments, 0, next(self._heads).encode() + b"\n", b""
            )
        if arguments[:3] == ("git", "status", "--porcelain"):
            return subprocess.CompletedProcess(arguments, 0, next(self._statuses), b"")

        assert arguments[0] == sys.executable
        assert Path(arguments[1]).name == "local_stack.py"
        workspace = Path(arguments[arguments.index("--workspace") + 1])
        branch = workspace.name
        if arguments[-1] == "doctor":
            payload = {
                "ok": True,
                "command": "doctor",
                "report": {
                    "ready": True,
                    "demo_ready": True,
                    "dependencies": {
                        "core": True,
                        "governance": True,
                        "server": False,
                    },
                    "data": {"ready": True},
                    "network": {"external_access": False},
                },
            }
        elif arguments[-1] == "init":
            workspace.mkdir(parents=True)
            (workspace / ".local-stack.json").write_text("{}", encoding="utf-8")
            payload = {
                "ok": True,
                "command": "init",
                "database": {
                    "performance_rows": 13_440,
                    "trace_rows": 579,
                    "incident_rows": 0,
                },
            }
        elif arguments[-1] == "status":
            payload = {
                "ok": True,
                "command": "status",
                "report": {
                    "ready": True,
                    "database": {
                        "initialized": True,
                        "schema_version": "1.1",
                        "incident_rows": 0,
                    },
                    "runtime": {"demo_ready": True, "governance": True},
                    "server": {"external_access": False},
                },
            }
        elif arguments[-2:] == ("reset", "--yes"):
            if branch == self.cleanup_failure:
                return subprocess.CompletedProcess(
                    arguments, 2, b"", b'{"unsafe":"private path"}\n'
                )
            (workspace / ".local-stack.json").unlink()
            workspace.rmdir()
            payload = {"ok": True, "command": "reset", "workspace_removed": True}
        elif "demo-verify" in arguments:
            status = arguments[arguments.index("--expected-status") + 1]
            payload = {
                "ok": True,
                "command": "demo-verify",
                "result": {
                    "incident_id": INCIDENT_ID,
                    "status": status,
                    "expected_status": status,
                    "revision": 7,
                    "rca_reports": 1,
                    "recommendations": 1,
                    "approvals": 2,
                    "action_runs": 1,
                    "verification_runs": 1,
                    "audit_events": 8,
                    "action": {
                        "action_type": "LOCAL_SIMULATION",
                        "status": "SUCCEEDED",
                        "side_effects": False,
                    },
                    "verification": {
                        "status": "PASSED" if status == "RESOLVED" else "FAILED"
                    },
                },
            }
        elif "--approve-action" in arguments:
            failed = "--verification-outcome" in arguments
            state = "REOPENED" if failed else "RESOLVED"
            self.approval_calls[branch] += 1
            retry = self.approval_calls[branch] == 2
            action_preview = {
                "action_hash": "b" * 64,
                "action_type": "LOCAL_SIMULATION",
                "resources": RESOURCES,
                "risk": "LOW",
            }
            if not retry:
                action_preview["expected_revision"] = 4
            payload = {
                "ok": True,
                "command": "demo",
                "result": {
                    "action_mode": "simulate",
                    "action_preview": action_preview,
                    "candidate_count": 15,
                    "selected_candidate": SELECTED_CANDIDATE,
                    "state": state,
                    "closed_loop": not failed,
                    "outcome": (
                        "SIMULATED_AND_REOPENED" if failed else "SIMULATED_AND_VERIFIED"
                    ),
                    "approval": {
                        "incident_confirmed": True,
                        "action_approved": True,
                        "decision_state": state if retry else "REMEDIATING",
                    },
                },
            }
        else:
            payload = {
                "ok": True,
                "command": "demo",
                "result": {
                    "action_mode": "disabled",
                    "candidate_count": 15,
                    "state": "AWAITING_APPROVAL",
                    "closed_loop": False,
                    "outcome": "AWAITING_EXPLICIT_APPROVAL",
                    "approval": {
                        "incident_confirmed": True,
                        "action_approved": False,
                    },
                    "selected_candidate": SELECTED_CANDIDATE,
                    "action_preview": {
                        "action_hash": "b" * 64,
                        "expected_revision": 4,
                        "action_type": "LOCAL_SIMULATION",
                        "resources": RESOURCES,
                        "risk": "LOW",
                    },
                },
            }
        return subprocess.CompletedProcess(arguments, 0, _document(payload), b"")


def _invoke(
    tmp_path: Path,
    runner: FakeRunner,
    *arguments: str,
) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = defense_demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        process_runner=runner,
        repository_root=tmp_path,
        utc_now=lambda: datetime(2026, 8, 31, 1, 2, 3, tzinfo=UTC),
        random_token=lambda: "c0ffee123456",
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def test_fake_full_sequence_is_fixed_bound_clean_and_hash_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "private")
    monkeypatch.setenv("HTTPS_PROXY", "http://private")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "private.json")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("HOMEDRIVE", "C:")
    monkeypatch.setenv("HOMEPATH", "\\Users\\safe")
    runner = FakeRunner()

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 0 and error is None, (error, runner.calls)
    assert payload["schema"] == "networkagent-native-defense-demo/1.0"
    assert payload["classification"] == "LOCAL_NATIVE_SIMULATION_EVIDENCE"
    assert payload["source"] == {
        "binding_stable": True,
        "commit_bound": True,
        "commit_sha": "a" * 40,
        "git_available": True,
        "tracked_clean": True,
    }
    assert payload["dataset"] == {
        "incident_rows": 0,
        "performance_rows": 13_440,
        "trace_rows": 579,
    }
    assert payload["results"]["success"]["terminal"] == {
        "closed_loop": True,
        "state": "RESOLVED",
        "verification": "PASSED",
    }
    assert payload["results"]["failure"]["terminal"] == {
        "closed_loop": False,
        "state": "REOPENED",
        "verification": "FAILED",
    }
    assert payload["results"]["success"]["status"] == {
        "database_initialized": True,
        "demo_ready": True,
        "governance": True,
        "incident_rows": 0,
        "ready": True,
        "schema_version": "1.1",
    }
    assert all(item["workspace_removed"] for item in payload["cleanup"].values())
    assert payload["coverage"]["not_claimed"] == [
        "CLOUD_EXECUTION",
        "CONTAINER_EXECUTION",
        "FULL_G2_SECURITY_CLOSURE",
        "G4_CLOUD_REHEARSAL",
        "G5_FINAL_ACCEPTANCE",
        "REAL_NETWORK_REMEDIATION",
        "REJECTION_OR_EXPIRY_BRANCHES",
    ]
    report_path = tmp_path / payload["report"]["relative_path"]
    report_bytes = report_path.read_bytes()
    assert hashlib.sha256(report_bytes).hexdigest() == payload["report"]["sha256"]
    assert json.loads(report_bytes)["results"] == payload["results"]
    assert str(tmp_path) not in json.dumps(payload)

    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert Path(local_calls[0][-1]).name == "doctor"
    assert sum(call[-1] == "status" for call in local_calls) == 2
    assert [call[-2:] for call in local_calls[-2:]] == [
        ("reset", "--yes"),
        ("reset", "--yes"),
    ]
    approvals = [call for call in local_calls if "--approve-action" in call]
    assert approvals[0] == approvals[1]
    assert approvals[2] == approvals[3]
    assert all(
        all(
            key not in env
            for key in (
                "PYTHONPATH",
                "HTTPS_PROXY",
                "GOOGLE_APPLICATION_CREDENTIALS",
            )
        )
        for env in runner.environments
    )
    assert all(
        env["PYTHONWARNINGS"]
        == (
            "ignore::DeprecationWarning,"
            "ignore::UserWarning:a2a.server.apps.jsonrpc.fastapi_app"
        )
        for env in runner.environments
    )
    assert all(
        env["HOME"] == str(tmp_path / "home")
        and env["USERPROFILE"] == str(tmp_path / "profile")
        and env["HOMEDRIVE"] == "C:"
        and env["HOMEPATH"] == "\\Users\\safe"
        for env in runner.environments
    )


@pytest.mark.parametrize("arguments", [(), ("--approve-local-simulation", "extra")])
def test_cli_rejects_missing_confirmation_or_extra_arguments(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    runner = FakeRunner()
    code, payload, error = _invoke(tmp_path, runner, *arguments)
    assert code == 2 and payload is None
    assert error["error"]["code"] in {"confirmation_required", "invalid_arguments"}
    assert not runner.calls


def test_binding_drift_and_tracked_dirty_downgrade_without_blocking(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(heads=("a" * 40, "b" * 40))
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 0 and error is None
    assert payload["classification"] == "LOCAL_WORKTREE_SIMULATION_EVIDENCE"
    assert payload["source"]["commit_bound"] is False
    assert payload["source"]["binding_stable"] is False
    assert payload["source"]["tracked_clean"] is True


def test_tracked_dirty_source_is_not_commit_bound(tmp_path: Path) -> None:
    runner = FakeRunner(statuses=(b" M tracked.py\n", b" M tracked.py\n"))
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 0 and error is None
    assert payload["classification"] == "LOCAL_WORKTREE_SIMULATION_EVIDENCE"
    assert payload["source"]["binding_stable"] is True
    assert payload["source"]["commit_bound"] is False
    assert payload["source"]["tracked_clean"] is False


def test_cleanup_failure_is_stable_and_does_not_echo_child_details(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(cleanup_failure="failure")
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert error == {
        "ok": False,
        "error": {
            "code": "cleanup_failed",
            "message": "local demo cleanup failed safely",
        },
    }
    assert "private" not in json.dumps(error)
    assert [call[-2:] for call in runner.calls if call[0] == sys.executable][-2:] == [
        ("reset", "--yes"),
        ("reset", "--yes"),
    ]


def test_primary_doctor_failure_is_preserved_when_workspaces_do_not_exist(
    tmp_path: Path,
) -> None:
    class InvalidDoctor(FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if arguments[-1] == "doctor":
                return _changed(
                    completed,
                    lambda payload: payload["report"].update({"demo_ready": False}),
                )
            return completed

    runner = InvalidDoctor()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert error["error"]["code"] == "evidence_contract_failed"
    assert not any(call[-2:] == ("reset", "--yes") for call in runner.calls)


def test_invalid_init_response_with_created_workspace_is_reset_without_masking(
    tmp_path: Path,
) -> None:
    class InvalidInit(FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if arguments[-1] == "init":
                return _changed(
                    completed,
                    lambda payload: payload["database"].update({"trace_rows": 1}),
                )
            return completed

    runner = InvalidInit()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert error["error"]["code"] == "evidence_contract_failed"
    resets = [call for call in runner.calls if call[-2:] == ("reset", "--yes")]
    assert len(resets) == 1


def test_preview_rejects_resource_fields_outside_the_allowlist(tmp_path: Path) -> None:
    class UnsafePreview(FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if arguments[-2:] == ("demo", "--confirm-incident"):
                return _changed(
                    completed,
                    lambda payload: payload["result"]["action_preview"]["resources"][
                        0
                    ].update({"url": "https://example.test"}),
                )
            return completed

    code, payload, error = _invoke(
        tmp_path, UnsafePreview(), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "evidence_contract_failed"


def test_initial_terminal_must_retain_the_exact_preview_binding(tmp_path: Path) -> None:
    class ChangedTerminal(FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if (
                "--approve-action" in arguments
                and self.approval_calls[
                    arguments[arguments.index("--workspace") + 1].split(os.sep)[-1]
                ]
                == 1
            ):
                return _changed(
                    completed,
                    lambda payload: payload["result"]["action_preview"].update(
                        {"action_hash": "d" * 64}
                    ),
                )
            return completed

    code, payload, error = _invoke(
        tmp_path, ChangedTerminal(), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "evidence_contract_failed"


def test_exact_retry_rejects_semantic_terminal_or_verification_drift(
    tmp_path: Path,
) -> None:
    class ChangedRetry(FakeRunner):
        verification_calls = 0

        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if "demo-verify" in arguments:
                self.verification_calls += 1
                if self.verification_calls == 2:
                    return _changed(
                        completed,
                        lambda payload: payload["result"].update(
                            {"replay_drift": True}
                        ),
                    )
            return completed

    code, payload, error = _invoke(
        tmp_path, ChangedRetry(), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "evidence_contract_failed"


@pytest.mark.parametrize(
    "body",
    [
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b"\xff",
        _document({"value": [[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]}),
        b" " * (64 * 1024 + 1),
    ],
    ids=("duplicate", "nan", "utf8", "depth", "size"),
)
def test_strict_child_json_rejects_duplicates_nan_utf8_depth_and_size(
    body: bytes,
) -> None:
    with pytest.raises(defense_demo.DefenseDemoError):
        defense_demo._decode_json_document(body)


def test_git_unavailable_is_unbound_but_demo_can_complete(tmp_path: Path) -> None:
    class GitUnavailableRunner(FakeRunner):
        def __call__(
            self, arguments: tuple[str, ...], **kwargs
        ):  # type: ignore[no-untyped-def]
            if arguments[0] == "git":
                raise FileNotFoundError
            return super().__call__(arguments, **kwargs)

    code, payload, error = _invoke(
        tmp_path, GitUnavailableRunner(), "--approve-local-simulation"
    )
    assert code == 0 and error is None
    assert payload["classification"] == "LOCAL_WORKTREE_SIMULATION_EVIDENCE"
    assert payload["source"]["git_available"] is False
    assert payload["source"]["commit_bound"] is False


@pytest.mark.skipif(
    importlib.util.find_spec("duckdb") is None,
    reason="real Local Profile dependencies are not installed",
)
def test_real_runtime_runs_both_terminal_branches() -> None:
    completed = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--approve-local-simulation"],
        cwd=MODULE_PATH.parents[2],
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        check=False,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stderr == b""
    payload = json.loads(completed.stdout)
    assert payload["results"]["success"]["terminal"]["state"] == "RESOLVED"
    assert payload["results"]["failure"]["terminal"]["state"] == "REOPENED"
    assert all(item["workspace_removed"] for item in payload["cleanup"].values())
