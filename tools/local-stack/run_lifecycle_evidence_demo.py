#!/usr/bin/env python3
"""Build bounded lifecycle evidence from the fixed offline defense demo."""

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
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "networkagent-local-lifecycle-evidence/1.0"
PROJECTION_SCHEMA = "networkagent-local-lifecycle-projection/1.0"
REPORT_NAME = "local-lifecycle-report.json"
MAX_REPORT_BYTES = 64 * 1024
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")

_NOT_CLAIMED = [
    "OPEN_TELEMETRY_EXPORT",
    "DISTRIBUTED_TRACE",
    "RUNTIME_STRUCTURED_LOGGING",
    "CROSS_HTTP_REPLAY_A2A_MCP_TRACE",
    "PROMETHEUS_METRICS",
    "SERVICE_LEVEL_OBJECTIVES",
    "EXTERNAL_ALERT_DELIVERY",
    "GATE_E_OR_G5_CLOSURE",
    "CLOUD_OR_PRODUCTION_EXECUTION",
]
_RECORD_COUNTS = {
    "action_runs": 1,
    "approval_decisions": 2,
    "incident_audit_events": 8,
    "incidents": 1,
    "projected_events": 14,
    "rca_reports": 1,
    "remediation_actions": 1,
    "verification_runs": 1,
}
_INVARIANTS = {
    "bindings_exact": True,
    "revision_contiguous": True,
    "side_effects": False,
    "single_execution_attempt": True,
    "single_incident": True,
}
_AUDIT_PREFIX = (
    "INCIDENT_AUDIT_EVENT",
    "INCIDENT_REPOSITORY",
    "RECORD_STATE_TRANSITION",
)
_COMMON_EVENT_GRAPH = [
    (0, *_AUDIT_PREFIX, "DETECTED"),
    (1, *_AUDIT_PREFIX, "TRIAGED"),
    (2, *_AUDIT_PREFIX, "INVESTIGATING"),
    (3, "RCA_REPORT", "RCA_GATEWAY", "PROPOSE_REPORT", "CONCLUSIVE"),
    (
        3,
        "REMEDIATION_ACTION",
        "GOVERNANCE_ENGINE",
        "PROPOSE_ACTION",
        "LOCAL_SIMULATION",
    ),
    (3, *_AUDIT_PREFIX, "RCA_COMPLETE"),
    (
        4,
        "APPROVAL_DECISION",
        "APPROVAL_GATEWAY",
        "REQUEST_NETWORK_ACTION_APPROVAL",
        "PENDING",
    ),
    (4, *_AUDIT_PREFIX, "AWAITING_APPROVAL"),
    (
        5,
        "APPROVAL_DECISION",
        "APPROVAL_GATEWAY",
        "DECIDE_NETWORK_ACTION_APPROVAL",
        "APPROVED",
    ),
    (5, *_AUDIT_PREFIX, "REMEDIATING"),
    (
        6,
        "ACTION_RUN",
        "SIMULATED_ACTION_GATEWAY",
        "EXECUTE_LOCAL_SIMULATION",
        "SUCCEEDED",
    ),
    (6, *_AUDIT_PREFIX, "VERIFYING"),
]


def _event_graph(branch: str) -> list[tuple[object, ...]]:
    if branch == "success":
        terminal = ("PASSED", "RESOLVED")
    elif branch == "failure":
        terminal = ("FAILED", "REOPENED")
    else:
        raise LifecycleEvidenceError("lifecycle_contract_failed")
    return [
        *_COMMON_EVENT_GRAPH,
        (
            7,
            "VERIFICATION_RUN",
            "LOCAL_VERIFICATION_GATEWAY",
            "VERIFY_LOCAL_SIMULATION",
            terminal[0],
        ),
        (7, *_AUDIT_PREFIX, terminal[1]),
    ]


