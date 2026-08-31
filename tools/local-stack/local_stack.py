#!/usr/bin/env python3
"""Safe, JSON-only local deployment entry point for NetworkAgent.

The module intentionally has no import-time dependency on project packages.  A
plain Python interpreter can therefore run ``doctor`` and receive a useful,
machine-readable dependency report before anything is installed.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import errno
import hashlib
import importlib
import json
import os
import shutil
import socket
import stat
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STACK_SCHEMA_VERSION = "1.0"
MARKER_NAME = ".local-stack.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8085
BACKUP_SCHEMA_VERSION = "networkagent-local-cold-backup/1.0"
BACKUP_DATABASE_NAME = "networkagent.duckdb"
BACKUP_MANIFEST_NAME = "backup-manifest.json"
_BACKUP_MAX_DATABASE_BYTES = 128 * 1024 * 1024
_BACKUP_MAX_MANIFEST_BYTES = 16 * 1024
_BACKUP_MAX_SCHEMAS = 4
_BACKUP_MAX_TABLES = 64
_BACKUP_MAX_VIEWS = 16
_BACKUP_MAX_ROWS = 100_000
_BACKUP_MAX_CATALOG_RECORDS = 1024
_MAINTENANCE_LOCK_NAME = ".backup-restore.lock"
_RESTORE_TEMP_NAME = ".networkagent.duckdb.restore.tmp"
_LOCAL_BACKUP_OWNERSHIP_DOMAIN = b"networkagent-local-backup-ownership/2\0"
DirectoryIdentity = tuple[int, int]
FileIdentity = tuple[int, int, int, int, int, int]
_PACKAGE_SOURCES = (
    REPOSITORY_ROOT / "packages" / "telco-domain" / "src",
    REPOSITORY_ROOT / "packages" / "telco-local" / "src",
    REPOSITORY_ROOT / "packages" / "telco-lab" / "src",
    REPOSITORY_ROOT / "networkagents" / "assurance" / "src",
)
_SOURCE_INPUTS = {
    "performance": REPOSITORY_ROOT
    / "data"
    / "samples"
    / "lte-demo"
    / "performance.csv",
    "safe_trace": REPOSITORY_ROOT
    / "data"
    / "samples"
    / "lte-demo"
    / "safe-cell-traces.csv",
    "rules": REPOSITORY_ROOT / "data" / "rca-rules" / "lte",
    "documents": REPOSITORY_ROOT / "data" / "docs" / "lte",
}


class SafeCliError(Exception):
    """An error whose stable code is safe to return without local details."""

    def __init__(self, code: str, *, exit_code: int = 2) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


_ERROR_MESSAGES = {
    "actions_disabled": "action approval is unavailable in disabled mode",
    "approval_binding_mismatch": "action approval does not match the reviewed preview",
    "approval_binding_required": "action approval requires the reviewed hash and revision",
    "approval_requires_incident": "action approval requires incident confirmation",
    "approval_requires_prior_preview": "action approval requires a prior preview command",
    "approval_reason_required": "action approval requires a non-empty reason",
    "backup_exists": "the selected backup destination already exists",
    "backup_failed": "the cold backup failed safely",
    "backup_invalid": "the selected cold backup is invalid",
    "backup_too_large": "the selected cold backup exceeds the local size budget",
    "dependencies_missing": "required local runtime dependencies are unavailable",
    "demo_seed_requires_fresh_workspace": (
        "container demo seeding requires a fresh incident store"
    ),
    "demo_verification_failed": "container demo state verification failed",
    "governance_unavailable": "the local governance engine is unavailable",
    "invalid_arguments": "command arguments are invalid",
    "lifecycle_projection_failed": (
        "local lifecycle projection did not match the fixed contract"
    ),
    "manifest_mismatch": "the supplied backup manifest hash does not match",
    "no_candidates": "the sample data produced no incident candidates",
    "not_awaiting_approval": "the incident has no approvable simulated action",
    "port_unavailable": "the selected loopback port is unavailable",
    "runtime_failed": "the local operation failed safely",
    "restore_confirmation_required": "restore requires explicit confirmation",
    "restore_failed": "the cold restore failed safely",
    "server_dependencies_missing": "optional Assurance server dependencies are unavailable",
    "unsafe_workspace": "the selected workspace is not safe for local-stack operations",
    "workspace_not_initialized": "the selected workspace is not initialized",
    "workspace_not_owned": "the selected directory is not owned by local-stack",
    "workspace_busy": "the local workspace is busy",
}


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


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    """Recognize both POSIX links and Windows junction/reparse directories."""

    try:
        if path.is_symlink():
            return True
        if os.name == "nt":
            try:
                attributes = path.lstat().st_file_attributes
            except FileNotFoundError:
                return False
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if attributes & reparse_flag:
                return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        raise SafeCliError("unsafe_workspace") from None


def _reject_non_local_path(path: str | os.PathLike[str]) -> None:
    """Reject network/device paths before any filesystem metadata operation."""

    rendered = os.fspath(path)
    if not isinstance(rendered, str) or not rendered or "\x00" in rendered:
        raise SafeCliError("unsafe_workspace")
    windows_form = rendered.replace("/", "\\")
    if windows_form.startswith("\\\\") or windows_form.startswith(
        ("\\\\?\\", "\\\\.\\")
    ):
        raise SafeCliError("unsafe_workspace")
    if os.name != "nt":
        if rendered.startswith("//"):
            raise SafeCliError("unsafe_workspace")
        return

    candidate = Path(rendered)
    absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
    drive = absolute.drive
    if not drive or drive.startswith("\\"):
        raise SafeCliError("unsafe_workspace")
    try:
        # DRIVE_FIXED=3. Mapped SMB drives (DRIVE_REMOTE=4), device roots,
        # removable media and unknown roots are outside this local-only profile.
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\"))
    except Exception:
        raise SafeCliError("unsafe_workspace") from None
    if drive_type != 3:
        raise SafeCliError("unsafe_workspace")


def _validate_workspace_path(path: Path) -> Path:
    _reject_non_local_path(path)
    supplied = Path(path).expanduser()
    _reject_non_local_path(supplied)
    if _is_link_like(supplied):
        raise SafeCliError("unsafe_workspace")
    resolved = supplied.resolve(strict=False)
    _reject_non_local_path(resolved)
    anchor = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    forbidden = {anchor, home, REPOSITORY_ROOT, *REPOSITORY_ROOT.parents}
    if any(_same_path(resolved, item) for item in forbidden):
        raise SafeCliError("unsafe_workspace")

    # Repository-contained workspaces are permitted only below the conventional
    # ignored .local area.  This makes a forged marker unable to turn source
    # directories into reset targets.
    local_root = (REPOSITORY_ROOT / ".local").resolve(strict=False)
    if _is_relative_to(resolved, REPOSITORY_ROOT) and not _is_relative_to(
        resolved, local_root
    ):
        raise SafeCliError("unsafe_workspace")
    return resolved


def _canonical_json_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise SafeCliError("backup_failed") from None
    return (rendered + "\n").encode("utf-8")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_file_stat(path: Path, *, invalid_code: str) -> os.stat_result:
    try:
        if _is_link_like(path):
            raise SafeCliError(invalid_code)
        metadata = path.stat(follow_symlinks=False)
    except SafeCliError:
        raise
    except OSError:
        raise SafeCliError(invalid_code) from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SafeCliError(invalid_code)
    return metadata


def _strict_directory_identity(
    identity: DirectoryIdentity, *, invalid_code: str
) -> list[int]:
    device, inode = identity
    if type(device) is not int or type(inode) is not int or device < 0 or inode <= 0:
        raise SafeCliError(invalid_code)
    return [device, inode]


def _strict_file_identity(identity: FileIdentity, *, invalid_code: str) -> list[int]:
    device, inode, size, modified_ns, changed_ns, link_count = identity
    if (
        type(device) is not int
        or type(inode) is not int
        or type(size) is not int
        or type(modified_ns) is not int
        or type(changed_ns) is not int
        or type(link_count) is not int
        or device < 0
        or inode <= 0
        or size < 0
        or modified_ns <= 0
        or changed_ns <= 0
        or link_count != 1
    ):
        raise SafeCliError(invalid_code)
    return [device, inode, size, modified_ns, changed_ns, link_count]


def _file_identity(metadata: os.stat_result, *, invalid_code: str) -> FileIdentity:
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
    )
    _strict_file_identity(identity, invalid_code=invalid_code)
    return identity


def _require_file_identity(
    path: Path, expected: FileIdentity, *, invalid_code: str
) -> os.stat_result:
    _strict_file_identity(expected, invalid_code=invalid_code)
    metadata = _safe_file_stat(path, invalid_code=invalid_code)
    if _file_identity(metadata, invalid_code=invalid_code) != expected:
        raise SafeCliError(invalid_code)
    return metadata


def _unlink_file_identity(
    path: Path,
    expected: FileIdentity,
    *,
    invalid_code: str,
    failure_code: str,
) -> None:
    _require_file_identity(path, expected, invalid_code=invalid_code)
    try:
        path.unlink()
    except OSError:
        raise SafeCliError(failure_code) from None


def _capture_partial_child(
    path: Path,
    expected_children: dict[str, FileIdentity],
    *,
    invalid_code: str,
) -> None:
    metadata = _safe_file_stat(path, invalid_code=invalid_code)
    identity = _file_identity(metadata, invalid_code=invalid_code)
    previous = expected_children.get(path.name)
    if previous is not None and previous != identity:
        raise SafeCliError(invalid_code)
    expected_children[path.name] = identity


def _directory_identity(path: Path, *, invalid_code: str) -> DirectoryIdentity:
    try:
        if _is_link_like(path):
            raise SafeCliError(invalid_code)
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SafeCliError(invalid_code)
        if not _same_path(path.resolve(strict=True), path):
            raise SafeCliError(invalid_code)
    except SafeCliError:
        raise
    except OSError:
        raise SafeCliError(invalid_code) from None
    identity = (metadata.st_dev, metadata.st_ino)
    _strict_directory_identity(identity, invalid_code=invalid_code)
    return identity


def _require_directory_identity(
    path: Path, expected: DirectoryIdentity, *, invalid_code: str
) -> None:
    _strict_directory_identity(expected, invalid_code=invalid_code)
    if _directory_identity(path, invalid_code=invalid_code) != expected:
        raise SafeCliError(invalid_code)


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
        left.st_nlink,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
        right.st_nlink,
    )


def _same_path_and_open_snapshot(
    path_metadata: os.stat_result, opened_metadata: os.stat_result
) -> bool:
    """Compare path and handle views without Windows' divergent ctime view."""

    return (
        path_metadata.st_dev,
        path_metadata.st_ino,
        path_metadata.st_size,
        path_metadata.st_mtime_ns,
        path_metadata.st_nlink,
    ) == (
        opened_metadata.st_dev,
        opened_metadata.st_ino,
        opened_metadata.st_size,
        opened_metadata.st_mtime_ns,
        opened_metadata.st_nlink,
    )


def _capture_open_file_identity(
    path: Path, opened_metadata: os.stat_result, *, invalid_code: str
) -> FileIdentity:
    path_metadata = _safe_file_stat(path, invalid_code=invalid_code)
    if not _same_path_and_open_snapshot(path_metadata, opened_metadata):
        raise SafeCliError(invalid_code)
    return _file_identity(path_metadata, invalid_code=invalid_code)


def _bounded_file_sha256(
    path: Path,
    *,
    maximum_bytes: int,
    invalid_code: str,
    too_large_code: str,
) -> tuple[int, str]:
    before = _safe_file_stat(path, invalid_code=invalid_code)
    if before.st_size > maximum_bytes:
        raise SafeCliError(too_large_code)
    digest = hashlib.sha256()
    consumed = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_path_and_open_snapshot(before, opened)
        ):
            os.close(descriptor)
            raise SafeCliError(invalid_code)
        with os.fdopen(descriptor, "rb") as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, maximum_bytes + 1 - consumed))
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > maximum_bytes:
                    raise SafeCliError(too_large_code)
                digest.update(chunk)
            opened_after = os.fstat(handle.fileno())
    except SafeCliError:
        raise
    except OSError:
        raise SafeCliError(invalid_code) from None
    after = _safe_file_stat(path, invalid_code=invalid_code)
    if (
        consumed != before.st_size
        or not _same_file_snapshot(opened, opened_after)
        or not _same_file_snapshot(before, after)
    ):
        raise SafeCliError(invalid_code)
    return consumed, digest.hexdigest()


