#!/usr/bin/env python3
"""Evaluate a fixed three-run Local acceptance SLO evidence window."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from io import StringIO
from pathlib import Path
from typing import TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "networkagent-local-slo-evidence/1.0"
CHILD_SCHEMA = "networkagent-local-observability/1.0"
REPORT_NAME = "local-slo-report.json"
CHILD_REPORT_NAME = "local-observability-report.json"
MAX_REPORT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 20
INJECTED_RUNNER_TIMEOUT_SECONDS = 240
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RUN_DIRECTORY = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_OBSERVATION_ID = re.compile(r"observation-[0-9a-f]{12}\Z")

_EXPECTED_GRAPH = [
    ("source_revision", "none", 1),
    ("source_cleanliness", "none", 1),
    ("preflight", "none", 1),
    ("workspace_init", "success", 1),
    ("workspace_status", "success", 1),
    ("governance_preview", "success", 1),
    ("approval_execute", "success", 1),
    ("terminal_verify", "success", 1),
    ("approval_execute", "success", 2),
    ("terminal_verify", "success", 2),
    ("workspace_init", "failure", 1),
    ("workspace_status", "failure", 1),
    ("governance_preview", "failure", 1),
    ("approval_execute", "failure", 1),
    ("terminal_verify", "failure", 1),
    ("approval_execute", "failure", 2),
    ("terminal_verify", "failure", 2),
    ("workspace_cleanup", "success", 1),
    ("workspace_cleanup", "failure", 1),
    ("source_revision", "none", 2),
    ("source_cleanliness", "none", 2),
    ("run_finalize", "none", 1),
]
_STAGES = tuple(dict.fromkeys(item[0] for item in _EXPECTED_GRAPH))
_BRANCHES = frozenset({"none", "success", "failure"})
_ERROR_CLASSES = frozenset(
    {"NONE", "INPUT", "EXECUTION", "CONTRACT", "CLEANUP", "ARTIFACT", "OBSERVATION"}
)
_EVENT_KEYS = {
    "attempt",
    "branch",
    "duration_ms",
    "error_class",
    "outcome",
    "sequence",
    "stage",
}
_RUN_KEYS = {
    "diagnostic_only",
    "duration_ms",
    "error_class",
    "error_code",
    "event_count",
    "finished_at",
    "observation_id",
    "started_at",
    "status",
}
_WINDOW_KEYS = {
    "duration_ms",
    "exact_retry_integrities",
    "expected_branch_outcomes",
    "local_alerts_ok",
    "observation_contract_valid",
    "sequence",
    "stage_command_successes",
    "workspace_cleanups",
}
_CHILD_BODY_KEYS = {
    "business_outcomes",
    "correlation",
    "coverage",
    "events",
    "local_alerts",
    "metrics",
    "ok",
    "privacy",
    "run",
    "schema",
    "source",
    "timing_snapshot",
}
_SLI_SPECS = (
    ("LOCAL_STAGE_COMMAND_SUCCESS", "stage_command_successes", 66),
    ("LOCAL_EXPECTED_BRANCH_OUTCOME", "expected_branch_outcomes", 6),
    ("LOCAL_EXACT_RETRY_INTEGRITY", "exact_retry_integrities", 6),
    ("LOCAL_WORKSPACE_CLEANUP", "workspace_cleanups", 6),
    ("LOCAL_OBSERVATION_CONTRACT_VALID", "observation_contract_valid", 3),
)
_SCOPE = {
    "execution_mode": "SEQUENTIAL",
    "isolated_run_directories": True,
    "latency_slo": False,
    "production_or_cloud_slo": False,
    "statistical_reliability_claim": False,
    "window_count": 3,
    "window_type": "FIXED_THREE_ISOLATED_RUN_ACCEPTANCE_WINDOW",
}
_PRIVACY = {
    "absolute_paths_recorded": False,
    "child_stderr_recorded": False,
    "child_stdout_recorded": False,
    "domain_identifiers_recorded": False,
    "environment_recorded": False,
    "high_cardinality_metric_labels": False,
    "observation_identifiers_recorded": False,
    "raw_arguments_recorded": False,
    "raw_events_recorded": False,
    "report_paths_recorded": False,
    "status": "PASS",
    "workspace_identifiers_recorded": False,
}
_DELIVERED = [
    "FIXED_THREE_RUN_LOCAL_ACCEPTANCE_WINDOW",
    "INTEGER_PPM_ACCEPTANCE_SLIS",
    "ZERO_ERROR_BUDGET_ACCEPTANCE_OBJECTIVE",
    "IN_REPORT_BREACH_EVALUATION",
]
_NOT_CLAIMED = [
    "TIME_BASED_AVAILABILITY_SLO",
    "LATENCY_SLO",
    "LONG_TERM_STATISTICAL_RELIABILITY",
    "RUNTIME_STRUCTURED_LOGGING",
    "OPEN_TELEMETRY_EXPORT",
    "COLLECTOR_OR_DISTRIBUTED_TRACE",
    "PROMETHEUS_RECORDING_RULES",
    "EXTERNAL_ALERT_DELIVERY",
    "MULTI_REPLICA_LOAD_OR_CAPACITY",
    "BACKUP_OR_RECOVERY",
    "GATE_E_OR_G5_CLOSURE",
    "CLOUD_OR_PRODUCTION_SLO",
]
_CHILD_DELIVERED = [
    "BOUNDED_LOCAL_STAGE_EVENTS",
    "LOCAL_TIMING_SNAPSHOT",
    "STABLE_LOCAL_ERROR_CLASSIFICATION",
    "IN_REPORT_LOCAL_ALERT_EVALUATION",
]
_CHILD_NOT_CLAIMED = [
    "OPEN_TELEMETRY_EXPORT",
    "CROSS_HTTP_REPLAY_A2A_MCP_TRACE",
    "PROMETHEUS_METRICS",
    "EXTERNAL_ALERT_DELIVERY",
    "SERVICE_LEVEL_OBJECTIVES",
    "COLLECTOR_FAILURE_TOLERANCE",
    "GATE_E_OR_G5_CLOSURE",
    "CLOUD_OR_PRODUCTION_OBSERVABILITY",
]
_EXPECTED_BRANCHES = {
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
}
_ALERTS = {
    "LOCAL_EXECUTION_FAILURE": (
        "local-observability-demo#execution-failure",
        "execution_error_count > 0",
    ),
    "LOCAL_CLEANUP_FAILURE": (
        "local-observability-demo#cleanup-failure",
        "cleanup_error_count > 0",
    ),
    "LOCAL_RETRY_AMPLIFICATION": (
        "local-observability-demo#retry-amplification",
        "exact_retry_proof != complete",
    ),
    "LOCAL_CONTRACT_DRIFT": (
        "local-observability-demo#contract-drift",
        "contract_or_observation_error_count > 0",
    ),
}
_CHILD_ERRORS = {
    "confirmation_required": (
        "INPUT",
        "explicit local simulation approval is required",
    ),
    "invalid_arguments": ("INPUT", "command arguments are invalid"),
    "command_failed": (
        "EXECUTION",
        "local observability demo command failed safely",
    ),
    "evidence_contract_failed": (
        "CONTRACT",
        "local observability demo detected contract drift",
    ),
    "cleanup_failed": ("CLEANUP", "local observability demo cleanup failed safely"),
    "report_write_failed": (
        "ARTIFACT",
        "local observability report could not be written safely",
    ),
    "observation_contract_failed": (
        "OBSERVATION",
        "local observability evidence violated its fixed contract",
    ),
}


def _load_observation_demo() -> object:
    module_path = Path(__file__).with_name("run_observability_demo.py")
    spec = importlib.util.spec_from_file_location(
        "networkagent_slo_observability_demo", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("observability demo is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observation_demo = _load_observation_demo()


class SloEvidenceError(Exception):
    """A stable SLO-evidence failure safe for the JSON boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_MESSAGES = {
    "confirmation_required": "explicit local simulation approval is required",
    "invalid_arguments": "command arguments are invalid",
    "window_execution_failed": "local acceptance window execution failed safely",
    "window_contract_failed": "local acceptance window evidence was not trustworthy",
    "source_binding_failed": "local acceptance windows did not share one stable source",
    "slo_breach": "local fixed-window acceptance SLO was breached",
    "report_write_failed": "local acceptance SLO report could not be written safely",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise SloEvidenceError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="networkagent-local-slo-evidence", add_help=False)
    parser.add_argument("--approve-local-simulation", action="store_true")
    return parser


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


