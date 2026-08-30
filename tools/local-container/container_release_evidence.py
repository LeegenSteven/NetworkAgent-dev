#!/usr/bin/env python3
"""Build fail-closed release evidence for the local container image.

The utility intentionally uses only the Python standard library.  It validates
native Trivy JSON, a Trivy-generated CycloneDX SBOM, Docker inspection data,
and the Docker-save config object before it emits a commit-bound manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


CONTEXT_SCHEMA = "networkagent-container-release-context/v1"
MANIFEST_SCHEMA = "networkagent-container-release-evidence/v1"
TRIVY_VERSION = "0.74.0"
TRIVY_SETUP_ACTION = "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567"
TRIVY_DB_REPOSITORY = "ghcr.io/aquasecurity/trivy-db:2"
TRIVY_DB_SCHEMA_VERSION = 2
TRIVY_LINUX_AMD64_RELEASE_ARCHIVE_SHA256 = (
    "2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a"
)
TRIVY_LINUX_AMD64_BINARY_SHA256 = (
    "d89bcc6510a267f11b773398cbf1be5520ce39f9e8b6633178c4487f05b7d791"
)
CYCLONEDX_SPEC_VERSION = "1.7"
CYCLONEDX_SCHEMA = "http://cyclonedx.org/schema/bom-1.7.schema.json"
EXPECTED_IMAGE_REFERENCE = "networkagent-local:dev"
EXPECTED_BASE_IMAGE_NAME = "python:3.12-slim-bookworm"
EXPECTED_REPOSITORY = "LeegenSteven/NetworkAgent-dev"
EXPECTED_OS = "linux"
EXPECTED_ARCHITECTURE = "amd64"
EXPECTED_WORKFLOW = "telco-container"
EXPECTED_JOB = "build-inspect-smoke"
EXPECTED_RETENTION_DAYS = 14

MAX_CONTEXT_BYTES = 64 * 1024
MAX_INSPECT_BYTES = 2 * 1024 * 1024
MAX_TOOL_METADATA_BYTES = 256 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_SBOM_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_DOCKER_CONFIG_BYTES = 4 * 1024 * 1024
MAX_DOCKER_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOCKER_LAYER_BYTES = 1024 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 250_000
MAX_TOP_LEVEL_TAR_MEMBERS = 256
MAX_COMPONENTS = 100_000
MAX_DATABASE_AGE = timedelta(hours=48)
DATABASE_CLOCK_SKEW = timedelta(minutes=5)
DATABASE_EXPIRY_GRACE = timedelta(minutes=15)
MAX_EVIDENCE_RUN_AGE = timedelta(hours=2)
CYCLONEDX_COMPONENT_TYPES = {
    "application",
    "container",
    "cryptographic-asset",
    "data",
    "device",
    "device-driver",
    "file",
    "firmware",
    "framework",
    "library",
    "machine-learning-model",
    "operating-system",
    "platform",
}

SHA256_HEX = re.compile(r"[0-9a-f]{64}")
SHA256_ID = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}")
ARTIFACT_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")
UUID_URN = re.compile(
    r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)

EVIDENCE_FILES = {
    "context": "context.json",
    "image_inspect": "image-inspect.json",
    "base_image_inspect": "base-image-inspect.json",
    "trivy_version": "trivy-version.json",
    "db_metadata": "db-metadata.json",
    "scan_report": "trivy-vulnerability.json",
    "full_scan_report": "trivy-vulnerability-all.json",
    "sbom": "sbom.cdx.json",
}
MANIFEST_FILENAME = "release-manifest.json"


class EvidenceError(RuntimeError):
    """An evidence input is unsafe, malformed, or inconsistent."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise EvidenceError("timestamp is invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise EvidenceError("timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvidenceError("timestamp must use UTC")
    return parsed.astimezone(timezone.utc)


def _parse_aware_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise EvidenceError("timestamp is invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise EvidenceError("timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise EvidenceError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _valid_generated_at(value: object) -> bool:
    try:
        parsed = _parse_timestamp(value)
    except EvidenceError:
        return False
    now = datetime.now(timezone.utc)
    return now - MAX_EVIDENCE_RUN_AGE <= parsed <= now + timedelta(minutes=5)


def _identity_set_sha256(values: Sequence[str]) -> str:
    encoded = "".join(f"{len(value)}:{value}\n" for value in sorted(values)).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _reject_constant(value: str) -> object:
    raise EvidenceError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate JSON object key")
        result[key] = value
    return result


def _validate_json_budget(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise EvidenceError("JSON node budget exceeded")
        if depth > MAX_JSON_DEPTH:
            raise EvidenceError("JSON depth budget exceeded")
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise EvidenceError("JSON object key is invalid")
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            for item in current:
                stack.append((item, depth + 1))
        elif isinstance(current, float) and not math.isfinite(current):
            raise EvidenceError("non-finite JSON number is forbidden")
        elif not isinstance(current, (str, int, float, bool, type(None))):
            raise EvidenceError("JSON value type is invalid")


def _loads_json(raw: bytes) -> object:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError("JSON is unreadable") from error
    _validate_json_budget(value)
    return value


def _regular_file(path: Path, *, max_bytes: int | None = None) -> int:
    try:
        info = path.lstat()
    except OSError as error:
        raise EvidenceError("evidence file is unavailable") from error
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise EvidenceError("evidence input must be a regular file")
    if info.st_size < 1:
        raise EvidenceError("evidence input is empty")
    if max_bytes is not None and info.st_size > max_bytes:
        raise EvidenceError("evidence input exceeds its byte budget")
    return info.st_size


def _within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise EvidenceError("evidence path escapes its root") from None
    return resolved


def _expected_path(path: Path, root: Path, expected_name: str) -> Path:
    resolved = _within(path, root)
    expected = (root.resolve() / expected_name).resolve()
    if resolved != expected:
        raise EvidenceError("evidence filename is not the fixed contract")
    return resolved


def _validate_evidence_root(root: Path, *, include_manifest: bool) -> None:
    try:
        info = root.lstat()
    except OSError as error:
        raise EvidenceError("evidence root is unavailable") from error
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise EvidenceError("evidence root must be a real directory")
    expected = set(EVIDENCE_FILES.values())
    if include_manifest:
        expected.add(MANIFEST_FILENAME)
    try:
        entries = list(os.scandir(root))
    except OSError as error:
        raise EvidenceError("evidence root cannot be enumerated") from error
    actual = {entry.name for entry in entries}
    if len(entries) != len(actual) or actual != expected:
        raise EvidenceError("evidence root file set is not the fixed contract")
    for entry in entries:
        try:
            entry_info = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise EvidenceError("evidence root entry is unavailable") from error
        if entry.is_symlink() or not stat.S_ISREG(entry_info.st_mode):
            raise EvidenceError("evidence root entries must be regular files")


def _load_json(path: Path, *, max_bytes: int) -> object:
    _regular_file(path, max_bytes=max_bytes)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EvidenceError("evidence input is unreadable") from error
    return _loads_json(raw)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be an array")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise EvidenceError("evidence input cannot be hashed") from error
    return digest.hexdigest()


def _file_record(
    path: Path, *, label: str, max_bytes: int | None = None
) -> dict[str, object]:
    size = _regular_file(path, max_bytes=max_bytes)
    return {"path": label, "bytes": size, "sha256": _sha256(path)}


def _write_json(path: Path, payload: object) -> None:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise EvidenceError("output path is not a regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise EvidenceError("output directory is unsafe")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() and (temporary.is_symlink() or not temporary.is_file()):
        raise EvidenceError("temporary output path is unsafe")
    temporary.write_text(encoded, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _positive_environment_integer(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isascii() or not value.isdecimal():
        raise EvidenceError(f"{name} is invalid")
    parsed = int(value)
    if parsed <= 0:
        raise EvidenceError(f"{name} is invalid")
    return parsed


def _source_metadata() -> dict[str, object]:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    commit = os.environ.get("GITHUB_SHA", "")
    job = os.environ.get("GITHUB_JOB", "")
    workflow = os.environ.get("GITHUB_WORKFLOW", "")
    server = os.environ.get("GITHUB_SERVER_URL", "")
    if REPOSITORY.fullmatch(repository) is None or repository != EXPECTED_REPOSITORY:
        raise EvidenceError("GitHub repository is invalid")
    if COMMIT_SHA.fullmatch(commit) is None:
        raise EvidenceError("GitHub commit SHA is invalid")
    if SAFE_TOKEN.fullmatch(job) is None or job != EXPECTED_JOB:
        raise EvidenceError("GitHub job is invalid")
    if workflow != EXPECTED_WORKFLOW:
        raise EvidenceError("GitHub workflow is invalid")
    if server != "https://github.com":
        raise EvidenceError("GitHub server is invalid")
    run_id = _positive_environment_integer("GITHUB_RUN_ID")
    run_attempt = _positive_environment_integer("GITHUB_RUN_ATTEMPT")
    return {
        "repository": repository,
        "commit_sha": commit,
        "workflow": workflow,
        "job": job,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": f"{server}/{repository}/actions/runs/{run_id}",
    }


def _artifact(artifact_name: str, retention_days: int) -> dict[str, object]:
    if ARTIFACT_NAME.fullmatch(artifact_name) is None:
        raise EvidenceError("artifact name is invalid")
    if retention_days != EXPECTED_RETENTION_DAYS:
        raise EvidenceError("artifact retention is invalid")
    return {"name": artifact_name, "retention_days": retention_days}


def _runner_metadata() -> dict[str, str]:
    runner_os = os.environ.get("RUNNER_OS", "")
    runner_arch = os.environ.get("RUNNER_ARCH", "")
    if runner_os != "Linux" or runner_arch != "X64":
        raise EvidenceError("GitHub runner platform is invalid")
    return {"os": runner_os, "arch": runner_arch}


def initialize(args: argparse.Namespace) -> int:
    payload = {
        "schema_version": CONTEXT_SCHEMA,
        "generated_at_utc": _utc_now(),
        "source": _source_metadata(),
        "artifact": _artifact(args.artifact_name, args.artifact_retention_days),
        "expected": {
            "trivy_version": TRIVY_VERSION,
            "trivy_setup_action": TRIVY_SETUP_ACTION,
            "trivy_database_repository": TRIVY_DB_REPOSITORY,
            "trivy_database_schema_version": TRIVY_DB_SCHEMA_VERSION,
            "cyclonedx_spec_version": CYCLONEDX_SPEC_VERSION,
            "trivy_linux_amd64_release_archive_sha256": (
                TRIVY_LINUX_AMD64_RELEASE_ARCHIVE_SHA256
            ),
            "trivy_linux_amd64_binary_sha256": TRIVY_LINUX_AMD64_BINARY_SHA256,
        },
    }
    if args.output.name != EVIDENCE_FILES["context"]:
        raise EvidenceError("context output filename is invalid")
    _write_json(args.output, payload)
    print("container release evidence context: INITIALIZED")
    return 0


def _validate_context(
    path: Path, *, source: dict[str, object], artifact: dict[str, object]
) -> dict[str, Any]:
    payload = _mapping(_load_json(path, max_bytes=MAX_CONTEXT_BYTES), "context")
    if payload.get("schema_version") != CONTEXT_SCHEMA:
        raise EvidenceError("context schema mismatch")
    if not _valid_generated_at(payload.get("generated_at_utc")):
        raise EvidenceError("context timestamp is invalid")
    if payload.get("source") != source or payload.get("artifact") != artifact:
        raise EvidenceError("context source or artifact mismatch")
    expected = {
        "trivy_version": TRIVY_VERSION,
        "trivy_setup_action": TRIVY_SETUP_ACTION,
        "trivy_database_repository": TRIVY_DB_REPOSITORY,
        "trivy_database_schema_version": TRIVY_DB_SCHEMA_VERSION,
        "cyclonedx_spec_version": CYCLONEDX_SPEC_VERSION,
        "trivy_linux_amd64_release_archive_sha256": (
            TRIVY_LINUX_AMD64_RELEASE_ARCHIVE_SHA256
        ),
        "trivy_linux_amd64_binary_sha256": TRIVY_LINUX_AMD64_BINARY_SHA256,
    }
    if payload.get("expected") != expected:
        raise EvidenceError("context tool contract mismatch")
    return payload


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_ID.fullmatch(value) is None:
        raise EvidenceError(f"{label} digest is invalid")
    return value


def _inspect_payload(path: Path) -> dict[str, Any]:
    raw = _list(_load_json(path, max_bytes=MAX_INSPECT_BYTES), "Docker inspect")
    if len(raw) != 1:
        raise EvidenceError("Docker inspect must contain exactly one image")
    value = _mapping(raw[0], "Docker image")
    _digest(value.get("Id"), "Docker image ID")
    if (
        value.get("Os") != EXPECTED_OS
        or value.get("Architecture") != EXPECTED_ARCHITECTURE
    ):
        raise EvidenceError("Docker image platform is invalid")
    rootfs = _mapping(value.get("RootFS"), "Docker RootFS")
    layers = _list(rootfs.get("Layers"), "Docker RootFS layers")
    if rootfs.get("Type") != "layers" or not layers or len(layers) > 256:
        raise EvidenceError("Docker RootFS is invalid")
    for layer in layers:
        _digest(layer, "Docker layer")
    _mapping(value.get("Config"), "Docker Config")
    return value


def _archive_config(path: Path) -> dict[str, object]:
    _regular_file(path, max_bytes=MAX_DOCKER_ARCHIVE_BYTES)
    try:
        with tarfile.open(path, mode="r:") as archive:
            members = archive.getmembers()
            if not 2 <= len(members) <= MAX_TOP_LEVEL_TAR_MEMBERS:
                raise EvidenceError("Docker archive member count is invalid")
            seen: set[str] = set()
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                normalized = PurePosixPath(member.name)
                if (
                    not member.name
                    or "\\" in member.name
                    or normalized.is_absolute()
                    or ".." in normalized.parts
                    or member.name.casefold() in seen
                    or not (member.isfile() or member.isdir())
                ):
                    raise EvidenceError("Docker archive member path is unsafe")
                seen.add(member.name.casefold())
                by_name[member.name] = member
            manifest_member = by_name.get("manifest.json")
            if (
                manifest_member is None
                or not manifest_member.isfile()
                or manifest_member.size > MAX_CONTEXT_BYTES
            ):
                raise EvidenceError("Docker archive manifest is invalid")
            handle = archive.extractfile(manifest_member)
            if handle is None:
                raise EvidenceError("Docker archive manifest is unavailable")
            manifest = _list(_loads_json(handle.read()), "Docker archive manifest")
            if len(manifest) != 1:
                raise EvidenceError("Docker archive must contain one image")
            entry = _mapping(manifest[0], "Docker archive manifest entry")
            config_name = entry.get("Config")
            if not isinstance(config_name, str):
                raise EvidenceError("Docker config member is invalid")
            config_path = PurePosixPath(config_name)
            if (
                len(config_path.parts) != 1
                or config_path.suffix != ".json"
                or SHA256_HEX.fullmatch(config_path.stem) is None
            ):
                raise EvidenceError("Docker config member is invalid")
            repo_tags = entry.get("RepoTags")
            if (
                not isinstance(repo_tags, list)
                or EXPECTED_IMAGE_REFERENCE not in repo_tags
            ):
                raise EvidenceError("Docker archive image reference is invalid")
            layer_names = _list(entry.get("Layers"), "Docker archive layers")
            if (
                not layer_names
                or len(layer_names) > 256
                or len(layer_names) != len(set(layer_names))
            ):
                raise EvidenceError("Docker archive layer list is invalid")
            layer_hashes: list[str] = []
            for layer_name in layer_names:
                if not isinstance(layer_name, str) or not layer_name:
                    raise EvidenceError("Docker archive layer name is invalid")
                layer_path = PurePosixPath(layer_name)
                if (
                    "\\" in layer_name
                    or layer_path.is_absolute()
                    or ".." in layer_path.parts
                ):
                    raise EvidenceError("Docker archive layer name is unsafe")
                layer_member = by_name.get(layer_name)
                if (
                    layer_member is None
                    or not layer_member.isfile()
                    or not 0 < layer_member.size <= MAX_DOCKER_LAYER_BYTES
                ):
                    raise EvidenceError("Docker archive layer object is invalid")
                layer_handle = archive.extractfile(layer_member)
                if layer_handle is None:
                    raise EvidenceError("Docker archive layer is unavailable")
                layer_digest = hashlib.sha256()
                for chunk in iter(lambda: layer_handle.read(1024 * 1024), b""):
                    layer_digest.update(chunk)
                layer_hashes.append(f"sha256:{layer_digest.hexdigest()}")
            config_member = by_name.get(config_name)
            if (
                config_member is None
                or not config_member.isfile()
                or not 0 < config_member.size <= MAX_DOCKER_CONFIG_BYTES
            ):
                raise EvidenceError("Docker config object is invalid")
            handle = archive.extractfile(config_member)
            if handle is None:
                raise EvidenceError("Docker config object is unavailable")
            config_bytes = handle.read()
    except EvidenceError:
        raise
    except (KeyError, OSError, tarfile.TarError) as error:
        raise EvidenceError("Docker archive is invalid") from error

    actual_hash = hashlib.sha256(config_bytes).hexdigest()
    if actual_hash != config_path.stem:
        raise EvidenceError("Docker config digest mismatch")
    config = _mapping(_loads_json(config_bytes), "Docker config object")
    if (
        config.get("os") != EXPECTED_OS
        or config.get("architecture") != EXPECTED_ARCHITECTURE
    ):
        raise EvidenceError("Docker config platform is invalid")
    _mapping(config.get("config"), "Docker config settings")
    rootfs = _mapping(config.get("rootfs"), "Docker config RootFS")
    diff_ids = _list(rootfs.get("diff_ids"), "Docker config diff IDs")
    if rootfs.get("type") != "layers" or not diff_ids:
        raise EvidenceError("Docker config RootFS is invalid")
    for layer in diff_ids:
        _digest(layer, "Docker config layer")
    if list(diff_ids) != layer_hashes:
        raise EvidenceError("Docker layer content does not match config DiffIDs")
    return {
        "digest": f"sha256:{actual_hash}",
        "bytes": len(config_bytes),
        "sha256": actual_hash,
        "diff_ids": list(diff_ids),
        "platform": {
            "os": EXPECTED_OS,
            "architecture": EXPECTED_ARCHITECTURE,
        },
    }


def _container_identity(
    inspect_path: Path, archive_path: Path, image_reference: str
) -> dict[str, object]:
    if image_reference != EXPECTED_IMAGE_REFERENCE:
        raise EvidenceError("container image reference is invalid")
    inspection = _inspect_payload(inspect_path)
    image_id = _digest(inspection.get("Id"), "container image ID")
    repo_tags = inspection.get("RepoTags")
    if not isinstance(repo_tags, list) or image_reference not in repo_tags:
        raise EvidenceError("container image tag is invalid")
    config = _archive_config(archive_path)
    if config["digest"] != image_id:
        raise EvidenceError("container image ID does not match config digest")
    inspection_rootfs = _mapping(inspection.get("RootFS"), "Docker RootFS")
    inspection_diff_ids = _list(inspection_rootfs.get("Layers"), "Docker RootFS layers")
    if config["diff_ids"] != inspection_diff_ids:
        raise EvidenceError("Docker inspect and archive layer identities disagree")
    return {
        "reference": image_reference,
        "local_image_id": image_id,
        "config_digest": config["digest"],
        "config_bytes": config["bytes"],
        "config_sha256": config["sha256"],
        "rootfs_diff_ids": list(inspection_diff_ids),
        "platform": config["platform"],
    }


def _base_identity(path: Path, reference: str) -> dict[str, object]:
    prefix = EXPECTED_BASE_IMAGE_NAME + "@sha256:"
    if not reference.startswith(prefix):
        raise EvidenceError("base image reference is invalid")
    manifest_hash = reference.removeprefix(prefix)
    if SHA256_HEX.fullmatch(manifest_hash) is None:
        raise EvidenceError("base image manifest digest is invalid")
    inspection = _inspect_payload(path)
    local_id = _digest(inspection.get("Id"), "base image ID")
    repo_digests = inspection.get("RepoDigests")
    if not isinstance(repo_digests, list) or not repo_digests:
        raise EvidenceError("base image RepoDigests are invalid")
    expected_suffix = f"python@sha256:{manifest_hash}"
    if not any(
        isinstance(item, str)
        and (item == expected_suffix or item.endswith("/" + expected_suffix))
        for item in repo_digests
    ):
        raise EvidenceError("base image manifest digest mismatch")
    rootfs = _mapping(inspection.get("RootFS"), "base Docker RootFS")
    diff_ids = _list(rootfs.get("Layers"), "base Docker RootFS layers")
    return {
        "reference": reference,
        "manifest_digest": f"sha256:{manifest_hash}",
        "local_image_id": local_id,
        "rootfs_diff_ids": list(diff_ids),
        "platform": {
            "os": EXPECTED_OS,
            "architecture": EXPECTED_ARCHITECTURE,
        },
    }


def _database_metadata(value: object) -> dict[str, object]:
    raw = _mapping(value, "Trivy vulnerability database metadata")
    if raw.get("Version") != TRIVY_DB_SCHEMA_VERSION:
        raise EvidenceError("Trivy database schema version mismatch")
    updated = _parse_timestamp(raw.get("UpdatedAt"))
    next_update = _parse_timestamp(raw.get("NextUpdate"))
    downloaded = _parse_timestamp(raw.get("DownloadedAt"))
    if not updated < next_update or downloaded < updated:
        raise EvidenceError("Trivy database timestamps are inconsistent")
    return {
        "Version": TRIVY_DB_SCHEMA_VERSION,
        "UpdatedAt": str(raw["UpdatedAt"]),
        "NextUpdate": str(raw["NextUpdate"]),
        "DownloadedAt": str(raw["DownloadedAt"]),
    }


def _validate_database_freshness(
    metadata: dict[str, object], *, context_generated_at: datetime
) -> None:
    updated = _parse_timestamp(metadata["UpdatedAt"])
    next_update = _parse_timestamp(metadata["NextUpdate"])
    downloaded = _parse_timestamp(metadata["DownloadedAt"])
    now = datetime.now(timezone.utc)
    if updated < now - MAX_DATABASE_AGE or updated > now + DATABASE_CLOCK_SKEW:
        raise EvidenceError("Trivy database update is stale or future-dated")
    if next_update < now - DATABASE_EXPIRY_GRACE:
        raise EvidenceError("Trivy database next-update deadline has expired")
    if (
        downloaded < context_generated_at - DATABASE_CLOCK_SKEW
        or downloaded > now + DATABASE_CLOCK_SKEW
    ):
        raise EvidenceError("Trivy database download is outside the current run")


def _tool_and_database(
    *,
    version_path: Path,
    binary_path: Path,
    database_path: Path,
    database_metadata_path: Path,
    context_generated_at: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        version = _mapping(
            _load_json(version_path, max_bytes=MAX_TOOL_METADATA_BYTES),
            "Trivy version metadata",
        )
    except EvidenceError as error:
        raise EvidenceError("Trivy tool metadata is invalid") from error
    if version.get("Version") != TRIVY_VERSION:
        raise EvidenceError("Trivy tool version mismatch")
    try:
        from_version = _database_metadata(version.get("VulnerabilityDB"))
        from_file = _database_metadata(
            _load_json(database_metadata_path, max_bytes=MAX_TOOL_METADATA_BYTES)
        )
    except EvidenceError as error:
        raise EvidenceError("Trivy database metadata is invalid") from error
    if from_version != from_file:
        raise EvidenceError("Trivy database metadata sources disagree")
    _validate_database_freshness(from_file, context_generated_at=context_generated_at)
    try:
        binary = _file_record(binary_path, label="trivy")
    except EvidenceError as error:
        raise EvidenceError("Trivy tool binary is invalid") from error
    if binary["sha256"] != TRIVY_LINUX_AMD64_BINARY_SHA256:
        raise EvidenceError("Trivy tool binary digest mismatch")
    try:
        database_file = _file_record(database_path, label="trivy-cache/db/trivy.db")
    except EvidenceError as error:
        raise EvidenceError("Trivy database content is invalid") from error
    tool = {
        "name": "trivy",
        "version": TRIVY_VERSION,
        "expected_version": TRIVY_VERSION,
        "setup_action": TRIVY_SETUP_ACTION,
        "release_archive_sha256": TRIVY_LINUX_AMD64_RELEASE_ARCHIVE_SHA256,
        "expected_binary_sha256": TRIVY_LINUX_AMD64_BINARY_SHA256,
        "binary": binary,
    }
    database = {
        "repository": TRIVY_DB_REPOSITORY,
        "schema_version": TRIVY_DB_SCHEMA_VERSION,
        "updated_at": from_file["UpdatedAt"],
        "next_update": from_file["NextUpdate"],
        "downloaded_at": from_file["DownloadedAt"],
        "content": database_file,
    }
    return tool, database


def _scan_summary(
    path: Path,
    *,
    image_id: str,
    expected_diff_ids: Sequence[str],
    context_generated_at: datetime,
    exit_code: int,
    ignore_unfixed: bool,
) -> tuple[dict[str, object], list[str], list[str], list[str]]:
    report = _mapping(_load_json(path, max_bytes=MAX_SCAN_BYTES), "Trivy report")
    if report.get("SchemaVersion") != 2:
        raise EvidenceError("Trivy report schema mismatch")
    if report.get("ArtifactType") != "container_image":
        raise EvidenceError("Trivy report artifact type mismatch")
    trivy = _mapping(report.get("Trivy"), "Trivy report tool")
    if trivy.get("Version") != TRIVY_VERSION:
        raise EvidenceError("Trivy report tool version mismatch")
    created_at = _parse_aware_timestamp(report.get("CreatedAt"))
    now = datetime.now(timezone.utc)
    if (
        created_at < context_generated_at - DATABASE_CLOCK_SKEW
        or created_at > now + DATABASE_CLOCK_SKEW
    ):
        raise EvidenceError("Trivy report time is outside the current run")
    artifact_name = report.get("ArtifactName")
    if artifact_name != image_id:
        raise EvidenceError("Trivy report artifact name does not match the image")
    metadata = _mapping(report.get("Metadata"), "Trivy report metadata")
    if metadata.get("ImageID") != image_id:
        raise EvidenceError("Trivy report image ID mismatch")
    diff_ids = _list(metadata.get("DiffIDs"), "Trivy report DiffIDs")
    if diff_ids != list(expected_diff_ids):
        raise EvidenceError("Trivy report layer identities mismatch")
    image_config = _mapping(metadata.get("ImageConfig"), "Trivy image config")
    if (
        image_config.get("architecture") != EXPECTED_ARCHITECTURE
        or image_config.get("os") != EXPECTED_OS
    ):
        raise EvidenceError("Trivy report image platform mismatch")
    operating_system = _mapping(metadata.get("OS"), "Trivy report operating system")
    os_family = operating_system.get("Family")
    os_name = operating_system.get("Name")
    if (
        os_family != "debian"
        or not isinstance(os_name, str)
        or re.fullmatch(r"12(?:\.[0-9]+)*", os_name) is None
    ):
        raise EvidenceError("Trivy report operating system is invalid")
    repo_tags = _list(metadata.get("RepoTags"), "Trivy report repository tags")
    if EXPECTED_IMAGE_REFERENCE not in repo_tags:
        raise EvidenceError("Trivy report repository tag mismatch")
    for identity_key in ("ArtifactID",):
        identity = report.get(identity_key)
        if not isinstance(identity, str) or not identity or len(identity) > 512:
            raise EvidenceError("Trivy report artifact identity is invalid")
    reference = metadata.get("Reference")
    if not isinstance(reference, str) or not reference or len(reference) > 512:
        raise EvidenceError("Trivy report reference is invalid")
    results = _list(report.get("Results"), "Trivy results")
    if not results or len(results) > MAX_COMPONENTS:
        raise EvidenceError("Trivy result count is invalid")

    vulnerabilities = 0
    fixable = 0
    unfixed = 0
    high = 0
    critical = 0
    package_count = 0
    package_purls: list[str] = []
    seen_package_purls: set[str] = set()
    result_identities: set[tuple[str, str, str]] = set()
    seen_classes: set[str] = set()
    vulnerability_identities: list[str] = []
    fixable_vulnerability_identities: list[str] = []
    unfixed_vulnerability_identities: list[str] = []
    seen_vulnerability_identities: set[str] = set()
    for raw_result in results:
        result = _mapping(raw_result, "Trivy result")
        for key in ("Target", "Class", "Type"):
            if not isinstance(result.get(key), str) or not result[key]:
                raise EvidenceError("Trivy result identity is invalid")
        result_identity = (result["Target"], result["Class"], result["Type"])
        if result_identity in result_identities:
            raise EvidenceError("Trivy result identity is duplicated")
        result_identities.add(result_identity)
        result_class = result["Class"]
        result_type = result["Type"]
        if not (
            (result_class == "os-pkgs" and result_type == os_family)
            or (result_class == "lang-pkgs" and result_type == "python-pkg")
        ):
            raise EvidenceError("Trivy result scope is outside the runtime contract")
        seen_classes.add(result_class)
        packages = _list(result.get("Packages"), "Trivy packages")
        if not packages:
            raise EvidenceError("Trivy package inventory is empty")
        for raw_package in packages:
            package = _mapping(raw_package, "Trivy package")
            name = package.get("Name")
            package_version = package.get("Version")
            identifier = _mapping(package.get("Identifier"), "Trivy package identifier")
            purl = identifier.get("PURL")
            layer = _mapping(package.get("Layer"), "Trivy package layer")
            layer_diff_id = layer.get("DiffID")
            analyzed_by = package.get("AnalyzedBy")
            expected_purl_prefix = (
                "pkg:deb/" if result_class == "os-pkgs" else "pkg:pypi/"
            )
            expected_analyzer = "dpkg" if result_class == "os-pkgs" else "python-pkg"
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(package_version, str)
                or not package_version
                or not isinstance(purl, str)
                or not purl.startswith(expected_purl_prefix)
                or len(purl) > 2048
                or purl in seen_package_purls
                or layer_diff_id not in expected_diff_ids
                or analyzed_by != expected_analyzer
            ):
                raise EvidenceError("Trivy package identity is invalid")
            seen_package_purls.add(purl)
            package_purls.append(purl)
            package_count += 1
            if package_count > MAX_COMPONENTS:
                raise EvidenceError("Trivy package inventory exceeds limit")
        raw_vulnerabilities = result.get("Vulnerabilities")
        if raw_vulnerabilities is None:
            continue
        records = _list(raw_vulnerabilities, "Trivy vulnerabilities")
        for raw_vulnerability in records:
            item = _mapping(raw_vulnerability, "Trivy vulnerability")
            for key in ("VulnerabilityID", "PkgName", "Severity"):
                if not isinstance(item.get(key), str) or not item[key]:
                    raise EvidenceError("Trivy vulnerability identity is invalid")
            severity = item["Severity"]
            if severity not in {"HIGH", "CRITICAL"}:
                raise EvidenceError("Trivy vulnerability severity filter drifted")
            vulnerabilities += 1
            installed_version = item.get("InstalledVersion")
            fixed_version = item.get("FixedVersion")
            if (
                not isinstance(installed_version, str)
                or not installed_version
                or len(installed_version) > 2048
            ):
                raise EvidenceError("Trivy installed package version is invalid")
            if fixed_version is not None and (
                not isinstance(fixed_version, str) or len(fixed_version) > 2048
            ):
                raise EvidenceError("Trivy fixed package version is invalid")
            for key in ("VulnerabilityID", "PkgName", "Severity"):
                if len(str(item[key])) > 2048:
                    raise EvidenceError("Trivy vulnerability identity is too large")
            is_fixable = bool(fixed_version)
            if ignore_unfixed and not is_fixable:
                raise EvidenceError("Trivy fixable gate contains an unfixed finding")
            fixable += int(is_fixable)
            unfixed += int(not is_fixable)
            vulnerability_identity = json.dumps(
                [
                    result_class,
                    result_type,
                    item["VulnerabilityID"],
                    item["PkgName"],
                    installed_version,
                    fixed_version or "",
                    severity,
                ],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            if vulnerability_identity in seen_vulnerability_identities:
                raise EvidenceError("Trivy vulnerability identity is duplicated")
            seen_vulnerability_identities.add(vulnerability_identity)
            vulnerability_identities.append(vulnerability_identity)
            if is_fixable:
                fixable_vulnerability_identities.append(vulnerability_identity)
            else:
                unfixed_vulnerability_identities.append(vulnerability_identity)
            high += int(severity == "HIGH")
            critical += int(severity == "CRITICAL")
            if vulnerabilities > MAX_COMPONENTS:
                raise EvidenceError("Trivy vulnerability count exceeds limit")
    if seen_classes != {"os-pkgs", "lang-pkgs"}:
        raise EvidenceError("Trivy runtime package coverage is incomplete")
    passed = exit_code == 0 and (not ignore_unfixed or vulnerabilities == 0)
    return (
        {
            "scanner": "vuln",
            "gate_severities": ["CRITICAL", "HIGH"],
            "scan_exit_code": exit_code,
            "vulnerability_count": vulnerabilities,
            "fixable_vulnerability_count": fixable,
            "unfixed_vulnerability_count": unfixed,
            "vulnerability_inventory_sha256": _identity_set_sha256(
                vulnerability_identities
            ),
            "fixable_vulnerability_inventory_sha256": _identity_set_sha256(
                fixable_vulnerability_identities
            ),
            "unfixed_vulnerability_inventory_sha256": _identity_set_sha256(
                unfixed_vulnerability_identities
            ),
            "package_count": package_count,
            "package_inventory_sha256": _identity_set_sha256(package_purls),
            "os_family": os_family,
            "os_version": os_name,
            "created_at": str(report["CreatedAt"]),
            "high_count": high,
            "critical_count": critical,
            "ignore_unfixed": ignore_unfixed,
            "status": "PASS" if passed else "FAIL",
        },
        package_purls,
        vulnerability_identities,
        fixable_vulnerability_identities,
    )


def _sbom_summary(
    path: Path,
    *,
    image_id: str,
    expected_diff_ids: Sequence[str],
    expected_package_purls: Sequence[str],
    expected_os_family: str,
    expected_os_version: str,
    context_generated_at: datetime,
    exit_code: int,
) -> dict[str, object]:
    sbom = _mapping(_load_json(path, max_bytes=MAX_SBOM_BYTES), "CycloneDX SBOM")
    if (
        sbom.get("$schema") != CYCLONEDX_SCHEMA
        or sbom.get("bomFormat") != "CycloneDX"
        or sbom.get("specVersion") != CYCLONEDX_SPEC_VERSION
    ):
        raise EvidenceError("CycloneDX identity is invalid")
    version = sbom.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise EvidenceError("CycloneDX document version is invalid")
    serial = sbom.get("serialNumber")
    if not isinstance(serial, str) or UUID_URN.fullmatch(serial) is None:
        raise EvidenceError("CycloneDX serial number is invalid")
    metadata = _mapping(sbom.get("metadata"), "CycloneDX metadata")
    sbom_created_at = _parse_aware_timestamp(metadata.get("timestamp"))
    now = datetime.now(timezone.utc)
    if (
        sbom_created_at < context_generated_at - DATABASE_CLOCK_SKEW
        or sbom_created_at > now + DATABASE_CLOCK_SKEW
    ):
        raise EvidenceError("CycloneDX generation time is outside the current run")
    tools = _mapping(metadata.get("tools"), "CycloneDX tools")
    tool_components = _list(tools.get("components"), "CycloneDX tool components")
    expected_tool = [
        item
        for item in tool_components
        if isinstance(item, dict)
        and item.get("group") == "aquasecurity"
        and item.get("name") == "trivy"
        and item.get("version") == TRIVY_VERSION
        and item.get("type") == "application"
    ]
    if len(expected_tool) != 1 or len(tool_components) != 1:
        raise EvidenceError("CycloneDX Trivy tool metadata is invalid")
    root = _mapping(metadata.get("component"), "CycloneDX root component")
    if root.get("type") != "container" or root.get("name") != image_id:
        raise EvidenceError("CycloneDX root is not a container")
    root_ref = root.get("bom-ref")
    if not isinstance(root_ref, str) or not root_ref:
        raise EvidenceError("CycloneDX root reference is invalid")
    properties = _list(root.get("properties"), "CycloneDX root properties")
    property_map: dict[str, str] = {}
    diff_ids: list[str] = []
    for raw_property in properties:
        item = _mapping(raw_property, "CycloneDX property")
        name = item.get("name")
        value = item.get("value")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(value, str)
            or not value
        ):
            raise EvidenceError("CycloneDX property is invalid")
        if name == "aquasecurity:trivy:DiffID":
            _digest(value, "CycloneDX DiffID")
            diff_ids.append(value)
            continue
        if name in property_map:
            raise EvidenceError("CycloneDX singleton property is duplicated")
        property_map[name] = value
    if property_map.get("aquasecurity:trivy:ImageID") != image_id:
        raise EvidenceError("CycloneDX image ID mismatch")
    if property_map.get("aquasecurity:trivy:SchemaVersion") != "2":
        raise EvidenceError("CycloneDX Trivy schema mismatch")
    # CycloneDX serialization sorts Trivy properties by (name, value), so DiffID
    # order is lexical rather than Docker layer order. Sorting both lists keeps
    # duplicate multiplicity while binding the exact layer-identity multiset.
    if sorted(diff_ids) != sorted(expected_diff_ids):
        raise EvidenceError("CycloneDX layer identities do not match the image")

    components = _list(sbom.get("components"), "CycloneDX components")
    if not components or len(components) > MAX_COMPONENTS:
        raise EvidenceError("CycloneDX component count is invalid")
    references = {root_ref}
    component_type_counts: dict[str, int] = {}
    library_purls: list[str] = []
    operating_system_components: list[tuple[str, str]] = []
    for raw_component in components:
        component = _mapping(raw_component, "CycloneDX component")
        reference = component.get("bom-ref")
        if not isinstance(reference, str) or not reference or reference in references:
            raise EvidenceError("CycloneDX component reference is invalid")
        references.add(reference)
        if component.get("type") not in CYCLONEDX_COMPONENT_TYPES:
            raise EvidenceError("CycloneDX component type is invalid")
        component_type = str(component["type"])
        component_type_counts[component_type] = (
            component_type_counts.get(component_type, 0) + 1
        )
        if not isinstance(component.get("name"), str) or not component["name"]:
            raise EvidenceError("CycloneDX component name is invalid")
        component_version = component.get("version")
        if component_version is not None and (
            not isinstance(component_version, str) or not component_version
        ):
            raise EvidenceError("CycloneDX component version is invalid")
        if component_type == "library":
            purl = component.get("purl")
            if (
                not isinstance(purl, str)
                or not purl.startswith("pkg:")
                or len(purl) > 2048
                or purl in library_purls
            ):
                raise EvidenceError("CycloneDX library PURL is invalid")
            library_purls.append(purl)
        elif component_type == "operating-system":
            operating_system_components.append(
                (str(component.get("name")), str(component_version))
            )
    dependencies = _list(sbom.get("dependencies"), "CycloneDX dependencies")
    if not dependencies:
        raise EvidenceError("CycloneDX dependency graph is empty")
    seen_dependencies: set[str] = set()
    dependency_graph: dict[str, list[str]] = {}
    for raw_dependency in dependencies:
        dependency = _mapping(raw_dependency, "CycloneDX dependency")
        reference = dependency.get("ref")
        depends_on = dependency.get("dependsOn", [])
        if (
            not isinstance(reference, str)
            or reference not in references
            or reference in seen_dependencies
        ):
            raise EvidenceError("CycloneDX dependency reference is invalid")
        seen_dependencies.add(reference)
        children = _list(depends_on, "CycloneDX dependsOn")
        if len(children) != len(set(children)) or any(
            not isinstance(item, str) or item not in references for item in children
        ):
            raise EvidenceError("CycloneDX dependency edge is invalid")
        dependency_graph[reference] = list(children)
    if root_ref not in dependency_graph:
        raise EvidenceError("CycloneDX root dependency is missing")
    reachable: set[str] = set()
    pending = [root_ref]
    while pending:
        reference = pending.pop()
        if reference in reachable:
            continue
        reachable.add(reference)
        pending.extend(dependency_graph.get(reference, []))
    if reachable != references:
        raise EvidenceError(
            "CycloneDX dependency graph contains unreachable components"
        )
    if set(component_type_counts) != {"library", "operating-system"}:
        raise EvidenceError("CycloneDX runtime component coverage is incomplete")
    if operating_system_components != [(expected_os_family, expected_os_version)]:
        raise EvidenceError("CycloneDX operating system identity mismatch")
    if sorted(library_purls) != sorted(expected_package_purls):
        raise EvidenceError("CycloneDX and Trivy package inventories disagree")
    vulnerabilities = sbom.get("vulnerabilities")
    if vulnerabilities not in (None, []):
        raise EvidenceError("CycloneDX SBOM unexpectedly contains vulnerabilities")
    return {
        "format": "CycloneDX",
        "spec_version": CYCLONEDX_SPEC_VERSION,
        "component_count": len(components),
        "component_type_counts": dict(sorted(component_type_counts.items())),
        "package_inventory_sha256": _identity_set_sha256(library_purls),
        "created_at": str(metadata["timestamp"]),
        "rootfs_diff_id_count": len(diff_ids),
        "generation_exit_code": exit_code,
        "status": "PASS" if exit_code == 0 else "FAIL",
    }


def _record_evidence_files(
    args: argparse.Namespace,
) -> tuple[list[dict[str, object]], list[str]]:
    records: list[dict[str, object]] = []
    failures: list[str] = []
    limits = {
        "context": MAX_CONTEXT_BYTES,
        "image_inspect": MAX_INSPECT_BYTES,
        "base_image_inspect": MAX_INSPECT_BYTES,
        "trivy_version": MAX_TOOL_METADATA_BYTES,
        "db_metadata": MAX_TOOL_METADATA_BYTES,
        "scan_report": MAX_SCAN_BYTES,
        "full_scan_report": MAX_SCAN_BYTES,
        "sbom": MAX_SBOM_BYTES,
    }
    for key, name in EVIDENCE_FILES.items():
        path = getattr(args, key)
        try:
            fixed = _expected_path(path, args.evidence_root, name)
            records.append(_file_record(fixed, label=name, max_bytes=limits[key]))
        except EvidenceError:
            failures.append("evidence_file_set_invalid")
    return sorted(records, key=lambda item: str(item["path"])), failures


def _empty_image(args: argparse.Namespace) -> dict[str, object]:
    return {
        "reference": args.image_reference,
        "local_image_id": None,
        "config_digest": None,
        "config_bytes": 0,
        "config_sha256": None,
        "rootfs_diff_ids": [],
        "platform": {
            "os": EXPECTED_OS,
            "architecture": EXPECTED_ARCHITECTURE,
        },
        "base": {
            "reference": args.base_image_reference,
            "manifest_digest": None,
            "local_image_id": None,
            "rootfs_diff_ids": [],
            "platform": {
                "os": EXPECTED_OS,
                "architecture": EXPECTED_ARCHITECTURE,
            },
        },
    }


def _empty_tool() -> dict[str, object]:
    return {
        "name": "trivy",
        "version": None,
        "expected_version": TRIVY_VERSION,
        "setup_action": TRIVY_SETUP_ACTION,
        "release_archive_sha256": TRIVY_LINUX_AMD64_RELEASE_ARCHIVE_SHA256,
        "expected_binary_sha256": TRIVY_LINUX_AMD64_BINARY_SHA256,
        "binary": None,
    }


def _empty_database() -> dict[str, object]:
    return {
        "repository": TRIVY_DB_REPOSITORY,
        "schema_version": None,
        "updated_at": None,
        "next_update": None,
        "downloaded_at": None,
        "content": None,
    }


def _empty_security(exit_code: int, *, ignore_unfixed: bool) -> dict[str, object]:
    return {
        "scanner": "vuln",
        "gate_severities": ["CRITICAL", "HIGH"],
        "scan_exit_code": exit_code,
        "vulnerability_count": 0,
        "fixable_vulnerability_count": 0,
        "unfixed_vulnerability_count": 0,
        "vulnerability_inventory_sha256": None,
        "fixable_vulnerability_inventory_sha256": None,
        "unfixed_vulnerability_inventory_sha256": None,
        "package_count": 0,
        "package_inventory_sha256": None,
        "os_family": None,
        "os_version": None,
        "created_at": None,
        "high_count": 0,
        "critical_count": 0,
        "ignore_unfixed": ignore_unfixed,
        "status": "FAIL",
    }


def _empty_sbom(exit_code: int) -> dict[str, object]:
    return {
        "format": "CycloneDX",
        "spec_version": CYCLONEDX_SPEC_VERSION,
        "component_count": 0,
        "component_type_counts": {},
        "package_inventory_sha256": None,
        "created_at": None,
        "rootfs_diff_id_count": 0,
        "generation_exit_code": exit_code,
        "status": "FAIL",
    }


def _assemble(args: argparse.Namespace, *, generated_at: str) -> dict[str, object]:
    source = _source_metadata()
    artifact = _artifact(args.artifact_name, args.artifact_retention_days)
    failures: list[str] = []
    files, file_failures = _record_evidence_files(args)
    failures.extend(file_failures)

    context_generated_at: datetime | None = None
    try:
        context = _validate_context(args.context, source=source, artifact=artifact)
        context_generated_at = _parse_timestamp(context.get("generated_at_utc"))
    except EvidenceError:
        failures.append("source_context_invalid")

    image = _empty_image(args)
    try:
        final = _container_identity(
            args.image_inspect, args.image_archive, args.image_reference
        )
        base = _base_identity(args.base_image_inspect, args.base_image_reference)
        final_layers = _list(final.get("rootfs_diff_ids"), "container RootFS layers")
        base_layers = _list(base.get("rootfs_diff_ids"), "base RootFS layers")
        if (
            len(final_layers) <= len(base_layers)
            or final_layers[: len(base_layers)] != base_layers
        ):
            raise EvidenceError("base image layers are not a prefix of the final image")
        image = {**final, "base": base}
    except EvidenceError as error:
        if "base image" in str(error):
            failures.append("base_image_identity_invalid")
        else:
            failures.append("container_image_identity_invalid")

    tool = _empty_tool()
    database = _empty_database()
    try:
        if context_generated_at is None:
            raise EvidenceError("source context time is unavailable")
        tool, database = _tool_and_database(
            version_path=args.trivy_version,
            binary_path=args.trivy_binary,
            database_path=args.trivy_db,
            database_metadata_path=args.db_metadata,
            context_generated_at=context_generated_at,
        )
    except EvidenceError as error:
        if "database" in str(error).lower():
            failures.append("trivy_database_metadata_invalid")
        else:
            failures.append("trivy_tool_metadata_invalid")

    security = _empty_security(args.scan_exit_code, ignore_unfixed=True)
    scan_package_purls: list[str] = []
    gate_vulnerability_identities: list[str] = []
    gate_fixable_vulnerability_identities: list[str] = []
    if (
        isinstance(image.get("local_image_id"), str)
        and context_generated_at is not None
    ):
        try:
            (
                security,
                scan_package_purls,
                gate_vulnerability_identities,
                gate_fixable_vulnerability_identities,
            ) = _scan_summary(
                args.scan_report,
                image_id=str(image["local_image_id"]),
                expected_diff_ids=_list(
                    image.get("rootfs_diff_ids"), "container RootFS diff IDs"
                ),
                context_generated_at=context_generated_at,
                exit_code=args.scan_exit_code,
                ignore_unfixed=True,
            )
        except EvidenceError:
            failures.append("trivy_scan_invalid")
    else:
        failures.append("trivy_scan_invalid")
    if args.scan_exit_code != 0:
        failures.append("trivy_scan_command_failed")
    if security["high_count"] or security["critical_count"]:
        failures.append("fixable_critical_or_high_vulnerabilities_found")

    diagnostic_security = _empty_security(
        args.full_scan_exit_code, ignore_unfixed=False
    )
    diagnostic_package_purls: list[str] = []
    diagnostic_vulnerability_identities: list[str] = []
    diagnostic_fixable_vulnerability_identities: list[str] = []
    if (
        isinstance(image.get("local_image_id"), str)
        and context_generated_at is not None
    ):
        try:
            (
                diagnostic_security,
                diagnostic_package_purls,
                diagnostic_vulnerability_identities,
                diagnostic_fixable_vulnerability_identities,
            ) = _scan_summary(
                args.full_scan_report,
                image_id=str(image["local_image_id"]),
                expected_diff_ids=_list(
                    image.get("rootfs_diff_ids"), "container RootFS diff IDs"
                ),
                context_generated_at=context_generated_at,
                exit_code=args.full_scan_exit_code,
                ignore_unfixed=False,
            )
        except EvidenceError:
            failures.append("trivy_full_scan_invalid")
    else:
        failures.append("trivy_full_scan_invalid")
    if args.full_scan_exit_code != 0:
        failures.append("trivy_full_scan_command_failed")
    if (
        sorted(diagnostic_package_purls) != sorted(scan_package_purls)
        or sorted(gate_vulnerability_identities)
        != sorted(gate_fixable_vulnerability_identities)
        or sorted(gate_fixable_vulnerability_identities)
        != sorted(diagnostic_fixable_vulnerability_identities)
    ):
        failures.append("trivy_gate_and_diagnostic_disagree")

    sbom = _empty_sbom(args.sbom_exit_code)
    if (
        isinstance(image.get("local_image_id"), str)
        and context_generated_at is not None
    ):
        try:
            sbom = _sbom_summary(
                args.sbom,
                image_id=str(image["local_image_id"]),
                expected_diff_ids=_list(
                    image.get("rootfs_diff_ids"), "container RootFS diff IDs"
                ),
                expected_package_purls=scan_package_purls,
                expected_os_family=str(security.get("os_family")),
                expected_os_version=str(security.get("os_version")),
                context_generated_at=context_generated_at,
                exit_code=args.sbom_exit_code,
            )
        except EvidenceError:
            failures.append("cyclonedx_sbom_invalid")
    else:
        failures.append("cyclonedx_sbom_invalid")
    if args.sbom_exit_code != 0:
        failures.append("cyclonedx_sbom_command_failed")

    if database.get("schema_version") != TRIVY_DB_SCHEMA_VERSION:
        failures.append("trivy_database_metadata_invalid")
    if tool.get("version") != TRIVY_VERSION:
        failures.append("trivy_tool_metadata_invalid")
    if security.get("status") != "PASS":
        failures.append("fixable_critical_high_gate_failed")
    if diagnostic_security.get("status") != "PASS":
        failures.append("trivy_full_diagnostic_failed")
    if sbom.get("status") != "PASS":
        failures.append("cyclonedx_sbom_failed")

    try:
        manifest_generated_at = _parse_timestamp(generated_at)
        timeline = [manifest_generated_at]
        if context_generated_at is not None:
            timeline.append(context_generated_at)
        if isinstance(database.get("downloaded_at"), str):
            timeline.append(_parse_timestamp(database["downloaded_at"]))
        if isinstance(security.get("created_at"), str):
            timeline.append(_parse_aware_timestamp(security["created_at"]))
        if isinstance(diagnostic_security.get("created_at"), str):
            timeline.append(_parse_aware_timestamp(diagnostic_security["created_at"]))
        if isinstance(sbom.get("created_at"), str):
            timeline.append(_parse_aware_timestamp(sbom["created_at"]))
        if not _valid_generated_at(generated_at) or manifest_generated_at + timedelta(
            seconds=5
        ) < max(timeline):
            raise EvidenceError("manifest timeline is invalid")
    except EvidenceError:
        failures.append("manifest_timeline_invalid")

    unique_failures = sorted(set(failures))
    status = "PASS" if not unique_failures else "FAIL"
    file_by_name = {str(item["path"]): item for item in files}
    security["report"] = file_by_name.get(EVIDENCE_FILES["scan_report"])
    diagnostic_security["report"] = file_by_name.get(EVIDENCE_FILES["full_scan_report"])
    sbom["file"] = file_by_name.get(EVIDENCE_FILES["sbom"])
    database["metadata_file"] = file_by_name.get(EVIDENCE_FILES["db_metadata"])
    return {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at_utc": generated_at,
        "status": status,
        "classification": (
            "PENDING VERIFY-MANIFEST" if status == "PASS" else "DIAGNOSTIC ONLY"
        ),
        "failures": unique_failures,
        "source": source,
        "artifact": artifact,
        "runner": _runner_metadata(),
        "image": image,
        "tool": tool,
        "database": database,
        "security": security,
        "security_diagnostic": diagnostic_security,
        "sbom": sbom,
        "files": files,
        "boundaries": {
            "registry_image_published": False,
            "signing_attestation_or_provenance": False,
            "trivy_database_registry_digest_or_signature": False,
            "offline_independent_reverification": False,
        },
    }


def _append_summary(path: Path, manifest: dict[str, object]) -> None:
    source = _mapping(manifest["source"], "manifest source")
    image = _mapping(manifest["image"], "manifest image")
    tool = _mapping(manifest["tool"], "manifest tool")
    database = _mapping(manifest["database"], "manifest database")
    security = _mapping(manifest["security"], "manifest security")
    diagnostic_security = _mapping(
        manifest["security_diagnostic"], "manifest diagnostic security"
    )
    sbom = _mapping(manifest["sbom"], "manifest SBOM")
    failures = _list(manifest["failures"], "manifest failures")
    database_content = database.get("content")
    database_sha = (
        database_content.get("sha256")
        if isinstance(database_content, dict)
        else "unavailable"
    )
    lines = [
        "## Container release evidence",
        "",
        f"- Release Gate: **{manifest['status']}**",
        f"- Artifact classification: **{manifest['classification']}**",
        "- Failures: "
        + (", ".join(f"`{item}`" for item in failures) if failures else "none"),
        f"- Commit: `{source['commit_sha']}`",
        f"- Run: {source['run_url']}",
        f"- Job: `{source['job']}`",
        f"- Local image ID: `{image.get('local_image_id')}`",
        f"- Image config digest: `{image.get('config_digest')}`",
        f"- Trivy: `{tool.get('version')}` (expected `{TRIVY_VERSION}`)",
        f"- Trivy DB source: `{TRIVY_DB_REPOSITORY}`",
        f"- Trivy DB content SHA-256: `{database_sha}`",
        "- Fixable Critical/High Gate: **"
        + str(security.get("status", "FAIL"))
        + "** "
        + f"(Critical {security.get('critical_count', 0)}, "
        + f"High {security.get('high_count', 0)})",
        "- Full Critical/High diagnostic: **"
        + str(diagnostic_security.get("status", "FAIL"))
        + "** "
        + f"(Critical {diagnostic_security.get('critical_count', 0)}, "
        + f"High {diagnostic_security.get('high_count', 0)}, "
        + f"fixable {diagnostic_security.get('fixable_vulnerability_count', 0)}, "
        + f"unfixed {diagnostic_security.get('unfixed_vulnerability_count', 0)})",
        "- Vulnerability policy: `--ignore-unfixed` applies only to the release "
        "gate; the complete Critical/High diagnostic report is retained",
        "- CycloneDX container SBOM: **"
        + str(sbom.get("status", "FAIL"))
        + "** "
        + f"({sbom.get('component_count', 0)} components, "
        + f"spec {sbom.get('spec_version')})",
        "- Registry image publication: **NOT PERFORMED**",
        "- Signing/attestation/provenance: **NOT PERFORMED**",
        "- Offline independent re-verification: **NOT AVAILABLE** ",
        "  (image, scanner binary, and database are not uploaded)",
        "- Trivy DB registry digest/signature: **NOT CAPTURED**",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def build_manifest(args: argparse.Namespace) -> int:
    if args.output.name != MANIFEST_FILENAME:
        raise EvidenceError("manifest output filename is invalid")
    _within(args.output, args.evidence_root)
    _validate_evidence_root(args.evidence_root, include_manifest=False)
    manifest = _assemble(args, generated_at=_utc_now())
    _write_json(args.output, manifest)
    _validate_evidence_root(args.evidence_root, include_manifest=True)
    _append_summary(args.summary, manifest)
    print(
        "container release evidence manifest: "
        f"{manifest['status']} ({len(manifest['files'])} evidence files)"
    )
    return 0 if manifest["status"] == "PASS" else 1


def verify_manifest(args: argparse.Namespace) -> int:
    errors: list[str] = []
    try:
        if args.manifest.name != MANIFEST_FILENAME:
            raise EvidenceError("manifest filename is invalid")
        _within(args.manifest, args.evidence_root)
        _validate_evidence_root(args.evidence_root, include_manifest=True)
        payload = _mapping(
            _load_json(args.manifest, max_bytes=MAX_MANIFEST_BYTES),
            "release manifest",
        )
        if payload.get("schema_version") != MANIFEST_SCHEMA:
            errors.append("manifest_schema_mismatch")
        generated_at = payload.get("generated_at_utc")
        if not _valid_generated_at(generated_at):
            errors.append("manifest_generated_at_invalid")
            generated_at = _utc_now()
        expected = _assemble(args, generated_at=str(generated_at))
        _validate_evidence_root(args.evidence_root, include_manifest=True)
        if payload != expected:
            errors.append("manifest_content_or_digest_mismatch")
        if payload.get("status") != "PASS" or payload.get("failures") != []:
            errors.append("manifest_status_failed")
        if payload.get("classification") != "PENDING VERIFY-MANIFEST":
            errors.append("manifest_classification_invalid")
    except (EvidenceError, KeyError, OSError, TypeError, ValueError) as error:
        errors.append(f"manifest_verification_error:{type(error).__name__}")
    if errors:
        for error in sorted(set(errors)):
            print(
                f"container release evidence verification error: {error}",
                file=sys.stderr,
            )
        return 1
    print("container release evidence manifest verification: PASS")
    return 0


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--image-inspect", type=Path, required=True)
    parser.add_argument("--base-image-inspect", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--trivy-version", type=Path, required=True)
    parser.add_argument("--trivy-binary", type=Path, required=True)
    parser.add_argument("--trivy-db", type=Path, required=True)
    parser.add_argument("--db-metadata", type=Path, required=True)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--scan-exit-code", type=int, required=True)
    parser.add_argument("--full-scan-report", type=Path, required=True)
    parser.add_argument("--full-scan-exit-code", type=int, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--sbom-exit-code", type=int, required=True)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--base-image-reference", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--artifact-retention-days", type=int, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    initialize_parser = commands.add_parser(
        "initialize", help="write a commit/run/job-bound diagnostic context"
    )
    initialize_parser.add_argument("--output", type=Path, required=True)
    initialize_parser.add_argument("--artifact-name", required=True)
    initialize_parser.add_argument("--artifact-retention-days", type=int, required=True)
    initialize_parser.set_defaults(func=initialize)

    manifest = commands.add_parser(
        "manifest", help="validate reports and write release evidence"
    )
    _common_arguments(manifest)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--summary", type=Path, required=True)
    manifest.set_defaults(func=build_manifest)

    verify = commands.add_parser(
        "verify-manifest", help="recompute and verify all manifest bindings"
    )
    _common_arguments(verify)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.set_defaults(func=verify_manifest)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "artifact_retention_days", 0) != EXPECTED_RETENTION_DAYS:
        print(
            "container release evidence error: retention must be 14 days",
            file=sys.stderr,
        )
        return 2
    for name in ("scan_exit_code", "full_scan_exit_code", "sbom_exit_code"):
        value = getattr(args, name, 0)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 255
        ):
            print(
                "container release evidence error: exit code is invalid",
                file=sys.stderr,
            )
            return 2
    try:
        return int(args.func(args))
    except (
        EvidenceError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"container release evidence error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
