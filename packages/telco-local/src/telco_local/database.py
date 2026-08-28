"""DuckDB bootstrap for safe LTE telemetry and canonical Incident storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from .config import LocalProfileConfig
from .lte_identifiers import (
    LTE_IDENTIFIER_DECIMAL_PATTERN,
    LTE_IDENTIFIER_MAX,
)


LOCAL_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class DatabaseSummary:
    database_path: Path
    schema_version: str
    performance_rows: int
    trace_rows: int
    incident_rows: int


_REPOSITORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_schema_metadata (
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_incidents (
    incident_id VARCHAR PRIMARY KEY,
    correlation_key VARCHAR,
    source_event_ids JSON NOT NULL,
    status VARCHAR NOT NULL,
    revision BIGINT NOT NULL,
    trace_id VARCHAR NOT NULL,
    payload JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS canonical_incidents_status_idx
    ON canonical_incidents(status);
CREATE INDEX IF NOT EXISTS canonical_incidents_correlation_idx
    ON canonical_incidents(correlation_key);

CREATE TABLE IF NOT EXISTS canonical_incident_source_events (
    incident_id VARCHAR NOT NULL,
    source_event_id VARCHAR NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL,
    idempotency_key VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    trace_id VARCHAR NOT NULL,
    PRIMARY KEY (incident_id, source_event_id)
);

-- Forward migration for Local Profile databases initialized by an earlier P2a
-- development build.  Historical rows remain readable with NULL provenance;
-- every new association is complete and immutable.
ALTER TABLE canonical_incident_source_events
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR;
ALTER TABLE canonical_incident_source_events
    ADD COLUMN IF NOT EXISTS actor VARCHAR;
ALTER TABLE canonical_incident_source_events
    ADD COLUMN IF NOT EXISTS reason VARCHAR;
ALTER TABLE canonical_incident_source_events
    ADD COLUMN IF NOT EXISTS trace_id VARCHAR;

CREATE INDEX IF NOT EXISTS canonical_incident_source_events_source_idx
    ON canonical_incident_source_events(source_event_id);

CREATE TABLE IF NOT EXISTS canonical_incident_audit (
    event_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL,
    revision BIGINT NOT NULL,
    from_status VARCHAR,
    to_status VARCHAR NOT NULL,
    trace_id VARCHAR NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload JSON NOT NULL,
    UNIQUE (incident_id, revision)
);

CREATE INDEX IF NOT EXISTS canonical_incident_audit_incident_idx
    ON canonical_incident_audit(incident_id);

CREATE TABLE IF NOT EXISTS canonical_incident_idempotency (
    operation VARCHAR NOT NULL,
    requested_incident_id VARCHAR NOT NULL,
    idempotency_key VARCHAR NOT NULL,
    request_fingerprint VARCHAR NOT NULL,
    result_payload JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (operation, requested_incident_id, idempotency_key)
);
"""


_KPI_VIEW = """
CREATE OR REPLACE VIEW performance_kpi AS
SELECT
    CAST(enodeb_id AS VARCHAR) AS enodeb_id,
    CAST(cell_id AS VARCHAR) AS cell_id,
    measurement_end,
    CAST(rowid AS BIGINT) AS source_row_id,
    (
        ERAB_EstabInitSuccNbr_QCI1 + ERAB_EstabInitSuccNbr_QCI2 +
        ERAB_EstabInitSuccNbr_QCI3 + ERAB_EstabInitSuccNbr_QCI4 +
        ERAB_EstabInitSuccNbr_QCI5 + ERAB_EstabInitSuccNbr_QCI6 +
        ERAB_EstabInitSuccNbr_QCI7 + ERAB_EstabInitSuccNbr_QCI8 +
        ERAB_EstabInitSuccNbr_QCI9
    ) * 100.0 / NULLIF(
        ERAB_EstabInitAttNbr_QCI1 + ERAB_EstabInitAttNbr_QCI2 +
        ERAB_EstabInitAttNbr_QCI3 + ERAB_EstabInitAttNbr_QCI4 +
        ERAB_EstabInitAttNbr_QCI5 + ERAB_EstabInitAttNbr_QCI6 +
        ERAB_EstabInitAttNbr_QCI7 + ERAB_EstabInitAttNbr_QCI8 +
        ERAB_EstabInitAttNbr_QCI9,
        0
    ) AS erab_success_rate,
    (
        ERAB_RelActNbr_QCI1 + ERAB_RelActNbr_QCI2 +
        ERAB_RelActNbr_QCI3 + ERAB_RelActNbr_QCI4 +
        ERAB_RelActNbr_QCI5 + ERAB_RelActNbr_QCI6 +
        ERAB_RelActNbr_QCI7 + ERAB_RelActNbr_QCI8 +
        ERAB_RelActNbr_QCI9
    ) * 3600.0 / NULLIF(ERAB_SessionTimeUE, 0) AS retainability,
    TRY_CAST(UL_RSSI AS DOUBLE) AS uplink_rssi_avg
FROM performance;
"""


