from __future__ import annotations

from types import SimpleNamespace

import pytest
import telco_cloud
from google.cloud import spanner

from telco_fault_ingress import __main__ as entrypoint


def test_spanner_database_role_is_forwarded_without_credentials(monkeypatch) -> None:
    calls = {}
    database = object()
    profile = SimpleNamespace(
        project_id="project-demo",
        instance_id="instance-demo",
        database_id="database-demo",
        database_role="telco_fault_writer",
        emulator_host=None,
    )

    class Instance:
        def database(self, database_id, *, database_role):
            calls["database"] = (database_id, database_role)
            return database

    class Client:
        def __init__(self, *, project):
            calls["project"] = project

        def instance(self, instance_id):
            calls["instance"] = instance_id
            return Instance()

    monkeypatch.delenv("SPANNER_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(spanner, "Client", Client)
    monkeypatch.setattr(
        telco_cloud.CloudProfileConfig,
        "from_env",
        classmethod(lambda cls: profile),
    )

    repository = entrypoint._build_repository()

    assert repository._database is database
    assert calls == {
        "project": "project-demo",
        "instance": "instance-demo",
        "database": ("database-demo", "telco_fault_writer"),
    }


def test_real_profile_fails_before_client_when_production_role_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOOGLE_PROJECT", "network-agent-test")
    monkeypatch.setenv("GOOGLE_SPANNER_INSTANCE", "network-agent-instance")
    monkeypatch.setenv("GOOGLE_SPANNER_DATABASE", "network-agent-db")
    monkeypatch.delenv("TELCO_SPANNER_DATABASE_ROLE", raising=False)
    monkeypatch.delenv("SPANNER_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(
        spanner,
        "Client",
        lambda **kwargs: pytest.fail("client must not be constructed"),
    )

    with pytest.raises(ValueError, match="TELCO_SPANNER_DATABASE_ROLE"):
        entrypoint._build_repository()


def test_real_emulator_profile_forwards_none_database_role(monkeypatch) -> None:
    calls = []

    class Instance:
        def database(self, database_id, *, database_role):
            calls.append((database_id, database_role))
            return object()

    class Client:
        def __init__(self, *, project):
            pass

        def instance(self, instance_id):
            return Instance()

    monkeypatch.setenv("GOOGLE_PROJECT", "network-agent-test")
    monkeypatch.setenv("GOOGLE_SPANNER_INSTANCE", "network-agent-instance")
    monkeypatch.setenv("GOOGLE_SPANNER_DATABASE", "network-agent-db")
    monkeypatch.setenv("SPANNER_EMULATOR_HOST", "127.0.0.1:9010")
    monkeypatch.delenv("TELCO_SPANNER_DATABASE_ROLE", raising=False)
    monkeypatch.setattr(spanner, "Client", Client)

    entrypoint._build_repository()

    assert calls == [("network-agent-db", None)]


def test_production_rejects_any_role_other_than_fault_writer(monkeypatch) -> None:
    profile = SimpleNamespace(
        project_id="project-demo",
        instance_id="instance-demo",
        database_id="database-demo",
        database_role="overprivileged_role",
        emulator_host=None,
    )
    monkeypatch.delenv("SPANNER_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(
        telco_cloud.CloudProfileConfig,
        "from_env",
        classmethod(lambda cls: profile),
    )
    monkeypatch.setattr(
        spanner,
        "Client",
        lambda **kwargs: pytest.fail("client must not be constructed"),
    )

    with pytest.raises(RuntimeError, match="database role mismatch"):
        entrypoint._build_repository()


def test_emulator_rejects_arbitrary_database_role(monkeypatch) -> None:
    profile = SimpleNamespace(
        project_id="project-demo",
        instance_id="instance-demo",
        database_id="database-demo",
        database_role="overprivileged_role",
        emulator_host="127.0.0.1:9010",
    )
    monkeypatch.setenv("SPANNER_EMULATOR_HOST", "127.0.0.1:9010")
    monkeypatch.setattr(
        telco_cloud.CloudProfileConfig,
        "from_env",
        classmethod(lambda cls: profile),
    )
    monkeypatch.setattr(
        spanner,
        "Client",
        lambda **kwargs: pytest.fail("client must not be constructed"),
    )

    with pytest.raises(RuntimeError, match="database role mismatch"):
        entrypoint._build_repository()
