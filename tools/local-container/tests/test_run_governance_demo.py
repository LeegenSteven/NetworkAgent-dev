from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "run_governance_demo.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_governance_demo", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _encoded(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class FakeDocker:
    def __init__(
        self,
        module,
        *,
        fail_at: str | None = None,
        context: str = "default",
        endpoint: str = "unix:///var/run/docker.sock",
    ) -> None:
        self.module = module
        self.fail_at = fail_at
        self.context = context
        self.endpoint = endpoint
        self.calls: list[tuple[str, ...]] = []
        self.started: dict[str, int] = {}
        self.running: dict[str, bool] = {}
        self.probe_failures_remaining: dict[str, int] = {}
        self.incidents: dict[str, str] = {}
        self.final_statuses: dict[str, str] = {}

    @staticmethod
    def _project(arguments: tuple[str, ...]) -> str:
        return arguments[arguments.index("--project-name") + 1]

    def __call__(self, arguments, _timeout):
        args = tuple(arguments)
        self.calls.append(args)
        joined = " ".join(args)
        if self.fail_at and self.fail_at in joined:
            return self.module.CommandResult(1, b"", b"failure not reflected")
        if args == ("docker", "context", "show"):
            return self.module.CommandResult(0, (self.context + "\n").encode(), b"")
        if args == (
            "docker",
            "context",
            "inspect",
            "default",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ):
            return self.module.CommandResult(0, (self.endpoint + "\n").encode(), b"")
        if args[:2] == ("docker", "inspect"):
            container_id = args[-1]
            project = next(
                key for key in self.started if self._container_id(key) == container_id
            )
            return self.module.CommandResult(
                0, f"2026-08-30T00:00:0{self.started[project]}Z\n".encode(), b""
            )

        project = self._project(args)
        tail = args[args.index(project) + 1 :]
        if tail[:3] == ("run", "--rm", "--no-deps"):
            service_and_command = tail[3:]
            if service_and_command == ("init",):
                return self._json(
                    {
                        "ok": True,
                        "command": "init",
                        "created": True,
                        "database": {"incident_rows": 0},
                        "network": {"external_access": False},
                    }
                )
            if service_and_command == ("init", "demo-seed"):
                incident = "incident-" + project[-12:]
                self.incidents[project] = incident
                return self._json(
                    {
                        "ok": True,
                        "command": "demo-seed",
                        "action_mode": "disabled",
                        "result": {
                            "incident_id": incident,
                            "status": "DETECTED",
                            "revision": 0,
                            "candidate_count": 15,
                        },
                    }
                )
            if service_and_command[:2] == ("init", "demo-verify"):
                assert self.running.get(project) is False
                expected = service_and_command[-1]
                return self._json(
                    {
                        "ok": True,
                        "command": "demo-verify",
                        "action_mode": "disabled",
                        "result": {
                            "incident_id": self.incidents[project],
                            "status": expected,
                            "expected_status": expected,
                            "revision": 7,
                            "action_runs": 1,
                            "verification_runs": 1,
                            "audit_events": 8,
                            "rca_reports": 1,
                            "recommendations": 1,
                            "approvals": 2,
                            "action": {
                                "action_type": "LOCAL_SIMULATION",
                                "status": "SUCCEEDED",
                                "side_effects": False,
                            },
                            "verification": {
                                "status": (
                                    "PASSED" if expected == "RESOLVED" else "FAILED"
                                )
                            },
                        },
                    }
                )
            if service_and_command == ("reset",):
                assert self.running.get(project) is False
                return self._json(
                    {
                        "ok": True,
                        "command": "reset",
                        "reset": True,
                        "workspace_removed": True,
                    }
                )
            if service_and_command[0] == "smoke":
                assert self.running.get(project) is True
                return self._governance(project, service_and_command[1:])
        if tail[:2] == ("up", "--detach"):
            self.started[project] = 1
            self.running[project] = True
            self.probe_failures_remaining[project] = 1
            return self._ok()
        if tail == ("restart", "assurance"):
            assert self.running.get(project) is True
            self.started[project] += 1
            self.probe_failures_remaining[project] = 1
            return self._ok()
        if tail == ("stop", "assurance"):
            assert self.running.get(project) is True
            self.running[project] = False
            return self._ok()
        if tail == ("ps", "-q", "assurance"):
            if not self.running.get(project):
                return self.module.CommandResult(0, b"", b"")
            return self.module.CommandResult(
                0, (self._container_id(project) + "\n").encode(), b""
            )
        if tail[:3] == ("exec", "-T", "assurance"):
            if not self.running.get(project):
                return self.module.CommandResult(1, b"", b"stopped")
            remaining = self.probe_failures_remaining[project]
            if remaining:
                self.probe_failures_remaining[project] = remaining - 1
                return self.module.CommandResult(1, b"", b"starting")
            return self._ok()
        if tail == ("down", "--volumes", "--remove-orphans"):
            return self._ok()
        raise AssertionError(args)

    def _container_id(self, project: str) -> str:
        return (project[-12:] * 6)[:64]

    def _governance(self, project: str, command: tuple[str, ...]):
        incident = self.incidents[project]
        replayed = self.started[project] == 2
        if command == ("governance-prepare", incident):
            return self._json(
                {
                    "ok": True,
                    "command": "governance-prepare",
                    "incident_id": incident,
                    "status": (
                        self.final_statuses[project]
                        if replayed
                        else "AWAITING_APPROVAL"
                    ),
                    "replayed": replayed,
                    "action_hash": "a" * 64,
                    "revision": 7 if replayed else 4,
                }
            )
        if command == ("governance-decide", incident, "a" * 64, "4"):
            return self._json(
                {
                    "ok": True,
                    "command": "governance-decide",
                    "incident_id": incident,
                    "status": (
                        self.final_statuses[project] if replayed else "REMEDIATING"
                    ),
                    "replayed": replayed,
                }
            )
        if command[:2] == ("governance-execute", incident):
            status = "RESOLVED" if command[2] == "passed" else "REOPENED"
            self.final_statuses[project] = status
            return self._json(
                {
                    "ok": True,
                    "command": "governance-execute",
                    "incident_id": incident,
                    "status": status,
                    "replayed": replayed,
                }
            )
        raise AssertionError(command)

    def _json(self, payload: dict[str, object]):
        return self.module.CommandResult(0, _encoded(payload), b"")

    def _ok(self):
        return self.module.CommandResult(0, b"", b"")


def test_runs_success_failure_restart_replay_and_cleanup(monkeypatch) -> None:
    module = _load_module()
    tokens = iter(("111111111111", "222222222222"))
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: next(tokens))
    docker = FakeDocker(module)
    result = module.GovernanceDemoRunner(
        command_runner=docker, sleeper=lambda _seconds: None, environ={}
    ).run()
    assert result == {
        "ok": True,
        "command": "container-governance-demo",
        "results": [
            {
                "outcome": "success",
                "status": "RESOLVED",
                "restart_observed": True,
                "exact_replay": True,
                "real_network_side_effects": False,
            },
            {
                "outcome": "failure",
                "status": "REOPENED",
                "restart_observed": True,
                "exact_replay": True,
                "real_network_side_effects": False,
            },
        ],
        "projects_removed": True,
    }
    down_calls = [call for call in docker.calls if "down" in call]
    assert len(down_calls) == 2
    assert all(
        call[-3:] == ("down", "--volumes", "--remove-orphans") for call in down_calls
    )


