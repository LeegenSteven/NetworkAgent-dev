from __future__ import annotations

import os
import hashlib
from pathlib import Path

import pytest

from telco_lab import (
    adapt_bubbleran_alerts,
    adapt_bubbleran_persistent_interference_csv,
    evaluate_episodes,
    PackageCatalogProvider,
)
from telco_lab.pipeline import (
    BUBBLERAN_ALERT_RESOURCE_ID,
    BUBBLERAN_ANOMALOUS_RESOURCE_ID,
    BUBBLERAN_CLEAN_RESOURCE_ID,
)


DATA_DIRECTORY_ENV = "TELCO_LAB_BUBBLERAN_DATA_DIR"


def _data_directory() -> Path:
    raw = os.environ.get(DATA_DIRECTORY_ENV)
    if not raw:
        pytest.skip(f"{DATA_DIRECTORY_ENV} is not set")
    return Path(raw)


def _assert_pinned_artifact(path: Path, resource_id: str) -> None:
    resource = PackageCatalogProvider().load().resource(resource_id)
    assert resource is not None
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(256 * 1024):
            size += len(block)
            digest.update(block)
    assert size == resource.size_bytes
    assert digest.hexdigest() == resource.sha256
    assert resource.license.evidence_sha256 == (
        "a25b2415e77fbec63d46ddf10c638218cffdcf63875386c59e766f4fba59897a"
    )


def test_pinned_bubbleran_full_dataset_reproduces_the_reference_baseline() -> None:
    directory = _data_directory()
    anomalous_path = directory / "anomalous_data.csv"
    clean_path = directory / "clean_data.csv"
    alert_path = directory / "alerts_predicted.json"
    _assert_pinned_artifact(anomalous_path, BUBBLERAN_ANOMALOUS_RESOURCE_ID)
    _assert_pinned_artifact(clean_path, BUBBLERAN_CLEAN_RESOURCE_ID)
    _assert_pinned_artifact(alert_path, BUBBLERAN_ALERT_RESOURCE_ID)
    anomalous = adapt_bubbleran_persistent_interference_csv(
        anomalous_path
    )
    clean = adapt_bubbleran_persistent_interference_csv(clean_path)
    assert anomalous.manifest.resource_ids == clean.manifest.resource_ids
    predictions = adapt_bubbleran_alerts(
        alert_path,
        resource_id=anomalous.manifest.resource_ids[0],
    )
    evaluation = evaluate_episodes(
        anomalous.ground_truth_episodes,
        predictions,
        overlap_threshold=0.1,
    )

    assert anomalous.manifest.observation_count == 1_597
    assert anomalous.manifest.ground_truth_episode_count == 5
    assert clean.manifest.observation_count == 3_601
    assert clean.manifest.ground_truth_episode_count == 0
    assert len(predictions) == 55
    assert (
        evaluation.true_positives,
        evaluation.false_positives,
        evaluation.false_negatives,
    ) == (5, 50, 0)
    assert evaluation.precision == pytest.approx(5 / 55)
    assert evaluation.recall == 1.0