def _connect(database_path: Path, *, read_only: bool = False):
    return duckdb.connect(str(database_path), read_only=read_only)


def _table_exists(connection: Any, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def _ensure_repository_schema(connection: Any) -> None:
    connection.execute(_REPOSITORY_SCHEMA)
    row = connection.execute(
        "SELECT value FROM local_schema_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO local_schema_metadata VALUES ('schema_version', ?)",
            [LOCAL_SCHEMA_VERSION],
        )
    elif str(row[0]) != LOCAL_SCHEMA_VERSION:
        raise RuntimeError(
            "unsupported Local Profile schema version "
            f"{row[0]!r}; expected {LOCAL_SCHEMA_VERSION!r}"
        )


def _drop_local_tables(connection: Any) -> None:
    connection.execute("DROP VIEW IF EXISTS performance_kpi")
    for table_name in (
        "performance",
        "cell_traces",
        "canonical_incident_idempotency",
        "canonical_incident_audit",
        "canonical_incident_source_events",
        "canonical_incidents",
        "local_schema_metadata",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table_name}")


def _valid_lte_identifier_sql(column: str) -> str:
    """Return static SQL driven by the shared Python identifier contract."""

    return (
        "COALESCE(REGEXP_FULL_MATCH("
        f"TRIM(CAST({column} AS VARCHAR)), "
        f"'{LTE_IDENTIFIER_DECIMAL_PATTERN}'"
        "), FALSE) AND COALESCE(TRY_CAST("
        f"TRIM(CAST({column} AS VARCHAR)) AS UBIGINT"
        f") <= {LTE_IDENTIFIER_MAX}, FALSE)"
    )


def _normalized_lte_identifier_sql(column: str) -> str:
    return (
        "CAST(CAST(TRIM(CAST("
        f"{column} AS VARCHAR)) AS UBIGINT) AS VARCHAR)"
    )


def _validate_performance_resource_ids(
    connection: Any,
    source: Path,
) -> None:
    enodeb_valid = _valid_lte_identifier_sql("EnodeB_id")
    cell_valid = _valid_lte_identifier_sql("cell_id")
    invalid_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM read_csv(
                ?,
                header = true,
                all_varchar = true,
                timestampformat = '%m/%d/%Y %H:%M:%S',
                sample_size = -1,
                ignore_errors = false
            )
            WHERE NOT ({enodeb_valid}) OR NOT ({cell_valid})
            """,
            [str(source)],
        ).fetchone()[0]
    )
    if invalid_count:
        raise ValueError(
            "performance CSV contains invalid LTE resource identifiers"
        )


def _validate_trace_resource_ids(
    connection: Any,
    source: Path,
) -> None:
    enodeb_valid = _valid_lte_identifier_sql("start_enodeb_id")
    cell_valid = _valid_lte_identifier_sql("start_cell_id")
    invalid_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM read_csv(
                ?,
                header = true,
                all_varchar = true,
                timestampformat = '%m/%d/%Y %H:%M:%S',
                sample_size = -1,
                ignore_errors = false
            )
            WHERE NOT ({enodeb_valid}) OR NOT ({cell_valid})
            """,
            [str(source)],
        ).fetchone()[0]
    )
    if invalid_count:
        raise ValueError(
            "safe trace CSV contains invalid LTE resource identifiers"
        )


