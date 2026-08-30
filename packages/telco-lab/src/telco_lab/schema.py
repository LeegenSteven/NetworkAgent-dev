"""Versioned, privacy-safe contracts for reproducible local dataset tests.

The lab contracts deliberately separate detector inputs, dataset ground truth,
and detector predictions.  In particular, :class:`LabObservation` has no label
field: adapters may use an upstream label to build :class:`LabEpisode` objects,
but that label cannot accidentally enter the detector feature payload.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Set
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Literal, Self, Sequence

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from telco_domain import Technology, assert_model_safe


LAB_SCHEMA_VERSION = "1.0"
LAB_BUNDLE_HASH_ALGORITHM = "sha256-canonical-json-lines-v1"

MAX_METRICS_PER_OBSERVATION = 64
MAX_QUALITY_FLAGS = 8
MAX_EPISODE_FEATURES = 32
MAX_BUNDLE_OBSERVATIONS = 50_000
MAX_BUNDLE_EPISODES = 10_000
MAX_BUNDLE_RESOURCES = 1_000
MAX_OBSERVATION_SERIALIZED_BYTES = 32 * 1024
MAX_EPISODE_SERIALIZED_BYTES = 16 * 1024
MAX_MANIFEST_SERIALIZED_BYTES = 64 * 1024
MAX_BUNDLE_SERIALIZED_BYTES = 64 * 1024 * 1024

SchemaVersion = Literal["1.0"]
EpisodeLabel = Literal["persistent_interference"]
Sha256Digest = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]
ContentId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^lab(?:obs|truth|pred|bundle|eval)-[0-9a-f]{64}$",
        max_length=96,
    ),
]
SafeName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$",
    ),
]
MetricName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:[._][a-z0-9]+)*$",
    ),
]
MetricUnit = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=32,
        pattern=r"^[a-zA-Z][a-zA-Z0-9._/-]*$",
    ),
]
LabResourceId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=(
            r"^lab:(?:lte|5g-nsa|5g-sa):"
            r"(?:enodeb|gnb|cell):[0-9a-f]{24}$"
        ),
        max_length=80,
    ),
]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include a timezone")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetime values must include a timezone")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        return sorted((_json_value(item) for item in value), key=repr)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-safe value in the stable lab wire representation."""

    return json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def stable_content_id(prefix: Literal["obs", "truth", "pred", "bundle", "eval"], value: Any) -> str:
    """Return a domain-separated SHA-256 content identifier."""

    digest = hashlib.sha256()
    digest.update(f"telco-lab:{LAB_SCHEMA_VERSION}:{prefix}\0".encode("ascii"))
    digest.update(canonical_json_bytes(value))
    return f"lab{prefix}-{digest.hexdigest()}"


