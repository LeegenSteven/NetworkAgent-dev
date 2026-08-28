"""Idempotent GoogleSQL DDL for the Canonical Cloud Profile schema."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


FAULT_DATABASE_ROLE = "telco_fault_writer"
MCP_DATABASE_ROLE = "telco_mcp_reader"
MIGRATION_DATABASE_ROLE = "telco_migration_importer"
OUTBOX_DATABASE_ROLE = "telco_outbox_dispatcher"

_CANONICAL_TABLES = (
    "CanonicalIncidentActiveKeysV2",
    "CanonicalIncidentAuditV2",
    "CanonicalIncidentIdempotencyV2",
    "CanonicalIncidentOutboxV2",
    "CanonicalIncidentSourceEventsV2",
    "CanonicalIncidentsV2",
    "CanonicalResourceReferencesV1",
    "CanonicalSourceEventInboxV2",
    "RadioKpiObservationsV1",
    "SafeEvidenceReferencesV1",
)
_CANONICAL_TABLE_SQL = ", ".join(
    f"'{table}'" for table in _CANONICAL_TABLES
)

_CANONICAL_OBJECT_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS CanonicalIncidentsV2 (
        incident_id STRING(256) NOT NULL,
        correlation_key STRING(256),
        schema_version STRING(16) NOT NULL,
        technology STRING(16) NOT NULL,
        status STRING(32) NOT NULL,
        severity STRING(16) NOT NULL,
        revision INT64 NOT NULL,
        trace_id STRING(256) NOT NULL,
        detected_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        payload JSON NOT NULL
    ) PRIMARY KEY (incident_id)""",
    """CREATE INDEX IF NOT EXISTS CanonicalIncidentsV2ByStatusUpdated
        ON CanonicalIncidentsV2(status, updated_at DESC, incident_id)
        STORING (payload)""",
    """CREATE NULL_FILTERED INDEX IF NOT EXISTS CanonicalIncidentsV2ByCorrelation
        ON CanonicalIncidentsV2(correlation_key, incident_id)
        STORING (status, payload)""",
    """CREATE TABLE IF NOT EXISTS CanonicalIncidentSourceEventsV2 (
        incident_id STRING(256) NOT NULL,
        source_event_id STRING(256) NOT NULL,
        registered_at TIMESTAMP NOT NULL,
        actor STRING(256) NOT NULL,
        reason STRING(MAX) NOT NULL,
        idempotency_key STRING(256) NOT NULL,
        trace_id STRING(256) NOT NULL,
        payload JSON NOT NULL
    ) PRIMARY KEY (incident_id, source_event_id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS CanonicalIncidentSourceEventsV2ByGlobalSource
        ON CanonicalIncidentSourceEventsV2(source_event_id)
        STORING (incident_id, payload)""",
    """CREATE TABLE IF NOT EXISTS CanonicalIncidentAuditV2 (
        incident_id STRING(256) NOT NULL,
        revision INT64 NOT NULL,
        event_id STRING(256) NOT NULL,
        from_status STRING(32),
        to_status STRING(32) NOT NULL,
        trace_id STRING(256) NOT NULL,
        occurred_at TIMESTAMP NOT NULL,
        committed_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
        payload JSON NOT NULL
    ) PRIMARY KEY (incident_id, revision)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS CanonicalIncidentAuditV2ByEvent
        ON CanonicalIncidentAuditV2(event_id)""",
    """CREATE TABLE IF NOT EXISTS CanonicalIncidentIdempotencyV2 (
        operation STRING(64) NOT NULL,
        requested_incident_id STRING(256) NOT NULL,
        idempotency_key STRING(256) NOT NULL,
        request_fingerprint STRING(64) NOT NULL,
        result_incident_id STRING(256) NOT NULL,
        result_payload JSON NOT NULL,
        created_at TIMESTAMP NOT NULL
    ) PRIMARY KEY (operation, requested_incident_id, idempotency_key)""",
    """CREATE TABLE IF NOT EXISTS CanonicalIncidentActiveKeysV2 (
        key_hash STRING(64) NOT NULL,
        key_kind STRING(16) NOT NULL,
        incident_id STRING(256) NOT NULL,
        registered_at TIMESTAMP NOT NULL
    ) PRIMARY KEY (key_hash)""",
    """CREATE INDEX IF NOT EXISTS CanonicalIncidentActiveKeysV2ByIncident
        ON CanonicalIncidentActiveKeysV2(incident_id, key_hash)""",
    """CREATE TABLE IF NOT EXISTS CanonicalSourceEventInboxV2 (
        source_event_id STRING(256) NOT NULL,
        source STRING(MAX) NOT NULL,
        event_type STRING(128) NOT NULL,
        payload_sha256 STRING(64) NOT NULL,
        envelope_fingerprint STRING(64) NOT NULL,
        trace_id STRING(256) NOT NULL,
        received_at TIMESTAMP NOT NULL,
        processed_at TIMESTAMP NOT NULL,
        disposition STRING(32) NOT NULL,
        incident_id STRING(256),
        outbox_event_id STRING(256),
        result_payload JSON NOT NULL
    ) PRIMARY KEY (source_event_id)""",
    """CREATE TABLE IF NOT EXISTS CanonicalIncidentOutboxV2 (
        event_id STRING(256) NOT NULL,
        incident_id STRING(256) NOT NULL,
        source_event_id STRING(256) NOT NULL,
        event_type STRING(128) NOT NULL,
        payload JSON NOT NULL,
        status STRING(16) NOT NULL,
        attempts INT64 NOT NULL,
        available_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP NOT NULL,
        published_at TIMESTAMP,
        lease_owner STRING(256),
        lease_expires_at TIMESTAMP,
        last_error_code STRING(128)
    ) PRIMARY KEY (event_id)""",
    """ALTER TABLE IF EXISTS CanonicalIncidentOutboxV2
        ADD COLUMN IF NOT EXISTS lease_owner STRING(256)""",
    """ALTER TABLE IF EXISTS CanonicalIncidentOutboxV2
        ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP""",
    """ALTER TABLE IF EXISTS CanonicalIncidentOutboxV2
        ADD COLUMN IF NOT EXISTS last_error_code STRING(128)""",
    """CREATE INDEX IF NOT EXISTS CanonicalIncidentOutboxV2ByDelivery
        ON CanonicalIncidentOutboxV2(status, available_at, event_id)""",
    """CREATE INDEX IF NOT EXISTS CanonicalIncidentOutboxV2ByLeaseExpiry
        ON CanonicalIncidentOutboxV2(status, lease_expires_at, event_id)
        STORING (attempts, lease_owner, incident_id, source_event_id,
                 event_type, payload, created_at, published_at, last_error_code)""",
    """CREATE TABLE IF NOT EXISTS RadioKpiObservationsV1 (
        observation_id STRING(256) NOT NULL,
        kpi_name STRING(256) NOT NULL,
        technology STRING(16) NOT NULL,
        primary_resource_id STRING(256) NOT NULL,
        observed_at TIMESTAMP NOT NULL,
        payload JSON NOT NULL
    ) PRIMARY KEY (observation_id)""",
    """CREATE INDEX IF NOT EXISTS RadioKpiObservationsV1ByQuery
        ON RadioKpiObservationsV1(technology, kpi_name, observed_at, observation_id)
        STORING (primary_resource_id, payload)""",
    """CREATE INDEX IF NOT EXISTS RadioKpiObservationsV1ByResource
        ON RadioKpiObservationsV1(technology, primary_resource_id, observed_at, observation_id)
        STORING (kpi_name, payload)""",
    """CREATE TABLE IF NOT EXISTS SafeEvidenceReferencesV1 (
        evidence_id STRING(256) NOT NULL,
        incident_id STRING(256) NOT NULL,
        evidence_type STRING(32) NOT NULL,
        collected_at TIMESTAMP NOT NULL,
        payload JSON NOT NULL
    ) PRIMARY KEY (evidence_id)""",
    """CREATE INDEX IF NOT EXISTS SafeEvidenceReferencesV1ByIncident
        ON SafeEvidenceReferencesV1(incident_id, collected_at, evidence_id)
        STORING (evidence_type, payload)""",
    """CREATE TABLE IF NOT EXISTS CanonicalResourceReferencesV1 (
        resource_id STRING(256) NOT NULL,
        technology STRING(16) NOT NULL,
        resource_type STRING(32) NOT NULL,
        payload JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL
    ) PRIMARY KEY (resource_id)""",
    """CREATE INDEX IF NOT EXISTS CanonicalResourceReferencesV1ByTechnology
        ON CanonicalResourceReferencesV1(technology, resource_id)
        STORING (payload)""",
)