@pytest.mark.parametrize(
    "failure",
    ("demo-seed", "governance-prepare", "restart assurance", "demo-verify"),
)
def test_failure_always_removes_exact_random_project(monkeypatch, failure: str) -> None:
    module = _load_module()
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: "333333333333")
    docker = FakeDocker(module, fail_at=failure)
    runner = module.GovernanceDemoRunner(
        command_runner=docker, sleeper=lambda _seconds: None, environ={}
    )
    with pytest.raises(module.GovernanceDemoError):
        runner.run_outcome("success")
    assert docker.calls[-1][-3:] == ("down", "--volumes", "--remove-orphans")
    assert "networkagent-s2-success-333333333333" in docker.calls[-1]


def test_changed_replay_response_fails_closed_and_cleans(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: "444444444444")
    docker = FakeDocker(module)
    original = docker._governance

    def changed(project, command):
        response = original(project, command)
        if docker.started[project] == 2 and command[0] == "governance-execute":
            payload = json.loads(response.stdout)
            payload["replayed"] = False
            return module.CommandResult(0, _encoded(payload), b"")
        return response

    docker._governance = changed
    with pytest.raises(module.GovernanceDemoError, match="response_invalid"):
        module.GovernanceDemoRunner(
            command_runner=docker, sleeper=lambda _seconds: None, environ={}
        ).run_outcome("success")
    assert docker.calls[-1][-3:] == ("down", "--volumes", "--remove-orphans")


def test_invalid_or_oversized_json_is_rejected() -> None:
    module = _load_module()
    with pytest.raises(module.GovernanceDemoError, match="response_invalid"):
        module._load_json(b'{"ok":true,"ok":true}')
    with pytest.raises(module.GovernanceDemoError, match="response_invalid"):
        module._load_json(b"{" + b"x" * module.MAX_JSON_BYTES + b"}")
    with pytest.raises(module.GovernanceDemoError, match="response_invalid"):
        module._load_json(b'{"ok":true,"value":NaN}')
    deeply_nested = b'{"ok":true,"value":' + (b"[" * 2_000) + (b"]" * 2_000) + b"}"
    with pytest.raises(module.GovernanceDemoError, match="response_invalid"):
        module._load_json(deeply_nested)


