#!/usr/bin/env python3
"""Observe the fixed offline defense demo without changing its behavior."""

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
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "networkagent-local-observability/1.0"
REPORT_NAME = "local-observability-report.json"
MAX_REPORT_BYTES = 64 * 1024
MAX_EVENTS = 24
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")

_STAGES = (
    "source_revision",
    "source_cleanliness",
    "preflight",
    "workspace_init",
    "workspace_status",
    "governance_preview",
    "approval_execute",
    "terminal_verify",
    "workspace_cleanup",
    "run_finalize",
)
_CHILD_STAGES = frozenset(_STAGES[:-1])
_BRANCHES = ("none", "success", "failure")
_OUTCOMES = frozenset({"SUCCEEDED", "FAILED"})
_ERROR_CLASSES = frozenset(
    {
        "NONE",
        "INPUT",
        "EXECUTION",
        "CONTRACT",
        "CLEANUP",
        "ARTIFACT",
        "OBSERVATION",
    }
)
_METRIC_LABEL_KEYS = (
    "branch",
    "error_class",
    "outcome",
    "stage",
)
_NOT_CLAIMED = [
    "OPEN_TELEMETRY_EXPORT",
    "CROSS_HTTP_REPLAY_A2A_MCP_TRACE",
    "PROMETHEUS_METRICS",
    "EXTERNAL_ALERT_DELIVERY",
    "SERVICE_LEVEL_OBJECTIVES",
    "COLLECTOR_FAILURE_TOLERANCE",
    "GATE_E_OR_G5_CLOSURE",
    "CLOUD_OR_PRODUCTION_OBSERVABILITY",
]
_EXPECTED_CHILD_GRAPH = [
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
]


