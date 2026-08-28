from __future__ import annotations

import csv
from pathlib import Path

import pytest


def _performance_row(
    *,
    enodeb_id: str,
    cell_id: str,
    measurement_end: str,
    attempts: int,
    successes: int,
    releases: int,
    session_time: int,
    uplink_rssi: float,
) -> dict[str, object]:
    row: dict[str, object] = {
        "EnodeB_id": enodeb_id,
        "cell_id": cell_id,
        "measurement_end": measurement_end,
        "ERAB_SessionTimeUE": session_time,
        "UL_RSSI": uplink_rssi,
    }
    for qci in range(1, 10):
        row[f"ERAB_EstabInitAttNbr_QCI{qci}"] = attempts if qci == 1 else 0
        row[f"ERAB_EstabInitSuccNbr_QCI{qci}"] = successes if qci == 1 else 0
        row[f"ERAB_RelActNbr_QCI{qci}"] = releases if qci == 1 else 0
    return row


@pytest.fixture()
def local_config(tmp_path: Path):
    from telco_local.config import LocalProfileConfig

    performance_path = tmp_path / "performance.csv"
    trace_path = tmp_path / "cell-traces.csv"
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "test-rule.json").write_text("{}", encoding="utf-8")

    performance_rows = [
        _performance_row(
            enodeb_id="1",
            cell_id="12314",
            measurement_end="11/20/2025 00:00:00",
            attempts=431,
            successes=430,
            releases=9,
            session_time=20907,
            uplink_rssi=-100.0,
        ),
        _performance_row(
            enodeb_id="2",
            cell_id="22414",
            measurement_end="11/20/2025 00:15:00",
            attempts=100,
            successes=90,
            releases=1,
            session_time=1000,
            uplink_rssi=-121.0,
        ),
    ]
    with performance_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(performance_rows[0]))
        writer.writeheader()
        writer.writerows(performance_rows)

    with trace_path.open("w", newline="", encoding="utf-8") as stream:
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
        writer.writeheader()
        writer.writerows(
            [
                {
                    "PROCEDURE_TYPE": "INITIAL_ATTACH",
                    "STARTTIME": "11/20/2025 00:00:00",
                    "ENDTIME": "11/20/2025 00:00:05",
                    "IMSI": "310410000000001",
                    "IMEISV": "19724123000001",
                    "MSISDN": "19725551045",
                    "START_ENODEB_ID": "1",
                    "START_CELL_ID": "12314",
                    "S1_SIG_CONN_SETUP_SIG_CONN_RESULT": "FAILED_SECURITY_SETUP",
                },
                {
                    "PROCEDURE_TYPE": "RRC_SETUP",
                    "STARTTIME": "11/20/2025 00:00:05",
                    "ENDTIME": "11/20/2025 00:00:10",
                    "IMSI": "310410000000002",
                    "IMEISV": "19724123000002",
                    "MSISDN": "19725551046",
                    "START_ENODEB_ID": "1",
                    "START_CELL_ID": "12314",
                    "S1_SIG_CONN_SETUP_SIG_CONN_RESULT": "SUCCESS",
                },
            ]
        )

    return LocalProfileConfig(
        database_path=tmp_path / "local.duckdb",
        performance_csv_path=performance_path,
        safe_trace_csv_path=trace_path,
        rules_dir=rules_dir,
        source_timezone="UTC",
    )


@pytest.fixture()
def initialized_config(local_config):
    from telco_local.database import initialize_database

    initialize_database(local_config)
    return local_config