def _matches_exact(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            _matches_exact(value[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _matches_exact(item, expected_item)
            for item, expected_item in zip(value, expected, strict=True)
        )
    return value == expected


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


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
    try:
        if type(body) is not bytes or len(body) > MAX_REPORT_BYTES:
            raise ValueError("invalid body")
        value = json.loads(
            body.decode("utf-8", errors="strict"),
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
        raise SloEvidenceError("window_contract_failed") from None


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError):
        raise SloEvidenceError("report_write_failed") from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise SloEvidenceError("report_write_failed")
    return encoded


def _safe_environment() -> dict[str, str]:
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
            "SOURCE_DATE_EPOCH": "946684800",
            "TZ": "UTC",
        }
    )
    return result


def _run_in_process_window(
    arguments: tuple[str, ...],
    *,
    repository_root: Path,
) -> subprocess.CompletedProcess[bytes]:
    stdout = StringIO()
    stderr = StringIO()
    try:
        main_function = getattr(observation_demo, "main")
        returncode = main_function(
            ["--approve-local-simulation"],
            stdout=stdout,
            stderr=stderr,
            repository_root=repository_root,
        )
        encoded_stdout = stdout.getvalue().encode("utf-8", errors="strict")
        encoded_stderr = stderr.getvalue().encode("utf-8", errors="strict")
    except Exception:
        raise SloEvidenceError("window_execution_failed") from None
    if (
        type(returncode) is not int
        or len(encoded_stdout) > MAX_REPORT_BYTES
        or len(encoded_stderr) > MAX_REPORT_BYTES
    ):
        raise SloEvidenceError("window_execution_failed")
    return subprocess.CompletedProcess(
        arguments, returncode, encoded_stdout, encoded_stderr
    )