def _bounded_file_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    invalid_code: str,
    too_large_code: str,
) -> tuple[bytes, str]:
    before = _safe_file_stat(path, invalid_code=invalid_code)
    if before.st_size > maximum_bytes:
        raise SafeCliError(too_large_code)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_path_and_open_snapshot(before, opened)
        ):
            os.close(descriptor)
            raise SafeCliError(invalid_code)
        with os.fdopen(descriptor, "rb") as handle:
            payload = handle.read(maximum_bytes + 1)
            opened_after = os.fstat(handle.fileno())
    except SafeCliError:
        raise
    except OSError:
        raise SafeCliError(invalid_code) from None
    if len(payload) > maximum_bytes:
        raise SafeCliError(too_large_code)
    after = _safe_file_stat(path, invalid_code=invalid_code)
    if (
        len(payload) != before.st_size
        or not _same_file_snapshot(opened, opened_after)
        or not _same_file_snapshot(before, after)
    ):
        raise SafeCliError(invalid_code)
    return payload, hashlib.sha256(payload).hexdigest()


def _absolute_without_links(path: Path, *, invalid_code: str) -> Path:
    _reject_non_local_path(path)
    supplied = Path(path).expanduser()
    _reject_non_local_path(supplied)
    try:
        absolute = Path(os.path.abspath(os.fspath(supplied)))
    except (OSError, TypeError, ValueError):
        raise SafeCliError(invalid_code) from None
    _reject_non_local_path(absolute)

    # ``resolve`` detects any symlink/junction/reparse ancestor, while the
    # explicit walk also catches a link-like final component that does not
    # currently resolve to a live target.
    current = absolute
    while True:
        if current.exists() or _is_link_like(current):
            if _is_link_like(current):
                raise SafeCliError(invalid_code)
        if current == current.parent:
            break
        current = current.parent
    try:
        resolved = absolute.resolve(strict=False)
    except OSError:
        raise SafeCliError(invalid_code) from None
    if not _same_path(absolute, resolved):
        raise SafeCliError(invalid_code)
    return resolved


def _validate_backup_directory_path(
    path: Path,
    *,
    workspace: "Workspace",
    must_exist: bool,
) -> Path:
    invalid_code = "backup_invalid" if must_exist else "unsafe_workspace"
    resolved = _absolute_without_links(path, invalid_code=invalid_code)
    anchor = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    if any(
        _same_path(resolved, boundary)
        for boundary in {anchor, home, REPOSITORY_ROOT, *REPOSITORY_ROOT.parents}
    ):
        raise SafeCliError(invalid_code)
    if _is_relative_to(resolved, workspace.root) or _is_relative_to(
        workspace.root, resolved
    ):
        raise SafeCliError(invalid_code)

    if must_exist:
        try:
            if _is_link_like(resolved) or not resolved.is_dir():
                raise SafeCliError("backup_invalid")
            if not _same_path(resolved.resolve(strict=True), resolved):
                raise SafeCliError("backup_invalid")
        except SafeCliError:
            raise
        except OSError:
            raise SafeCliError("backup_invalid") from None
        return resolved

    if resolved.exists():
        raise SafeCliError("backup_exists")
    if _is_link_like(resolved):
        raise SafeCliError("unsafe_workspace")
    parent = resolved.parent
    try:
        if _is_link_like(parent) or not parent.is_dir():
            raise SafeCliError("unsafe_workspace")
        if not _same_path(parent.resolve(strict=True), parent):
            raise SafeCliError("unsafe_workspace")
    except SafeCliError:
        raise
    except OSError:
        raise SafeCliError("unsafe_workspace") from None
    return resolved


def _quote_sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _safe_catalog_identifier(value: object) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= 64):
        raise SafeCliError("backup_failed")
    if not value.isascii() or not (value[0].isalpha() or value[0] == "_"):
        raise SafeCliError("backup_failed")
    if not all(character.isalnum() or character == "_" for character in value):
        raise SafeCliError("backup_failed")
    return value


def _schema_version(
    connection: object, table_name: str, *, required: bool
) -> str | None:
    safe_table = _safe_catalog_identifier(table_name)
    exists = connection.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = ? AND table_type = 'BASE TABLE'",
        [safe_table],
    ).fetchone()
    if exists is None:
        if required:
            raise SafeCliError("backup_failed")
        return None
    row = connection.execute(
        f"SELECT value FROM {_quote_sql_identifier(safe_table)} "
        "WHERE key = 'schema_version'"
    ).fetchone()
    if row is None or not isinstance(row[0], str) or not (1 <= len(row[0]) <= 32):
        raise SafeCliError("backup_failed")
    return row[0]


def _catalog_summary(connection: object) -> dict[str, object]:
    records = connection.execute(
        "SELECT table_schema, table_name, table_type "
        "FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
        "ORDER BY table_schema, table_name LIMIT ?",
        [_BACKUP_MAX_TABLES + _BACKUP_MAX_VIEWS + 1],
    ).fetchall()
    if len(records) > _BACKUP_MAX_TABLES + _BACKUP_MAX_VIEWS:
        raise SafeCliError("backup_too_large")
    schemas: set[str] = set()
    tables: list[dict[str, object]] = []
    view_count = 0
    for raw_schema, raw_name, raw_type in records:
        schema = _safe_catalog_identifier(raw_schema)
        name = _safe_catalog_identifier(raw_name)
        if raw_type == "VIEW":
            view_count += 1
            if view_count > _BACKUP_MAX_VIEWS:
                raise SafeCliError("backup_too_large")
            continue
        if raw_type != "BASE TABLE":
            raise SafeCliError("backup_failed")
        schemas.add(schema)
        row = connection.execute(
            "SELECT COUNT(*) FROM "
            f"{_quote_sql_identifier(schema)}.{_quote_sql_identifier(name)}"
        ).fetchone()
        if row is None or not isinstance(row[0], int) or row[0] < 0:
            raise SafeCliError("backup_failed")
        tables.append({"schema": schema, "name": name, "rows": row[0]})
        if len(tables) > _BACKUP_MAX_TABLES:
            raise SafeCliError("backup_too_large")
        if sum(int(table["rows"]) for table in tables) > _BACKUP_MAX_ROWS:
            raise SafeCliError("backup_too_large")
    if not tables:
        raise SafeCliError("backup_failed")
    if len(schemas) > _BACKUP_MAX_SCHEMAS:
        raise SafeCliError("backup_too_large")
    return {
        "catalog": {
            "schema_count": len(schemas),
            "table_count": len(tables),
            "view_count": view_count,
        },
        "tables": tables,
        "row_count": sum(int(table["rows"]) for table in tables),
    }


def _fingerprint_value(digest: object, value: object) -> None:
    type_name = type(value).__name__.encode("ascii", errors="backslashreplace")
    if value is None:
        rendered = b""
    elif isinstance(value, bytes):
        rendered = value
    elif isinstance(value, str):
        rendered = value.encode("utf-8", errors="strict")
    elif isinstance(value, float):
        rendered = value.hex().encode("ascii")
    elif isinstance(value, (bool, int)):
        rendered = str(value).encode("ascii")
    elif isinstance(value, datetime):
        rendered = value.isoformat().encode("ascii")
    elif isinstance(value, (list, tuple)):
        nested = hashlib.sha256()
        for item in value:
            _fingerprint_value(nested, item)
        rendered = nested.digest()
    elif isinstance(value, dict):
        nested = hashlib.sha256()
        for key in sorted(value, key=lambda item: repr(item)):
            _fingerprint_value(nested, key)
            _fingerprint_value(nested, value[key])
        rendered = nested.digest()
    else:
        # Decimal, date, time and UUID values returned by DuckDB all have
        # deterministic string forms within the exact DuckDB version bound in
        # the manifest.  No rendered value is persisted or emitted.
        rendered = str(value).encode("utf-8", errors="strict")
    digest.update(len(type_name).to_bytes(2, "big"))
    digest.update(type_name)
    digest.update(len(rendered).to_bytes(8, "big"))
    digest.update(rendered)


def _logical_catalog_sha256(
    connection: object, tables: Sequence[Mapping[str, object]]
) -> str:
    digest = hashlib.sha256(b"networkagent-local-logical-catalog-v1\0")
    metadata_queries = (
        (
            "columns",
            "SELECT table_schema, table_name, column_name, ordinal_position, "
            "column_default, is_nullable, data_type, character_maximum_length, "
            "numeric_precision, numeric_scale, datetime_precision, is_identity, "
            "identity_generation, generation_expression "
            "FROM information_schema.columns "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name, ordinal_position",
        ),
        (
            "table_constraints",
            "SELECT constraint_schema, constraint_name, table_schema, table_name, "
            "constraint_type, is_deferrable, initially_deferred, enforced, "
            "nulls_distinct FROM information_schema.table_constraints "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name, constraint_name",
        ),
        (
            "key_column_usage",
            "SELECT constraint_schema, constraint_name, table_schema, table_name, "
            "column_name, ordinal_position, position_in_unique_constraint "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name, constraint_name, ordinal_position",
        ),
        (
            "referential_constraints",
            "SELECT constraint_schema, constraint_name, unique_constraint_schema, "
            "unique_constraint_name, match_option, update_rule, delete_rule "
            "FROM information_schema.referential_constraints "
            "ORDER BY constraint_schema, constraint_name",
        ),
        (
            "check_constraints",
            "SELECT constraint_schema, constraint_name, check_clause "
            "FROM information_schema.check_constraints "
            "ORDER BY constraint_schema, constraint_name",
        ),
        (
            "indexes",
            "SELECT schema_name, index_name, table_name, is_unique, is_primary, "
            "expressions, sql FROM duckdb_indexes() "
            "WHERE database_name = current_database() "
            "ORDER BY schema_name, table_name, index_name",
        ),
        (
            "sequences",
            "SELECT schema_name, sequence_name, start_value, min_value, max_value, "
            "increment_by, cycle, last_value, sql FROM duckdb_sequences() "
            "WHERE database_name = current_database() "
            "ORDER BY schema_name, sequence_name",
        ),
        (
            "views",
            "SELECT schema_name, view_name, column_count, sql, is_bound "
            "FROM duckdb_views() WHERE database_name = current_database() "
            "AND internal = false ORDER BY schema_name, view_name",
        ),
        (
            "macros",
            "SELECT schema_name, function_name, function_type, return_type, "
            "parameters, parameter_types, varargs, macro_definition, "
            "has_side_effects FROM duckdb_functions() "
            "WHERE database_name = current_database() AND internal = false "
            "ORDER BY schema_name, function_name, function_type",
        ),
    )
    for label, query in metadata_queries:
        _fingerprint_value(digest, label)
        rows = connection.execute(
            f"{query} LIMIT {_BACKUP_MAX_CATALOG_RECORDS + 1}"
        ).fetchall()
        if len(rows) > _BACKUP_MAX_CATALOG_RECORDS:
            raise SafeCliError("backup_too_large")
        for row in rows:
            _fingerprint_value(digest, row)

    for table in tables:
        schema = _safe_catalog_identifier(table["schema"])
        name = _safe_catalog_identifier(table["name"])
        _fingerprint_value(digest, (schema, name))
        cursor = connection.execute(
            "SELECT * FROM "
            f"{_quote_sql_identifier(schema)}.{_quote_sql_identifier(name)} "
            "ORDER BY ALL"
        )
        while True:
            rows = cursor.fetchmany(1024)
            if not rows:
                break
            for row in rows:
                _fingerprint_value(digest, row)
    return digest.hexdigest()


def _database_metadata(connection: object) -> dict[str, object]:
    summary = _catalog_summary(connection)
    local_schema = _schema_version(connection, "local_schema_metadata", required=True)
    assurance_schema = _schema_version(
        connection, "assurance_schema_metadata", required=False
    )
    version = connection.execute("PRAGMA version").fetchone()
    storage = connection.execute(
        "SELECT current_setting('storage_compatibility_version')"
    ).fetchone()
    if (
        version is None
        or not isinstance(version[0], str)
        or not (1 <= len(version[0]) <= 64)
        or storage is None
        or not isinstance(storage[0], str)
        or not (1 <= len(storage[0]) <= 64)
    ):
        raise SafeCliError("backup_failed")
    return {
        **summary,
        "logical_sha256": _logical_catalog_sha256(connection, summary["tables"]),
        "schemas": {"local": local_schema, "assurance": assurance_schema},
        "duckdb": {
            "library_version": version[0],
            "storage_version": storage[0],
        },
    }


