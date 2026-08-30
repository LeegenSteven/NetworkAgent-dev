from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "rootfs_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rootfs_guard", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(path: Path, *names: str) -> None:
    with tarfile.open(path, mode="w") as archive:
        for name in names:
            if name.endswith("/"):
                member = tarfile.TarInfo(name.rstrip("/"))
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
                continue
            content = b"fixture\n"
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def test_safe_merged_rootfs_is_accepted(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "rootfs.tar"
    _archive(
        archive,
        "usr/local/bin/python",
        "usr/local/lib/python3.12/ensurepip/_bundled/pip-25.0.1-py3-none-any.whl",
        "opt/networkagent/bin/container_entrypoint.py",
        "usr/local/lib/python3.12/site-packages/telco_local/__init__.py",
        "opt/networkagent/data/",
        "opt/networkagent/data/samples/",
        "opt/networkagent/data/samples/lte-demo/",
        "opt/networkagent/data/rca-rules/",
        "opt/networkagent/data/rca-rules/lte/",
        "opt/networkagent/data/docs/",
        "opt/networkagent/data/docs/lte/",
    )
    assert module.inspect_rootfs_archive(archive) == {"members": 11}


@pytest.mark.parametrize(
    "name",
    [
        "root/.cache/pip/http/item",
        "build/telco_local/package.py",
        "wheels/telco_local.zip",
        "tmp/wheels/telco_domain.zip",
        "opt/networkagent/data/performance.csv",
        "opt/networkagent/data/unexpected/",
        "runtime-constraints.txt",
    ],
)
def test_forbidden_runtime_content_is_rejected(tmp_path: Path, name: str) -> None:
    module = _load_module()
    archive = tmp_path / "rootfs.tar"
    _archive(archive, name)
    with pytest.raises(module.RootfsPolicyViolation):
        module.inspect_rootfs_archive(archive)


def test_unsafe_archive_member_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "rootfs.tar"
    _archive(archive, "../escape")
    with pytest.raises(module.RootfsPolicyViolation, match="unsafe member"):
        module.inspect_rootfs_archive(archive)


def test_data_mount_skeleton_must_contain_real_directories(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "rootfs.tar"
    with tarfile.open(archive, mode="w") as output:
        member = tarfile.TarInfo("opt/networkagent/data/samples/lte-demo")
        member.type = tarfile.SYMTYPE
        member.linkname = "/tmp"
        output.addfile(member)
    with pytest.raises(module.RootfsPolicyViolation, match="empty mount skeleton"):
        module.inspect_rootfs_archive(archive)


def test_archive_byte_limit_is_enforced(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "rootfs.tar"
    archive.write_bytes(b"x")
    with archive.open("r+b") as handle:
        handle.truncate(module.MAX_ROOTFS_ARCHIVE_BYTES + 1)
    with pytest.raises(module.RootfsPolicyViolation, match="file is unsafe"):
        module.inspect_rootfs_archive(archive)


def test_archive_member_limit_is_enforced(tmp_path: Path) -> None:
    module = _load_module()
    module.MAX_ROOTFS_MEMBERS = 1
    archive = tmp_path / "rootfs.tar"
    _archive(archive, "usr/bin/python", "usr/bin/pip")
    with pytest.raises(module.RootfsPolicyViolation, match="member count"):
        module.inspect_rootfs_archive(archive)