def _run_window_process(
    runner: Callable[..., subprocess.CompletedProcess[bytes]] | None,
    *,
    repository_root: Path,
) -> subprocess.CompletedProcess[bytes]:
    arguments = (
        sys.executable,
        str(repository_root / "tools" / "local-stack" / "run_observability_demo.py"),
        "--approve-local-simulation",
    )
    try:
        if runner is None:
            completed = _run_in_process_window(
                arguments, repository_root=repository_root
            )
        else:
            completed = runner(
                arguments,
                cwd=repository_root,
                env=_safe_environment(),
                timeout=INJECTED_RUNNER_TIMEOUT_SECONDS,
            )
    except SloEvidenceError:
        raise
    except Exception:
        raise SloEvidenceError("window_execution_failed") from None
    if (
        completed.args != arguments
        or type(completed.returncode) is not int
        or type(completed.stdout) is not bytes
        or type(completed.stderr) is not bytes
    ):
        raise SloEvidenceError("window_execution_failed")
    return completed


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


PathIdentity = tuple[int, int]
DirectoryChain = tuple[PathIdentity, PathIdentity, PathIdentity]


def _directory_identity(path: Path) -> PathIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise SloEvidenceError("window_contract_failed") from None
    if _is_link_like(path) or not stat.S_ISDIR(details.st_mode):
        raise SloEvidenceError("window_contract_failed")
    return details.st_dev, details.st_ino


def _regular_file_identity(path: Path) -> PathIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise SloEvidenceError("window_contract_failed") from None
    if _is_link_like(path) or not stat.S_ISREG(details.st_mode):
        raise SloEvidenceError("window_contract_failed")
    return details.st_dev, details.st_ino


def _child_report_reference(envelope: Mapping[str, object]) -> Mapping[str, object]:
    report = _mapping(envelope.get("report"))
    if report is None or set(report) != {"relative_path", "sha256"}:
        raise SloEvidenceError("window_contract_failed")
    relative_path = report.get("relative_path")
    digest = report.get("sha256")
    if (
        not isinstance(relative_path, str)
        or "\\" in relative_path
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
    ):
        raise SloEvidenceError("window_contract_failed")
    return report


def _read_child_report(
    repository_root: Path,
    reference: Mapping[str, object],
) -> tuple[dict[str, object], Path, DirectoryChain]:
    relative_value = reference["relative_path"]
    digest = reference["sha256"]
    assert isinstance(relative_value, str) and isinstance(digest, str)
    relative = Path(relative_value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) != 4
        or relative.parts[0:2] != (".local", "networkagent-defense")
        or _RUN_DIRECTORY.fullmatch(relative.parts[2]) is None
        or relative.parts[3] != CHILD_REPORT_NAME
    ):
        raise SloEvidenceError("window_contract_failed")
    local_root = repository_root / ".local"
    defense_root = local_root / "networkagent-defense"
    run_directory = defense_root / relative.parts[2]
    report_path = run_directory / CHILD_REPORT_NAME
    chain_before: DirectoryChain = tuple(
        _directory_identity(item) for item in (local_root, defense_root, run_directory)
    )  # type: ignore[assignment]
    file_before = _regular_file_identity(report_path)
    try:
        with report_path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != file_before
            ):
                raise SloEvidenceError("window_contract_failed")
            encoded = stream.read(MAX_REPORT_BYTES + 1)
    except SloEvidenceError:
        raise
    except OSError:
        raise SloEvidenceError("window_contract_failed") from None
    chain_after = tuple(
        _directory_identity(item) for item in (local_root, defense_root, run_directory)
    )
    if (
        chain_after != chain_before
        or _regular_file_identity(report_path) != file_before
        or len(encoded) > MAX_REPORT_BYTES
        or hashlib.sha256(encoded).hexdigest() != digest
    ):
        raise SloEvidenceError("window_contract_failed")
    body = _decode_json_document(encoded)
    try:
        canonical = _canonical_bytes(body)
    except SloEvidenceError:
        raise SloEvidenceError("window_contract_failed") from None
    if canonical != encoded:
        raise SloEvidenceError("window_contract_failed")
    return body, run_directory, chain_before


