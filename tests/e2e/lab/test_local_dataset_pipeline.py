from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from telco_lab import (
    BUBBLERAN_ALERT_ADAPTER_ID,
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
    BUBBLERAN_RESOURCE_IDS,
    DownloadReceipt,
    FixtureCatalogProvider,
    TelcoLab,
)
from telco_lab.cli import main as cli_main


BASE_TIME = datetime(2026, 1, 15, 13, 46, 33, tzinfo=UTC)


def _csv_payload(*, anomalous: bool) -> bytes:
    headers = [
        "",
        "timestamp",
        "ran_ue_id",
        "e2node_nb_id",
        *BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
        "timestamp_iso",
        "persistent_anomaly",
    ]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    row: dict[str, object] = {
        "": 0,
        "timestamp": int(BASE_TIME.timestamp()),
        "ran_ue_id": "local-fixture",
        "e2node_nb_id": "50",
        "timestamp_iso": BASE_TIME.replace(tzinfo=None).isoformat(),
        "persistent_anomaly": str(anomalous),
    }
    row.update(
        {
            name: f"{index / 100:.2f}"
            for index, name in enumerate(
                BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
                start=1,
            )
        }
    )
    writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _alert_payload() -> bytes:
    end = BASE_TIME + timedelta(seconds=59)
    return json.dumps(
        [
            {
                "timestamp_utc": (end + timedelta(seconds=1)).isoformat(),
                "chunk_start": BASE_TIME.replace(tzinfo=None).isoformat(),
                "chunk_end": end.replace(tzinfo=None).isoformat(),
                "model": {"type": "LSTM_Autoencoder", "window_sec": 60},
                "anomaly": {
                    "score": 1.0,
                    "is_anomalous": True,
                    "severity_ratio": 1.0,
                    "violations": 1,
                    "violated_features": ["mac_ul_bler"],
                },
                "top_features": [
                    {
                        "feature_id": "mac_ul_bler",
                        "reconstruction_error": 1.0,
                        "threshold": 0.5,
                        "kpi_severity": 2.0,
                    }
                ],
            }
        ],
        separators=(",", ":"),
    ).encode("utf-8")


def _fixture_catalog():
    payloads = {
        BUBBLERAN_RESOURCE_IDS[0]: _csv_payload(anomalous=False),
        BUBBLERAN_RESOURCE_IDS[1]: _csv_payload(anomalous=True),
        BUBBLERAN_RESOURCE_IDS[2]: _alert_payload(),
    }
    definitions = (
        (BUBBLERAN_RESOURCE_IDS[0], "clean.csv", BUBBLERAN_CSV_ADAPTER_ID),
        (BUBBLERAN_RESOURCE_IDS[1], "anomalous.csv", BUBBLERAN_CSV_ADAPTER_ID),
        (BUBBLERAN_RESOURCE_IDS[2], "alerts.json", BUBBLERAN_ALERT_ADAPTER_ID),
    )
    resources = []
    for resource_id, filename, adapter in definitions:
        body = payloads[resource_id]
        resources.append(
            {
                "resource_id": resource_id,
                "dataset_id": BUBBLERAN_DATASET_ID,
                "dataset_version": BUBBLERAN_DATASET_VERSION,
                "filename": filename,
                "source_url": f"https://fixtures.example.test/{filename}",
                "allowed_hosts": ["fixtures.example.test"],
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "media_type": (
                    "application/json" if filename.endswith(".json") else "text/csv"
                ),
                "adapter": adapter,
                "license": {
                    "id": "CC-BY-SA-4.0",
                    "name": "Creative Commons Attribution-ShareAlike 4.0",
                    "url": "https://creativecommons.org/licenses/by-sa/4.0/",
                    "evidence_url": "https://fixtures.example.test/LICENSE",
                    "evidence_sha256": "a" * 64,
                    "attribution": "Fixture dataset authors",
                    "reviewed_at": "2026-08-30",
                    "acceptance_required": True,
                },
            }
        )
    return FixtureCatalogProvider(
        {
            "schema_version": "1.0",
            "catalog_id": "e2e-fixture",
            "catalog_version": "1.0.0",
            "resources": resources,
        }
    ), payloads


class _Downloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def download(self, resource, target: Path) -> DownloadReceipt:
        body = self._payloads[resource.resource_id]
        cached = target.is_file() and target.read_bytes() == body
        if not cached:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return DownloadReceipt(
            resource_id=resource.resource_id,
            filename=resource.filename,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            cached=cached,
        )


def test_one_command_fetch_evaluate_and_offline_replay(tmp_path: Path) -> None:
    provider, payloads = _fixture_catalog()
    downloader = _Downloader(payloads)
    first_output, first_error = StringIO(), StringIO()

    first_code = cli_main(
        [
            "--workspace",
            str(tmp_path),
            "run",
            "bubbleran-persistent-interference",
            "--accept-license",
            "CC-BY-SA-4.0",
            "--overlap-threshold",
            "0.01",
        ],
        provider=provider,
        downloader=downloader,  # type: ignore[arg-type]
        stdout=first_output,
        stderr=first_error,
    )
    replay_output, replay_error = StringIO(), StringIO()
    replay_code = cli_main(
        [
            "--workspace",
            str(tmp_path),
            "evaluate",
            "bubbleran-persistent-interference",
            "--overlap-threshold",
            "0.01",
        ],
        provider=provider,
        downloader=downloader,  # type: ignore[arg-type]
        stdout=replay_output,
        stderr=replay_error,
    )

    first = json.loads(first_output.getvalue())
    replay = json.loads(replay_output.getvalue())
    assert first_code == replay_code == 0
    assert first_error.getvalue() == replay_error.getvalue() == ""
    assert TelcoLab(
        provider,
        tmp_path,
        downloader=downloader,  # type: ignore[arg-type]
    ).verify().valid
    assert first["result"] == replay["result"]
    assert first["result"]["evaluation"]["true_positives"] == 1
    assert first["result"]["clean"]["ground_truth_episode_count"] == 0
    output_wire = first_output.getvalue() + replay_output.getvalue()
    assert str(tmp_path) not in output_wire
    assert "fixtures.example.test" not in output_wire
    assert "local-fixture" not in output_wire
