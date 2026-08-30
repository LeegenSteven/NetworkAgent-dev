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
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


INPUT_MANIFEST = Path("/opt/networkagent/share/input-manifest.json")
LOCAL_STACK = Path("/opt/networkagent/tools/local-stack/local_stack.py")
WORKSPACE = Path("/var/lib/networkagent/workspace")
LOOPBACK_ORIGIN = "http://127.0.0.1:8085"
MAX_MANIFEST_BYTES = 65_536
MAX_MANIFEST_FILES = 64
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_DIRECTORY_ROOTS = 16
MAX_HTTP_REQUEST_BYTES = 4_096
MAX_HTTP_RESPONSE_BYTES = 65_536
MAX_HTTP_JSON_DEPTH = 16
HTTP_TIMEOUT_SECONDS = 2.0
GOVERNANCE_HTTP_TIMEOUT_SECONDS = 7.0
GOVERNANCE_OPERATION_HEADER = "X-NetworkAgent-Local-Operation"
GOVERNANCE_OPERATION_VALUE = "governance-v1"
GOVERNANCE_ACTOR = "local-container-governance"
GOVERNANCE_REASON = "approve exact side-effect-free local simulation"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INCIDENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_REVISION = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")
MAX_GOVERNANCE_REVISION = 2_147_483_646
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


class GovernanceCommandError(RuntimeError):
    """A fixed loopback governance operation failed its local contract."""


class _RejectRedirects(HTTPRedirectHandler):
    """Keep fixed loopback operations from following any redirect."""

    def redirect_request(self, *_args, **_kwargs):
        return None


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


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _json_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, level = stack.pop()
        maximum = max(maximum, level)
        if maximum > MAX_HTTP_JSON_DEPTH:
            return maximum
        if isinstance(current, dict):
            stack.extend((nested, level + 1) for nested in current.values())
        elif isinstance(current, list):
            stack.extend((nested, level + 1) for nested in current)
    return maximum


