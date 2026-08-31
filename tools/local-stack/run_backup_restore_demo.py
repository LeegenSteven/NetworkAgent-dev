#!/usr/bin/env python3
"""Produce bounded evidence for one fixed Local cold-backup recovery drill."""

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
SCHEMA = "networkagent-local-backup-recovery/1.0"
BACKUP_SCHEMA = "networkagent-local-cold-backup/1.0"
REPORT_NAME = "local-backup-recovery-report.json"
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 16
MAX_BACKUP_DATABASE_BYTES = 128 * 1024 * 1024
MAX_BACKUP_MANIFEST_BYTES = 16 * 1024
LOCAL_OWNERSHIP_DOMAIN = b"networkagent-local-backup-ownership/1\0"
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CATALOG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")

PathIdentity = tuple[int, int]
BackupTreeIdentity = tuple[PathIdentity, tuple[tuple[str, PathIdentity], ...]]

_NOT_CLAIMED = [
    "ONLINE_BACKUP",
    "PRODUCTION_HA",
    "MULTI_REPLICA_FAILOVER",
    "RPO_OR_RTO_SLO",
    "POWER_LOSS_DURABILITY",
    "ENCRYPTED_OR_SIGNED_BACKUP",
    "OFF_HOST_OR_REMOTE_BACKUP",
    "CROSS_VERSION_MIGRATION",
    "CLOUD_OR_SPANNER_BACKUP",
    "GATE_E_OR_G5_CLOSURE",
    "CLOUD_OR_PRODUCTION_RECOVERY",
    "IDENTITY_UNKNOWN_OR_RACED_RESIDUE_AUTO_CLEANUP",
]


