#!/usr/bin/env python3
"""Build machine-readable release evidence for NetworkAgent wheels.

The script intentionally uses only the Python standard library.  CI invokes it
after wheel construction and after ``pip-audit`` has produced its native JSON
report and CycloneDX SBOM.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import sys
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile, ZipInfo


MANIFEST_SCHEMA = "networkagent-release-evidence/v1"
WHEEL_SCAN_SCHEMA = "networkagent-wheel-content-scan/v1"
RUNTIME_INVENTORY_SCHEMA = "networkagent-runtime-inventory/v1"
# pip-audit 2.10.1 emits CycloneDX 1.4; keep this coupled to the pinned
# producer version in the workflows and fail closed on a silent format drift.
CYCLONEDX_SPEC_VERSION = "1.4"
DEFAULT_MAX_WHEEL_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_SECRET_SCAN_MEMBER_BYTES = 2 * 1024 * 1024
_EVIDENCE_FILENAMES = {
    "pip-audit-sbom.cdx.json",
    "pip-audit.json",
    "runtime-inventory.json",
    "runtime-requirements.txt",
    "sbom.cdx.json",
    "wheel-content-scan.json",
}
_SAFE_SUPPLEMENTAL_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

_FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "test",
    "tests",
}
_FORBIDDEN_SUFFIXES = {
    ".7z",
    ".csv",
    ".db",
    ".duckdb",
    ".env",
    ".gz",
    ".jsonl",
    ".key",
    ".parquet",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pickle",
    ".pkl",
    ".sqlite",
    ".tar",
    ".xz",
    ".zip",
}
_ALLOWED_JSON_MEMBERS = {"telco_lab/catalogs/default.json"}
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    re.compile(rb"gh[pousr]_[0-9A-Za-z]{36,}"),
    re.compile(rb"xox[baprs]-[0-9A-Za-z-]{20,}"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_generated_at_utc(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc and parsed <= datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _wheel_paths(wheel_root: Path) -> tuple[Path, ...]:
    wheels = tuple(sorted(wheel_root.rglob("*.whl")))
    if not wheels:
        raise ValueError(f"no wheels found below {wheel_root}")
    for wheel in wheels:
        if wheel.is_symlink() or not wheel.is_file():
            raise ValueError(f"wheel is not a regular file: {wheel}")
    return wheels


def _file_record(path: Path, *, base: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"evidence input is not a regular file: {path}")
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(base.resolve()).as_posix()
    except ValueError:
        relative = resolved.as_posix()
    return {
        "path": relative,
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _member_type(info: ZipInfo) -> str:
    mode = (info.external_attr >> 16) & 0o170000
    if info.is_dir():
        return "directory"
    if mode in (0, stat.S_IFREG):
        return "regular"
    if mode == stat.S_IFLNK:
        return "symlink"
    return f"special:{oct(mode)}"


def _member_violations(info: ZipInfo) -> list[str]:
    violations: list[str] = []
    name = info.filename
    lowered = name.casefold()
    path = PurePosixPath(name)
    parts = tuple(part.casefold() for part in path.parts)

    if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
        violations.append("unsafe_member_path")
    if _member_type(info) not in {"regular", "directory"}:
        violations.append("non_regular_member")
    if any(part in _FORBIDDEN_PARTS for part in parts):
        violations.append("forbidden_build_or_test_path")
    suffix = path.suffix.casefold()
    if suffix in _FORBIDDEN_SUFFIXES or suffix in {".pyc", ".pyo"}:
        violations.append("forbidden_file_type")
    if suffix == ".json" and lowered not in _ALLOWED_JSON_MEMBERS:
        violations.append("unexpected_json_payload")
    return violations


def _scan_wheel(
    wheel: Path,
    *,
    base: Path,
    max_wheel_bytes: int,
    max_uncompressed_bytes: int,
) -> dict[str, object]:
    violations: list[dict[str, str]] = []
    member_count = 0
    uncompressed_bytes = 0
    seen_names: set[str] = set()
    wheel_bytes = wheel.stat().st_size

    if wheel_bytes > max_wheel_bytes:
        violations.append({"member": "", "rule": "wheel_size_limit_exceeded"})

    try:
        with ZipFile(wheel) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                violations.append({"member": bad_member, "rule": "zip_crc_failure"})
            for info in archive.infolist():
                member_count += 1
                uncompressed_bytes += info.file_size
                normalized = info.filename.casefold()
                if normalized in seen_names:
                    violations.append(
                        {"member": info.filename, "rule": "duplicate_member_name"}
                    )
                seen_names.add(normalized)
                for rule in _member_violations(info):
                    violations.append({"member": info.filename, "rule": rule})

                if info.is_dir():
                    continue
                if info.file_size > MAX_SECRET_SCAN_MEMBER_BYTES:
                    violations.append(
                        {
                            "member": info.filename,
                            "rule": "secret_scan_size_limit_exceeded",
                        }
                    )
                    continue
                content = archive.read(info)
                if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
                    violations.append(
                        {"member": info.filename, "rule": "strong_secret_marker"}
                    )
    except BadZipFile:
        violations.append({"member": "", "rule": "invalid_wheel_zip"})

    if uncompressed_bytes > max_uncompressed_bytes:
        violations.append({"member": "", "rule": "uncompressed_size_limit_exceeded"})

    record = _file_record(wheel, base=base)
    record.update(
        {
            "member_count": member_count,
            "uncompressed_bytes": uncompressed_bytes,
            "status": "PASS" if not violations else "FAIL",
            "violations": violations,
        }
    )
    return record


def scan_wheels(args: argparse.Namespace) -> int:
    wheel_root = args.wheel_root.resolve()
    try:
        wheels = _wheel_paths(wheel_root)
        records = [
            _scan_wheel(
                wheel,
                base=Path.cwd().resolve(),
                max_wheel_bytes=args.max_wheel_bytes,
                max_uncompressed_bytes=args.max_uncompressed_bytes,
            )
            for wheel in wheels
        ]
        errors: list[str] = []
    except ValueError as error:
        records = []
        errors = [str(error)]

    passed = not errors and all(record["status"] == "PASS" for record in records)
    payload = {
        "schema_version": WHEEL_SCAN_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": "PASS" if passed else "FAIL",
        "limits": {
            "max_wheel_bytes": args.max_wheel_bytes,
            "max_uncompressed_bytes": args.max_uncompressed_bytes,
        },
        "errors": errors,
        "wheels": records,
    }
    _write_json(args.output, payload)
    print(f"wheel content scan: {payload['status']} ({len(records)} wheel(s))")
    return 0 if passed else 1


def _metadata_value(metadata: Any, key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def build_runtime_inventory(args: argparse.Namespace) -> int:
    environment_path = args.environment_path.resolve()
    if environment_path.is_symlink() or not environment_path.is_dir():
        raise ValueError(f"runtime environment is not a directory: {environment_path}")

    expected_first_party = {_normalize_distribution(name) for name in args.first_party}
    packages: dict[str, dict[str, object]] = {}
    for distribution in importlib.metadata.distributions(path=[str(environment_path)]):
        name = _metadata_value(distribution.metadata, "Name")
        version = distribution.version
        if name is None or not isinstance(version, str) or not version.strip():
            raise ValueError(
                "runtime distribution has invalid name or version metadata"
            )
        normalized = _normalize_distribution(name)
        record = {
            "name": name,
            "normalized_name": normalized,
            "version": version.strip(),
            "scope": (
                "first-party" if normalized in expected_first_party else "runtime"
            ),
            "license_expression": _metadata_value(
                distribution.metadata, "License-Expression"
            ),
            "license": _metadata_value(distribution.metadata, "License"),
        }
        if normalized in packages:
            raise ValueError(f"duplicate runtime distribution: {normalized}")
        packages[normalized] = record

    missing = sorted(expected_first_party - set(packages))
    if missing:
        raise ValueError(f"missing first-party runtime distributions: {missing}")
    runtime_packages = [
        record for record in packages.values() if record["scope"] == "runtime"
    ]
    if not runtime_packages:
        raise ValueError("runtime dependency inventory is empty")

    ordered = sorted(packages.values(), key=lambda item: str(item["normalized_name"]))
    args.requirements_output.parent.mkdir(parents=True, exist_ok=True)
    requirements = [
        f"{record['name']}=={record['version']}"
        for record in sorted(
            runtime_packages, key=lambda item: str(item["normalized_name"])
        )
    ]
    args.requirements_output.write_text(
        "\n".join(requirements) + "\n", encoding="utf-8", newline="\n"
    )
    payload = {
        "schema_version": RUNTIME_INVENTORY_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": "PASS",
        "first_party": sorted(expected_first_party),
        "package_count": len(ordered),
        "runtime_dependency_count": len(runtime_packages),
        "requirements": _file_record(
            args.requirements_output,
            base=args.requirements_output.parent.resolve(),
        ),
        "packages": ordered,
    }
    _write_json(args.output, payload)
    print(
        "runtime inventory: PASS "
        f"({len(expected_first_party)} first-party, "
        f"{len(runtime_packages)} runtime dependencies)"
    )
    return 0


def _wheel_component(wheel: Path) -> dict[str, object]:
    with ZipFile(wheel) as archive:
        metadata_members = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_members) != 1:
            raise ValueError(f"wheel has invalid METADATA count: {wheel}")
        metadata = Parser().parsestr(
            archive.read(metadata_members[0]).decode("utf-8", errors="strict")
        )
    name = _metadata_value(metadata, "Name")
    version = _metadata_value(metadata, "Version")
    if name is None or version is None:
        raise ValueError(f"wheel METADATA is missing Name or Version: {wheel}")
    normalized = _normalize_distribution(name)
    bom_ref = (
        "pkg:generic/networkagent/"
        f"{quote(normalized, safe='')}@{quote(version, safe='')}"
    )
    return {
        "bom-ref": bom_ref,
        "type": "library",
        "name": name,
        "version": version,
        "purl": bom_ref,
        "hashes": [{"alg": "SHA-256", "content": _sha256(wheel)}],
        "properties": [
            {"name": "networkagent:first-party", "value": "true"},
            {"name": "networkagent:wheel-filename", "value": wheel.name},
        ],
    }


def _first_party_versions(wheels: Iterable[Path]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for wheel in wheels:
        component = _wheel_component(wheel)
        normalized = _normalize_distribution(str(component["name"]))
        if normalized in versions:
            raise ValueError(f"duplicate first-party wheel: {normalized}")
        versions[normalized] = str(component["version"])
    if not versions:
        raise ValueError("first-party wheel set is empty")
    return versions


def finalize_sbom(args: argparse.Namespace) -> int:
    payload = _load_json(args.input)
    if payload.get("bomFormat") != "CycloneDX":
        raise ValueError("base SBOM is not CycloneDX JSON")
    if payload.get("specVersion") != CYCLONEDX_SPEC_VERSION:
        raise ValueError("base SBOM uses an unsupported CycloneDX version")
    bom_version = payload.get("version")
    if (
        not isinstance(bom_version, int)
        or isinstance(bom_version, bool)
        or bom_version <= 0
    ):
        raise ValueError("base SBOM version must be a positive integer")
    components = payload.get("components")
    dependencies = payload.get("dependencies")
    if not isinstance(components, list) or not isinstance(dependencies, list):
        raise ValueError("base CycloneDX components/dependencies are invalid")

    wheels = _expected_wheels(args.wheel_root.resolve(), args.expected_wheel)
    first_party = [_wheel_component(wheel) for wheel in wheels]
    inventory, runtime_dependencies = _runtime_inventory_summary(
        args.runtime_inventory,
        args.runtime_requirements,
        {
            _normalize_distribution(str(component["name"])): str(component["version"])
            for component in first_party
        },
    )
    if inventory["status"] != "PASS":
        raise ValueError(
            "runtime inventory is invalid: " + ",".join(inventory["errors"])
        )
    existing_names = {
        _normalize_distribution(str(component.get("name", "")))
        for component in components
        if isinstance(component, dict)
    }
    raw_runtime: dict[str, str] = {}
    runtime_ref_map: dict[str, str] = {}
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("base CycloneDX component must be an object")
        normalized = _normalize_distribution(str(component.get("name", "")))
        version = component.get("version")
        bom_ref = component.get("bom-ref")
        if (
            not normalized
            or not isinstance(version, str)
            or not version
            or component.get("type") != "library"
            or not isinstance(bom_ref, str)
            or not bom_ref
        ):
            raise ValueError("base CycloneDX component identity is invalid")
        if normalized in raw_runtime:
            raise ValueError(f"duplicate base CycloneDX component: {normalized}")
        expected_purl = _runtime_purl(normalized, version)
        purl = component.get("purl")
        if purl is not None and purl != expected_purl:
            raise ValueError("base CycloneDX component purl is inconsistent")
        if bom_ref in runtime_ref_map:
            raise ValueError("duplicate base CycloneDX bom-ref")
        runtime_ref_map[bom_ref] = expected_purl
        component["bom-ref"] = expected_purl
        component["purl"] = expected_purl
        raw_runtime[normalized] = version
    if raw_runtime != runtime_dependencies:
        raise ValueError("base CycloneDX components do not match runtime inventory")
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise ValueError("base CycloneDX dependency must be an object")
        ref = dependency.get("ref")
        if not isinstance(ref, str) or ref not in runtime_ref_map:
            raise ValueError("base CycloneDX dependency ref is invalid")
        dependency["ref"] = runtime_ref_map[ref]
        depends_on = dependency.get("dependsOn", [])
        if not isinstance(depends_on, list) or any(
            not isinstance(item, str) or item not in runtime_ref_map
            for item in depends_on
        ):
            raise ValueError("base CycloneDX dependsOn is invalid")
        if len(depends_on) != len(set(depends_on)):
            raise ValueError("base CycloneDX dependsOn contains duplicates")
        if "dependsOn" in dependency:
            dependency["dependsOn"] = [runtime_ref_map[item] for item in depends_on]
    for component in first_party:
        normalized = _normalize_distribution(str(component["name"]))
        if normalized in existing_names:
            raise ValueError(
                f"first-party component already exists in SBOM: {normalized}"
            )
        existing_names.add(normalized)
        components.append(component)
        dependencies.append({"ref": component["bom-ref"]})

    source = _source_metadata()
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("CycloneDX metadata must be an object")
    metadata["component"] = _release_metadata_component(source)
    components.sort(
        key=lambda component: (
            _normalize_distribution(str(component.get("name", ""))),
            str(component.get("version", "")),
        )
    )
    dependencies.sort(key=lambda dependency: str(dependency.get("ref", "")))
    _write_json(args.output, payload)
    print(
        "CycloneDX finalization: PASS "
        f"({len(components)} components, {len(first_party)} first-party wheels)"
    )
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _runtime_inventory_summary(
    inventory_path: Path,
    requirements_path: Path,
    expected_first_party: dict[str, str],
) -> tuple[dict[str, object], dict[str, str]]:
    payload = _load_json(inventory_path)
    errors: list[str] = []
    expected_versions = {
        _normalize_distribution(name): str(version)
        for name, version in expected_first_party.items()
    }
    expected = sorted(expected_versions)
    if payload.get("schema_version") != RUNTIME_INVENTORY_SCHEMA:
        errors.append("schema_mismatch")
    if payload.get("status") != "PASS":
        errors.append("declared_status_failed")
    if payload.get("first_party") != expected:
        errors.append("first_party_mismatch")

    packages = payload.get("packages")
    records: dict[str, dict[str, object]] = {}
    if not isinstance(packages, list):
        packages = []
        errors.append("packages_invalid")
    for item in packages:
        if not isinstance(item, dict):
            errors.append("package_record_invalid")
            continue
        name = item.get("name")
        normalized = item.get("normalized_name")
        version = item.get("version")
        scope = item.get("scope")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(normalized, str)
            or normalized != _normalize_distribution(name)
            or not isinstance(version, str)
            or not version.strip()
            or scope not in {"first-party", "runtime"}
            or "license_expression" not in item
            or "license" not in item
        ):
            errors.append("package_record_invalid")
            continue
        if normalized in records:
            errors.append("package_name_duplicate")
            continue
        records[normalized] = item

    first_party_records = {
        name: str(item["version"])
        for name, item in records.items()
        if item["scope"] == "first-party"
    }
    runtime_records = {
        name: str(item["version"])
        for name, item in records.items()
        if item["scope"] == "runtime"
    }
    if first_party_records != expected_versions:
        errors.append("first_party_records_mismatch")
    if payload.get("package_count") != len(records):
        errors.append("package_count_mismatch")
    if payload.get("runtime_dependency_count") != len(runtime_records):
        errors.append("runtime_dependency_count_mismatch")
    if not runtime_records:
        errors.append("runtime_dependencies_empty")

    requirements = [
        f"{records[name]['name']}=={records[name]['version']}"
        for name in sorted(runtime_records)
    ]
    expected_requirements = "\n".join(requirements) + "\n"
    actual_requirements = requirements_path.read_text(encoding="utf-8")
    if actual_requirements != expected_requirements:
        errors.append("requirements_content_mismatch")
    requirement_record = payload.get("requirements")
    expected_record = _file_record(
        requirements_path, base=requirements_path.parent.resolve()
    )
    if requirement_record != expected_record:
        errors.append("requirements_digest_or_size_mismatch")

    summary = {
        "schema_version": payload.get("schema_version"),
        "package_count": len(records),
        "runtime_dependency_count": len(runtime_records),
        "first_party": sorted(first_party_records),
        "first_party_components": [
            {"name": name, "version": first_party_records[name]}
            for name in sorted(first_party_records)
        ],
        "runtime_dependencies": [
            {"name": name, "version": runtime_records[name]}
            for name in sorted(runtime_records)
        ],
        "requirements": expected_record,
        "errors": sorted(set(errors)),
        "status": "PASS" if not errors else "FAIL",
    }
    return summary, runtime_records


def _audit_summary(
    path: Path,
    exit_code: int,
    tool_version: str | None,
    expected_tool_version: str,
    expected_dependencies: dict[str, str],
) -> dict[str, object]:
    payload = _load_json(path)
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("pip-audit JSON is missing dependencies")
    vulnerability_count = 0
    audited_dependencies: dict[str, str] = {}
    dependency_errors: list[str] = []
    if payload.get("fixes") != []:
        dependency_errors.append("unexpected_fixes")
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(
            dependency.get("vulns"), list
        ):
            raise ValueError("pip-audit dependency has an invalid vulns field")
        name = dependency.get("name")
        version = dependency.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            dependency_errors.append("invalid_dependency_record")
        else:
            normalized = _normalize_distribution(name)
            if normalized in audited_dependencies:
                dependency_errors.append("duplicate_dependency")
            audited_dependencies[normalized] = version
        vulnerability_count += len(dependency["vulns"])
    if audited_dependencies != expected_dependencies:
        dependency_errors.append("runtime_inventory_mismatch")
    passed = (
        exit_code == 0
        and bool(expected_dependencies)
        and not dependency_errors
        and vulnerability_count == 0
        and tool_version == expected_tool_version
    )
    return {
        "tool": "pip-audit",
        "tool_version": tool_version,
        "expected_tool_version": expected_tool_version,
        "exit_code": exit_code,
        "dependency_count": len(dependencies),
        "dependencies": [
            {"name": name, "version": audited_dependencies[name]}
            for name in sorted(audited_dependencies)
        ],
        "dependency_errors": sorted(set(dependency_errors)),
        "vulnerability_count": vulnerability_count,
        "status": "PASS" if passed else "FAIL",
    }


def _normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _runtime_purl(normalized_name: str, version: str) -> str:
    return f"pkg:pypi/{quote(normalized_name, safe='')}@" f"{quote(version, safe='')}"


def _distribution_exists(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _installed_tool_versions() -> dict[str, str | None]:
    return {
        name: importlib.metadata.version(name) if _distribution_exists(name) else None
        for name in ("build", "pip", "setuptools", "wheel", "pip-audit")
    }


def _sbom_component_hashes(component: dict[str, Any]) -> set[str]:
    hashes = component.get("hashes", [])
    if not isinstance(hashes, list):
        return set()
    return {
        str(item.get("content", "")).casefold()
        for item in hashes
        if isinstance(item, dict)
        and str(item.get("alg", "")).replace("-", "").casefold() == "sha256"
    }


def _sbom_summary(
    path: Path,
    exit_code: int,
    expected_components: Iterable[str],
    wheels: Iterable[Path],
    expected_runtime_dependencies: dict[str, str],
) -> dict[str, object]:
    payload = _load_json(path)
    if payload.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM is not CycloneDX JSON")
    spec_version = payload.get("specVersion")
    bom_version = payload.get("version")
    schema_errors: list[str] = []
    if spec_version != CYCLONEDX_SPEC_VERSION:
        schema_errors.append("unsupported_spec_version")
    if (
        not isinstance(bom_version, int)
        or isinstance(bom_version, bool)
        or bom_version <= 0
    ):
        schema_errors.append("invalid_bom_version")
    vulnerabilities = payload.get("vulnerabilities", [])
    if not isinstance(vulnerabilities, list) or vulnerabilities:
        schema_errors.append("vulnerabilities_present_or_invalid")
    components = payload.get("components", [])
    if not isinstance(components, list):
        raise ValueError("CycloneDX components must be a list")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        schema_errors.append("dependencies_invalid")
        dependencies = []
    components_by_name: dict[str, list[dict[str, Any]]] = {}
    component_refs: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            schema_errors.append("component_invalid")
            continue
        name = component.get("name")
        version = component.get("version")
        bom_ref = component.get("bom-ref")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(version, str)
            or not version.strip()
            or not isinstance(bom_ref, str)
            or not bom_ref.strip()
        ):
            schema_errors.append("component_invalid")
            continue
        if bom_ref in component_refs:
            schema_errors.append("component_bom_ref_duplicate")
        component_refs.add(bom_ref)
        normalized = _normalize_distribution(name)
        components_by_name.setdefault(normalized, []).append(component)
    dependency_refs: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            schema_errors.append("dependency_invalid")
            continue
        ref = dependency.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            schema_errors.append("dependency_invalid")
            continue
        dependency_refs.append(ref)
        if ref not in component_refs:
            schema_errors.append("dependency_ref_unknown")
        depends_on = dependency.get("dependsOn", [])
        if not isinstance(depends_on, list):
            schema_errors.append("depends_on_invalid")
            continue
        if len(depends_on) != len(set(depends_on)):
            schema_errors.append("depends_on_duplicate")
        if any(
            not isinstance(item, str) or item not in component_refs
            for item in depends_on
        ):
            schema_errors.append("depends_on_ref_unknown")
    if len(dependency_refs) != len(set(dependency_refs)):
        schema_errors.append("dependency_ref_duplicate")
    metadata = payload.get("metadata")
    expected_metadata_component = _release_metadata_component(_source_metadata())
    if (
        not isinstance(metadata, dict)
        or metadata.get("component") != expected_metadata_component
    ):
        schema_errors.append("metadata_component_mismatch")
    component_names = set(components_by_name)
    normalized_expected = {
        _normalize_distribution(name) for name in expected_components
    }
    missing_components = sorted(normalized_expected - component_names)
    expected_names = normalized_expected | set(expected_runtime_dependencies)
    unexpected_components = sorted(component_names - expected_names)
    wheel_hash_mismatches: list[str] = []
    first_party_component_errors: list[str] = []
    first_party_names: set[str] = set()
    for wheel in wheels:
        expected = _wheel_component(wheel)
        normalized = _normalize_distribution(str(expected["name"]))
        first_party_names.add(normalized)
        candidates = components_by_name.get(normalized, [])
        matching = [component for component in candidates if component == expected]
        if len(candidates) != 1:
            first_party_component_errors.append("first_party_component_count")
        if len(matching) != 1:
            wheel_hash_mismatches.append(wheel.name)
        if dependency_refs.count(str(expected["bom-ref"])) != 1:
            first_party_component_errors.append("first_party_dependency_ref")
    runtime_components: dict[str, str] = {}
    runtime_component_errors: list[str] = []
    for normalized, candidates in components_by_name.items():
        if normalized in first_party_names:
            continue
        if len(candidates) != 1:
            runtime_component_errors.append("duplicate_runtime_component")
            continue
        version = candidates[0].get("version")
        if not isinstance(version, str) or not version:
            runtime_component_errors.append("invalid_runtime_component")
            continue
        expected_purl = _runtime_purl(normalized, version)
        if (
            candidates[0].get("type") != "library"
            or candidates[0].get("bom-ref") != expected_purl
            or candidates[0].get("purl") != expected_purl
        ):
            runtime_component_errors.append("runtime_component_identity_mismatch")
        runtime_components[normalized] = version
    if runtime_components != expected_runtime_dependencies:
        runtime_component_errors.append("runtime_inventory_mismatch")
    passed = (
        bool(components)
        and not schema_errors
        and not missing_components
        and not unexpected_components
        and not wheel_hash_mismatches
        and not first_party_component_errors
        and not runtime_component_errors
        and path.stat().st_size > 0
        and exit_code == 0
    )
    return {
        "format": "CycloneDX",
        "spec_version": spec_version,
        "bom_version": bom_version,
        "component_count": len(components),
        "expected_components": sorted(normalized_expected),
        "missing_components": missing_components,
        "unexpected_components": unexpected_components,
        "schema_errors": sorted(set(schema_errors)),
        "first_party_component_errors": sorted(set(first_party_component_errors)),
        "wheel_hash_mismatches": sorted(wheel_hash_mismatches),
        "runtime_components": [
            {"name": name, "version": runtime_components[name]}
            for name in sorted(runtime_components)
        ],
        "runtime_component_errors": sorted(set(runtime_component_errors)),
        "generation_exit_code": exit_code,
        "status": "PASS" if passed else "FAIL",
    }


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable is empty: {name}")
    return value


def _source_metadata() -> dict[str, str]:
    repository = _required_env("GITHUB_REPOSITORY")
    run_id = _required_env("GITHUB_RUN_ID")
    server_url = _required_env("GITHUB_SERVER_URL").rstrip("/")
    source = {
        "repository": repository,
        "commit_sha": _required_env("GITHUB_SHA"),
        "ref": _required_env("GITHUB_REF"),
        "event_name": _required_env("GITHUB_EVENT_NAME"),
        "workflow": _required_env("GITHUB_WORKFLOW"),
        "job": _required_env("GITHUB_JOB"),
        "run_id": run_id,
        "run_attempt": _required_env("GITHUB_RUN_ATTEMPT"),
        "run_url": f"{server_url}/{repository}/actions/runs/{run_id}",
    }
    if re.fullmatch(r"[0-9a-f]{40}", source["commit_sha"]) is None:
        raise ValueError("GITHUB_SHA is not a lowercase 40-character commit SHA")
    if not source["run_id"].isdigit() or not source["run_attempt"].isdigit():
        raise ValueError("GitHub run ID and attempt must be decimal integers")
    return source


def _release_metadata_component(source: dict[str, str]) -> dict[str, str]:
    return {
        "bom-ref": f"urn:networkagent:commit:{source['commit_sha']}",
        "type": "application",
        "name": "NetworkAgent local release evidence",
        "version": source["commit_sha"],
    }


def _expected_wheels(wheel_root: Path, patterns: Iterable[str]) -> tuple[Path, ...]:
    wheels = _wheel_paths(wheel_root)
    matched: set[Path] = set()
    pattern_list = tuple(patterns)
    if len(pattern_list) != len(set(pattern_list)):
        raise ValueError("expected wheel patterns must be unique")
    for pattern in pattern_list:
        candidates = tuple(sorted(wheel_root.rglob(pattern)))
        if len(candidates) != 1:
            raise ValueError(
                f"expected exactly one wheel for {pattern!r}, found {len(candidates)}"
            )
        matched.add(candidates[0].resolve())
    actual = {wheel.resolve() for wheel in wheels}
    if matched != actual:
        unexpected = sorted(path.as_posix() for path in actual - matched)
        raise ValueError(f"unexpected wheels in release set: {unexpected}")
    return wheels


def _evidence_files(
    wheel_root: Path,
    evidence_root: Path,
    output: Path,
    expected_wheels: Iterable[str],
    supplemental_evidence: Iterable[Path] = (),
) -> list[dict[str, object]]:
    base = Path.cwd().resolve()
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ValueError("evidence root must be a regular directory")
    allowed = set(_EVIDENCE_FILENAMES)
    if output.parent.resolve() != evidence_root.resolve():
        raise ValueError("release manifest must be inside the evidence root")
    allowed.add(output.name)
    supplemental_paths: list[Path] = []
    reserved_names = {name.casefold() for name in allowed}
    supplemental_names: set[str] = set()
    for raw_path in supplemental_evidence:
        candidate = Path(raw_path)
        name = candidate.name
        if _SAFE_SUPPLEMENTAL_BASENAME.fullmatch(name) is None:
            raise ValueError(f"unsafe supplemental evidence basename: {name!r}")
        if candidate.resolve().parent != evidence_root.resolve():
            raise ValueError("supplemental evidence must be inside the evidence root")
        normalized = name.casefold()
        if normalized in reserved_names:
            raise ValueError(
                f"supplemental evidence conflicts with a reserved file: {name}"
            )
        if normalized in supplemental_names:
            raise ValueError(f"duplicate supplemental evidence file: {name}")
        supplemental_names.add(normalized)
        supplemental_paths.append(candidate)
        allowed.add(name)
    actual_entries = tuple(evidence_root.iterdir())
    unexpected = sorted(
        entry.name for entry in actual_entries if entry.name not in allowed
    )
    if unexpected:
        raise ValueError(f"unexpected release evidence files: {unexpected}")
    if any(entry.is_symlink() or not entry.is_file() for entry in actual_entries):
        raise ValueError("release evidence entries must be regular files")
    missing = sorted(
        name for name in _EVIDENCE_FILENAMES if not (evidence_root / name).is_file()
    )
    missing.extend(
        path.name
        for path in supplemental_paths
        if path.is_symlink() or not path.is_file()
    )
    if missing:
        raise ValueError(f"missing release evidence files: {missing}")
    paths: set[Path] = set(_expected_wheels(wheel_root, expected_wheels))
    paths.update(evidence_root / name for name in _EVIDENCE_FILENAMES)
    paths.update(supplemental_paths)
    return [_file_record(path, base=base) for path in sorted(paths)]


def _wheel_scan_summary(
    path: Path, wheels: Iterable[Path]
) -> tuple[dict[str, Any], list[str]]:
    payload = _load_json(path)
    if payload.get("schema_version") != WHEEL_SCAN_SCHEMA:
        raise ValueError("wheel scan schema is not supported")
    records = payload.get("wheels")
    if not isinstance(records, list):
        raise ValueError("wheel scan wheels must be a list")

    expected = [
        _scan_wheel(
            wheel,
            base=Path.cwd().resolve(),
            max_wheel_bytes=DEFAULT_MAX_WHEEL_BYTES,
            max_uncompressed_bytes=DEFAULT_MAX_UNCOMPRESSED_BYTES,
        )
        for wheel in sorted(wheels)
    ]
    actual: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("wheel scan record must be an object")
        actual.append(record)

    failures: list[str] = []
    if actual != expected:
        failures.append("wheel_scan_record_mismatch")
    if any(record.get("status") != "PASS" for record in expected):
        failures.append("wheel_scan_record_failed")
    if payload.get("limits") != {
        "max_wheel_bytes": DEFAULT_MAX_WHEEL_BYTES,
        "max_uncompressed_bytes": DEFAULT_MAX_UNCOMPRESSED_BYTES,
    }:
        failures.append("wheel_scan_limits_mismatch")
    if payload.get("errors") != []:
        failures.append("wheel_scan_errors_present")
    expected_status = (
        "PASS"
        if not failures and all(record.get("status") == "PASS" for record in expected)
        else "FAIL"
    )
    if payload.get("status") != expected_status:
        failures.append("wheel_scan_status_failed")
    return payload, failures


def _append_summary(
    path: Path,
    *,
    artifact_name: str,
    source: dict[str, str],
    python_version: str,
    wheels: Iterable[dict[str, object]],
    audit: dict[str, object],
    sbom: dict[str, object],
    wheel_scan: dict[str, Any],
    inventory: dict[str, object],
    overall_status: str,
    failures: Iterable[str],
    retention_days: int,
) -> None:
    failure_list = list(failures)
    lines = [
        f"## Release evidence — {source['workflow']} / Python {python_version}",
        "",
        f"- Release Gate: **{overall_status}**",
        "- Artifact classification: **"
        + ("PENDING VERIFY-MANIFEST" if overall_status == "PASS" else "DIAGNOSTIC ONLY")
        + "**",
        "- Failures: "
        + (", ".join(f"`{failure}`" for failure in failure_list) or "none"),
        f"- Commit: `{source['commit_sha']}`",
        f"- Artifact: `{artifact_name}`",
        f"- Artifact retention: {retention_days} days",
        f"- Run: {source['run_url']}",
        f"- Runtime inventory: **{inventory['status']}** "
        f"({inventory['runtime_dependency_count']} runtime dependencies)",
        f"- pip-audit: **{audit['status']}** "
        f"({audit['dependency_count']} dependencies, "
        f"{audit['vulnerability_count']} known vulnerabilities)",
        f"- CycloneDX SBOM: **{sbom['status']}** "
        f"({sbom['component_count']} components, spec {sbom['spec_version']})",
        f"- Wheel content scan: **{wheel_scan.get('status', 'FAIL')}**",
        "",
        "| Wheel | Bytes | SHA-256 |",
        "|---|---:|---|",
    ]
    for wheel in wheels:
        lines.append(f"| `{wheel['path']}` | {wheel['bytes']} | `{wheel['sha256']}` |")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def build_manifest(args: argparse.Namespace) -> int:
    wheel_root = args.wheel_root.resolve()
    evidence_root = args.evidence_root.resolve()
    output = args.output.resolve()
    failures: list[str] = []

    try:
        source = _source_metadata()
        wheel_paths = _expected_wheels(wheel_root, args.expected_wheel)
        raw_supplementals = getattr(args, "supplemental_evidence", ())
        supplemental_evidence = tuple(raw_supplementals or ())
        files = _evidence_files(
            wheel_root,
            evidence_root,
            output,
            args.expected_wheel,
            supplemental_evidence,
        )
    except (OSError, ValueError) as error:
        print(f"release evidence error: {error}", file=sys.stderr)
        return 2

    wheels = [item for item in files if str(item["path"]).endswith(".whl")]
    tool_versions = _installed_tool_versions()
    try:
        first_party_versions = _first_party_versions(wheel_paths)
        inventory, runtime_dependencies = _runtime_inventory_summary(
            evidence_root / "runtime-inventory.json",
            evidence_root / "runtime-requirements.txt",
            first_party_versions,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        inventory = {
            "schema_version": None,
            "package_count": 0,
            "runtime_dependency_count": 0,
            "first_party": [],
            "first_party_components": [],
            "runtime_dependencies": [],
            "requirements": None,
            "errors": [str(error)],
            "status": "FAIL",
        }
        runtime_dependencies = {}
    try:
        audit = _audit_summary(
            args.audit_report,
            args.audit_exit_code,
            tool_versions["pip-audit"],
            args.expected_pip_audit_version,
            runtime_dependencies,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        audit = {
            "tool": "pip-audit",
            "tool_version": tool_versions["pip-audit"],
            "expected_tool_version": args.expected_pip_audit_version,
            "exit_code": args.audit_exit_code,
            "dependency_count": 0,
            "dependencies": [],
            "dependency_errors": [str(error)],
            "vulnerability_count": 0,
            "status": "FAIL",
            "error": str(error),
        }
    try:
        sbom = _sbom_summary(
            args.sbom,
            args.sbom_exit_code,
            args.expected_component,
            wheel_paths,
            runtime_dependencies,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sbom = {
            "format": "CycloneDX",
            "spec_version": None,
            "component_count": 0,
            "expected_components": sorted(
                _normalize_distribution(name) for name in args.expected_component
            ),
            "missing_components": sorted(
                _normalize_distribution(name) for name in args.expected_component
            ),
            "wheel_hash_mismatches": sorted(wheel.name for wheel in wheel_paths),
            "runtime_components": [],
            "runtime_component_errors": [str(error)],
            "generation_exit_code": args.sbom_exit_code,
            "status": "FAIL",
            "error": str(error),
        }
    try:
        wheel_scan, wheel_scan_failures = _wheel_scan_summary(
            args.wheel_scan, wheel_paths
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        wheel_scan = {
            "status": "FAIL",
            "wheels": [],
            "error": str(error),
        }
        wheel_scan_failures = ["wheel_scan_invalid"]

    if inventory["status"] != "PASS":
        failures.append("runtime_inventory_failed")
    if audit["status"] != "PASS":
        failures.append("pip_audit_failed")
    if sbom["status"] != "PASS":
        failures.append("sbom_generation_failed")
    if wheel_scan_failures:
        failures.append("wheel_content_scan_failed")
    if any(version is None for version in tool_versions.values()):
        failures.append("build_tool_version_missing")
    if not platform.python_version().startswith(f"{args.python_version}."):
        failures.append("python_version_mismatch")

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at_utc": _utc_now(),
        "source": source,
        "build": {
            "python_version": platform.python_version(),
            "expected_python_version": args.python_version,
            "python_implementation": platform.python_implementation(),
            "runner_os": os.environ.get("RUNNER_OS", "unknown"),
            "runner_arch": os.environ.get("RUNNER_ARCH", "unknown"),
            "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
            "tools": tool_versions,
        },
        "artifact_name": args.artifact_name,
        "artifact_retention_days": args.artifact_retention_days,
        "expected_wheels": sorted(args.expected_wheel),
        "files": files,
        "wheel_count": len(wheels),
        "runtime_inventory": inventory,
        "sbom": sbom,
        "security": {
            "pip_audit": audit,
            "wheel_content_scan": {
                "status": wheel_scan.get("status", "FAIL"),
                "wheel_count": len(wheel_scan.get("wheels", [])),
            },
        },
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }
    if supplemental_evidence:
        manifest["supplemental_evidence"] = sorted(
            Path(path).name for path in supplemental_evidence
        )
    _write_json(output, manifest)
    _append_summary(
        args.summary,
        artifact_name=args.artifact_name,
        source=source,
        python_version=args.python_version,
        wheels=wheels,
        audit=audit,
        sbom=sbom,
        wheel_scan=wheel_scan,
        inventory=inventory,
        overall_status=str(manifest["status"]),
        failures=failures,
        retention_days=args.artifact_retention_days,
    )
    print(f"release evidence manifest: {manifest['status']} ({len(wheels)} wheel(s))")
    return 0 if not failures else 1


def verify_manifest(args: argparse.Namespace) -> int:
    errors: list[str] = []
    try:
        payload = _load_json(args.manifest)
        source = _source_metadata()
        wheel_root = args.wheel_root.resolve()
        evidence_root = args.evidence_root.resolve()
        wheels = _expected_wheels(wheel_root, args.expected_wheel)
        raw_supplementals = getattr(args, "supplemental_evidence", ())
        supplemental_evidence = tuple(raw_supplementals or ())
        files = _evidence_files(
            wheel_root,
            evidence_root,
            args.manifest.resolve(),
            args.expected_wheel,
            supplemental_evidence,
        )
        tool_versions = _installed_tool_versions()
        inventory, runtime_dependencies = _runtime_inventory_summary(
            evidence_root / "runtime-inventory.json",
            evidence_root / "runtime-requirements.txt",
            _first_party_versions(wheels),
        )

        if payload.get("schema_version") != MANIFEST_SCHEMA:
            errors.append("manifest_schema_mismatch")
        if not _valid_generated_at_utc(payload.get("generated_at_utc")):
            errors.append("manifest_generated_at_invalid")
        if payload.get("source") != source:
            errors.append("manifest_source_mismatch")
        if payload.get("artifact_name") != args.artifact_name:
            errors.append("manifest_artifact_name_mismatch")
        if payload.get("artifact_retention_days") != args.artifact_retention_days:
            errors.append("manifest_retention_mismatch")
        if payload.get("expected_wheels") != sorted(args.expected_wheel):
            errors.append("manifest_expected_wheels_mismatch")
        if payload.get("files") != files:
            errors.append("manifest_file_digest_or_size_mismatch")
        expected_supplemental = sorted(
            Path(path).name for path in supplemental_evidence
        )
        if expected_supplemental:
            if payload.get("supplemental_evidence") != expected_supplemental:
                errors.append("manifest_supplemental_evidence_mismatch")
        elif "supplemental_evidence" in payload:
            errors.append("manifest_supplemental_evidence_unexpected")
        if payload.get("wheel_count") != len(wheels):
            errors.append("manifest_wheel_count_mismatch")
        if payload.get("runtime_inventory") != inventory:
            errors.append("manifest_runtime_inventory_mismatch")
        if inventory["status"] != "PASS":
            errors.append("runtime_inventory_failed")
        if payload.get("status") != "PASS" or payload.get("failures") != []:
            errors.append("manifest_status_failed")

        build = payload.get("build")
        if not isinstance(build, dict):
            errors.append("manifest_build_invalid")
        else:
            if build.get("expected_python_version") != args.python_version:
                errors.append("manifest_expected_python_mismatch")
            if build.get("python_version") != platform.python_version():
                errors.append("manifest_python_mismatch")
            if build.get("python_implementation") != platform.python_implementation():
                errors.append("manifest_python_implementation_mismatch")
            if build.get("runner_os") != os.environ.get("RUNNER_OS", "unknown"):
                errors.append("manifest_runner_os_mismatch")
            if build.get("runner_arch") != os.environ.get("RUNNER_ARCH", "unknown"):
                errors.append("manifest_runner_arch_mismatch")
            if build.get("tools") != tool_versions:
                errors.append("manifest_tool_versions_mismatch")
            if build.get("source_date_epoch") != os.environ.get("SOURCE_DATE_EPOCH"):
                errors.append("manifest_source_date_epoch_mismatch")

        security = payload.get("security")
        sbom_record = payload.get("sbom")
        if not isinstance(security, dict) or not isinstance(sbom_record, dict):
            errors.append("manifest_security_invalid")
        else:
            audit_record = security.get("pip_audit")
            if not isinstance(audit_record, dict):
                errors.append("manifest_audit_invalid")
            else:
                expected_audit = _audit_summary(
                    evidence_root / "pip-audit.json",
                    args.audit_exit_code,
                    tool_versions["pip-audit"],
                    args.expected_pip_audit_version,
                    runtime_dependencies,
                )
                if audit_record != expected_audit:
                    errors.append("manifest_audit_mismatch")
                if expected_audit.get("status") != "PASS":
                    errors.append("pip_audit_failed")

            expected_sbom = _sbom_summary(
                evidence_root / "sbom.cdx.json",
                args.sbom_exit_code,
                args.expected_component,
                wheels,
                runtime_dependencies,
            )
            if sbom_record != expected_sbom:
                errors.append("manifest_sbom_mismatch")
            if expected_sbom.get("status") != "PASS":
                errors.append("sbom_failed")

            wheel_scan, wheel_scan_errors = _wheel_scan_summary(
                evidence_root / "wheel-content-scan.json", wheels
            )
            expected_scan_record = {
                "status": wheel_scan.get("status", "FAIL"),
                "wheel_count": len(wheel_scan.get("wheels", [])),
            }
            if security.get("wheel_content_scan") != expected_scan_record:
                errors.append("manifest_wheel_scan_mismatch")
            if expected_scan_record["status"] != "PASS":
                errors.append("wheel_content_scan_failed")
            errors.extend(wheel_scan_errors)
            if args.audit_exit_code != 0:
                errors.append("pip_audit_command_failed")
            if args.sbom_exit_code != 0:
                errors.append("sbom_command_failed")

    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"manifest_verification_error:{error}")

    if errors:
        for error in sorted(set(errors)):
            print(f"release evidence verification error: {error}", file=sys.stderr)
        return 1
    print("release evidence manifest verification: PASS")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan-wheels", help="scan wheel archive contents")
    scan.add_argument("--wheel-root", type=Path, default=Path("dist"))
    scan.add_argument("--output", type=Path, required=True)
    scan.add_argument("--max-wheel-bytes", type=int, default=DEFAULT_MAX_WHEEL_BYTES)
    scan.add_argument(
        "--max-uncompressed-bytes",
        type=int,
        default=DEFAULT_MAX_UNCOMPRESSED_BYTES,
    )
    scan.set_defaults(func=scan_wheels)

    inventory = subparsers.add_parser(
        "inventory", help="write a pinned third-party runtime inventory"
    )
    inventory.add_argument("--environment-path", type=Path, required=True)
    inventory.add_argument("--first-party", action="append", required=True)
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument("--requirements-output", type=Path, required=True)
    inventory.set_defaults(func=build_runtime_inventory)

    sbom = subparsers.add_parser(
        "finalize-sbom", help="add first-party wheel components to CycloneDX"
    )
    sbom.add_argument("--input", type=Path, required=True)
    sbom.add_argument("--output", type=Path, required=True)
    sbom.add_argument("--wheel-root", type=Path, default=Path("dist"))
    sbom.add_argument("--expected-wheel", action="append", required=True)
    sbom.add_argument("--runtime-inventory", type=Path, required=True)
    sbom.add_argument("--runtime-requirements", type=Path, required=True)
    sbom.set_defaults(func=finalize_sbom)

    manifest = subparsers.add_parser(
        "manifest", help="write the release manifest and GitHub step summary"
    )
    manifest.add_argument("--wheel-root", type=Path, default=Path("dist"))
    manifest.add_argument("--evidence-root", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--audit-report", type=Path, required=True)
    manifest.add_argument("--audit-exit-code", type=int, required=True)
    manifest.add_argument("--expected-pip-audit-version", required=True)
    manifest.add_argument("--sbom", type=Path, required=True)
    manifest.add_argument("--sbom-exit-code", type=int, required=True)
    manifest.add_argument("--wheel-scan", type=Path, required=True)
    manifest.add_argument("--expected-wheel", action="append", required=True)
    manifest.add_argument("--expected-component", action="append", required=True)
    manifest.add_argument("--python-version", required=True)
    manifest.add_argument("--artifact-name", required=True)
    manifest.add_argument("--artifact-retention-days", type=int, required=True)
    manifest.add_argument(
        "--supplemental-evidence", type=Path, action="append", default=[]
    )
    manifest.add_argument("--summary", type=Path, required=True)
    manifest.set_defaults(func=build_manifest)

    verify = subparsers.add_parser(
        "verify-manifest", help="verify manifest fields and file digests"
    )
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--wheel-root", type=Path, default=Path("dist"))
    verify.add_argument("--evidence-root", type=Path, required=True)
    verify.add_argument("--expected-wheel", action="append", required=True)
    verify.add_argument("--expected-component", action="append", required=True)
    verify.add_argument("--audit-exit-code", type=int, required=True)
    verify.add_argument("--sbom-exit-code", type=int, required=True)
    verify.add_argument("--expected-pip-audit-version", required=True)
    verify.add_argument("--python-version", required=True)
    verify.add_argument("--artifact-name", required=True)
    verify.add_argument("--artifact-retention-days", type=int, required=True)
    verify.add_argument(
        "--supplemental-evidence", type=Path, action="append", default=[]
    )
    verify.set_defaults(func=verify_manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "scan-wheels":
        if args.max_wheel_bytes <= 0:
            raise SystemExit("--max-wheel-bytes must be positive")
        if args.max_uncompressed_bytes <= 0:
            raise SystemExit("--max-uncompressed-bytes must be positive")
    if args.command in {"manifest", "verify-manifest"} and (
        args.artifact_retention_days <= 0
    ):
        raise SystemExit("--artifact-retention-days must be positive")
    try:
        return int(args.func(args))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"release evidence error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