def test_command_timeout_terminates_descendants_holding_output_pipes() -> None:
    module = _load_module()
    child = "import time; time.sleep(30)"
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
    )
    started = time.monotonic()
    with pytest.raises(module.GovernanceDemoError, match="command_failed"):
        module._default_command_runner((sys.executable, "-c", parent), 0.2)
    assert time.monotonic() - started < 6.0


def test_command_output_is_stopped_at_hard_limit() -> None:
    module = _load_module()
    program = f"import sys; sys.stdout.buffer.write(b'x'*{module.MAX_PROCESS_OUTPUT_BYTES + 1})"
    with pytest.raises(module.GovernanceDemoError, match="output_limit"):
        module._default_command_runner((sys.executable, "-c", program), 5.0)


def test_command_runner_collects_bounded_stdout_and_stderr() -> None:
    module = _load_module()
    program = "import sys; sys.stdout.write('ok'); sys.stderr.write('diagnostic')"
    result = module._default_command_runner((sys.executable, "-c", program), 5.0)
    assert result == module.CommandResult(0, b"ok", b"diagnostic")


def test_invalid_project_and_outcome_are_rejected_before_docker() -> None:
    module = _load_module()
    calls = []
    runner = module.GovernanceDemoRunner(
        command_runner=FakeDocker(module),
        sleeper=lambda _seconds: None,
        environ={},
    )
    with pytest.raises(module.GovernanceDemoError, match="arguments_invalid"):
        runner.run_outcome("other")
    with pytest.raises(module.GovernanceDemoError, match="arguments_invalid"):
        runner._compose("../unsafe", "down")
    assert calls == []


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("DOCKER_HOST", "tcp://production.example.test:2376"),
        ("DOCKER_CONTEXT", "remote-production"),
        ("DOCKER_TLS_VERIFY", "1"),
        ("DOCKER_CERT_PATH", "/untrusted"),
        ("COMPOSE_PROJECT_NAME", "untrusted"),
        ("COMPOSE_PROFILES", "untrusted"),
    ),
)
def test_remote_or_overriding_docker_environment_is_rejected_before_docker(
    name: str, value: str
) -> None:
    module = _load_module()
    calls = []
    runner = module.GovernanceDemoRunner(
        command_runner=lambda args, timeout: calls.append((args, timeout)),
        sleeper=lambda _seconds: None,
        environ={name: value},
    )
    with pytest.raises(module.GovernanceDemoError, match="environment_unsafe"):
        runner.run_outcome("success")
    assert calls == []


@pytest.mark.parametrize(
    ("context", "endpoint"),
    (
        ("production", "unix:///var/run/docker.sock"),
        ("default", "tcp://production.example.test:2376"),
        ("default", "ssh://operator@production.example.test"),
        ("default", "unix:///var/run/../remote.sock"),
    ),
)
def test_nonlocal_current_docker_context_is_rejected_before_compose_mutation(
    context: str, endpoint: str
) -> None:
    module = _load_module()
    docker = FakeDocker(module, context=context, endpoint=endpoint)
    runner = module.GovernanceDemoRunner(
        command_runner=docker, sleeper=lambda _seconds: None, environ={}
    )
    with pytest.raises(module.GovernanceDemoError, match="environment_unsafe"):
        runner.run_outcome("success")
    assert not any("compose" in call for call in docker.calls)


def test_local_windows_named_pipe_context_is_allowed(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: "555555555555")
    docker = FakeDocker(module, endpoint="npipe:////./pipe/dockerDesktopLinuxEngine")
    result = module.GovernanceDemoRunner(
        command_runner=docker, sleeper=lambda _seconds: None, environ={}
    ).run_outcome("success")
    assert result["status"] == "RESOLVED"


def test_primary_failure_plus_repeated_cleanup_failure_is_explicit(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: "666666666666")
    docker = FakeDocker(module)
    original = docker.__call__
    down_attempts = 0

    def fail_seed_and_cleanup(arguments, timeout):
        nonlocal down_attempts
        joined = " ".join(arguments)
        if " down " in f" {joined} ":
            down_attempts += 1
            return module.CommandResult(1, b"", b"failure")
        if "demo-seed" in joined:
            return module.CommandResult(1, b"", b"failure")
        return original(arguments, timeout)

    runner = module.GovernanceDemoRunner(
        command_runner=fail_seed_and_cleanup,
        sleeper=lambda _seconds: None,
        environ={},
    )
    with pytest.raises(module.GovernanceDemoError, match="cleanup_failed"):
        runner.run_outcome("success")
    assert down_attempts == 2
