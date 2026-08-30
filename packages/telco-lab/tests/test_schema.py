from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from telco_domain import Technology
from telco_lab.schema import (
    LabBundle,
    LabBundleManifest,
    LabEpisode,
    LabObservation,
    compute_bundle_content_sha256,
    stable_content_id,
)


NOW = datetime(2026, 1, 15, 13, 46, 33, tzinfo=UTC)
DATASET_VERSION = "fa4e3333855d64474e710bc5bebf11a9ec075e0b"
RESOURCE_ID = "lab:5g-sa:gnb:0123456789abcdef01234567"
SOURCE_SHA = "a" * 64


def _observation(*, value: float = 1.0, observed_at: datetime = NOW) -> LabObservation:
    payload = {
        "dataset_id": "bubbleran-persistent-interference",
        "dataset_version": DATASET_VERSION,
        "source_artifact_sha256": SOURCE_SHA,
        "source_row_number": 2,
        "observed_at": observed_at,
        "resource_id": RESOURCE_ID,
        "technology": Technology.FIVE_G_SA,
        "metrics": {"ran.mac.ul_bler": value},
        "units": {"ran.mac.ul_bler": "ratio"},
        "quality_flags": (),
    }
    identity = {
        **payload,
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "technology": Technology.FIVE_G_SA.value,
    }
    return LabObservation(
        observation_id=stable_content_id("obs", identity),
        **payload,
    )


def _episode(observation: LabObservation) -> LabEpisode:
    payload = {
        "dataset_id": observation.dataset_id,
        "dataset_version": observation.dataset_version,
        "source_artifact_sha256": observation.source_artifact_sha256,
        "resource_id": observation.resource_id,
        "label": "persistent_interference",
        "window_start": observation.observed_at,
        "window_end": observation.observed_at,
        "sample_count": 1,
        "first_observation_id": observation.observation_id,
        "last_observation_id": observation.observation_id,
    }
    return LabEpisode(
        episode_id=stable_content_id("truth", payload),
        **payload,
    )


def _bundle() -> LabBundle:
    observation = _observation()
    episode = _episode(observation)
    content_sha = compute_bundle_content_sha256((observation,), (episode,))
    manifest_payload = {
        "dataset_id": observation.dataset_id,
        "dataset_version": observation.dataset_version,
        "source_artifact_sha256": observation.source_artifact_sha256,
        "source_license": "CC-BY-SA-4.0",
        "adapter_id": "bubbleran_persistent_interference_v1",
        "adapter_version": "1.0",
        "content_hash_algorithm": "sha256-canonical-json-lines-v1",
        "content_sha256": content_sha,
        "observation_count": 1,
        "ground_truth_episode_count": 1,
        "window_start": observation.observed_at,
        "window_end": observation.observed_at,
        "resource_ids": (observation.resource_id,),
        "metric_names": ("ran.mac.ul_bler",),
    }
    manifest = LabBundleManifest(
        bundle_id=stable_content_id("bundle", manifest_payload),
        **manifest_payload,
    )
    return LabBundle(
        manifest=manifest,
        observations=(observation,),
        ground_truth_episodes=(episode,),
    )


def test_observation_is_versioned_utc_safe_and_contains_no_answer_key() -> None:
    shifted = NOW.astimezone(tz=__import__("datetime").timezone(timedelta(hours=8)))
    observation = _observation(observed_at=shifted)

    assert observation.schema_version == "1.0"
    assert observation.observed_at.tzinfo is UTC
    assert "persistent_anomaly" not in LabObservation.model_fields
    assert "ground_truth" not in LabObservation.model_fields
    assert "ran_ue_id" not in LabObservation.model_fields


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_observation_rejects_non_finite_metrics(value: float) -> None:
    payload = _observation().model_dump()
    payload["metrics"]["ran.mac.ul_bler"] = value
    with pytest.raises(ValidationError):
        LabObservation.model_validate(payload)


def test_observation_rejects_naive_time_and_subscriber_fields() -> None:
    with pytest.raises(ValidationError):
        _observation(observed_at=NOW.replace(tzinfo=None))

    payload = _observation().model_dump()
    payload["ran_ue_id"] = "IMSI-310410000000001"
    with pytest.raises(ValidationError):
        LabObservation.model_validate(payload)


def test_ground_truth_requires_exact_one_second_contiguity() -> None:
    observation = _observation()
    payload = _episode(observation).model_dump()
    payload["window_end"] = NOW + timedelta(seconds=2)
    payload["sample_count"] = 2

    with pytest.raises(ValidationError, match="one-second contiguous"):
        LabEpisode.model_validate(payload)


def test_bundle_manifest_binds_counts_scope_checksum_and_content_id() -> None:
    bundle = _bundle()
    assert bundle.manifest.observation_count == 1
    assert bundle.manifest.ground_truth_episode_count == 1

    tampered = bundle.model_dump()
    tampered["manifest"]["content_sha256"] = "b" * 64
    with pytest.raises(ValidationError, match="content checksum"):
        LabBundle.model_validate(tampered)