_FGAC_TABLE_PRIVILEGES: dict[str, dict[str, tuple[str, ...]]] = {
    FAULT_DATABASE_ROLE: {
        "CanonicalIncidentsV2": ("SELECT", "INSERT"),
        "CanonicalIncidentSourceEventsV2": ("SELECT", "INSERT"),
        "CanonicalIncidentAuditV2": ("INSERT",),
        "CanonicalIncidentIdempotencyV2": ("SELECT", "INSERT"),
        "CanonicalIncidentActiveKeysV2": ("SELECT", "INSERT"),
        "CanonicalSourceEventInboxV2": ("SELECT", "INSERT"),
        # SELECT is required to validate the durable CREATED replay snapshot
        # before acknowledging a redelivered source event.
        "CanonicalIncidentOutboxV2": ("SELECT", "INSERT"),
    },
    MCP_DATABASE_ROLE: {
        "CanonicalIncidentsV2": ("SELECT",),
        "CanonicalIncidentAuditV2": ("SELECT",),
        "RadioKpiObservationsV1": ("SELECT",),
        "SafeEvidenceReferencesV1": ("SELECT",),
        "CanonicalResourceReferencesV1": ("SELECT",),
    },
    OUTBOX_DATABASE_ROLE: {
        "CanonicalIncidentOutboxV2": ("SELECT",),
    },
    MIGRATION_DATABASE_ROLE: {
        "CanonicalIncidentsV2": ("SELECT", "INSERT"),
        "CanonicalIncidentSourceEventsV2": ("SELECT", "INSERT"),
        "CanonicalIncidentAuditV2": ("SELECT", "INSERT"),
        "CanonicalIncidentIdempotencyV2": ("SELECT", "INSERT"),
        "CanonicalIncidentActiveKeysV2": ("SELECT", "INSERT"),
    },
}
_FGAC_COLUMN_PRIVILEGES: dict[
    str, dict[str, dict[str, tuple[str, ...]]]
] = {
    OUTBOX_DATABASE_ROLE: {
        "CanonicalIncidentOutboxV2": {
            "UPDATE": (
                "status",
                "attempts",
                "available_at",
                "published_at",
                "lease_owner",
                "lease_expires_at",
                "last_error_code",
            )
        }
    }
}
_PRIVILEGE_ORDER = {name: index for index, name in enumerate(
    ("SELECT", "INSERT", "UPDATE", "DELETE")
)}


