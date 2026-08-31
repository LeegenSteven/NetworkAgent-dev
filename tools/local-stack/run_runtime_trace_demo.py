#!/usr/bin/env python3
"""Produce bounded evidence for one fixed local runtime correlation chain."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, Iterator, TextIO

from telco_lab import (
    LOCAL_RUNTIME_TRACE_SCHEMA,
    LocalRuntimeTraceEvent,
    derive_local_replay_trace_id,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "networkagent-local-runtime-trace-evidence/1.0"
CLASSIFICATION = "LOCAL_SINGLE_PROCESS_LOOPBACK_TRACE_EVIDENCE"
REPORT_NAME = "local-runtime-trace-report.json"
RAW_EVENT_NAME = "local-runtime-events.jsonl"
MAX_REPORT_BYTES = 64 * 1024
MAX_RAW_EVENT_BYTES = 4 * 1024
MAX_RAW_STREAM_BYTES = 64 * 1024
MAX_GIT_OUTPUT_BYTES = 64 * 1024
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")

_EXPECTED_COMPONENTS = (
    "sender",
    "repository",
    "receiver",
    "sender",
    "a2a",
    "a2a",
)
_EXPECTED_OPERATIONS = (
    "REPLAY_REQUEST_VALIDATED",
    "INCIDENT_DURABLE_READBACK",
    "REPLAY_RESPONSE_ACCEPTED",
    "REPLAY_DELIVERY_ACKNOWLEDGED",
    "ANALYZE_REQUEST_VALIDATED",
    "ANALYZE_COMPLETED",
)
_TABLES = frozenset(
    {
        "assurance_a2a_tasks",
        "assurance_pending_confirmations",
        "assurance_schema_metadata",
        "canonical_incident_audit",
        "canonical_incident_idempotency",
        "canonical_incident_source_events",
        "canonical_incidents",
        "cell_traces",
        "local_schema_metadata",
        "performance",
    }
)
_CHANGED_TABLE = "assurance_a2a_tasks"
_FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "body",
        "context_id",
        "idempotency_key",
        "incident_id",
        "metrics",
        "path",
        "resource_id",
        "source_event_id",
        "task_id",
        "trace_id",
        "workflow_id",
    }
)
_NOT_CLAIMED = [
    "DISTRIBUTED_OR_CROSS_PROCESS_CORRELATION",
    "OPEN_TELEMETRY_EXPORT",
    "MCP_PROPAGATION",
    "MULTI_EVENT_OR_CONCURRENT_CORRELATION",
    "SINK_DELIVERY_GUARANTEE",
    "FULL_DATABASE_READ_ONLY_ANALYZE",
    "RAW_EVENT_ARTIFACT_UPLOAD",
    "PRODUCTION_OR_CLOUD_OBSERVABILITY",
    "GATE_E_OR_G5_CLOSURE",
    "IDENTITY_UNKNOWN_OR_RACED_RESIDUE_AUTO_CLEANUP",
]

DirectoryIdentity = tuple[int, int]
FileIdentity = tuple[int, int, int, int, int, int]


class RuntimeTraceEvidenceError(Exception):
    """A stable evidence error that never reflects input or platform details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_MESSAGES = {
    "cleanup_failed": "local runtime trace demo cleanup failed safely",
    "command_failed": "local runtime trace demo command failed safely",
    "confirmation_required": "explicit local simulation approval is required",
    "invalid_arguments": "command arguments are invalid",
    "report_write_failed": "local runtime trace report could not be written safely",
    "trace_contract_failed": (
        "local runtime trace evidence violated its fixed contract"
    ),
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise RuntimeTraceEvidenceError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-runtime-trace-evidence", add_help=False
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


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        details = os.lstat(path)
        attributes = getattr(details, "st_file_attributes", 0)
        return bool(attributes & 0x400)
    except OSError:
        return True


def _directory_identity(path: Path, *, code: str) -> DirectoryIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise RuntimeTraceEvidenceError(code) from None
    if _is_link_like(path) or not stat.S_ISDIR(details.st_mode):
        raise RuntimeTraceEvidenceError(code)
    return details.st_dev, details.st_ino


def _file_identity(details: os.stat_result, *, code: str) -> FileIdentity:
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise RuntimeTraceEvidenceError(code)
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
        details.st_nlink,
    )


def _path_file_identity(path: Path, *, code: str) -> FileIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise RuntimeTraceEvidenceError(code) from None
    if _is_link_like(path):
        raise RuntimeTraceEvidenceError(code)
    return _file_identity(details, code=code)


def _same_opened_file(path: Path, details: os.stat_result, *, code: str) -> bool:
    _bound_path_identity(path, details, code=code)
    return True


