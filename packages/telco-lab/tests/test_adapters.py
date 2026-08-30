from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telco_lab.adapters import (
    AdapterError,
    BUBBLERAN_ALERT_ADAPTER_ID,
    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
    adapt_bubbleran_alerts,
    adapt_bubbleran_persistent_interference_csv,
)


BASE_TIME = datetime(2026, 1, 15, 13, 46, 33, tzinfo=UTC)
RAW_UE_ID = "IMSI-310410000000001"


def _headers() -> list[str]:
    return [
        "",
        "timestamp",
        "ran_ue_id",
        "e2node_nb_id",
        *BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
        "timestamp_iso",
        "persistent_anomaly",
    ]


def _row(index: int, label: bool) -> dict[str, object]:
    instant = BASE_TIME + timedelta(seconds=index)
    row: dict[str, object] = {
        "": index,
        "timestamp": int(instant.timestamp()),
        "ran_ue_id": RAW_UE_ID,
        "e2node_nb_id": "50",
        "timestamp_iso": instant.replace(tzinfo=None).isoformat(),
        "persistent_anomaly": str(label),
    }
    row.update(
        {
            source_name: f"{index + metric_index / 100:.2f}"
            for metric_index, source_name in enumerate(
                BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
                start=1,
            )
        }
    )
    return row


def _write_csv(path: Path, labels: list[bool]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_headers(), lineterminator="\n")
        writer.writeheader()
        for index, label in enumerate(labels):
            writer.writerow(_row(index, label))


def _alert(*, anomalous: bool = True) -> dict[str, object]:
    start = BASE_TIME
    end = start + timedelta(seconds=59)
    detected = end + timedelta(seconds=1)
    features = ["mac_ul_bler"] if anomalous else []
    top_features = (
        [
            {
                "feature_id": "mac_ul_bler",
                "reconstruction_error": 1.2,
                "threshold": 0.5,
                "kpi_severity": 2.4,
            }
        ]
        if anomalous
        else []
    )
    return {
        "timestamp_utc": detected.isoformat(),
        "chunk_start": start.replace(tzinfo=None).isoformat(),
        "chunk_end": end.replace(tzinfo=None).isoformat(),
        "model": {"type": "LSTM_Autoencoder", "window_sec": 60},
        "anomaly": {
            "score": 1.2 if anomalous else 0.0,
            "is_anomalous": anomalous,
            "severity_ratio": 0.5 if anomalous else 0.0,
            "violations": len(features),
            "violated_features": features,
        },
        "top_features": top_features,
    }


def test_csv_adapter_separates_features_from_contiguous_truth_and_is_stable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.csv"
    _write_csv(path, [False, True, True, False, True])

    first = adapt_bubbleran_persistent_interference_csv(path)
    second = adapt_bubbleran_persistent_interference_csv(path)

    assert len(first.observations) == 5
    assert [item.sample_count for item in first.ground_truth_episodes] == [2, 1]
    assert all(item.observed_at.tzinfo is UTC for item in first.observations)
    assert first.manifest.bundle_id == second.manifest.bundle_id
    assert [item.observation_id for item in first.observations] == [
        item.observation_id for item in second.observations
    ]
    wire = first.model_dump_json()
    assert RAW_UE_ID not in wire
    assert "ran_ue_id" not in wire
    assert "persistent_anomaly" not in first.observations[0].model_dump()
    assert first.manifest.resource_ids[0].startswith("lab:5g-sa:gnb:")


