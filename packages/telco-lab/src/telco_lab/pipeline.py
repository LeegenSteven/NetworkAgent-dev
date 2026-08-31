"""One-command, reproducible local evaluation pipelines.

Each pipeline deliberately separates its explicitly network-capable fetch
operation from a fully offline evaluation operation.  Evaluators consume
artifacts exclusively through a verified
:class:`~telco_lab.workspace.TelcoLab` workspace.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from .adapters import (
    BUBBLERAN_ALERT_ADAPTER_ID,
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_SOURCE_LICENSE,
    AdapterError,
    adapt_bubbleran_alerts,
    adapt_bubbleran_persistent_interference_csv,
)
from .errors import LabError
from .evaluation import EvaluationError, evaluate_episodes
from .models import CatalogResource, LicenseSpec, LockedArtifact
from .parquet_reader import ParquetContract
from .rcaeval_adapter import RcaTelemetryCase, adapt_rcaeval_cases
from .rcaeval_case_index import load_case_answers, load_case_timings
from .rcaeval_commitment import (
    create_ranking_batch_commitment,
    verify_ranking_batch_commitment,
)
from .rcaeval_contracts import (
    RCAEVAL_CASE_ANSWER_CONTRACT,
    RCAEVAL_CASE_KEY_SHA256_BY_SLOT,
    RCAEVAL_CASE_TIMING_CONTRACT,
    RCAEVAL_CATALOG_ID,
    RCAEVAL_CATALOG_VERSION,
    RCAEVAL_DATASET_ID,
    RCAEVAL_DATASET_VERSION,
    RCAEVAL_FIXTURE_CLASSIFICATION,
    RCAEVAL_INDEX_RESOURCE_ID,
    RCAEVAL_LICENSE_ID,
    RCAEVAL_PIPELINE_ID,
    RCAEVAL_RESOURCE_CONTRACTS,
    RCAEVAL_RESOURCE_COUNT,
    RCAEVAL_RESOURCE_IDS,
    RCAEVAL_SAMPLE_COUNT,
    RCAEVAL_TELEMETRY_GROUPS,
    RCAEVAL_TOTAL_BYTES,
    RCAEVAL_UPSTREAM_CLASSIFICATION,
    RcaEvalResourceContract,
)
from .rcaeval_evaluation import evaluate_rca_rankings
from .rcaeval_models import RcaEvaluationReport, RcaTruth
from .rcaeval_ranking import rank_rca_features
from .schema import EpisodeEvaluation, LabBundle, PredictedEpisode
from .schema import canonical_json_bytes
from .workspace import TelcoLab


BUBBLERAN_PIPELINE_ID: Final = "bubbleran-persistent-interference"
BUBBLERAN_CLEAN_RESOURCE_ID: Final = "bubbleran.persistent-interference.clean.v1"
BUBBLERAN_ANOMALOUS_RESOURCE_ID: Final = (
    "bubbleran.persistent-interference.anomalous.v1"
)
BUBBLERAN_ALERT_RESOURCE_ID: Final = "bubbleran.persistent-interference.alerts.v1"
BUBBLERAN_RESOURCE_IDS: Final = (
    BUBBLERAN_CLEAN_RESOURCE_ID,
    BUBBLERAN_ANOMALOUS_RESOURCE_ID,
    BUBBLERAN_ALERT_RESOURCE_ID,
)

_RCAEVAL_MEDIA_TYPE: Final = "application/vnd.apache.parquet"
_RCAEVAL_NOT_CLAIMED: Final = (
    "COMPLETE_UPSTREAM_RCAEVAL_BENCHMARK",
    "FULL_RCAEVAL_DATASET_COVERAGE",
    "UPSTREAM_RCAEVAL_IMPLEMENTATION_PARITY",
    "INDEPENDENT_EVIDENCE_LABEL_ANNOTATIONS",
    "MALICIOUS_IN_PROCESS_RANKER_ISOLATION",
    "PRODUCTION_RCA_ACCURACY",
    "CROSS_DATASET_GENERALIZATION",
    "STATISTICAL_SIGNIFICANCE_OR_GENERALIZATION",
    "CAUSAL_IDENTIFICATION",
    "LIVE_NETWORK_REMEDIATION",
    "ONLINE_OR_STREAMING_EVALUATION",
    "EXTERNALLY_TIMESTAMPED_COMMITMENT",
    "CLOUD_OR_GCP_DEPLOYMENT",
    "OPEN_TELEMETRY_OR_DISTRIBUTED_TRACE",
    "UNIFIED_DASHBOARD",
    "GATE_E_OR_G5_CLOSURE",
    "P3E_OR_S7_OVERALL_CLOSURE",
)


@dataclass(frozen=True, slots=True)
class _RcaEvalPipelineProfile:
    """Private contract injection used only by code-generated test fixtures."""

    classification: str
    catalog_id: str
    catalog_version: str
    dataset_id: str
    dataset_version: str
    license_id: str
    resource_ids: tuple[str, ...]
    resource_contracts: Mapping[str, RcaEvalResourceContract]
    index_resource_id: str
    telemetry_groups: tuple[tuple[str, str, str, str], ...]
    case_key_sha256_by_slot: Mapping[str, str]
    case_timing_contract: ParquetContract
    case_answer_contract: ParquetContract
    total_bytes: int
    sample_count: int


_UPSTREAM_RCAEVAL_PROFILE: Final = _RcaEvalPipelineProfile(
    classification=RCAEVAL_UPSTREAM_CLASSIFICATION,
    catalog_id=RCAEVAL_CATALOG_ID,
    catalog_version=RCAEVAL_CATALOG_VERSION,
    dataset_id=RCAEVAL_DATASET_ID,
    dataset_version=RCAEVAL_DATASET_VERSION,
    license_id=RCAEVAL_LICENSE_ID,
    resource_ids=RCAEVAL_RESOURCE_IDS,
    resource_contracts=RCAEVAL_RESOURCE_CONTRACTS,
    index_resource_id=RCAEVAL_INDEX_RESOURCE_ID,
    telemetry_groups=RCAEVAL_TELEMETRY_GROUPS,
    case_key_sha256_by_slot=RCAEVAL_CASE_KEY_SHA256_BY_SLOT,
    case_timing_contract=RCAEVAL_CASE_TIMING_CONTRACT,
    case_answer_contract=RCAEVAL_CASE_ANSWER_CONTRACT,
    total_bytes=RCAEVAL_TOTAL_BYTES,
    sample_count=RCAEVAL_SAMPLE_COUNT,
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
                        "catalog_resource_sha256": (artifact.catalog_resource_sha256),
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


@dataclass(frozen=True, slots=True)
class RcaEvalEvaluationRun:
    """Aggregate-only RCAEval result safe for CLI and release evidence."""

    classification: str
    catalog_id: str
    catalog_version: str
    dataset_id: str
    dataset_version: str
    license_id: str
    license_attribution: str
    license_evidence_sha256: str
    artifact_count: int
    total_bytes: int
    artifact_closure_sha256: str
    report: RcaEvaluationReport = field(repr=False)
    sealed_ranking_count: int
    batch_commitment_sha256: str
    ranking_algorithm: str
    commitment_created_before_reveal: bool
    commitment_validated_after_reveal: bool
    externally_timestamped: bool

    def summary(self) -> dict[str, object]:
        report = self.report
        return {
            "schema_version": "1.0",
            "pipeline_id": RCAEVAL_PIPELINE_ID,
            "classification": self.classification,
            "dataset": {
                "artifact_count": self.artifact_count,
                "artifact_closure_sha256": self.artifact_closure_sha256,
                "catalog_id": self.catalog_id,
                "catalog_version": self.catalog_version,
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "license": {
                    "id": self.license_id,
                    "attribution": self.license_attribution,
                    "evidence_sha256": self.license_evidence_sha256,
                },
                "sample_count": report.sample_count,
                "total_bytes": self.total_bytes,
            },
            "evaluation": {
                "ranking_algorithm": report.ranking_algorithm,
                "sample_count": report.sample_count,
                "ranked_count": report.ranked_count,
                "inconclusive_count": report.inconclusive_count,
                "ac_at_1_ppm": report.ac_at_1_ppm,
                "ac_at_2_ppm": report.ac_at_2_ppm,
                "ac_at_3_ppm": report.ac_at_3_ppm,
                "ac_at_4_ppm": report.ac_at_4_ppm,
                "ac_at_5_ppm": report.ac_at_5_ppm,
                "average_at_5_ppm": report.avg_at_5_ppm,
                "mean_reciprocal_rank_ppm": report.mrr_ppm,
                "ranked_reference_count": report.evidence_reference_count,
                "truth_owned_reference_count": (report.valid_evidence_reference_count),
                "candidate_ownership_validity_ppm": (report.evidence_validity_ppm),
            },
            "protocol": {
                "answer_blind_ranking": True,
                "batch_commitment_sha256": self.batch_commitment_sha256,
                "commitment_created_before_answer_reveal": (
                    self.commitment_created_before_reveal
                ),
                "ranking_algorithm": self.ranking_algorithm,
                "post_reveal_commitment_validation": (
                    "PASS" if self.commitment_validated_after_reveal else "FAIL"
                ),
                "ranking_reused_after_reveal": True,
                "sealed_ranking_count": self.sealed_ranking_count,
                "externally_timestamped": self.externally_timestamped,
            },
            "privacy": {
                "policy": "rcaeval-aggregate-only-v1",
                "status": "PASS",
                "private_sample_details": "OMITTED",
                "candidate_details": "OMITTED",
                "reference_identifiers": "OMITTED",
                "artifact_locations": "OMITTED",
                "raw_rows": "OMITTED",
            },
            "not_claimed": list(_RCAEVAL_NOT_CLAIMED),
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


def _normalized_rcaeval_profile(
    profile: _RcaEvalPipelineProfile,
) -> _RcaEvalPipelineProfile:
    failure_code: str | None = None
    try:
        if type(profile) is not _RcaEvalPipelineProfile:
            raise LabError("invalid_catalog")
        contracts = dict(profile.resource_contracts)
        case_keys = dict(profile.case_key_sha256_by_slot)
        normalized = _RcaEvalPipelineProfile(
            classification=profile.classification,
            catalog_id=profile.catalog_id,
            catalog_version=profile.catalog_version,
            dataset_id=profile.dataset_id,
            dataset_version=profile.dataset_version,
            license_id=profile.license_id,
            resource_ids=tuple(profile.resource_ids),
            resource_contracts=MappingProxyType(contracts),
            index_resource_id=profile.index_resource_id,
            telemetry_groups=tuple(tuple(item) for item in profile.telemetry_groups),
            case_key_sha256_by_slot=MappingProxyType(case_keys),
            case_timing_contract=profile.case_timing_contract,
            case_answer_contract=profile.case_answer_contract,
            total_bytes=profile.total_bytes,
            sample_count=profile.sample_count,
        )
    except LabError as error:
        failure_code = error.code
    except Exception:
        failure_code = "invalid_catalog"
    if failure_code is not None:
        raise LabError(failure_code) from None

    resource_ids = normalized.resource_ids
    groups = normalized.telemetry_groups
    group_slots = tuple(item[0] for item in groups if len(item) == 4)
    group_resources = tuple(value for item in groups for value in item[1:])
    if (
        normalized.classification
        not in {
            RCAEVAL_UPSTREAM_CLASSIFICATION,
            RCAEVAL_FIXTURE_CLASSIFICATION,
        }
        or normalized.sample_count != 5
        or len(resource_ids) != RCAEVAL_RESOURCE_COUNT
        or len(resource_ids) != len(set(resource_ids))
        or resource_ids[0] != normalized.index_resource_id
        or set(contracts) != set(resource_ids)
        or any(
            type(contract) is not RcaEvalResourceContract
            or contract.resource_id != resource_id
            for resource_id, contract in contracts.items()
        )
        or type(normalized.case_timing_contract) is not ParquetContract
        or type(normalized.case_answer_contract) is not ParquetContract
        or len(groups) != normalized.sample_count
        or any(len(item) != 4 for item in groups)
        or len(group_slots) != len(set(group_slots))
        or set(group_slots) != set(case_keys)
        or len(group_resources) != len(set(group_resources))
        or set(group_resources) != set(resource_ids[1:])
        or type(normalized.total_bytes) is not int
        or normalized.total_bytes
        != sum(contract.size_bytes for contract in contracts.values())
    ):
        raise LabError("invalid_catalog")
    return normalized


def _validate_rcaeval_catalog(
    lab: TelcoLab,
    profile: _RcaEvalPipelineProfile,
) -> tuple[CatalogResource, ...]:
    catalog = lab.catalog()
    if (
        catalog.catalog_id != profile.catalog_id
        or catalog.catalog_version != profile.catalog_version
    ):
        raise LabError("invalid_catalog")
    resources: list[CatalogResource] = []
    for resource_id in profile.resource_ids:
        resource = catalog.resource(resource_id)
        contract = profile.resource_contracts[resource_id]
        if resource is None or (
            resource.dataset_id != profile.dataset_id
            or resource.dataset_version != profile.dataset_version
            or resource.sha256 != contract.sha256
            or resource.size_bytes != contract.size_bytes
            or resource.media_type != _RCAEVAL_MEDIA_TYPE
            or resource.adapter != contract.adapter
            or resource.license.id != profile.license_id
        ):
            raise LabError("invalid_catalog")
        resources.append(resource)
    dataset_closure = {
        resource.resource_id
        for resource in catalog.resources
        if resource.dataset_id == profile.dataset_id
        and resource.dataset_version == profile.dataset_version
    }
    if dataset_closure != set(profile.resource_ids) or any(
        resource.license != resources[0].license for resource in resources[1:]
    ):
        raise LabError("invalid_catalog")
    return tuple(resources)


def _validate_rcaeval_manifest(
    manifest,
    profile: _RcaEvalPipelineProfile,
) -> None:
    if (
        manifest.catalog_id != profile.catalog_id
        or manifest.catalog_version != profile.catalog_version
        or len(manifest.artifacts) != len(profile.resource_ids)
    ):
        raise LabError("artifact_unverified")
    locked_by_id = {artifact.resource_id: artifact for artifact in manifest.artifacts}
    if set(locked_by_id) != set(profile.resource_ids):
        raise LabError("artifact_unverified")
    for resource_id in profile.resource_ids:
        artifact = locked_by_id[resource_id]
        contract = profile.resource_contracts[resource_id]
        if (
            artifact.dataset_id != profile.dataset_id
            or artifact.dataset_version != profile.dataset_version
            or artifact.sha256 != contract.sha256
            or artifact.size_bytes != contract.size_bytes
            or artifact.media_type != _RCAEVAL_MEDIA_TYPE
            or artifact.adapter != contract.adapter
            or artifact.license_id != profile.license_id
        ):
            raise LabError("artifact_unverified")


def _artifact_closure_sha256(
    manifest,
    profile: _RcaEvalPipelineProfile,
) -> str:
    locked_by_id = {artifact.resource_id: artifact for artifact in manifest.artifacts}
    body = {
        "catalog_id": manifest.catalog_id,
        "catalog_version": manifest.catalog_version,
        "artifacts": [
            {
                "resource_id": resource_id,
                "dataset_id": locked_by_id[resource_id].dataset_id,
                "dataset_version": locked_by_id[resource_id].dataset_version,
                "sha256": locked_by_id[resource_id].sha256,
                "size_bytes": locked_by_id[resource_id].size_bytes,
                "adapter": locked_by_id[resource_id].adapter,
                "catalog_resource_sha256": (
                    locked_by_id[resource_id].catalog_resource_sha256
                ),
            }
            for resource_id in profile.resource_ids
        ],
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _evaluation_lab_error_code(error: EvaluationError) -> str:
    if error.code == "evaluation_limit_exceeded":
        return "adapter_limit_exceeded"
    return "adapter_invalid_input"


def _rcaeval_error_boundary(
    operation: Callable[[], RcaEvalEvaluationRun],
) -> RcaEvalEvaluationRun:
    failure_code: str | None = None
    try:
        return operation()
    except LabError as error:
        failure_code = error.code
    except EvaluationError as error:
        failure_code = _evaluation_lab_error_code(error)
    except Exception:
        failure_code = "internal_error"
    if failure_code is None:  # pragma: no cover - all successful calls return
        failure_code = "internal_error"
    raise LabError(failure_code) from None


def _evaluate_cached_rcaeval(
    lab: TelcoLab,
    profile: _RcaEvalPipelineProfile,
) -> RcaEvalEvaluationRun:
    profile = _normalized_rcaeval_profile(profile)
    resources = _validate_rcaeval_catalog(lab, profile)
    manifest_before = lab.verified_manifest()
    _validate_rcaeval_manifest(manifest_before, profile)
    closure_sha256 = _artifact_closure_sha256(manifest_before, profile)

    try:
        with lab.open_verified_artifacts(profile.resource_ids) as streams:
            streams_by_id = {stream.resource_id: stream for stream in streams}
            if len(streams) != len(streams_by_id) or set(streams_by_id) != set(
                profile.resource_ids
            ):
                raise LabError("artifact_unverified")
            for resource_id, stream in streams_by_id.items():
                contract = profile.resource_contracts[resource_id]
                if (
                    stream.dataset_id != profile.dataset_id
                    or stream.dataset_version != profile.dataset_version
                    or stream.sha256 != contract.sha256
                    or stream.size_bytes != contract.size_bytes
                    or stream.media_type != _RCAEVAL_MEDIA_TYPE
                    or stream.adapter != contract.adapter
                ):
                    raise LabError("artifact_unverified")

            timings = load_case_timings(
                streams_by_id[profile.index_resource_id],
                contract=profile.case_timing_contract,
                case_key_sha256_by_slot=profile.case_key_sha256_by_slot,
            )
            timings_by_slot = {timing.opaque_slot: timing for timing in timings}
            if len(timings_by_slot) != profile.sample_count or set(
                timings_by_slot
            ) != set(profile.case_key_sha256_by_slot):
                raise AdapterError("adapter_invalid_input")

            cases = tuple(
                RcaTelemetryCase(
                    opaque_slot=slot,
                    timing=timings_by_slot[slot],
                    metrics_stream=streams_by_id[metric_id],
                    metrics_contract=profile.resource_contracts[metric_id].parquet,
                    logs_stream=streams_by_id[log_id],
                    logs_contract=profile.resource_contracts[log_id].parquet,
                    traces_stream=streams_by_id[trace_id],
                    traces_contract=profile.resource_contracts[trace_id].parquet,
                )
                for slot, metric_id, log_id, trace_id in profile.telemetry_groups
            )
            feature_items = adapt_rcaeval_cases(cases)
            features_by_slot = dict(feature_items)
            if len(features_by_slot) != profile.sample_count or set(
                features_by_slot
            ) != set(profile.case_key_sha256_by_slot):
                raise AdapterError("adapter_invalid_input")

            sealed_rankings = {
                slot: rank_rca_features(features_by_slot[slot])
                for slot in sorted(features_by_slot)
            }
            commitment = create_ranking_batch_commitment(
                catalog_id=profile.catalog_id,
                catalog_version=profile.catalog_version,
                dataset_id=profile.dataset_id,
                dataset_version=profile.dataset_version,
                lock_id=manifest_before.lock_id,
                artifact_closure_count=len(profile.resource_ids),
                artifact_closure_sha256=closure_sha256,
                case_key_sha256_by_slot=profile.case_key_sha256_by_slot,
                features_by_slot=features_by_slot,
                sealed_rankings=sealed_rankings,
            )
            answers = load_case_answers(
                streams_by_id[profile.index_resource_id],
                contract=profile.case_answer_contract,
                case_key_sha256_by_slot=profile.case_key_sha256_by_slot,
                commitment=commitment,
                features_by_slot=features_by_slot,
                sealed_rankings=sealed_rankings,
            )
            validated_commitment = verify_ranking_batch_commitment(
                commitment,
                case_key_sha256_by_slot=profile.case_key_sha256_by_slot,
                features_by_slot=features_by_slot,
                sealed_rankings=sealed_rankings,
            )
            if validated_commitment != commitment:
                raise AdapterError("adapter_invalid_input")

            answers_by_slot = {answer.opaque_slot: answer for answer in answers}
            if len(answers_by_slot) != profile.sample_count or set(
                answers_by_slot
            ) != set(sealed_rankings):
                raise AdapterError("adapter_invalid_input")
            truth_by_slot: dict[str, RcaTruth] = {}
            for slot in sorted(sealed_rankings):
                candidate_id = answers_by_slot[slot].candidate_id
                matching = tuple(
                    candidate
                    for candidate in sealed_rankings[slot].ranking.candidates
                    if candidate.candidate_id == candidate_id
                )
                valid_references = matching[0].evidence_ids if matching else ()
                truth_by_slot[slot] = RcaTruth(
                    candidate_id=candidate_id,
                    valid_evidence_ids=valid_references,
                )
            report = evaluate_rca_rankings(sealed_rankings, truth_by_slot)
    except EvaluationError as error:
        failure_code = _evaluation_lab_error_code(error)
    else:
        failure_code = None
    if failure_code is not None:
        raise LabError(failure_code) from None

    manifest_after = lab.verified_manifest()
    _validate_rcaeval_manifest(manifest_after, profile)
    if manifest_after != manifest_before:
        raise LabError("artifact_unverified")
    if report.sample_count != profile.sample_count:
        raise LabError("adapter_invalid_input")
    return RcaEvalEvaluationRun(
        classification=profile.classification,
        catalog_id=manifest_before.catalog_id,
        catalog_version=manifest_before.catalog_version,
        dataset_id=profile.dataset_id,
        dataset_version=profile.dataset_version,
        license_id=resources[0].license.id,
        license_attribution=resources[0].license.attribution,
        license_evidence_sha256=resources[0].license.evidence_sha256,
        artifact_count=len(profile.resource_ids),
        total_bytes=profile.total_bytes,
        artifact_closure_sha256=closure_sha256,
        report=report,
        sealed_ranking_count=len(sealed_rankings),
        batch_commitment_sha256=commitment.commitment_sha256,
        ranking_algorithm=commitment.ranking_algorithm,
        commitment_created_before_reveal=(
            commitment.artifact_closure_sha256 == closure_sha256
            and commitment.artifact_closure_count == len(profile.resource_ids)
        ),
        commitment_validated_after_reveal=(validated_commitment == commitment),
        externally_timestamped=commitment.externally_timestamped,
    )


def _validate_rcaeval_fetch_state(
    lab: TelcoLab,
    profile: _RcaEvalPipelineProfile,
) -> None:
    report = lab.verify()
    if report.valid:
        manifest = lab.verified_manifest()
        if (
            manifest.catalog_id != profile.catalog_id
            or manifest.catalog_version != profile.catalog_version
            or not {artifact.resource_id for artifact in manifest.artifacts}.issubset(
                profile.resource_ids
            )
        ):
            raise LabError("artifact_unverified")
        return
    if any(artifact.status != "NOT_FETCHED" for artifact in report.artifacts):
        raise LabError("artifact_unverified")


def _fetch_and_evaluate_rcaeval(
    lab: TelcoLab,
    *,
    accepted_license: str,
    profile: _RcaEvalPipelineProfile,
) -> RcaEvalEvaluationRun:
    profile = _normalized_rcaeval_profile(profile)
    _validate_rcaeval_catalog(lab, profile)
    if accepted_license != profile.license_id:
        raise LabError("license_not_accepted")
    _validate_rcaeval_fetch_state(lab, profile)
    for resource_id in profile.resource_ids:
        lab.fetch(resource_id, accepted_license=accepted_license)
    return _evaluate_cached_rcaeval(lab, profile)


def evaluate_cached_rcaeval(lab: TelcoLab) -> RcaEvalEvaluationRun:
    """Evaluate the pinned RCAEval closure with no network-capable operation."""

    return _rcaeval_error_boundary(
        lambda: _evaluate_cached_rcaeval(lab, _UPSTREAM_RCAEVAL_PROFILE)
    )


def fetch_and_evaluate_rcaeval(
    lab: TelcoLab,
    *,
    accepted_license: str,
) -> RcaEvalEvaluationRun:
    """Fetch the fixed RCAEval closure and evaluate it without tuning knobs."""

    return _rcaeval_error_boundary(
        lambda: _fetch_and_evaluate_rcaeval(
            lab,
            accepted_license=accepted_license,
            profile=_UPSTREAM_RCAEVAL_PROFILE,
        )
    )


def _evaluate_cached_rcaeval_for_test(
    lab: TelcoLab,
    *,
    profile: _RcaEvalPipelineProfile,
) -> RcaEvalEvaluationRun:
    if (
        type(profile) is not _RcaEvalPipelineProfile
        or profile.classification != RCAEVAL_FIXTURE_CLASSIFICATION
    ):
        raise LabError("invalid_arguments")
    return _rcaeval_error_boundary(lambda: _evaluate_cached_rcaeval(lab, profile))


def _fetch_and_evaluate_rcaeval_for_test(
    lab: TelcoLab,
    *,
    accepted_license: str,
    profile: _RcaEvalPipelineProfile,
) -> RcaEvalEvaluationRun:
    if (
        type(profile) is not _RcaEvalPipelineProfile
        or profile.classification != RCAEVAL_FIXTURE_CLASSIFICATION
    ):
        raise LabError("invalid_arguments")
    return _rcaeval_error_boundary(
        lambda: _fetch_and_evaluate_rcaeval(
            lab,
            accepted_license=accepted_license,
            profile=profile,
        )
    )


__all__ = [
    "BUBBLERAN_ALERT_RESOURCE_ID",
    "BUBBLERAN_ANOMALOUS_RESOURCE_ID",
    "BUBBLERAN_CLEAN_RESOURCE_ID",
    "BUBBLERAN_PIPELINE_ID",
    "BUBBLERAN_RESOURCE_IDS",
    "RCAEVAL_PIPELINE_ID",
    "BubbleRanEvaluationRun",
    "RcaEvalEvaluationRun",
    "evaluate_cached_bubbleran",
    "evaluate_cached_rcaeval",
    "fetch_and_evaluate_bubbleran",
    "fetch_and_evaluate_rcaeval",
]
