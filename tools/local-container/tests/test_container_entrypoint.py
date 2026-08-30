from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "container_entrypoint.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("container_entrypoint", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_for(root: Path, files: dict[str, bytes]) -> Path:
    entries = []
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        entries.append(
            {
                "source": relative,
                "container_path": str(target),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "algorithm": "sha256",
        "max_files": 8,
        "max_total_bytes": 4096,
        "files": entries,
        "directory_roots": [str(root / "rules")],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_bounded_manifest_validation_accepts_exact_inputs(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(
        tmp_path,
        {
            "performance.csv": b"time,kpi\n0,1\n",
            "rules/rule.json": b'{"rule":"safe"}\n',
        },
    )
    result = module.validate_inputs(manifest)
    assert result == {"files": 2, "bytes": 29}


def test_manifest_rejects_modified_input(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"performance.csv": b"safe\n"})
    (tmp_path / "performance.csv").write_bytes(b"tampered\n")
    with pytest.raises(module.InputValidationError, match="size mismatch"):
        module.validate_inputs(manifest)


def test_manifest_rejects_extra_file_below_controlled_directory(
    tmp_path: Path,
) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"rules/rule.json": b"{}\n"})
    (tmp_path / "rules" / "unreviewed.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(module.InputValidationError, match="directory contents"):
        module.validate_inputs(manifest)


def test_manifest_rejects_excess_file_count(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"performance.csv": b"safe\n"})
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["max_files"] = 0
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.InputValidationError, match="file count"):
        module.validate_inputs(manifest)


def test_manifest_rejects_symlinked_file_when_supported(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"performance.csv": b"safe\n"})
    source = tmp_path / "real.csv"
    source.write_bytes(b"safe\n")
    target = tmp_path / "performance.csv"
    target.unlink()
    try:
        target.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(module.InputValidationError, match="regular file"):
        module.validate_inputs(manifest)


def test_reset_executes_without_input_manifest_validation(monkeypatch) -> None:
    module = _load_module()
    calls: list[tuple[str, list[str]]] = []

    def fail_validation(_path: Path):
        raise AssertionError("reset must not depend on mounted input data")

    def record_exec(executable: str, argv: list[str]) -> None:
        calls.append((executable, argv))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(module, "validate_inputs", fail_validation)
    monkeypatch.setattr(os, "execv", record_exec)
    with pytest.raises(RuntimeError, match="intercepted"):
        module.main(["reset"])
    assert calls
    assert calls[0][1][-2:] == ["reset", "--yes"]


def test_unknown_command_fails_without_exec() -> None:
    module = _load_module()
    with pytest.raises(SystemExit) as error:
        module.main(["shell"])
    assert error.value.code == 2
