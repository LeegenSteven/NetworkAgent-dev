from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "image_layer_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("image_layer_guard", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _layer(*names: str) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name in names:
            content = b"fixture\n"
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def _docker_archive(path: Path, *, base: bytes, application: bytes) -> None:
    manifest = json.dumps(
        [
            {
                "Config": "config.json",
                "RepoTags": ["test:dev"],
                "Layers": ["base/layer.tar", "app/layer.tar"],
            }
        ]
    ).encode("utf-8")
    with tarfile.open(path, mode="w") as archive:
        for name, content in (
            ("manifest.json", manifest),
            ("base/layer.tar", base),
            ("app/layer.tar", application),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def test_only_application_layers_are_inspected(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "image.tar"
    _docker_archive(
        archive,
        base=_layer("usr/lib/python/__pycache__/base.pyc"),
        application=_layer("opt/networkagent/bin/container_entrypoint.py"),
    )
    assert module.inspect_archive(archive, base_layer_count=1) == {
        "application_layers": 1,
        "members": 1,
    }


def test_pep770_package_sbom_is_allowed(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "image.tar"
    _docker_archive(
        archive,
        base=_layer("usr/lib/python/base.py"),
        application=_layer(
            "usr/local/lib/python3.12/site-packages/pydantic_core-2.46.4.dist-info/"
            "sboms/pydantic-core.cyclonedx.json"
        ),
    )
    assert module.inspect_archive(archive, base_layer_count=1) == {
        "application_layers": 1,
        "members": 1,
    }


@pytest.mark.parametrize(
    "name",
    [
        "tmp/wheels/telco_domain.whl",
        "opt/networkagent/data/performance.csv",
        "usr/local/lib/python3.12/site-packages/telco_local/tests/test_db.py",
        "usr/local/lib/python3.12/site-packages/telco_lab/__pycache__/cli.pyc",
        "runtime-constraints.txt",
        "build-requirements-py312-linux-amd64.lock",
        "runtime-requirements-py312-linux-amd64.lock",
        "usr/local/build/unreviewed.txt",
        "var/tmp/wheels/unreviewed.zip",
        "usr/local/lib/python3.12/site-packages/telco_local/Tests/test_db.py",
        "usr/local/lib/python3.12/site-packages/pip/_vendor/bom.cdx.json",
    ],
)
def test_application_layer_leakage_is_rejected(tmp_path: Path, name: str) -> None:
    module = _load_module()
    archive = tmp_path / "image.tar"
    _docker_archive(
        archive,
        base=_layer("usr/lib/python/base.py"),
        application=_layer(name),
    )
    with pytest.raises(module.LayerPolicyViolation):
        module.inspect_archive(archive, base_layer_count=1)