def _import_performance(connection: Any, source: Path) -> None:
    # This projection is a privacy and resource boundary.  The source demo can
    # evolve or callers can accidentally provide a wider export; only the
    # counters needed by the Local Profile are ever materialized.
    _validate_performance_resource_ids(connection, source)
    enodeb_id = _normalized_lte_identifier_sql("EnodeB_id")
    cell_id = _normalized_lte_identifier_sql("cell_id")
    connection.execute(
        f"""
        CREATE TABLE performance AS
        SELECT
            {enodeb_id} AS enodeb_id,
            {cell_id} AS cell_id,
            STRPTIME(measurement_end, '%m/%d/%Y %H:%M:%S')
                AT TIME ZONE 'UTC'
                AS measurement_end,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI1 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI1,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI2 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI2,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI3 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI3,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI4 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI4,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI5 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI5,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI6 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI6,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI7 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI7,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI8 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI8,
            TRY_CAST(ERAB_EstabInitAttNbr_QCI9 AS DOUBLE)
                AS ERAB_EstabInitAttNbr_QCI9,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI1 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI1,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI2 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI2,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI3 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI3,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI4 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI4,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI5 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI5,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI6 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI6,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI7 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI7,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI8 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI8,
            TRY_CAST(ERAB_EstabInitSuccNbr_QCI9 AS DOUBLE)
                AS ERAB_EstabInitSuccNbr_QCI9,
            TRY_CAST(ERAB_RelActNbr_QCI1 AS DOUBLE)
                AS ERAB_RelActNbr_QCI1,
            TRY_CAST(ERAB_RelActNbr_QCI2 AS DOUBLE)
                AS ERAB_RelActNbr_QCI2,
            TRY_CAST(ERAB_RelActNbr_QCI3 AS DOUBLE)
                AS ERAB_RelActNbr_QCI3,
            TRY_CAST(ERAB_RelActNbr_QCI4 AS DOUBLE)
                AS ERAB_RelActNbr_QCI4,
            TRY_CAST(ERAB_RelActNbr_QCI5 AS DOUBLE)
                AS ERAB_RelActNbr_QCI5,
            TRY_CAST(ERAB_RelActNbr_QCI6 AS DOUBLE)
                AS ERAB_RelActNbr_QCI6,
            TRY_CAST(ERAB_RelActNbr_QCI7 AS DOUBLE)
                AS ERAB_RelActNbr_QCI7,
            TRY_CAST(ERAB_RelActNbr_QCI8 AS DOUBLE)
                AS ERAB_RelActNbr_QCI8,
            TRY_CAST(ERAB_RelActNbr_QCI9 AS DOUBLE)
                AS ERAB_RelActNbr_QCI9,
            TRY_CAST(ERAB_SessionTimeUE AS DOUBLE) AS ERAB_SessionTimeUE,
            TRY_CAST(UL_RSSI AS DOUBLE) AS UL_RSSI
        FROM read_csv(
            ?,
            header = true,
            all_varchar = true,
            timestampformat = '%m/%d/%Y %H:%M:%S',
            sample_size = -1,
            ignore_errors = false
        )
        """,
        [str(source)],
    )


def _import_safe_traces(connection: Any, source: Path) -> None:
    # The projection is the privacy boundary.  Even if a caller accidentally
    # points at the original wide Cell Trace CSV, subscriber columns are never
    # copied into DuckDB.
    _validate_trace_resource_ids(connection, source)
    enodeb_id = _normalized_lte_identifier_sql("start_enodeb_id")
    cell_id = _normalized_lte_identifier_sql("start_cell_id")
    connection.execute(
        f"""
        CREATE TABLE cell_traces AS
        SELECT
            CAST(procedure_type AS VARCHAR) AS procedure_type,
            STRPTIME(starttime, '%m/%d/%Y %H:%M:%S')
                AT TIME ZONE 'UTC' AS starttime,
            STRPTIME(endtime, '%m/%d/%Y %H:%M:%S')
                AT TIME ZONE 'UTC' AS endtime,
            {enodeb_id} AS start_enodeb_id,
            {cell_id} AS start_cell_id,
            CASE UPPER(TRIM(CAST(
                s1_sig_conn_setup_sig_conn_result AS VARCHAR
            )))
                WHEN 'SUCCESS' THEN 'SUCCESS'
                WHEN 'FAILURE' THEN 'FAILURE'
                WHEN 'FAILED_SECURITY_SETUP' THEN 'FAILED_SECURITY_SETUP'
                ELSE 'OTHER'
            END AS s1_sig_conn_setup_sig_conn_result
        FROM read_csv(
            ?,
            header = true,
            all_varchar = true,
            timestampformat = '%m/%d/%Y %H:%M:%S',
            sample_size = -1,
            ignore_errors = false
        )
        """,
        [str(source)],
    )


def initialize_database(
    config: LocalProfileConfig,
    *,
    reset: bool = False,
) -> DatabaseSummary:
    """Idempotently initialize one explicitly configured Local Profile store."""

    config.validate_inputs()
    config.database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = _connect(config.database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        if reset:
            _drop_local_tables(connection)
        _ensure_repository_schema(connection)
        if not _table_exists(connection, "performance"):
            _import_performance(connection, config.performance_csv_path)
        if not _table_exists(connection, "cell_traces"):
            _import_safe_traces(connection, config.safe_trace_csv_path)
        connection.execute(_KPI_VIEW)

        performance_rows = int(
            connection.execute("SELECT COUNT(*) FROM performance").fetchone()[0]
        )
        trace_rows = int(
            connection.execute("SELECT COUNT(*) FROM cell_traces").fetchone()[0]
        )
        incident_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM canonical_incidents"
            ).fetchone()[0]
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()

    return DatabaseSummary(
        database_path=config.database_path,
        schema_version=LOCAL_SCHEMA_VERSION,
        performance_rows=performance_rows,
        trace_rows=trace_rows,
        incident_rows=incident_rows,
    )


__all__ = [
    "DatabaseSummary",
    "LOCAL_SCHEMA_VERSION",
    "initialize_database",
]
