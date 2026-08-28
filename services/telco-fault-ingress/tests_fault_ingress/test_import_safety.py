from __future__ import annotations

import os
import subprocess
import sys


def test_import_does_not_load_spanner_or_read_credentials() -> None:
    environment = dict(os.environ)
    environment.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    command = (
        "import sys; import telco_fault_ingress; "
        "assert 'telco_cloud' not in sys.modules; "
        "assert 'google.cloud.spanner' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr


def test_help_exits_without_configuration_or_credentials() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "FAULT_ALLOWED_SUBSCRIPTIONS",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_PROJECT",
            "GOOGLE_SPANNER_INSTANCE",
            "GOOGLE_SPANNER_DATABASE",
            "TELCO_SPANNER_DATABASE_ROLE",
        }
    }
    completed = subprocess.run(
        [sys.executable, "-m", "telco_fault_ingress", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage: telco-fault-ingress" in completed.stdout
