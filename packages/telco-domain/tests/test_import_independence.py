"""The shared package must stay independent from either agent runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src"
FORBIDDEN_PREFIXES = (
    "a2a",
    "duckdb",
    "fastmcp",
    "google.adk",
    "google.cloud",
    "langgraph",
)


def test_public_package_import_does_not_load_runtime_frameworks() -> None:
    script = """
import json
import sys
import telco_domain

prefixes = %r
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes)
)
print(json.dumps(loaded))
""" % (FORBIDDEN_PREFIXES,)
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SOURCE_ROOT), existing_path) if part
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert json.loads(result.stdout) == []


def test_public_api_exports_the_primary_workflow_types() -> None:
    import telco_domain

    required = {
        "Incident",
        "IncidentRepository",
        "IncidentTrigger",
        "InMemoryIncidentRepository",
        "NetworkChangeRequest",
        "transition_incident",
    }
    assert required.issubset(set(telco_domain.__all__))
