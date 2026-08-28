from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import duckdb
import pytest

from telco_domain.models import Incident
from telco_local.config import LocalProfileConfig
from telco_local.database import LOCAL_SCHEMA_VERSION, initialize_database
from telco_local.incident_repository import DuckDbIncidentRepository
from telco_local.lte_identifiers import LTE_IDENTIFIER_MAX


def _replace_first_csv_value(
    path: Path,
    *,
    column: str,
    value: str,
) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fieldnames = tuple(reader.fieldnames or ())
    rows[0][column] = value
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_local_profile_requires_explicit_utc_and_resolves_paths(tmp_path: Path) -> None:
    kwargs = {
        "database_path": tmp_path / "db.duckdb",
        "performance_csv_path": tmp_path / "performance.csv",
        "safe_trace_csv_path": tmp_path / "traces.csv",
        "rules_dir": tmp_path / "rules",
    }
    config = LocalProfileConfig(**kwargs, source_timezone="UTC")
    assert config.source_timezone == "UTC"
    assert config.database_path.is_absolute()
    assert config.performance_csv_path.is_absolute()
    assert config.safe_trace_csv_path.is_absolute()
    assert config.rules_dir.is_absolute()

    with pytest.raises(ValueError, match="UTC"):
        LocalProfileConfig(**kwargs, source_timezone="Asia/Shanghai")


def test_initialize_database_builds_kpis_and_imports_only_safe_trace_columns(
    local_config,
) -> None:
    summary = initialize_database(local_config)

    assert summary.schema_version == LOCAL_SCHEMA_VERSION
    assert summary.performance_rows == 2
    assert summary.trace_rows == 2
    assert summary.incident_rows == 0
    assert summary.database_path == local_config.database_path

    with duckdb.connect(str(local_config.database_path), read_only=True) as connection:
        first = connection.execute(
            """
            SELECT erab_success_rate, retainability, uplink_rssi_avg,
                   measurement_end
            FROM performance_kpi
            ORDER BY measurement_end
            LIMIT 1
            """
        ).fetchone()
        assert first is not None
        assert first[0] == pytest.approx(430 * 100 / 431)
        assert first[1] == pytest.approx(9 * 3600 / 20907)
        assert first[2] == -100.0
        assert first[3].tzinfo is not None

        columns = {
            row[1].lower(): row[2]
            for row in connection.execute("PRAGMA table_info('cell_traces')").fetchall()
        }
        assert set(columns) == {
            "procedure_type",
            "starttime",
            "endtime",
            "start_enodeb_id",
            "start_cell_id",
            "s1_sig_conn_setup_sig_conn_result",
        }
        assert {"imsi", "msisdn", "imei", "imeisv", "supi"}.isdisjoint(columns)
        assert "TIME ZONE" in columns["starttime"].upper()
        assert "TIME ZONE" in columns["endtime"].upper()

    database_bytes = local_config.database_path.read_bytes()
    for raw_identifier in (
        b"310410000000001",
        b"19724123000001",
        b"19725551045",
    ):
        assert raw_identifier not in database_bytes


def test_performance_import_is_an_explicit_privacy_allowlist(local_config) -> None:
    """Extra source columns must never cross the DuckDB import boundary."""

    with local_config.performance_csv_path.open(
        newline="", encoding="utf-8"
    ) as stream:
        rows = list(csv.DictReader(stream))
        original_fields = list(rows[0])
    for index, row in enumerate(rows):
        row["IMSI"] = f"IMSI-3104100000000{index}"
        row["arbitrary_private_blob"] = f"do-not-store-{index}"
    with local_config.performance_csv_path.open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[*original_fields, "IMSI", "arbitrary_private_blob"],
        )
        writer.writeheader()
        writer.writerows(rows)

    initialize_database(local_config)

    expected_columns = {
        "enodeb_id",
        "cell_id",
        "measurement_end",
        "erab_sessiontimeue",
        "ul_rssi",
        *{
            f"erab_{family}_qci{qci}"
            for family in (
                "estabinitattnbr",
                "estabinitsuccnbr",
                "relactnbr",
            )
            for qci in range(1, 10)
        },
    }
    with duckdb.connect(str(local_config.database_path), read_only=True) as connection:
        actual_columns = {
            str(row[1]).lower()
            for row in connection.execute(
                "PRAGMA table_info('performance')"
            ).fetchall()
        }
        assert actual_columns == expected_columns
        assert connection.execute(
            "SELECT COUNT(*) FROM performance_kpi"
        ).fetchone()[0] == 2

    database_bytes = local_config.database_path.read_bytes()
    assert b"3104100000000" not in database_bytes
    assert b"do-not-store" not in database_bytes


