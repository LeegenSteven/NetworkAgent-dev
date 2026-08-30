from __future__ import annotations

import importlib.util
import json
import sys
import asyncio
import os
import subprocess
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).parents[1] / "local_stack.py"
SPEC = importlib.util.spec_from_file_location("networkagent_local_stack", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
local_stack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_stack)


class FakeRuntime:
    def __init__(self, workspace: local_stack.Workspace) -> None:
        self.workspace = workspace

    def doctor(self, *, port: int) -> dict[str, object]:
        return {
            "ready": True,
            "python": {"supported": True, "version": "3.12"},
            "dependencies": {"core": True, "server": True},
            "data": {"ready": True},
            "port": {"number": port, "available": True},
        }

    def initialize(self) -> dict[str, object]:
        self.workspace.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.database_path.write_bytes(b"test-db")
        return {
            "schema_version": "1.1",
            "performance_rows": 16,
            "trace_rows": 4,
            "incident_rows": 0,
            "server_schema": True,
        }

    def status(self, *, port: int) -> dict[str, object]:
        return {
            "ready": self.workspace.database_path.is_file(),
            "database": {
                "initialized": self.workspace.database_path.is_file(),
                "schema_version": "1.1",
                "incident_rows": 0,
            },
            "server": {"host": "127.0.0.1", "port": port, "available": True},
        }

    def demo(
        self,
        *,
        action_mode: str,
        confirm_incident: bool,
        approve_action: bool,
        reason: str | None,
        expected_action_hash: str | None,
        expected_revision: int | None,
        verification_outcome: str,
    ) -> dict[str, object]:
        confirmation = self.workspace.state_dir / "confirmed"
        if confirm_incident and approve_action:
            raise local_stack.SafeCliError("approval_requires_prior_preview")
        if approve_action and not confirmation.is_file():
            raise local_stack.SafeCliError("approval_requires_incident")
        if approve_action and action_mode != "simulate":
            raise local_stack.SafeCliError("actions_disabled")
        state = "PREVIEW"
        if confirm_incident:
            confirmation.write_text("confirmed")
            state = "AWAITING_APPROVAL"
        if approve_action:
            if expected_action_hash != "action-sha256" or expected_revision != 2:
                raise local_stack.SafeCliError("approval_binding_mismatch")
            state = "RESOLVED" if verification_outcome == "passed" else "REOPENED"
        return {
            "workflow_id": "local-demo-fixed",
            "state": state,
            "closed_loop": state == "RESOLVED",
            "action_mode": action_mode,
            "candidate_count": 2,
            "action_preview": {
                "action_hash": "action-sha256",
                "expected_revision": 2,
                "resources": ["lte-cell:1/1"],
                "risk": "LOW",
            },
            "artifacts": ["artifacts/demo-result.json"],
        }

    def serve(self, *, port: int) -> None:  # pragma: no cover - foreground path
        raise AssertionError("serve should not be exercised in unit tests")


def invoke(tmp_path: Path, *args: str) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(tmp_path / "stack"), *args],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FakeRuntime,
    )
    success = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, success, error


