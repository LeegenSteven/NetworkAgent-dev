#!/usr/bin/env python3
"""Closed-command container entry point for the isolated Local profile.

The release image deliberately has no general command pass-through.  Every
supported operation is selected from a fixed command table, and every
data-consuming operation verifies the image-owned input manifest first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import BinaryIO, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


INPUT_MANIFEST = Path("/opt/networkagent/share/input-manifest.json")
LOCAL_STACK = Path("/opt/networkagent/tools/local-stack/local_stack.py")
WORKSPACE = Path("/var/lib/networkagent/workspace")
LOOPBACK_ORIGIN = "http://127.0.0.1:8085"
MAX_MANIFEST_BYTES = 65_536
MAX_MANIFEST_FILES = 64
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_DIRECTORY_ROOTS = 16
MAX_HTTP_RESPONSE_BYTES = 65_536
HTTP_TIMEOUT_SECONDS = 2.0
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_RUNTIME_INPUTS = {
    "/opt/networkagent/data/samples/lte-demo/performance.csv",
    "/opt/networkagent/data/samples/lte-demo/safe-cell-traces.csv",
    "/opt/networkagent/data/rca-rules/lte/5g-sa-bubbleran-persistent-interference-ul-bler.json",
    "/opt/networkagent/data/rca-rules/lte/erab-security-setup.json",
    "/opt/networkagent/data/rca-rules/lte/retainability-uplink-rssi.json",
    "/opt/networkagent/data/docs/lte/telco-lte-fields-guide.zh-CN.md",
}
EXPECTED_RUNTIME_DIRECTORY_ROOTS = {
    "/opt/networkagent/data/rca-rules/lte",
    "/opt/networkagent/data/docs/lte",
}


class InputValidationError(RuntimeError):
    """The mounted input set does not match the image-owned manifest."""


class HealthProbeError(RuntimeError):
    """The loopback-only service did not return its bounded status contract."""


def _bounded_read(handle: BinaryIO, maximum: int) -> bytes:
    value = handle.read(maximum + 1)
    if len(value) > maximum:
        raise InputValidationError("manifest exceeds byte limit")
    return value


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise InputValidationError("manifest is not a regular file")
        with path.open("rb") as handle:
            raw = _bounded_read(handle, MAX_MANIFEST_BYTES)
        value = json.loads(raw.decode("utf-8"))
    except InputValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise InputValidationError("manifest is unreadable") from None
    if not isinstance(value, dict):
        raise InputValidationError("manifest must be an object")
    if value.get("schema_version") != "1.0" or value.get("algorithm") != "sha256":
        raise InputValidationError("manifest schema is unsupported")
    return value


def _safe_manifest_limits(manifest: dict[str, object]) -> tuple[int, int]:
    max_files = manifest.get("max_files")
    max_total_bytes = manifest.get("max_total_bytes")
    if (
        not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or not 1 <= max_files <= MAX_MANIFEST_FILES
    ):
        raise InputValidationError("manifest file count limit is invalid")
    if (
        not isinstance(max_total_bytes, int)
        or isinstance(max_total_bytes, bool)
        or not 1 <= max_total_bytes <= MAX_INPUT_BYTES
    ):
        raise InputValidationError("manifest byte limit is invalid")
    return max_files, max_total_bytes


def _manifest_entries(
    manifest: dict[str, object], *, max_files: int, max_total_bytes: int
) -> list[dict[str, object]]:
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= max_files:
        raise InputValidationError("manifest file count exceeds limit")
    entries: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    seen_sources: set[str] = set()
    declared_bytes = 0
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise InputValidationError("manifest file entry is invalid")
        source = raw.get("source")
        rendered = raw.get("container_path")
        expected_bytes = raw.get("bytes")
        expected_hash = raw.get("sha256")
        if (
            not isinstance(source, str)
            or not source
            or Path(source).is_absolute()
            or ".." in Path(source).parts
        ):
            raise InputValidationError("manifest source path is invalid")
        if not isinstance(rendered, str) or not Path(rendered).is_absolute():
            raise InputValidationError("manifest container path is invalid")
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes < 0
            or expected_bytes > max_total_bytes
        ):
            raise InputValidationError("manifest file size is invalid")
        if (
            not isinstance(expected_hash, str)
            or _SHA256.fullmatch(expected_hash) is None
        ):
            raise InputValidationError("manifest sha256 is invalid")
        normalized_path = os.path.normpath(rendered)
        if normalized_path in seen_paths or source in seen_sources:
            raise InputValidationError("manifest contains duplicate files")
        seen_paths.add(normalized_path)
        seen_sources.add(source)
        declared_bytes += expected_bytes
        if declared_bytes > max_total_bytes:
            raise InputValidationError("manifest declared bytes exceed limit")
        entries.append(raw)
    return entries


def _open_regular_file(path: Path) -> tuple[int, os.stat_result]:
    try:
        metadata = path.lstat()
    except OSError:
        raise InputValidationError("input is not a regular file") from None
    if not stat.S_ISREG(metadata.st_mode):
        raise InputValidationError("input is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
    except OSError:
        raise InputValidationError("input is not a regular file") from None
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise InputValidationError("input is not a regular file")
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(descriptor)
        raise InputValidationError("input changed during validation")
    return descriptor, opened


def _hash_exact_file(path: Path, expected_bytes: int) -> str:
    descriptor, before = _open_regular_file(path)
    if before.st_size != expected_bytes:
        os.close(descriptor)
        raise InputValidationError("input size mismatch")
    digest = hashlib.sha256()
    consumed = 0
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            while True:
                chunk = handle.read(min(65_536, expected_bytes - consumed + 1))
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > expected_bytes:
                    raise InputValidationError("input size mismatch")
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except InputValidationError:
        raise
    except OSError:
        raise InputValidationError("input read failed") from None
    if consumed != expected_bytes:
        raise InputValidationError("input size mismatch")
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ) or getattr(before, "st_mtime_ns", None) != getattr(after, "st_mtime_ns", None):
        raise InputValidationError("input changed during validation")
    return digest.hexdigest()


def _directory_files(root: Path, *, maximum_files: int) -> set[str]:
    try:
        root_meta = root.lstat()
    except OSError:
        raise InputValidationError("controlled directory is unavailable") from None
    if not stat.S_ISDIR(root_meta.st_mode):
        raise InputValidationError("controlled directory is not a regular directory")
    found: set[str] = set()
    try:
        with os.scandir(root) as children:
            for child in children:
                if len(found) >= maximum_files:
                    raise InputValidationError(
                        "controlled directory file count exceeds limit"
                    )
                try:
                    if child.is_symlink():
                        raise InputValidationError(
                            "controlled directory contains a link"
                        )
                    if child.is_dir(follow_symlinks=False):
                        raise InputValidationError(
                            "controlled directory contains an unexpected directory"
                        )
                    if not child.is_file(follow_symlinks=False):
                        raise InputValidationError(
                            "controlled directory contains a special file"
                        )
                except OSError:
                    raise InputValidationError(
                        "controlled directory is unreadable"
                    ) from None
                found.add(os.path.normpath(child.path))
    except InputValidationError:
        raise
    except OSError:
        raise InputValidationError("controlled directory is unreadable") from None
    return found


def _verify_directory_sets(
    manifest: dict[str, object], expected_paths: set[str], *, max_files: int
) -> None:
    roots = manifest.get("directory_roots")
    if not isinstance(roots, list) or len(roots) > MAX_DIRECTORY_ROOTS:
        raise InputValidationError("manifest directory roots are invalid")
    seen_roots: set[str] = set()
    for value in roots:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise InputValidationError("manifest directory root is invalid")
        normalized_root = os.path.normpath(value)
        if normalized_root in seen_roots:
            raise InputValidationError("manifest directory root is duplicated")
        seen_roots.add(normalized_root)
        root = Path(normalized_root)
        actual = _directory_files(root, maximum_files=max_files)
        prefix = normalized_root + os.sep
        expected = {path for path in expected_paths if path.startswith(prefix)}
        if actual != expected:
            raise InputValidationError(
                "controlled directory contents do not match manifest"
            )


def validate_inputs(manifest_path: Path = INPUT_MANIFEST) -> dict[str, int]:
    """Verify the complete bounded mounted-input set before consuming it."""

    manifest = _load_manifest(manifest_path)
    max_files, max_total_bytes = _safe_manifest_limits(manifest)
    entries = _manifest_entries(
        manifest,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
    )
    if manifest_path == INPUT_MANIFEST:
        declared_paths = {
            os.path.normpath(str(entry["container_path"])) for entry in entries
        }
        expected_paths = {os.path.normpath(path) for path in EXPECTED_RUNTIME_INPUTS}
        roots = manifest.get("directory_roots")
        if (
            declared_paths != expected_paths
            or not isinstance(roots, list)
            or {os.path.normpath(str(root)) for root in roots}
            != {os.path.normpath(root) for root in EXPECTED_RUNTIME_DIRECTORY_ROOTS}
        ):
            raise InputValidationError("runtime input layout does not match policy")
    total = 0
    expected_paths: set[str] = set()
    for entry in entries:
        path = Path(str(entry["container_path"]))
        expected_bytes = int(entry["bytes"])
        actual_hash = _hash_exact_file(path, expected_bytes)
        if actual_hash != entry["sha256"]:
            raise InputValidationError("input sha256 mismatch")
        total += expected_bytes
        if total > max_total_bytes:
            raise InputValidationError("input bytes exceed limit")
        expected_paths.add(os.path.normpath(str(path)))
    _verify_directory_sets(manifest, expected_paths, max_files=max_files)
    return {"files": len(entries), "bytes": total}


def _get_loopback_json(path: str) -> dict[str, object]:
    request = Request(
        LOOPBACK_ORIGIN + path,
        method="GET",
        headers={"Accept": "application/json", "Connection": "close"},
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise HealthProbeError("loopback status was not successful")
            media_type = response.headers.get_content_type()
            if media_type != "application/json":
                raise HealthProbeError("loopback response was not JSON")
            raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    except HealthProbeError:
        raise
    except (HTTPError, URLError, OSError, TimeoutError):
        raise HealthProbeError("loopback endpoint is unavailable") from None
    if len(raw) > MAX_HTTP_RESPONSE_BYTES:
        raise HealthProbeError("loopback response exceeded byte limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise HealthProbeError("loopback response was invalid") from None
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise HealthProbeError("loopback response contract failed")
    return value


def _probe() -> None:
    payload = _get_loopback_json("/local/v1/healthz")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("status") != "alive":
        raise HealthProbeError("health response contract failed")


def _smoke() -> None:
    validate_inputs()
    contracts = (
        ("/local/v1/healthz", "alive"),
        ("/local/v1/readyz", "ready"),
    )
    for path, expected_status in contracts:
        payload = _get_loopback_json(path)
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("status") != expected_status:
            raise HealthProbeError("smoke response contract failed")
        if data.get("service") != "telco-assurance-agent":
            raise HealthProbeError("smoke service identity failed")
    version = _get_loopback_json("/local/v1/version").get("data")
    if (
        not isinstance(version, dict)
        or version.get("service") != "telco-assurance-agent"
    ):
        raise HealthProbeError("version response contract failed")
    print(json.dumps({"ok": True, "command": "smoke"}, separators=(",", ":")))


def _exec_local_stack(command: str) -> None:
    arguments = [
        sys.executable,
        str(LOCAL_STACK),
        "--workspace",
        str(WORKSPACE),
        "--action-mode",
        "disabled",
        "--port",
        "8085",
        command,
    ]
    if command == "reset":
        arguments.append("--yes")
    os.execv(sys.executable, arguments)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="networkagent-local-container")
    parser.add_argument("command", choices=("init", "serve", "reset", "probe", "smoke"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    command = _parser().parse_args(argv).command
    if command == "probe":
        _probe()
        return 0
    if command == "smoke":
        _smoke()
        return 0
    if command != "reset":
        validate_inputs()
    _exec_local_stack(command)
    return 0


def _run() -> int:
    try:
        return main()
    except (InputValidationError, HealthProbeError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "CONTAINER_BOUNDARY_REJECTED",
                        "message": str(exc),
                    },
                },
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_run())