def _bound_path_identity(
    path: Path, details: os.stat_result, *, code: str
) -> FileIdentity:
    opened = _file_identity(details, code=code)
    current = _path_file_identity(path, code=code)
    if not (
        opened[0] == current[0]
        and opened[1] == current[1]
        and opened[2] == current[2]
        and opened[3] == current[3]
        and opened[5] == current[5]
    ):
        raise RuntimeTraceEvidenceError(code)
    return current


@dataclass(frozen=True, slots=True)
class _RunDirectory:
    repository_root: Path
    directory: Path
    chain: tuple[
        DirectoryIdentity, DirectoryIdentity, DirectoryIdentity, DirectoryIdentity
    ]

    def validate(self, *, code: str) -> None:
        local_root = self.repository_root / ".local"
        evidence_root = local_root / "networkagent-runtime-trace"
        if self.directory.parent != evidence_root:
            raise RuntimeTraceEvidenceError(code)
        current = tuple(
            _directory_identity(item, code=code)
            for item in (
                self.repository_root,
                local_root,
                evidence_root,
                self.directory,
            )
        )
        if current != self.chain:
            raise RuntimeTraceEvidenceError(code)


def _plain_mkdir(path: Path, *, code: str) -> None:
    try:
        if os.path.lexists(path):
            _directory_identity(path, code=code)
            return
        os.mkdir(path)
        _directory_identity(path, code=code)
    except RuntimeTraceEvidenceError:
        raise
    except OSError:
        raise RuntimeTraceEvidenceError(code) from None


def _create_run_directory(
    repository_root: Path,
    *,
    utc_now: Callable[[], datetime],
    random_token: Callable[[], str],
) -> tuple[_RunDirectory, str]:
    try:
        root = repository_root.resolve(strict=True)
        _directory_identity(root, code="report_write_failed")
        moment = utc_now()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise ValueError
        moment = moment.astimezone(UTC)
        token = random_token()
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise ValueError
        local_root = root / ".local"
        evidence_root = local_root / "networkagent-runtime-trace"
        _plain_mkdir(local_root, code="report_write_failed")
        _plain_mkdir(evidence_root, code="report_write_failed")
        candidate = evidence_root / f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{token}"
        if os.path.lexists(candidate):
            raise RuntimeTraceEvidenceError("report_write_failed")
        os.mkdir(candidate)
        chain = tuple(
            _directory_identity(item, code="report_write_failed")
            for item in (root, local_root, evidence_root, candidate)
        )
        result = _RunDirectory(root, candidate, chain)  # type: ignore[arg-type]
        result.validate(code="report_write_failed")
        return result, token
    except RuntimeTraceEvidenceError:
        raise
    except Exception:
        raise RuntimeTraceEvidenceError("report_write_failed") from None