def test_trace_import_normalizes_untrusted_outcomes_before_storage(
    local_config,
) -> None:
    malicious = "IMSI-310410000000009"
    with local_config.safe_trace_csv_path.open(
        "a", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "PROCEDURE_TYPE",
                "STARTTIME",
                "ENDTIME",
                "IMSI",
                "IMEISV",
                "MSISDN",
                "START_ENODEB_ID",
                "START_CELL_ID",
                "S1_SIG_CONN_SETUP_SIG_CONN_RESULT",
            ],
        )
        writer.writerow(
            {
                "PROCEDURE_TYPE": "UNTRUSTED",
                "STARTTIME": "11/20/2025 00:00:10",
                "ENDTIME": "11/20/2025 00:00:11",
                "IMSI": "310410000000009",
                "IMEISV": "19724123000009",
                "MSISDN": "19725551049",
                "START_ENODEB_ID": "1",
                "START_CELL_ID": "12314",
                "S1_SIG_CONN_SETUP_SIG_CONN_RESULT": malicious,
            }
        )

    initialize_database(local_config)
    with duckdb.connect(str(local_config.database_path), read_only=True) as connection:
        outcomes = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT s1_sig_conn_setup_sig_conn_result FROM cell_traces"
            ).fetchall()
        }
    assert outcomes == {"FAILED_SECURITY_SETUP", "OTHER", "SUCCESS"}
    assert malicious.encode() not in local_config.database_path.read_bytes()


def test_initialize_is_idempotent_and_reset_is_explicit(local_config) -> None:
    first = initialize_database(local_config)
    repository = DuckDbIncidentRepository(local_config)
    incident = Incident(incident_id="reset-test", trace_id="trace-reset")
    asyncio.run(
        repository.create(
            incident,
            idempotency_key="reset-create",
            actor="detector-agent",
            reason="reset behavior test",
            trace_id=incident.trace_id,
        )
    )
    second = initialize_database(local_config)
    reset = initialize_database(local_config, reset=True)

    assert first.incident_rows == 0
    assert second.incident_rows == 1
    assert reset.performance_rows == 2
    assert reset.trace_rows == 2
    assert reset.incident_rows == 0
    with duckdb.connect(str(local_config.database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_incident_audit"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_incident_idempotency"
        ).fetchone()[0] == 0


def test_initialize_rejects_missing_inputs_without_creating_database(tmp_path: Path) -> None:
    config = LocalProfileConfig(
        database_path=tmp_path / "missing.duckdb",
        performance_csv_path=tmp_path / "missing-performance.csv",
        safe_trace_csv_path=tmp_path / "missing-traces.csv",
        rules_dir=tmp_path / "missing-rules",
        source_timezone="UTC",
    )

    with pytest.raises(FileNotFoundError):
        initialize_database(config)
    assert not config.database_path.exists()


@pytest.mark.parametrize(
    ("source_name", "source_column", "table_name", "stored_column"),
    (
        (
            "performance_csv_path",
            "EnodeB_id",
            "performance",
            "enodeb_id",
        ),
        (
            "safe_trace_csv_path",
            "START_CELL_ID",
            "cell_traces",
            "start_cell_id",
        ),
    ),
)
@pytest.mark.parametrize(
    "invalid_identifier",
    (
        "310410000000001",
        "000000000000001",
        "-1",
        "1e3",
        str(LTE_IDENTIFIER_MAX + 1),
    ),
)
def test_import_rejects_non_lte_identifiers_without_echo_and_rolls_back_reset(
    local_config,
    source_name: str,
    source_column: str,
    table_name: str,
    stored_column: str,
    invalid_identifier: str,
) -> None:
    baseline = initialize_database(local_config)
    source_path = getattr(local_config, source_name)
    _replace_first_csv_value(
        source_path,
        column=source_column,
        value=invalid_identifier,
    )

    with pytest.raises(ValueError) as error:
        initialize_database(local_config, reset=True)
    assert invalid_identifier not in str(error.value)

    with duckdb.connect(
        str(local_config.database_path), read_only=True
    ) as connection:
        assert connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0] == (
            baseline.performance_rows
            if table_name == "performance"
            else baseline.trace_rows
        )
        stored_values = {
            str(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT {stored_column} FROM {table_name}"
            ).fetchall()
        }
    assert invalid_identifier not in stored_values


def test_import_accepts_28_bit_boundaries_and_normalizes_leading_zeroes(
    local_config,
) -> None:
    _replace_first_csv_value(
        local_config.performance_csv_path,
        column="EnodeB_id",
        value=str(LTE_IDENTIFIER_MAX),
    )
    _replace_first_csv_value(
        local_config.performance_csv_path,
        column="cell_id",
        value="000000000",
    )
    _replace_first_csv_value(
        local_config.safe_trace_csv_path,
        column="START_ENODEB_ID",
        value=str(LTE_IDENTIFIER_MAX),
    )
    _replace_first_csv_value(
        local_config.safe_trace_csv_path,
        column="START_CELL_ID",
        value="000000000",
    )

    initialize_database(local_config)
    with duckdb.connect(
        str(local_config.database_path), read_only=True
    ) as connection:
        performance_identity = connection.execute(
            """
            SELECT enodeb_id, cell_id
            FROM performance
            ORDER BY measurement_end
            LIMIT 1
            """
        ).fetchone()
        trace_identity = connection.execute(
            """
            SELECT start_enodeb_id, start_cell_id
            FROM cell_traces
            ORDER BY starttime
            LIMIT 1
            """
        ).fetchone()

    expected = (str(LTE_IDENTIFIER_MAX), "0")
    assert performance_identity == expected
    assert trace_identity == expected
