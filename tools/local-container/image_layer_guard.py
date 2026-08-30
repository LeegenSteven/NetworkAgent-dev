#!/usr/bin/env python3
"""Inspect application layers from a Docker-save archive for build leakage."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Sequence


MAX_MANIFEST_BYTES = 1_048_576
MAX_APPLICATION_LAYERS = 16
MAX_LAYER_MEMBERS = 250_000
FIRST_PARTY_TESTS = re.compile(
    r"(?:^|/)site-packages/(?:telco_domain|telco_local|telco_lab|"
    r"telco_assurance_agent)/(?:test|tests)(?:/|$)",
    re.IGNORECASE,
)
RAW_INPUT_NAMES = {
    "performance.csv",
    "safe-cell-traces.csv",
    "5g-sa-bubbleran-persistent-interference-ul-bler.json",
    "erab-security-setup.json",
    "retainability-uplink-rssi.json",
    "telco-lte-fields-guide.zh-CN.md",
}
TRANSIENT_LOCK_NAMES = {
    "runtime-constraints.txt",
    "build-requirements-py312-linux-amd64.lock",
    "runtime-requirements-py312-linux-amd64.lock",
}
EMBEDDED_SBOM_SUFFIXES = (
    ".spdx",
    ".spdx.json",
    ".cdx",
    ".cdx.json",
    ".cyclonedx",
    ".cyclonedx.json",
    ".bom.json",
)


class LayerPolicyViolation(RuntimeError):
    """A final-image application layer contains a forbidden build artifact."""


def _normalized_member(name: str) -> str:
    value = name.replace("\\", "/")
    if value.startswith("/"):
        raise LayerPolicyViolation("layer contains an unsafe member path")
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise LayerPolicyViolation("layer contains an unsafe member path")
    return path.as_posix()


def _reject_member(name: str) -> None:
    normalized = _normalized_member(name)
    path = PurePosixPath(normalized)
    lowered = normalized.lower()
    if path.name in RAW_INPUT_NAMES:
        raise LayerPolicyViolation(f"application layer contains raw input {path.name}")
    if path.suffix.lower() in {".whl", ".pyc", ".pyo"}:
        raise LayerPolicyViolation(
            f"application layer contains build cache {path.name}"
        )
    lowered_parts = {part.lower() for part in path.parts}
    if "__pycache__" in lowered_parts:
        raise LayerPolicyViolation("application layer contains Python bytecode cache")
    if lowered_parts.intersection({"build", "wheels", ".cache"}):
        raise LayerPolicyViolation("application layer contains a build/cache directory")
    if lowered in {
        "build",
        "wheels",
        "tmp/wheels",
        "root/.cache",
    } or lowered.startswith(("build/", "wheels/", "tmp/wheels/", "root/.cache/")):
        raise LayerPolicyViolation("application layer contains a build/cache directory")
    if path.name in TRANSIENT_LOCK_NAMES:
        raise LayerPolicyViolation(
            "application layer contains the transient constraints file"
        )
    if FIRST_PARTY_TESTS.search(normalized):
        raise LayerPolicyViolation("application layer contains first-party tests")
    if lowered.endswith(EMBEDDED_SBOM_SUFFIXES):
        parts = tuple(part.lower() for part in path.parts)
        pep770 = any(
            part.endswith(".dist-info")
            and index + 1 < len(parts)
            and parts[index + 1] == "sboms"
            for index, part in enumerate(parts)
        )
        if not pep770:
            raise LayerPolicyViolation(
                "application layer contains an untrusted embedded SBOM"
            )


def inspect_archive(archive_path: Path, *, base_layer_count: int) -> dict[str, int]:
    if base_layer_count < 1:
        raise LayerPolicyViolation("base layer count is invalid")
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            manifest_member = archive.getmember("manifest.json")
            if manifest_member.size > MAX_MANIFEST_BYTES:
                raise LayerPolicyViolation("Docker manifest exceeds byte limit")
            manifest_handle = archive.extractfile(manifest_member)
            if manifest_handle is None:
                raise LayerPolicyViolation("Docker manifest is unavailable")
            manifest = json.loads(manifest_handle.read().decode("utf-8"))
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise LayerPolicyViolation(
                    "Docker archive must contain exactly one image"
                )
            entry = manifest[0]
            if not isinstance(entry, dict) or not isinstance(entry.get("Layers"), list):
                raise LayerPolicyViolation("Docker manifest layer list is invalid")
            layers = entry["Layers"]
            if not base_layer_count < len(layers):
                raise LayerPolicyViolation("application layer boundary is invalid")
            application_layers = layers[base_layer_count:]
            if len(application_layers) > MAX_APPLICATION_LAYERS:
                raise LayerPolicyViolation("application layer count exceeds limit")
            members_seen = 0
            for layer_name in application_layers:
                if not isinstance(layer_name, str):
                    raise LayerPolicyViolation("Docker layer name is invalid")
                layer_member = archive.getmember(layer_name)
                layer_handle = archive.extractfile(layer_member)
                if layer_handle is None:
                    raise LayerPolicyViolation("Docker layer is unavailable")
                with tarfile.open(fileobj=layer_handle, mode="r|") as layer:
                    for member in layer:
                        members_seen += 1
                        if members_seen > MAX_LAYER_MEMBERS:
                            raise LayerPolicyViolation(
                                "application layer entries exceed limit"
                            )
                        _reject_member(member.name)
    except LayerPolicyViolation:
        raise
    except (OSError, tarfile.TarError, KeyError, UnicodeError, json.JSONDecodeError):
        raise LayerPolicyViolation("Docker archive is invalid") from None
    return {"application_layers": len(application_layers), "members": members_seen}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="networkagent-image-layer-guard")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--base-layer-count", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = inspect_archive(
        arguments.archive, base_layer_count=arguments.base_layer_count
    )
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


def _run() -> int:
    try:
        return main()
    except LayerPolicyViolation as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "LAYER_POLICY", "message": str(exc)}},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_run())
