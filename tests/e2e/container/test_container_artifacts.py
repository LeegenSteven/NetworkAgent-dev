from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = REPOSITORY_ROOT / "tools" / "local-container" / "compose_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("container_compose_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_image_policy_and_input_manifest_match_the_repository() -> None:
    guard = _load_guard()
    guard.validate_repository_artifacts(REPOSITORY_ROOT)


def test_compose_source_has_no_host_publish_or_network_definition() -> None:
    source = (REPOSITORY_ROOT / "deploy" / "local" / "compose.yaml").read_text(
        encoding="utf-8"
    )
    forbidden_lines = {
        "ports:",
        "expose:",
        "networks:",
        "privileged:",
        "env_file:",
        "environment:",
        "devices:",
    }
    rendered = {line.strip() for line in source.splitlines()}
    assert forbidden_lines.isdisjoint(rendered)
    assert source.count("network_mode: none") == 3
    assert source.count("network_mode: service:assurance") == 2
