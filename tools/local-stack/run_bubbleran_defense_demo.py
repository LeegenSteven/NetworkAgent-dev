#!/usr/bin/env python3
"""Produce bounded defense evidence for one fixed local BubbleRAN chain."""

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
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, Iterator, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "networkagent-local-bubbleran-defense-evidence/1.0"
CLASSIFICATION = "LOCAL_BUBBLERAN_VERTICAL_DEFENSE_EVIDENCE"
REPORT_NAME = "local-bubbleran-defense-report.json"
MAX_REPORT_BYTES = 64 * 1024
MAX_GIT_OUTPUT_BYTES = 64 * 1024
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")

_FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "body",
        "event_id",
        "events",
        "ground_truth",
        "incident_id",
        "label",
        "labels",
        "path",
        "ran_ue_id",
        "raw",
        "row",
        "rows",
        "source_event_id",
        "source_url",
        "trace_id",
        "ue",
        "ue_id",
    }
)
_NOT_CLAIMED = [
    "COMPLETE_UPSTREAM_BENCHMARK",
    "RCA_EVAL_MULTI_SOURCE",
    "CROSS_EVENT_AGGREGATION",
    "PRODUCTION_ACCURACY",
    "REAL_NETWORK_REMEDIATION",
    "CLOUD_OR_GCP_DEPLOYMENT",
    "OPEN_TELEMETRY_OR_DISTRIBUTED_TRACE",
    "UNIFIED_DASHBOARD",
    "GATE_E_OR_G5_CLOSURE",
    "P3E_OR_S7_OVERALL_CLOSURE",
]

DirectoryIdentity = tuple[int, int]
FileIdentity = tuple[int, int, int, int, int, int]


