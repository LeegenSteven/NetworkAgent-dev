from __future__ import annotations

import pytest

import telco_cloud.schema_cli as schema_cli


def test_help_has_no_environment_or_credential_side_effect(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        schema_cli.main(["--help"], environ={})

    assert exc_info.value.code == 0
    assert "apply" in capsys.readouterr().out


def test_apply_uses_explicit_profile_and_role_scoped_composition(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    database = object()

    class Instance:
        def database(self, database_id, *, database_role):
            calls.append(("database", (database_id, database_role)))
            return database

    class Client:
        def instance(self, instance_id):
            calls.append(("instance", instance_id))
            return Instance()

    monkeypatch.setattr(
        schema_cli,
        "apply_schema",
        lambda value: calls.append(("apply", value)),
    )

    result = schema_cli.main(
        ["apply"],
        environ={
            "GOOGLE_PROJECT": "network-agent-test",
            "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
            "GOOGLE_SPANNER_DATABASE": "network-agent-db",
        },
        client=Client(),
    )

    assert result == 0
    assert calls == [
        ("instance", "network-agent-instance"),
        ("database", ("network-agent-db", None)),
        ("apply", database),
    ]


def test_apply_rejects_runtime_database_role() -> None:
    with pytest.raises(ValueError, match="rejects TELCO_SPANNER_DATABASE_ROLE"):
        schema_cli.main(
            ["apply"],
            environ={
                "GOOGLE_PROJECT": "network-agent-test",
                "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
                "GOOGLE_SPANNER_DATABASE": "network-agent-db",
                "TELCO_SPANNER_DATABASE_ROLE": "telco_fault_writer",
            },
            client=object(),
        )


def test_apply_uses_object_only_schema_for_explicit_loopback_emulator(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []
    database = object()

    class Instance:
        def database(self, database_id, *, database_role):
            calls.append(("database", (database_id, database_role)))
            return database

    class Client:
        def instance(self, instance_id):
            calls.append(("instance", instance_id))
            return Instance()

    monkeypatch.setenv("SPANNER_EMULATOR_HOST", "127.0.0.1:9010")
    monkeypatch.setattr(
        schema_cli,
        "apply_object_schema",
        lambda value: calls.append(("apply-objects", value)),
    )
    monkeypatch.setattr(
        schema_cli,
        "apply_schema",
        lambda value: pytest.fail(f"emulator attempted FGAC schema: {value!r}"),
    )

    result = schema_cli.main(
        ["apply"],
        environ={
            "GOOGLE_PROJECT": "network-agent-test",
            "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
            "GOOGLE_SPANNER_DATABASE": "network-agent-db",
            "SPANNER_EMULATOR_HOST": "127.0.0.1:9010",
        },
        client=Client(),
    )

    assert result == 0
    assert calls == [
        ("instance", "network-agent-instance"),
        ("database", ("network-agent-db", None)),
        ("apply-objects", database),
    ]
