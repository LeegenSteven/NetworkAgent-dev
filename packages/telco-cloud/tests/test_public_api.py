from __future__ import annotations

import os
import re

import pytest
import telco_cloud

from telco_cloud import (
    CANONICAL_SCHEMA_DDL,
    CloudProfileConfig,
    SpannerEventIngestRepository,
    SpannerIncidentRepository,
    SpannerTelemetryRepository,
    apply_object_schema,
    apply_schema,
    compose_spanner_database,
)
from telco_cloud._spanner import commit_timestamp, json_object


class _NoTouchDatabase:
    def __getattr__(self, name: str):
        raise AssertionError(f"constructor unexpectedly touched database.{name}")


def test_config_is_explicit_and_has_no_environment_side_effect(monkeypatch) -> None:
    monkeypatch.setenv("SPANNER_EMULATOR_HOST", "127.0.0.1:9010")
    original = dict(os.environ)

    config = CloudProfileConfig(
        project_id="network-agent-test",
        instance_id="network-agent-instance",
        database_id="network-agent-db",
        database_role=None,
        emulator_host="127.0.0.1:9010",
    )

    assert config.project_id == "network-agent-test"
    assert dict(os.environ) == original


def test_config_from_env_requires_all_resource_ids() -> None:
    with pytest.raises(ValueError, match="GOOGLE_SPANNER_DATABASE"):
        CloudProfileConfig.from_env(
            {
                "GOOGLE_PROJECT": "network-agent-test",
                "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
            }
        )

    with pytest.raises(ValueError, match="TELCO_SPANNER_DATABASE_ROLE"):
        CloudProfileConfig.from_env(
            {
                "GOOGLE_CLOUD_PROJECT": "network-agent-test",
                "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
                "GOOGLE_SPANNER_DATABASE": "network-agent-db",
            }
        )

    config = CloudProfileConfig.from_env(
        {
            "GOOGLE_CLOUD_PROJECT": "network-agent-test",
            "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
            "GOOGLE_SPANNER_DATABASE": "network-agent-db",
            "TELCO_SPANNER_DATABASE_ROLE": "telco_mcp_reader",
        }
    )
    assert config.database_id == "network-agent-db"
    assert config.database_role == "telco_mcp_reader"

    emulator = CloudProfileConfig.from_env(
        {
            "GOOGLE_CLOUD_PROJECT": "network-agent-test",
            "GOOGLE_SPANNER_INSTANCE": "network-agent-instance",
            "GOOGLE_SPANNER_DATABASE": "network-agent-db",
            "SPANNER_EMULATOR_HOST": "127.0.0.1:9010",
        }
    )
    assert emulator.database_role is None


@pytest.mark.parametrize(
    "database_role",
    ["", "public", "spanner_info_reader", "role-with-dash", "1reader"],
)
def test_config_rejects_unsafe_or_reserved_database_roles(database_role) -> None:
    with pytest.raises(ValueError, match="database_role"):
        CloudProfileConfig(
            project_id="network-agent-test",
            instance_id="network-agent-instance",
            database_id="network-agent-db",
            database_role=database_role,
        )


def test_composition_forwards_database_role_without_touching_credentials() -> None:
    calls: list[tuple[str, object]] = []

    class Instance:
        def database(self, database_id, *, database_role):
            calls.append(("database", (database_id, database_role)))
            return object()

    class Client:
        def instance(self, instance_id):
            calls.append(("instance", instance_id))
            return Instance()

    profile = CloudProfileConfig(
        project_id="network-agent-test",
        instance_id="network-agent-instance",
        database_id="network-agent-db",
        database_role="telco_fault_writer",
    )
    database = compose_spanner_database(profile, client=Client())

    assert database is not None
    assert calls == [
        ("instance", "network-agent-instance"),
        ("database", ("network-agent-db", "telco_fault_writer")),
    ]