def _read_completed_window(
    completed: subprocess.CompletedProcess[bytes],
    *,
    repository_root: Path,
) -> tuple[dict[str, object], Path, DirectoryChain, bool]:
    if completed.returncode == 0:
        if completed.stderr != b"":
            raise SloEvidenceError("window_contract_failed")
        summary = _decode_json_document(completed.stdout)
        reference = _child_report_reference(summary)
        body, run_directory, chain = _read_child_report(repository_root, reference)
        public_body = {key: value for key, value in summary.items() if key != "report"}
        if not _matches_exact(public_body, body):
            raise SloEvidenceError("window_contract_failed")
        return body, run_directory, chain, True
    if completed.returncode != 2 or completed.stdout != b"" or completed.stderr == b"":
        raise SloEvidenceError("window_execution_failed")
    envelope = _decode_json_document(completed.stderr)
    if set(envelope) != {"error", "ok", "report", "schema"}:
        raise SloEvidenceError("window_contract_failed")
    error = _mapping(envelope.get("error"))
    if (
        error is None
        or set(error) != {"class", "code", "message"}
        or not all(isinstance(error[key], str) for key in error)
        or envelope.get("ok") is not False
        or envelope.get("schema") != CHILD_SCHEMA
    ):
        raise SloEvidenceError("window_contract_failed")
    reference = _child_report_reference(envelope)
    body, run_directory, chain = _read_child_report(repository_root, reference)
    error_code = error["code"]
    expected_error = _CHILD_ERRORS.get(str(error_code))
    run = _mapping(body.get("run"))
    if (
        expected_error is None
        or not _matches_exact(
            dict(error),
            {
                "class": expected_error[0],
                "code": error_code,
                "message": expected_error[1],
            },
        )
        or set(body) != _CHILD_BODY_KEYS
        or body.get("schema") != CHILD_SCHEMA
        or body.get("ok") is not False
        or run is None
        or set(run) != _RUN_KEYS
        or run.get("status") != "FAIL"
        or run.get("error_code") != error_code
        or run.get("error_class") != expected_error[0]
    ):
        raise SloEvidenceError("window_contract_failed")
    return body, run_directory, chain, False


def _validate_source(value: object) -> dict[str, object]:
    source = _mapping(value)
    expected = {
        "binding_stable",
        "commit_bound",
        "commit_sha",
        "git_available",
        "tracked_clean",
    }
    if source is None or set(source) != expected:
        raise SloEvidenceError("source_binding_failed")
    for key in (
        "binding_stable",
        "commit_bound",
        "git_available",
        "tracked_clean",
    ):
        if type(source[key]) is not bool:
            raise SloEvidenceError("source_binding_failed")
    sha = source["commit_sha"]
    if not isinstance(sha, str) or _GIT_SHA.fullmatch(sha) is None:
        raise SloEvidenceError("source_binding_failed")
    if (
        source["binding_stable"] is not True
        or source["git_available"] is not True
        or source["commit_bound"] is not source["tracked_clean"]
    ):
        raise SloEvidenceError("source_binding_failed")
    return {key: source[key] for key in sorted(source)}


def _event_matches(
    value: object,
    *,
    expected_sequence: int,
    expected_graph: tuple[str, str, int],
) -> bool:
    event = _mapping(value)
    if event is None or set(event) != _EVENT_KEYS:
        return False
    stage, branch, attempt = expected_graph
    return bool(
        type(event["attempt"]) is int
        and event["attempt"] == attempt
        and event["branch"] == branch
        and type(event["duration_ms"]) is int
        and event["duration_ms"] >= 0
        and event["error_class"] == "NONE"
        and event["outcome"] == "SUCCEEDED"
        and type(event["sequence"]) is int
        and event["sequence"] == expected_sequence
        and event["stage"] == stage
    )


