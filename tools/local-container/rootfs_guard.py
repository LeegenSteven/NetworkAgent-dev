#!/usr/bin/env python3
"""Inspect an exported final container filesystem without executing the image."""

from __future__ import annotations

import argparse
import json
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Sequence


MAX_ROOTFS_ARCHIVE_BYTES = 1_073_741_824
MAX_ROOTFS_MEMBERS = 500_000
FORBIDDEN_ROOTFS_PATHS = {
    "build",
    "wheels",
    "tmp/wheels",
    "root/.cache",
}
DATA_ROOT = "opt/networkagent/data"
ALLOWED_EMPTY_DATA_DIRECTORIES = {
    DATA_ROOT,
    f"{DATA_ROOT}/samples",
    f"{DATA_ROOT}/samples/lte-demo",
    f"{DATA_ROOT}/rca-rules",
    f"{DATA_ROOT}/rca-rules/lte",
    f"{DATA_ROOT}/docs",
    f"{DATA_ROOT}/docs/lte",
}
RAW_INPUT_NAMES = {
    "performance.csv",
    "safe-cell-traces.csv",
    "5g-sa-bubbleran-persistent-interference-ul-bler.json",
    "erab-security-setup.json",
    "retainability-uplink-rssi.json",
    "telco-lte-fields-guide.zh-CN.md",
}


class RootfsPolicyViolation(RuntimeError):
    """The final merged image filesystem violates the local runtime policy."""


def _normalized_member(name: str) -> PurePosixPath:
    value = name.replace("\\", "/")
    if value.startswith("/"):
        raise RootfsPolicyViolation("rootfs archive contains an unsafe member path")
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise RootfsPolicyViolation("rootfs archive contains an unsafe member path")
    return path


def _reject_member(member: tarfile.TarInfo) -> None:
    path = _normalized_member(member.name)
    normalized = path.as_posix().lower()
    if normalized == DATA_ROOT or normalized.startswith(DATA_ROOT + "/"):
        if normalized not in ALLOWED_EMPTY_DATA_DIRECTORIES or not member.isdir():
            raise RootfsPolicyViolation(
                "rootfs archive contains data outside the empty mount skeleton"
            )
        return
    for forbidden in FORBIDDEN_ROOTFS_PATHS:
        if normalized == forbidden or normalized.startswith(forbidden + "/"):
            raise RootfsPolicyViolation(
                f"rootfs archive contains forbidden path {forbidden}"
            )
    if path.name in RAW_INPUT_NAMES:
        raise RootfsPolicyViolation(f"rootfs archive contains raw input {path.name}")
    # Base Python legitimately contains ensurepip's bundled wheel. Application
    # layers are scanned separately and reject every newly introduced wheel.
    if path.name == "runtime-constraints.txt":
        raise RootfsPolicyViolation(
            f"rootfs archive contains transient build artifact {path.name}"
        )


def inspect_rootfs_archive(archive_path: Path) -> dict[str, int]:
    try:
        metadata = archive_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or archive_path.is_symlink()
            or not 0 < metadata.st_size <= MAX_ROOTFS_ARCHIVE_BYTES
        ):
            raise RootfsPolicyViolation("rootfs archive file is unsafe")
        members = 0
        with tarfile.open(archive_path, mode="r|") as archive:
            for member in archive:
                members += 1
                if members > MAX_ROOTFS_MEMBERS:
                    raise RootfsPolicyViolation(
                        "rootfs archive member count exceeds limit"
                    )
                _reject_member(member)
    except RootfsPolicyViolation:
        raise
    except (OSError, tarfile.TarError, UnicodeError):
        raise RootfsPolicyViolation("rootfs archive is invalid") from None
    return {"members": members}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="networkagent-rootfs-guard")
    parser.add_argument("--archive", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = inspect_rootfs_archive(arguments.archive)
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


def _run() -> int:
    try:
        return main()
    except RootfsPolicyViolation as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "ROOTFS_POLICY", "message": str(exc)}},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_run())
