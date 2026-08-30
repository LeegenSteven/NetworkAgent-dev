from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest

from telco_lab.adapters import (
    BUBBLERAN_ALERT_ADAPTER_ID,
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
)
from telco_lab.catalog import FixtureCatalogProvider
from telco_lab.cli import main as cli_main
from telco_lab.downloader import DownloadReceipt
from telco_lab.errors import LabError
from telco_lab.pipeline import (
    BUBBLERAN_ALERT_RESOURCE_ID,
    BUBBLERAN_ANOMALOUS_RESOURCE_ID,
    BUBBLERAN_CLEAN_RESOURCE_ID,
    BUBBLERAN_RESOURCE_IDS,
    evaluate_cached_bubbleran,
    fetch_and_evaluate_bubbleran,
)
from telco_lab.workspace import TelcoLab


BASE_TIME = datetime(2026, 1, 15, 13, 46, 33, tzinfo=UTC)


def _csv_bytes(*, anomalous: bool) -> bytes:
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
    for index in range(60):
        instant = BASE_TIME + timedelta(seconds=index)
        row: dict[str, object] = {
            "": index,
            "timestamp": int(instant.timestamp()),
            "ran_ue_id": "fixture-ue",
            "e2node_nb_id": "50",
            "timestamp_iso": instant.replace(tzinfo=None).isoformat(),
            "persistent_anomaly": str(anomalous),
        }
        row.update(
            {
                name: f"{index + metric_index / 100:.2f}"
                for metric_index, name in enumerate(
                    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
                    start=1,
                )
            }
        )
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _alert_bytes() -> bytes:
    end = BASE_TIME + timedelta(seconds=59)
    return json.dumps(
        [
            {
                "timestamp_utc": (end + timedelta(seconds=1)).isoformat(),
                "chunk_start": BASE_TIME.replace(tzinfo=None).isoformat(),
                "chunk_end": end.replace(tzinfo=None).isoformat(),
                "model": {"type": "LSTM_Autoencoder", "window_sec": 60},
                "anomaly": {
                    "score": 1.2,
                    "is_anomalous": True,
                    "severity_ratio": 0.5,
                    "violations": 1,
                    "violated_features": ["mac_ul_bler"],
                },
                "top_features": [
                    {
                        "feature_id": "mac_ul_bler",
                        "reconstruction_error": 1.2,
                        "threshold": 0.5,
                        "kpi_severity": 2.4,
                    }
                ],
            }
        ],
        separators=(",", ":"),
    ).encode("utf-8")