def _business_counts(value: object) -> tuple[int, int, int, bool]:
    business = _mapping(value)
    if business is None:
        return 0, 0, 0, False
    outcomes = sum(
        1
        for branch in ("success", "failure")
        if _matches_exact(business.get(branch), _EXPECTED_BRANCHES[branch])
    )
    retry = _mapping(business.get("exact_retry"))
    cleanup = _mapping(business.get("cleanup"))
    retry_count = sum(
        1 for branch in ("success", "failure") if retry and retry.get(branch) is True
    )
    cleanup_count = sum(
        1
        for branch in ("success", "failure")
        if cleanup and cleanup.get(branch) is True
    )
    contract_valid = bool(
        set(business) == {"cleanup", "exact_retry", "failure", "success"}
        and retry is not None
        and set(retry) == {"failure", "success"}
        and cleanup is not None
        and set(cleanup) == {"failure", "success"}
        and outcomes == 2
        and retry_count == 2
        and cleanup_count == 2
    )
    return outcomes, retry_count, cleanup_count, contract_valid


def _alert_count(value: object) -> tuple[int, bool]:
    if not isinstance(value, list):
        return 0, False
    records: dict[str, Mapping[str, object]] = {}
    duplicates = False
    for item in value:
        alert = _mapping(item)
        if alert is None or set(alert) != {
            "name",
            "owner",
            "runbook_anchor",
            "state",
            "threshold",
        }:
            continue
        name = alert.get("name")
        if not isinstance(name, str) or name not in _ALERTS or name in records:
            duplicates = True
            continue
        records[name] = alert
    count = 0
    for name, (anchor, threshold) in _ALERTS.items():
        alert = records.get(name)
        if alert is not None and _matches_exact(
            dict(alert),
            {
                "name": name,
                "owner": "networkagent-local-owner",
                "runbook_anchor": anchor,
                "state": "OK",
                "threshold": threshold,
            },
        ):
            count += 1
    return count, not duplicates and len(value) == 4 and count == 4


def _metrics_valid(value: object, events: object) -> bool:
    metrics = _mapping(value)
    if metrics is None or not isinstance(events, list) or not events:
        return False
    aggregates: dict[tuple[str, str, str, str], list[int]] = {}
    for item in events:
        event = _mapping(item)
        if event is None or set(event) != _EVENT_KEYS:
            return False
        if (
            type(event["duration_ms"]) is not int
            or event["duration_ms"] < 0
            or event["branch"] not in _BRANCHES
            or event["error_class"] not in _ERROR_CLASSES
            or event["outcome"] not in {"SUCCEEDED", "FAILED"}
            or event["stage"] not in _STAGES
        ):
            return False
        key = (
            str(event["branch"]),
            str(event["error_class"]),
            str(event["outcome"]),
            str(event["stage"]),
        )
        aggregate = aggregates.setdefault(key, [0, 0])
        aggregate[0] += 1
        aggregate[1] += int(event["duration_ms"])
    expected_series = [
        {
            "duration_ms": aggregates[key][1],
            "event_count": aggregates[key][0],
            "labels": {
                "branch": key[0],
                "error_class": key[1],
                "outcome": key[2],
                "stage": key[3],
            },
            "name": "networkagent_local_stage",
        }
        for key in sorted(aggregates)
    ]
    return _matches_exact(
        dict(metrics),
        {
            "high_cardinality_labels_present": False,
            "label_keys": ["branch", "error_class", "outcome", "stage"],
            "series": expected_series,
        },
    )


def _child_timing_valid(value: object, events: object, run: object) -> bool:
    timing = _mapping(value)
    run_map = _mapping(run)
    if timing is None or run_map is None or not isinstance(events, list):
        return False
    wall_duration = run_map.get("duration_ms")
    if type(wall_duration) is not int or wall_duration < 0:
        return False
    by_branch = {
        branch: {"duration_ms": 0, "event_count": 0}
        for branch in ("none", "success", "failure")
    }
    by_stage = {stage: {"duration_ms": 0, "event_count": 0} for stage in _STAGES}
    total = 0
    for item in events:
        event = _mapping(item)
        if (
            event is None
            or event.get("branch") not in _BRANCHES
            or event.get("stage") not in _STAGES
            or type(event.get("duration_ms")) is not int
            or event["duration_ms"] < 0
        ):
            return False
        duration = int(event["duration_ms"])
        branch_record = by_branch[str(event["branch"])]
        stage_record = by_stage[str(event["stage"])]
        branch_record["duration_ms"] += duration
        branch_record["event_count"] += 1
        stage_record["duration_ms"] += duration
        stage_record["event_count"] += 1
        total += duration
    return _matches_exact(
        dict(timing),
        {
            "by_branch": by_branch,
            "by_stage": by_stage,
            "diagnostic_only": True,
            "instrumented_duration_ms": total,
            "sample_count": 1,
            "wall_duration_ms": wall_duration,
        },
    )