def test_emulator_composition_requires_exact_process_endpoint(monkeypatch) -> None:
    profile = CloudProfileConfig(
        project_id="network-agent-test",
        instance_id="network-agent-instance",
        database_id="network-agent-db",
        emulator_host="127.0.0.1:9010",
    )
    monkeypatch.delenv("SPANNER_EMULATOR_HOST", raising=False)

    with pytest.raises(ValueError, match="must exist and match"):
        compose_spanner_database(profile, client=object())


def test_repository_construction_has_no_io() -> None:
    database = _NoTouchDatabase()

    SpannerIncidentRepository(database)
    SpannerTelemetryRepository(database)
    SpannerEventIngestRepository(database)


def test_spanner_json_mutation_wrapper_is_lazy_and_mapping_compatible() -> None:
    payload = {"schema_version": "1.0", "safe": True}

    wrapped = json_object(payload)

    assert dict(wrapped) == payload
    assert commit_timestamp() is not None


def test_schema_is_v2_idempotent_and_complete() -> None:
    joined = "\n".join(CANONICAL_SCHEMA_DDL)
    expected = {
        "CanonicalIncidentsV2",
        "CanonicalIncidentSourceEventsV2",
        "CanonicalIncidentAuditV2",
        "CanonicalIncidentIdempotencyV2",
        "CanonicalIncidentActiveKeysV2",
        "CanonicalSourceEventInboxV2",
        "CanonicalIncidentOutboxV2",
        "RadioKpiObservationsV1",
        "SafeEvidenceReferencesV1",
    }
    assert expected <= {name for name in expected if name in joined}
    object_ddl = [
        ddl
        for ddl in CANONICAL_SCHEMA_DDL
        if ddl.lstrip().startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE", "CREATE NULL_FILTERED"))
    ]
    assert all("IF NOT EXISTS" in ddl for ddl in object_ddl)
    assert "CREATE ROLE telco_fault_writer" in joined
    assert "CREATE ROLE telco_mcp_reader" in joined
    assert (
        "GRANT SELECT ON TABLE CanonicalIncidentsV2, "
        "CanonicalIncidentAuditV2, RadioKpiObservationsV1, "
        "SafeEvidenceReferencesV1, "
        "CanonicalResourceReferencesV1 TO ROLE telco_mcp_reader"
    ) in joined
    assert "CanonicalSourceEventInboxV2" not in next(
        ddl for ddl in CANONICAL_SCHEMA_DDL if "TO ROLE telco_mcp_reader" in ddl
    )
    fault_grants = "\n".join(
        ddl for ddl in CANONICAL_SCHEMA_DDL if "TO ROLE telco_fault_writer" in ddl
    )
    assert "UPDATE" not in fault_grants
    assert "DELETE" not in fault_grants
    assert "GRANT SELECT, INSERT ON TABLE" in fault_grants
    assert "CanonicalIncidentOutboxV2" in fault_grants
    assert (
        "GRANT INSERT ON TABLE CanonicalIncidentAuditV2 "
        "TO ROLE telco_fault_writer"
    ) in fault_grants
    assert "SELECT, INSERT ON TABLE CanonicalIncidentAuditV2" not in fault_grants
    dispatcher_grants = "\n".join(
        ddl
        for ddl in CANONICAL_SCHEMA_DDL
        if "TO ROLE telco_outbox_dispatcher" in ddl
    )
    assert "GRANT UPDATE(status, attempts, available_at, published_at, " in dispatcher_grants
    assert "incident_id" not in dispatcher_grants
    assert "payload" not in dispatcher_grants
    assert "CREATE TABLE Incident " not in joined
    active_keys_ddl = next(
        ddl
        for ddl in CANONICAL_SCHEMA_DDL
        if "CREATE TABLE IF NOT EXISTS CanonicalIncidentActiveKeysV2" in ddl
    )
    assert "key_value" not in active_keys_ddl