def _looks_like_lock_error(error: BaseException) -> bool:
    rendered = str(error).lower()
    return "lock" in rendered or any(
        marker in rendered
        for marker in (
            "another process",
            "conflicting lock",
            "could not set lock",
            "database is locked",
            "file is already open",
            "file handle conflict",
            "cannot open file",
            "being used by another process",
        )
    )


def _validate_database_sidecars(
    database_path: Path, *, invalid_code: str, allow_wal: bool
) -> None:
    wal_path = Path(f"{database_path}.wal")
    if wal_path.exists() or _is_link_like(wal_path):
        _safe_file_stat(wal_path, invalid_code=invalid_code)
        if not allow_wal:
            raise SafeCliError(invalid_code)
    temp_path = Path(f"{database_path}.tmp")
    if temp_path.exists() or _is_link_like(temp_path):
        # All maintenance connections disable spilling.  A pre-existing temp
        # sidecar is therefore never required and is not traversed or removed.
        raise SafeCliError(invalid_code)


def _duckdb_connect(
    duckdb_module: object,
    database_path: Path,
    *,
    read_only: bool,
    allow_attach: bool = False,
) -> object:
    config = {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "enable_external_access": "true" if allow_attach else "false",
        "max_temp_directory_size": "0B",
    }
    return duckdb_module.connect(str(database_path), read_only=read_only, config=config)