def test_workspace_is_required_and_errors_do_not_echo_paths(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()
    secret_path = tmp_path / "private-user-name" / "stack"
    code = local_stack.main(
        ["--workspace", str(secret_path), "status"],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FakeRuntime,
    )
    assert code == 1
    payload = json.loads(stderr.getvalue())
    assert payload["error"]["code"] == "workspace_not_initialized"
    assert str(secret_path) not in stderr.getvalue()


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC regression")
def test_unc_workspace_is_rejected_before_any_filesystem_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_probe(_path: Path) -> bool:
        raise AssertionError("UNC rejection must happen before a filesystem probe")

    monkeypatch.setattr(local_stack, "_is_link_like", unexpected_probe)
    with pytest.raises(local_stack.SafeCliError) as caught:
        local_stack.Workspace(Path(r"\\example.invalid\share\stack"))
    assert caught.value.code == "unsafe_workspace"


def test_doctor_is_read_only_and_json_contains_no_workspace_path(tmp_path: Path) -> None:
    code, payload, error = invoke(tmp_path, "doctor")
    assert (code, error) == (0, None)
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert not (tmp_path / "stack").exists()
    assert str(tmp_path) not in json.dumps(payload)


def test_init_is_idempotent_for_owned_workspace(tmp_path: Path) -> None:
    first_code, first, first_error = invoke(tmp_path, "init")
    second_code, second, second_error = invoke(tmp_path, "init")
    assert (first_code, first_error) == (0, None)
    assert (second_code, second_error) == (0, None)
    assert first["workspace"]["initialized"] is True
    assert second["workspace"]["initialized"] is True
    assert first["workspace"]["workspace_id"] == second["workspace"]["workspace_id"]
    marker = json.loads((tmp_path / "stack" / ".local-stack.json").read_text())
    assert marker["kind"] == "networkagent-local-stack"


def test_init_rejects_nonempty_unowned_directory_without_deleting(tmp_path: Path) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    sentinel = stack / "keep-me.txt"
    sentinel.write_text("owned by user")
    code, payload, error = invoke(tmp_path, "init")
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "workspace_not_owned"
    assert sentinel.read_text() == "owned by user"


def test_failed_first_init_rolls_back_only_new_owned_entries_and_can_retry(
    tmp_path: Path,
) -> None:
    class FailingRuntime(FakeRuntime):
        def initialize(self) -> dict[str, object]:
            self.workspace.state_dir.mkdir(parents=True, exist_ok=True)
            self.workspace.database_path.write_bytes(b"partial")
            raise local_stack.SafeCliError("runtime_failed")

    stack = tmp_path / "stack"
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(stack), "init"],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FailingRuntime,
    )
    assert code == 2
    assert not stack.exists()

    retry_code, retry, retry_error = invoke(tmp_path, "init")
    assert (retry_code, retry_error) == (0, None)
    assert retry["workspace"]["initialized"] is True


def test_reset_requires_yes_and_only_removes_owned_content(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    stack = tmp_path / "stack"
    user_file = stack / "user-notes.txt"
    user_file.write_text("preserve")

    code, payload, error = invoke(tmp_path, "reset")
    assert (code, error) == (1, None)
    assert payload["confirmation_required"] is True
    assert (stack / ".local-stack.json").is_file()

    code, payload, error = invoke(tmp_path, "reset", "--yes")
    assert (code, error) == (0, None)
    assert payload["reset"] is True
    assert payload["workspace_removed"] is False
    assert user_file.read_text() == "preserve"
    assert not (stack / ".local-stack.json").exists()
    assert not (stack / "state").exists()


@pytest.mark.parametrize("mode", ["disabled", "simulate"])
def test_demo_never_approves_without_explicit_flag(tmp_path: Path, mode: str) -> None:
    invoke(tmp_path, "init")
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        mode,
        "demo",
        "--confirm-incident",
    )
    assert (code, error) == (0, None)
    assert payload["result"]["state"] == "AWAITING_APPROVAL"
    assert payload["result"]["closed_loop"] is False


def test_simulated_closed_loop_requires_prior_preview_and_bound_confirmation(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")
    preview_code, preview, preview_error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    assert (preview_code, preview_error) == (0, None)
    binding = preview["result"]["action_preview"]

    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "approved local simulation",
        "--expected-action-hash",
        binding["action_hash"],
        "--expected-revision",
        str(binding["expected_revision"]),
    )
    assert (code, error) == (0, None)
    assert payload["result"]["state"] == "RESOLVED"
    assert payload["result"]["closed_loop"] is True
    assert str(tmp_path) not in json.dumps(payload)