def _stable_governance_key(operation: str, incident_id: str, *bindings: object) -> str:
    encoded = json.dumps(
        ["local-container-governance-v1", operation, incident_id, *bindings],
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"local-container-{operation}-v1-{digest}"


def _governance_request(
    operation: str,
    incident_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    if operation not in {
        "governance-prepare",
        "governance-decide",
        "governance-execute",
    }:
        raise GovernanceCommandError("governance operation is not allowed")
    route = operation.removeprefix("governance-")
    url = f"{LOOPBACK_ORIGIN}/local/v1/incidents/{incident_id}/{route}"
    try:
        body = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise GovernanceCommandError("governance request contract failed") from None
    if not body or len(body) > MAX_HTTP_REQUEST_BYTES:
        raise GovernanceCommandError("governance request exceeded byte limit")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Connection": "close",
            "Content-Type": "application/json",
            GOVERNANCE_OPERATION_HEADER: GOVERNANCE_OPERATION_VALUE,
        },
    )
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    try:
        with opener.open(request, timeout=GOVERNANCE_HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise GovernanceCommandError(
                    "loopback governance status was not successful"
                )
            if response.headers.get_content_type() != "application/json":
                raise GovernanceCommandError(
                    "loopback governance response was not JSON"
                )
            declared_lengths = response.headers.get_all("Content-Length", [])
            declared_length: int | None = None
            if len(declared_lengths) > 1:
                raise GovernanceCommandError(
                    "loopback governance response contract failed"
                )
            if declared_lengths:
                declared = declared_lengths[0]
                if (
                    not isinstance(declared, str)
                    or not declared.isascii()
                    or not declared.isdecimal()
                    or len(declared) > len(str(MAX_HTTP_RESPONSE_BYTES))
                    or int(declared) > MAX_HTTP_RESPONSE_BYTES
                ):
                    raise GovernanceCommandError(
                        "loopback governance response exceeded byte limit"
                    )
                declared_length = int(declared)
            response_url = getattr(response, "geturl", None)
            if callable(response_url) and response_url() != url:
                raise GovernanceCommandError(
                    "loopback governance response contract failed"
                )
            raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    except GovernanceCommandError:
        raise
    except (HTTPError, URLError, OSError, TimeoutError):
        raise GovernanceCommandError(
            "loopback governance endpoint is unavailable"
        ) from None
    if not isinstance(raw, bytes):
        raise GovernanceCommandError("loopback governance response contract failed")
    if len(raw) > MAX_HTTP_RESPONSE_BYTES:
        raise GovernanceCommandError("loopback governance response exceeded byte limit")
    if declared_length is not None and declared_length != len(raw):
        raise GovernanceCommandError("loopback governance response contract failed")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise GovernanceCommandError(
            "loopback governance response was invalid"
        ) from None
    if (
        not isinstance(value, dict)
        or set(value) != {"ok", "data"}
        or value.get("ok") is not True
        or not isinstance(value.get("data"), dict)
        or _json_depth(value) > MAX_HTTP_JSON_DEPTH
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    return value["data"]


def _response_object(container: dict[str, object], key: str) -> dict[str, object]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise GovernanceCommandError("loopback governance response contract failed")
    return value


def _response_revision(incident: dict[str, object]) -> int:
    value = incident.get("revision")
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_GOVERNANCE_REVISION + 1
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    return value


def _response_replayed(data: dict[str, object]) -> bool:
    value = data.get("replayed")
    if not isinstance(value, bool):
        raise GovernanceCommandError("loopback governance response contract failed")
    return value


def _response_action(data: dict[str, object]) -> tuple[str, dict[str, object]]:
    action = _response_object(data, "action")
    action_hash = action.get("action_hash")
    if (
        action.get("action_type") != "LOCAL_SIMULATION"
        or not isinstance(action_hash, str)
        or _SHA256.fullmatch(action_hash) is None
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    return action_hash, action


def _validate_incident_response(
    data: dict[str, object], incident_id: str, expected_status: str
) -> tuple[dict[str, object], int, bool]:
    incident = _response_object(data, "incident")
    if (
        incident.get("incident_id") != incident_id
        or incident.get("status") != expected_status
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    return incident, _response_revision(incident), _response_replayed(data)


def _print_governance_result(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _governance_prepare(incident_id: str) -> None:
    validate_inputs()
    data = _governance_request(
        "governance-prepare",
        incident_id,
        {
            "actor": GOVERNANCE_ACTOR,
            "idempotency_key": _stable_governance_key(
                "governance-prepare", incident_id
            ),
        },
    )
    incident = _response_object(data, "incident")
    revision = _response_revision(incident)
    replayed = _response_replayed(data)
    action_hash, _action = _response_action(data)
    approval = _response_object(data, "approval")
    rca = _response_object(data, "rca")
    status = incident.get("status")
    expected_approval = "APPROVED" if replayed else "PENDING"
    if (
        incident.get("incident_id") != incident_id
        or (replayed and status not in {"RESOLVED", "REOPENED"})
        or (not replayed and status != "AWAITING_APPROVAL")
        or approval.get("status") != expected_approval
        or approval.get("action_hash") != action_hash
        or rca.get("conclusion") != "CONCLUSIVE"
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    _print_governance_result(
        {
            "action_hash": action_hash,
            "command": "governance-prepare",
            "incident_id": incident["incident_id"],
            "ok": True,
            "replayed": replayed,
            "revision": revision,
            "status": incident["status"],
        }
    )


def _governance_decide(
    incident_id: str, action_hash: str, expected_revision: int
) -> None:
    validate_inputs()
    data = _governance_request(
        "governance-decide",
        incident_id,
        {
            "actor": GOVERNANCE_ACTOR,
            "approve": True,
            "expected_action_hash": action_hash,
            "expected_revision": expected_revision,
            "idempotency_key": _stable_governance_key(
                "governance-decide", incident_id, action_hash, expected_revision
            ),
            "reason": GOVERNANCE_REASON,
        },
    )
    incident = _response_object(data, "incident")
    revision = _response_revision(incident)
    replayed = _response_replayed(data)
    returned_hash, _action = _response_action(data)
    approval = _response_object(data, "approval")
    status = incident.get("status")
    if (
        incident.get("incident_id") != incident_id
        or (
            replayed
            and (
                status not in {"RESOLVED", "REOPENED"}
                or revision < expected_revision + 1
            )
        )
        or (
            not replayed
            and (status != "REMEDIATING" or revision != expected_revision + 1)
        )
        or returned_hash != action_hash
        or approval.get("status") != "APPROVED"
        or approval.get("action_hash") != action_hash
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    _print_governance_result(
        {
            "command": "governance-decide",
            "incident_id": incident["incident_id"],
            "ok": True,
            "replayed": replayed,
            "status": incident["status"],
        }
    )


def _governance_execute(incident_id: str, outcome: str) -> None:
    verification_passed = outcome == "passed"
    expected_incident_status = "RESOLVED" if verification_passed else "REOPENED"
    expected_verification_status = "PASSED" if verification_passed else "FAILED"
    validate_inputs()
    data = _governance_request(
        "governance-execute",
        incident_id,
        {
            "actor": GOVERNANCE_ACTOR,
            "idempotency_key": _stable_governance_key(
                "governance-execute", incident_id, outcome
            ),
            "verification_passed": verification_passed,
        },
    )
    incident, _revision, replayed = _validate_incident_response(
        data, incident_id, expected_incident_status
    )
    action_hash, _action = _response_action(data)
    approval = _response_object(data, "approval")
    verification = _response_object(data, "verification")
    if (
        approval.get("status") != "APPROVED"
        or approval.get("action_hash") != action_hash
        or verification.get("status") != expected_verification_status
    ):
        raise GovernanceCommandError("loopback governance response contract failed")
    _print_governance_result(
        {
            "command": "governance-execute",
            "incident_id": incident["incident_id"],
            "ok": True,
            "replayed": replayed,
            "status": incident["status"],
        }
    )


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


def _exec_local_stack(command: str, *command_arguments: str) -> None:
    allowed_arguments = {
        "init": (),
        "serve": (),
        "reset": ("--yes",),
        "demo-seed": (),
        "demo-verify-resolved": ("--expected-status", "RESOLVED"),
        "demo-verify-reopened": ("--expected-status", "REOPENED"),
    }
    lookup = command
    if command == "demo-verify" and command_arguments == (
        "--expected-status",
        "RESOLVED",
    ):
        lookup = "demo-verify-resolved"
    elif command == "demo-verify" and command_arguments == (
        "--expected-status",
        "REOPENED",
    ):
        lookup = "demo-verify-reopened"
    expected_arguments = allowed_arguments.get(lookup)
    if expected_arguments is None or (
        command != "demo-verify" and command_arguments != ()
    ):
        raise GovernanceCommandError("local stack command is not allowed")
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
        *expected_arguments,
    ]
    os.execv(sys.executable, arguments)


def _incident_id_argument(value: str) -> str:
    if _INCIDENT_ID.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("incident ID is invalid")
    return value


def _action_hash_argument(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("action hash is invalid")
    return value


def _revision_argument(value: str) -> int:
    if _REVISION.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("revision is invalid")
    revision = int(value)
    if revision > MAX_GOVERNANCE_REVISION:
        raise argparse.ArgumentTypeError("revision is invalid")
    return revision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="networkagent-local-container")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "serve", "reset", "probe", "smoke", "demo-seed"):
        commands.add_parser(command)
    verify = commands.add_parser("demo-verify")
    verify.add_argument(
        "--expected-status", choices=("RESOLVED", "REOPENED"), required=True
    )
    prepare = commands.add_parser("governance-prepare")
    prepare.add_argument("incident_id", type=_incident_id_argument)
    decide = commands.add_parser("governance-decide")
    decide.add_argument("incident_id", type=_incident_id_argument)
    decide.add_argument("action_hash", type=_action_hash_argument)
    decide.add_argument("revision", type=_revision_argument)
    execute = commands.add_parser("governance-execute")
    execute.add_argument("incident_id", type=_incident_id_argument)
    execute.add_argument("outcome", choices=("passed", "failed"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    command = arguments.command
    if command == "probe":
        _probe()
        return 0
    if command == "smoke":
        _smoke()
        return 0
    if command == "governance-prepare":
        _governance_prepare(arguments.incident_id)
        return 0
    if command == "governance-decide":
        _governance_decide(
            arguments.incident_id,
            arguments.action_hash,
            arguments.revision,
        )
        return 0
    if command == "governance-execute":
        _governance_execute(arguments.incident_id, arguments.outcome)
        return 0
    if command == "demo-verify":
        validate_inputs()
        _exec_local_stack(
            command,
            "--expected-status",
            arguments.expected_status,
        )
        return 0
    if command != "reset":
        validate_inputs()
    _exec_local_stack(command)
    return 0


def _run() -> int:
    try:
        return main()
    except (InputValidationError, HealthProbeError, GovernanceCommandError) as exc:
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
