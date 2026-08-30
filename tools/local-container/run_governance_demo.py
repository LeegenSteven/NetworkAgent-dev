#!/usr/bin/env python3
"""Run the bounded S2 container governance acceptance flow.

This host-side CI helper has a deliberately closed command graph.  It creates
two isolated Compose projects, proves one successful and one failed local
verification path, restarts Assurance, replays the exact governance requests,
and always removes the project volumes.  It never accepts a command, URL,
workspace path, or Compose project name from the caller.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, NamedTuple, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "local" / "compose.yaml"
MAX_PROCESS_OUTPUT_BYTES = 256 * 1024
MAX_JSON_BYTES = 65_536
MAX_JSON_DEPTH = 16
COMMAND_TIMEOUT_SECONDS = 120.0
HEALTH_ATTEMPTS = 30
HEALTH_INTERVAL_SECONDS = 2.0
HEALTH_COMMAND_TIMEOUT_SECONDS = 5.0
HEALTH_DEADLINE_SECONDS = 90.0
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}\Z")
_INCIDENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT = re.compile(r"networkagent-s2-(success|failure)-[0-9a-f]{12}\Z")
_LOCAL_UNIX_ENDPOINT = re.compile(r"unix:///[A-Za-z0-9_./-]{1,240}\Z")
_LOCAL_NPIPE_ENDPOINT = re.compile(r"npipe:////\./pipe/[A-Za-z0-9_.-]{1,120}\Z")
_UNSAFE_DOCKER_ENVIRONMENT = {
    "COMPOSE_PROFILES",
    "COMPOSE_PROJECT_NAME",
    "DOCKER_CERT_PATH",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
}


class GovernanceDemoError(RuntimeError):
    """A stable, non-reflecting acceptance-flow failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CommandResult(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: bytes


CommandRunner = Callable[[Sequence[str], float], CommandResult]
Sleeper = Callable[[float], None]


def _default_command_runner(arguments: Sequence[str], timeout: float) -> CommandResult:
    process_options: dict[str, object] = {}
    if os.name == "nt":
        process_options["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        process_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            list(arguments),
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **process_options,
        )
    except OSError:
        raise GovernanceDemoError("container_demo_command_failed") from None
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    read_failed = threading.Event()

    def terminate_tree() -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                subprocess.run(
                    (
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                    check=False,
                    shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            process.kill()
        except OSError:
            pass

    def drain(stream, target: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                if len(target) + len(chunk) > MAX_PROCESS_OUTPUT_BYTES:
                    overflow.set()
                    terminate_tree()
                    return
                target.extend(chunk)
        except OSError:
            read_failed.set()
            terminate_tree()

    readers = (
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_tree()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass
        returncode = process.returncode
    finally:
        join_deadline = time.monotonic() + 2.0
        for reader in readers:
            reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
        if not readers[0].is_alive():
            process.stdout.close()
        if not readers[1].is_alive():
            process.stderr.close()
    if overflow.is_set():
        raise GovernanceDemoError("container_demo_output_limit")
    if (
        timed_out
        or read_failed.is_set()
        or any(reader.is_alive() for reader in readers)
    ):
        raise GovernanceDemoError("container_demo_command_failed")
    return CommandResult(returncode, bytes(stdout), bytes(stderr))


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _load_json(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > MAX_JSON_BYTES:
        raise GovernanceDemoError("container_demo_response_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise GovernanceDemoError("container_demo_response_invalid") from None
    if (
        not isinstance(value, dict)
        or value.get("ok") is not True
        or _json_depth(value) > MAX_JSON_DEPTH
    ):
        raise GovernanceDemoError("container_demo_response_invalid")
    return value


def _json_depth(value: object) -> int:
    maximum = 1
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if maximum > MAX_JSON_DEPTH:
            return maximum
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return maximum


def _bounded_result(result: CommandResult) -> CommandResult:
    if (
        len(result.stdout) > MAX_PROCESS_OUTPUT_BYTES
        or len(result.stderr) > MAX_PROCESS_OUTPUT_BYTES
    ):
        raise GovernanceDemoError("container_demo_output_limit")
    if result.returncode != 0:
        raise GovernanceDemoError("container_demo_command_failed")
    return result


class GovernanceDemoRunner:
    """Closed Docker Compose orchestrator used only by the S2 acceptance gate."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner = _default_command_runner,
        sleeper: Sleeper = time.sleep,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._command_runner = command_runner
        self._sleeper = sleeper
        self._environ = dict(os.environ if environ is None else environ)
        self._preflight_complete = False

    def _validate_environment(self) -> None:
        if any(self._environ.get(name) for name in _UNSAFE_DOCKER_ENVIRONMENT):
            raise GovernanceDemoError("container_demo_environment_unsafe")
        context = self._environ.get("DOCKER_CONTEXT")
        if context not in {None, "", "default"}:
            raise GovernanceDemoError("container_demo_environment_unsafe")

    def _validate_docker_context(self) -> None:
        shown = _bounded_result(
            self._command_runner(("docker", "context", "show"), COMMAND_TIMEOUT_SECONDS)
        ).stdout
        try:
            context = shown.decode("ascii").strip()
        except UnicodeError:
            raise GovernanceDemoError("container_demo_environment_unsafe") from None
        if context != "default":
            raise GovernanceDemoError("container_demo_environment_unsafe")
        inspected = _bounded_result(
            self._command_runner(
                (
                    "docker",
                    "context",
                    "inspect",
                    "default",
                    "--format",
                    "{{.Endpoints.docker.Host}}",
                ),
                COMMAND_TIMEOUT_SECONDS,
            )
        ).stdout
        try:
            endpoint = inspected.decode("ascii").strip()
        except UnicodeError:
            raise GovernanceDemoError("container_demo_environment_unsafe") from None
        if _LOCAL_UNIX_ENDPOINT.fullmatch(endpoint):
            socket_path = endpoint.removeprefix("unix://")
            if ".." not in Path(socket_path).parts:
                return
        if _LOCAL_NPIPE_ENDPOINT.fullmatch(endpoint):
            return
        raise GovernanceDemoError("container_demo_environment_unsafe")

    def _preflight(self) -> None:
        if self._preflight_complete:
            return
        self._validate_environment()
        self._validate_docker_context()
        self._preflight_complete = True

    @staticmethod
    def project_name(outcome: str) -> str:
        if outcome not in {"success", "failure"}:
            raise GovernanceDemoError("container_demo_arguments_invalid")
        value = f"networkagent-s2-{outcome}-{secrets.token_hex(6)}"
        if _PROJECT.fullmatch(value) is None:
            raise GovernanceDemoError("container_demo_arguments_invalid")
        return value

    def _compose(
        self,
        project: str,
        *arguments: str,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> CommandResult:
        if _PROJECT.fullmatch(project) is None:
            raise GovernanceDemoError("container_demo_arguments_invalid")
        command = (
            "docker",
            "compose",
            "--file",
            str(COMPOSE_FILE),
            "--project-name",
            project,
            *arguments,
        )
        return _bounded_result(self._command_runner(command, timeout))

    def _json(self, project: str, *arguments: str) -> dict[str, object]:
        return _load_json(self._compose(project, *arguments).stdout)

    @staticmethod
    def _require_fields(
        payload: dict[str, object],
        *,
        command: str,
        incident_id: str | None = None,
        status: str | None = None,
        replayed: bool | None = None,
    ) -> None:
        if payload.get("command") != command:
            raise GovernanceDemoError("container_demo_response_invalid")
        if incident_id is not None and payload.get("incident_id") != incident_id:
            raise GovernanceDemoError("container_demo_response_invalid")
        if status is not None and payload.get("status") != status:
            raise GovernanceDemoError("container_demo_response_invalid")
        if replayed is not None and payload.get("replayed") is not replayed:
            raise GovernanceDemoError("container_demo_response_invalid")

    def _wait_healthy(self, project: str) -> tuple[str, str]:
        deadline = time.monotonic() + HEALTH_DEADLINE_SECONDS
        for _attempt in range(HEALTH_ATTEMPTS):
            if time.monotonic() >= deadline:
                break
            try:
                container_id = self._compose(
                    project,
                    "ps",
                    "-q",
                    "assurance",
                    timeout=HEALTH_COMMAND_TIMEOUT_SECONDS,
                ).stdout
                try:
                    rendered_id = container_id.decode("ascii").strip()
                except UnicodeError:
                    rendered_id = ""
                if _CONTAINER_ID.fullmatch(rendered_id):
                    self._compose(
                        project,
                        "exec",
                        "-T",
                        "assurance",
                        "python",
                        "/opt/networkagent/bin/container_entrypoint.py",
                        "probe",
                        timeout=HEALTH_COMMAND_TIMEOUT_SECONDS,
                    )
                    started = self._inspect_started_at(
                        rendered_id, timeout=HEALTH_COMMAND_TIMEOUT_SECONDS
                    )
                    return rendered_id, started
            except GovernanceDemoError:
                pass
            if time.monotonic() + HEALTH_INTERVAL_SECONDS >= deadline:
                break
            self._sleeper(HEALTH_INTERVAL_SECONDS)
        raise GovernanceDemoError("container_demo_health_timeout")

    def _inspect_started_at(
        self, container_id: str, *, timeout: float = COMMAND_TIMEOUT_SECONDS
    ) -> str:
        if _CONTAINER_ID.fullmatch(container_id) is None:
            raise GovernanceDemoError("container_demo_response_invalid")
        value = _bounded_result(
            self._command_runner(
                (
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.StartedAt}}",
                    container_id,
                ),
                timeout,
            )
        ).stdout
        try:
            started = value.decode("ascii").strip()
        except UnicodeError:
            raise GovernanceDemoError("container_demo_response_invalid") from None
        if not started or len(started) > 64:
            raise GovernanceDemoError("container_demo_response_invalid")
        return started

    def _seed(self, project: str) -> str:
        initialized = self._json(project, "run", "--rm", "--no-deps", "init")
        if (
            initialized.get("command") != "init"
            or initialized.get("created") is not True
            or not isinstance(initialized.get("database"), dict)
            or initialized["database"].get("incident_rows") != 0
            or not isinstance(initialized.get("network"), dict)
            or initialized["network"].get("external_access") is not False
        ):
            raise GovernanceDemoError("container_demo_response_invalid")
        seeded = self._json(
            project,
            "run",
            "--rm",
            "--no-deps",
            "init",
            "demo-seed",
        )
        if seeded.get("command") != "demo-seed":
            raise GovernanceDemoError("container_demo_response_invalid")
        if seeded.get("action_mode") != "disabled":
            raise GovernanceDemoError("container_demo_response_invalid")
        result = seeded.get("result")
        if not isinstance(result, dict):
            raise GovernanceDemoError("container_demo_response_invalid")
        incident_id = result.get("incident_id")
        if (
            not isinstance(incident_id, str)
            or _INCIDENT_ID.fullmatch(incident_id) is None
            or result.get("status") != "DETECTED"
            or result.get("revision") != 0
            or not isinstance(result.get("candidate_count"), int)
            or isinstance(result.get("candidate_count"), bool)
            or not 1 <= result["candidate_count"] <= 1_000
        ):
            raise GovernanceDemoError("container_demo_response_invalid")
        return incident_id

    def _govern(
        self,
        project: str,
        incident_id: str,
        verification: str,
        *,
        replayed: bool,
        binding: tuple[str, int] | None = None,
    ) -> tuple[str, int]:
        expected_final_status = "RESOLVED" if verification == "passed" else "REOPENED"
        prepare = self._json(
            project,
            "run",
            "--rm",
            "--no-deps",
            "smoke",
            "governance-prepare",
            incident_id,
        )
        self._require_fields(
            prepare,
            command="governance-prepare",
            incident_id=incident_id,
            status=expected_final_status if replayed else "AWAITING_APPROVAL",
            replayed=replayed,
        )
        returned_action_hash = prepare.get("action_hash")
        returned_revision = prepare.get("revision")
        if (
            not isinstance(returned_action_hash, str)
            or _SHA256.fullmatch(returned_action_hash) is None
            or not isinstance(returned_revision, int)
            or isinstance(returned_revision, bool)
            or returned_revision < 0
        ):
            raise GovernanceDemoError("container_demo_response_invalid")
        if replayed:
            if binding is None or returned_action_hash != binding[0]:
                raise GovernanceDemoError("container_demo_response_invalid")
            action_hash, revision = binding
        else:
            if binding is not None:
                raise GovernanceDemoError("container_demo_response_invalid")
            action_hash, revision = returned_action_hash, returned_revision

        decide = self._json(
            project,
            "run",
            "--rm",
            "--no-deps",
            "smoke",
            "governance-decide",
            incident_id,
            action_hash,
            str(revision),
        )
        self._require_fields(
            decide,
            command="governance-decide",
            incident_id=incident_id,
            status=expected_final_status if replayed else "REMEDIATING",
            replayed=replayed,
        )

        execute = self._json(
            project,
            "run",
            "--rm",
            "--no-deps",
            "smoke",
            "governance-execute",
            incident_id,
            verification,
        )
        self._require_fields(
            execute,
            command="governance-execute",
            incident_id=incident_id,
            status=expected_final_status,
            replayed=replayed,
        )
        return action_hash, revision

    def _verify_and_reset(
        self, project: str, incident_id: str, expected_status: str
    ) -> None:
        verified = self._json(
            project,
            "run",
            "--rm",
            "--no-deps",
            "init",
            "demo-verify",
            "--expected-status",
            expected_status,
        )
        if (
            verified.get("command") != "demo-verify"
            or verified.get("action_mode") != "disabled"
        ):
            raise GovernanceDemoError("container_demo_response_invalid")
        result = verified.get("result")
        expected_verification = "PASSED" if expected_status == "RESOLVED" else "FAILED"
        if (
            not isinstance(result, dict)
            or result.get("incident_id") != incident_id
            or result.get("status") != expected_status
            or result.get("expected_status") != expected_status
            or result.get("revision") != 7
            or result.get("action_runs") != 1
            or result.get("verification_runs") != 1
            or result.get("audit_events") != 8
            or result.get("rca_reports") != 1
            or result.get("recommendations") != 1
            or result.get("approvals") != 2
            or result.get("action")
            != {
                "action_type": "LOCAL_SIMULATION",
                "status": "SUCCEEDED",
                "side_effects": False,
            }
            or result.get("verification") != {"status": expected_verification}
        ):
            raise GovernanceDemoError("container_demo_response_invalid")
        reset = self._json(project, "run", "--rm", "--no-deps", "reset")
        if (
            reset.get("command") != "reset"
            or reset.get("reset") is not True
            or reset.get("workspace_removed") is not True
        ):
            raise GovernanceDemoError("container_demo_response_invalid")

    def run_outcome(self, outcome: str) -> dict[str, object]:
        if outcome not in {"success", "failure"}:
            raise GovernanceDemoError("container_demo_arguments_invalid")
        self._preflight()
        project = self.project_name(outcome)
        verification = "passed" if outcome == "success" else "failed"
        expected_status = "RESOLVED" if outcome == "success" else "REOPENED"
        try:
            incident_id = self._seed(project)
            self._compose(project, "up", "--detach", "assurance")
            before_id, before_started = self._wait_healthy(project)
            binding = self._govern(project, incident_id, verification, replayed=False)
            self._compose(project, "restart", "assurance")
            after_id, after_started = self._wait_healthy(project)
            if (before_id, before_started) == (after_id, after_started):
                raise GovernanceDemoError("container_demo_restart_not_observed")
            self._govern(
                project,
                incident_id,
                verification,
                replayed=True,
                binding=binding,
            )
            self._compose(project, "stop", "assurance")
            self._verify_and_reset(project, incident_id, expected_status)
            return {
                "outcome": outcome,
                "status": expected_status,
                "restart_observed": True,
                "exact_replay": True,
                "real_network_side_effects": False,
            }
        finally:
            cleanup_error: GovernanceDemoError | None = None
            for _attempt in range(2):
                try:
                    self._compose(
                        project,
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    )
                    cleanup_error = None
                    break
                except GovernanceDemoError as exc:
                    cleanup_error = exc
            if cleanup_error is not None:
                raise GovernanceDemoError("container_demo_cleanup_failed") from None

    def run(self) -> dict[str, object]:
        results = [self.run_outcome("success"), self.run_outcome("failure")]
        return {
            "ok": True,
            "command": "container-governance-demo",
            "results": results,
            "projects_removed": True,
        }


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="networkagent-container-governance-demo")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    del arguments
    try:
        payload = GovernanceDemoRunner().run()
    except GovernanceDemoError as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": exc.code}},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