def _render_grants(
    privileges: dict[str, dict[str, tuple[str, ...]]]
) -> tuple[str, ...]:
    grouped: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for role, tables in privileges.items():
        for table, allowed in tables.items():
            ordered = tuple(sorted(set(allowed), key=_PRIVILEGE_ORDER.__getitem__))
            if ordered:
                grouped[(role, ordered)].append(table)
    statements: list[str] = []
    for (role, allowed), tables in sorted(grouped.items()):
        statements.append(
            f"GRANT {', '.join(allowed)} ON TABLE {', '.join(tables)} TO ROLE {role}"
        )
    return tuple(statements)


def _render_column_grants(
    privileges: dict[str, dict[str, dict[str, tuple[str, ...]]]]
) -> tuple[str, ...]:
    statements: list[str] = []
    for role, tables in sorted(privileges.items()):
        for table, grants in sorted(tables.items()):
            for privilege, columns in sorted(grants.items()):
                if columns:
                    statements.append(
                        f"GRANT {privilege}({', '.join(columns)}) "
                        f"ON TABLE {table} TO ROLE {role}"
                    )
    return tuple(statements)


_FGAC_ROLE_DDL = tuple(
    f"CREATE ROLE {role}" for role in _FGAC_TABLE_PRIVILEGES
)
_FGAC_GRANT_DDL = (
    _render_grants(_FGAC_TABLE_PRIVILEGES)
    + _render_column_grants(_FGAC_COLUMN_PRIVILEGES)
)
_FGAC_AUDIT_GRANTEES_SQL = ", ".join(
    f"'{role}'" for role in (*_FGAC_TABLE_PRIVILEGES, "public")
)

