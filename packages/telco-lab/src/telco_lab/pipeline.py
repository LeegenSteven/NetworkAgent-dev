"""One-command, reproducible local evaluation pipelines.

The pipeline module deliberately separates the only network-capable operation
(``fetch_and_evaluate_bubbleran``) from the fully offline evaluation operation
(``evaluate_cached_bubbleran``).  Both consume artifacts exclusively through a
verified :class:`~telco_lab.workspace.TelcoLab` workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .adapters import (
    BUBBLERAN_ALERT_ADAPTER_ID,
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_SOURCE_LICENSE,
    adapt_bubbleran_alerts,
    adapt_bubbleran_persistent_interference_csv,
)
from .errors import LabError
from .evaluation import EvaluationError, evaluate_episodes
from .models import CatalogResource, LicenseSpec, LockedArtifact
from .schema import EpisodeEvaluation, LabBundle, PredictedEpisode
from .workspace import TelcoLab


BUBBLERAN_PIPELINE_ID: Final = "bubbleran-persistent-interference"
BUBBLERAN_CLEAN_RESOURCE_ID: Final = (
    "bubbleran.persistent-interference.clean.v1"
)
BUBBLERAN_ANOMALOUS_RESOURCE_ID: Final = (
    "bubbleran.persistent-interference.anomalous.v1"
)
BUBBLERAN_ALERT_RESOURCE_ID: Final = (
    "bubbleran.persistent-interference.alerts.v1"
)
BUBBLERAN_RESOURCE_IDS: Final = (
    BUBBLERAN_CLEAN_RESOURCE_ID,
    BUBBLERAN_ANOMALOUS_RESOURCE_ID,
    BUBBLERAN_ALERT_RESOURCE_ID,
)


@dataclass(frozen=True, slots=True)
class BubbleRanEvaluationRun:
    """Typed in-memory result plus a bounded, presentation-safe summary."""

    clean_bundle: LabBundle
    anomalous_bundle: LabBundle
    predictions: tuple[PredictedEpisode, ...]
    evaluation: EpisodeEvaluation
    resources: tuple[CatalogResource, ...]
    locked_artifacts: tuple[LockedArtifact, ...]
    license: LicenseSpec
    lock_id: str

    def summary(self) -> dict[str, object]:
        report = self.evaluation
        return {
            "schema_version": "1.0",
            "pipeline_id": BUBBLERAN_PIPELINE_ID,
            "dataset": {
                "dataset_id": BUBBLERAN_DATASET_ID,
                "dataset_version": BUBBLERAN_DATASET_VERSION,
                "lock_id": self.lock_id,
                "artifact_count": len(BUBBLERAN_RESOURCE_IDS),
                "license": {
                    "id": self.license.id,
                    "name": self.license.name,
                    "attribution": self.license.attribution,
                    "evidence_sha256": self.license.evidence_sha256,
                    "reviewed_at": self.license.reviewed_at.isoformat(),
                },
                "artifacts": [
                    {
                        "resource_id": artifact.resource_id,
                        "sha256": artifact.sha256,
                        "size_bytes": artifact.size_bytes,
                        "media_type": artifact.media_type,
                        "adapter": artifact.adapter,
                        "catalog_resource_sha256": (
                            artifact.catalog_resource_sha256
                        ),
                    }
                    for artifact in self.locked_artifacts
                ],
            },
            "clean": {
                "bundle_id": self.clean_bundle.manifest.bundle_id,
                "content_sha256": self.clean_bundle.manifest.content_sha256,
                "adapter_id": self.clean_bundle.manifest.adapter_id,
                "adapter_version": self.clean_bundle.manifest.adapter_version,
                "observation_count": len(self.clean_bundle.observations),
                "ground_truth_episode_count": len(
                    self.clean_bundle.ground_truth_episodes
                ),
            },
            "anomalous": {
                "bundle_id": self.anomalous_bundle.manifest.bundle_id,
                "content_sha256": self.anomalous_bundle.manifest.content_sha256,
                "adapter_id": self.anomalous_bundle.manifest.adapter_id,
                "adapter_version": self.anomalous_bundle.manifest.adapter_version,
                "observation_count": len(self.anomalous_bundle.observations),
                "ground_truth_episode_count": len(
                    self.anomalous_bundle.ground_truth_episodes
                ),
            },
            "predictions": {"episode_count": len(self.predictions)},
            "evaluation": {
                "evaluation_id": report.evaluation_id,
                "algorithm": report.algorithm,
                "overlap_threshold": report.overlap_threshold,
                "true_positives": report.true_positives,
                "false_positives": report.false_positives,
                "false_negatives": report.false_negatives,
                "precision": report.precision,
                "recall": report.recall,
                "f1": report.f1,
                "duration_precision": report.duration_precision,
                "duration_recall": report.duration_recall,
                "duration_f1": report.duration_f1,
                "mean_matched_iou": report.mean_matched_iou,
            },
            "privacy": {
                "policy": "bubbleran-exact-schema-projection-v1",
                "status": "PASS",
                "unknown_columns": "REJECT",
                "excluded_source_fields": ["ran_ue_id"],
                "excluded_label_fields": ["persistent_anomaly"],
                "excluded_source_value_count": (
                    len(self.clean_bundle.observations)
                    + len(self.anomalous_bundle.observations)
                ),
                "excluded_label_value_count": (
                    len(self.clean_bundle.observations)
                    + len(self.anomalous_bundle.observations)
                ),
                "output_model_validation": "PASS",
            },
        }


def _validate_catalog(lab: TelcoLab) -> tuple[CatalogResource, ...]:
    expected_adapters = {
        BUBBLERAN_CLEAN_RESOURCE_ID: BUBBLERAN_CSV_ADAPTER_ID,
        BUBBLERAN_ANOMALOUS_RESOURCE_ID: BUBBLERAN_CSV_ADAPTER_ID,
        BUBBLERAN_ALERT_RESOURCE_ID: BUBBLERAN_ALERT_ADAPTER_ID,
    }
    catalog = lab.catalog()
    resources: list[CatalogResource] = []
    for resource_id, adapter_id in expected_adapters.items():
        resource = catalog.resource(resource_id)
        if resource is None or (
            resource.dataset_id != BUBBLERAN_DATASET_ID
            or resource.dataset_version != BUBBLERAN_DATASET_VERSION
            or resource.adapter != adapter_id
            or resource.license.id != BUBBLERAN_SOURCE_LICENSE
        ):
            raise LabError("invalid_catalog")
        resources.append(resource)
    if any(resource.license != resources[0].license for resource in resources[1:]):
        raise LabError("invalid_catalog")
    return tuple(resources)


def evaluate_cached_bubbleran(
    lab: TelcoLab,
    *,
    overlap_threshold: float = 0.1,
) -> BubbleRanEvaluationRun:
    """Evaluate the three verified BubbleRAN artifacts without network I/O."""

    resources = _validate_catalog(lab)
    clean = adapt_bubbleran_persistent_interference_csv(
        lab.artifact_path(BUBBLERAN_CLEAN_RESOURCE_ID)
    )
    anomalous = adapt_bubbleran_persistent_interference_csv(
        lab.artifact_path(BUBBLERAN_ANOMALOUS_RESOURCE_ID)
    )
    if (
        len(clean.manifest.resource_ids) != 1
        or clean.manifest.resource_ids != anomalous.manifest.resource_ids
    ):
        raise LabError("adapter_invalid_input")
    predictions = adapt_bubbleran_alerts(
        lab.artifact_path(BUBBLERAN_ALERT_RESOURCE_ID),
        resource_id=anomalous.manifest.resource_ids[0],
    )
    try:
        evaluation = evaluate_episodes(
            anomalous.ground_truth_episodes,
            predictions,
            overlap_threshold=overlap_threshold,
        )
    except EvaluationError as exc:
        raise LabError("invalid_arguments") from exc
    manifest = lab.verified_manifest()
    locked_by_id = {item.resource_id: item for item in manifest.artifacts}
    try:
        locked_artifacts = tuple(
            locked_by_id[resource_id] for resource_id in BUBBLERAN_RESOURCE_IDS
        )
    except KeyError as exc:  # pragma: no cover - artifact_path already verifies each
        raise LabError("artifact_unverified") from exc
    return BubbleRanEvaluationRun(
        clean_bundle=clean,
        anomalous_bundle=anomalous,
        predictions=predictions,
        evaluation=evaluation,
        resources=resources,
        locked_artifacts=locked_artifacts,
        license=resources[0].license,
        lock_id=manifest.lock_id,
    )


def fetch_and_evaluate_bubbleran(
    lab: TelcoLab,
    *,
    accepted_license: str,
    overlap_threshold: float = 0.1,
) -> BubbleRanEvaluationRun:
    """Explicitly fetch the pinned artifacts, then run the offline pipeline."""

    _validate_catalog(lab)
    for resource_id in BUBBLERAN_RESOURCE_IDS:
        lab.fetch(resource_id, accepted_license=accepted_license)
    return evaluate_cached_bubbleran(
        lab,
        overlap_threshold=overlap_threshold,
    )


__all__ = [
    "BUBBLERAN_ALERT_RESOURCE_ID",
    "BUBBLERAN_ANOMALOUS_RESOURCE_ID",
    "BUBBLERAN_CLEAN_RESOURCE_ID",
    "BUBBLERAN_PIPELINE_ID",
    "BUBBLERAN_RESOURCE_IDS",
    "BubbleRanEvaluationRun",
    "evaluate_cached_bubbleran",
    "fetch_and_evaluate_bubbleran",
]