def test_secondary_index_storing_columns_exclude_table_and_index_keys() -> None:
    """Catch GoogleSQL DDL that the real Spanner service rejects.

    Secondary indexes already carry their own key columns and every base-table
    primary-key column. GoogleSQL therefore rejects either category in a
    STORING clause even though lightweight DDL fakes can accept the text.
    """

    primary_keys: dict[str, set[str]] = {}
    for ddl in CANONICAL_SCHEMA_DDL:
        table = re.search(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+).*?"
            r"PRIMARY\s+KEY\s*\(([^)]*)\)",
            ddl,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if table is not None:
            primary_keys[table.group(1)] = {
                column.strip().split()[0]
                for column in table.group(2).split(",")
            }

    index_ddls = tuple(
        ddl
        for ddl in CANONICAL_SCHEMA_DDL
        if re.match(
            r"^\s*CREATE\s+(?:(?:UNIQUE|NULL_FILTERED)\s+)*INDEX\b",
            ddl,
            flags=re.IGNORECASE,
        )
    )
    parsed_indexes = 0
    for ddl in index_ddls:
        index = re.search(
            r"CREATE\s+(?:(?:UNIQUE|NULL_FILTERED)\s+)*INDEX\s+"
            r"(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*"
            r"\((.*?)\)(?:\s+STORING\s*\((.*?)\))?\s*$",
            ddl,
            flags=re.IGNORECASE | re.DOTALL,
        )
        assert index is not None, f"static index DDL parser missed: {ddl!r}"
        parsed_indexes += 1
        if index.group(4) is None:
            continue
        index_name, table_name = index.group(1), index.group(2)
        key_columns = {
            column.strip().split()[0]
            for column in index.group(3).split(",")
        }
        storing_columns = {
            column.strip().split()[0]
            for column in index.group(4).split(",")
        }
        forbidden = key_columns | primary_keys[table_name]
        assert storing_columns.isdisjoint(forbidden), (
            f"{index_name} repeats key columns in STORING: "
            f"{sorted(storing_columns & forbidden)}"
        )
    assert parsed_indexes == len(index_ddls)


def test_migration_importer_role_is_exported_and_exactly_bounded() -> None:
    assert getattr(telco_cloud, "MIGRATION_DATABASE_ROLE", None) == (
        "telco_migration_importer"
    )
    grants = tuple(
        ddl
        for ddl in CANONICAL_SCHEMA_DDL
        if "TO ROLE telco_migration_importer" in ddl
    )
    assert grants == (
        "GRANT SELECT, INSERT ON TABLE CanonicalIncidentsV2, "
        "CanonicalIncidentSourceEventsV2, CanonicalIncidentAuditV2, "
        "CanonicalIncidentIdempotencyV2, CanonicalIncidentActiveKeysV2 "
        "TO ROLE telco_migration_importer",
    )
    assert not any(
        forbidden in grants[0]
        for forbidden in (
            "UPDATE",
            "DELETE",
            "CanonicalSourceEventInboxV2",
            "CanonicalIncidentOutboxV2",
            "RadioKpiObservationsV1",
            "SafeEvidenceReferencesV1",
            "CanonicalResourceReferencesV1",
        )
    )


def test_apply_schema_waits_for_operation() -> None:
    calls: list[tuple[str, object]] = []

    class Operation:
        def result(self, *, timeout: int):
            calls.append(("result", timeout))
            return None

    class Database:
        def list_database_roles(self):
            return ()

        def update_ddl(self, statements):
            calls.append(("update", tuple(statements)))
            return Operation()

        def snapshot(self):
            class Snapshot:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def execute_sql(self, sql, **kwargs):
                    del sql, kwargs
                    return ()

            return Snapshot()

    apply_schema(Database())

    assert len(calls) == 6
    assert calls[0][0] == "update"
    assert all("CREATE ROLE" not in ddl and "GRANT " not in ddl for ddl in calls[0][1])
    assert calls[1] == ("result", 600)
    assert calls[2] == (
        "update",
        (
            "CREATE ROLE telco_fault_writer",
            "CREATE ROLE telco_mcp_reader",
            "CREATE ROLE telco_outbox_dispatcher",
            "CREATE ROLE telco_migration_importer",
        ),
    )
    assert calls[3] == ("result", 600)
    assert calls[4][0] == "update"
    assert all(ddl.startswith("GRANT ") for ddl in calls[4][1])
    assert calls[5] == ("result", 600)