def _load_defense_demo() -> Any:
    module_path = Path(__file__).with_name("run_defense_demo.py")
    spec = importlib.util.spec_from_file_location(
        "networkagent_observed_defense_demo", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("defense demo is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defense_demo = _load_defense_demo()


class ObservationError(Exception):
    """A stable observability failure safe for the public JSON boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_CLASS_BY_CODE = {
    "confirmation_required": "INPUT",
    "invalid_arguments": "INPUT",
    "command_failed": "EXECUTION",
    "evidence_contract_failed": "CONTRACT",
    "cleanup_failed": "CLEANUP",
    "report_write_failed": "ARTIFACT",
    "observation_contract_failed": "OBSERVATION",
}
_ERROR_MESSAGES = {
    "confirmation_required": "explicit local simulation approval is required",
    "invalid_arguments": "command arguments are invalid",
    "command_failed": "local observability demo command failed safely",
    "evidence_contract_failed": ("local observability demo detected contract drift"),
    "cleanup_failed": "local observability demo cleanup failed safely",
    "report_write_failed": ("local observability report could not be written safely"),
    "observation_contract_failed": (
        "local observability evidence violated its fixed contract"
    ),
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ObservationError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-observability", add_help=False
    )
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


def _utc_value(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise ObservationError("observation_contract_failed") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ObservationError("observation_contract_failed")
    try:
        if value.utcoffset() is None:
            raise ObservationError("observation_contract_failed")
        return value.astimezone(UTC)
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("observation_contract_failed") from None


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


PathIdentity = tuple[int, int]


def _directory_identity(path: Path) -> PathIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise ObservationError("report_write_failed") from None
    if _is_link_like(path) or not stat.S_ISDIR(details.st_mode):
        raise ObservationError("report_write_failed")
    return details.st_dev, details.st_ino


def _directory_chain_identity(
    repository_root: Path, run_directory: Path
) -> tuple[PathIdentity, PathIdentity, PathIdentity]:
    local_root = repository_root / ".local"
    defense_root = local_root / "networkagent-defense"
    if run_directory.parent != defense_root:
        raise ObservationError("report_write_failed")
    return tuple(
        _directory_identity(item) for item in (local_root, defense_root, run_directory)
    )  # type: ignore[return-value]


def _regular_file_identity(details: os.stat_result) -> PathIdentity:
    if not stat.S_ISREG(details.st_mode):
        raise ObservationError("report_write_failed")
    return details.st_dev, details.st_ino


def _regular_path_identity(path: Path) -> PathIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise ObservationError("report_write_failed") from None
    if _is_link_like(path):
        raise ObservationError("report_write_failed")
    return _regular_file_identity(details)


class _DefenseRunCapture:
    """Capture the fixed defense run identity, never user input."""

    def __init__(
        self,
        repository_root: Path,
        *,
        utc_now: Callable[[], datetime],
        random_token: Callable[[], str],
    ) -> None:
        self.repository_root = repository_root
        self._utc_now = utc_now
        self._random_token = random_token
        self.moment: datetime | None = None
        self.token_value: str | None = None
        self.candidate: Path | None = None
        self.existed_before: bool | None = None

    def now(self) -> datetime:
        value = _utc_value(self._utc_now)
        if self.moment is None:
            self.moment = value
        return value

    def token(self) -> str:
        try:
            value = self._random_token()
        except Exception:
            raise ObservationError("observation_contract_failed") from None
        if not isinstance(value, str):
            raise ObservationError("observation_contract_failed")
        if self.token_value is None:
            self.token_value = value
            if self.moment is not None and _TOKEN.fullmatch(value) is not None:
                stamp = self.moment.strftime("%Y%m%dT%H%M%SZ")
                self.candidate = (
                    self.repository_root
                    / ".local"
                    / "networkagent-defense"
                    / f"{stamp}-{value}"
                )
                self.existed_before = os.path.lexists(self.candidate)
        return value

    @property
    def observation_id(self) -> str:
        if self.token_value is None or _TOKEN.fullmatch(self.token_value) is None:
            raise ObservationError("observation_contract_failed")
        return f"observation-{self.token_value}"

    def owned_run_directory(self) -> Path:
        candidate = self.candidate
        if candidate is None or self.existed_before is not False:
            raise ObservationError("report_write_failed")
        try:
            if not candidate.is_dir():
                raise ObservationError("report_write_failed")
            _directory_chain_identity(self.repository_root, candidate)
        except ObservationError:
            raise
        except OSError:
            raise ObservationError("report_write_failed") from None
        return candidate


def _classify_command(
    arguments: tuple[str, ...],
    *,
    repository_root: Path = REPOSITORY_ROOT,
    run_directory: Path | None = None,
) -> tuple[str, str] | None:
    """Map the fixed child graph without retaining any raw argument value."""

    if arguments == ("git", "rev-parse", "--verify", "HEAD"):
        return "source_revision", "none"
    if arguments == (
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        return "source_cleanliness", "none"
    if run_directory is None:
        return None
    script = str(repository_root / "tools" / "local-stack" / "local_stack.py")
    reason = "approved fixed isolated local simulation"
    for branch in ("success", "failure"):
        prefix = (
            sys.executable,
            script,
            "--workspace",
            str(run_directory / branch),
        )
        if branch == "success" and arguments == (*prefix, "doctor"):
            return "preflight", "none"
        if arguments == (*prefix, "init"):
            return "workspace_init", branch
        if arguments == (*prefix, "status"):
            return "workspace_status", branch
        if arguments == (*prefix, "demo", "--confirm-incident"):
            return "governance_preview", branch
        approval_before_hash = (
            *prefix,
            "--action-mode",
            "simulate",
            "demo",
            "--approve-action",
            "--reason",
            reason,
            "--expected-action-hash",
        )
        approval_after_hash = (
            "--expected-revision",
            "4",
            *(("--verification-outcome", "failed") if branch == "failure" else ()),
        )
        if (
            len(arguments) == len(approval_before_hash) + 1 + len(approval_after_hash)
            and arguments[: len(approval_before_hash)] == approval_before_hash
            and arguments[len(approval_before_hash) + 1 :] == approval_after_hash
        ):
            action_hash = arguments[len(approval_before_hash)]
            if _SHA256.fullmatch(action_hash) is not None:
                return "approval_execute", branch
        expected_status = "RESOLVED" if branch == "success" else "REOPENED"
        if arguments == (
            *prefix,
            "demo-verify",
            "--expected-status",
            expected_status,
        ):
            return "terminal_verify", branch
        if arguments == (*prefix, "reset", "--yes"):
            return "workspace_cleanup", branch
    return None


StageMapper = Callable[[tuple[str, ...]], tuple[str, str] | None]


class _RecordingProcessRunner:
    """Observe a delegate after every call and never interfere with cleanup."""

    def __init__(
        self,
        delegate: Callable[..., subprocess.CompletedProcess[bytes]],
        *,
        monotonic_ns: Callable[[], int],
        stage_mapper: StageMapper,
        event_limit: int,
    ) -> None:
        self.delegate = delegate
        self.monotonic_ns = monotonic_ns
        self.stage_mapper = stage_mapper
        self.event_limit = min(MAX_EVENTS, max(1, int(event_limit)))
        self.events: list[dict[str, object]] = []
        self.violations: set[str] = set()
        self._attempts: Counter[tuple[str, str]] = Counter()
        if event_limit != self.event_limit:
            self.violations.add("event_budget_invalid")

    def _tick(self) -> int | None:
        try:
            value = self.monotonic_ns()
        except Exception:
            self.violations.add("clock_failed")
            return None
        if type(value) is not int:
            self.violations.add("clock_invalid")
            return None
        return value

    def _duration(self, start: int | None, finish: int | None) -> int:
        if start is None or finish is None or finish < start:
            self.violations.add("clock_regression")
            return 0
        return (finish - start) // 1_000_000

    def _append_child(
        self,
        arguments: tuple[str, ...],
        *,
        duration_ms: int,
        outcome: str,
    ) -> None:
        try:
            descriptor = self.stage_mapper(arguments)
        except Exception:
            descriptor = None
        if (
            descriptor is None
            or not isinstance(descriptor, tuple)
            or len(descriptor) != 2
            or descriptor[0] not in _CHILD_STAGES
            or descriptor[1] not in _BRANCHES
        ):
            self.violations.add("unknown_command_graph")
            return
        stage, branch = descriptor
        self._attempts[(stage, branch)] += 1
        attempt = self._attempts[(stage, branch)]
        if len(self.events) >= self.event_limit - 1:
            self.violations.add("event_budget_exceeded")
            return
        self.events.append(
            {
                "attempt": attempt,
                "branch": branch,
                "duration_ms": duration_ms,
                "error_class": ("NONE" if outcome == "SUCCEEDED" else "EXECUTION"),
                "outcome": outcome,
                "sequence": len(self.events) + 1,
                "stage": stage,
            }
        )

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        start = self._tick()
        try:
            completed = self.delegate(
                arguments,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        except Exception:
            finish = self._tick()
            self._append_child(
                arguments,
                duration_ms=self._duration(start, finish),
                outcome="FAILED",
            )
            raise
        finish = self._tick()
        succeeded = bool(
            isinstance(completed, subprocess.CompletedProcess)
            and completed.returncode == 0
            and isinstance(completed.stderr, bytes)
            and completed.stderr == b""
        )
        self._append_child(
            arguments,
            duration_ms=self._duration(start, finish),
            outcome="SUCCEEDED" if succeeded else "FAILED",
        )
        return completed

    def validate_success_graph(self) -> None:
        observed = [
            (str(item["stage"]), str(item["branch"]), int(item["attempt"]))
            for item in self.events
        ]
        if observed != _EXPECTED_CHILD_GRAPH:
            self.violations.add("event_graph_drift")

    def add_finalize(
        self,
        *,
        duration_ms: int,
        outcome: str,
        error_class: str,
    ) -> None:
        if len(self.events) >= self.event_limit:
            self.violations.add("event_budget_exceeded")
            if self.events:
                self.events = self.events[: self.event_limit - 1]
        self.events.append(
            {
                "attempt": 1,
                "branch": "none",
                "duration_ms": duration_ms,
                "error_class": error_class,
                "outcome": outcome,
                "sequence": len(self.events) + 1,
                "stage": "run_finalize",
            }
        )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ObservationError("evidence_contract_failed")
    return value


def _safe_source(value: object) -> dict[str, object]:
    source = _mapping(value)
    if set(source) != {
        "binding_stable",
        "commit_bound",
        "commit_sha",
        "git_available",
        "tracked_clean",
    }:
        raise ObservationError("evidence_contract_failed")
    for key in (
        "binding_stable",
        "commit_bound",
        "git_available",
        "tracked_clean",
    ):
        if type(source[key]) is not bool:
            raise ObservationError("evidence_contract_failed")
    commit_sha = source["commit_sha"]
    if commit_sha is not None and (
        not isinstance(commit_sha, str) or _GIT_SHA.fullmatch(commit_sha) is None
    ):
        raise ObservationError("evidence_contract_failed")
    return {key: source[key] for key in sorted(source)}


def _empty_source() -> dict[str, object]:
    return {
        "binding_stable": False,
        "commit_bound": False,
        "commit_sha": None,
        "git_available": False,
        "tracked_clean": False,
    }


def _empty_business_outcomes() -> dict[str, object]:
    return {
        "cleanup": {"failure": False, "success": False},
        "exact_retry": {"failure": False, "success": False},
        "failure": None,
        "success": None,
    }


def _safe_defense_summary(
    value: object,
    *,
    repository_root: Path,
    run_directory: Path,
) -> tuple[dict[str, object], dict[str, object], str]:
    result = _mapping(value)
    source = _safe_source(result.get("source"))
    raw_results = _mapping(result.get("results"))
    raw_cleanup = _mapping(result.get("cleanup"))
    if set(raw_results) != {"success", "failure"} or set(raw_cleanup) != {
        "success",
        "failure",
    }:
        raise ObservationError("evidence_contract_failed")

    expected = {
        "success": {
            "closed_loop": True,
            "state": "RESOLVED",
            "verification": "PASSED",
        },
        "failure": {
            "closed_loop": False,
            "state": "REOPENED",
            "verification": "FAILED",
        },
    }
    business: dict[str, object] = {
        "cleanup": {},
        "exact_retry": {},
    }
    for branch in ("success", "failure"):
        branch_result = _mapping(raw_results[branch])
        terminal = _mapping(branch_result.get("terminal"))
        if dict(terminal) != expected[branch]:
            raise ObservationError("evidence_contract_failed")
        retry = _mapping(branch_result.get("exact_retry"))
        if retry != {
            "approval_command_reused": True,
            "terminal_unchanged": True,
            "verification_unchanged": True,
        }:
            raise ObservationError("evidence_contract_failed")
        cleanup = _mapping(raw_cleanup[branch])
        if cleanup != {"workspace_removed": True}:
            raise ObservationError("evidence_contract_failed")
        business[branch] = {
            **expected[branch],
            "expected_business_result": True,
        }
        business["exact_retry"][branch] = True  # type: ignore[index]
        business["cleanup"][branch] = True  # type: ignore[index]

    report = _mapping(result.get("report"))
    if set(report) != {"relative_path", "sha256"}:
        raise ObservationError("evidence_contract_failed")
    relative_path = report["relative_path"]
    digest = report["sha256"]
    if (
        not isinstance(relative_path, str)
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
    ):
        raise ObservationError("evidence_contract_failed")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ObservationError("evidence_contract_failed")
    defense_report = repository_root / relative
    if defense_report.parent != run_directory or defense_report.name != getattr(
        defense_demo, "REPORT_NAME", "defense-demo-report.json"
    ):
        raise ObservationError("evidence_contract_failed")
    return source, business, digest


def _validate_metric_labels(labels: Mapping[str, object]) -> None:
    if set(labels) != set(_METRIC_LABEL_KEYS):
        raise ObservationError("observation_contract_failed")
    if (
        labels.get("stage") not in _STAGES
        or labels.get("branch") not in _BRANCHES
        or labels.get("outcome") not in _OUTCOMES
        or labels.get("error_class") not in _ERROR_CLASSES
    ):
        raise ObservationError("observation_contract_failed")


def _metrics(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    aggregates: dict[tuple[str, str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for event in events:
        labels = {
            "branch": event["branch"],
            "error_class": event["error_class"],
            "outcome": event["outcome"],
            "stage": event["stage"],
        }
        _validate_metric_labels(labels)
        key = tuple(str(labels[name]) for name in _METRIC_LABEL_KEYS)
        aggregates[key][0] += 1
        aggregates[key][1] += int(event["duration_ms"])
    series = []
    for key in sorted(aggregates):
        count, duration = aggregates[key]
        labels = dict(zip(_METRIC_LABEL_KEYS, key, strict=True))
        _validate_metric_labels(labels)
        series.append(
            {
                "duration_ms": duration,
                "event_count": count,
                "labels": labels,
                "name": "networkagent_local_stage",
            }
        )
    return {
        "high_cardinality_labels_present": False,
        "label_keys": list(_METRIC_LABEL_KEYS),
        "series": series,
    }


def _timing_snapshot(
    events: Sequence[Mapping[str, object]], *, wall_duration_ms: int
) -> dict[str, object]:
    by_branch = {branch: {"duration_ms": 0, "event_count": 0} for branch in _BRANCHES}
    by_stage = {stage: {"duration_ms": 0, "event_count": 0} for stage in _STAGES}
    for event in events:
        duration = int(event["duration_ms"])
        branch_item = by_branch[str(event["branch"])]
        branch_item["duration_ms"] += duration
        branch_item["event_count"] += 1
        stage_item = by_stage[str(event["stage"])]
        stage_item["duration_ms"] += duration
        stage_item["event_count"] += 1
    return {
        "by_branch": by_branch,
        "by_stage": by_stage,
        "diagnostic_only": True,
        "instrumented_duration_ms": sum(int(item["duration_ms"]) for item in events),
        "sample_count": 1,
        "wall_duration_ms": wall_duration_ms,
    }


def _alerts(
    events: Sequence[Mapping[str, object]],
    *,
    business: Mapping[str, object],
    primary_class: str,
    observer_violations: bool,
) -> list[dict[str, str]]:
    execution_alert = primary_class == "EXECUTION" or any(
        item["error_class"] == "EXECUTION" for item in events
    )
    cleanup_alert = primary_class == "CLEANUP" or any(
        item["stage"] == "workspace_cleanup" and item["outcome"] == "FAILED"
        for item in events
    )
    expected_attempts = all(
        sum(1 for item in events if item["stage"] == stage and item["branch"] == branch)
        == 2
        for stage in ("approval_execute", "terminal_verify")
        for branch in ("success", "failure")
    )
    retry = business.get("exact_retry")
    exact_retry = isinstance(retry, Mapping) and retry == {
        "failure": True,
        "success": True,
    }
    retry_alert = not (expected_attempts and exact_retry)
    contract_alert = primary_class == "CONTRACT" or observer_violations
    owner = "networkagent-local-owner"
    return [
        {
            "name": "LOCAL_EXECUTION_FAILURE",
            "owner": owner,
            "runbook_anchor": "local-observability-demo#execution-failure",
            "state": "ALERT" if execution_alert else "OK",
            "threshold": "execution_error_count > 0",
        },
        {
            "name": "LOCAL_CLEANUP_FAILURE",
            "owner": owner,
            "runbook_anchor": "local-observability-demo#cleanup-failure",
            "state": "ALERT" if cleanup_alert else "OK",
            "threshold": "cleanup_error_count > 0",
        },
        {
            "name": "LOCAL_RETRY_AMPLIFICATION",
            "owner": owner,
            "runbook_anchor": "local-observability-demo#retry-amplification",
            "state": "ALERT" if retry_alert else "OK",
            "threshold": "exact_retry_proof != complete",
        },
        {
            "name": "LOCAL_CONTRACT_DRIFT",
            "owner": owner,
            "runbook_anchor": "local-observability-demo#contract-drift",
            "state": "ALERT" if contract_alert else "OK",
            "threshold": "contract_or_observation_error_count > 0",
        },
    ]


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
        raise ObservationError("observation_contract_failed") from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise ObservationError("observation_contract_failed")
    return encoded


def _write_report(
    repository_root: Path,
    run_directory: Path,
    report: Mapping[str, object],
    *,
    token: str,
) -> tuple[str, str]:
    if _TOKEN.fullmatch(token) is None:
        raise ObservationError("report_write_failed")
    final_path = run_directory / REPORT_NAME
    temporary = run_directory / f".{REPORT_NAME}.{token}.tmp"
    try:
        chain_identity = _directory_chain_identity(repository_root, run_directory)
        if os.path.lexists(final_path) or os.path.lexists(temporary):
            raise ObservationError("report_write_failed")
        encoded = _canonical_bytes(report)
        with temporary.open("xb") as stream:
            opened_identity = _regular_file_identity(os.fstat(stream.fileno()))
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
            if _regular_file_identity(
                written
            ) != opened_identity or written.st_size != len(encoded):
                raise ObservationError("report_write_failed")
        if (
            _directory_chain_identity(repository_root, run_directory) != chain_identity
            or _regular_path_identity(temporary) != opened_identity
        ):
            raise ObservationError("report_write_failed")
        os.link(temporary, final_path, follow_symlinks=False)
        if (
            _directory_chain_identity(repository_root, run_directory) != chain_identity
            or _regular_path_identity(temporary) != opened_identity
            or _regular_path_identity(final_path) != opened_identity
            or not os.path.samefile(temporary, final_path)
        ):
            raise ObservationError("report_write_failed")
        with final_path.open("rb") as stream:
            if _regular_file_identity(os.fstat(stream.fileno())) != opened_identity:
                raise ObservationError("report_write_failed")
            persisted = stream.read(MAX_REPORT_BYTES + 1)
        if (
            persisted != encoded
            or _regular_path_identity(final_path) != opened_identity
        ):
            raise ObservationError("report_write_failed")
        temporary.unlink()
        if (
            _directory_chain_identity(repository_root, run_directory) != chain_identity
            or _regular_path_identity(final_path) != opened_identity
        ):
            raise ObservationError("report_write_failed")
        relative = final_path.relative_to(repository_root).as_posix()
        return relative, hashlib.sha256(persisted).hexdigest()
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("report_write_failed") from None
    finally:
        try:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _duration_ms(start: int | None, finish: int | None) -> int:
    if start is None or finish is None or finish < start:
        return 0
    return (finish - start) // 1_000_000


def _primary_error(
    defense_error: Exception | None,
    *,
    observer_violations: bool,
) -> tuple[str | None, str]:
    if defense_error is not None:
        code = getattr(defense_error, "code", "observation_contract_failed")
        if code not in _ERROR_CLASS_BY_CODE:
            code = "observation_contract_failed"
        return code, _ERROR_CLASS_BY_CODE[code]
    if observer_violations:
        return "observation_contract_failed", "OBSERVATION"
    return None, "NONE"


def _error_payload(
    code: str,
    error_class: str,
    *,
    report: Mapping[str, str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "error": {
            "class": error_class,
            "code": code,
            "message": _ERROR_MESSAGES[code],
        },
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
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    random_token: Callable[[], str] = lambda: secrets.token_hex(6),
    stage_mapper: StageMapper | None = None,
    event_limit: int = MAX_EVENTS,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if not arguments.approve_local_simulation:
            raise ObservationError("confirmation_required")
    except ObservationError as exc:
        code = exc.code if exc.code in _ERROR_CLASS_BY_CODE else "invalid_arguments"
        _write_json(
            errors,
            _error_payload(code, _ERROR_CLASS_BY_CODE[code]),
        )
        return 2

    capture = _DefenseRunCapture(
        repository_root,
        utc_now=utc_now,
        random_token=random_token,
    )
    effective_mapper = stage_mapper or (
        lambda child_arguments: _classify_command(
            child_arguments,
            repository_root=repository_root,
            run_directory=capture.candidate,
        )
    )
    runner = _RecordingProcessRunner(
        (
            process_runner
            if process_runner is not None
            else defense_demo._default_process_runner
        ),
        monotonic_ns=monotonic_ns,
        stage_mapper=effective_mapper,
        event_limit=event_limit,
    )
    start_ns = runner._tick()
    try:
        started_at = _utc_value(utc_now)
    except ObservationError as exc:
        _write_json(
            errors,
            _error_payload(exc.code, _ERROR_CLASS_BY_CODE[exc.code]),
        )
        return 2
    defense_result: dict[str, object] | None = None
    defense_error: Exception | None = None
    try:
        defense_result = defense_demo._execute_demo(
            process_runner=runner,
            repository_root=repository_root,
            utc_now=capture.now,
            random_token=capture.token,
        )
    except Exception as exc:
        defense_error = exc

    source = _empty_source()
    business = _empty_business_outcomes()
    defense_report_sha: str | None = None
    run_directory: Path | None = None
    try:
        run_directory = capture.owned_run_directory()
        if defense_error is None:
            source, business, defense_report_sha = _safe_defense_summary(
                defense_result,
                repository_root=repository_root,
                run_directory=run_directory,
            )
            runner.validate_success_graph()
    except ObservationError as exc:
        if defense_error is None:
            defense_error = exc

    finalize_start = runner._tick()
    finalize_finish = runner._tick()
    finalize_duration = runner._duration(finalize_start, finalize_finish)
    error_code, error_class = _primary_error(
        defense_error,
        observer_violations=bool(runner.violations),
    )
    runner.add_finalize(
        duration_ms=finalize_duration,
        outcome="SUCCEEDED" if error_code is None else "FAILED",
        error_class=error_class,
    )
    finish_ns = runner._tick()
    try:
        finished_at = _utc_value(utc_now)
    except ObservationError:
        finished_at = started_at
        runner.violations.add("utc_clock_failed")
        if error_code is None:
            error_code, error_class = (
                "observation_contract_failed",
                "OBSERVATION",
            )
            runner.events[-1]["outcome"] = "FAILED"
            runner.events[-1]["error_class"] = "OBSERVATION"
    wall_duration_ms = _duration_ms(start_ns, finish_ns)
    if start_ns is None or finish_ns is None or finish_ns < start_ns:
        runner.violations.add("clock_regression")
        if error_code is None:
            error_code, error_class = (
                "observation_contract_failed",
                "OBSERVATION",
            )
            runner.events[-1]["outcome"] = "FAILED"
            runner.events[-1]["error_class"] = "OBSERVATION"

    observation_id = (
        capture.observation_id
        if capture.token_value is not None
        and _TOKEN.fullmatch(capture.token_value) is not None
        else "observation-unavailable"
    )
    report_body: dict[str, object] = {
        "business_outcomes": business,
        "correlation": {
            "defense_report_sha256": defense_report_sha,
            "observation_id": observation_id,
            "propagated_trace": False,
            "source_commit": source["commit_sha"],
        },
        "coverage": {
            "delivered": [
                "BOUNDED_LOCAL_STAGE_EVENTS",
                "LOCAL_TIMING_SNAPSHOT",
                "STABLE_LOCAL_ERROR_CLASSIFICATION",
                "IN_REPORT_LOCAL_ALERT_EVALUATION",
            ],
            "not_claimed": list(_NOT_CLAIMED),
        },
        "events": runner.events,
        "local_alerts": _alerts(
            runner.events,
            business=business,
            primary_class=error_class,
            observer_violations=bool(runner.violations),
        ),
        "metrics": _metrics(runner.events),
        "ok": error_code is None,
        "privacy": {
            "absolute_paths_recorded": False,
            "child_stderr_recorded": False,
            "child_stdout_recorded": False,
            "environment_recorded": False,
            "high_cardinality_metric_labels": False,
            "raw_arguments_recorded": False,
            "status": "PASS",
        },
        "run": {
            "diagnostic_only": True,
            "duration_ms": wall_duration_ms,
            "error_class": error_class,
            "error_code": error_code,
            "event_count": len(runner.events),
            "finished_at": _timestamp(finished_at),
            "observation_id": observation_id,
            "started_at": _timestamp(started_at),
            "status": "PASS" if error_code is None else "FAIL",
        },
        "schema": SCHEMA,
        "source": source,
        "timing_snapshot": _timing_snapshot(
            runner.events,
            wall_duration_ms=wall_duration_ms,
        ),
    }

    report_reference: dict[str, str] | None = None
    try:
        if run_directory is None or capture.token_value is None:
            raise ObservationError("report_write_failed")
        relative_path, report_sha = _write_report(
            repository_root,
            run_directory,
            report_body,
            token=capture.token_value,
        )
        report_reference = {
            "relative_path": relative_path,
            "sha256": report_sha,
        }
    except ObservationError as exc:
        code = exc.code if exc.code in _ERROR_CLASS_BY_CODE else "report_write_failed"
        _write_json(
            errors,
            _error_payload(code, _ERROR_CLASS_BY_CODE[code]),
        )
        return 2

    if error_code is not None:
        _write_json(
            errors,
            _error_payload(
                error_code,
                error_class,
                report=report_reference,
            ),
        )
        return 2
    _write_json(output, {**report_body, "report": report_reference})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