def test_csv_adapter_omits_blank_metrics_without_emitting_nan(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    rows = [_row(0, False)]
    rows[0]["mac_ul_bler"] = ""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_headers(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    bundle = adapt_bubbleran_persistent_interference_csv(path)
    observation = bundle.observations[0]
    assert "ran.mac.ul_bler" not in observation.metrics
    assert observation.quality_flags == ("MISSING_METRIC_VALUES",)
    assert "NaN" not in bundle.model_dump_json()


@pytest.mark.parametrize("invalid_value", ["NaN", "Infinity", "-Infinity"])
def test_csv_adapter_rejects_non_finite_values(
    tmp_path: Path,
    invalid_value: str,
) -> None:
    path = tmp_path / "invalid.csv"
    row = _row(0, False)
    row["mac_ul_bler"] = invalid_value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_headers(), lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)

    with pytest.raises(AdapterError) as caught:
        adapt_bubbleran_persistent_interference_csv(path)
    assert caught.value.code == "adapter_invalid_input"
    assert invalid_value not in str(caught.value)


def test_csv_adapter_rejects_duplicate_or_repeated_headers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("timestamp,timestamp\n1,1\n", encoding="utf-8")
    with pytest.raises(AdapterError) as duplicate_error:
        adapt_bubbleran_persistent_interference_csv(duplicate)
    assert duplicate_error.value.code == "adapter_invalid_input"

    repeated = tmp_path / "repeated.csv"
    _write_csv(repeated, [False])
    with repeated.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_headers())
    with pytest.raises(AdapterError) as repeated_error:
        adapt_bubbleran_persistent_interference_csv(repeated)
    assert repeated_error.value.code == "adapter_invalid_input"


def test_csv_adapter_rejects_unknown_columns_instead_of_silently_dropping_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unknown-column.csv"
    headers = [*_headers(), "subscriber_note"]
    row = _row(0, False)
    row["subscriber_note"] = "prohibited-free-text"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)

    with pytest.raises(AdapterError) as caught:
        adapt_bubbleran_persistent_interference_csv(path)

    assert caught.value.code == "adapter_unsafe_field"
    assert "prohibited-free-text" not in str(caught.value)


def test_csv_adapter_fails_closed_instead_of_truncating_limits(tmp_path: Path) -> None:
    path = tmp_path / "bounded.csv"
    _write_csv(path, [False, False])

    with pytest.raises(AdapterError) as caught:
        adapt_bubbleran_persistent_interference_csv(path, max_observations=1)
    assert caught.value.code == "adapter_limit_exceeded"

    with pytest.raises(AdapterError) as remote:
        adapt_bubbleran_persistent_interference_csv("https://example.test/data.csv")
    assert remote.value.code == "adapter_unsafe_field"


def test_alert_adapter_outputs_only_explicit_predictions(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, [False])
    resource_id = adapt_bubbleran_persistent_interference_csv(
        csv_path
    ).manifest.resource_ids[0]
    alert_path = tmp_path / "alerts.json"
    alert_path.write_text(
        json.dumps([_alert(anomalous=True), _alert(anomalous=False)]),
        encoding="utf-8",
    )

    first = adapt_bubbleran_alerts(alert_path, resource_id=resource_id)
    second = adapt_bubbleran_alerts(alert_path, resource_id=resource_id)

    assert len(first) == 1
    assert first[0].origin == "PREDICTION"
    assert first[0].detector_id == BUBBLERAN_ALERT_ADAPTER_ID
    assert first[0].features == ("ran.mac.ul_bler",)
    assert first[0].prediction_id == second[0].prediction_id


def test_alert_adapter_rejects_unknown_free_text_and_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, [False])
    resource_id = adapt_bubbleran_persistent_interference_csv(
        csv_path
    ).manifest.resource_ids[0]

    unknown = _alert()
    unknown["message"] = RAW_UE_ID
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(json.dumps([unknown]), encoding="utf-8")
    with pytest.raises(AdapterError) as caught:
        adapt_bubbleran_alerts(unknown_path, resource_id=resource_id)
    assert RAW_UE_ID not in str(caught.value)

    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text('[{"timestamp_utc":"a","timestamp_utc":"b"}]', encoding="utf-8")
    with pytest.raises(AdapterError) as duplicate:
        adapt_bubbleran_alerts(duplicate_path, resource_id=resource_id)
    assert duplicate.value.code == "adapter_invalid_input"