def test_apply_object_schema_never_touches_fgac_apis() -> None:
    calls: list[tuple[str, object]] = []

    class Operation:
        def result(self, *, timeout: int):
            calls.append(("result", timeout))

    class Database:
        def update_ddl(self, statements):
            calls.append(("update", tuple(statements)))
            return Operation()

        def __getattr__(self, name: str):
            raise AssertionError(f"object-only schema touched FGAC API {name}")

    apply_object_schema(Database())

    assert calls[0][0] == "update"
    assert all(
        "CREATE ROLE" not in ddl and "GRANT " not in ddl
        for ddl in calls[0][1]
    )
    assert calls[1] == ("result", 600)


def test_every_information_schema_audit_query_includes_all_managed_roles() -> None:
    queries: list[str] = []

    class Operation:
        def result(self, *, timeout):
            assert timeout == 600

    class Snapshot:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute_sql(self, sql, **kwargs):
            del kwargs
            queries.append(sql)
            return ()

    class Database:
        def list_database_roles(self):
            return (
                "telco_fault_writer",
                "telco_mcp_reader",
                "telco_outbox_dispatcher",
                "telco_migration_importer",
            )

        def update_ddl(self, statements):
            assert statements
            return Operation()

        def snapshot(self):
            return Snapshot()

    apply_schema(Database())

    assert len(queries) == 6
    for query in queries:
        assert all(
            f"'{role}'" in query
            for role in (
                "telco_fault_writer",
                "telco_mcp_reader",
                "telco_outbox_dispatcher",
                "telco_migration_importer",
                "public",
            )
        )


@pytest.mark.parametrize(
    ("query_marker", "row"),
    [
        (
            "TABLE_PRIVILEGES",
            ("public", "LegacyRawLogs", "SELECT"),
        ),
        (
            "COLUMN_PRIVILEGES",
            ("telco_mcp_reader", "LegacyRawLogs", "payload", "SELECT"),
        ),
        (
            "CHANGE_STREAM_PRIVILEGES",
            ("telco_mcp_reader", "raw_changes", "SELECT"),
        ),
        (
            "ROLE_GRANTEES",
            ("spanner_info_reader", "telco_mcp_reader"),
        ),
        (
            "ROLE_GRANTEES",
            ("legacy_raw_reader", "public"),
        ),
        (
            "TABLE_PRIVILEGES",
            ("evil_writer", "CanonicalIncidentsV2", "UPDATE"),
        ),
        (
            "COLUMN_PRIVILEGES",
            (
                "evil_writer",
                "CanonicalIncidentOutboxV2",
                "payload",
                "UPDATE",
            ),
        ),
        (
            "ROLE_GRANTEES",
            ("telco_fault_writer", "evil_role"),
        ),
    ],
)
def test_apply_schema_fails_closed_on_unexpected_fgac_access(
    query_marker: str, row: tuple[str, ...]
) -> None:
    class Operation:
        def result(self, *, timeout):
            assert timeout == 600

    class Snapshot:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute_sql(self, sql, **kwargs):
            del kwargs
            return (row,) if query_marker in sql else ()

    class Database:
        def list_database_roles(self):
            return (
                "telco_fault_writer",
                "telco_mcp_reader",
                "telco_outbox_dispatcher",
                "telco_migration_importer",
            )

        def update_ddl(self, statements):
            assert statements
            return Operation()

        def snapshot(self):
            return Snapshot()

    with pytest.raises(RuntimeError, match="unexpected"):
        apply_schema(Database())