@contextmanager
def _maintenance_lock(workspace: "Workspace") -> Iterator[None]:
    workspace.marker()
    workspace._validate_owned_directory(workspace.state_dir)
    lock_path = workspace.state_dir / _MAINTENANCE_LOCK_NAME
    if lock_path.exists() or _is_link_like(lock_path):
        _safe_file_stat(lock_path, invalid_code="unsafe_workspace")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b")
    except OSError:
        raise SafeCliError("workspace_busy") from None
    acquired = False
    try:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SafeCliError("unsafe_workspace")
        path_metadata = _safe_file_stat(lock_path, invalid_code="unsafe_workspace")
        if (metadata.st_dev, metadata.st_ino) != (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ):
            raise SafeCliError("unsafe_workspace")
        if metadata.st_size == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, ImportError):
            raise SafeCliError("workspace_busy") from None
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass
        handle.close()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory while refusing an existing target."""

    if os.name == "nt":
        try:
            os.rename(source, destination)
            return
        except FileExistsError:
            raise SafeCliError("backup_exists") from None
        except OSError as error:
            if getattr(error, "winerror", None) in {80, 183}:
                raise SafeCliError("backup_exists") from None
            raise SafeCliError("backup_failed") from None

    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is not None:
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            encoded_source,
            -100,
            encoded_destination,
            1,
        )
    else:
        renamex_np = getattr(library, "renamex_np", None)
        if renamex_np is None:
            raise SafeCliError("backup_failed")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(encoded_source, encoded_destination, 0x00000004)
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise SafeCliError("backup_exists")
    raise SafeCliError("backup_failed")


def _require_exact_keys(
    value: object, keys: set[str], *, invalid_code: str
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SafeCliError(invalid_code)
    return value


def _validate_manifest(value: object) -> dict[str, object]:
    manifest = _require_exact_keys(
        value,
        {
            "backup_id",
            "catalog",
            "created_at",
            "database",
            "duckdb",
            "schema",
            "schemas",
            "source",
        },
        invalid_code="backup_invalid",
    )
    if manifest["schema"] != BACKUP_SCHEMA_VERSION:
        raise SafeCliError("backup_invalid")
    try:
        parsed_id = uuid.UUID(str(manifest["backup_id"]))
    except (ValueError, TypeError, AttributeError):
        raise SafeCliError("backup_invalid") from None
    if str(parsed_id) != manifest["backup_id"]:
        raise SafeCliError("backup_invalid")
    created_at = manifest["created_at"]
    if not isinstance(created_at, str):
        raise SafeCliError("backup_invalid")
    try:
        parsed_time = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        raise SafeCliError("backup_invalid") from None
    if parsed_time.strftime("%Y-%m-%dT%H:%M:%SZ") != created_at:
        raise SafeCliError("backup_invalid")

    source = _require_exact_keys(
        manifest["source"], {"workspace_marker_sha256"}, invalid_code="backup_invalid"
    )
    if not _is_sha256(source["workspace_marker_sha256"]):
        raise SafeCliError("backup_invalid")
    schemas = _require_exact_keys(
        manifest["schemas"], {"assurance", "local"}, invalid_code="backup_invalid"
    )
    if not isinstance(schemas["local"], str) or not (1 <= len(schemas["local"]) <= 32):
        raise SafeCliError("backup_invalid")
    if schemas["assurance"] is not None and (
        not isinstance(schemas["assurance"], str)
        or not (1 <= len(schemas["assurance"]) <= 32)
    ):
        raise SafeCliError("backup_invalid")
    duckdb_metadata = _require_exact_keys(
        manifest["duckdb"],
        {"library_version", "storage_version"},
        invalid_code="backup_invalid",
    )
    if not all(
        isinstance(duckdb_metadata[field], str)
        and 1 <= len(duckdb_metadata[field]) <= 64
        for field in ("library_version", "storage_version")
    ):
        raise SafeCliError("backup_invalid")
    database = _require_exact_keys(
        manifest["database"],
        {"bytes", "checkpointed", "filename", "sha256"},
        invalid_code="backup_invalid",
    )
    if database["filename"] != BACKUP_DATABASE_NAME:
        raise SafeCliError("backup_invalid")
    if type(database["bytes"]) is not int or not (
        1 <= database["bytes"] <= _BACKUP_MAX_DATABASE_BYTES
    ):
        raise SafeCliError("backup_invalid")
    if not _is_sha256(database["sha256"]) or database["checkpointed"] is not True:
        raise SafeCliError("backup_invalid")

    catalog = _require_exact_keys(
        manifest["catalog"],
        {
            "logical_equivalence",
            "logical_sha256",
            "row_count",
            "schema_count",
            "table_count",
            "tables",
            "view_count",
        },
        invalid_code="backup_invalid",
    )
    if catalog["logical_equivalence"] is not True:
        raise SafeCliError("backup_invalid")
    if not _is_sha256(catalog["logical_sha256"]):
        raise SafeCliError("backup_invalid")
    for field in ("row_count", "schema_count", "table_count", "view_count"):
        if type(catalog[field]) is not int or catalog[field] < 0:
            raise SafeCliError("backup_invalid")
    if (
        catalog["row_count"] > _BACKUP_MAX_ROWS
        or catalog["schema_count"] > _BACKUP_MAX_SCHEMAS
        or catalog["table_count"] > _BACKUP_MAX_TABLES
        or catalog["view_count"] > _BACKUP_MAX_VIEWS
    ):
        raise SafeCliError("backup_too_large")
    raw_tables = catalog["tables"]
    if (
        not isinstance(raw_tables, list)
        or not raw_tables
        or len(raw_tables) > _BACKUP_MAX_TABLES
    ):
        raise SafeCliError("backup_invalid")
    tables: list[dict[str, object]] = []
    for raw_table in raw_tables:
        table = _require_exact_keys(
            raw_table, {"name", "rows", "schema"}, invalid_code="backup_invalid"
        )
        try:
            schema = _safe_catalog_identifier(table["schema"])
            name = _safe_catalog_identifier(table["name"])
        except SafeCliError:
            raise SafeCliError("backup_invalid") from None
        if type(table["rows"]) is not int or table["rows"] < 0:
            raise SafeCliError("backup_invalid")
        tables.append({"schema": schema, "name": name, "rows": table["rows"]})
    if tables != sorted(
        tables, key=lambda item: (str(item["schema"]), str(item["name"]))
    ):
        raise SafeCliError("backup_invalid")
    if len({(table["schema"], table["name"]) for table in tables}) != len(tables):
        raise SafeCliError("backup_invalid")
    if catalog["table_count"] != len(tables):
        raise SafeCliError("backup_invalid")
    if catalog["schema_count"] != len({str(table["schema"]) for table in tables}):
        # The Local profile currently has no view-only schemas.  Requiring
        # this exact relation keeps the compact manifest independently
        # checkable without trusting unlisted catalog objects.
        raise SafeCliError("backup_invalid")
    if catalog["row_count"] != sum(int(table["rows"]) for table in tables):
        raise SafeCliError("backup_invalid")
    return manifest


def _local_backup_ownership_sha256(
    directory: Path,
    *,
    expected_directory: DirectoryIdentity,
    expected_children: Mapping[str, FileIdentity],
) -> str:
    """Bind outer-process cleanup to this exact local published tree.

    The digest is process-local cleanup metadata, not portable backup evidence.
    It deliberately contains no path or raw filesystem identity in public JSON.
    """

    expected_names = {BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME}
    if set(expected_children) != expected_names:
        raise SafeCliError("backup_failed")
    _strict_directory_identity(expected_directory, invalid_code="backup_failed")
    _exact_backup_entries(directory, invalid_code="backup_failed")
    actual_directory = _directory_identity(directory, invalid_code="backup_failed")
    if actual_directory != expected_directory:
        raise SafeCliError("backup_failed")
    entries: list[list[object]] = [
        [
            "directory",
            *_strict_directory_identity(actual_directory, invalid_code="backup_failed"),
        ]
    ]
    for name in (BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME):
        expected = expected_children[name]
        _strict_file_identity(expected, invalid_code="backup_failed")
        metadata = _require_file_identity(
            directory / name, expected, invalid_code="backup_failed"
        )
        entries.append(
            [
                name,
                *_strict_file_identity(
                    _file_identity(metadata, invalid_code="backup_failed"),
                    invalid_code="backup_failed",
                ),
            ]
        )

    # Recheck the exact closure and identities immediately before constructing
    # the response so an outer wrapper never adopts a same-content replacement.
    _exact_backup_entries(directory, invalid_code="backup_failed")
    _require_directory_identity(
        directory, expected_directory, invalid_code="backup_failed"
    )
    for name in (BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME):
        _require_file_identity(
            directory / name,
            expected_children[name],
            invalid_code="backup_failed",
        )
    encoded = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(_LOCAL_BACKUP_OWNERSHIP_DOMAIN + encoded).hexdigest()


def _backup_public_summary(
    manifest: Mapping[str, object],
    *,
    manifest_bytes: int,
    manifest_sha256: str,
    local_ownership_sha256: str,
    changed: bool,
) -> dict[str, object]:
    database = manifest["database"]
    catalog = manifest["catalog"]
    assert isinstance(database, Mapping)
    assert isinstance(catalog, Mapping)
    if not _is_sha256(local_ownership_sha256):
        raise SafeCliError("backup_failed")
    return {
        "schema": BACKUP_SCHEMA_VERSION,
        "changed": changed,
        "manifest": {
            "filename": BACKUP_MANIFEST_NAME,
            "bytes": manifest_bytes,
            "sha256": manifest_sha256,
        },
        "database": {
            "filename": BACKUP_DATABASE_NAME,
            "bytes": database["bytes"],
            "sha256": database["sha256"],
        },
        "catalog": {
            "schema_count": catalog["schema_count"],
            "table_count": catalog["table_count"],
            "view_count": catalog["view_count"],
        },
        "tables": catalog["tables"],
        "row_count": catalog["row_count"],
        "checkpointed": True,
        "logical_equivalence": True,
        "local_ownership_sha256": local_ownership_sha256,
    }


def _restore_public_summary(
    manifest: Mapping[str, object],
    *,
    manifest_sha256: str,
    changed: bool,
) -> dict[str, object]:
    database = manifest["database"]
    catalog = manifest["catalog"]
    assert isinstance(database, Mapping)
    assert isinstance(catalog, Mapping)
    return {
        "schema": BACKUP_SCHEMA_VERSION,
        "changed": changed,
        "manifest_sha256": manifest_sha256,
        "database_sha256": database["sha256"],
        "catalog": {
            "schema_count": catalog["schema_count"],
            "table_count": catalog["table_count"],
            "view_count": catalog["view_count"],
        },
        "tables": catalog["tables"],
        "row_count": catalog["row_count"],
        "verified": True,
    }


def _exact_backup_entries(directory: Path, *, invalid_code: str) -> None:
    try:
        names = {entry.name for entry in directory.iterdir()}
    except OSError:
        raise SafeCliError(invalid_code) from None
    if names != {BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME}:
        raise SafeCliError(invalid_code)


def _reject_duplicate_json_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _load_and_verify_backup(
    directory: Path,
) -> tuple[dict[str, object], int, str]:
    _exact_backup_entries(directory, invalid_code="backup_invalid")
    manifest_path = directory / BACKUP_MANIFEST_NAME
    database_path = directory / BACKUP_DATABASE_NAME
    manifest_raw, manifest_sha256 = _bounded_file_bytes(
        manifest_path,
        maximum_bytes=_BACKUP_MAX_MANIFEST_BYTES,
        invalid_code="backup_invalid",
        too_large_code="backup_too_large",
    )
    if not manifest_raw:
        raise SafeCliError("backup_invalid")
    try:
        decoded = manifest_raw.decode("utf-8", errors="strict")
        manifest_value = json.loads(
            decoded, object_pairs_hook=_reject_duplicate_json_pairs
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise SafeCliError("backup_invalid") from None
    manifest = _validate_manifest(manifest_value)
    try:
        canonical = _canonical_json_bytes(manifest)
    except SafeCliError:
        raise SafeCliError("backup_invalid") from None
    if manifest_raw != canonical:
        raise SafeCliError("backup_invalid")

    database = manifest["database"]
    assert isinstance(database, Mapping)
    database_bytes, database_sha256 = _bounded_file_sha256(
        database_path,
        maximum_bytes=_BACKUP_MAX_DATABASE_BYTES,
        invalid_code="backup_invalid",
        too_large_code="backup_too_large",
    )
    if database_bytes != database["bytes"] or database_sha256 != database["sha256"]:
        raise SafeCliError("backup_invalid")

    database_snapshot = _safe_file_stat(database_path, invalid_code="backup_invalid")
    _validate_database_sidecars(
        database_path, invalid_code="backup_invalid", allow_wal=False
    )
    try:
        import duckdb

        connection = _duckdb_connect(duckdb, database_path, read_only=True)
        try:
            actual = _database_metadata(connection)
        finally:
            connection.close()
    except SafeCliError:
        raise SafeCliError("backup_invalid") from None
    except Exception:
        raise SafeCliError("backup_invalid") from None
    database_after = _safe_file_stat(database_path, invalid_code="backup_invalid")
    if not _same_file_snapshot(database_snapshot, database_after):
        raise SafeCliError("backup_invalid")
    catalog = manifest["catalog"]
    assert isinstance(catalog, Mapping)
    if (
        actual["catalog"]
        != {
            "schema_count": catalog["schema_count"],
            "table_count": catalog["table_count"],
            "view_count": catalog["view_count"],
        }
        or actual["tables"] != catalog["tables"]
        or actual["row_count"] != catalog["row_count"]
        or actual["schemas"] != manifest["schemas"]
        or actual["duckdb"] != manifest["duckdb"]
        or actual["logical_sha256"] != catalog["logical_sha256"]
    ):
        raise SafeCliError("backup_invalid")
    _exact_backup_entries(directory, invalid_code="backup_invalid")
    return manifest, len(manifest_raw), manifest_sha256


def _cleanup_partial_backup(
    directory: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_children: Mapping[str, FileIdentity],
) -> None:
    _strict_directory_identity(expected_identity, invalid_code="backup_failed")
    if not directory.exists() and not _is_link_like(directory):
        return
    try:
        if _is_link_like(directory) or not directory.is_dir():
            raise SafeCliError("unsafe_workspace")
        metadata = directory.stat(follow_symlinks=False)
        actual_identity = (metadata.st_dev, metadata.st_ino)
        _strict_directory_identity(actual_identity, invalid_code="backup_failed")
        if actual_identity != expected_identity:
            raise SafeCliError("backup_failed")
        names = {entry.name for entry in directory.iterdir()}
    except SafeCliError:
        raise
    except OSError:
        raise SafeCliError("backup_failed") from None
    if not names.issubset(set(expected_children)):
        raise SafeCliError("backup_failed")
    # Validate every child before deleting any child.  A same-name replacement
    # is preserved and turns cleanup into a safe failure.
    for name in sorted(names):
        _require_file_identity(
            directory / name,
            expected_children[name],
            invalid_code="backup_failed",
        )
    _require_directory_identity(
        directory, expected_identity, invalid_code="backup_failed"
    )
    for name in sorted(names):
        _unlink_file_identity(
            directory / name,
            expected_children[name],
            invalid_code="backup_failed",
            failure_code="backup_failed",
        )
    _require_directory_identity(
        directory, expected_identity, invalid_code="backup_failed"
    )
    try:
        directory.rmdir()
    except OSError:
        raise SafeCliError("backup_failed") from None


def _write_new_file(path: Path, payload: bytes, *, failure_code: str) -> FileIdentity:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    created_identity: FileIdentity | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SafeCliError(failure_code)
            created_location = (metadata.st_dev, metadata.st_ino)
            _strict_directory_identity(created_location, invalid_code=failure_code)
            try:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                if os.name != "nt":
                    os.fchmod(handle.fileno(), 0o600)
            finally:
                final_metadata = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(final_metadata.st_mode)
                    or final_metadata.st_nlink != 1
                    or (final_metadata.st_dev, final_metadata.st_ino)
                    != created_location
                ):
                    raise SafeCliError(failure_code)
                created_identity = _capture_open_file_identity(
                    path, final_metadata, invalid_code=failure_code
                )
        _require_file_identity(path, created_identity, invalid_code=failure_code)
        return created_identity
    except BaseException as error:
        if created_identity is not None and (path.exists() or _is_link_like(path)):
            try:
                _unlink_file_identity(
                    path,
                    created_identity,
                    invalid_code=failure_code,
                    failure_code=failure_code,
                )
            except SafeCliError:
                raise SafeCliError(failure_code) from None
        if isinstance(error, SafeCliError):
            raise
        raise SafeCliError(failure_code) from None


def _copy_database_to_restore_temp(
    source: Path,
    target: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> FileIdentity:
    source_before = _safe_file_stat(source, invalid_code="backup_invalid")
    if source_before.st_size != expected_bytes:
        raise SafeCliError("backup_invalid")
    if target.exists() or _is_link_like(target):
        # A fixed-name temp from another invocation has no current-operation
        # identity.  Preserve it and fail closed instead of deleting it.
        _safe_file_stat(target, invalid_code="unsafe_workspace")
        raise SafeCliError("restore_failed")

    source_flags = (
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    target_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = hashlib.sha256()
    copied = 0
    target_identity: FileIdentity | None = None
    try:
        source_descriptor = os.open(source, source_flags)
        opened_source = os.fstat(source_descriptor)
        if not _same_path_and_open_snapshot(source_before, opened_source):
            os.close(source_descriptor)
            raise SafeCliError("backup_invalid")
        try:
            target_descriptor = os.open(target, target_flags, 0o600)
        except OSError:
            os.close(source_descriptor)
            raise
        with os.fdopen(source_descriptor, "rb") as source_handle, os.fdopen(
            target_descriptor, "wb"
        ) as target_handle:
            target_metadata = os.fstat(target_handle.fileno())
            if (
                not stat.S_ISREG(target_metadata.st_mode)
                or target_metadata.st_nlink != 1
            ):
                raise SafeCliError("restore_failed")
            target_location = (target_metadata.st_dev, target_metadata.st_ino)
            _strict_directory_identity(target_location, invalid_code="restore_failed")
            try:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > _BACKUP_MAX_DATABASE_BYTES:
                        raise SafeCliError("backup_too_large")
                    digest.update(chunk)
                    target_handle.write(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
                if os.name != "nt":
                    os.fchmod(target_handle.fileno(), 0o600)
            finally:
                final_target_metadata = os.fstat(target_handle.fileno())
                if (
                    not stat.S_ISREG(final_target_metadata.st_mode)
                    or final_target_metadata.st_nlink != 1
                    or (
                        final_target_metadata.st_dev,
                        final_target_metadata.st_ino,
                    )
                    != target_location
                ):
                    raise SafeCliError("restore_failed")
                target_identity = _capture_open_file_identity(
                    target,
                    final_target_metadata,
                    invalid_code="restore_failed",
                )
            opened_source_after = os.fstat(source_handle.fileno())
    except BaseException as error:
        if target_identity is not None and (target.exists() or _is_link_like(target)):
            try:
                _unlink_file_identity(
                    target,
                    target_identity,
                    invalid_code="restore_failed",
                    failure_code="restore_failed",
                )
            except SafeCliError:
                raise SafeCliError("restore_failed") from None
        if isinstance(error, SafeCliError):
            raise
        raise SafeCliError("restore_failed") from None
    try:
        source_after = _safe_file_stat(source, invalid_code="backup_invalid")
        if (
            copied != expected_bytes
            or digest.hexdigest() != expected_sha256
            or not _same_file_snapshot(opened_source, opened_source_after)
            or not _same_file_snapshot(source_before, source_after)
        ):
            raise SafeCliError("backup_invalid")
        target_bytes, target_sha256 = _bounded_file_sha256(
            target,
            maximum_bytes=_BACKUP_MAX_DATABASE_BYTES,
            invalid_code="restore_failed",
            too_large_code="backup_too_large",
        )
        if target_bytes != expected_bytes or target_sha256 != expected_sha256:
            raise SafeCliError("restore_failed")
        if target_identity is None:
            raise SafeCliError("restore_failed")
        _require_file_identity(target, target_identity, invalid_code="restore_failed")
        return target_identity
    except BaseException as error:
        if target_identity is not None and (target.exists() or _is_link_like(target)):
            try:
                _unlink_file_identity(
                    target,
                    target_identity,
                    invalid_code="restore_failed",
                    failure_code="restore_failed",
                )
            except SafeCliError:
                raise SafeCliError("restore_failed") from None
        if isinstance(error, SafeCliError):
            raise
        raise SafeCliError("restore_failed") from None


class Workspace:
    """One explicitly selected, marker-owned local workspace."""

    def __init__(self, root: Path) -> None:
        self.root = _validate_workspace_path(root)
        self.marker_path = self.root / MARKER_NAME
        self.state_dir = self.root / "state"
        self.artifacts_dir = self.root / "artifacts"
        self.database_path = self.state_dir / "networkagent.duckdb"

    def _validate_root(self, *, required: bool) -> None:
        if not self.root.exists():
            if required:
                raise SafeCliError("workspace_not_initialized", exit_code=1)
            return
        if _is_link_like(self.root) or not self.root.is_dir():
            raise SafeCliError("unsafe_workspace")
        try:
            resolved = self.root.resolve(strict=True)
        except OSError:
            raise SafeCliError("unsafe_workspace") from None
        if not _same_path(resolved, self.root):
            raise SafeCliError("unsafe_workspace")

    def _validate_owned_directory(self, target: Path) -> None:
        self._validate_root(required=True)
        if _is_link_like(target) or not target.is_dir():
            raise SafeCliError("unsafe_workspace")
        try:
            root = self.root.resolve(strict=True)
            resolved = target.resolve(strict=True)
        except OSError:
            raise SafeCliError("unsafe_workspace") from None
        if (
            not _same_path(target.parent, self.root)
            or not _same_path(resolved.parent, root)
            or resolved.name != target.name
        ):
            raise SafeCliError("unsafe_workspace")

    def _read_marker(self) -> dict[str, object]:
        self._validate_root(required=True)
        if not self.marker_path.is_file() or _is_link_like(self.marker_path):
            raise SafeCliError("workspace_not_initialized", exit_code=1)
        try:
            marker_raw, _ = _bounded_file_bytes(
                self.marker_path,
                maximum_bytes=_BACKUP_MAX_MANIFEST_BYTES,
                invalid_code="workspace_not_owned",
                too_large_code="workspace_not_owned",
            )
            value = json.loads(
                marker_raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_json_pairs,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            raise SafeCliError("workspace_not_owned") from None
        if (
            not isinstance(value, dict)
            or set(value) != {"kind", "owned_entries", "schema_version", "workspace_id"}
            or value.get("kind") != "networkagent-local-stack"
        ):
            raise SafeCliError("workspace_not_owned")
        if value.get("schema_version") != STACK_SCHEMA_VERSION:
            raise SafeCliError("workspace_not_owned")
        workspace_id = value.get("workspace_id")
        if value.get("owned_entries") != ["state", "artifacts", MARKER_NAME]:
            raise SafeCliError("workspace_not_owned")
        try:
            uuid.UUID(str(workspace_id))
        except (ValueError, TypeError, AttributeError):
            raise SafeCliError("workspace_not_owned") from None
        canonical_marker = (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if marker_raw != canonical_marker:
            raise SafeCliError("workspace_not_owned")
        for target in (self.state_dir, self.artifacts_dir):
            if target.exists() or _is_link_like(target):
                self._validate_owned_directory(target)
        return value

    def marker(self) -> dict[str, object]:
        return self._read_marker()

    def prepare_init(self) -> tuple[str, bool, bool]:
        root_existed = self.root.exists()
        if root_existed:
            self._validate_root(required=True)
        if self.marker_path.exists():
            marker = self._read_marker()
            return str(marker["workspace_id"]), False, False
        if self.root.exists() and any(self.root.iterdir()):
            raise SafeCliError("workspace_not_owned")
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_root(required=True)
        self.state_dir.mkdir()
        self.artifacts_dir.mkdir()
        self._validate_owned_directory(self.state_dir)
        self._validate_owned_directory(self.artifacts_dir)
        return str(uuid.uuid4()), True, not root_existed

    def rollback_uncommitted_init(self, *, root_created: bool) -> None:
        """Restore the pre-init shape after a failed first initialization."""

        if self.marker_path.exists() or _is_link_like(self.marker_path):
            self._read_marker()
            return
        self._validate_root(required=True)
        for target in (self.state_dir, self.artifacts_dir):
            if target.exists() or _is_link_like(target):
                self._validate_owned_directory(target)
                shutil.rmtree(target)
        if root_created:
            try:
                self.root.rmdir()
            except OSError:
                # Never remove unexpected entries created outside this operation.
                pass

    def commit_marker(self, workspace_id: str) -> None:
        self._validate_owned_directory(self.state_dir)
        self._validate_owned_directory(self.artifacts_dir)
        marker = {
            "kind": "networkagent-local-stack",
            "schema_version": STACK_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "owned_entries": ["state", "artifacts", MARKER_NAME],
        }
        temporary = self.root / f"{MARKER_NAME}.tmp"
        if temporary.exists() or _is_link_like(temporary):
            raise SafeCliError("unsafe_workspace")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                handle.write(
                    json.dumps(
                        marker,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_root(required=True)
            temporary.replace(self.marker_path)
        except SafeCliError:
            raise
        except OSError:
            raise SafeCliError("unsafe_workspace") from None
        finally:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)

    def write_artifact(self, name: str, value: object) -> str:
        self._read_marker()
        if Path(name).name != name or not name:
            raise SafeCliError("unsafe_workspace")
        if not self.artifacts_dir.exists():
            try:
                self.artifacts_dir.mkdir()
            except OSError:
                raise SafeCliError("unsafe_workspace") from None
        self._validate_owned_directory(self.artifacts_dir)
        target = self.artifacts_dir / name
        temporary = self.artifacts_dir / f".{name}.tmp"
        if temporary.exists() or _is_link_like(temporary):
            raise SafeCliError("unsafe_workspace")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                handle.write(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_owned_directory(self.artifacts_dir)
            temporary.replace(target)
        except SafeCliError:
            raise
        except (OSError, TypeError, ValueError):
            raise SafeCliError("runtime_failed") from None
        finally:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)
        return f"artifacts/{name}"

    def reset(self) -> dict[str, object]:
        self._read_marker()
        removed: list[str] = []
        for label, target in (
            ("state", self.state_dir),
            ("artifacts", self.artifacts_dir),
        ):
            if target.exists() or _is_link_like(target):
                self._validate_owned_directory(target)
                shutil.rmtree(target)
                removed.append(label)
        self.marker_path.unlink()
        removed.append("marker")
        workspace_removed = False
        try:
            self.root.rmdir()
            workspace_removed = True
        except OSError:
            # User-owned extra entries are deliberately preserved.
            pass
        return {
            "reset": True,
            "removed": removed,
            "workspace_removed": workspace_removed,
            "preserved_unknown_entries": not workspace_removed,
        }


def _configure_import_paths() -> None:
    for source in reversed(_PACKAGE_SOURCES):
        rendered = str(source)
        if rendered not in sys.path:
            sys.path.insert(0, rendered)


def _can_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _port_available(port: int) -> bool:
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        candidate.bind((DEFAULT_HOST, port))
        return True
    except OSError:
        return False
    finally:
        candidate.close()


def _model_value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _assert_project_safe(value: object) -> None:
    """Apply the canonical sensitive-data boundary before durable/output JSON."""

    _configure_import_paths()
    try:
        from telco_domain import assert_model_safe
    except Exception:
        # doctor and dependency errors must remain usable before installation;
        # those payloads are fixed whitelists and contain no caller values.
        return
    try:
        assert_model_safe(value)
    except Exception:
        raise SafeCliError("runtime_failed") from None


def _safe_action_preview(action: object) -> dict[str, object]:
    resources = _model_value(action, "target_resources", ())
    if resources is None:
        resources = ()
    safe_resources = []
    for resource in resources:
        technology = _model_value(resource, "technology")
        safe_resources.append(
            {
                "resource_id": str(_model_value(resource, "resource_id", "")),
                "resource_type": str(
                    _enum_value(_model_value(resource, "resource_type", ""))
                ),
                "technology": (
                    None if technology is None else str(_enum_value(technology))
                ),
            }
        )
    return {
        "action_hash": str(_model_value(action, "action_hash", "")),
        "action_type": str(
            _enum_value(
                _model_value(
                    action, "action_type", _model_value(action, "kind", "SIMULATE")
                )
            )
        ),
        "resources": safe_resources,
        "risk": str(
            _enum_value(_model_value(action, "risk_level", "LOCAL_SIMULATION"))
        ),
    }


def _incident_state(result: object) -> str:
    incident = _model_value(result, "incident")
    status = _model_value(incident, "status", "UNKNOWN")
    return str(_enum_value(status))


class LocalStackRuntime:
    """Lazy adapter over the existing Local Profile and governance engine."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        _configure_import_paths()

    def _config(self) -> object:
        try:
            from telco_local import LocalProfileConfig
        except Exception:
            raise SafeCliError("dependencies_missing") from None
        return LocalProfileConfig(
            database_path=self.workspace.database_path,
            performance_csv_path=_SOURCE_INPUTS["performance"],
            safe_trace_csv_path=_SOURCE_INPUTS["safe_trace"],
            rules_dir=_SOURCE_INPUTS["rules"],
            documents_dir=_SOURCE_INPUTS["documents"],
            source_timezone="UTC",
        )

    def _assurance_config(self, port: int) -> object:
        try:
            from telco_assurance_agent.config import AssuranceConfig
        except Exception:
            raise SafeCliError("server_dependencies_missing") from None
        return AssuranceConfig(
            database_path=self.workspace.database_path,
            performance_csv_path=_SOURCE_INPUTS["performance"],
            safe_trace_csv_path=_SOURCE_INPUTS["safe_trace"],
            rules_dir=_SOURCE_INPUTS["rules"],
            documents_dir=_SOURCE_INPUTS["documents"],
            public_url=f"http://{DEFAULT_HOST}:{port}/",
            actor="local-stack-assurance",
            host=DEFAULT_HOST,
            port=port,
        )

    def doctor(self, *, port: int) -> dict[str, object]:
        version = sys.version_info
        python_supported = (3, 12) <= version[:2] < (3, 14)
        core_modules = ("duckdb", "pydantic", "telco_domain", "telco_local")
        core = all(_can_import(name) for name in core_modules)
        governance = _can_import("telco_local.governance")
        server = all(
            _can_import(name)
            for name in ("a2a", "starlette", "uvicorn", "telco_assurance_agent")
        )
        data_checks = {
            name: path.is_dir() if name in {"rules", "documents"} else path.is_file()
            for name, path in _SOURCE_INPUTS.items()
        }
        data_ready = all(data_checks.values())
        available = _port_available(port)
        demo_ready = python_supported and core and governance and data_ready
        return {
            "ready": demo_ready,
            "demo_ready": demo_ready,
            "server_ready": demo_ready and server and available,
            "python": {
                "supported": python_supported,
                "version": f"{version.major}.{version.minor}.{version.micro}",
            },
            "dependencies": {
                "core": core,
                "governance": governance,
                "server": server,
            },
            "data": {"ready": data_ready, "checks": data_checks},
            "port": {"number": port, "available": available},
            "network": {"bind_host": DEFAULT_HOST, "external_access": False},
        }

    def initialize(self) -> dict[str, object]:
        doctor = self.doctor(port=DEFAULT_PORT)
        if not doctor["python"]["supported"] or not doctor["dependencies"]["core"]:
            raise SafeCliError("dependencies_missing")
        if not doctor["data"]["ready"]:
            raise SafeCliError("runtime_failed")
        try:
            from telco_local import LocalProfile

            profile = LocalProfile.initialize(self._config(), reset=False)
            server_schema = False
            if doctor["dependencies"]["server"]:
                from telco_assurance_agent.app import initialize_assurance

                initialize_assurance(self._assurance_config(DEFAULT_PORT), reset=False)
                server_schema = True
            summary = profile.database_summary
            return {
                "schema_version": summary.schema_version,
                "performance_rows": summary.performance_rows,
                "trace_rows": summary.trace_rows,
                "incident_rows": summary.incident_rows,
                "server_schema": server_schema,
            }
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None

    def status(self, *, port: int) -> dict[str, object]:
        self.workspace.marker()
        try:
            from telco_local import LocalProfile

            profile = LocalProfile.open_existing(self._config())
            summary = profile.database_summary
        except Exception:
            return {
                "ready": False,
                "database": {"initialized": False},
                "server": {
                    "host": DEFAULT_HOST,
                    "port": port,
                    "available": _port_available(port),
                },
            }
        doctor = self.doctor(port=port)
        return {
            "ready": bool(doctor["demo_ready"]),
            "database": {
                "initialized": True,
                "schema_version": summary.schema_version,
                "performance_rows": summary.performance_rows,
                "trace_rows": summary.trace_rows,
                "incident_rows": summary.incident_rows,
            },
            "runtime": {
                "demo_ready": doctor["demo_ready"],
                "server_dependencies": doctor["dependencies"]["server"],
                "governance": doctor["dependencies"]["governance"],
            },
            "server": {
                "host": DEFAULT_HOST,
                "port": port,
                "available": doctor["port"]["available"],
                "external_access": False,
            },
        }

    async def _seed_container_demo(self) -> dict[str, object]:
        """Persist exactly one deterministic DETECTED incident, and nothing else."""

        try:
            from telco_local import LocalProfile
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        profile = LocalProfile.open_existing(self._config())
        repository = profile.incident_repository
        if await repository.list(limit=1, offset=0):
            raise SafeCliError("demo_seed_requires_fresh_workspace")

        triggers = await profile.detector.scan(
            "container-demo-seed-trace-v1",
            workflow_id="container-demo-seed-workflow-v1",
        )
        if not triggers:
            raise SafeCliError("no_candidates")
        selected = sorted(triggers, key=lambda item: item.incident_id)[0]
        digest = hashlib.sha256(selected.incident_id.encode("utf-8")).hexdigest()[:16]
        incident = await profile.detector.confirm(
            selected.incident_id,
            trace_id=f"container-demo-confirm-trace-{digest}",
            idempotency_key=f"container-demo-confirm-key-{digest}",
            actor="container-demo-seeder",
            reason="Seed one deterministic isolated container demo incident",
        )

        incidents = tuple(await repository.list(limit=2, offset=0))
        history = tuple(
            await repository.history(incident.incident_id, limit=2, offset=0)
        )
        if not (
            len(incidents) == 1
            and incidents[0].incident_id == incident.incident_id
            and str(_enum_value(incident.status)) == "DETECTED"
            and incident.revision == 0
            and not incident.rca_reports
            and not incident.recommendations
            and not incident.approvals
            and not incident.action_runs
            and not incident.verification_runs
            and len(history) == 1
            and history[0].revision == 0
            and history[0].from_status is None
            and str(_enum_value(history[0].to_status)) == "DETECTED"
        ):
            raise SafeCliError("demo_verification_failed")
        return {
            "candidate_count": len(triggers),
            "incident_id": incident.incident_id,
            "status": str(_enum_value(incident.status)),
            "revision": incident.revision,
        }

    def seed_container_demo(self) -> dict[str, object]:
        try:
            result = asyncio.run(self._seed_container_demo())
            _assert_project_safe(result)
            return result
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None

    async def _verify_container_demo(
        self, *, expected_status: str
    ) -> dict[str, object]:
        """Read and verify one completed offline demo without mutating it."""

        expected_verification = {
            "RESOLVED": "PASSED",
            "REOPENED": "FAILED",
        }.get(expected_status)
        if expected_verification is None:
            raise SafeCliError("invalid_arguments")
        try:
            from telco_local import LocalProfile
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        profile = LocalProfile.open_existing(self._config())
        repository = profile.incident_repository
        incidents = tuple(await repository.list(limit=2, offset=0))
        if len(incidents) != 1:
            raise SafeCliError("demo_verification_failed")
        incident = incidents[0]
        history = tuple(
            await repository.history(incident.incident_id, limit=9, offset=0)
        )
        reports = tuple(incident.rca_reports)
        recommendations = tuple(incident.recommendations)
        approvals = tuple(incident.approvals)
        action_runs = tuple(incident.action_runs)
        verification_runs = tuple(incident.verification_runs)
        if not (
            len(reports) == 1
            and len(recommendations) == 1
            and len(approvals) == 2
            and len(action_runs) == 1
            and len(verification_runs) == 1
        ):
            raise SafeCliError("demo_verification_failed")

        report = reports[0]
        action = recommendations[0]
        pending_approval, approved_approval = approvals
        action_run = action_runs[0]
        verification = verification_runs[0]
        approval_bindings_are_exact = all(
            approval.incident_id == incident.incident_id
            and approval.report_id == report.report_id
            and approval.report_version == report.version
            and approval.subject_id == action.action_id
            and approval.action_hash == action.action_hash
            for approval in approvals
        )
        expected_to_statuses = (
            "DETECTED",
            "TRIAGED",
            "INVESTIGATING",
            "RCA_COMPLETE",
            "AWAITING_APPROVAL",
            "REMEDIATING",
            "VERIFYING",
            expected_status,
        )
        expected_from_statuses = (
            None,
            "DETECTED",
            "TRIAGED",
            "INVESTIGATING",
            "RCA_COMPLETE",
            "AWAITING_APPROVAL",
            "REMEDIATING",
            "VERIFYING",
        )
        if not (
            str(_enum_value(incident.status)) == expected_status
            and incident.revision == 7
            and report.incident_id == incident.incident_id
            and len(report.recommendations) == 1
            and report.recommendations[0].action_hash == action.action_hash
            and action.action_type == "LOCAL_SIMULATION"
            and action.requires_approval is True
            and action.reversible is True
            and approval_bindings_are_exact
            and str(_enum_value(pending_approval.status)) == "PENDING"
            and pending_approval.sequence == 0
            and str(_enum_value(approved_approval.status)) == "APPROVED"
            and approved_approval.sequence == 1
            and pending_approval.request_id == approved_approval.request_id
            and action_run.incident_id == incident.incident_id
            and action_run.action_id == action.action_id
            and action_run.action_hash == action.action_hash
            and str(_enum_value(action_run.status)) == "SUCCEEDED"
            and action_run.attempt == 1
            and action_run.metadata == {"mode": "simulation", "side_effects": False}
            and verification.incident_id == incident.incident_id
            and verification.action_run_ids == (action_run.action_run_id,)
            and str(_enum_value(verification.status)) == expected_verification
            and verification.metadata.get("mode") == "simulation"
            and verification.metadata.get("side_effects") is False
            and verification.metadata.get("requested_outcome") == expected_verification
            and len(history) == 8
            and tuple(item.revision for item in history) == tuple(range(8))
            and tuple(str(_enum_value(item.to_status)) for item in history)
            == expected_to_statuses
            and tuple(
                None if item.from_status is None else str(_enum_value(item.from_status))
                for item in history
            )
            == expected_from_statuses
        ):
            raise SafeCliError("demo_verification_failed")
        return {
            "incident_id": incident.incident_id,
            "status": str(_enum_value(incident.status)),
            "expected_status": expected_status,
            "revision": incident.revision,
            "rca_reports": len(reports),
            "recommendations": len(recommendations),
            "approvals": len(approvals),
            "action_runs": len(action_runs),
            "verification_runs": len(verification_runs),
            "audit_events": len(history),
            "action": {
                "action_type": action.action_type,
                "status": str(_enum_value(action_run.status)),
                "side_effects": action_run.metadata["side_effects"],
            },
            "verification": {
                "status": str(_enum_value(verification.status)),
            },
        }

    def verify_container_demo(self, *, expected_status: str) -> dict[str, object]:
        try:
            result = asyncio.run(
                self._verify_container_demo(expected_status=expected_status)
            )
            _assert_project_safe(result)
            return result
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None

    async def _build_lifecycle_events(
        self, *, expected_status: str
    ) -> dict[str, object]:
        """Project one completed demo from durable records without mutations."""

        try:
            from telco_local import (
                LifecycleProjectionError,
                LocalProfile,
                build_lifecycle_projection,
            )
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        profile = LocalProfile.open_existing(self._config())
        repository = profile.incident_repository
        incidents = tuple(await repository.list(limit=2, offset=0))
        if len(incidents) != 1:
            raise SafeCliError("lifecycle_projection_failed")
        incident = incidents[0]
        history = tuple(
            await repository.history(incident.incident_id, limit=9, offset=0)
        )
        try:
            result = build_lifecycle_projection(
                incident,
                history,
                expected_status=expected_status,
            )
        except LifecycleProjectionError:
            raise SafeCliError("lifecycle_projection_failed") from None
        if not isinstance(result, dict):
            raise SafeCliError("lifecycle_projection_failed")
        return result

    @staticmethod
    def _database_digest(path: Path) -> str:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            return digest.hexdigest()
        except OSError:
            raise SafeCliError("runtime_failed") from None

    def lifecycle_events(self, *, expected_status: str) -> dict[str, object]:
        """Return a safe lifecycle projection and prove the database stayed fixed."""

        before = self._database_digest(self.workspace.database_path)
        try:
            result = asyncio.run(
                self._build_lifecycle_events(expected_status=expected_status)
            )
            _assert_project_safe(result)
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None
        after = self._database_digest(self.workspace.database_path)
        if before != after:
            raise SafeCliError("lifecycle_projection_failed")
        return result

    async def _run_demo(
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
        try:
            from telco_local import LocalProfile
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        profile = LocalProfile.open_existing(self._config())
        triggers = await profile.detector.scan(
            "local-stack-detect-trace-v1",
            workflow_id="local-stack-detect-workflow-v1",
        )
        if not triggers:
            raise SafeCliError("no_candidates")
        selected = sorted(triggers, key=lambda item: item.incident_id)[0]
        selected_incident = selected.incident
        candidate = {
            "incident_id": selected.incident_id,
            "severity": str(_enum_value(selected_incident.severity)),
            "technology": str(_enum_value(selected_incident.technology)),
            "resource_count": len(selected_incident.affected_resources),
        }
        base: dict[str, object] = {
            "workflow_id": "local-governance-demo-v1",
            "action_mode": action_mode,
            "candidate_count": len(triggers),
            "selected_candidate": candidate,
            "state": "PREVIEW",
            "closed_loop": False,
            "approval": {"incident_confirmed": False, "action_approved": False},
        }
        if not confirm_incident and not approve_action:
            return base

        digest = hashlib.sha256(selected.incident_id.encode("utf-8")).hexdigest()[:16]
        if confirm_incident:
            incident = await profile.detector.confirm(
                selected.incident_id,
                trace_id=f"local-stack-confirm-trace-{digest}",
                idempotency_key=f"local-stack-confirm-key-{digest}",
                actor="local-stack-operator",
                reason="explicit local demo incident confirmation",
            )
        else:
            incident = await profile.incident_repository.get(selected.incident_id)
            if incident is None:
                raise SafeCliError("approval_requires_incident")
        base["approval"] = {"incident_confirmed": True, "action_approved": False}

        try:
            from telco_local.governance import LocalGovernanceEngine
        except Exception:
            raise SafeCliError("governance_unavailable") from None
        engine = LocalGovernanceEngine(
            profile.incident_repository,
            profile.rca_gateway,
            clock=lambda: datetime.now(UTC),
        )
        prepared = await engine.prepare(
            incident.incident_id,
            idempotency_key=f"local-stack-prepare-key-{digest}",
            actor="local-governance",
        )
        base["state"] = _incident_state(prepared)
        action = _model_value(prepared, "action")
        awaiting_approval = bool(_model_value(prepared, "awaiting_approval", False))
        if action is not None:
            base["action_preview"] = _safe_action_preview(action)
            if awaiting_approval:
                base["action_preview"]["expected_revision"] = int(
                    _model_value(_model_value(prepared, "incident"), "revision")
                )
        if action is None:
            base["outcome"] = "NO_ACTION_PROPOSED"
            return base
        if not approve_action:
            if not awaiting_approval:
                base["outcome"] = "GOVERNANCE_RESUME_REQUIRES_ORIGINAL_BINDING"
                return base
            base["outcome"] = "AWAITING_EXPLICIT_APPROVAL"
            return base
        if action_mode == "disabled":
            raise SafeCliError("actions_disabled")
        normalized_reason = " ".join((reason or "").split())
        if not normalized_reason:
            raise SafeCliError("approval_reason_required")
        current_action_hash = str(_model_value(action, "action_hash", ""))
        current_revision = int(
            _model_value(_model_value(prepared, "incident"), "revision")
        )
        if expected_action_hash != current_action_hash or (
            awaiting_approval and expected_revision != current_revision
        ):
            raise SafeCliError("approval_binding_mismatch")

        decided = await engine.decide(
            incident.incident_id,
            approve=True,
            actor="local-stack-operator",
            reason=normalized_reason,
            idempotency_key=f"local-stack-decision-key-{digest}",
            expected_action_hash=expected_action_hash,
            expected_revision=expected_revision,
        )
        decided_state = _incident_state(decided)
        if decided_state == "REJECTED":
            base.update(
                {
                    "state": decided_state,
                    "closed_loop": False,
                    "approval": {
                        "incident_confirmed": True,
                        "action_approved": False,
                        "decision_state": decided_state,
                    },
                    "outcome": "APPROVAL_NOT_EFFECTIVE",
                }
            )
            return base
        executed = await engine.execute(
            incident.incident_id,
            idempotency_key=f"local-stack-execute-key-{digest}",
            actor="local-simulator",
            verification_passed=verification_outcome == "passed",
        )
        executed_state = _incident_state(executed)
        if executed_state == "FAILED":
            base.update(
                {
                    "state": executed_state,
                    "closed_loop": False,
                    "approval": {
                        "incident_confirmed": True,
                        "action_approved": False,
                        "decision_state": decided_state,
                    },
                    "outcome": "APPROVAL_NOT_EFFECTIVE",
                }
            )
            return base
        base.update(
            {
                "state": executed_state,
                "closed_loop": executed_state == "RESOLVED",
                "approval": {
                    "incident_confirmed": True,
                    "action_approved": True,
                    "decision_state": decided_state,
                },
                "outcome": (
                    "SIMULATED_AND_VERIFIED"
                    if verification_outcome == "passed"
                    else "SIMULATED_AND_REOPENED"
                ),
            }
        )
        return base

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
        if approve_action and confirm_incident:
            raise SafeCliError("approval_requires_prior_preview")
        if approve_action and action_mode != "simulate":
            raise SafeCliError("actions_disabled")
        if approve_action and (not expected_action_hash or expected_revision is None):
            raise SafeCliError("approval_binding_required")
        if not approve_action and (
            expected_action_hash is not None or expected_revision is not None
        ):
            raise SafeCliError("invalid_arguments")
        try:
            result = asyncio.run(
                self._run_demo(
                    action_mode=action_mode,
                    confirm_incident=confirm_incident,
                    approve_action=approve_action,
                    reason=reason,
                    expected_action_hash=expected_action_hash,
                    expected_revision=expected_revision,
                    verification_outcome=verification_outcome,
                )
            )
            _assert_project_safe(result)
            artifact = self.workspace.write_artifact("demo-result.json", result)
            result["artifacts"] = [artifact]
            return result
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None

    def backup(self, *, destination: Path) -> dict[str, object]:
        """Create one offline DuckDB COPY backup and publish it atomically."""

        self.workspace.marker()
        destination = _validate_backup_directory_path(
            destination, workspace=self.workspace, must_exist=False
        )
        staging = destination.parent / (
            f".networkagent-backup-{uuid.uuid4().hex}.partial"
        )
        try:
            import duckdb
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        with _maintenance_lock(self.workspace):
            # Recheck the externally selected path after acquiring the only
            # cooperative maintenance writer lock.
            destination = _validate_backup_directory_path(
                destination, workspace=self.workspace, must_exist=False
            )
            parent_identity = _directory_identity(
                destination.parent, invalid_code="unsafe_workspace"
            )
            database_path = self.workspace.database_path
            if not database_path.exists() and not _is_link_like(database_path):
                raise SafeCliError("workspace_not_initialized", exit_code=1)
            source_metadata = _safe_file_stat(
                database_path, invalid_code="unsafe_workspace"
            )
            _validate_database_sidecars(
                database_path,
                invalid_code="unsafe_workspace",
                allow_wal=True,
            )
            if source_metadata.st_size > _BACKUP_MAX_DATABASE_BYTES:
                raise SafeCliError("backup_too_large")
            try:
                free_bytes = shutil.disk_usage(destination.parent).free
            except OSError:
                raise SafeCliError("backup_failed") from None
            if free_bytes < _BACKUP_MAX_DATABASE_BYTES + _BACKUP_MAX_MANIFEST_BYTES:
                raise SafeCliError("backup_too_large")

            staging_identity: DirectoryIdentity | None = None
            staging_children: dict[str, FileIdentity] = {}
            staging_created = False
            try:
                staging.mkdir(mode=0o700)
                staging_created = True
                staging_metadata = staging.stat(follow_symlinks=False)
                if not stat.S_ISDIR(staging_metadata.st_mode):
                    raise SafeCliError("backup_failed")
                staging_identity = (staging_metadata.st_dev, staging_metadata.st_ino)
                _strict_directory_identity(
                    staging_identity, invalid_code="backup_failed"
                )
            except BaseException as error:
                if staging_created:
                    try:
                        if staging_identity is not None:
                            _cleanup_partial_backup(
                                staging,
                                expected_identity=staging_identity,
                                expected_children=staging_children,
                            )
                    except (OSError, SafeCliError):
                        raise SafeCliError("backup_failed") from None
                if isinstance(error, SafeCliError):
                    raise
                raise SafeCliError("backup_failed") from None

            connection = None
            published = False
            try:
                if os.name != "nt":
                    os.chmod(staging, 0o700, follow_symlinks=False)
                if _is_link_like(staging):
                    raise SafeCliError("backup_failed")
                _require_directory_identity(
                    destination.parent,
                    parent_identity,
                    invalid_code="unsafe_workspace",
                )
                connection = _duckdb_connect(
                    duckdb,
                    database_path,
                    read_only=False,
                    allow_attach=True,
                )
                connection.execute("CHECKPOINT")
                source_database = connection.execute(
                    "SELECT current_database()"
                ).fetchone()
                if (
                    source_database is None
                    or not isinstance(source_database[0], str)
                    or not source_database[0]
                ):
                    raise SafeCliError("backup_failed")
                copied_database = staging / BACKUP_DATABASE_NAME
                connection.execute(
                    "ATTACH "
                    f"{_quote_sql_string(str(copied_database))} "
                    "AS networkagent_backup"
                )
                connection.execute("SET enable_external_access = false")
                source = _database_metadata(connection)
                connection.execute(
                    "COPY FROM DATABASE "
                    f"{_quote_sql_identifier(source_database[0])} "
                    "TO networkagent_backup"
                )
                copied_wal = Path(f"{copied_database}.wal")
                connection.execute("CHECKPOINT networkagent_backup")
                connection.execute("DETACH networkagent_backup")
                connection.close()
                connection = None
                _validate_database_sidecars(
                    database_path,
                    invalid_code="workspace_busy",
                    allow_wal=False,
                )
                if os.name != "nt":
                    os.chmod(copied_database, 0o600, follow_symlinks=False)
                _capture_partial_child(
                    copied_database,
                    staging_children,
                    invalid_code="backup_failed",
                )
                if copied_wal.exists() or _is_link_like(copied_wal):
                    _capture_partial_child(
                        copied_wal,
                        staging_children,
                        invalid_code="backup_failed",
                    )

                _validate_database_sidecars(
                    copied_database,
                    invalid_code="backup_failed",
                    allow_wal=False,
                )
                copied_connection = _duckdb_connect(
                    duckdb, copied_database, read_only=True
                )
                try:
                    copied = _database_metadata(copied_connection)
                finally:
                    copied_connection.close()
                if copied != source:
                    raise SafeCliError("backup_failed")

                database_bytes, database_sha256 = _bounded_file_sha256(
                    copied_database,
                    maximum_bytes=_BACKUP_MAX_DATABASE_BYTES,
                    invalid_code="backup_failed",
                    too_large_code="backup_too_large",
                )
                _, marker_sha256 = _bounded_file_sha256(
                    self.workspace.marker_path,
                    maximum_bytes=_BACKUP_MAX_MANIFEST_BYTES,
                    invalid_code="unsafe_workspace",
                    too_large_code="unsafe_workspace",
                )
                manifest = {
                    "schema": BACKUP_SCHEMA_VERSION,
                    "backup_id": str(uuid.uuid4()),
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source": {"workspace_marker_sha256": marker_sha256},
                    "schemas": source["schemas"],
                    "duckdb": source["duckdb"],
                    "database": {
                        "filename": BACKUP_DATABASE_NAME,
                        "bytes": database_bytes,
                        "sha256": database_sha256,
                        "checkpointed": True,
                    },
                    "catalog": {
                        **source["catalog"],
                        "tables": source["tables"],
                        "row_count": source["row_count"],
                        "logical_sha256": source["logical_sha256"],
                        "logical_equivalence": True,
                    },
                }
                manifest_payload = _canonical_json_bytes(manifest)
                if len(manifest_payload) > _BACKUP_MAX_MANIFEST_BYTES:
                    raise SafeCliError("backup_too_large")
                _require_directory_identity(
                    destination.parent,
                    parent_identity,
                    invalid_code="unsafe_workspace",
                )
                _require_directory_identity(
                    staging,
                    staging_identity,
                    invalid_code="backup_failed",
                )
                staging_children[BACKUP_MANIFEST_NAME] = _write_new_file(
                    staging / BACKUP_MANIFEST_NAME,
                    manifest_payload,
                    failure_code="backup_failed",
                )
                _require_file_identity(
                    copied_database,
                    staging_children[BACKUP_DATABASE_NAME],
                    invalid_code="backup_failed",
                )
                _fsync_directory(staging)
                try:
                    checked, manifest_bytes, manifest_sha256 = _load_and_verify_backup(
                        staging
                    )
                except SafeCliError as error:
                    if error.code == "backup_too_large":
                        raise
                    raise SafeCliError("backup_failed") from None

                _validate_backup_directory_path(
                    destination, workspace=self.workspace, must_exist=False
                )
                _require_directory_identity(
                    destination.parent,
                    parent_identity,
                    invalid_code="unsafe_workspace",
                )
                _require_directory_identity(
                    staging,
                    staging_identity,
                    invalid_code="backup_failed",
                )
                for name in (BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME):
                    _require_file_identity(
                        staging / name,
                        staging_children[name],
                        invalid_code="backup_failed",
                    )
                _publish_directory_no_replace(staging, destination)
                published = True
                _require_directory_identity(
                    destination.parent,
                    parent_identity,
                    invalid_code="unsafe_workspace",
                )
                _require_directory_identity(
                    destination,
                    staging_identity,
                    invalid_code="backup_failed",
                )
                for name in (BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME):
                    _require_file_identity(
                        destination / name,
                        staging_children[name],
                        invalid_code="backup_failed",
                    )
                _fsync_directory(destination.parent)
                local_ownership_sha256 = _local_backup_ownership_sha256(
                    destination,
                    expected_directory=staging_identity,
                    expected_children={
                        name: staging_children[name]
                        for name in (BACKUP_DATABASE_NAME, BACKUP_MANIFEST_NAME)
                    },
                )
                return _backup_public_summary(
                    checked,
                    manifest_bytes=manifest_bytes,
                    manifest_sha256=manifest_sha256,
                    local_ownership_sha256=local_ownership_sha256,
                    changed=True,
                )
            except SafeCliError:
                raise
            except BaseException as error:
                if _looks_like_lock_error(error):
                    raise SafeCliError("workspace_busy") from None
                raise SafeCliError("backup_failed") from None
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                if not published and staging_identity is not None:
                    _cleanup_partial_backup(
                        staging,
                        expected_identity=staging_identity,
                        expected_children=staging_children,
                    )

    def restore(
        self,
        *,
        source: Path,
        expected_manifest_sha256: str,
    ) -> dict[str, object]:
        """Verify and atomically restore one exact cold-backup database."""

        if not _is_sha256(expected_manifest_sha256):
            raise SafeCliError("invalid_arguments")
        self.workspace.marker()
        source = _validate_backup_directory_path(
            source, workspace=self.workspace, must_exist=True
        )
        source_identity = _directory_identity(source, invalid_code="backup_invalid")
        manifest, _, manifest_sha256 = _load_and_verify_backup(source)
        _require_directory_identity(
            source, source_identity, invalid_code="backup_invalid"
        )
        if manifest_sha256 != expected_manifest_sha256:
            raise SafeCliError("manifest_mismatch")
        database = manifest["database"]
        assert isinstance(database, Mapping)
        expected_bytes = int(database["bytes"])
        expected_database_sha256 = str(database["sha256"])
        source_database = source / BACKUP_DATABASE_NAME

        try:
            import duckdb
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        with _maintenance_lock(self.workspace):
            # A second full validation closes the verify/use gap for ordinary
            # local races before any workspace state can be replaced.
            repeated_manifest, _, repeated_sha256 = _load_and_verify_backup(source)
            _require_directory_identity(
                source, source_identity, invalid_code="backup_invalid"
            )
            if (
                repeated_sha256 != expected_manifest_sha256
                or repeated_manifest != manifest
            ):
                raise SafeCliError("backup_invalid")
            state_dir = self.workspace.state_dir
            self.workspace._validate_owned_directory(state_dir)
            state_identity = _directory_identity(
                state_dir, invalid_code="unsafe_workspace"
            )
            database_path = self.workspace.database_path
            restore_temp = state_dir / _RESTORE_TEMP_NAME

            current_exists = database_path.exists() or _is_link_like(database_path)
            current_database_identity: FileIdentity | None = None
            checkpoint_succeeded = False
            if current_exists:
                _safe_file_stat(database_path, invalid_code="unsafe_workspace")
                _validate_database_sidecars(
                    database_path,
                    invalid_code="unsafe_workspace",
                    allow_wal=True,
                )
                current_connection = None
                try:
                    current_connection = _duckdb_connect(
                        duckdb, database_path, read_only=False
                    )
                    current_connection.execute("CHECKPOINT")
                    checkpoint_succeeded = True
                except Exception as error:
                    if _looks_like_lock_error(error):
                        raise SafeCliError("workspace_busy") from None
                    # A corrupt current database may be replaced by an already
                    # verified backup, but a WAL that cannot be checkpointed
                    # must never be discarded.
                    wal_path = Path(f"{database_path}.wal")
                    if wal_path.exists() or _is_link_like(wal_path):
                        raise SafeCliError("restore_failed") from None
                finally:
                    if current_connection is not None:
                        try:
                            current_connection.close()
                        except Exception:
                            pass
                wal_path = Path(f"{database_path}.wal")
                if checkpoint_succeeded and (
                    wal_path.exists() or _is_link_like(wal_path)
                ):
                    raise SafeCliError("workspace_busy")

            if database_path.exists():
                current = _safe_file_stat(
                    database_path, invalid_code="unsafe_workspace"
                )
                current_database_identity = _file_identity(
                    current, invalid_code="unsafe_workspace"
                )
                if current.st_size == expected_bytes:
                    _, current_sha256 = _bounded_file_sha256(
                        database_path,
                        maximum_bytes=_BACKUP_MAX_DATABASE_BYTES,
                        invalid_code="restore_failed",
                        too_large_code="backup_too_large",
                    )
                    if current_sha256 == expected_database_sha256:
                        return _restore_public_summary(
                            manifest,
                            manifest_sha256=manifest_sha256,
                            changed=False,
                        )

            try:
                free_bytes = shutil.disk_usage(state_dir).free
            except OSError:
                raise SafeCliError("restore_failed") from None
            if free_bytes < expected_bytes + 1024 * 1024:
                raise SafeCliError("restore_failed")
            restore_temp_identity: FileIdentity | None = None
            try:
                _require_directory_identity(
                    source, source_identity, invalid_code="backup_invalid"
                )
                _exact_backup_entries(source, invalid_code="backup_invalid")
                _, final_manifest_sha256 = _bounded_file_bytes(
                    source / BACKUP_MANIFEST_NAME,
                    maximum_bytes=_BACKUP_MAX_MANIFEST_BYTES,
                    invalid_code="backup_invalid",
                    too_large_code="backup_too_large",
                )
                if final_manifest_sha256 != expected_manifest_sha256:
                    raise SafeCliError("backup_invalid")
                _require_directory_identity(
                    state_dir, state_identity, invalid_code="unsafe_workspace"
                )
                restore_temp_identity = _copy_database_to_restore_temp(
                    source_database,
                    restore_temp,
                    expected_bytes=expected_bytes,
                    expected_sha256=expected_database_sha256,
                )
                _validate_database_sidecars(
                    restore_temp,
                    invalid_code="restore_failed",
                    allow_wal=False,
                )
                temporary_connection = _duckdb_connect(
                    duckdb, restore_temp, read_only=True
                )
                try:
                    temporary = _database_metadata(temporary_connection)
                finally:
                    temporary_connection.close()
                catalog = manifest["catalog"]
                assert isinstance(catalog, Mapping)
                if (
                    temporary["catalog"]
                    != {
                        "schema_count": catalog["schema_count"],
                        "table_count": catalog["table_count"],
                        "view_count": catalog["view_count"],
                    }
                    or temporary["tables"] != catalog["tables"]
                    or temporary["row_count"] != catalog["row_count"]
                    or temporary["schemas"] != manifest["schemas"]
                    or temporary["duckdb"] != manifest["duckdb"]
                    or temporary["logical_sha256"] != catalog["logical_sha256"]
                ):
                    raise SafeCliError("backup_invalid")
                _require_directory_identity(
                    source, source_identity, invalid_code="backup_invalid"
                )
                _exact_backup_entries(source, invalid_code="backup_invalid")
                _, final_manifest_sha256 = _bounded_file_bytes(
                    source / BACKUP_MANIFEST_NAME,
                    maximum_bytes=_BACKUP_MAX_MANIFEST_BYTES,
                    invalid_code="backup_invalid",
                    too_large_code="backup_too_large",
                )
                if final_manifest_sha256 != expected_manifest_sha256:
                    raise SafeCliError("backup_invalid")
                final_temp_bytes, final_temp_sha256 = _bounded_file_sha256(
                    restore_temp,
                    maximum_bytes=_BACKUP_MAX_DATABASE_BYTES,
                    invalid_code="restore_failed",
                    too_large_code="backup_too_large",
                )
                if (
                    final_temp_bytes != expected_bytes
                    or final_temp_sha256 != expected_database_sha256
                ):
                    raise SafeCliError("restore_failed")
                _require_directory_identity(
                    state_dir, state_identity, invalid_code="unsafe_workspace"
                )
                if database_path.exists() or _is_link_like(database_path):
                    if current_database_identity is None:
                        raise SafeCliError("unsafe_workspace")
                    _require_file_identity(
                        database_path,
                        current_database_identity,
                        invalid_code="unsafe_workspace",
                    )
                elif current_database_identity is not None:
                    raise SafeCliError("unsafe_workspace")
                if restore_temp_identity is None:
                    raise SafeCliError("restore_failed")
                _require_file_identity(
                    restore_temp,
                    restore_temp_identity,
                    invalid_code="restore_failed",
                )
                try:
                    os.replace(restore_temp, database_path)
                except OSError as error:
                    if _looks_like_lock_error(error):
                        raise SafeCliError("workspace_busy") from None
                    raise SafeCliError("restore_failed") from None
                _fsync_directory(state_dir)
            finally:
                if restore_temp.exists() or _is_link_like(restore_temp):
                    if restore_temp_identity is None:
                        _safe_file_stat(restore_temp, invalid_code="unsafe_workspace")
                        raise SafeCliError("restore_failed")
                    _unlink_file_identity(
                        restore_temp,
                        restore_temp_identity,
                        invalid_code="unsafe_workspace",
                        failure_code="restore_failed",
                    )

            restored_bytes, restored_sha256 = _bounded_file_sha256(
                database_path,
                maximum_bytes=_BACKUP_MAX_DATABASE_BYTES,
                invalid_code="restore_failed",
                too_large_code="backup_too_large",
            )
            if (
                restored_bytes != expected_bytes
                or restored_sha256 != expected_database_sha256
            ):
                raise SafeCliError("restore_failed")
            return _restore_public_summary(
                manifest,
                manifest_sha256=manifest_sha256,
                changed=True,
            )

    def serve(self, *, port: int) -> None:
        if not _port_available(port):
            raise SafeCliError("port_unavailable")
        try:
            import uvicorn
            from telco_assurance_agent.app import create_app
            from telco_assurance_agent.transport_http import BoundedH11Protocol
        except Exception:
            raise SafeCliError("server_dependencies_missing") from None
        status = self.status(port=port)
        if not status["database"]["initialized"]:
            raise SafeCliError("workspace_not_initialized", exit_code=1)
        try:
            application = create_app(self._assurance_config(port))
            uvicorn.run(
                application,
                host=DEFAULT_HOST,
                port=port,
                workers=1,
                reload=False,
                interface="asgi3",
                lifespan="on",
                http=BoundedH11Protocol,
                ws="none",
                proxy_headers=False,
                forwarded_allow_ips="",
                access_log=False,
                server_header=False,
                date_header=False,
                limit_concurrency=None,
                backlog=16,
                timeout_keep_alive=5,
                timeout_graceful_shutdown=10,
                h11_max_incomplete_event_size=16_384,
            )
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise SafeCliError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-stack",
        description="Safe local NetworkAgent deployment and governance demo",
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--action-mode", choices=("disabled", "simulate"), default="disabled"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check the local runtime without writes")
    commands.add_parser("init", help="initialize an explicitly selected workspace")
    commands.add_parser("status", help="inspect workspace and runtime readiness")
    commands.add_parser(
        "demo-seed",
        help="seed one deterministic DETECTED incident for the container demo",
    )
    demo_verify = commands.add_parser(
        "demo-verify",
        help="verify the stopped container demo state without writes",
    )
    demo_verify.add_argument(
        "--expected-status",
        choices=("RESOLVED", "REOPENED"),
        required=True,
    )
    demo_events = commands.add_parser(
        "demo-events",
        help="project the completed demo from durable lifecycle records",
    )
    demo_events.add_argument(
        "--expected-status",
        choices=("RESOLVED", "REOPENED"),
        required=True,
    )
    demo = commands.add_parser("demo", help="run a deterministic governance demo")
    demo.add_argument("--confirm-incident", action="store_true")
    demo.add_argument("--approve-action", action="store_true")
    demo.add_argument("--reason")
    demo.add_argument("--expected-action-hash")
    demo.add_argument("--expected-revision", type=int)
    demo.add_argument(
        "--verification-outcome", choices=("passed", "failed"), default="passed"
    )
    commands.add_parser(
        "serve", help="run the optional loopback A2A service in foreground"
    )
    backup = commands.add_parser(
        "backup", help="create one verified cold backup in a new directory"
    )
    backup.add_argument("--destination", type=Path, required=True)
    restore = commands.add_parser(
        "restore", help="verify and atomically restore one cold backup"
    )
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--expected-manifest-sha256", required=True)
    restore.add_argument("--yes", action="store_true")
    reset = commands.add_parser("reset", help="reset only marker-owned local state")
    reset.add_argument("--yes", action="store_true")
    return parser