# Complete declarative schema artifact. ``apply_schema`` separates role DDL
# because GoogleSQL deliberately has no ``CREATE ROLE IF NOT EXISTS`` syntax.
CANONICAL_SCHEMA_DDL: tuple[str, ...] = (
    _CANONICAL_OBJECT_DDL + _FGAC_ROLE_DDL + _FGAC_GRANT_DDL
)


def _role_name(role: object) -> str:
    raw = str(getattr(role, "name", role))
    return raw.rsplit("/databaseRoles/", 1)[-1]


def _existing_privileges(
    database: Any,
) -> tuple[
    set[tuple[str, str, str]],
    set[tuple[str, str, str, str]],
    set[tuple[str, str]],
    tuple[tuple[object, ...], ...],
]:
    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(
            f"""SELECT GRANTEE, TABLE_NAME, PRIVILEGE_TYPE
            FROM INFORMATION_SCHEMA.TABLE_PRIVILEGES
            WHERE TABLE_NAME IN ({_CANONICAL_TABLE_SQL})
               OR GRANTEE IN ({_FGAC_AUDIT_GRANTEES_SQL})"""
        )
        privileges = {
            (str(role), str(table), str(privilege).upper())
            for role, table, privilege in rows
        }
        column_privileges = {
            (str(role), str(table), str(column), str(privilege).upper())
            for role, table, column, privilege in snapshot.execute_sql(
                f"""SELECT GRANTEE, TABLE_NAME, COLUMN_NAME, PRIVILEGE_TYPE
                FROM INFORMATION_SCHEMA.COLUMN_PRIVILEGES
                WHERE TABLE_NAME IN ({_CANONICAL_TABLE_SQL})
                   OR GRANTEE IN ({_FGAC_AUDIT_GRANTEES_SQL})"""
            )
        }
        memberships = {
            (str(role), str(grantee))
            for role, grantee in snapshot.execute_sql(
                f"""SELECT ROLE_NAME, GRANTEE
                FROM INFORMATION_SCHEMA.ROLE_GRANTEES
                WHERE ROLE_NAME IN ({_FGAC_AUDIT_GRANTEES_SQL})
                   OR GRANTEE IN ({_FGAC_AUDIT_GRANTEES_SQL})"""
            )
        }
        non_table_grants: list[tuple[object, ...]] = []
        for sql in (
            f"""SELECT GRANTEE, CHANGE_STREAM_NAME, PRIVILEGE_TYPE
            FROM INFORMATION_SCHEMA.CHANGE_STREAM_PRIVILEGES
            WHERE GRANTEE IN ({_FGAC_AUDIT_GRANTEES_SQL})""",
            f"""SELECT GRANTEE, SPECIFIC_NAME, PRIVILEGE_TYPE
            FROM INFORMATION_SCHEMA.ROUTINE_PRIVILEGES
            WHERE GRANTEE IN ({_FGAC_AUDIT_GRANTEES_SQL})""",
            f"""SELECT GRANTEE, MODEL_NAME, PRIVILEGE_TYPE
            FROM INFORMATION_SCHEMA.ROLE_MODEL_GRANTS
            WHERE GRANTEE IN ({_FGAC_AUDIT_GRANTEES_SQL})""",
        ):
            non_table_grants.extend(tuple(row) for row in snapshot.execute_sql(sql))
        return (
            privileges,
            column_privileges,
            memberships,
            tuple(non_table_grants),
        )