class BubbleRanDefenseError(Exception):
    """Stable, non-reflective failure at the evidence boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_MESSAGES = {
    "cleanup_failed": "local BubbleRAN defense cleanup failed safely",
    "command_failed": "local BubbleRAN defense command failed safely",
    "confirmation_required": "explicit local simulation approval is required",
    "contract_failed": "local BubbleRAN defense evidence violated its fixed contract",
    "invalid_arguments": "command arguments are invalid",
    "offline_required": "explicit offline mode is required",
    "report_write_failed": "local BubbleRAN defense report could not be written safely",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise BubbleRanDefenseError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-bubbleran-defense", add_help=False
    )
    parser.add_argument("--offline", action="store_true")
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
        return bool(getattr(details, "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _directory_identity(path: Path, *, code: str) -> DirectoryIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise BubbleRanDefenseError(code) from None
    if _is_link_like(path) or not stat.S_ISDIR(details.st_mode):
        raise BubbleRanDefenseError(code)
    return details.st_dev, details.st_ino


def _file_identity(details: os.stat_result, *, code: str) -> FileIdentity:
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise BubbleRanDefenseError(code)
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
        raise BubbleRanDefenseError(code) from None
    if _is_link_like(path):
        raise BubbleRanDefenseError(code)
    return _file_identity(details, code=code)


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
        raise BubbleRanDefenseError(code)
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
        evidence_root = local_root / "networkagent-bubbleran-defense"
        if self.directory.parent != evidence_root:
            raise BubbleRanDefenseError(code)
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
            raise BubbleRanDefenseError(code)


def _plain_mkdir(path: Path, *, code: str) -> None:
    try:
        if os.path.lexists(path):
            _directory_identity(path, code=code)
            return
        os.mkdir(path)
        _directory_identity(path, code=code)
    except BubbleRanDefenseError:
        raise
    except OSError:
        raise BubbleRanDefenseError(code) from None


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
        evidence_root = local_root / "networkagent-bubbleran-defense"
        _plain_mkdir(local_root, code="report_write_failed")
        _plain_mkdir(evidence_root, code="report_write_failed")
        candidate = evidence_root / f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{token}"
        if os.path.lexists(candidate):
            raise BubbleRanDefenseError("report_write_failed")
        os.mkdir(candidate)
        chain = tuple(
            _directory_identity(item, code="report_write_failed")
            for item in (root, local_root, evidence_root, candidate)
        )
        result = _RunDirectory(root, candidate, chain)  # type: ignore[arg-type]
        result.validate(code="report_write_failed")
        return result, token
    except BubbleRanDefenseError:
        raise
    except Exception:
        raise BubbleRanDefenseError("report_write_failed") from None


@dataclass(frozen=True, slots=True)
class _TreeManifest:
    root: Path
    root_identity: DirectoryIdentity
    files: tuple[tuple[Path, FileIdentity], ...]
    directories: tuple[tuple[Path, DirectoryIdentity], ...]


def _capture_owned_tree(
    root: Path, *, expected_root_identity: DirectoryIdentity
) -> _TreeManifest:
    root_identity = _directory_identity(root, code="cleanup_failed")
    if root_identity != expected_root_identity:
        raise BubbleRanDefenseError("cleanup_failed")
    files: list[tuple[Path, FileIdentity]] = []
    directories: list[tuple[Path, DirectoryIdentity]] = []

    def visit(directory: Path) -> None:
        try:
            children = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
        except OSError:
            raise BubbleRanDefenseError("cleanup_failed") from None
        for child in children:
            if _is_link_like(child):
                raise BubbleRanDefenseError("cleanup_failed")
            try:
                details = os.lstat(child)
            except OSError:
                raise BubbleRanDefenseError("cleanup_failed") from None
            if stat.S_ISDIR(details.st_mode):
                identity = _directory_identity(child, code="cleanup_failed")
                visit(child)
                directories.append((child, identity))
            elif stat.S_ISREG(details.st_mode):
                files.append((child, _file_identity(details, code="cleanup_failed")))
            else:
                raise BubbleRanDefenseError("cleanup_failed")

    visit(root)
    return _TreeManifest(
        root=root,
        root_identity=root_identity,
        files=tuple(files),
        directories=tuple(directories),
    )


def _manifest_signature(manifest: _TreeManifest) -> tuple[object, ...]:
    return (
        manifest.root_identity,
        tuple((path, identity) for path, identity in manifest.files),
        tuple((path, identity) for path, identity in manifest.directories),
    )


def _remove_captured_tree(manifest: _TreeManifest) -> None:
    """Remove only a fully captured tree; never discover ownership on failure."""

    current = _capture_owned_tree(
        manifest.root, expected_root_identity=manifest.root_identity
    )
    if _manifest_signature(current) != _manifest_signature(manifest):
        raise BubbleRanDefenseError("cleanup_failed")
    try:
        for path, identity in manifest.files:
            if _path_file_identity(path, code="cleanup_failed") != identity:
                raise BubbleRanDefenseError("cleanup_failed")
        for path, identity in manifest.directories:
            if _directory_identity(path, code="cleanup_failed") != identity:
                raise BubbleRanDefenseError("cleanup_failed")
        if (
            _directory_identity(manifest.root, code="cleanup_failed")
            != manifest.root_identity
        ):
            raise BubbleRanDefenseError("cleanup_failed")

        for path, identity in manifest.files:
            if _path_file_identity(path, code="cleanup_failed") != identity:
                raise BubbleRanDefenseError("cleanup_failed")
            path.unlink()
        for path, identity in manifest.directories:
            if _directory_identity(path, code="cleanup_failed") != identity:
                raise BubbleRanDefenseError("cleanup_failed")
            path.rmdir()
        if (
            _directory_identity(manifest.root, code="cleanup_failed")
            != manifest.root_identity
        ):
            raise BubbleRanDefenseError("cleanup_failed")
        manifest.root.rmdir()
    except BubbleRanDefenseError:
        raise
    except OSError:
        raise BubbleRanDefenseError("cleanup_failed") from None


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
        raise BubbleRanDefenseError("contract_failed") from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise BubbleRanDefenseError("contract_failed")
    return encoded


def _write_report(
    run: _RunDirectory,
    report: Mapping[str, object],
    *,
    token: str,
) -> tuple[int, str, FileIdentity]:
    if _TOKEN.fullmatch(token) is None:
        raise BubbleRanDefenseError("report_write_failed")
    final_path = run.directory / REPORT_NAME
    temporary = run.directory / f".{REPORT_NAME}.{token}.tmp"
    temporary_identity: FileIdentity | None = None
    try:
        run.validate(code="report_write_failed")
        if os.path.lexists(final_path) or os.path.lexists(temporary):
            raise BubbleRanDefenseError("report_write_failed")
        encoded = _canonical_bytes(report)
        with temporary.open("x+b") as stream:
            details = os.fstat(stream.fileno())
            created_identity = _bound_path_identity(
                temporary, details, code="report_write_failed"
            )
            temporary_identity = created_identity
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            written_identity = _bound_path_identity(
                temporary, os.fstat(stream.fileno()), code="report_write_failed"
            )
        if (
            _path_file_identity(temporary, code="report_write_failed")
            != written_identity
        ):
            raise BubbleRanDefenseError("report_write_failed")
        temporary_identity = written_identity
        run.validate(code="report_write_failed")
        if os.path.lexists(final_path):
            raise BubbleRanDefenseError("report_write_failed")
        os.link(temporary, final_path, follow_symlinks=False)
        source_details = os.lstat(temporary)
        final_details = os.lstat(final_path)
        if (
            source_details.st_dev != final_details.st_dev
            or source_details.st_ino != final_details.st_ino
            or source_details.st_nlink != 2
            or final_details.st_nlink != 2
        ):
            raise BubbleRanDefenseError("report_write_failed")
        with final_path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                opened.st_dev != final_details.st_dev
                or opened.st_ino != final_details.st_ino
                or opened.st_nlink != 2
            ):
                raise BubbleRanDefenseError("report_write_failed")
            temporary.unlink()
            persisted = stream.read(MAX_REPORT_BYTES + 1)
            final_identity = _bound_path_identity(
                final_path, os.fstat(stream.fileno()), code="report_write_failed"
            )
            if persisted != encoded or final_identity[5] != 1:
                raise BubbleRanDefenseError("report_write_failed")
        if (
            _path_file_identity(final_path, code="report_write_failed")
            != final_identity
        ):
            raise BubbleRanDefenseError("report_write_failed")
        run.validate(code="report_write_failed")
        return len(persisted), hashlib.sha256(persisted).hexdigest(), final_identity
    except BubbleRanDefenseError:
        raise
    except Exception:
        raise BubbleRanDefenseError("report_write_failed") from None
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
    try:
        with path.open("rb") as stream:
            before = _bound_path_identity(path, os.fstat(stream.fileno()), code=code)
            if before != expected_identity:
                raise BubbleRanDefenseError(code)
            content = stream.read(maximum_bytes + 1)
            after = _bound_path_identity(path, os.fstat(stream.fileno()), code=code)
            if len(content) > maximum_bytes or after != expected_identity:
                raise BubbleRanDefenseError(code)
        if _path_file_identity(path, code=code) != expected_identity:
            raise BubbleRanDefenseError(code)
        return content
    except BubbleRanDefenseError:
        raise
    except Exception:
        raise BubbleRanDefenseError(code) from None


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


@dataclass(frozen=True, slots=True)
class TerminalEvidence:
    branch: str
    terminal: str
    verification: str
    action_runs: int
    verification_runs: int


@dataclass(frozen=True, slots=True)
class ScenarioEvidence:
    generated_records: int
    canonical_cases: int
    source_associations: int
    independent_associations: bool
    checkpoint_first: tuple[int, int, int]
    checkpoint_reopened: tuple[int, int, int]
    terminal: tuple[TerminalEvidence, ...]
    action_runs: int
    verification_runs: int
    action_type: str
    side_effects: bool
    bypass_delivered: int
    bypass_business_delta: tuple[int, int, int, int]

    @classmethod
    def fixed_success(cls) -> ScenarioEvidence:
        return cls(
            generated_records=4,
            canonical_cases=4,
            source_associations=4,
            independent_associations=True,
            checkpoint_first=(4, 4, 4),
            checkpoint_reopened=(0, 0, 0),
            terminal=(
                TerminalEvidence("APPROVED_PASS", "RESOLVED", "PASSED", 1, 1),
                TerminalEvidence("APPROVED_FAIL", "REOPENED", "FAILED", 1, 1),
                TerminalEvidence("REJECTED", "REJECTED", "NOT_RUN", 0, 0),
                TerminalEvidence("APPROVAL_EXPIRED", "FAILED", "NOT_RUN", 0, 0),
            ),
            action_runs=2,
            verification_runs=2,
            action_type="LOCAL_SIMULATION",
            side_effects=False,
            bypass_delivered=4,
            bypass_business_delta=(0, 0, 0, 0),
        )


def expected_proof() -> dict[str, object]:
    return {
        "canonical_cases": {
            "count": 4,
            "independent": True,
            "source_associations": 4,
        },
        "checkpoint": {
            "first": {"attempted": 4, "delivered": 4, "selected": 4},
            "reopened": {"attempted": 0, "delivered": 0, "selected": 0},
            "settled": True,
        },
        "governance": {
            "action_contract": {
                "side_effects": False,
                "type": "LOCAL_SIMULATION",
            },
            "action_runs": 2,
            "terminal": [
                {
                    "action_runs": 1,
                    "branch": "APPROVED_PASS",
                    "terminal": "RESOLVED",
                    "verification": "PASSED",
                    "verification_runs": 1,
                },
                {
                    "action_runs": 1,
                    "branch": "APPROVED_FAIL",
                    "terminal": "REOPENED",
                    "verification": "FAILED",
                    "verification_runs": 1,
                },
                {
                    "action_runs": 0,
                    "branch": "REJECTED",
                    "terminal": "REJECTED",
                    "verification": "NOT_RUN",
                    "verification_runs": 0,
                },
                {
                    "action_runs": 0,
                    "branch": "APPROVAL_EXPIRED",
                    "terminal": "FAILED",
                    "verification": "NOT_RUN",
                    "verification_runs": 0,
                },
            ],
            "verification_runs": 2,
        },
        "settled_bypass": {
            "business_record_delta": {
                "audit": 0,
                "cases": 0,
                "idempotency": 0,
                "source_associations": 0,
            },
            "delivered": 4,
        },
    }


def _validate_evidence(evidence: ScenarioEvidence) -> dict[str, object]:
    if (
        type(evidence) is not ScenarioEvidence
        or evidence != ScenarioEvidence.fixed_success()
    ):
        raise BubbleRanDefenseError("contract_failed")
    return expected_proof()


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def _error_payload(code: str) -> dict[str, object]:
    stable = code if code in _ERROR_MESSAGES else "command_failed"
    return {
        "error": {"code": stable, "message": _ERROR_MESSAGES[stable]},
        "ok": False,
        "schema": SCHEMA,
    }


@dataclass(slots=True)
class _MutableClock:
    instant: datetime

    def __call__(self) -> datetime:
        return self.instant

    def advance(self, delta: timedelta) -> None:
        self.instant += delta


class _FixedDownloader:
    def __init__(self, body: bytes, receipt_type: type[Any]) -> None:
        self._body = body
        self._receipt_type = receipt_type
        self.calls = 0

    def download(self, resource: Any, target: Path) -> Any:
        self.calls += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._body)
        return self._receipt_type(
            resource_id=resource.resource_id,
            filename=resource.filename,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            cached=False,
        )


def _generated_bubbleran_csv(column_names: Sequence[str]) -> bytes:
    source_start = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    headers = [
        "",
        "timestamp",
        "ran_ue_id",
        "e2node_nb_id",
        *column_names,
        "timestamp_iso",
        "persistent_anomaly",
    ]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for record_index in range(4):
        instant = source_start + timedelta(seconds=record_index)
        record: dict[str, object] = {
            "": record_index,
            "timestamp": int(instant.timestamp()),
            "ran_ue_id": "schema-fixture-private-value",
            "e2node_nb_id": "50",
            "timestamp_iso": instant.replace(tzinfo=None).isoformat(),
            "persistent_anomaly": "True",
        }
        record.update(
            {
                name: f"{(metric_index + record_index) / 100:.2f}"
                for metric_index, name in enumerate(column_names, start=1)
            }
        )
        record["mac_ul_bler"] = "0.20"
        writer.writerow(record)
    return stream.getvalue().encode("utf-8")


def _fixed_catalog(
    body: bytes,
    *,
    catalog_type: type[Any],
    adapter_id: str,
    dataset_id: str,
    dataset_version: str,
    source_license: str,
) -> Any:
    return catalog_type(
        {
            "schema_version": "1.0",
            "catalog_id": "bubbleran-defense-demo",
            "catalog_version": "1.0.0",
            "resources": [
                {
                    "resource_id": "bubbleran.defense.generated.v1",
                    "dataset_id": dataset_id,
                    "dataset_version": dataset_version,
                    "filename": "generated.csv",
                    "source_url": "https://fixtures.example.test/generated.csv",
                    "allowed_hosts": ["fixtures.example.test"],
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "media_type": "text/csv",
                    "adapter": adapter_id,
                    "license": {
                        "id": source_license,
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


@contextmanager
def _serve(application: Any, listener: socket.socket) -> Iterator[int]:
    import uvicorn

    selected_port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=selected_port,
            access_log=False,
            log_level="critical",
            lifespan="off",
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
        listener.close()
        raise BubbleRanDefenseError("contract_failed")
    try:
        yield selected_port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise BubbleRanDefenseError("contract_failed")


def _business_counts(database_path: Path) -> tuple[int, int, int, int]:
    import duckdb

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "canonical_incidents",
                "canonical_incident_audit",
                "canonical_incident_source_events",
                "canonical_incident_idempotency",
            )
        )
    finally:
        connection.close()


async def _prepare(client: Any, case_id: str, suffix: str) -> dict[str, Any]:
    response = await client.post(
        f"/local/v1/incidents/{case_id}/prepare",
        headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
        json={
            "idempotency_key": f"prepare-defense-{suffix}",
            "actor": "local-governance",
        },
    )
    if response.status_code != 200:
        raise BubbleRanDefenseError("contract_failed")
    data = response.json()["data"]
    if not (
        data["incident"]["status"] == "AWAITING_APPROVAL"
        and data["rca"]["conclusion"] == "CONCLUSIVE"
        and data["action"]["action_type"] == "LOCAL_SIMULATION"
        and data["approval"]["status"] == "PENDING"
    ):
        raise BubbleRanDefenseError("contract_failed")
    return data


async def _decide(
    client: Any,
    case_id: str,
    suffix: str,
    prepared: Mapping[str, Any],
    *,
    approve: bool,
) -> dict[str, Any]:
    response = await client.post(
        f"/local/v1/incidents/{case_id}/decide",
        headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
        json={
            "idempotency_key": f"decide-defense-{suffix}",
            "actor": "local-defense-operator",
            "reason": "Review the fixed side-effect-free local simulation",
            "approve": approve,
            "expected_action_hash": prepared["action"]["action_hash"],
            "expected_revision": prepared["incident"]["revision"],
        },
    )
    if response.status_code != 200:
        raise BubbleRanDefenseError("contract_failed")
    return response.json()["data"]


async def _execute(
    client: Any,
    case_id: str,
    suffix: str,
    *,
    verification_passed: bool,
) -> dict[str, Any]:
    response = await client.post(
        f"/local/v1/incidents/{case_id}/execute",
        headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
        json={
            "idempotency_key": f"execute-defense-{suffix}",
            "actor": "local-defense-operator",
            "verification_passed": verification_passed,
        },
    )
    if response.status_code != 200:
        raise BubbleRanDefenseError("contract_failed")
    return response.json()["data"]


def _run_fixed_scenario(
    *,
    repository_root: Path,
    asset_root: Path,
    work_directory: Path,
    clock: Callable[[], datetime],
) -> ScenarioEvidence:
    del repository_root, clock
    import httpx

    from telco_assurance_agent import (
        AssuranceConfig,
        create_app,
        initialize_assurance,
    )
    from telco_domain import Incident, IncidentStatus
    from telco_lab import (
        BUBBLERAN_CSV_ADAPTER_ID,
        BUBBLERAN_DATASET_ID,
        BUBBLERAN_DATASET_VERSION,
        BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
        BUBBLERAN_SOURCE_LICENSE,
        DownloadReceipt,
        FixtureCatalogProvider,
        LoopbackHttpReplaySink,
        ReplayPolicy,
        TelcoLab,
        adapt_bubbleran_persistent_interference_csv,
        build_replay_plan,
        load_replay_checkpoint,
        run_paced_replay,
        run_persistent_paced_replay,
    )

    local_environment = {"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"}
    replay_start = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
    mutable_clock = _MutableClock(replay_start + timedelta(minutes=1))
    body = _generated_bubbleran_csv(tuple(BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP))
    fixture_path = work_directory / "generated.csv"
    fixture_path.write_bytes(body)
    provider = _fixed_catalog(
        body,
        catalog_type=FixtureCatalogProvider,
        adapter_id=BUBBLERAN_CSV_ADAPTER_ID,
        dataset_id=BUBBLERAN_DATASET_ID,
        dataset_version=BUBBLERAN_DATASET_VERSION,
        source_license=BUBBLERAN_SOURCE_LICENSE,
    )
    downloader = _FixedDownloader(body, DownloadReceipt)
    lab = TelcoLab(
        provider,
        work_directory / "lab",
        downloader=downloader,  # type: ignore[arg-type]
    )
    artifact = lab.fetch(
        "bubbleran.defense.generated.v1",
        accepted_license=BUBBLERAN_SOURCE_LICENSE,
    )
    bundle = adapt_bubbleran_persistent_interference_csv(artifact.local_path)
    if downloader.calls != 1 or bundle.manifest.observation_count != 4:
        raise BubbleRanDefenseError("contract_failed")

    database_path = work_directory / "local-defense.duckdb"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    selected_port = int(listener.getsockname()[1])
    config = AssuranceConfig(
        database_path=database_path,
        performance_csv_path=asset_root / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=asset_root / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=asset_root / "data/rca-rules/lte",
        documents_dir=asset_root / "data/docs/lte",
        public_url=f"http://127.0.0.1:{selected_port}/",
        actor="local-assurance-service",
        host="127.0.0.1",
        port=selected_port,
    )
    initialize_assurance(config, clock=mutable_clock)
    app = create_app(config, clock=mutable_clock)

    with _serve(app, listener) as port:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False
        ) as probe:
            if not all(
                response.status_code == 200
                for response in (
                    probe.get("/local/v1/healthz"),
                    probe.get("/local/v1/readyz"),
                    probe.get("/local/v1/version"),
                )
            ):
                raise BubbleRanDefenseError("contract_failed")

        policy = ReplayPolicy(
            endpoint=f"http://127.0.0.1:{port}/local/v1/faults/replay",
            action_mode="disabled",
            speed=100,
            max_events=4,
            max_rate_per_second=100,
            max_duration_seconds=30,
            max_payload_bytes=64 * 1024,
            max_total_payload_bytes=512 * 1024,
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
        if len(plan.events) != 4:
            raise BubbleRanDefenseError("contract_failed")
        if len({item.source_event_id for item in plan.events}) != 4:
            raise BubbleRanDefenseError("contract_failed")
        serialized_wire = json.dumps(
            [item.sink_payload() for item in plan.events],
            default=str,
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        if any(
            forbidden in serialized_wire
            for forbidden in (
                "persistent_anomaly",
                "ground_truth",
                "schema-fixture-private-value",
                "ran_ue_id",
                "e2node_nb_id",
            )
        ):
            raise BubbleRanDefenseError("contract_failed")

        sink = LoopbackHttpReplaySink(
            policy, environ=local_environment, timeout_seconds=5
        )
        checkpoint_workspace = work_directory / "checkpoint-workspace"
        checkpoint_workspace.mkdir()
        checkpoint_directory = checkpoint_workspace / "checkpoints"
        first = asyncio.run(
            run_persistent_paced_replay(
                plan,
                sink,
                workspace=checkpoint_workspace,
                checkpoint_directory=checkpoint_directory,
            )
        )
        if not (
            first.plan_complete is True
            and first.selected_count == 4
            and first.attempted_count == 4
            and first.delivered_count == 4
            and first.retry_count == 0
            and first.error_code is None
        ):
            raise BubbleRanDefenseError("contract_failed")
        persisted_checkpoint = load_replay_checkpoint(
            plan,
            workspace=checkpoint_workspace,
            checkpoint_directory=checkpoint_directory,
        )
        if (
            persisted_checkpoint != first.checkpoint
            or persisted_checkpoint.sequence_number != 4
        ):
            raise BubbleRanDefenseError("contract_failed")
        reopened = asyncio.run(
            run_persistent_paced_replay(
                plan,
                sink,
                workspace=checkpoint_workspace,
                checkpoint_directory=checkpoint_directory,
            )
        )
        if not (
            reopened.plan_complete is True
            and reopened.selected_count == 0
            and reopened.attempted_count == 0
            and reopened.delivered_count == 0
            and reopened.retry_count == 0
            and reopened.error_code is None
            and reopened.checkpoint == persisted_checkpoint
        ):
            raise BubbleRanDefenseError("contract_failed")

        repository = app.state.assurance_components.profile.incident_repository

        async def govern() -> tuple[Incident, ...]:
            cases: list[Incident] = []
            for replay_item in plan.events:
                current = await repository.find_active(
                    source_event_id=replay_item.source_event_id
                )
                if (
                    type(current) is not Incident
                    or current.technology.value != "5G_SA"
                    or len(current.violated_kpis) != 1
                    or current.violated_kpis[0].kpi_name != "ran.mac.ul_bler"
                ):
                    raise BubbleRanDefenseError("contract_failed")
                cases.append(current)
            if len({item.incident_id for item in cases}) != 4:
                raise BubbleRanDefenseError("contract_failed")

            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                trust_env=False,
            ) as client:
                prepared = await _prepare(client, cases[0].incident_id, "pass")
                approved = await _decide(
                    client,
                    cases[0].incident_id,
                    "pass",
                    prepared,
                    approve=True,
                )
                if not (
                    approved["incident"]["status"] == "REMEDIATING"
                    and approved["approval"]["status"] == "APPROVED"
                ):
                    raise BubbleRanDefenseError("contract_failed")
                resolved = await _execute(
                    client,
                    cases[0].incident_id,
                    "pass",
                    verification_passed=True,
                )
                if not (
                    resolved["incident"]["status"] == "RESOLVED"
                    and resolved["verification"]["status"] == "PASSED"
                ):
                    raise BubbleRanDefenseError("contract_failed")

                prepared = await _prepare(client, cases[1].incident_id, "fail")
                approved_failure = await _decide(
                    client,
                    cases[1].incident_id,
                    "fail",
                    prepared,
                    approve=True,
                )
                if not (
                    approved_failure["incident"]["status"] == "REMEDIATING"
                    and approved_failure["approval"]["status"] == "APPROVED"
                ):
                    raise BubbleRanDefenseError("contract_failed")
                failed = await _execute(
                    client,
                    cases[1].incident_id,
                    "fail",
                    verification_passed=False,
                )
                if not (
                    failed["incident"]["status"] == "REOPENED"
                    and failed["verification"]["status"] == "FAILED"
                ):
                    raise BubbleRanDefenseError("contract_failed")

                prepared = await _prepare(client, cases[2].incident_id, "reject")
                rejected = await _decide(
                    client,
                    cases[2].incident_id,
                    "reject",
                    prepared,
                    approve=False,
                )
                if not (
                    rejected["incident"]["status"] == "REJECTED"
                    and rejected["approval"]["status"] == "REJECTED"
                    and rejected["action_runs"] == []
                    and rejected["verification"] is None
                ):
                    raise BubbleRanDefenseError("contract_failed")

                prepared = await _prepare(client, cases[3].incident_id, "expiry")
                approved_expiry = await _decide(
                    client,
                    cases[3].incident_id,
                    "expiry",
                    prepared,
                    approve=True,
                )
                if not (
                    approved_expiry["incident"]["status"] == "REMEDIATING"
                    and approved_expiry["approval"]["status"] == "APPROVED"
                ):
                    raise BubbleRanDefenseError("contract_failed")
                mutable_clock.advance(timedelta(minutes=16))
                expired = await _execute(
                    client,
                    cases[3].incident_id,
                    "expiry",
                    verification_passed=True,
                )
                if not (
                    expired["incident"]["status"] == "FAILED"
                    and expired["action_runs"] == []
                    and expired["verification"] is None
                ):
                    raise BubbleRanDefenseError("contract_failed")

            persisted: list[Incident] = []
            for item in cases:
                current = await repository.get(item.incident_id)
                if type(current) is not Incident:
                    raise BubbleRanDefenseError("contract_failed")
                persisted.append(current)
            return tuple(persisted)

        governed = asyncio.run(govern())
        if tuple(item.status for item in governed) != (
            IncidentStatus.RESOLVED,
            IncidentStatus.REOPENED,
            IncidentStatus.REJECTED,
            IncidentStatus.FAILED,
        ):
            raise BubbleRanDefenseError("contract_failed")
        action_runs = sum(len(item.action_runs) for item in governed)
        verification_runs = sum(len(item.verification_runs) for item in governed)
        branches = (
            "APPROVED_PASS",
            "APPROVED_FAIL",
            "REJECTED",
            "APPROVAL_EXPIRED",
        )
        terminal = tuple(
            TerminalEvidence(
                branch=branch,
                terminal=item.status.value,
                verification=(
                    item.verification_runs[-1].status.value
                    if item.verification_runs
                    else "NOT_RUN"
                ),
                action_runs=len(item.action_runs),
                verification_runs=len(item.verification_runs),
            )
            for branch, item in zip(branches, governed, strict=True)
        )
        if not (
            action_runs == 2
            and verification_runs == 2
            and terminal == ScenarioEvidence.fixed_success().terminal
            and all(
                run.status.value == "SUCCEEDED"
                and run.metadata == {"mode": "simulation", "side_effects": False}
                for item in governed
                for run in item.action_runs
            )
        ):
            raise BubbleRanDefenseError("contract_failed")

        before_bypass = _business_counts(database_path)
        bypass = asyncio.run(run_paced_replay(plan, sink))
        after_bypass = _business_counts(database_path)
        if not (
            bypass.plan_complete is True
            and bypass.selected_count == 4
            and bypass.attempted_count == 4
            and bypass.delivered_count == 4
            and bypass.retry_count == 0
            and bypass.error_code is None
            and before_bypass == after_bypass
        ):
            raise BubbleRanDefenseError("contract_failed")

        async def verify_unchanged() -> tuple[Incident, ...]:
            result: list[Incident] = []
            for item in governed:
                current = await repository.get(item.incident_id)
                if type(current) is not Incident:
                    raise BubbleRanDefenseError("contract_failed")
                result.append(current)
            return tuple(result)

        if asyncio.run(verify_unchanged()) != governed:
            raise BubbleRanDefenseError("contract_failed")

    association_count = before_bypass[2]
    if association_count != 4 or before_bypass[0] != 4:
        raise BubbleRanDefenseError("contract_failed")
    delta = tuple(after - before for before, after in zip(before_bypass, after_bypass))
    return ScenarioEvidence(
        generated_records=4,
        canonical_cases=before_bypass[0],
        source_associations=association_count,
        independent_associations=True,
        checkpoint_first=(
            first.selected_count,
            first.attempted_count,
            first.delivered_count,
        ),
        checkpoint_reopened=(
            reopened.selected_count,
            reopened.attempted_count,
            reopened.delivered_count,
        ),
        terminal=terminal,
        action_runs=action_runs,
        verification_runs=verification_runs,
        action_type="LOCAL_SIMULATION",
        side_effects=False,
        bypass_delivered=bypass.delivered_count,
        bypass_business_delta=delta,
    )


def _validate_run_contents(
    run: _RunDirectory, expected: set[str], *, code: str
) -> None:
    run.validate(code=code)
    try:
        children = tuple(run.directory.iterdir())
    except OSError:
        raise BubbleRanDefenseError(code) from None
    if {item.name for item in children} != expected:
        raise BubbleRanDefenseError(code)
    for child in children:
        if _is_link_like(child):
            raise BubbleRanDefenseError(code)
        details = os.lstat(child)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise BubbleRanDefenseError(code)


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
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if arguments.offline is not True:
            raise BubbleRanDefenseError("offline_required")
        if arguments.approve_local_simulation is not True:
            raise BubbleRanDefenseError("confirmation_required")
    except BubbleRanDefenseError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2

    try:
        repository = repository_root.resolve(strict=True)
        assets = repository if asset_root is None else asset_root.resolve(strict=True)
        source_before = _source_snapshot(process_runner, repository_root=repository)
        run, token = _create_run_directory(
            repository,
            utc_now=utc_now,
            random_token=random_token,
        )
        run.validate(code="contract_failed")
        work_directory = run.directory / "work"
        if os.path.lexists(work_directory):
            raise BubbleRanDefenseError("contract_failed")
        os.mkdir(work_directory)
        work_identity = _directory_identity(work_directory, code="contract_failed")
        run.validate(code="contract_failed")

        evidence = scenario_runner(
            repository_root=repository,
            asset_root=assets,
            work_directory=work_directory,
            clock=utc_now,
        )
        proof = _validate_evidence(evidence)
        run.validate(code="contract_failed")

        # Ownership is captured only after a successful fixed scenario. If the
        # scenario or first capture fails, the unknown residue is deliberately
        # preserved; no finally block re-discovers and deletes it.
        manifest = _capture_owned_tree(
            work_directory, expected_root_identity=work_identity
        )
        _remove_captured_tree(manifest)
        if os.path.lexists(work_directory):
            raise BubbleRanDefenseError("cleanup_failed")
        run.validate(code="contract_failed")
        _validate_run_contents(run, set(), code="contract_failed")

        source_after = _source_snapshot(process_runner, repository_root=repository)
        source = _source_binding(source_before, source_after)
        report_body: dict[str, object] = {
            "classification": CLASSIFICATION,
            "coverage": {
                "delivered": [
                    "FOUR_RECORD_CODE_GENERATED_FIXTURE",
                    "PERSISTENT_REPLAY_AND_SETTLED_REOPEN",
                    "FOUR_INDEPENDENT_CANONICAL_CASES",
                    "FOUR_GOVERNANCE_TERMINAL_BRANCHES",
                    "SETTLED_BYPASS_ZERO_BUSINESS_DELTA",
                    "SUCCESSFUL_EPHEMERAL_STATE_CLEANUP",
                ],
                "not_claimed": list(_NOT_CLAIMED),
            },
            "fixture": {
                "origin": "CODE_GENERATED_SCHEMA_FIXTURE",
                "record_count": 4,
            },
            "ok": True,
            "privacy": {
                "absolute_locations_recorded": False,
                "raw_records_recorded": False,
                "sensitive_identifiers_recorded": False,
                "source_locations_recorded": False,
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
                "data": "CODE_GENERATED_SCHEMA_FIXTURE",
                "execution": "LOCAL_SINGLE_PROCESS",
                "network": "REAL_LOOPBACK_TCP",
                "offline": True,
                "scenario": "FIXED_BUBBLERAN_FOUR_RECORD_GOVERNANCE",
            },
            "source": source,
        }
        if not _nested_keys(report_body).isdisjoint(_FORBIDDEN_REPORT_KEYS):
            raise BubbleRanDefenseError("contract_failed")
        report_size, report_sha256, report_identity = _write_report(
            run, report_body, token=token
        )
        _validate_run_contents(run, {REPORT_NAME}, code="report_write_failed")
        persisted = _read_identity_bound_file(
            run.directory / REPORT_NAME,
            expected_identity=report_identity,
            maximum_bytes=MAX_REPORT_BYTES,
            code="report_write_failed",
        )
        if not (
            len(persisted) == report_size
            and hashlib.sha256(persisted).hexdigest() == report_sha256
            and json.loads(persisted) == report_body
        ):
            raise BubbleRanDefenseError("report_write_failed")
        run.validate(code="report_write_failed")
        _write_json(
            output,
            {
                **report_body,
                "report": {
                    "bytes": report_size,
                    "filename": REPORT_NAME,
                    "sha256": report_sha256,
                },
            },
        )
        return 0
    except BubbleRanDefenseError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "command_failed")
        _write_json(errors, _error_payload(code))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