def _catalog_and_payloads() -> tuple[FixtureCatalogProvider, dict[str, bytes]]:
    payloads = {
        BUBBLERAN_CLEAN_RESOURCE_ID: _csv_bytes(anomalous=False),
        BUBBLERAN_ANOMALOUS_RESOURCE_ID: _csv_bytes(anomalous=True),
        BUBBLERAN_ALERT_RESOURCE_ID: _alert_bytes(),
    }
    definitions = (
        (BUBBLERAN_CLEAN_RESOURCE_ID, "clean.csv", BUBBLERAN_CSV_ADAPTER_ID),
        (
            BUBBLERAN_ANOMALOUS_RESOURCE_ID,
            "anomalous.csv",
            BUBBLERAN_CSV_ADAPTER_ID,
        ),
        (BUBBLERAN_ALERT_RESOURCE_ID, "alerts.json", BUBBLERAN_ALERT_ADAPTER_ID),
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
    return (
        FixtureCatalogProvider(
            {
                "schema_version": "1.0",
                "catalog_id": "pipeline-fixture",
                "catalog_version": "1.0.0",
                "resources": resources,
            }
        ),
        payloads,
    )


class _MemoryDownloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def download(self, resource, target: Path) -> DownloadReceipt:
        self.calls.append(resource.resource_id)
        body = self.payloads[resource.resource_id]
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


class _FailingDownloader:
    def download(self, _resource, _target: Path) -> DownloadReceipt:
        raise AssertionError("offline evaluation must not access the downloader")


def test_fetch_then_offline_evaluate_is_deterministic_and_privacy_safe(
    tmp_path: Path,
) -> None:
    provider, payloads = _catalog_and_payloads()
    downloader = _MemoryDownloader(payloads)
    lab = TelcoLab(provider, tmp_path, downloader=downloader)  # type: ignore[arg-type]

    first = fetch_and_evaluate_bubbleran(
        lab,
        accepted_license="CC-BY-SA-4.0",
    )
    offline = evaluate_cached_bubbleran(
        TelcoLab(provider, tmp_path, downloader=_FailingDownloader())  # type: ignore[arg-type]
    )

    assert tuple(downloader.calls) == BUBBLERAN_RESOURCE_IDS
    assert first.clean_bundle.manifest.observation_count == 60
    assert first.clean_bundle.manifest.ground_truth_episode_count == 0
    assert first.anomalous_bundle.manifest.ground_truth_episode_count == 1
    assert first.evaluation.true_positives == 1
    assert first.evaluation.false_positives == 0
    assert first.evaluation.false_negatives == 0
    assert first.summary() == offline.summary()
    assert first.summary()["dataset"]["license"] == {
        "id": "CC-BY-SA-4.0",
        "name": "Creative Commons Attribution-ShareAlike 4.0",
        "attribution": "Fixture dataset authors",
        "evidence_sha256": "a" * 64,
        "reviewed_at": "2026-08-30",
    }
    assert first.summary()["dataset"]["lock_id"].startswith("lablock-")
    assert len(first.summary()["dataset"]["artifacts"]) == 3
    assert all(
        len(item["catalog_resource_sha256"]) == 64
        for item in first.summary()["dataset"]["artifacts"]
    )
    assert first.summary()["privacy"]["unknown_columns"] == "REJECT"
    assert first.summary()["privacy"]["excluded_source_value_count"] == 120
    wire = json.dumps(first.summary(), allow_nan=False, sort_keys=True)
    assert "fixture-ue" not in wire
    assert str(tmp_path) not in wire
    assert "fixtures.example.test" not in wire


def test_offline_evaluate_requires_all_verified_artifacts(tmp_path: Path) -> None:
    provider, _payloads = _catalog_and_payloads()

    with pytest.raises(LabError) as caught:
        evaluate_cached_bubbleran(TelcoLab(provider, tmp_path))

    assert caught.value.code == "artifact_unverified"
    assert str(tmp_path) not in str(caught.value)


def test_pipeline_rejects_catalog_provenance_drift_before_download(
    tmp_path: Path,
) -> None:
    provider, payloads = _catalog_and_payloads()
    mapping = provider.load().model_dump(mode="json")
    mapping["resources"][0]["dataset_version"] = "different-version"
    drifted = FixtureCatalogProvider(mapping)
    downloader = _MemoryDownloader(payloads)

    with pytest.raises(LabError) as caught:
        fetch_and_evaluate_bubbleran(
            TelcoLab(drifted, tmp_path, downloader=downloader),  # type: ignore[arg-type]
            accepted_license="CC-BY-SA-4.0",
        )

    assert caught.value.code == "invalid_catalog"
    assert downloader.calls == []


def test_cli_run_then_evaluate_provides_the_same_safe_json_result(
    tmp_path: Path,
) -> None:
    provider, payloads = _catalog_and_payloads()
    downloader = _MemoryDownloader(payloads)
    run_output, run_errors = StringIO(), StringIO()

    run_code = cli_main(
        [
            "--workspace",
            str(tmp_path),
            "run",
            "bubbleran-persistent-interference",
            "--accept-license",
            "CC-BY-SA-4.0",
            "--overlap-threshold",
            "0.1",
        ],
        stdout=run_output,
        stderr=run_errors,
        provider=provider,
        downloader=downloader,  # type: ignore[arg-type]
    )
    offline_output, offline_errors = StringIO(), StringIO()
    offline_code = cli_main(
        [
            "--workspace",
            str(tmp_path),
            "evaluate",
            "bubbleran-persistent-interference",
        ],
        stdout=offline_output,
        stderr=offline_errors,
        provider=provider,
        downloader=_FailingDownloader(),  # type: ignore[arg-type]
    )

    run_payload = json.loads(run_output.getvalue())
    offline_payload = json.loads(offline_output.getvalue())
    assert run_code == offline_code == 0
    assert run_errors.getvalue() == offline_errors.getvalue() == ""
    assert run_payload["mode"] == "fetch-and-evaluate"
    assert offline_payload["mode"] == "offline-cache"
    assert run_payload["execution"]["code_revision"] == "package:0.1.0"
    assert run_payload["execution"]["runtime"]["telco_domain_schema"] == "1.0"
    assert run_payload["execution"]["generated_at"].endswith("+00:00")
    assert run_payload["result"] == offline_payload["result"]
    wire = run_output.getvalue() + offline_output.getvalue()
    assert str(tmp_path) not in wire
    assert "fixtures.example.test" not in wire
    assert "fixture-ue" not in wire


def test_cli_run_rejects_license_before_any_download(tmp_path: Path) -> None:
    provider, payloads = _catalog_and_payloads()
    downloader = _MemoryDownloader(payloads)
    output, errors = StringIO(), StringIO()

    code = cli_main(
        [
            "--workspace",
            str(tmp_path),
            "run",
            "bubbleran-persistent-interference",
            "--accept-license",
            "wrong-token",
        ],
        stdout=output,
        stderr=errors,
        provider=provider,
        downloader=downloader,  # type: ignore[arg-type]
    )

    assert code == 2
    assert output.getvalue() == ""
    assert json.loads(errors.getvalue())["error"]["code"] == "license_not_accepted"
    assert downloader.calls == []
    assert "wrong-token" not in errors.getvalue()