def _load_defense_demo() -> Any:
    module_path = Path(__file__).with_name("run_defense_demo.py")
    spec = importlib.util.spec_from_file_location(
        "networkagent_backup_recovery_defense_demo", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("defense demo is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defense_demo = _load_defense_demo()


class BackupRecoveryError(Exception):
    """A stable recovery-evidence failure safe for the JSON boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ERROR_MESSAGES = {
    "cleanup_failed": "local backup recovery demo cleanup failed safely",
    "command_failed": "local backup recovery demo command failed safely",
    "confirmation_required": "explicit local simulation approval is required",
    "invalid_arguments": "command arguments are invalid",
    "recovery_contract_failed": (
        "local backup recovery evidence violated its fixed contract"
    ),
    "report_write_failed": "local backup recovery report could not be written safely",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise BackupRecoveryError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-backup-recovery", add_help=False
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


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BackupRecoveryError("recovery_contract_failed")
    return value


def _strict_int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise BackupRecoveryError("recovery_contract_failed")
    return value


def _validate_file_summary(value: object, *, filename: str) -> dict[str, object]:
    summary = _mapping(value)
    if set(summary) != {"bytes", "filename", "sha256"}:
        raise BackupRecoveryError("recovery_contract_failed")
    size = _strict_int(summary.get("bytes"), minimum=1)
    digest = summary.get("sha256")
    if (
        summary.get("filename") != filename
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
    ):
        raise BackupRecoveryError("recovery_contract_failed")
    return {"bytes": size, "filename": filename, "sha256": digest}


def _validate_catalog(value: object) -> dict[str, int]:
    catalog = _mapping(value)
    expected = {"schema_count", "table_count", "view_count"}
    if set(catalog) != expected:
        raise BackupRecoveryError("recovery_contract_failed")
    return {
        key: _strict_int(catalog.get(key), minimum=0)
        for key in ("schema_count", "table_count", "view_count")
    }


def _validate_tables(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise BackupRecoveryError("recovery_contract_failed")
    result: list[dict[str, object]] = []
    for item in value:
        table = _mapping(item)
        if set(table) != {"name", "rows", "schema"}:
            raise BackupRecoveryError("recovery_contract_failed")
        schema = table.get("schema")
        name = table.get("name")
        if (
            not isinstance(schema, str)
            or _SAFE_CATALOG_NAME.fullmatch(schema) is None
            or not isinstance(name, str)
            or _SAFE_CATALOG_NAME.fullmatch(name) is None
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        result.append(
            {
                "name": name,
                "rows": _strict_int(table.get("rows"), minimum=0),
                "schema": schema,
            }
        )
    if result != sorted(result, key=lambda item: (item["schema"], item["name"])):
        raise BackupRecoveryError("recovery_contract_failed")
    if len({(item["schema"], item["name"]) for item in result}) != len(result):
        raise BackupRecoveryError("recovery_contract_failed")
    return result


def _validate_backup(value: object) -> dict[str, object]:
    payload = _mapping(value)
    if set(payload) != {"command", "ok", "result"}:
        raise BackupRecoveryError("recovery_contract_failed")
    if payload.get("command") != "backup" or payload.get("ok") is not True:
        raise BackupRecoveryError("recovery_contract_failed")
    result = _mapping(payload.get("result"))
    if set(result) != {
        "catalog",
        "changed",
        "checkpointed",
        "database",
        "local_ownership_sha256",
        "logical_equivalence",
        "manifest",
        "row_count",
        "schema",
        "tables",
    }:
        raise BackupRecoveryError("recovery_contract_failed")
    catalog = _validate_catalog(result.get("catalog"))
    tables = _validate_tables(result.get("tables"))
    row_count = _strict_int(result.get("row_count"), minimum=0)
    local_ownership_sha256 = result.get("local_ownership_sha256")
    if (
        result.get("schema") != BACKUP_SCHEMA
        or result.get("changed") is not True
        or result.get("checkpointed") is not True
        or result.get("logical_equivalence") is not True
        or catalog["table_count"] != len(tables)
        or row_count != sum(int(item["rows"]) for item in tables)
        or not isinstance(local_ownership_sha256, str)
        or _SHA256.fullmatch(local_ownership_sha256) is None
    ):
        raise BackupRecoveryError("recovery_contract_failed")
    return {
        "catalog": catalog,
        "changed": True,
        "checkpointed": True,
        "database": _validate_file_summary(
            result.get("database"), filename="networkagent.duckdb"
        ),
        "local_ownership_sha256": local_ownership_sha256,
        "logical_equivalence": True,
        "manifest": _validate_file_summary(
            result.get("manifest"), filename="backup-manifest.json"
        ),
        "row_count": row_count,
        "schema": BACKUP_SCHEMA,
        "tables": tables,
    }


def _validate_restore(
    value: object,
    *,
    backup: Mapping[str, object],
    expected_changed: bool,
) -> dict[str, object]:
    payload = _mapping(value)
    if set(payload) != {"command", "ok", "result"}:
        raise BackupRecoveryError("recovery_contract_failed")
    if payload.get("command") != "restore" or payload.get("ok") is not True:
        raise BackupRecoveryError("recovery_contract_failed")
    result = _mapping(payload.get("result"))
    if set(result) != {
        "catalog",
        "changed",
        "database_sha256",
        "manifest_sha256",
        "row_count",
        "schema",
        "tables",
        "verified",
    }:
        raise BackupRecoveryError("recovery_contract_failed")
    catalog = _validate_catalog(result.get("catalog"))
    tables = _validate_tables(result.get("tables"))
    row_count = _strict_int(result.get("row_count"), minimum=0)
    backup_manifest = _mapping(backup.get("manifest"))
    backup_database = _mapping(backup.get("database"))
    if (
        result.get("schema") != BACKUP_SCHEMA
        or result.get("changed") is not expected_changed
        or result.get("manifest_sha256") != backup_manifest.get("sha256")
        or result.get("database_sha256") != backup_database.get("sha256")
        or result.get("verified") is not True
        or not _matches_exact(catalog, backup.get("catalog"))
        or not _matches_exact(tables, backup.get("tables"))
        or row_count != backup.get("row_count")
        or catalog["table_count"] != len(tables)
        or row_count != sum(int(item["rows"]) for item in tables)
    ):
        raise BackupRecoveryError("recovery_contract_failed")
    return {
        "catalog": catalog,
        "changed": expected_changed,
        "database_sha256": str(result["database_sha256"]),
        "manifest_sha256": str(result["manifest_sha256"]),
        "row_count": row_count,
        "schema": BACKUP_SCHEMA,
        "tables": tables,
        "verified": True,
    }


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        details = os.lstat(path)
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _regular_file(
    path: Path,
    *,
    expected_links: int = 1,
    error_code: str = "recovery_contract_failed",
) -> os.stat_result:
    try:
        details = os.lstat(path)
    except OSError:
        raise BackupRecoveryError(error_code) from None
    if (
        _is_link_like(path)
        or not stat.S_ISREG(details.st_mode)
        or details.st_nlink != expected_links
    ):
        raise BackupRecoveryError(error_code)
    return details


def _plain_directory_identity(path: Path, *, error_code: str) -> PathIdentity:
    try:
        details = os.lstat(path)
    except OSError:
        raise BackupRecoveryError(error_code) from None
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or attributes & reparse_flag
        or _is_link_like(path)
    ):
        raise BackupRecoveryError(error_code)
    try:
        after = os.lstat(path)
    except OSError:
        raise BackupRecoveryError(error_code) from None
    if (
        (after.st_dev, after.st_ino) != (details.st_dev, details.st_ino)
        or after.st_mode != details.st_mode
        or getattr(after, "st_file_attributes", 0) != attributes
    ):
        raise BackupRecoveryError(error_code)
    return details.st_dev, details.st_ino


def _unlink_owned_file(
    path: Path,
    expected_identity: PathIdentity,
    *,
    error_code: str,
    allowed_links: frozenset[int] = frozenset({1}),
) -> None:
    try:
        details = os.lstat(path)
        if (
            _is_link_like(path)
            or not stat.S_ISREG(details.st_mode)
            or details.st_nlink not in allowed_links
            or (details.st_dev, details.st_ino) != expected_identity
        ):
            raise BackupRecoveryError(error_code)
        path.unlink()
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError(error_code) from None


def _remove_owned_empty_directory(
    path: Path, expected_identity: PathIdentity, *, error_code: str
) -> None:
    if not os.path.lexists(path):
        return
    current = _plain_directory_identity(path, error_code=error_code)
    try:
        if current != expected_identity or any(path.iterdir()):
            raise BackupRecoveryError(error_code)
        path.rmdir()
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError(error_code) from None


def _file_snapshot(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_nlink,
    )


def _file_digest(
    path: Path,
    *,
    maximum_bytes: int,
    error_code: str = "recovery_contract_failed",
) -> tuple[int, str]:
    before = _regular_file(path, error_code=error_code)
    if before.st_size > maximum_bytes:
        raise BackupRecoveryError(error_code)
    digest = hashlib.sha256()
    size = 0
    descriptor: int | None = None
    opened_after: os.stat_result | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _file_snapshot(opened) != _file_snapshot(before)
            ):
                raise BackupRecoveryError(error_code)
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum_bytes:
                    raise BackupRecoveryError(error_code)
                digest.update(chunk)
            opened_after = os.fstat(stream.fileno())
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError(error_code) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    after = _regular_file(path, error_code=error_code)
    if (
        opened_after is None
        or _file_snapshot(opened_after) != _file_snapshot(before)
        or _file_snapshot(after) != _file_snapshot(before)
        or size != before.st_size
    ):
        raise BackupRecoveryError(error_code)
    return size, digest.hexdigest()


def _capture_backup_tree_identity(
    backup_directory: Path, *, error_code: str
) -> BackupTreeIdentity:
    try:
        directory_identity = _plain_directory_identity(
            backup_directory, error_code=error_code
        )
        children = tuple(backup_directory.iterdir())
        if {child.name for child in children} != {
            "backup-manifest.json",
            "networkagent.duckdb",
        }:
            raise BackupRecoveryError(error_code)
        files: list[tuple[str, PathIdentity]] = []
        for child in sorted(children, key=lambda item: item.name):
            details = _regular_file(child, error_code=error_code)
            files.append((child.name, (details.st_dev, details.st_ino)))
        if (
            _plain_directory_identity(backup_directory, error_code=error_code)
            != directory_identity
        ):
            raise BackupRecoveryError(error_code)
        for name, identity in files:
            details = _regular_file(backup_directory / name, error_code=error_code)
            if (details.st_dev, details.st_ino) != identity:
                raise BackupRecoveryError(error_code)
        return directory_identity, tuple(files)
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError(error_code) from None


def _local_ownership_sha256(identity: BackupTreeIdentity) -> str:
    directory_identity, file_identities = identity
    if len(file_identities) != 2:
        raise BackupRecoveryError("recovery_contract_failed")
    files = dict(file_identities)
    if set(files) != {"backup-manifest.json", "networkagent.duckdb"}:
        raise BackupRecoveryError("recovery_contract_failed")

    def entry(name: str, value: PathIdentity) -> list[object]:
        return [
            name,
            _strict_int(value[0], minimum=0),
            _strict_int(value[1], minimum=1),
        ]

    encoded = json.dumps(
        [
            entry("directory", directory_identity),
            entry("networkagent.duckdb", files["networkagent.duckdb"]),
            entry("backup-manifest.json", files["backup-manifest.json"]),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(LOCAL_OWNERSHIP_DOMAIN + encoded).hexdigest()


def _validate_backup_files(
    backup_directory: Path, backup: Mapping[str, object]
) -> BackupTreeIdentity:
    identity_before = _capture_backup_tree_identity(
        backup_directory, error_code="recovery_contract_failed"
    )
    expected_ownership = backup.get("local_ownership_sha256")
    if (
        not isinstance(expected_ownership, str)
        or _SHA256.fullmatch(expected_ownership) is None
        or _local_ownership_sha256(identity_before) != expected_ownership
    ):
        raise BackupRecoveryError("recovery_contract_failed")
    for key, filename in (
        ("manifest", "backup-manifest.json"),
        ("database", "networkagent.duckdb"),
    ):
        expected = _mapping(backup.get(key))
        size, digest = _file_digest(
            backup_directory / filename,
            maximum_bytes=(
                MAX_BACKUP_MANIFEST_BYTES
                if key == "manifest"
                else MAX_BACKUP_DATABASE_BYTES
            ),
        )
        if size != expected.get("bytes") or digest != expected.get("sha256"):
            raise BackupRecoveryError("recovery_contract_failed")
    identity_after = _capture_backup_tree_identity(
        backup_directory, error_code="recovery_contract_failed"
    )
    if (
        identity_after != identity_before
        or _local_ownership_sha256(identity_after) != expected_ownership
    ):
        raise BackupRecoveryError("recovery_contract_failed")
    return identity_before


def _create_run_directory(
    repository_root: Path,
    *,
    utc_now: Callable[[], datetime],
    random_token: Callable[[], str],
) -> tuple[Path, str, PathIdentity]:
    local_root = repository_root / ".local"
    defense_root = local_root / "networkagent-defense"
    run_directory: Path | None = None
    created_identity: tuple[int, int] | None = None
    completed = False
    try:
        for candidate in (local_root, defense_root):
            if (candidate.exists() or os.path.lexists(candidate)) and _is_link_like(
                candidate
            ):
                raise BackupRecoveryError("report_write_failed")
        defense_root.mkdir(parents=True, exist_ok=True)
        if _is_link_like(defense_root) or not defense_root.is_dir():
            raise BackupRecoveryError("report_write_failed")
        moment = utc_now()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise BackupRecoveryError("report_write_failed")
        token = random_token()
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise BackupRecoveryError("report_write_failed")
        timestamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_directory = defense_root / f"{timestamp}-{token}"
        run_directory.mkdir(parents=False, exist_ok=False)
        created = os.lstat(run_directory)
        attributes = getattr(created, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISDIR(created.st_mode)
            or stat.S_ISLNK(created.st_mode)
            or attributes & reparse_flag
        ):
            raise BackupRecoveryError("report_write_failed")
        created_identity = (created.st_dev, created.st_ino)
        if _is_link_like(run_directory):
            raise BackupRecoveryError("report_write_failed")
        completed = True
        return run_directory, token, created_identity
    except BackupRecoveryError:
        raise
    except Exception:
        raise BackupRecoveryError("report_write_failed") from None
    finally:
        if not completed and run_directory is not None and created_identity is not None:
            try:
                _remove_owned_empty_directory(
                    run_directory,
                    created_identity,
                    error_code="report_write_failed",
                )
            except BackupRecoveryError:
                pass


def _create_workspace_directory(
    run_directory: Path,
    run_identity: PathIdentity,
    workspace: Path,
) -> PathIdentity:
    created_identity: PathIdentity | None = None
    completed = False
    try:
        if workspace.parent != run_directory or workspace.name != "success":
            raise BackupRecoveryError("recovery_contract_failed")
        if _plain_directory_identity(
            run_directory, error_code="recovery_contract_failed"
        ) != run_identity or os.path.lexists(workspace):
            raise BackupRecoveryError("recovery_contract_failed")
        workspace.mkdir(parents=False, exist_ok=False)
        created_identity = _plain_directory_identity(
            workspace, error_code="recovery_contract_failed"
        )
        if (
            _plain_directory_identity(
                run_directory, error_code="recovery_contract_failed"
            )
            != run_identity
            or _plain_directory_identity(
                workspace, error_code="recovery_contract_failed"
            )
            != created_identity
            or tuple(workspace.iterdir())
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        completed = True
        return created_identity
    except BackupRecoveryError:
        raise
    except Exception:
        raise BackupRecoveryError("recovery_contract_failed") from None
    finally:
        if not completed and created_identity is not None:
            try:
                if (
                    _plain_directory_identity(
                        run_directory, error_code="recovery_contract_failed"
                    )
                    == run_identity
                ):
                    _remove_owned_empty_directory(
                        workspace,
                        created_identity,
                        error_code="recovery_contract_failed",
                    )
            except BackupRecoveryError:
                pass


def _call_stack(
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    arguments: tuple[str, ...],
    *,
    repository_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    try:
        return defense_demo._call_stack(
            process_runner,
            arguments,
            repository_root=repository_root,
            environment=environment,
        )
    except Exception:
        raise BackupRecoveryError("command_failed") from None


def _call_expected_restore_rejection(
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    arguments: tuple[str, ...],
    *,
    repository_root: Path,
    environment: dict[str, str],
    error_code: str,
    post_call_check: Callable[[], None],
) -> None:
    command = (
        sys.executable,
        str(repository_root / "tools" / "local-stack" / "local_stack.py"),
        *arguments,
    )
    try:
        completed = defense_demo._run_process(
            process_runner,
            command,
            repository_root=repository_root,
            environment=environment,
        )
        post_call_check()
        if (
            completed.returncode != 2
            or completed.stdout != b""
            or completed.stderr == b""
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        payload = defense_demo._decode_json_document(completed.stderr)
        error = _mapping(payload.get("error"))
        if (
            set(payload) != {"error", "ok"}
            or payload.get("ok") is not False
            or set(error) != {"code", "message"}
            or error.get("code") != error_code
            or not isinstance(error.get("message"), str)
        ):
            raise BackupRecoveryError("recovery_contract_failed")
    except BackupRecoveryError:
        raise
    except Exception:
        raise BackupRecoveryError("recovery_contract_failed") from None


def _validate_reset(payload: Mapping[str, object]) -> None:
    if (
        payload.get("command") != "reset"
        or payload.get("ok") is not True
        or payload.get("reset") is not True
        or payload.get("workspace_removed") is not True
        or payload.get("preserved_unknown_entries") is not False
    ):
        raise BackupRecoveryError("cleanup_failed")


def _copy_bounded_regular_file(
    source: Path, destination: Path, *, maximum_bytes: int
) -> PathIdentity:
    source_before = _regular_file(source)
    if source_before.st_size > maximum_bytes:
        raise BackupRecoveryError("recovery_contract_failed")
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    copied = 0
    source_opened_after: os.stat_result | None = None
    destination_opened_after: os.stat_result | None = None
    try:
        read_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        read_flags |= getattr(os, "O_NOFOLLOW", 0)
        source_descriptor = os.open(source, read_flags)
        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        write_flags |= getattr(os, "O_BINARY", 0)
        write_flags |= getattr(os, "O_NOFOLLOW", 0)
        destination_descriptor = os.open(destination, write_flags, 0o600)
        source_opened = os.fstat(source_descriptor)
        destination_opened = os.fstat(destination_descriptor)
        if (
            not stat.S_ISREG(source_opened.st_mode)
            or source_opened.st_nlink != 1
            or _file_snapshot(source_opened) != _file_snapshot(source_before)
            or not stat.S_ISREG(destination_opened.st_mode)
            or destination_opened.st_nlink != 1
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > maximum_bytes:
                raise BackupRecoveryError("recovery_contract_failed")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise BackupRecoveryError("recovery_contract_failed")
                view = view[written:]
        os.fsync(destination_descriptor)
        source_opened_after = os.fstat(source_descriptor)
        destination_opened_after = os.fstat(destination_descriptor)
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError("recovery_contract_failed") from None
    finally:
        for descriptor in (source_descriptor, destination_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    source_after = _regular_file(source)
    destination_after = _regular_file(destination)
    if (
        source_opened_after is None
        or destination_opened_after is None
        or _file_snapshot(source_opened_after) != _file_snapshot(source_before)
        or _file_snapshot(source_after) != _file_snapshot(source_before)
        or _file_snapshot(destination_after) != _file_snapshot(destination_opened_after)
        or copied != source_before.st_size
        or copied != destination_after.st_size
    ):
        raise BackupRecoveryError("recovery_contract_failed")
    return destination_after.st_dev, destination_after.st_ino


def _copy_corrupt_backup(
    source: Path,
    destination: Path,
    *,
    source_identity: BackupTreeIdentity,
) -> BackupTreeIdentity:
    destination_identity: PathIdentity | None = None
    created_files: dict[str, PathIdentity] = {}
    try:
        if (
            os.path.lexists(destination)
            or destination.parent != source.parent
            or _capture_backup_tree_identity(
                source, error_code="recovery_contract_failed"
            )
            != source_identity
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        destination.mkdir(parents=False, exist_ok=False)
        destination_identity = _plain_directory_identity(
            destination, error_code="recovery_contract_failed"
        )
        created_files["backup-manifest.json"] = _copy_bounded_regular_file(
            source / "backup-manifest.json",
            destination / "backup-manifest.json",
            maximum_bytes=MAX_BACKUP_MANIFEST_BYTES,
        )
        if (
            _plain_directory_identity(
                destination, error_code="recovery_contract_failed"
            )
            != destination_identity
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        created_files["networkagent.duckdb"] = _copy_bounded_regular_file(
            source / "networkagent.duckdb",
            destination / "networkagent.duckdb",
            maximum_bytes=MAX_BACKUP_DATABASE_BYTES,
        )
        expected_destination: BackupTreeIdentity = (
            destination_identity,
            tuple(sorted(created_files.items())),
        )
        if (
            _capture_backup_tree_identity(
                destination, error_code="recovery_contract_failed"
            )
            != expected_destination
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        corrupted_database = destination / "networkagent.duckdb"
        before_corruption = _regular_file(corrupted_database)
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(corrupted_database, flags)
        corrupted_opened_after: os.stat_result | None = None
        try:
            opened = os.fstat(descriptor)
            if (
                _file_snapshot(opened) != _file_snapshot(before_corruption)
                or (opened.st_dev, opened.st_ino)
                != created_files["networkagent.duckdb"]
                or _plain_directory_identity(
                    destination, error_code="recovery_contract_failed"
                )
                != destination_identity
            ):
                raise BackupRecoveryError("recovery_contract_failed")
            original = os.read(descriptor, 1)
            if len(original) != 1:
                raise BackupRecoveryError("recovery_contract_failed")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, bytes([original[0] ^ 0xFF])) != 1:
                raise BackupRecoveryError("recovery_contract_failed")
            os.fsync(descriptor)
            corrupted_opened_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        corrupted_path_after = _regular_file(corrupted_database)
        if (
            corrupted_opened_after is None
            or _file_snapshot(corrupted_path_after)
            != _file_snapshot(corrupted_opened_after)
            or (
                corrupted_opened_after.st_dev,
                corrupted_opened_after.st_ino,
                corrupted_opened_after.st_size,
                corrupted_opened_after.st_nlink,
            )
            != (
                before_corruption.st_dev,
                before_corruption.st_ino,
                before_corruption.st_size,
                before_corruption.st_nlink,
            )
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        if _file_digest(
            corrupted_database, maximum_bytes=MAX_BACKUP_DATABASE_BYTES
        ) == _file_digest(
            source / "networkagent.duckdb",
            maximum_bytes=MAX_BACKUP_DATABASE_BYTES,
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        if (
            _capture_backup_tree_identity(source, error_code="recovery_contract_failed")
            != source_identity
            or _capture_backup_tree_identity(
                destination, error_code="recovery_contract_failed"
            )
            != expected_destination
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        return expected_destination
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError("recovery_contract_failed") from None


def _remove_backup_directory(
    backup_directory: Path, expected_identity: BackupTreeIdentity
) -> None:
    try:
        if not os.path.lexists(backup_directory):
            return
        current = _capture_backup_tree_identity(
            backup_directory, error_code="cleanup_failed"
        )
        if current != expected_identity:
            raise BackupRecoveryError("cleanup_failed")
        directory_identity, files = expected_identity
        for filename, identity in files:
            if (
                _plain_directory_identity(backup_directory, error_code="cleanup_failed")
                != directory_identity
            ):
                raise BackupRecoveryError("cleanup_failed")
            _unlink_owned_file(
                backup_directory / filename,
                identity,
                error_code="cleanup_failed",
            )
        _remove_owned_empty_directory(
            backup_directory,
            directory_identity,
            error_code="cleanup_failed",
        )
    except BackupRecoveryError:
        raise
    except OSError:
        raise BackupRecoveryError("cleanup_failed") from None


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
        raise BackupRecoveryError("report_write_failed") from None
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise BackupRecoveryError("report_write_failed")
    return encoded


def _write_report(
    run_directory: Path,
    report: Mapping[str, object],
    *,
    token: str,
    run_identity: PathIdentity,
) -> tuple[int, str]:
    if _TOKEN.fullmatch(token) is None:
        raise BackupRecoveryError("report_write_failed")
    final_path = run_directory / REPORT_NAME
    temporary = run_directory / f".{REPORT_NAME}.{token}.tmp"
    owned_identity: tuple[int, int] | None = None
    published = False
    linked = False

    def ensure_run_owned() -> None:
        if (
            _plain_directory_identity(run_directory, error_code="report_write_failed")
            != run_identity
        ):
            raise BackupRecoveryError("report_write_failed")

    try:
        ensure_run_owned()
        if (
            _is_link_like(run_directory)
            or not run_directory.is_dir()
            or os.path.lexists(final_path)
            or os.path.lexists(temporary)
            or tuple(run_directory.iterdir())
        ):
            raise BackupRecoveryError("report_write_failed")
        encoded = _canonical_bytes(report)
        ensure_run_owned()
        with temporary.open("xb") as stream:
            try:
                opened = os.fstat(stream.fileno())
            except OSError:
                try:
                    recovered = os.fstat(stream.fileno())
                except OSError:
                    raise BackupRecoveryError("report_write_failed") from None
                owned_identity = (recovered.st_dev, recovered.st_ino)
                raise BackupRecoveryError("report_write_failed") from None
            owned_identity = (opened.st_dev, opened.st_ino)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise BackupRecoveryError("report_write_failed")
            if stream.write(encoded) != len(encoded):
                raise BackupRecoveryError("report_write_failed")
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(written.st_mode)
                or written.st_nlink != 1
                or (written.st_dev, written.st_ino) != owned_identity
                or written.st_size != len(encoded)
            ):
                raise BackupRecoveryError("report_write_failed")
        ensure_run_owned()
        temporary_identity = _regular_file(temporary, error_code="report_write_failed")
        if (
            temporary_identity.st_dev,
            temporary_identity.st_ino,
        ) != owned_identity or temporary_identity.st_size != len(encoded):
            raise BackupRecoveryError("report_write_failed")
        ensure_run_owned()
        os.link(temporary, final_path, follow_symlinks=False)
        linked = True
        linked_temporary = _regular_file(
            temporary, expected_links=2, error_code="report_write_failed"
        )
        final_identity = _regular_file(
            final_path, expected_links=2, error_code="report_write_failed"
        )
        if (
            owned_identity
            != (
                linked_temporary.st_dev,
                linked_temporary.st_ino,
            )
            or owned_identity
            != (
                final_identity.st_dev,
                final_identity.st_ino,
            )
            or not os.path.samefile(temporary, final_path)
        ):
            raise BackupRecoveryError("report_write_failed")
        ensure_run_owned()
        _unlink_owned_file(
            temporary,
            owned_identity,
            error_code="report_write_failed",
            allowed_links=frozenset({2}),
        )
        size, digest = _file_digest(
            final_path,
            maximum_bytes=MAX_DOCUMENT_BYTES,
            error_code="report_write_failed",
        )
        if size != len(encoded) or digest != hashlib.sha256(encoded).hexdigest():
            raise BackupRecoveryError("report_write_failed")
        ensure_run_owned()
        published_identity = _regular_file(
            final_path, expected_links=1, error_code="report_write_failed"
        )
        if (
            published_identity.st_dev,
            published_identity.st_ino,
        ) != owned_identity or published_identity.st_size != len(encoded):
            raise BackupRecoveryError("report_write_failed")
        ensure_run_owned()
        published = True
        return size, digest
    except BackupRecoveryError:
        raise
    except Exception:
        raise BackupRecoveryError("report_write_failed") from None
    finally:
        if not published:
            try:
                ensure_run_owned()
            except BackupRecoveryError:
                pass
            else:
                if owned_identity is not None:
                    for candidate in (temporary, final_path):
                        try:
                            if not os.path.lexists(candidate):
                                continue
                            _unlink_owned_file(
                                candidate,
                                owned_identity,
                                error_code="report_write_failed",
                                allowed_links=(
                                    frozenset({1, 2}) if linked else frozenset({1})
                                ),
                            )
                        except BackupRecoveryError:
                            pass


def _error_payload(code: str) -> dict[str, object]:
    stable = code if code in _ERROR_MESSAGES else "command_failed"
    return {
        "error": {"code": stable, "message": _ERROR_MESSAGES[stable]},
        "ok": False,
        "schema": SCHEMA,
    }


def _execute(
    *,
    process_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    repository_root: Path,
    utc_now: Callable[[], datetime],
    random_token: Callable[[], str],
) -> dict[str, object]:
    environment = defense_demo._safe_environment()
    source_before = defense_demo._read_source_binding(
        process_runner,
        repository_root=repository_root,
        environment=environment,
    )
    run_directory, token, run_identity = _create_run_directory(
        repository_root, utc_now=utc_now, random_token=random_token
    )
    workspace = run_directory / "success"
    backup_directory = run_directory / "backup"
    corrupt_backup_directory = run_directory / "corrupt-backup"
    operation_error: BackupRecoveryError | None = None
    cleanup_error: BackupRecoveryError | None = None
    backup: dict[str, object] | None = None
    backup_identity: BackupTreeIdentity | None = None
    corrupt_backup_identity: BackupTreeIdentity | None = None
    workspace_identity: PathIdentity | None = None
    lifecycle_before: dict[str, object] | None = None
    first_restore: dict[str, object] | None = None
    retry_restore: dict[str, object] | None = None
    mismatch_rejected = False
    fresh_unchanged = False

    def ensure_run_owned(error_code: str) -> None:
        if (
            _plain_directory_identity(run_directory, error_code=error_code)
            != run_identity
        ):
            raise BackupRecoveryError(error_code)

    def ensure_workspace_owned(error_code: str) -> None:
        ensure_run_owned(error_code)
        if workspace_identity is None or (
            _plain_directory_identity(workspace, error_code=error_code)
            != workspace_identity
        ):
            raise BackupRecoveryError(error_code)
        ensure_run_owned(error_code)

    def workspace_process_runner(
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        ensure_workspace_owned("recovery_contract_failed")
        completed = process_runner(
            arguments,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
        ensure_workspace_owned("recovery_contract_failed")
        return completed

    try:
        workspace_identity = _create_workspace_directory(
            run_directory, run_identity, workspace
        )
        ensure_workspace_owned("recovery_contract_failed")
        doctor = _call_stack(
            workspace_process_runner,
            ("--workspace", str(workspace), "doctor"),
            repository_root=repository_root,
            environment=environment,
        )
        try:
            defense_demo._validate_doctor(doctor)
            ensure_workspace_owned("recovery_contract_failed")
            defense_demo._run_branch(
                "success",
                workspace=workspace,
                repository_root=repository_root,
                environment=environment,
                process_runner=workspace_process_runner,
            )
            ensure_workspace_owned("recovery_contract_failed")
            lifecycle_before = defense_demo._read_lifecycle_projection(
                "success",
                workspace=workspace,
                repository_root=repository_root,
                environment=environment,
                process_runner=workspace_process_runner,
            )
            ensure_workspace_owned("recovery_contract_failed")
        except Exception:
            raise BackupRecoveryError("recovery_contract_failed") from None

        ensure_workspace_owned("recovery_contract_failed")
        backup_payload = _call_stack(
            workspace_process_runner,
            (
                "--workspace",
                str(workspace),
                "backup",
                "--destination",
                str(backup_directory),
            ),
            repository_root=repository_root,
            environment=environment,
        )
        ensure_workspace_owned("recovery_contract_failed")
        backup = _validate_backup(backup_payload)
        backup_identity = _validate_backup_files(backup_directory, backup)

        ensure_workspace_owned("recovery_contract_failed")
        reset = _call_stack(
            process_runner,
            ("--workspace", str(workspace), "reset", "--yes"),
            repository_root=repository_root,
            environment=environment,
        )
        ensure_run_owned("recovery_contract_failed")
        if os.path.lexists(workspace):
            raise BackupRecoveryError("cleanup_failed")
        _validate_reset(reset)
        workspace_identity = None

        ensure_run_owned("recovery_contract_failed")
        if os.path.lexists(workspace):
            raise BackupRecoveryError("recovery_contract_failed")
        workspace_identity = _create_workspace_directory(
            run_directory, run_identity, workspace
        )
        ensure_workspace_owned("recovery_contract_failed")
        fresh = _call_stack(
            workspace_process_runner,
            ("--workspace", str(workspace), "init"),
            repository_root=repository_root,
            environment=environment,
        )
        ensure_workspace_owned("recovery_contract_failed")
        try:
            defense_demo._validate_init(fresh)
        except Exception:
            raise BackupRecoveryError("recovery_contract_failed") from None
        ensure_workspace_owned("recovery_contract_failed")

        fresh_database = workspace / "state" / "networkagent.duckdb"
        fresh_before = _file_digest(
            fresh_database, maximum_bytes=MAX_BACKUP_DATABASE_BYTES
        )
        ensure_workspace_owned("recovery_contract_failed")
        manifest = _mapping(backup.get("manifest"))
        manifest_digest = manifest.get("sha256")
        if (
            not isinstance(manifest_digest, str)
            or _SHA256.fullmatch(manifest_digest) is None
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        ensure_workspace_owned("recovery_contract_failed")
        corrupt_backup_identity = _copy_corrupt_backup(
            backup_directory,
            corrupt_backup_directory,
            source_identity=backup_identity,
        )
        ensure_workspace_owned("recovery_contract_failed")
        _call_expected_restore_rejection(
            workspace_process_runner,
            (
                "--workspace",
                str(workspace),
                "restore",
                "--source",
                str(corrupt_backup_directory),
                "--expected-manifest-sha256",
                manifest_digest,
                "--yes",
            ),
            repository_root=repository_root,
            environment=environment,
            error_code="backup_invalid",
            post_call_check=lambda: ensure_workspace_owned("recovery_contract_failed"),
        )
        ensure_workspace_owned("recovery_contract_failed")
        mismatch_rejected = True
        fresh_unchanged = (
            _file_digest(fresh_database, maximum_bytes=MAX_BACKUP_DATABASE_BYTES)
            == fresh_before
        )
        ensure_workspace_owned("recovery_contract_failed")
        if not fresh_unchanged:
            raise BackupRecoveryError("recovery_contract_failed")

        restore_arguments = (
            "--workspace",
            str(workspace),
            "restore",
            "--source",
            str(backup_directory),
            "--expected-manifest-sha256",
            manifest_digest,
            "--yes",
        )
        ensure_workspace_owned("recovery_contract_failed")
        first_restore_payload = _call_stack(
            workspace_process_runner,
            restore_arguments,
            repository_root=repository_root,
            environment=environment,
        )
        ensure_workspace_owned("recovery_contract_failed")
        first_restore = _validate_restore(
            first_restore_payload,
            backup=backup,
            expected_changed=True,
        )
        try:
            ensure_workspace_owned("recovery_contract_failed")
            lifecycle_after = defense_demo._read_lifecycle_projection(
                "success",
                workspace=workspace,
                repository_root=repository_root,
                environment=environment,
                process_runner=workspace_process_runner,
            )
            ensure_workspace_owned("recovery_contract_failed")
        except Exception:
            raise BackupRecoveryError("recovery_contract_failed") from None
        if not _matches_exact(lifecycle_after, lifecycle_before):
            raise BackupRecoveryError("recovery_contract_failed")

        ensure_workspace_owned("recovery_contract_failed")
        retry_restore_payload = _call_stack(
            workspace_process_runner,
            restore_arguments,
            repository_root=repository_root,
            environment=environment,
        )
        ensure_workspace_owned("recovery_contract_failed")
        retry_restore = _validate_restore(
            retry_restore_payload,
            backup=backup,
            expected_changed=False,
        )
        try:
            ensure_workspace_owned("recovery_contract_failed")
            lifecycle_retry = defense_demo._read_lifecycle_projection(
                "success",
                workspace=workspace,
                repository_root=repository_root,
                environment=environment,
                process_runner=workspace_process_runner,
            )
            ensure_workspace_owned("recovery_contract_failed")
        except Exception:
            raise BackupRecoveryError("recovery_contract_failed") from None
        if not _matches_exact(lifecycle_retry, lifecycle_before) or not _matches_exact(
            {key: value for key, value in first_restore.items() if key != "changed"},
            {key: value for key, value in retry_restore.items() if key != "changed"},
        ):
            raise BackupRecoveryError("recovery_contract_failed")
        ensure_workspace_owned("recovery_contract_failed")
        if _validate_backup_files(backup_directory, backup) != backup_identity:
            raise BackupRecoveryError("recovery_contract_failed")
    except BackupRecoveryError as exc:
        operation_error = exc
    except Exception:
        operation_error = BackupRecoveryError("command_failed")
    finally:
        try:
            ensure_run_owned("cleanup_failed")
        except BackupRecoveryError:
            cleanup_error = BackupRecoveryError("cleanup_failed")
        if os.path.lexists(workspace):
            try:
                ensure_workspace_owned("cleanup_failed")
                reset = _call_stack(
                    process_runner,
                    ("--workspace", str(workspace), "reset", "--yes"),
                    repository_root=repository_root,
                    environment=environment,
                )
                ensure_run_owned("cleanup_failed")
                if os.path.lexists(workspace):
                    raise BackupRecoveryError("cleanup_failed")
                _validate_reset(reset)
                workspace_identity = None
            except Exception:
                cleanup_error = BackupRecoveryError("cleanup_failed")
        else:
            workspace_identity = None
        if os.path.lexists(backup_directory):
            if backup_identity is None:
                cleanup_error = BackupRecoveryError("cleanup_failed")
            else:
                try:
                    _remove_backup_directory(backup_directory, backup_identity)
                except Exception:
                    cleanup_error = BackupRecoveryError("cleanup_failed")
        if os.path.lexists(corrupt_backup_directory):
            if corrupt_backup_identity is None:
                cleanup_error = BackupRecoveryError("cleanup_failed")
            else:
                try:
                    _remove_backup_directory(
                        corrupt_backup_directory, corrupt_backup_identity
                    )
                except Exception:
                    cleanup_error = BackupRecoveryError("cleanup_failed")

    if cleanup_error is not None:
        raise cleanup_error
    if operation_error is not None:
        try:
            _remove_owned_empty_directory(
                run_directory, run_identity, error_code="cleanup_failed"
            )
        except BackupRecoveryError:
            pass
        raise operation_error
    if (
        backup is None
        or lifecycle_before is None
        or first_restore is None
        or retry_restore is None
        or not mismatch_rejected
        or not fresh_unchanged
        or os.path.lexists(workspace)
        or os.path.lexists(backup_directory)
        or os.path.lexists(corrupt_backup_directory)
        or tuple(run_directory.iterdir())
    ):
        raise BackupRecoveryError("recovery_contract_failed")

    source_after = defense_demo._read_source_binding(
        process_runner,
        repository_root=repository_root,
        environment=environment,
    )
    source = defense_demo._combine_source_bindings(source_before, source_after)
    report_body: dict[str, object] = {
        "classification": (
            "LOCAL_COLD_BACKUP_RECOVERY_EVIDENCE"
            if source["commit_bound"] is True
            else "LOCAL_WORKTREE_COLD_BACKUP_RECOVERY_EVIDENCE"
        ),
        "coverage": {
            "delivered": {
                "checkpointed_full_database_backup": True,
                "corrupt_backup_rejection": True,
                "lifecycle_equivalence": True,
                "manifest_hash_binding": True,
                "reset_fresh_init_restore": True,
                "restore_idempotency": True,
                "successful_run_workspace_backup_cleanup": True,
            },
            "not_claimed": list(_NOT_CLAIMED),
        },
        "ok": True,
        "privacy": {
            "absolute_paths_recorded": False,
            "backup_identifiers_recorded": False,
            "child_stderr_recorded": False,
            "child_stdout_recorded": False,
            "database_bytes_recorded": False,
            "database_digests_recorded": False,
            "database_filenames_recorded": False,
            "domain_identifiers_recorded": False,
            "environment_recorded": False,
            "manifest_content_recorded": False,
            "manifest_digests_recorded": False,
            "raw_arguments_recorded": False,
            "status": "PASS",
            "workspace_identifiers_recorded": False,
        },
        "proof": {
            "backup_changed": True,
            "backup_file_count": 2,
            "catalog_equivalent": True,
            "corrupt_backup_rejected": True,
            "fresh_database_unchanged_after_rejection": True,
            "lifecycle_projection_equivalent": True,
            "restore_changed": True,
            "restore_retry_changed": False,
            "restore_retry_equivalent": True,
            "row_count_equivalent": True,
        },
        "schema": SCHEMA,
        "scope": {
            "backup_mode": "COLD_OFFLINE",
            "database_engine": "DUCKDB",
            "execution_mode": "LOCAL_SINGLE_PROCESS",
            "restore_target": "RESET_FRESH_INITIALIZATION",
            "writer_stopped": True,
        },
        "source": source,
    }
    try:
        report_bytes, report_digest = _write_report(
            run_directory,
            report_body,
            token=token,
            run_identity=run_identity,
        )
    except BackupRecoveryError:
        try:
            _remove_owned_empty_directory(
                run_directory, run_identity, error_code="report_write_failed"
            )
        except BackupRecoveryError:
            pass
        raise
    return {
        **report_body,
        "report": {
            "bytes": report_bytes,
            "filename": REPORT_NAME,
            "sha256": report_digest,
        },
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
            raise BackupRecoveryError("confirmation_required")
    except BackupRecoveryError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2

    try:
        result = _execute(
            process_runner=(
                process_runner
                if process_runner is not None
                else defense_demo._default_process_runner
            ),
            repository_root=repository_root.resolve(),
            utc_now=utc_now,
            random_token=random_token,
        )
        _write_json(output, result)
        return 0
    except BackupRecoveryError as exc:
        _write_json(errors, _error_payload(exc.code))
        return 2
    except Exception:
        _write_json(errors, _error_payload("command_failed"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