def _normalize_observation(
    value: object,
    *,
    duration_ms: int,
    child_succeeded: bool = True,
) -> dict[str, object]:
    if (
        type(duration_ms) is not int
        or duration_ms < 0
        or type(child_succeeded) is not bool
    ):
        raise SloEvidenceError("window_contract_failed")
    body = _mapping(value)
    if body is None:
        raise SloEvidenceError("window_contract_failed")
    source = _validate_source(body.get("source"))
    events = body.get("events")
    event_list = events if isinstance(events, list) else []
    stage_successes = sum(
        1
        for index, expected in enumerate(_EXPECTED_GRAPH, start=1)
        if index <= len(event_list)
        and _event_matches(
            event_list[index - 1],
            expected_sequence=index,
            expected_graph=expected,
        )
    )
    outcomes, retries, cleanups, business_valid = _business_counts(
        body.get("business_outcomes")
    )
    alerts_ok, alerts_valid = _alert_count(body.get("local_alerts"))

    run = _mapping(body.get("run"))
    correlation = _mapping(body.get("correlation"))
    observation_id = run.get("observation_id") if run is not None else None
    run_valid = bool(
        run is not None
        and set(run) == _RUN_KEYS
        and run["diagnostic_only"] is True
        and type(run["duration_ms"]) is int
        and run["duration_ms"] >= 0
        and run["error_class"] == "NONE"
        and run["error_code"] is None
        and type(run["event_count"]) is int
        and run["event_count"] == 22
        and isinstance(run["started_at"], str)
        and _UTC_TIMESTAMP.fullmatch(run["started_at"]) is not None
        and isinstance(run["finished_at"], str)
        and _UTC_TIMESTAMP.fullmatch(run["finished_at"]) is not None
        and isinstance(observation_id, str)
        and _OBSERVATION_ID.fullmatch(observation_id) is not None
        and run["status"] == "PASS"
    )
    correlation_valid = bool(
        correlation is not None
        and set(correlation)
        == {
            "defense_report_sha256",
            "observation_id",
            "propagated_trace",
            "source_commit",
        }
        and isinstance(correlation["defense_report_sha256"], str)
        and _SHA256.fullmatch(correlation["defense_report_sha256"]) is not None
        and correlation["observation_id"] == observation_id
        and correlation["propagated_trace"] is False
        and correlation["source_commit"] == source["commit_sha"]
    )
    privacy_valid = _matches_exact(
        body.get("privacy"),
        {
            "absolute_paths_recorded": False,
            "child_stderr_recorded": False,
            "child_stdout_recorded": False,
            "environment_recorded": False,
            "high_cardinality_metric_labels": False,
            "raw_arguments_recorded": False,
            "status": "PASS",
        },
    )
    coverage_valid = _matches_exact(
        body.get("coverage"),
        {"delivered": _CHILD_DELIVERED, "not_claimed": _CHILD_NOT_CLAIMED},
    )
    event_contract_valid = len(event_list) == 22 and stage_successes == 22
    observation_valid = bool(
        child_succeeded
        and set(body) == _CHILD_BODY_KEYS
        and body.get("schema") == CHILD_SCHEMA
        and body.get("ok") is True
        and event_contract_valid
        and business_valid
        and alerts_valid
        and _metrics_valid(body.get("metrics"), events)
        and privacy_valid
        and coverage_valid
        and run_valid
        and correlation_valid
        and _child_timing_valid(body.get("timing_snapshot"), events, run)
    )
    return {
        "duration_ms": duration_ms,
        "exact_retry_integrities": retries,
        "expected_branch_outcomes": outcomes,
        "local_alerts_ok": alerts_ok,
        "observation_contract_valid": observation_valid,
        "sequence": 0,
        "stage_command_successes": stage_successes,
        "workspace_cleanups": cleanups,
    }


def _validated_windows(
    windows: object,
) -> list[Mapping[str, object]]:
    if not isinstance(windows, list) or len(windows) != 3:
        raise SloEvidenceError("window_contract_failed")
    validated: list[Mapping[str, object]] = []
    bounds = {
        "exact_retry_integrities": 2,
        "expected_branch_outcomes": 2,
        "local_alerts_ok": 4,
        "stage_command_successes": 22,
        "workspace_cleanups": 2,
    }
    for expected_sequence, value in enumerate(windows, start=1):
        window = _mapping(value)
        if window is None or set(window) != _WINDOW_KEYS:
            raise SloEvidenceError("window_contract_failed")
        if (
            type(window["sequence"]) is not int
            or window["sequence"] != expected_sequence
            or type(window["duration_ms"]) is not int
            or window["duration_ms"] < 0
            or type(window["observation_contract_valid"]) is not bool
        ):
            raise SloEvidenceError("window_contract_failed")
        for key, maximum in bounds.items():
            if type(window[key]) is not int or window[key] < 0 or window[key] > maximum:
                raise SloEvidenceError("window_contract_failed")
        validated.append(window)
    return validated