def _workspace_payload(workspace_id: str, *, initialized: bool) -> dict[str, object]:
    return {"workspace_id": workspace_id, "initialized": initialized}


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    runtime_factory: Callable[[Workspace], Any] = LocalStackRuntime,
) -> int:
    """Run one command; stdout/stderr are always single JSON documents."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if not 1 <= arguments.port <= 65_535:
            raise SafeCliError("invalid_arguments")
        workspace = Workspace(arguments.workspace)
        runtime = runtime_factory(workspace)

        if arguments.command == "doctor":
            report = runtime.doctor(port=arguments.port)
            _write_json(
                output,
                {"ok": bool(report["ready"]), "command": "doctor", "report": report},
            )
            return 0 if report["ready"] else 1
        if arguments.command == "init":
            workspace_id, created, root_created = workspace.prepare_init()
            try:
                result = runtime.initialize()
                workspace.commit_marker(workspace_id)
            except BaseException:
                if created:
                    workspace.rollback_uncommitted_init(root_created=root_created)
                raise
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "init",
                    "workspace": _workspace_payload(workspace_id, initialized=True),
                    "created": created,
                    "database": result,
                    "network": {"external_access": False},
                    "action_mode": arguments.action_mode,
                },
            )
            return 0
        if arguments.command == "status":
            marker = workspace.marker()
            report = runtime.status(port=arguments.port)
            payload = {
                "ok": bool(report["ready"]),
                "command": "status",
                "workspace": _workspace_payload(
                    str(marker["workspace_id"]), initialized=True
                ),
                "report": report,
                "action_mode": arguments.action_mode,
            }
            _write_json(output if report["ready"] else errors, payload)
            return 0 if report["ready"] else 1
        if arguments.command == "demo-seed":
            marker = workspace.marker()
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            result = runtime.seed_container_demo()
            _assert_project_safe(result)
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "demo-seed",
                    "workspace": _workspace_payload(
                        str(marker["workspace_id"]), initialized=True
                    ),
                    "action_mode": "disabled",
                    "result": result,
                },
            )
            return 0
        if arguments.command == "demo-verify":
            marker = workspace.marker()
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            result = runtime.verify_container_demo(
                expected_status=arguments.expected_status
            )
            _assert_project_safe(result)
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "demo-verify",
                    "workspace": _workspace_payload(
                        str(marker["workspace_id"]), initialized=True
                    ),
                    "action_mode": "disabled",
                    "result": result,
                },
            )
            return 0
        if arguments.command == "demo-events":
            marker = workspace.marker()
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            result = runtime.lifecycle_events(expected_status=arguments.expected_status)
            _assert_project_safe(result)
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "demo-events",
                    "workspace": _workspace_payload(
                        str(marker["workspace_id"]), initialized=True
                    ),
                    "action_mode": "disabled",
                    "result": result,
                },
            )
            return 0
        if arguments.command == "demo":
            marker = workspace.marker()
            if arguments.approve_action and arguments.confirm_incident:
                raise SafeCliError("approval_requires_prior_preview")
            if arguments.approve_action and arguments.action_mode != "simulate":
                raise SafeCliError("actions_disabled")
            if arguments.approve_action and (
                not arguments.expected_action_hash
                or arguments.expected_revision is None
            ):
                raise SafeCliError("approval_binding_required")
            if arguments.verification_outcome != "passed" and (
                arguments.action_mode != "simulate" or not arguments.approve_action
            ):
                raise SafeCliError("actions_disabled")
            result = runtime.demo(
                action_mode=arguments.action_mode,
                confirm_incident=arguments.confirm_incident,
                approve_action=arguments.approve_action,
                reason=arguments.reason,
                expected_action_hash=arguments.expected_action_hash,
                expected_revision=arguments.expected_revision,
                verification_outcome=arguments.verification_outcome,
            )
            _assert_project_safe(result)
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "demo",
                    "workspace": _workspace_payload(
                        str(marker["workspace_id"]), initialized=True
                    ),
                    "result": result,
                },
            )
            return 0
        if arguments.command == "serve":
            workspace.marker()
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            # This is intentionally foreground-only; no PID files or orphaned
            # background processes are created by local-stack.
            runtime.serve(port=arguments.port)
            return 0
        if arguments.command == "backup":
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            result = runtime.backup(destination=arguments.destination)
            _assert_project_safe(result)
            _write_json(
                output,
                {"ok": True, "command": "backup", "result": result},
            )
            return 0
        if arguments.command == "restore":
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            if not arguments.yes:
                raise SafeCliError("restore_confirmation_required", exit_code=1)
            if not _is_sha256(arguments.expected_manifest_sha256):
                raise SafeCliError("invalid_arguments")
            result = runtime.restore(
                source=arguments.source,
                expected_manifest_sha256=arguments.expected_manifest_sha256,
            )
            _assert_project_safe(result)
            _write_json(
                output,
                {"ok": True, "command": "restore", "result": result},
            )
            return 0
        if arguments.command == "reset":
            marker = workspace.marker()
            if not arguments.yes:
                _write_json(
                    output,
                    {
                        "ok": False,
                        "command": "reset",
                        "confirmation_required": True,
                        "workspace": _workspace_payload(
                            str(marker["workspace_id"]), initialized=True
                        ),
                    },
                )
                return 1
            result = workspace.reset()
            _write_json(output, {"ok": True, "command": "reset", **result})
            return 0
        raise SafeCliError("invalid_arguments")
    except SafeCliError as exc:
        _write_json(
            errors,
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": _ERROR_MESSAGES.get(exc.code, "request rejected"),
                },
            },
        )
        return exc.exit_code
    except Exception:
        _write_json(
            errors,
            {
                "ok": False,
                "error": {
                    "code": "runtime_failed",
                    "message": _ERROR_MESSAGES["runtime_failed"],
                },
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