class LabModel(BaseModel):
    """Strict immutable base model with the shared privacy boundary."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
        revalidate_instances="always",
        str_strip_whitespace=True,
        validate_default=True,
    )

    @model_validator(mode="after")
    def _privacy_safe(self) -> Self:
        assert_model_safe(self.model_dump(mode="python"))
        return self


class LabObservation(LabModel):
    """One detector-safe feature row; upstream labels are intentionally absent."""

    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    observation_id: ContentId
    dataset_id: SafeName
    dataset_version: SafeName
    source_artifact_sha256: Sha256Digest
    source_row_number: int = Field(ge=2, le=10_000_001)
    observed_at: UtcDatetime
    resource_id: LabResourceId
    technology: Technology
    metrics: dict[MetricName, float] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )
    units: dict[MetricName, MetricUnit] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )
    quality_flags: tuple[SafeName, ...] = Field(
        default=(),
        max_length=MAX_QUALITY_FLAGS,
    )

    @model_validator(mode="after")
    def _validate_metric_projection(self) -> Self:
        if set(self.metrics) != set(self.units):
            raise ValueError("metric values and units must have identical names")
        if tuple(self.quality_flags) != tuple(sorted(set(self.quality_flags))):
            raise ValueError("quality_flags must be unique and sorted")
        if len(canonical_json_bytes(self)) > MAX_OBSERVATION_SERIALIZED_BYTES:
            raise ValueError("lab observation exceeds its serialized size budget")
        identity = self.model_dump(
            mode="python",
            exclude={"schema_version", "observation_id"},
        )
        if stable_content_id("obs", identity) != self.observation_id:
            raise ValueError("observation content identifier does not match content")
        return self


class LabEpisode(LabModel):
    """One contiguous ground-truth anomaly interval from an upstream label."""

    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    episode_id: ContentId
    origin: Literal["GROUND_TRUTH"] = "GROUND_TRUTH"
    dataset_id: SafeName
    dataset_version: SafeName
    source_artifact_sha256: Sha256Digest
    resource_id: LabResourceId
    label: EpisodeLabel = "persistent_interference"
    window_start: UtcDatetime
    window_end: UtcDatetime
    sample_count: int = Field(ge=1, le=MAX_BUNDLE_OBSERVATIONS)
    first_observation_id: ContentId
    last_observation_id: ContentId

    @model_validator(mode="after")
    def _validate_contiguous_window(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("episode window_end must not precede window_start")
        expected = int((self.window_end - self.window_start).total_seconds()) + 1
        if expected != self.sample_count:
            raise ValueError("ground-truth episode samples must be one-second contiguous")
        if len(canonical_json_bytes(self)) > MAX_EPISODE_SERIALIZED_BYTES:
            raise ValueError("lab episode exceeds its serialized size budget")
        identity = self.model_dump(
            mode="python",
            exclude={"schema_version", "episode_id", "origin"},
        )
        if stable_content_id("truth", identity) != self.episode_id:
            raise ValueError("episode content identifier does not match content")
        return self


class PredictedEpisode(LabModel):
    """A detector-produced interval, kept type-distinct from ground truth."""

    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    prediction_id: ContentId
    origin: Literal["PREDICTION"] = "PREDICTION"
    dataset_id: SafeName
    dataset_version: SafeName
    source_artifact_sha256: Sha256Digest
    source_item_number: int = Field(ge=1, le=1_000_000)
    resource_id: LabResourceId
    label: EpisodeLabel = "persistent_interference"
    window_start: UtcDatetime
    window_end: UtcDatetime
    detected_at: UtcDatetime
    detector_id: SafeName
    score: float = Field(ge=0)
    features: tuple[MetricName, ...] = Field(
        default=(),
        max_length=MAX_EPISODE_FEATURES,
    )

    @model_validator(mode="after")
    def _validate_prediction(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("prediction window_end must not precede window_start")
        if self.detected_at < self.window_end:
            raise ValueError("prediction detected_at must not precede window_end")
        if self.features != tuple(sorted(set(self.features))):
            raise ValueError("prediction features must be unique and sorted")
        if len(canonical_json_bytes(self)) > MAX_EPISODE_SERIALIZED_BYTES:
            raise ValueError("predicted episode exceeds its serialized size budget")
        identity = self.model_dump(
            mode="python",
            exclude={"schema_version", "prediction_id", "origin"},
        )
        if stable_content_id("pred", identity) != self.prediction_id:
            raise ValueError("prediction content identifier does not match content")
        return self


class LabBundleManifest(LabModel):
    """Small provenance manifest for one deterministic adapter output bundle."""

    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    bundle_id: ContentId
    dataset_id: SafeName
    dataset_version: SafeName
    source_artifact_sha256: Sha256Digest
    source_license: SafeName
    adapter_id: SafeName
    adapter_version: SafeName
    content_hash_algorithm: Literal[
        "sha256-canonical-json-lines-v1"
    ] = LAB_BUNDLE_HASH_ALGORITHM
    content_sha256: Sha256Digest
    observation_count: int = Field(ge=1, le=MAX_BUNDLE_OBSERVATIONS)
    ground_truth_episode_count: int = Field(ge=0, le=MAX_BUNDLE_EPISODES)
    window_start: UtcDatetime
    window_end: UtcDatetime
    resource_ids: tuple[LabResourceId, ...] = Field(
        min_length=1,
        max_length=MAX_BUNDLE_RESOURCES,
    )
    metric_names: tuple[MetricName, ...] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )

    @model_validator(mode="after")
    def _validate_manifest(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("manifest window_end must not precede window_start")
        if self.resource_ids != tuple(sorted(set(self.resource_ids))):
            raise ValueError("manifest resource_ids must be unique and sorted")
        if self.metric_names != tuple(sorted(set(self.metric_names))):
            raise ValueError("manifest metric_names must be unique and sorted")
        if len(canonical_json_bytes(self)) > MAX_MANIFEST_SERIALIZED_BYTES:
            raise ValueError("lab manifest exceeds its serialized size budget")
        return self

    def identity_payload(self) -> dict[str, Any]:
        """Return the immutable manifest fields covered by ``bundle_id``."""

        return self.model_dump(
            mode="python",
            # The schema version is already domain-separated by
            # ``stable_content_id`` and is therefore not duplicated here.
            exclude={"bundle_id", "schema_version"},
        )


def compute_bundle_content_sha256(
    observations: Sequence[LabObservation],
    ground_truth_episodes: Sequence[LabEpisode],
) -> str:
    """Hash ordered bundle items without constructing one giant JSON value."""

    digest = hashlib.sha256()
    digest.update(f"telco-lab:{LAB_SCHEMA_VERSION}:bundle-content\0".encode("ascii"))
    for kind, items in (
        (b"observation\0", observations),
        (b"ground-truth\0", ground_truth_episodes),
    ):
        for item in items:
            digest.update(kind)
            digest.update(canonical_json_bytes(item))
            digest.update(b"\n")
    return digest.hexdigest()


class LabBundle(LabModel):
    """A bounded in-memory fixture with features and separately held labels."""

    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    manifest: LabBundleManifest
    observations: tuple[LabObservation, ...] = Field(
        min_length=1,
        max_length=MAX_BUNDLE_OBSERVATIONS,
    )
    ground_truth_episodes: tuple[LabEpisode, ...] = Field(
        default=(),
        max_length=MAX_BUNDLE_EPISODES,
    )

    @model_validator(mode="after")
    def _validate_bundle(self) -> Self:
        manifest = self.manifest
        if manifest.observation_count != len(self.observations):
            raise ValueError("manifest observation count does not match bundle")
        if manifest.ground_truth_episode_count != len(self.ground_truth_episodes):
            raise ValueError("manifest episode count does not match bundle")

        observation_ids = tuple(item.observation_id for item in self.observations)
        episode_ids = tuple(item.episode_id for item in self.ground_truth_episodes)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("bundle observations must have unique content identifiers")
        if len(episode_ids) != len(set(episode_ids)):
            raise ValueError("bundle episodes must have unique content identifiers")

        dataset_scope = (manifest.dataset_id, manifest.dataset_version)
        if any(
            (item.dataset_id, item.dataset_version) != dataset_scope
            or item.source_artifact_sha256 != manifest.source_artifact_sha256
            for item in (*self.observations, *self.ground_truth_episodes)
        ):
            raise ValueError("bundle items must match manifest provenance")

        resources = tuple(sorted({item.resource_id for item in self.observations}))
        metric_names = tuple(
            sorted({name for item in self.observations for name in item.metrics})
        )
        if resources != manifest.resource_ids or metric_names != manifest.metric_names:
            raise ValueError("bundle projection does not match manifest scope")

        if min(item.observed_at for item in self.observations) != manifest.window_start:
            raise ValueError("bundle start does not match manifest")
        if max(item.observed_at for item in self.observations) != manifest.window_end:
            raise ValueError("bundle end does not match manifest")

        expected_content = compute_bundle_content_sha256(
            self.observations,
            self.ground_truth_episodes,
        )
        if expected_content != manifest.content_sha256:
            raise ValueError("bundle content checksum does not match manifest")
        if stable_content_id("bundle", manifest.identity_payload()) != manifest.bundle_id:
            raise ValueError("bundle content identifier does not match manifest")

        ordered_episodes = sorted(
            self.ground_truth_episodes,
            key=lambda item: (item.resource_id, item.label, item.window_start),
        )
        for previous, current in zip(ordered_episodes, ordered_episodes[1:]):
            if (
                previous.resource_id == current.resource_id
                and previous.label == current.label
                and current.window_start <= previous.window_end + timedelta(seconds=1)
            ):
                raise ValueError("ground-truth episodes must be maximally contiguous")

        total_bytes = len(canonical_json_bytes(manifest))
        total_bytes += sum(len(canonical_json_bytes(item)) for item in self.observations)
        total_bytes += sum(
            len(canonical_json_bytes(item)) for item in self.ground_truth_episodes
        )
        if total_bytes > MAX_BUNDLE_SERIALIZED_BYTES:
            raise ValueError("lab bundle exceeds its cumulative size budget")
        return self


class EpisodeMatch(LabModel):
    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    ground_truth_episode_id: ContentId
    prediction_id: ContentId
    temporal_iou: float = Field(ge=0, le=1)


class EpisodeEvaluation(LabModel):
    """Deterministic event- and duration-level anomaly evaluation result."""

    schema_version: SchemaVersion = LAB_SCHEMA_VERSION
    evaluation_id: ContentId
    algorithm: Literal["temporal-iou-one-to-one-v1"] = (
        "temporal-iou-one-to-one-v1"
    )
    overlap_threshold: float = Field(gt=0, le=1)
    ground_truth_count: int = Field(ge=0, le=MAX_BUNDLE_EPISODES)
    prediction_count: int = Field(ge=0, le=MAX_BUNDLE_EPISODES)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    duration_precision: float = Field(ge=0, le=1)
    duration_recall: float = Field(ge=0, le=1)
    duration_f1: float = Field(ge=0, le=1)
    mean_matched_iou: float = Field(ge=0, le=1)
    matches: tuple[EpisodeMatch, ...] = Field(
        default=(),
        max_length=MAX_BUNDLE_EPISODES,
    )

    @model_validator(mode="after")
    def _validate_confusion_counts(self) -> Self:
        if self.true_positives + self.false_negatives != self.ground_truth_count:
            raise ValueError("evaluation ground-truth counts are inconsistent")
        if self.true_positives + self.false_positives != self.prediction_count:
            raise ValueError("evaluation prediction counts are inconsistent")
        if len(self.matches) != self.true_positives:
            raise ValueError("evaluation matches do not equal true positives")
        truth_ids = tuple(item.ground_truth_episode_id for item in self.matches)
        prediction_ids = tuple(item.prediction_id for item in self.matches)
        if len(truth_ids) != len(set(truth_ids)) or len(prediction_ids) != len(
            set(prediction_ids)
        ):
            raise ValueError("evaluation matches must be one-to-one")
        return self


__all__ = [
    "EpisodeEvaluation",
    "EpisodeLabel",
    "EpisodeMatch",
    "LAB_BUNDLE_HASH_ALGORITHM",
    "LAB_SCHEMA_VERSION",
    "LabBundle",
    "LabBundleManifest",
    "LabEpisode",
    "LabObservation",
    "MAX_BUNDLE_EPISODES",
    "MAX_BUNDLE_OBSERVATIONS",
    "MAX_BUNDLE_SERIALIZED_BYTES",
    "MAX_EPISODE_SERIALIZED_BYTES",
    "MAX_MANIFEST_SERIALIZED_BYTES",
    "MAX_METRICS_PER_OBSERVATION",
    "MAX_OBSERVATION_SERIALIZED_BYTES",
    "PredictedEpisode",
    "canonical_json_bytes",
    "compute_bundle_content_sha256",
    "stable_content_id",
]