def _evaluate_windows(
    windows: object,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    validated = _validated_windows(windows)
    slis: dict[str, dict[str, object]] = {}
    breached: list[str] = []
    for name, field, denominator in _SLI_SPECS:
        if field == "observation_contract_valid":
            numerator = sum(1 for window in validated if window[field] is True)
        else:
            numerator = sum(int(window[field]) for window in validated)
        observed = numerator * 1_000_000 // denominator
        state = "OK" if observed >= 1_000_000 else "BREACH"
        slis[name] = {
            "denominator": denominator,
            "error_budget_ppm": 0,
            "numerator": numerator,
            "objective_ppm": 1_000_000,
            "observed_ppm": observed,
            "state": state,
        }
        if state == "BREACH":
            breached.append(name)
    evaluation = {
        "breached_slis": breached,
        "external_delivery": False,
        "name": "LOCAL_DEMO_ACCEPTANCE_SLO_BREACH",
        "owner": "networkagent-local-owner",
        "runbook_anchor": "local-slo-evidence#acceptance-slo-breach",
        "state": "BREACH" if breached else "OK",
        "threshold": "any_sli_observed_ppm < objective_ppm",
    }
    return slis, evaluation


def _timing_snapshot(windows: object) -> dict[str, object]:
    validated = _validated_windows(windows)
    durations = sorted(int(window["duration_ms"]) for window in validated)
    return {
        "diagnostic_only": True,
        "max_duration_ms": durations[-1],
        "median_duration_ms": durations[1],
        "min_duration_ms": durations[0],
        "sample_count": 3,
    }


def _aggregate_source(sources: object) -> dict[str, object]:
    if not isinstance(sources, list) or len(sources) != 3:
        raise SloEvidenceError("source_binding_failed")
    validated = [_validate_source(source) for source in sources]
    if any(not _matches_exact(source, validated[0]) for source in validated[1:]):
        raise SloEvidenceError("source_binding_failed")
    return validated[0]


def _collect_windows(
    runner: Callable[..., subprocess.CompletedProcess[bytes]] | None,
    *,
    repository_root: Path,
    monotonic_ns: Callable[[], int],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    Path,
    DirectoryChain,
]:
    windows: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    paths: set[Path] = set()
    identities: set[PathIdentity] = set()
    last_run: Path | None = None
    last_chain: DirectoryChain | None = None
    for sequence in range(1, 4):
        try:
            start = monotonic_ns()
            if type(start) is not int:
                raise ValueError
            completed = _run_window_process(runner, repository_root=repository_root)
            finish = monotonic_ns()
            if type(finish) is not int or finish < start:
                raise ValueError
        except SloEvidenceError:
            raise
        except Exception:
            raise SloEvidenceError("window_contract_failed") from None
        body, run_directory, chain, child_succeeded = _read_completed_window(
            completed, repository_root=repository_root
        )
        identity = chain[-1]
        if run_directory in paths or identity in identities:
            raise SloEvidenceError("window_contract_failed")
        paths.add(run_directory)
        identities.add(identity)
        window = _normalize_observation(
            body,
            duration_ms=(finish - start) // 1_000_000,
            child_succeeded=child_succeeded,
        )
        window["sequence"] = sequence
        if window["workspace_cleanups"] == 2 and any(
            os.path.lexists(run_directory / branch) for branch in ("success", "failure")
        ):
            raise SloEvidenceError("window_contract_failed")
        windows.append(window)
        sources.append(_validate_source(body.get("source")))
        last_run = run_directory
        last_chain = chain
    if last_run is None or last_chain is None:
        raise SloEvidenceError("window_contract_failed")
    return windows, sources, last_run, last_chain


def _write_report(
    repository_root: Path,
    run_directory: Path,
    report: Mapping[str, object],
    *,
    expected_chain: DirectoryChain,
    token: str,
) -> tuple[int, str]:
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise SloEvidenceError("report_write_failed")
    local_root = repository_root / ".local"
    defense_root = local_root / "networkagent-defense"
    if run_directory.parent != defense_root:
        raise SloEvidenceError("report_write_failed")
    final_path = run_directory / REPORT_NAME
    temporary = run_directory / f".{REPORT_NAME}.{token}.tmp"
    try:
        chain_before: DirectoryChain = tuple(
            _directory_identity(item)
            for item in (local_root, defense_root, run_directory)
        )  # type: ignore[assignment]
        if chain_before != expected_chain:
            raise SloEvidenceError("report_write_failed")
        if os.path.lexists(final_path) or os.path.lexists(temporary):
            raise SloEvidenceError("report_write_failed")
        encoded = _canonical_bytes(report)
        with temporary.open("xb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise SloEvidenceError("report_write_failed")
            identity = (opened.st_dev, opened.st_ino)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
            if (written.st_dev, written.st_ino) != identity or written.st_size != len(
                encoded
            ):
                raise SloEvidenceError("report_write_failed")
        if (
            tuple(
                _directory_identity(item)
                for item in (local_root, defense_root, run_directory)
            )
            != chain_before
            or _regular_file_identity(temporary) != identity
        ):
            raise SloEvidenceError("report_write_failed")
        os.link(temporary, final_path, follow_symlinks=False)
        if (
            _regular_file_identity(temporary) != identity
            or _regular_file_identity(final_path) != identity
            or not os.path.samefile(temporary, final_path)
        ):
            raise SloEvidenceError("report_write_failed")
        with final_path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != identity:
                raise SloEvidenceError("report_write_failed")
            persisted = stream.read(MAX_REPORT_BYTES + 1)
        if (
            persisted != encoded
            or _regular_file_identity(final_path) != identity
            or tuple(
                _directory_identity(item)
                for item in (local_root, defense_root, run_directory)
            )
            != chain_before
        ):
            raise SloEvidenceError("report_write_failed")
        temporary.unlink()
        if _regular_file_identity(final_path) != identity:
            raise SloEvidenceError("report_write_failed")
        return len(persisted), hashlib.sha256(persisted).hexdigest()
    except SloEvidenceError as exc:
        if exc.code == "window_contract_failed":
            raise SloEvidenceError("report_write_failed") from None
        raise
    except Exception:
        raise SloEvidenceError("report_write_failed") from None
    finally:
        try:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _error_payload(
    code: str,
    *,
    report: Mapping[str, object] | None = None,
) -> dict[str, object]:
    stable = code if code in _ERROR_MESSAGES else "window_execution_failed"
    payload: dict[str, object] = {
        "error": {"code": stable, "message": _ERROR_MESSAGES[stable]},
        "ok": False,
        "schema": SCHEMA,
    }
    if report is not None:
        payload["report"] = dict(report)
    return payload


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    repository_root: Path = REPOSITORY_ROOT,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    random_token: Callable[[], str] = lambda: secrets.token_hex(6),
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if arguments.approve_local_simulation is not True:
            raise SloEvidenceError("confirmation_required")
    except SloEvidenceError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2

    repository = repository_root.resolve()
    try:
        windows, sources, run_directory, run_chain = _collect_windows(
            process_runner,
            repository_root=repository,
            monotonic_ns=monotonic_ns,
        )
        source = _aggregate_source(sources)
        slis, evaluation = _evaluate_windows(windows)
        report_body: dict[str, object] = {
            "classification": (
                "LOCAL_DEMO_ACCEPTANCE_SLO_EVIDENCE"
                if source["commit_bound"] is True
                else "LOCAL_WORKTREE_DEMO_ACCEPTANCE_SLO_EVIDENCE"
            ),
            "coverage": {
                "delivered": list(_DELIVERED),
                "not_claimed": list(_NOT_CLAIMED),
            },
            "evaluation": evaluation,
            "ok": evaluation["state"] == "OK",
            "privacy": dict(_PRIVACY),
            "schema": SCHEMA,
            "scope": dict(_SCOPE),
            "slis": slis,
            "source": source,
            "timing_snapshot": _timing_snapshot(windows),
            "windows": windows,
        }
        try:
            token = random_token()
        except Exception:
            raise SloEvidenceError("report_write_failed") from None
        report_bytes, digest = _write_report(
            repository,
            run_directory,
            report_body,
            expected_chain=run_chain,
            token=token,
        )
        report = {
            "bytes": report_bytes,
            "filename": REPORT_NAME,
            "sha256": digest,
        }
        if evaluation["state"] == "BREACH":
            _write_json(errors, _error_payload("slo_breach", report=report))
            return 2
        _write_json(output, {**report_body, "report": report})
        return 0
    except SloEvidenceError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2
    except Exception:
        _write_json(errors, _error_payload("window_execution_failed"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