def _wait_for_ddl(database: Any, statements: tuple[str, ...]) -> None:
    if statements:
        database.update_ddl(statements).result(timeout=600)


def apply_object_schema(database: Any) -> None:
    """Apply only Canonical tables and indexes.

    This deliberately excludes database roles and privilege inspection.  It is
    the supported bootstrap for the Cloud Spanner emulator, which does not
    implement IAM/FGAC.  Production schema administration must call
    :func:`apply_schema` so that least-privilege roles are reconciled and
    audited instead of silently skipped.
    """

    _wait_for_ddl(database, _CANONICAL_OBJECT_DDL)


def apply_schema(database: Any) -> None:
    """Apply canonical objects and reconcile least-privilege database roles."""

    apply_object_schema(database)

    existing_roles = {_role_name(role) for role in database.list_database_roles()}
    missing_roles = tuple(
        statement
        for statement in _FGAC_ROLE_DDL
        if statement.removeprefix("CREATE ROLE ") not in existing_roles
    )
    _wait_for_ddl(database, missing_roles)

    existing, existing_columns, memberships, non_table_grants = (
        _existing_privileges(database)
    )
    if memberships or non_table_grants:
        raise RuntimeError(
            "canonical runtime roles have unexpected inherited or non-table "
            "privileges"
        )
    expected = {
        (role, table, privilege)
        for role, tables in _FGAC_TABLE_PRIVILEGES.items()
        for table, privileges in tables.items()
        for privilege in privileges
    }
    unexpected = existing - expected
    if unexpected:
        raise RuntimeError(
            "canonical runtime role has unexpected table privileges; revoke them "
            "explicitly before applying schema"
        )
    expected_columns = {
        (role, table, column, privilege)
        for role, tables in _FGAC_COLUMN_PRIVILEGES.items()
        for table, grants in tables.items()
        for privilege, columns in grants.items()
        for column in columns
    }
    unexpected_columns = {
        item
        for item in existing_columns
        if (item[0], item[1], item[3]) not in expected
        and item not in expected_columns
    }
    if unexpected_columns:
        raise RuntimeError(
            "canonical runtime role has unexpected column privileges; revoke "
            "them explicitly before applying schema"
        )
    missing: dict[str, dict[str, tuple[str, ...]]] = {}
    for role, tables in _FGAC_TABLE_PRIVILEGES.items():
        for table, privileges in tables.items():
            absent = tuple(
                privilege
                for privilege in privileges
                if (role, table, privilege) not in existing
            )
            if absent:
                missing.setdefault(role, {})[table] = absent
    missing_columns: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {}
    for role, tables in _FGAC_COLUMN_PRIVILEGES.items():
        for table, grants in tables.items():
            for privilege, columns in grants.items():
                absent = tuple(
                    column
                    for column in columns
                    if (role, table, column, privilege)
                    not in existing_columns
                )
                if absent:
                    missing_columns.setdefault(role, {}).setdefault(
                        table, {}
                    )[privilege] = absent
    _wait_for_ddl(
        database,
        _render_grants(missing) + _render_column_grants(missing_columns),
    )


__all__ = [
    "CANONICAL_SCHEMA_DDL",
    "FAULT_DATABASE_ROLE",
    "MCP_DATABASE_ROLE",
    "MIGRATION_DATABASE_ROLE",
    "OUTBOX_DATABASE_ROLE",
    "apply_object_schema",
    "apply_schema",
]