class RawEventCollector:
    """Owned, synchronous JSONL sink whose failures remain observable here."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[LocalRuntimeTraceEvent] = []
        self._lock = threading.Lock()
        self._closed = False
        self._size = 0
        self.final_identity: FileIdentity | None = None
        stream = None
        try:
            if os.path.lexists(path):
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            stream = path.open("x+b")
            self._stream = stream
            if not _same_opened_file(
                path,
                os.fstat(self._stream.fileno()),
                code="trace_contract_failed",
            ):
                raise RuntimeTraceEvidenceError("trace_contract_failed")
        except RuntimeTraceEvidenceError:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise
        except Exception:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise RuntimeTraceEvidenceError("trace_contract_failed") from None

    def __call__(self, event: LocalRuntimeTraceEvent) -> None:
        if type(event) is not LocalRuntimeTraceEvent:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        try:
            encoded = (
                json.dumps(
                    event.as_dict(),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
        except Exception:
            raise RuntimeTraceEvidenceError("trace_contract_failed") from None
        if len(encoded) > MAX_RAW_EVENT_BYTES:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        with self._lock:
            if self._closed or self._size + len(encoded) > MAX_RAW_STREAM_BYTES:
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            try:
                self._stream.write(encoded)
                self._stream.flush()
                os.fsync(self._stream.fileno())
            except Exception:
                raise RuntimeTraceEvidenceError("trace_contract_failed") from None
            self._size += len(encoded)
            self.events.append(event)

    def close_and_validate(self) -> tuple[LocalRuntimeTraceEvent, ...]:
        raw: bytes
        with self._lock:
            if self._closed:
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            try:
                self._stream.flush()
                os.fsync(self._stream.fileno())
                details = os.fstat(self._stream.fileno())
                if not _same_opened_file(
                    self.path, details, code="trace_contract_failed"
                ):
                    raise RuntimeTraceEvidenceError("trace_contract_failed")
                first_identity = _bound_path_identity(
                    self.path, details, code="trace_contract_failed"
                )
                self._stream.seek(0)
                raw = self._stream.read(MAX_RAW_STREAM_BYTES + 1)
                final_identity = _bound_path_identity(
                    self.path,
                    os.fstat(self._stream.fileno()),
                    code="trace_contract_failed",
                )
                if final_identity != first_identity:
                    raise RuntimeTraceEvidenceError("trace_contract_failed")
                self._stream.close()
                if (
                    _path_file_identity(self.path, code="trace_contract_failed")
                    != final_identity
                ):
                    raise RuntimeTraceEvidenceError("trace_contract_failed")
                self.final_identity = final_identity
            except RuntimeTraceEvidenceError:
                raise
            except Exception:
                raise RuntimeTraceEvidenceError("trace_contract_failed") from None
            self._closed = True
        try:
            if len(raw) != self._size or len(raw) > MAX_RAW_STREAM_BYTES:
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            lines = raw.splitlines()
            if len(lines) != len(self.events):
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            documents = [json.loads(line) for line in lines]
            if documents != [event.as_dict() for event in self.events]:
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            if (
                self.final_identity is None
                or _path_file_identity(self.path, code="trace_contract_failed")
                != self.final_identity
            ):
                raise RuntimeTraceEvidenceError("trace_contract_failed")
            return tuple(self.events)
        except RuntimeTraceEvidenceError:
            raise
        except Exception:
            raise RuntimeTraceEvidenceError("trace_contract_failed") from None

    def close_safely(self) -> None:
        try:
            with self._lock:
                if not self._closed:
                    self._stream.close()
                    self._closed = True
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class ScenarioEvidence:
    business_success: bool
    binding_checks: tuple[bool, bool, bool, bool, bool, bool]
    changed_tables: tuple[str, ...]
    unchanged_tables: tuple[str, ...]
    canonical_domain_unchanged: bool
    governance_before: tuple[int, int, int, int]
    governance_after: tuple[int, int, int, int]

    @classmethod
    def fixed_success(cls) -> ScenarioEvidence:
        return cls(
            business_success=True,
            binding_checks=(True, True, True, True, True, True),
            changed_tables=(_CHANGED_TABLE,),
            unchanged_tables=tuple(sorted(_TABLES - {_CHANGED_TABLE})),
            canonical_domain_unchanged=True,
            governance_before=(0, 0, 0, 0),
            governance_after=(0, 0, 0, 0),
        )


def _safe_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    return environment


def _default_process_runner(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
    )


def _source_snapshot(
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    *,
    repository_root: Path,
) -> dict[str, object]:
    environment = _safe_environment()
    try:
        revision = process_runner(
            ("git", "rev-parse", "--verify", "HEAD"),
            cwd=repository_root,
            env=environment,
            timeout=10,
        )
        status = process_runner(
            ("git", "status", "--porcelain", "--untracked-files=no"),
            cwd=repository_root,
            env=environment,
            timeout=10,
        )
        if (
            revision.returncode != 0
            or status.returncode != 0
            or revision.stderr != b""
            or status.stderr != b""
            or len(revision.stdout) > MAX_GIT_OUTPUT_BYTES
            or len(status.stdout) > MAX_GIT_OUTPUT_BYTES
        ):
            raise ValueError
        sha = revision.stdout.decode("ascii", errors="strict").strip()
        if _GIT_SHA.fullmatch(sha) is None:
            raise ValueError
        return {"available": True, "sha": sha, "tracked_clean": status.stdout == b""}
    except Exception:
        return {"available": False, "sha": None, "tracked_clean": False}


def _source_binding(
    before: Mapping[str, object], after: Mapping[str, object]
) -> dict[str, object]:
    available = before.get("available") is True and after.get("available") is True
    stable = bool(
        available
        and isinstance(before.get("sha"), str)
        and before.get("sha") == after.get("sha")
    )
    clean = bool(
        available
        and before.get("tracked_clean") is True
        and after.get("tracked_clean") is True
    )
    return {
        "binding_stable": stable,
        "commit_bound": stable and clean,
        "commit_sha": before.get("sha") if stable else None,
        "git_available": available,
        "tracked_clean": clean,
    }


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def _validate_and_summarize(
    evidence: ScenarioEvidence, events: tuple[LocalRuntimeTraceEvent, ...]
) -> dict[str, object]:
    if type(evidence) is not ScenarioEvidence or evidence.business_success is not True:
        raise RuntimeTraceEvidenceError("trace_contract_failed")
    if (
        type(events) is not tuple
        or len(events) != 6
        or tuple(event.component for event in events) != _EXPECTED_COMPONENTS
        or tuple(event.operation for event in events) != _EXPECTED_OPERATIONS
        or any(event.schema != LOCAL_RUNTIME_TRACE_SCHEMA for event in events)
        or any(
            event.outcome != "OK" or event.error_code is not None for event in events
        )
        or len({event.trace_id for event in events}) != 1
        or len({event.component for event in events}) != 4
        or evidence.binding_checks != (True, True, True, True, True, True)
        or evidence.changed_tables != (_CHANGED_TABLE,)
        or evidence.unchanged_tables != tuple(sorted(_TABLES - {_CHANGED_TABLE}))
        or evidence.canonical_domain_unchanged is not True
        or evidence.governance_before != (0, 0, 0, 0)
        or evidence.governance_after != (0, 0, 0, 0)
    ):
        raise RuntimeTraceEvidenceError("trace_contract_failed")
    return {
        "all_outcomes_ok": True,
        "binding_checks": 6,
        "component_count": 4,
        "event_count": 6,
        "expected_order": True,
        "governance_zero_delta": {
            "actions": 0,
            "approvals": 0,
            "executions": 0,
            "verifications": 0,
        },
        "single_correlation": True,
        "successful_run_cleanup": True,
        "write_semantics": {
            "canonical_domain_unchanged": True,
            "changed_table_count": 1,
            "transport_state_changed": True,
            "unchanged_table_count": 9,
            "whole_database_read_only_claimed": False,
        },
    }


def _capture_tree(
    root: Path,
) -> tuple[
    DirectoryIdentity,
    tuple[tuple[Path, FileIdentity], ...],
    tuple[tuple[Path, DirectoryIdentity], ...],
]:
    root_identity = _directory_identity(root, code="cleanup_failed")
    files: list[tuple[Path, FileIdentity]] = []
    directories: list[tuple[Path, DirectoryIdentity]] = []

    def visit(directory: Path) -> None:
        try:
            children = tuple(directory.iterdir())
        except OSError:
            raise RuntimeTraceEvidenceError("cleanup_failed") from None
        for child in children:
            if _is_link_like(child):
                raise RuntimeTraceEvidenceError("cleanup_failed")
            details = os.lstat(child)
            if stat.S_ISDIR(details.st_mode):
                identity = (details.st_dev, details.st_ino)
                visit(child)
                directories.append((child, identity))
            elif stat.S_ISREG(details.st_mode):
                files.append((child, _file_identity(details, code="cleanup_failed")))
            else:
                raise RuntimeTraceEvidenceError("cleanup_failed")

    visit(root)
    return root_identity, tuple(files), tuple(directories)


def _remove_owned_tree(
    root: Path, *, expected_root_identity: DirectoryIdentity | None = None
) -> None:
    if not os.path.lexists(root):
        return
    if (
        expected_root_identity is not None
        and _directory_identity(root, code="cleanup_failed") != expected_root_identity
    ):
        raise RuntimeTraceEvidenceError("cleanup_failed")
    root_identity, files, directories = _capture_tree(root)
    if expected_root_identity is not None and root_identity != expected_root_identity:
        raise RuntimeTraceEvidenceError("cleanup_failed")
    try:
        for path, identity in files:
            if _path_file_identity(path, code="cleanup_failed") != identity:
                raise RuntimeTraceEvidenceError("cleanup_failed")
            path.unlink()
        for path, identity in directories:
            if _directory_identity(path, code="cleanup_failed") != identity:
                raise RuntimeTraceEvidenceError("cleanup_failed")
            path.rmdir()
        if _directory_identity(root, code="cleanup_failed") != root_identity:
            raise RuntimeTraceEvidenceError("cleanup_failed")
        root.rmdir()
    except RuntimeTraceEvidenceError:
        raise
    except OSError:
        raise RuntimeTraceEvidenceError("cleanup_failed") from None


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
    except Exception:
        raise RuntimeTraceEvidenceError("trace_contract_failed") from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise RuntimeTraceEvidenceError("trace_contract_failed")
    return encoded


def _write_report(
    run: _RunDirectory,
    report: Mapping[str, object],
    *,
    token: str,
) -> tuple[int, str, FileIdentity]:
    if _TOKEN.fullmatch(token) is None:
        raise RuntimeTraceEvidenceError("report_write_failed")
    final_path = run.directory / REPORT_NAME
    temporary = run.directory / f".{REPORT_NAME}.{token}.tmp"
    temporary_identity: FileIdentity | None = None
    try:
        run.validate(code="report_write_failed")
        if os.path.lexists(final_path) or os.path.lexists(temporary):
            raise RuntimeTraceEvidenceError("report_write_failed")
        encoded = _canonical_bytes(report)
        written_identity: FileIdentity | None = None
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            details = os.fstat(stream.fileno())
            written_identity = _bound_path_identity(
                temporary, details, code="report_write_failed"
            )
        if (
            written_identity is None
            or _path_file_identity(temporary, code="report_write_failed")
            != written_identity
        ):
            raise RuntimeTraceEvidenceError("report_write_failed")
        temporary_identity = written_identity
        run.validate(code="report_write_failed")
        os.link(temporary, final_path, follow_symlinks=False)
        temporary_after_link = os.lstat(temporary)
        final_after_link = os.lstat(final_path)
        if (
            temporary_after_link.st_dev != final_after_link.st_dev
            or temporary_after_link.st_ino != final_after_link.st_ino
            or temporary_after_link.st_nlink != 2
            or final_after_link.st_nlink != 2
        ):
            raise RuntimeTraceEvidenceError("report_write_failed")
        with final_path.open("rb") as stream:
            linked = os.fstat(stream.fileno())
            if (
                linked.st_dev != final_after_link.st_dev
                or linked.st_ino != final_after_link.st_ino
                or linked.st_nlink != 2
            ):
                raise RuntimeTraceEvidenceError("report_write_failed")
            temporary.unlink()
            persisted = stream.read(MAX_REPORT_BYTES + 1)
            final_details = os.fstat(stream.fileno())
            final_identity = _bound_path_identity(
                final_path, final_details, code="report_write_failed"
            )
            if (
                persisted != encoded
                or final_details.st_nlink != 1
                or final_identity[5] != 1
            ):
                raise RuntimeTraceEvidenceError("report_write_failed")
        persisted_identity = final_identity
        if (
            _path_file_identity(final_path, code="report_write_failed")
            != persisted_identity
        ):
            raise RuntimeTraceEvidenceError("report_write_failed")
        run.validate(code="report_write_failed")
        return (
            len(persisted),
            hashlib.sha256(persisted).hexdigest(),
            persisted_identity,
        )
    except RuntimeTraceEvidenceError:
        raise
    except Exception:
        raise RuntimeTraceEvidenceError("report_write_failed") from None
    finally:
        try:
            if (
                temporary_identity is not None
                and os.path.lexists(temporary)
                and _path_file_identity(temporary, code="report_write_failed")
                == temporary_identity
            ):
                temporary.unlink()
        except Exception:
            pass


def _read_identity_bound_file(
    path: Path,
    *,
    expected_identity: FileIdentity,
    maximum_bytes: int,
    code: str,
) -> bytes:
    """Read through an owned handle and bind both path observations to it."""

    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            before_identity = _bound_path_identity(path, before, code=code)
            if before_identity != expected_identity:
                raise RuntimeTraceEvidenceError(code)
            content = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
            after_identity = _bound_path_identity(path, after, code=code)
            if len(content) > maximum_bytes or after_identity != expected_identity:
                raise RuntimeTraceEvidenceError(code)
        if _path_file_identity(path, code=code) != expected_identity:
            raise RuntimeTraceEvidenceError(code)
        return content
    except RuntimeTraceEvidenceError:
        raise
    except Exception:
        raise RuntimeTraceEvidenceError(code) from None


@dataclass
class _FixedDownloader:
    body: bytes

    def download(self, resource: Any, target: Path) -> Any:
        from telco_lab import DownloadReceipt

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.body)
        return DownloadReceipt(
            resource_id=resource.resource_id,
            filename=resource.filename,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            cached=False,
        )


def _fixed_bubbleran_csv() -> bytes:
    from telco_lab import BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP

    headers = [
        "",
        "timestamp",
        "ran_ue_id",
        "e2node_nb_id",
        *BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
        "timestamp_iso",
        "persistent_anomaly",
    ]
    instant = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    row: dict[str, object] = {
        "": 0,
        "timestamp": int(instant.timestamp()),
        "ran_ue_id": "answer-key-only-source",
        "e2node_nb_id": "50",
        "timestamp_iso": instant.replace(tzinfo=None).isoformat(),
        "persistent_anomaly": "True",
    }
    row.update(
        {
            name: f"{metric_index / 100:.2f}"
            for metric_index, name in enumerate(
                BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
                start=1,
            )
        }
    )
    row["mac_ul_bler"] = "0.20"
    writer.writerow(row)
    return stream.getvalue().encode("utf-8")


class _HeaderCaptureApp:
    def __init__(self, application: Any) -> None:
        self.application = application
        self.values: list[tuple[bytes, ...]] = []
        self._lock = threading.Lock()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == (
            "/local/v1/faults/replay"
        ):
            values = tuple(
                value
                for name, value in scope.get("headers", ())
                if name.lower() == b"x-networkagent-trace-id"
            )
            with self._lock:
                self.values.append(values)
        await self.application(scope, receive, send)


@contextmanager
def _serve(application: Any, listener: socket.socket) -> Iterator[int]:
    import uvicorn

    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="critical",
            lifespan="on",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeTraceEvidenceError("command_failed")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeTraceEvidenceError("command_failed")


def _table_snapshots(database_path: Path) -> dict[str, tuple[tuple[Any, ...], ...]]:
    import duckdb

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        if tables != _TABLES:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        return {
            table: tuple(
                connection.execute(f'SELECT * FROM "{table}" ORDER BY ALL').fetchall()
            )
            for table in sorted(tables)
        }
    finally:
        connection.close()


def _governance_counts(value: Any) -> tuple[int, int, int, int]:
    return (
        len(value.recommendations),
        len(value.approvals),
        len(value.action_runs),
        len(value.verification_runs),
    )


async def _read_replay_proof(
    repository: Any, event: Any, expected: str
) -> tuple[Any, bool, bool, bool]:
    current = await repository.find_active(source_event_id=event.source_event_id)
    if current is None:
        raise RuntimeTraceEvidenceError("trace_contract_failed")
    durable = (
        current.trace_id == expected
        and current.source_event_ids == (event.source_event_id,)
        and current.revision == 0
    )
    history = await repository.history(current.incident_id, limit=2, offset=0)
    audit = (
        len(history) == 1
        and history[0].revision == 0
        and history[0].from_status is None
        and history[0].trace_id == expected
    )
    associations = await repository.source_event_associations(
        current.incident_id, limit=1000
    )
    association = (
        len(associations) == 1
        and associations[0].source_event_id == event.source_event_id
        and associations[0].trace_id == expected
    )
    return current, durable, audit, association


def _artifact_data(response_text: str) -> dict[str, Any]:
    try:
        envelopes = [
            json.loads(line.removeprefix("data:").strip())
            for line in response_text.splitlines()
            if line.startswith("data:")
        ]
        results = [envelope["result"] for envelope in envelopes]
        artifact = next(
            item for item in results if item.get("kind") == "artifact-update"
        )
        return next(
            part["data"]
            for part in artifact["artifact"]["parts"]
            if part["kind"] == "data"
        )
    except Exception:
        raise RuntimeTraceEvidenceError("trace_contract_failed") from None


def _run_fixed_scenario(
    *,
    repository_root: Path,
    asset_root: Path,
    work_directory: Path,
    collector: RawEventCollector,
    clock: Callable[[], datetime],
) -> ScenarioEvidence:
    import httpx
    from telco_assurance_agent import AssuranceConfig, create_app, initialize_assurance
    from telco_domain import Incident, RcaResult
    from telco_lab import (
        BUBBLERAN_CSV_ADAPTER_ID,
        BUBBLERAN_DATASET_ID,
        BUBBLERAN_DATASET_VERSION,
        BUBBLERAN_SOURCE_LICENSE,
        FixtureCatalogProvider,
        LoopbackHttpReplaySink,
        ReplayPolicy,
        TelcoLab,
        adapt_bubbleran_persistent_interference_csv,
        build_replay_plan,
        run_paced_replay,
    )

    body = _fixed_bubbleran_csv()
    resource_name = "bubbleran.runtime-trace.anomalous.v1"
    provider = FixtureCatalogProvider(
        {
            "schema_version": "1.0",
            "catalog_id": "bubbleran-runtime-trace-evidence",
            "catalog_version": "1.0.0",
            "resources": [
                {
                    "resource_id": resource_name,
                    "dataset_id": BUBBLERAN_DATASET_ID,
                    "dataset_version": BUBBLERAN_DATASET_VERSION,
                    "filename": "anomalous.csv",
                    "source_url": "https://fixtures.example.test/anomalous.csv",
                    "allowed_hosts": ["fixtures.example.test"],
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "media_type": "text/csv",
                    "adapter": BUBBLERAN_CSV_ADAPTER_ID,
                    "license": {
                        "id": BUBBLERAN_SOURCE_LICENSE,
                        "name": "Creative Commons Attribution-ShareAlike 4.0",
                        "url": "https://creativecommons.org/licenses/by-sa/4.0/",
                        "evidence_url": "https://fixtures.example.test/LICENSE",
                        "evidence_sha256": "a" * 64,
                        "attribution": "BubbleRAN dataset authors",
                        "reviewed_at": "2026-08-31",
                        "acceptance_required": True,
                    },
                }
            ],
        }
    )
    lab = TelcoLab(
        provider,
        work_directory / "lab",
        downloader=_FixedDownloader(body),
    )
    artifact = lab.fetch(resource_name, accepted_license=BUBBLERAN_SOURCE_LICENSE)
    bundle = adapt_bubbleran_persistent_interference_csv(artifact.local_path)
    if bundle.manifest.observation_count != 1:
        raise RuntimeTraceEvidenceError("trace_contract_failed")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    database_path = work_directory / "assurance.duckdb"
    config = AssuranceConfig(
        database_path=database_path,
        performance_csv_path=asset_root / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=asset_root / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=asset_root / "data/rca-rules/lte",
        documents_dir=asset_root / "data/docs/lte",
        public_url=f"http://127.0.0.1:{port}/",
        actor="local-runtime-evidence",
        host="127.0.0.1",
        port=port,
    )
    initialize_assurance(config, reset=True, clock=clock)
    app = create_app(config, clock=clock, runtime_trace_sink=collector)
    capture = _HeaderCaptureApp(app)
    replay_start = datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)
    local_environment = {"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"}
    with _serve(capture, listener):
        endpoint = f"http://127.0.0.1:{port}/local/v1/faults/replay"
        policy = ReplayPolicy(
            endpoint=endpoint,
            action_mode="disabled",
            speed=100,
            max_events=1,
            max_rate_per_second=100,
            max_duration_seconds=30,
            max_payload_bytes=64 * 1024,
            max_total_payload_bytes=64 * 1024,
            max_resources=1,
            max_concurrency=1,
        )
        plan = build_replay_plan(
            lab,
            bundle,
            scenario="bubbleran-persistent-interference",
            replay_window_start=replay_start,
            policy=policy,
            environ=local_environment,
        )
        if len(plan.events) != 1:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        event = plan.events[0]
        expected = derive_local_replay_trace_id(event.source_event_id)
        sink = LoopbackHttpReplaySink(
            policy,
            environ=local_environment,
            timeout_seconds=5,
            runtime_trace_sink=collector,
            runtime_trace_clock=clock,
        )
        delivery = asyncio.run(run_paced_replay(plan, sink, deadline_seconds=10))
        if (
            delivery.plan_complete is not True
            or delivery.delivered_count != 1
            or delivery.error_code is not None
        ):
            raise RuntimeTraceEvidenceError("trace_contract_failed")

        repository = app.state.assurance_components.profile.incident_repository
        current, durable, audit, association = asyncio.run(
            _read_replay_proof(repository, event, expected)
        )
        if type(current) is not Incident:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        governance_before = _governance_counts(current)
        before = _table_snapshots(database_path)

        request_data = {
            "schema_version": "1.0",
            "message_type": "assurance_analyze_request",
            "message_id": "message-runtime-analyze",
            "workflow_id": "workflow-runtime-analyze",
            "trace_id": expected,
            "idempotency_key": "idempotency-runtime-analyze",
            "sent_at": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "incident_id": current.incident_id,
            "requested_report_version": 1,
        }
        wire_message = {
            "role": "user",
            "messageId": request_data["message_id"],
            "contextId": "context-runtime-analyze",
            "parts": [
                {"kind": "text", "text": "Run deterministic local RCA."},
                {"kind": "data", "data": request_data},
            ],
        }
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            timeout=10,
            trust_env=False,
        ) as client:
            response = client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": "rpc-runtime-analyze",
                    "method": "message/stream",
                    "params": {"message": wire_message},
                },
            )
        if response.status_code != 200:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        result = RcaResult.model_validate(_artifact_data(response.text))
        request_bound = request_data["trace_id"] == expected
        result_bound = (
            result.trace_id == expected
            and result.incident_id == current.incident_id
            and result.report.incident_id == current.incident_id
        )
        after = _table_snapshots(database_path)
        persisted_after = asyncio.run(repository.get(current.incident_id))
        if type(persisted_after) is not Incident:
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        governance_after = _governance_counts(persisted_after)

    changed = tuple(sorted(name for name in _TABLES if before[name] != after[name]))
    unchanged = tuple(sorted(name for name in _TABLES if before[name] == after[name]))
    header_bound = capture.values == [(expected.encode("ascii"),)]
    canonical_unchanged = (
        all(before[name] == after[name] for name in _TABLES - {_CHANGED_TABLE})
        and persisted_after == current
    )
    return ScenarioEvidence(
        business_success=True,
        binding_checks=(
            header_bound,
            durable,
            audit,
            association,
            request_bound,
            result_bound,
        ),
        changed_tables=changed,
        unchanged_tables=unchanged,
        canonical_domain_unchanged=canonical_unchanged,
        governance_before=governance_before,
        governance_after=governance_after,
    )


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
    repository_root: Path = REPOSITORY_ROOT,
    asset_root: Path | None = None,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    random_token: Callable[[], str] = lambda: secrets.token_hex(6),
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]] = (
        _default_process_runner
    ),
    scenario_runner: Callable[..., ScenarioEvidence] = _run_fixed_scenario,
    collector_factory: Callable[[Path], RawEventCollector] = RawEventCollector,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if arguments.approve_local_simulation is not True:
            raise RuntimeTraceEvidenceError("confirmation_required")
    except RuntimeTraceEvidenceError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2

    collector: RawEventCollector | None = None
    work_directory: Path | None = None
    work_identity: DirectoryIdentity | None = None
    operation_error: RuntimeTraceEvidenceError | None = None
    try:
        repository = repository_root.resolve(strict=True)
        source_before = _source_snapshot(process_runner, repository_root=repository)
        run, token = _create_run_directory(
            repository,
            utc_now=utc_now,
            random_token=random_token,
        )
        raw_path = run.directory / RAW_EVENT_NAME
        run.validate(code="trace_contract_failed")
        collector = collector_factory(raw_path)
        run.validate(code="trace_contract_failed")
        work_directory = run.directory / "work"
        os.mkdir(work_directory)
        work_identity = _directory_identity(
            work_directory, code="trace_contract_failed"
        )
        run.validate(code="trace_contract_failed")
        evidence = scenario_runner(
            repository_root=repository,
            asset_root=(
                repository if asset_root is None else asset_root.resolve(strict=True)
            ),
            work_directory=work_directory,
            collector=collector,
            clock=utc_now,
        )
        run.validate(code="trace_contract_failed")
        events = collector.close_and_validate()
        run.validate(code="trace_contract_failed")
        _remove_owned_tree(work_directory, expected_root_identity=work_identity)
        work_directory = None
        work_identity = None
        run.validate(code="trace_contract_failed")
        source_after = _source_snapshot(process_runner, repository_root=repository)
        proof = _validate_and_summarize(evidence, events)
        source = _source_binding(source_before, source_after)
        if (
            collector.final_identity is None
            or _path_file_identity(raw_path, code="trace_contract_failed")
            != collector.final_identity
        ):
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        report_body: dict[str, object] = {
            "classification": CLASSIFICATION,
            "coverage": {
                "delivered": [
                    "FIXED_SINGLE_EVENT_REAL_LOOPBACK",
                    "SIX_STAGE_RUNTIME_CORRELATION",
                    "DURABLE_TO_A2A_RCA_BINDING",
                    "ANALYZE_WRITE_SEMANTICS",
                    "SUCCESSFUL_RUN_EPHEMERAL_STATE_CLEANUP",
                ],
                "not_claimed": list(_NOT_CLAIMED),
            },
            "ok": True,
            "privacy": {
                "absolute_paths_recorded": False,
                "domain_identifiers_recorded": False,
                "raw_events_in_release_summary": False,
                "raw_payloads_recorded": False,
                "status": "PASS",
            },
            "proof": proof,
            "release": {
                "eligible": source["commit_bound"],
                "source_state": (
                    "COMMIT_BOUND"
                    if source["commit_bound"] is True
                    else "WORKTREE_ONLY"
                ),
            },
            "schema": SCHEMA,
            "scope": {
                "action_mode": "DISABLED",
                "analyze_semantics": "TRANSPORT_WRITE_DOMAIN_UNCHANGED",
                "execution": "SINGLE_PROCESS",
                "network": "REAL_LOOPBACK_TCP",
                "scenario": "FIXED_BUBBLERAN_SINGLE_EVENT",
            },
            "source": source,
        }
        if not _nested_keys(report_body).isdisjoint(_FORBIDDEN_REPORT_KEYS):
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        report_bytes, report_sha256, report_identity = _write_report(
            run, report_body, token=token
        )
        run.validate(code="trace_contract_failed")
        raw_bytes = _read_identity_bound_file(
            raw_path,
            expected_identity=collector.final_identity,
            maximum_bytes=MAX_RAW_STREAM_BYTES,
            code="trace_contract_failed",
        )
        persisted_report = _read_identity_bound_file(
            run.directory / REPORT_NAME,
            expected_identity=report_identity,
            maximum_bytes=MAX_REPORT_BYTES,
            code="report_write_failed",
        )
        if (
            len(raw_bytes.splitlines()) != 6
            or len(persisted_report) != report_bytes
            or hashlib.sha256(persisted_report).hexdigest() != report_sha256
            or json.loads(persisted_report) != report_body
        ):
            raise RuntimeTraceEvidenceError("trace_contract_failed")
        run.validate(code="trace_contract_failed")
        _write_json(
            output,
            {
                **report_body,
                "report": {
                    "bytes": report_bytes,
                    "filename": REPORT_NAME,
                    "sha256": report_sha256,
                },
            },
        )
        return 0
    except RuntimeTraceEvidenceError as exc:
        operation_error = exc
    except Exception as exc:
        code = getattr(exc, "code", "command_failed")
        operation_error = RuntimeTraceEvidenceError(code)
    finally:
        if collector is not None:
            collector.close_safely()
        if work_directory is not None and os.path.lexists(work_directory):
            if work_identity is None:
                operation_error = RuntimeTraceEvidenceError("cleanup_failed")
            else:
                try:
                    _remove_owned_tree(
                        work_directory,
                        expected_root_identity=work_identity,
                    )
                except Exception:
                    operation_error = RuntimeTraceEvidenceError("cleanup_failed")
    assert operation_error is not None
    _write_json(errors, _error_payload(operation_error.code))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
