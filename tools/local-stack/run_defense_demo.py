#!/usr/bin/env python3
"""Run the fixed, offline two-branch defense demonstration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "networkagent-native-defense-demo/1.0"
REPORT_NAME = "defense-demo-report.json"
MAX_STDOUT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 16
COMMAND_TIMEOUT_SECONDS = 60
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_INCIDENT_ID = re.compile(r"incident-[0-9a-f]{64}\Z")
_RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class DefenseDemoError(Exception):
    """A stable failure that is safe to expose without local details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_MESSAGES = {
    "cleanup_failed": "local demo cleanup failed safely",
    "command_failed": "local demo command failed safely",
    "confirmation_required": "explicit local simulation approval is required",
    "evidence_contract_failed": "local demo evidence did not match the fixed contract",
    "invalid_arguments": "command arguments are invalid",
    "report_write_failed": "local demo report could not be written safely",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise DefenseDemoError("invalid_arguments")


def _write_json(stream: TextIO, value: object) -> None:
    json.dump(
        value,
        stream,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="networkagent-defense-demo", add_help=False)
    parser.add_argument("--approve-local-simulation", action="store_true")
    return parser


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _validate_json_depth(value: object, *, depth: int = 1) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("JSON depth exceeded")
    if isinstance(value, Mapping):
        for child in value.values():
            _validate_json_depth(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json_depth(child, depth=depth + 1)


def _decode_json_document(body: bytes) -> dict[str, object]:
    """Decode one strictly bounded JSON object from a child process."""

    try:
        if not isinstance(body, bytes) or len(body) > MAX_STDOUT_BYTES:
            raise ValueError("invalid size")
        text = body.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
        )
        if not isinstance(value, dict):
            raise ValueError("object required")
        _validate_json_depth(value)
        return value
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise DefenseDemoError("command_failed") from None


def _safe_environment() -> dict[str, str]:
    """Return a minimal environment with no Python, proxy, cloud, or Docker input."""

    result: dict[str, str] = {}
    for key in (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
    ):
        value = os.environ.get(key)
        if value:
            result[key] = value
    result.update(
        {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "PYTHONWARNINGS": (
                "ignore::DeprecationWarning,"
                "ignore::UserWarning:a2a.server.apps.jsonrpc.fastapi_app"
            ),
            "SOURCE_DATE_EPOCH": "946684800",
            "TZ": "UTC",
        }
    )
    return result


def _default_process_runner(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    process_options: dict[str, object] = {}
    if os.name == "nt":
        process_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        process_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **process_options,
        )
    except OSError:
        raise DefenseDemoError("command_failed") from None
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    read_failed = threading.Event()

    def stop() -> None:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def drain(stream: Any, target: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(8_192)
                if not chunk:
                    return
                if len(target) + len(chunk) > MAX_STDOUT_BYTES:
                    overflow.set()
                    stop()
                    return
                target.extend(chunk)
        except OSError:
            read_failed.set()
            stop()

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
        stop()
        try:
            returncode = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            returncode = -1
    finally:
        join_deadline = time.monotonic() + 2
        for reader in readers:
            reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
        if not readers[0].is_alive():
            process.stdout.close()
        if not readers[1].is_alive():
            process.stderr.close()
    if (
        timed_out
        or overflow.is_set()
        or read_failed.is_set()
        or any(reader.is_alive() for reader in readers)
    ):
        raise DefenseDemoError("command_failed")
    return subprocess.CompletedProcess(
        arguments, returncode, bytes(stdout), bytes(stderr)
    )


def _run_process(
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    arguments: tuple[str, ...],
    *,
    repository_root: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = process_runner(
            arguments,
            cwd=repository_root,
            env=dict(environment),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except Exception:
        raise DefenseDemoError("command_failed") from None
    if not isinstance(completed.stdout, bytes) or not isinstance(
        completed.stderr, bytes
    ):
        raise DefenseDemoError("command_failed")
    return completed


def _call_stack(
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    arguments: tuple[str, ...],
    *,
    repository_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    command = (
        sys.executable,
        str(repository_root / "tools" / "local-stack" / "local_stack.py"),
        *arguments,
    )
    completed = _run_process(
        process_runner,
        command,
        repository_root=repository_root,
        environment=environment,
    )
    if completed.returncode != 0 or completed.stderr != b"":
        raise DefenseDemoError("command_failed")
    payload = _decode_json_document(completed.stdout)
    if payload.get("ok") is not True:
        raise DefenseDemoError("command_failed")
    return payload


def _read_source_binding(
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    *,
    repository_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    try:
        revision = _run_process(
            process_runner,
            ("git", "rev-parse", "--verify", "HEAD"),
            repository_root=repository_root,
            environment=environment,
        )
        status = _run_process(
            process_runner,
            ("git", "status", "--porcelain", "--untracked-files=no"),
            repository_root=repository_root,
            environment=environment,
        )
        if (
            revision.returncode != 0
            or status.returncode != 0
            or revision.stderr != b""
            or status.stderr != b""
            or len(revision.stdout) > MAX_STDOUT_BYTES
            or len(status.stdout) > MAX_STDOUT_BYTES
        ):
            raise DefenseDemoError("command_failed")
        sha = revision.stdout.decode("ascii", errors="strict").strip()
        if _GIT_SHA.fullmatch(sha) is None:
            raise DefenseDemoError("command_failed")
        return {"available": True, "sha": sha, "tracked_clean": status.stdout == b""}
    except Exception:
        return {"available": False, "sha": None, "tracked_clean": False}


def _combine_source_bindings(
    before: Mapping[str, object], after: Mapping[str, object]
) -> dict[str, object]:
    git_available = before.get("available") is True and after.get("available") is True
    binding_stable = bool(
        git_available
        and before.get("sha") == after.get("sha")
        and isinstance(before.get("sha"), str)
    )
    tracked_clean = bool(
        git_available
        and before.get("tracked_clean") is True
        and after.get("tracked_clean") is True
    )
    commit_bound = binding_stable and tracked_clean
    return {
        "binding_stable": binding_stable,
        "commit_bound": commit_bound,
        "commit_sha": before.get("sha") if binding_stable else None,
        "git_available": git_available,
        "tracked_clean": tracked_clean,
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DefenseDemoError("evidence_contract_failed")
    return value


def _expect(value: object, expected: object) -> None:
    if value != expected or type(value) is not type(expected):
        raise DefenseDemoError("evidence_contract_failed")


def _validate_doctor(payload: Mapping[str, object]) -> None:
    _expect(payload.get("command"), "doctor")
    report = _mapping(payload.get("report"))
    _expect(report.get("ready"), True)
    _expect(report.get("demo_ready"), True)
    dependencies = _mapping(report.get("dependencies"))
    _expect(dependencies.get("core"), True)
    _expect(dependencies.get("governance"), True)
    _expect(_mapping(report.get("data")).get("ready"), True)
    _expect(_mapping(report.get("network")).get("external_access"), False)


def _validate_init(payload: Mapping[str, object]) -> dict[str, int]:
    _expect(payload.get("command"), "init")
    database = _mapping(payload.get("database"))
    expected = {
        "performance_rows": 13_440,
        "trace_rows": 579,
        "incident_rows": 0,
    }
    for key, value in expected.items():
        _expect(database.get(key), value)
    return expected


def _validate_status(payload: Mapping[str, object]) -> dict[str, object]:
    _expect(payload.get("command"), "status")
    report = _mapping(payload.get("report"))
    _expect(report.get("ready"), True)
    database = _mapping(report.get("database"))
    _expect(database.get("initialized"), True)
    _expect(database.get("schema_version"), "1.1")
    _expect(database.get("incident_rows"), 0)
    runtime = _mapping(report.get("runtime"))
    _expect(runtime.get("demo_ready"), True)
    _expect(runtime.get("governance"), True)
    _expect(_mapping(report.get("server")).get("external_access"), False)
    return {
        "database_initialized": True,
        "demo_ready": True,
        "governance": True,
        "incident_rows": 0,
        "ready": True,
        "schema_version": "1.1",
    }


def _validate_selected_candidate(value: object) -> dict[str, object]:
    selected = _mapping(value)
    if set(selected) != {"incident_id", "resource_count", "severity", "technology"}:
        raise DefenseDemoError("evidence_contract_failed")
    incident_id = selected.get("incident_id")
    if not isinstance(incident_id, str) or _INCIDENT_ID.fullmatch(incident_id) is None:
        raise DefenseDemoError("evidence_contract_failed")
    _expect(selected.get("technology"), "LTE")
    _expect(selected.get("resource_count"), 2)
    _expect(selected.get("severity"), "UNKNOWN")
    return {
        "incident_id": incident_id,
        "resource_count": 2,
        "severity": "UNKNOWN",
        "technology": "LTE",
    }


def _validate_resources(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 2:
        raise DefenseDemoError("evidence_contract_failed")
    resources: list[dict[str, str]] = []
    for item in value:
        resource = _mapping(item)
        if set(resource) != {"resource_id", "resource_type", "technology"}:
            raise DefenseDemoError("evidence_contract_failed")
        resource_id = resource.get("resource_id")
        resource_type = resource.get("resource_type")
        if (
            not isinstance(resource_id, str)
            or _RESOURCE_ID.fullmatch(resource_id) is None
            or resource_type not in {"CELL", "ENODEB"}
        ):
            raise DefenseDemoError("evidence_contract_failed")
        _expect(resource.get("technology"), "LTE")
        resources.append(
            {
                "resource_id": resource_id,
                "resource_type": str(resource_type),
                "technology": "LTE",
            }
        )
    if {item["resource_type"] for item in resources} != {"CELL", "ENODEB"}:
        raise DefenseDemoError("evidence_contract_failed")
    return resources


def _validate_action_preview(
    value: object, *, require_revision: bool
) -> dict[str, object]:
    action = _mapping(value)
    required_keys = {"action_hash", "action_type", "resources", "risk"}
    allowed_keys = required_keys | {"expected_revision"}
    if set(action) - allowed_keys or not required_keys.issubset(action):
        raise DefenseDemoError("evidence_contract_failed")
    if require_revision and "expected_revision" not in action:
        raise DefenseDemoError("evidence_contract_failed")
    action_hash = action.get("action_hash")
    if not isinstance(action_hash, str) or _SHA256.fullmatch(action_hash) is None:
        raise DefenseDemoError("evidence_contract_failed")
    _expect(action.get("action_type"), "LOCAL_SIMULATION")
    _expect(action.get("risk"), "LOW")
    if "expected_revision" in action:
        _expect(action.get("expected_revision"), 4)
    result: dict[str, object] = {
        "action_hash": action_hash,
        "action_type": "LOCAL_SIMULATION",
        "resources": _validate_resources(action.get("resources")),
        "risk": "LOW",
    }
    if "expected_revision" in action:
        result["expected_revision"] = 4
    return result


def _validate_preview(payload: Mapping[str, object]) -> dict[str, object]:
    _expect(payload.get("command"), "demo")
    result = _mapping(payload.get("result"))
    for key, expected in {
        "action_mode": "disabled",
        "candidate_count": 15,
        "state": "AWAITING_APPROVAL",
        "closed_loop": False,
        "outcome": "AWAITING_EXPLICIT_APPROVAL",
    }.items():
        _expect(result.get(key), expected)
    approval = _mapping(result.get("approval"))
    _expect(approval.get("incident_confirmed"), True)
    _expect(approval.get("action_approved"), False)
    return {
        "candidate_count": 15,
        "state": "AWAITING_APPROVAL",
        "selected_candidate": _validate_selected_candidate(
            result.get("selected_candidate")
        ),
        "action": _validate_action_preview(
            result.get("action_preview"), require_revision=True
        ),
    }


def _validate_terminal(
    payload: Mapping[str, object],
    *,
    expected_status: str,
    preview: Mapping[str, object],
    replayed: bool,
) -> dict[str, object]:
    _expect(payload.get("command"), "demo")
    result = _mapping(payload.get("result"))
    closed_loop = expected_status == "RESOLVED"
    expected_outcome = (
        "SIMULATED_AND_VERIFIED" if closed_loop else "SIMULATED_AND_REOPENED"
    )
    _expect(result.get("action_mode"), "simulate")
    _expect(result.get("candidate_count"), 15)
    selected = _validate_selected_candidate(result.get("selected_candidate"))
    if selected != preview.get("selected_candidate"):
        raise DefenseDemoError("evidence_contract_failed")
    action = _validate_action_preview(
        result.get("action_preview"), require_revision=not replayed
    )
    action_without_revision = {
        key: value for key, value in action.items() if key != "expected_revision"
    }
    preview_action = _mapping(preview.get("action"))
    preview_without_revision = {
        key: value
        for key, value in preview_action.items()
        if key != "expected_revision"
    }
    if action_without_revision != preview_without_revision:
        raise DefenseDemoError("evidence_contract_failed")
    if not replayed and action != preview_action:
        raise DefenseDemoError("evidence_contract_failed")
    _expect(result.get("state"), expected_status)
    _expect(result.get("closed_loop"), closed_loop)
    _expect(result.get("outcome"), expected_outcome)
    approval = _mapping(result.get("approval"))
    _expect(approval.get("incident_confirmed"), True)
    _expect(approval.get("action_approved"), True)
    _expect(
        approval.get("decision_state"),
        expected_status if replayed else "REMEDIATING",
    )
    return {"closed_loop": closed_loop, "state": expected_status}


def _validate_verification(
    payload: Mapping[str, object], *, expected_status: str, incident_id: str
) -> dict[str, object]:
    _expect(payload.get("command"), "demo-verify")
    result = _mapping(payload.get("result"))
    expected_verification = "PASSED" if expected_status == "RESOLVED" else "FAILED"
    exact = {
        "incident_id": incident_id,
        "status": expected_status,
        "expected_status": expected_status,
        "revision": 7,
        "rca_reports": 1,
        "recommendations": 1,
        "approvals": 2,
        "action_runs": 1,
        "verification_runs": 1,
        "audit_events": 8,
    }
    if set(result) != {*exact, "action", "verification"}:
        raise DefenseDemoError("evidence_contract_failed")
    for key, expected in exact.items():
        _expect(result.get(key), expected)
    action = _mapping(result.get("action"))
    for key, expected in {
        "action_type": "LOCAL_SIMULATION",
        "status": "SUCCEEDED",
        "side_effects": False,
    }.items():
        _expect(action.get(key), expected)
    _expect(_mapping(result.get("verification")).get("status"), expected_verification)
    return {
        **exact,
        "action": {
            "action_type": "LOCAL_SIMULATION",
            "side_effects": False,
            "status": "SUCCEEDED",
        },
        "verification": {"status": expected_verification},
    }


def _run_branch(
    name: str,
    *,
    workspace: Path,
    repository_root: Path,
    environment: dict[str, str],
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> tuple[dict[str, object], dict[str, int]]:
    initialized = _call_stack(
        process_runner,
        ("--workspace", str(workspace), "init"),
        repository_root=repository_root,
        environment=environment,
    )
    dataset = _validate_init(initialized)
    status_payload = _call_stack(
        process_runner,
        ("--workspace", str(workspace), "status"),
        repository_root=repository_root,
        environment=environment,
    )
    status = _validate_status(status_payload)
    preview_payload = _call_stack(
        process_runner,
        ("--workspace", str(workspace), "demo", "--confirm-incident"),
        repository_root=repository_root,
        environment=environment,
    )
    preview = _validate_preview(preview_payload)
    action = _mapping(preview.get("action"))
    expected_status = "RESOLVED" if name == "success" else "REOPENED"
    approval = (
        "--workspace",
        str(workspace),
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "approved fixed isolated local simulation",
        "--expected-action-hash",
        str(action["action_hash"]),
        "--expected-revision",
        str(action["expected_revision"]),
        *(("--verification-outcome", "failed") if name == "failure" else ()),
    )
    terminal_payload = _call_stack(
        process_runner,
        approval,
        repository_root=repository_root,
        environment=environment,
    )
    terminal = _validate_terminal(
        terminal_payload,
        expected_status=expected_status,
        preview=preview,
        replayed=False,
    )
    selected = _mapping(preview.get("selected_candidate"))
    incident_id = str(selected["incident_id"])
    verify_command = (
        "--workspace",
        str(workspace),
        "demo-verify",
        "--expected-status",
        expected_status,
    )
    verification_payload = _call_stack(
        process_runner,
        verify_command,
        repository_root=repository_root,
        environment=environment,
    )
    verification = _validate_verification(
        verification_payload,
        expected_status=expected_status,
        incident_id=incident_id,
    )

    retry_payload = _call_stack(
        process_runner,
        approval,
        repository_root=repository_root,
        environment=environment,
    )
    retry_terminal = _validate_terminal(
        retry_payload,
        expected_status=expected_status,
        preview=preview,
        replayed=True,
    )
    retry_verification_payload = _call_stack(
        process_runner,
        verify_command,
        repository_root=repository_root,
        environment=environment,
    )
    retry_verification = _validate_verification(
        retry_verification_payload,
        expected_status=expected_status,
        incident_id=incident_id,
    )
    if (
        retry_terminal != terminal
        or verification != retry_verification
        or _mapping(verification_payload.get("result"))
        != _mapping(retry_verification_payload.get("result"))
    ):
        raise DefenseDemoError("evidence_contract_failed")
    return (
        {
            "preview": preview,
            "status": status,
            "terminal": {
                **terminal,
                "verification": verification["verification"]["status"],
            },
            "verification": verification,
            "exact_retry": {
                "approval_command_reused": True,
                "terminal_unchanged": True,
                "verification_unchanged": True,
            },
        },
        dataset,
    )


def _read_lifecycle_projection(
    branch: str,
    *,
    workspace: Path,
    repository_root: Path,
    environment: dict[str, str],
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> dict[str, object]:
    expected_status = "RESOLVED" if branch == "success" else "REOPENED"
    payload = _call_stack(
        process_runner,
        (
            "--workspace",
            str(workspace),
            "demo-events",
            "--expected-status",
            expected_status,
        ),
        repository_root=repository_root,
        environment=environment,
    )
    _expect(payload.get("command"), "demo-events")
    _expect(payload.get("action_mode"), "disabled")
    return dict(_mapping(payload.get("result")))


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def _create_run_directory(
    repository_root: Path,
    *,
    utc_now: Callable[[], datetime],
    random_token: Callable[[], str],
) -> Path:
    local_root = repository_root / ".local"
    defense_root = local_root / "networkagent-defense"
    try:
        for candidate in (local_root, defense_root):
            if candidate.exists() and _is_link_like(candidate):
                raise DefenseDemoError("report_write_failed")
        defense_root.mkdir(parents=True, exist_ok=True)
        if _is_link_like(defense_root):
            raise DefenseDemoError("report_write_failed")
        moment = utc_now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        token = random_token()
        if re.fullmatch(r"[0-9a-f]{12}", token) is None:
            raise DefenseDemoError("report_write_failed")
        run_directory = defense_root / f"{moment}-{token}"
        run_directory.mkdir(parents=False, exist_ok=False)
        return run_directory
    except DefenseDemoError:
        raise
    except Exception:
        raise DefenseDemoError("report_write_failed") from None


def _write_report(run_directory: Path, report: Mapping[str, object]) -> tuple[str, str]:
    final_path = run_directory / REPORT_NAME
    temporary = run_directory / f".{REPORT_NAME}.{secrets.token_hex(6)}.tmp"
    try:
        encoded = (
            json.dumps(
                report,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_STDOUT_BYTES:
            raise DefenseDemoError("report_write_failed")
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, final_path)
        return final_path.name, hashlib.sha256(encoded).hexdigest()
    except DefenseDemoError:
        raise
    except Exception:
        raise DefenseDemoError("report_write_failed") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _execute_demo(
    *,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    repository_root: Path,
    utc_now: Callable[[], datetime],
    random_token: Callable[[], str],
    lifecycle_projection_hook: (
        Callable[[str, Mapping[str, object]], None] | None
    ) = None,
) -> dict[str, object]:
    environment = _safe_environment()
    source_before = _read_source_binding(
        process_runner,
        repository_root=repository_root,
        environment=environment,
    )
    run_directory = _create_run_directory(
        repository_root, utc_now=utc_now, random_token=random_token
    )
    workspaces = {
        "success": run_directory / "success",
        "failure": run_directory / "failure",
    }
    results: dict[str, object] = {}
    datasets: list[dict[str, int]] = []
    cleanup: dict[str, dict[str, bool]] = {}
    operation_error: DefenseDemoError | None = None
    try:
        doctor = _call_stack(
            process_runner,
            ("--workspace", str(workspaces["success"]), "doctor"),
            repository_root=repository_root,
            environment=environment,
        )
        _validate_doctor(doctor)
        for name, workspace in workspaces.items():
            result, dataset = _run_branch(
                name,
                workspace=workspace,
                repository_root=repository_root,
                environment=environment,
                process_runner=process_runner,
            )
            results[name] = result
            datasets.append(dataset)
        if lifecycle_projection_hook is not None:
            projection_error: DefenseDemoError | None = None
            for name, workspace in workspaces.items():
                try:
                    projection = _read_lifecycle_projection(
                        name,
                        workspace=workspace,
                        repository_root=repository_root,
                        environment=environment,
                        process_runner=process_runner,
                    )
                    lifecycle_projection_hook(name, projection)
                except DefenseDemoError as exc:
                    if projection_error is None:
                        projection_error = exc
                except Exception:
                    if projection_error is None:
                        projection_error = DefenseDemoError("evidence_contract_failed")
            if projection_error is not None:
                raise projection_error
    except DefenseDemoError as exc:
        operation_error = exc
    except Exception:
        operation_error = DefenseDemoError("command_failed")
    finally:
        cleanup_failed = False
        for name, workspace in workspaces.items():
            if not os.path.lexists(workspace):
                cleanup[name] = {"workspace_removed": True}
                continue
            try:
                reset = _call_stack(
                    process_runner,
                    ("--workspace", str(workspace), "reset", "--yes"),
                    repository_root=repository_root,
                    environment=environment,
                )
                _expect(reset.get("command"), "reset")
                _expect(reset.get("workspace_removed"), True)
                cleanup[name] = {"workspace_removed": True}
            except Exception:
                cleanup[name] = {"workspace_removed": False}
                cleanup_failed = True
        if cleanup_failed:
            operation_error = DefenseDemoError("cleanup_failed")
    if operation_error is not None:
        raise operation_error
    if len(datasets) != 2 or datasets[0] != datasets[1]:
        raise DefenseDemoError("evidence_contract_failed")

    source_after = _read_source_binding(
        process_runner,
        repository_root=repository_root,
        environment=environment,
    )
    source = _combine_source_bindings(source_before, source_after)
    report: dict[str, object] = {
        "classification": (
            "LOCAL_NATIVE_SIMULATION_EVIDENCE"
            if source["commit_bound"] is True
            else "LOCAL_WORKTREE_SIMULATION_EVIDENCE"
        ),
        "cleanup": cleanup,
        "coverage": {
            "not_claimed": [
                "CLOUD_EXECUTION",
                "CONTAINER_EXECUTION",
                "FULL_G2_SECURITY_CLOSURE",
                "G4_CLOUD_REHEARSAL",
                "G5_FINAL_ACCEPTANCE",
                "REAL_NETWORK_REMEDIATION",
                "REJECTION_OR_EXPIRY_BRANCHES",
            ]
        },
        "dataset": datasets[0],
        "ok": True,
        "results": results,
        "schema": SCHEMA,
        "source": source,
    }
    _, digest = _write_report(run_directory, report)
    relative_path = (
        (run_directory / REPORT_NAME).relative_to(repository_root).as_posix()
    )
    return {
        **report,
        "report": {"relative_path": relative_path, "sha256": digest},
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    process_runner: Callable[
        ..., subprocess.CompletedProcess[bytes]
    ] = _default_process_runner,
    repository_root: Path = REPOSITORY_ROOT,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    random_token: Callable[[], str] = lambda: secrets.token_hex(6),
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if arguments.approve_local_simulation is not True:
            raise DefenseDemoError("confirmation_required")
        payload = _execute_demo(
            process_runner=process_runner,
            repository_root=repository_root.resolve(),
            utc_now=utc_now,
            random_token=random_token,
        )
        _write_json(output, payload)
        return 0
    except DefenseDemoError as exc:
        _write_json(
            errors,
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": _ERROR_MESSAGES.get(
                        exc.code, "local demo failed safely"
                    ),
                },
            },
        )
        return 2
    except Exception:
        _write_json(
            errors,
            {
                "ok": False,
                "error": {
                    "code": "command_failed",
                    "message": _ERROR_MESSAGES["command_failed"],
                },
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