def test_disabled_mode_rejects_action_approval(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    invoke(tmp_path, "demo", "--confirm-incident")
    code, payload, error = invoke(
        tmp_path,
        "demo",
        "--approve-action",
        "--reason",
        "must remain disabled",
        "--expected-action-hash",
        "action-sha256",
        "--expected-revision",
        "2",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "actions_disabled"


def test_action_approval_cannot_be_combined_with_first_confirmation(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
        "--approve-action",
        "--reason",
        "not reviewed yet",
        "--expected-action-hash",
        "guessed",
        "--expected-revision",
        "0",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "approval_requires_prior_preview"


def test_action_approval_requires_exact_preview_binding(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "reviewed",
        "--expected-action-hash",
        "wrong-hash",
        "--expected-revision",
        "2",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "approval_binding_mismatch"


def test_action_approval_requires_both_preview_binding_values(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "reviewed",
        "--expected-action-hash",
        "action-sha256",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "approval_binding_required"


def test_failed_simulated_verification_reopens_instead_of_claiming_closed_loop(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")
    _, preview, _ = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    binding = preview["result"]["action_preview"]
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "reviewed failure path",
        "--expected-action-hash",
        binding["action_hash"],
        "--expected-revision",
        str(binding["expected_revision"]),
        "--verification-outcome",
        "failed",
    )
    assert (code, error) == (0, None)
    assert payload["result"]["state"] == "REOPENED"
    assert payload["result"]["closed_loop"] is False


def test_action_preview_whitelists_resource_scope_fields() -> None:
    action = SimpleNamespace(
        action_hash="a" * 64,
        action_type="LOCAL_SIMULATION",
        risk_level=SimpleNamespace(value="LOW"),
        target_resources=(
            SimpleNamespace(
                resource_id="cell-1",
                resource_type=SimpleNamespace(value="CELL"),
                technology=SimpleNamespace(value="LTE"),
                attributes={"local_path": "must-not-leak"},
            ),
        ),
    )
    preview = local_stack._safe_action_preview(action)
    assert preview["resources"] == [
        {"resource_id": "cell-1", "resource_type": "CELL", "technology": "LTE"}
    ]
    assert preview["risk"] == "LOW"
    assert "must-not-leak" not in json.dumps(preview)


@pytest.mark.parametrize("resume_state", ("REMEDIATING", "VERIFYING"))
def test_real_runtime_resumes_after_approval_or_action_commit_response_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume_state: str,
) -> None:
    action = SimpleNamespace(
        action_hash="a" * 64,
        action_type="LOCAL_SIMULATION",
        target_resources=(),
        risk_level=SimpleNamespace(value="LOW"),
    )
    incident = SimpleNamespace(
        incident_id="incident-resume",
        revision=5 if resume_state == "REMEDIATING" else 6,
        status=SimpleNamespace(value=resume_state),
    )
    trigger = SimpleNamespace(
        incident_id=incident.incident_id,
        incident=SimpleNamespace(
            severity=SimpleNamespace(value="UNKNOWN"),
            technology=SimpleNamespace(value="LTE"),
            affected_resources=(),
        ),
    )

    class Repository:
        async def get(self, _incident_id: str):
            return incident

    class Detector:
        async def scan(self, _trace_id: str, *, workflow_id: str):
            assert workflow_id == "local-stack-detect-workflow-v1"
            return (trigger,)

    profile = SimpleNamespace(
        detector=Detector(),
        incident_repository=Repository(),
        rca_gateway=object(),
    )

    class LocalProfile:
        @staticmethod
        def open_existing(_config):
            return profile

    class Engine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(
                incident=incident,
                action=action,
                awaiting_approval=False,
            )

        async def decide(self, *_args, **kwargs):
            assert kwargs["expected_revision"] == 4
            return SimpleNamespace(incident=incident)

        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(
                incident=SimpleNamespace(status=SimpleNamespace(value="RESOLVED"))
            )

    local_module = SimpleNamespace(
        LocalProfile=LocalProfile,
        LocalProfileConfig=lambda **values: values,
    )
    governance_module = SimpleNamespace(LocalGovernanceEngine=Engine)
    monkeypatch.setitem(sys.modules, "telco_local", local_module)
    monkeypatch.setitem(sys.modules, "telco_local.governance", governance_module)

    workspace = local_stack.Workspace(tmp_path / "stack")
    runtime = local_stack.LocalStackRuntime(workspace)
    result = asyncio.run(
        runtime._run_demo(
            action_mode="simulate",
            confirm_incident=False,
            approve_action=True,
            reason="resume the exact reviewed simulation",
            expected_action_hash="a" * 64,
            expected_revision=4,
            verification_outcome="passed",
        )
    )
    assert result["state"] == "RESOLVED"
    assert result["closed_loop"] is True


def test_runtime_reports_expired_execution_grant_as_failed_without_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = SimpleNamespace(
        action_hash="b" * 64,
        action_type="LOCAL_SIMULATION",
        target_resources=(),
        risk_level=SimpleNamespace(value="LOW"),
    )
    failed_incident = SimpleNamespace(
        incident_id="incident-expired-execution",
        revision=6,
        status=SimpleNamespace(value="FAILED"),
    )
    trigger = SimpleNamespace(
        incident_id=failed_incident.incident_id,
        incident=SimpleNamespace(
            severity=SimpleNamespace(value="UNKNOWN"),
            technology=SimpleNamespace(value="LTE"),
            affected_resources=(),
        ),
    )

    class Repository:
        async def get(self, _incident_id: str):
            return failed_incident

    class Detector:
        async def scan(self, _trace_id: str, *, workflow_id: str):
            assert workflow_id == "local-stack-detect-workflow-v1"
            return (trigger,)

    profile = SimpleNamespace(
        detector=Detector(),
        incident_repository=Repository(),
        rca_gateway=object(),
    )

    class LocalProfile:
        @staticmethod
        def open_existing(_config):
            return profile

    class Engine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(
                incident=failed_incident,
                action=action,
                awaiting_approval=False,
            )

        async def decide(self, *_args, **_kwargs):
            return SimpleNamespace(incident=failed_incident)

        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(incident=failed_incident)

    monkeypatch.setitem(
        sys.modules,
        "telco_local",
        SimpleNamespace(
            LocalProfile=LocalProfile,
            LocalProfileConfig=lambda **values: values,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "telco_local.governance",
        SimpleNamespace(LocalGovernanceEngine=Engine),
    )

    result = asyncio.run(
        local_stack.LocalStackRuntime(
            local_stack.Workspace(tmp_path / "stack")
        )._run_demo(
            action_mode="simulate",
            confirm_incident=False,
            approve_action=True,
            reason="resume an expired local approval safely",
            expected_action_hash="b" * 64,
            expected_revision=4,
            verification_outcome="passed",
        )
    )
    assert result["state"] == "FAILED"
    assert result["closed_loop"] is False
    assert result["outcome"] == "APPROVAL_NOT_EFFECTIVE"
    assert result["approval"] == {
        "incident_confirmed": True,
        "action_approved": False,
        "decision_state": "FAILED",
    }


def test_reset_rejects_repository_root_even_with_forged_marker(tmp_path: Path) -> None:
    workspace = local_stack.REPOSITORY_ROOT
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(workspace), "reset", "--yes"],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FakeRuntime,
    )
    assert code == 2
    assert json.loads(stderr.getvalue())["error"]["code"] == "unsafe_workspace"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_junction_cannot_escape_artifact_write_or_reset(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    stack = tmp_path / "stack"
    artifacts = stack / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    artifacts.rmdir()
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(artifacts), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        workspace = local_stack.Workspace(stack)
        with pytest.raises(local_stack.SafeCliError) as caught:
            workspace.write_artifact("demo-result.json", {"safe": True})
        assert caught.value.code == "unsafe_workspace"
        assert not (outside / "demo-result.json").exists()

        code, payload, error = invoke(tmp_path, "reset", "--yes")
        assert (code, payload) == (2, None)
        assert error["error"]["code"] == "unsafe_workspace"
        assert outside.is_dir()
    finally:
        if getattr(artifacts, "is_junction", lambda: False)():
            os.rmdir(artifacts)