def _load_defense_demo() -> Any:
    module_path = Path(__file__).with_name("run_defense_demo.py")
    spec = importlib.util.spec_from_file_location(
        "networkagent_lifecycle_defense_demo", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("defense demo is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defense_demo = _load_defense_demo()


class LifecycleEvidenceError(Exception):
    """A stable lifecycle-evidence failure safe for the JSON boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_MESSAGES = {
    "cleanup_failed": "local lifecycle demo cleanup failed safely",
    "command_failed": "local lifecycle demo command failed safely",
    "confirmation_required": "explicit local simulation approval is required",
    "evidence_contract_failed": "local lifecycle demo detected contract drift",
    "invalid_arguments": "command arguments are invalid",
    "lifecycle_contract_failed": (
        "local lifecycle projection violated its fixed contract"
    ),
    "report_write_failed": "local lifecycle report could not be written safely",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise LifecycleEvidenceError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-lifecycle-evidence", add_help=False
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


def _mapping(
    value: object, *, code: str = "lifecycle_contract_failed"
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LifecycleEvidenceError(code)
    return value


def _matches_exact(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            _matches_exact(value[key], expected[key]) for key in expected
        )
    if isinstance(expected, (list, tuple)):
        return len(value) == len(expected) and all(
            _matches_exact(item, expected_item)
            for item, expected_item in zip(value, expected, strict=True)
        )
    return value == expected


def _expect(value: object, expected: object, *, code: str) -> None:
    if not _matches_exact(value, expected):
        raise LifecycleEvidenceError(code)


def _utc_value(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError
        if value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)
    except Exception:
        raise LifecycleEvidenceError("lifecycle_contract_failed") from None


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
        raise LifecycleEvidenceError("report_write_failed") from None
    if _is_link_like(path) or not stat.S_ISDIR(details.st_mode):
        raise LifecycleEvidenceError("report_write_failed")
    return details.st_dev, details.st_ino


def _directory_chain_identity(
    repository_root: Path, run_directory: Path
) -> tuple[PathIdentity, PathIdentity, PathIdentity]:
    local_root = repository_root / ".local"
    defense_root = local_root / "networkagent-defense"
    if run_directory.parent != defense_root:
        raise LifecycleEvidenceError("report_write_failed")
    return tuple(
        _directory_identity(item) for item in (local_root, defense_root, run_directory)
    )  # type: ignore[return-value]


def _regular_file_identity(details: os.stat_result) -> PathIdentity:
    if not stat.S_ISREG(details.st_mode):
        raise LifecycleEvidenceError("report_write_failed")
    return details.st_dev, details.st_ino


def _regular_path_identity(path: Path) -> PathIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise LifecycleEvidenceError("report_write_failed") from None
    if _is_link_like(path):
        raise LifecycleEvidenceError("report_write_failed")
    return _regular_file_identity(details)


class _DefenseRunCapture:
    """Capture only the fixed defense run directory identity."""

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
            raise LifecycleEvidenceError("lifecycle_contract_failed") from None
        if not isinstance(value, str):
            raise LifecycleEvidenceError("lifecycle_contract_failed")
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

    def owned_run_directory(self) -> Path:
        candidate = self.candidate
        if candidate is None or self.existed_before is not False:
            raise LifecycleEvidenceError("report_write_failed")
        try:
            if not candidate.is_dir():
                raise LifecycleEvidenceError("report_write_failed")
            _directory_chain_identity(self.repository_root, candidate)
        except LifecycleEvidenceError:
            raise
        except OSError:
            raise LifecycleEvidenceError("report_write_failed") from None
        return candidate


def _safe_source(value: object) -> dict[str, object]:
    source = _mapping(value, code="evidence_contract_failed")
    expected_keys = {
        "binding_stable",
        "commit_bound",
        "commit_sha",
        "git_available",
        "tracked_clean",
    }
    if set(source) != expected_keys:
        raise LifecycleEvidenceError("evidence_contract_failed")
    for key in expected_keys - {"commit_sha"}:
        if type(source[key]) is not bool:
            raise LifecycleEvidenceError("evidence_contract_failed")
    commit_sha = source["commit_sha"]
    if commit_sha is not None and (
        not isinstance(commit_sha, str) or _GIT_SHA.fullmatch(commit_sha) is None
    ):
        raise LifecycleEvidenceError("evidence_contract_failed")
    return {key: source[key] for key in sorted(source)}


def _validate_projection(branch: str, value: object) -> dict[str, object]:
    projection = _mapping(value)
    expected_keys = {
        "classification",
        "distributed_trace",
        "invariants",
        "ordering",
        "read_only",
        "record_counts",
        "revision_groups",
        "scenario",
        "schema",
        "terminal_status",
    }
    if set(projection) != expected_keys:
        raise LifecycleEvidenceError("lifecycle_contract_failed")
    terminal = "RESOLVED" if branch == "success" else "REOPENED"
    scenario = (
        "LOCAL_SIMULATION_RESOLVED"
        if branch == "success"
        else "LOCAL_SIMULATION_REOPENED"
    )
    for key, expected in {
        "classification": "DERIVED_FROM_DURABLE_CANONICAL_RECORDS",
        "distributed_trace": False,
        "ordering": "REVISION_GROUPED_ATOMIC_PROJECTION",
        "read_only": True,
        "scenario": scenario,
        "schema": PROJECTION_SCHEMA,
        "terminal_status": terminal,
    }.items():
        _expect(projection[key], expected, code="lifecycle_contract_failed")
    _expect(
        dict(_mapping(projection["record_counts"])),
        _RECORD_COUNTS,
        code="lifecycle_contract_failed",
    )
    _expect(
        dict(_mapping(projection["invariants"])),
        _INVARIANTS,
        code="lifecycle_contract_failed",
    )

    groups = projection["revision_groups"]
    if not isinstance(groups, list) or len(groups) != 8:
        raise LifecycleEvidenceError("lifecycle_contract_failed")
    observed: list[tuple[object, ...]] = []
    safe_groups: list[dict[str, object]] = []
    sequence = 0
    for expected_revision, raw_group in enumerate(groups):
        group = _mapping(raw_group)
        if set(group) != {"events", "revision"}:
            raise LifecycleEvidenceError("lifecycle_contract_failed")
        _expect(
            group["revision"],
            expected_revision,
            code="lifecycle_contract_failed",
        )
        events = group["events"]
        if not isinstance(events, list):
            raise LifecycleEvidenceError("lifecycle_contract_failed")
        safe_events: list[dict[str, object]] = []
        for raw_event in events:
            sequence += 1
            event = _mapping(raw_event)
            if set(event) != {
                "component",
                "occurred_at",
                "operation",
                "outcome",
                "record_type",
                "sequence",
            }:
                raise LifecycleEvidenceError("lifecycle_contract_failed")
            _expect(event["sequence"], sequence, code="lifecycle_contract_failed")
            occurred_at = event["occurred_at"]
            if (
                not isinstance(occurred_at, str)
                or _UTC_TIMESTAMP.fullmatch(occurred_at) is None
            ):
                raise LifecycleEvidenceError("lifecycle_contract_failed")
            descriptor = (
                expected_revision,
                event["record_type"],
                event["component"],
                event["operation"],
                event["outcome"],
            )
            if not all(isinstance(item, str) for item in descriptor[1:]):
                raise LifecycleEvidenceError("lifecycle_contract_failed")
            observed.append(descriptor)
            safe_events.append(
                {
                    "component": event["component"],
                    "occurred_at": occurred_at,
                    "operation": event["operation"],
                    "outcome": event["outcome"],
                    "record_type": event["record_type"],
                    "sequence": sequence,
                }
            )
        safe_groups.append({"events": safe_events, "revision": expected_revision})
    if observed != _event_graph(branch) or sequence != 14:
        raise LifecycleEvidenceError("lifecycle_contract_failed")
    return {
        "classification": "DERIVED_FROM_DURABLE_CANONICAL_RECORDS",
        "distributed_trace": False,
        "invariants": dict(_INVARIANTS),
        "ordering": "REVISION_GROUPED_ATOMIC_PROJECTION",
        "read_only": True,
        "record_counts": dict(_RECORD_COUNTS),
        "revision_groups": safe_groups,
        "scenario": scenario,
        "schema": PROJECTION_SCHEMA,
        "terminal_status": terminal,
    }


def _validate_defense_result(
    value: object,
) -> tuple[dict[str, object], dict[str, object]]:
    result = _mapping(value, code="evidence_contract_failed")
    _expect(
        result.get("schema"),
        "networkagent-native-defense-demo/1.0",
        code="evidence_contract_failed",
    )
    _expect(result.get("ok"), True, code="evidence_contract_failed")
    source = _safe_source(result.get("source"))
    raw_results = _mapping(result.get("results"), code="evidence_contract_failed")
    raw_cleanup = _mapping(result.get("cleanup"), code="evidence_contract_failed")
    if set(raw_results) != {"success", "failure"} or set(raw_cleanup) != {
        "success",
        "failure",
    }:
        raise LifecycleEvidenceError("evidence_contract_failed")
    terminal_contract = {
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
    proof: dict[str, object] = {
        "cleanup": {},
        "exact_retry": {},
        "terminal": {},
    }
    for branch in ("success", "failure"):
        branch_result = _mapping(raw_results[branch], code="evidence_contract_failed")
        terminal = dict(
            _mapping(branch_result.get("terminal"), code="evidence_contract_failed")
        )
        _expect(
            terminal,
            terminal_contract[branch],
            code="evidence_contract_failed",
        )
        retry = dict(
            _mapping(branch_result.get("exact_retry"), code="evidence_contract_failed")
        )
        _expect(
            retry,
            {
                "approval_command_reused": True,
                "terminal_unchanged": True,
                "verification_unchanged": True,
            },
            code="evidence_contract_failed",
        )
        cleanup = dict(_mapping(raw_cleanup[branch], code="evidence_contract_failed"))
        _expect(
            cleanup,
            {"workspace_removed": True},
            code="evidence_contract_failed",
        )
        proof["terminal"][branch] = {  # type: ignore[index]
            **terminal_contract[branch],
            "expected_business_result": True,
        }
        proof["exact_retry"][branch] = True  # type: ignore[index]
        proof["cleanup"][branch] = True  # type: ignore[index]
    return source, proof


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
        raise LifecycleEvidenceError("lifecycle_contract_failed") from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise LifecycleEvidenceError("lifecycle_contract_failed")
    return encoded


def _write_report(
    repository_root: Path,
    run_directory: Path,
    report: Mapping[str, object],
    *,
    token: str,
) -> tuple[int, str]:
    if _TOKEN.fullmatch(token) is None:
        raise LifecycleEvidenceError("report_write_failed")
    final_path = run_directory / REPORT_NAME
    temporary = run_directory / f".{REPORT_NAME}.{token}.tmp"
    try:
        chain_identity = _directory_chain_identity(repository_root, run_directory)
        if os.path.lexists(final_path) or os.path.lexists(temporary):
            raise LifecycleEvidenceError("report_write_failed")
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
                raise LifecycleEvidenceError("report_write_failed")
        if (
            _directory_chain_identity(repository_root, run_directory) != chain_identity
            or _regular_path_identity(temporary) != opened_identity
        ):
            raise LifecycleEvidenceError("report_write_failed")
        os.link(temporary, final_path, follow_symlinks=False)
        if (
            _directory_chain_identity(repository_root, run_directory) != chain_identity
            or _regular_path_identity(temporary) != opened_identity
            or _regular_path_identity(final_path) != opened_identity
            or not os.path.samefile(temporary, final_path)
        ):
            raise LifecycleEvidenceError("report_write_failed")
        with final_path.open("rb") as stream:
            if _regular_file_identity(os.fstat(stream.fileno())) != opened_identity:
                raise LifecycleEvidenceError("report_write_failed")
            persisted = stream.read(MAX_REPORT_BYTES + 1)
        if (
            persisted != encoded
            or _regular_path_identity(final_path) != opened_identity
        ):
            raise LifecycleEvidenceError("report_write_failed")
        temporary.unlink()
        if (
            _directory_chain_identity(repository_root, run_directory) != chain_identity
            or _regular_path_identity(final_path) != opened_identity
        ):
            raise LifecycleEvidenceError("report_write_failed")
        return len(persisted), hashlib.sha256(persisted).hexdigest()
    except LifecycleEvidenceError:
        raise
    except Exception:
        raise LifecycleEvidenceError("report_write_failed") from None
    finally:
        try:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _error_payload(code: str) -> dict[str, object]:
    stable = code if code in _ERROR_MESSAGES else "command_failed"
    return {
        "error": {"code": stable, "message": _ERROR_MESSAGES[stable]},
        "ok": False,
        "schema": SCHEMA,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    repository_root: Path = REPOSITORY_ROOT,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    random_token: Callable[[], str] = lambda: secrets.token_hex(6),
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if arguments.approve_local_simulation is not True:
            raise LifecycleEvidenceError("confirmation_required")
    except LifecycleEvidenceError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2

    repository = repository_root.resolve()
    capture = _DefenseRunCapture(
        repository,
        utc_now=utc_now,
        random_token=random_token,
    )
    projections: dict[str, dict[str, object]] = {}

    def collect(branch: str, projection: Mapping[str, object]) -> None:
        if branch in projections or branch not in {"success", "failure"}:
            raise LifecycleEvidenceError("lifecycle_contract_failed")
        projections[branch] = _validate_projection(branch, projection)

    try:
        defense_result = defense_demo._execute_demo(
            process_runner=(
                process_runner
                if process_runner is not None
                else defense_demo._default_process_runner
            ),
            repository_root=repository,
            utc_now=capture.now,
            random_token=capture.token,
            lifecycle_projection_hook=collect,
        )
        if set(projections) != {"success", "failure"}:
            raise LifecycleEvidenceError("lifecycle_contract_failed")
        source, proof = _validate_defense_result(defense_result)
        run_directory = capture.owned_run_directory()
        report_body: dict[str, object] = {
            "branches": {
                "failure": projections["failure"],
                "success": projections["success"],
            },
            "classification": (
                "LOCAL_CANONICAL_LIFECYCLE_EVIDENCE"
                if source["commit_bound"] is True
                else "LOCAL_WORKTREE_CANONICAL_LIFECYCLE_EVIDENCE"
            ),
            "coverage": {
                "delivered": [
                    "DURABLE_CANONICAL_RECORD_PROJECTION",
                    "REVISION_GROUPED_ATOMIC_LIFECYCLE",
                    "DUAL_TERMINAL_BRANCH_EVIDENCE",
                    "READ_ONLY_PROJECTION_AFTER_EXACT_RETRY",
                ],
                "not_claimed": list(_NOT_CLAIMED),
            },
            "ok": True,
            "privacy": {
                "absolute_paths_recorded": False,
                "domain_hashes_recorded": False,
                "domain_identifiers_recorded": False,
                "pseudonymous_correlation_recorded": False,
                "raw_records_recorded": False,
                "status": "PASS",
                "workspace_identifiers_recorded": False,
            },
            "proof": {
                **proof,
                "branch_count": 2,
                "projected_event_count": 28,
                "revision_group_count": 16,
            },
            "schema": SCHEMA,
            "source": source,
        }
        if capture.token_value is None:
            raise LifecycleEvidenceError("report_write_failed")
        report_bytes, digest = _write_report(
            repository,
            run_directory,
            report_body,
            token=capture.token_value,
        )
        _write_json(
            output,
            {
                **report_body,
                "report": {
                    "bytes": report_bytes,
                    "filename": REPORT_NAME,
                    "sha256": digest,
                },
            },
        )
        return 0
    except LifecycleEvidenceError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "command_failed")
        _write_json(errors, _error_payload(code))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
