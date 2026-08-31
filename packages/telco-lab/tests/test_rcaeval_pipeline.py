from __future__ import annotations

import hashlib
import inspect
import json
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pyarrow
import pytest

import telco_lab
import telco_lab.cli as cli_module
import telco_lab.pipeline as pipeline_module
from telco_lab.adapters import AdapterError
from telco_lab.catalog import PackageCatalogProvider
from telco_lab.errors import LabError
from telco_lab.models import (
    LockedArtifact,
    WorkspaceLock,
    catalog_resource_sha256,
    source_url_sha256,
    workspace_lock_id,
)
from telco_lab.rcaeval_case_index import CaseAnswer, CaseTiming
from telco_lab.rcaeval_contracts import (
    RCAEVAL_CASE_ANSWER_CONTRACT,
    RCAEVAL_CASE_KEY_SHA256_BY_SLOT,
    RCAEVAL_CASE_TIMING_CONTRACT,
    RCAEVAL_CATALOG_ID,
    RCAEVAL_CATALOG_VERSION,
    RCAEVAL_DATASET_ID,
    RCAEVAL_DATASET_VERSION,
    RCAEVAL_FIXTURE_CLASSIFICATION,
    RCAEVAL_INDEX_RESOURCE_ID,
    RCAEVAL_OPAQUE_SLOTS,
    RCAEVAL_PIPELINE_ID,
    RCAEVAL_RESOURCE_CONTRACTS,
    RCAEVAL_RESOURCE_IDS,
    RCAEVAL_SAMPLE_COUNT,
    RCAEVAL_TELEMETRY_GROUPS,
    RCAEVAL_TOTAL_BYTES,
    RCAEVAL_UPSTREAM_CLASSIFICATION,
)
from telco_lab.rcaeval_models import (
    RCA_RANKING_ALGORITHM,
    RcaEvaluationReport,
    RcaFeatureSet,
    RcaRankedCandidate,
    RcaRanking,
    RcaRankingSeal,
    RcaTruth,
)
from telco_lab.schema import canonical_json_bytes


_NOW = datetime(2026, 8, 31, 9, 30, tzinfo=UTC)
_EXTRA_RESOURCE_ID = "bubbleran.persistent-interference.clean.v1"


def _catalog():
    catalog = PackageCatalogProvider().load()
    assert all(catalog.resource(resource_id) for resource_id in RCAEVAL_RESOURCE_IDS)
    return catalog


def _locked_artifact(resource_id: str) -> LockedArtifact:
    resource = _catalog().resource(resource_id)
    assert resource is not None
    license_spec = resource.license
    return LockedArtifact(
        resource_id=resource.resource_id,
        dataset_id=resource.dataset_id,
        dataset_version=resource.dataset_version,
        filename=resource.filename,
        sha256=resource.sha256,
        size_bytes=resource.size_bytes,
        media_type=resource.media_type,
        adapter=resource.adapter,
        catalog_resource_sha256=catalog_resource_sha256(resource),
        source_url_sha256=source_url_sha256(resource.source_url),
        allowed_hosts=resource.allowed_hosts,
        license_id=license_spec.id,
        license_name=license_spec.name,
        license_url=license_spec.url,
        license_evidence_url=license_spec.evidence_url,
        license_evidence_sha256=license_spec.evidence_sha256,
        license_attribution=license_spec.attribution,
        license_reviewed_at=license_spec.reviewed_at,
        fetched_at=_NOW,
    )


def _manifest(resource_ids: tuple[str, ...] = RCAEVAL_RESOURCE_IDS) -> WorkspaceLock:
    catalog = _catalog()
    artifacts = tuple(_locked_artifact(resource_id) for resource_id in resource_ids)
    return WorkspaceLock(
        schema_version="1.0",
        lock_id=workspace_lock_id(
            catalog.catalog_id,
            catalog.catalog_version,
            artifacts,
        ),
        catalog_id=catalog.catalog_id,
        catalog_version=catalog.catalog_version,
        generated_at=_NOW,
        artifacts=artifacts,
    )


class _ArtifactMarker:
    def __init__(self, resource_id: str) -> None:
        resource = _catalog().resource(resource_id)
        assert resource is not None
        self.resource_id = resource_id
        self.dataset_id = resource.dataset_id
        self.dataset_version = resource.dataset_version
        self.sha256 = resource.sha256
        self.size_bytes = resource.size_bytes
        self.media_type = resource.media_type
        self.adapter = resource.adapter


class _StreamContext(AbstractContextManager[tuple[_ArtifactMarker, ...]]):
    def __init__(self, streams: tuple[_ArtifactMarker, ...]) -> None:
        self._streams = streams

    def __enter__(self) -> tuple[_ArtifactMarker, ...]:
        return self._streams

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        return None


class _FakeLab:
    """Small behavioural double that fails on every unapproved I/O path."""

    def __init__(
        self,
        manifests: tuple[WorkspaceLock, ...],
        *,
        bomb_open: bool = False,
    ) -> None:
        self._manifests = manifests
        self._bomb_open = bomb_open
        self.manifest_calls = 0
        self.catalog_calls = 0
        self.open_calls: list[tuple[str, ...]] = []
        self.fetch_calls: list[str] = []

    def catalog(self):
        self.catalog_calls += 1
        return _catalog()

    def verified_manifest(self) -> WorkspaceLock:
        index = min(self.manifest_calls, len(self._manifests) - 1)
        self.manifest_calls += 1
        return self._manifests[index]

    def verify(self):  # noqa: ANN201
        return SimpleNamespace(valid=True, artifacts=())

    def open_verified_artifacts(
        self,
        resource_ids,
    ) -> _StreamContext:  # noqa: ANN001
        if self._bomb_open:
            raise AssertionError(
                "invalid closure must be rejected before artifacts open"
            )
        requested = tuple(resource_ids)
        self.open_calls.append(requested)
        # Deliberately reverse the handles.  The pipeline must bind by resource_id,
        # never by the order supplied by the context manager.
        streams = tuple(_ArtifactMarker(item) for item in reversed(requested))
        return _StreamContext(streams)

    def fetch(self, resource_id: str, *, accepted_license: str):  # noqa: ANN201
        self.fetch_calls.append(resource_id)
        raise AssertionError(
            "mixed/extra closure must be rejected before downloader-backed fetch"
        )


def _evidence(slot_index: int, label: str) -> str:
    digest = hashlib.sha256(f"{slot_index}:{label}".encode("ascii")).hexdigest()
    return f"rcaevidence-{digest}"


def _seal(
    feature: RcaFeatureSet,
    *,
    slot_index: int,
) -> tuple[RcaRankingSeal, str, tuple[str, ...], tuple[str, ...]]:
    true_candidate = f"private-service-{slot_index}"
    decoy_candidate = f"private-decoy-{slot_index}"
    true_evidence = tuple(
        sorted(
            (
                _evidence(slot_index, "truth-metric"),
                _evidence(slot_index, "truth-trace"),
            )
        )
    )
    decoy_evidence = (_evidence(slot_index, "decoy-log"),)
    ranking = RcaRanking(
        outcome="RANKED",
        candidates=(
            RcaRankedCandidate(
                candidate_id=true_candidate,
                rank=1,
                score_numerator=2,
                score_denominator=1,
                evidence_ids=true_evidence,
            ),
            RcaRankedCandidate(
                candidate_id=decoy_candidate,
                rank=2,
                score_numerator=1,
                score_denominator=1,
                evidence_ids=decoy_evidence,
            ),
        ),
    )
    return (
        RcaRankingSeal(
            feature_sha256=hashlib.sha256(canonical_json_bytes(feature)).hexdigest(),
            ranking=ranking,
            ranking_sha256=hashlib.sha256(ranking.canonical_bytes()).hexdigest(),
        ),
        true_candidate,
        true_evidence,
        decoy_evidence,
    )


def _install_orchestration_spies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[str],
    dict[str, str],
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
    SimpleNamespace,
]:
    events: list[str] = []
    timings = tuple(
        CaseTiming(
            opaque_slot=slot,
            time_start=1_700_000_000 + index * 10_000,
            inject_time=1_700_000_100 + index * 10_000,
            time_end=1_700_000_200 + index * 10_000,
        )
        for index, slot in enumerate(RCAEVAL_OPAQUE_SLOTS)
    )
    features = {slot: RcaFeatureSet() for slot in RCAEVAL_OPAQUE_SLOTS}
    feature_slots = {id(feature): slot for slot, feature in features.items()}
    seals: dict[str, RcaRankingSeal] = {}
    answers: dict[str, str] = {}
    truth_evidence: dict[str, tuple[str, ...]] = {}
    decoy_evidence: dict[str, tuple[str, ...]] = {}
    for index, slot in enumerate(RCAEVAL_OPAQUE_SLOTS, start=1):
        seal, answer, true_refs, decoy_refs = _seal(
            features[slot],
            slot_index=index,
        )
        seals[slot] = seal
        answers[slot] = answer
        truth_evidence[slot] = true_refs
        decoy_evidence[slot] = decoy_refs

    commitment = SimpleNamespace(
        commitment_sha256="c" * 64,
        ranking_algorithm=RCA_RANKING_ALGORITHM,
        artifact_closure_sha256=None,
        artifact_closure_count=None,
        externally_timestamped=False,
        items=tuple(SimpleNamespace(opaque_slot=slot) for slot in sorted(seals)),
    )

    def load_timings(stream, **kwargs):  # noqa: ANN001,ANN202
        events.append("timing")
        assert stream.resource_id == RCAEVAL_INDEX_RESOURCE_ID
        assert kwargs == {
            "contract": RCAEVAL_CASE_TIMING_CONTRACT,
            "case_key_sha256_by_slot": RCAEVAL_CASE_KEY_SHA256_BY_SLOT,
        }
        return timings

    def adapt_cases(cases):  # noqa: ANN001,ANN202
        assert len(cases) == RCAEVAL_SAMPLE_COUNT
        timing_by_slot = {item.opaque_slot: item for item in timings}
        groups = {
            slot: (metrics, logs, traces)
            for slot, metrics, logs, traces in RCAEVAL_TELEMETRY_GROUPS
        }
        result: list[tuple[str, RcaFeatureSet]] = []
        for case in cases:
            slot = case.opaque_slot
            expected_metrics, expected_logs, expected_traces = groups[slot]
            assert case.timing is timing_by_slot[slot]
            assert case.metrics_stream.resource_id == expected_metrics
            assert case.logs_stream.resource_id == expected_logs
            assert case.traces_stream.resource_id == expected_traces
            assert (
                case.metrics_contract
                is RCAEVAL_RESOURCE_CONTRACTS[expected_metrics].parquet
            )
            assert (
                case.logs_contract is RCAEVAL_RESOURCE_CONTRACTS[expected_logs].parquet
            )
            assert (
                case.traces_contract
                is RCAEVAL_RESOURCE_CONTRACTS[expected_traces].parquet
            )
            events.append(f"feature:{slot}")
            result.append((slot, features[slot]))
        return tuple(result)

    def rank_feature(feature):  # noqa: ANN001,ANN202
        slot = feature_slots[id(feature)]
        events.append(f"rank:{slot}")
        return seals[slot]

    def create_commitment(**kwargs):  # noqa: ANN003,ANN202
        events.append("commit")
        assert kwargs["artifact_closure_count"] == len(RCAEVAL_RESOURCE_IDS)
        assert kwargs["case_key_sha256_by_slot"] == (RCAEVAL_CASE_KEY_SHA256_BY_SLOT)
        assert kwargs["features_by_slot"] == features
        assert kwargs["sealed_rankings"] == seals
        assert kwargs.get("externally_timestamped", False) is False
        commitment.artifact_closure_sha256 = kwargs["artifact_closure_sha256"]
        commitment.artifact_closure_count = kwargs["artifact_closure_count"]
        return commitment

    def load_answers(stream, **kwargs):  # noqa: ANN001,ANN202
        events.append("answers")
        assert stream.resource_id == RCAEVAL_INDEX_RESOURCE_ID
        assert kwargs["contract"] is RCAEVAL_CASE_ANSWER_CONTRACT
        assert kwargs["case_key_sha256_by_slot"] == RCAEVAL_CASE_KEY_SHA256_BY_SLOT
        assert kwargs["commitment"] is commitment
        assert kwargs["features_by_slot"] == features
        assert kwargs["sealed_rankings"] == seals
        return tuple(
            CaseAnswer(opaque_slot=slot, candidate_id=answers[slot])
            for slot in RCAEVAL_OPAQUE_SLOTS
        )

    def verify_commitment(supplied, **kwargs):  # noqa: ANN001,ANN202
        # This explicit verification must happen after the answer projection,
        # even though the answer reader also validates before opening labels.
        events.append("post-reveal-verify")
        assert supplied is commitment
        assert kwargs == {
            "case_key_sha256_by_slot": RCAEVAL_CASE_KEY_SHA256_BY_SLOT,
            "features_by_slot": features,
            "sealed_rankings": seals,
        }
        return commitment

    def aggregate(supplied_seals, supplied_truth):  # noqa: ANN001,ANN202
        events.append("aggregate")
        assert supplied_seals == seals
        assert set(supplied_truth) == set(RCAEVAL_OPAQUE_SLOTS)
        for slot, truth in supplied_truth.items():
            assert type(truth) is RcaTruth
            assert truth.candidate_id == answers[slot]
            sealed_candidate = next(
                item
                for item in seals[slot].ranking.candidates
                if item.candidate_id == answers[slot]
            )
            assert truth.valid_evidence_ids == sealed_candidate.evidence_ids
            assert truth.valid_evidence_ids == truth_evidence[slot]
            assert not set(truth.valid_evidence_ids).intersection(decoy_evidence[slot])
        return RcaEvaluationReport(
            sample_count=5,
            ranked_count=5,
            inconclusive_count=0,
            ac_at_1_ppm=1_000_000,
            ac_at_2_ppm=1_000_000,
            ac_at_3_ppm=1_000_000,
            ac_at_4_ppm=1_000_000,
            ac_at_5_ppm=1_000_000,
            avg_at_5_ppm=1_000_000,
            mrr_ppm=1_000_000,
            evidence_reference_count=15,
            valid_evidence_reference_count=10,
            evidence_validity_ppm=666_666,
        )

    monkeypatch.setattr(pipeline_module, "load_case_timings", load_timings)
    monkeypatch.setattr(pipeline_module, "adapt_rcaeval_cases", adapt_cases)
    monkeypatch.setattr(pipeline_module, "rank_rca_features", rank_feature)
    monkeypatch.setattr(
        pipeline_module,
        "create_ranking_batch_commitment",
        create_commitment,
    )
    monkeypatch.setattr(pipeline_module, "load_case_answers", load_answers)
    monkeypatch.setattr(
        pipeline_module,
        "verify_ranking_batch_commitment",
        verify_commitment,
    )
    monkeypatch.setattr(pipeline_module, "evaluate_rca_rankings", aggregate)
    return events, answers, truth_evidence, decoy_evidence, commitment


def _walk_keys(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_keys(child)


def test_public_rcaeval_functions_have_no_adjustable_evaluation_parameters() -> None:
    cached = inspect.signature(pipeline_module.evaluate_cached_rcaeval)
    fetched = inspect.signature(pipeline_module.fetch_and_evaluate_rcaeval)

    assert tuple(cached.parameters) == ("lab",)
    assert tuple(fetched.parameters) == ("lab", "accepted_license")
    assert fetched.parameters["accepted_license"].kind is inspect.Parameter.KEYWORD_ONLY
    assert fetched.parameters["accepted_license"].default is inspect.Parameter.empty
    for signature in (cached, fetched):
        assert "profile" not in signature.parameters
        assert "overlap_threshold" not in signature.parameters
    assert telco_lab.RCAEVAL_PIPELINE_ID == RCAEVAL_PIPELINE_ID
    assert telco_lab.evaluate_cached_rcaeval is pipeline_module.evaluate_cached_rcaeval
    assert (
        telco_lab.fetch_and_evaluate_rcaeval
        is pipeline_module.fetch_and_evaluate_rcaeval
    )


def test_pipeline_freezes_answer_blind_call_order_and_exact_handle_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _answers, _truth_refs, _decoy_refs, _commitment = (
        _install_orchestration_spies(monkeypatch)
    )
    manifest = _manifest()
    lab = _FakeLab((manifest, manifest))

    result = pipeline_module.evaluate_cached_rcaeval(lab)  # type: ignore[arg-type]

    expected = ["timing"]
    expected.extend(f"feature:{slot}" for slot in RCAEVAL_OPAQUE_SLOTS)
    expected.extend(f"rank:{slot}" for slot in sorted(RCAEVAL_OPAQUE_SLOTS))
    expected.extend(("commit", "answers", "post-reveal-verify", "aggregate"))
    assert events == expected
    assert lab.manifest_calls == 2
    assert lab.open_calls == [RCAEVAL_RESOURCE_IDS]
    assert lab.fetch_calls == []
    assert result.summary()["pipeline_id"] == RCAEVAL_PIPELINE_ID


def test_offline_pipeline_rejects_missing_or_extra_workspace_closure_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "load_case_timings",
        lambda *_args, **_kwargs: pytest.fail("must reject before timing projection"),
    )
    closures = (
        RCAEVAL_RESOURCE_IDS[:-1],
        RCAEVAL_RESOURCE_IDS + (_EXTRA_RESOURCE_ID,),
    )
    for resource_ids in closures:
        lab = _FakeLab((_manifest(resource_ids),), bomb_open=True)
        with pytest.raises(LabError):
            pipeline_module.evaluate_cached_rcaeval(lab)  # type: ignore[arg-type]
        assert lab.open_calls == []
        assert lab.fetch_calls == []


def test_fetch_rejects_mixed_or_extra_existing_closure_before_downloader() -> None:
    mixed = _manifest(RCAEVAL_RESOURCE_IDS + (_EXTRA_RESOURCE_ID,))
    lab = _FakeLab((mixed,), bomb_open=True)

    with pytest.raises(LabError):
        pipeline_module.fetch_and_evaluate_rcaeval(  # type: ignore[arg-type]
            lab,
            accepted_license="MIT",
        )

    assert lab.manifest_calls >= 1
    assert lab.fetch_calls == []
    assert lab.open_calls == []


def test_fetch_rejects_license_before_workspace_or_downloader_access() -> None:
    lab = _FakeLab(())

    with pytest.raises(LabError) as caught:
        pipeline_module.fetch_and_evaluate_rcaeval(  # type: ignore[arg-type]
            lab,
            accepted_license="wrong-license",
        )

    assert caught.value.code == "license_not_accepted"
    assert lab.manifest_calls == 0
    assert lab.fetch_calls == []
    assert lab.open_calls == []


def test_pipeline_rejects_any_verified_manifest_change_across_held_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_orchestration_spies(monkeypatch)
    before = _manifest()
    # fetched_at is intentionally excluded from lock_id.  Comparing lock_id alone
    # is therefore insufficient for the pre/post verified-manifest invariant.
    changed_artifact = before.artifacts[0].model_copy(
        update={"fetched_at": before.artifacts[0].fetched_at + timedelta(seconds=1)}
    )
    changed_artifacts = (changed_artifact, *before.artifacts[1:])
    after = WorkspaceLock(
        schema_version="1.0",
        lock_id=before.lock_id,
        catalog_id=before.catalog_id,
        catalog_version=before.catalog_version,
        generated_at=before.generated_at,
        artifacts=changed_artifacts,
    )
    lab = _FakeLab((before, after))

    with pytest.raises(LabError):
        pipeline_module.evaluate_cached_rcaeval(lab)  # type: ignore[arg-type]

    assert lab.manifest_calls == 2
    assert lab.open_calls == [RCAEVAL_RESOURCE_IDS]


def test_public_pipeline_detaches_private_lower_level_exception_chains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_canary = "PRIVATE_PARQUET_STREAM_CANARY"

    def poisoned_projection(*_args, **_kwargs):  # noqa: ANN002,ANN003,ANN202
        try:
            raise RuntimeError(private_canary)
        except RuntimeError as error:
            raise AdapterError("adapter_invalid_input") from error

    monkeypatch.setattr(
        pipeline_module,
        "load_case_timings",
        poisoned_projection,
    )
    manifest = _manifest()

    with pytest.raises(LabError) as caught:
        pipeline_module.evaluate_cached_rcaeval(  # type: ignore[arg-type]
            _FakeLab((manifest, manifest))
        )

    assert caught.value.code == "adapter_invalid_input"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True
    assert private_canary not in str(caught.value)
    assert private_canary not in repr(caught.value)


def test_summary_is_aggregate_only_and_states_sufficient_non_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _events, answers, truth_refs, decoy_refs, commitment = _install_orchestration_spies(
        monkeypatch
    )
    manifest = _manifest()
    result = pipeline_module.evaluate_cached_rcaeval(  # type: ignore[arg-type]
        _FakeLab((manifest, manifest))
    )
    summary = result.summary()

    assert summary["pipeline_id"] == RCAEVAL_PIPELINE_ID
    assert summary["classification"] == RCAEVAL_UPSTREAM_CLASSIFICATION
    assert summary["classification"] != RCAEVAL_FIXTURE_CLASSIFICATION
    assert summary["dataset"] == {
        "artifact_count": len(RCAEVAL_RESOURCE_IDS),
        "artifact_closure_sha256": (
            "c99ced28f1cb56464820a9570ead783de753c31ad36f5d7d29de594115101fb1"
        ),
        "catalog_id": RCAEVAL_CATALOG_ID,
        "catalog_version": RCAEVAL_CATALOG_VERSION,
        "dataset_id": RCAEVAL_DATASET_ID,
        "dataset_version": RCAEVAL_DATASET_VERSION,
        "license": {
            "attribution": "RCAEval dataset contributors",
            "evidence_sha256": (
                "c2990bbe2e040a8d2f55fdd47c4f47f02223d8ea098e5d6e8851585a64956a0f"
            ),
            "id": "MIT",
        },
        "sample_count": RCAEVAL_SAMPLE_COUNT,
        "total_bytes": RCAEVAL_TOTAL_BYTES,
    }
    assert summary["evaluation"]["ranked_reference_count"] == 15
    assert summary["evaluation"]["truth_owned_reference_count"] == 10
    assert summary["evaluation"]["candidate_ownership_validity_ppm"] == 666_666
    assert summary["protocol"]["sealed_ranking_count"] == 5
    assert summary["protocol"]["batch_commitment_sha256"] == "c" * 64
    assert summary["protocol"]["ranking_algorithm"] == RCA_RANKING_ALGORITHM
    assert summary["protocol"]["externally_timestamped"] is False

    expected_non_claims = [
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
    ]
    assert summary["not_claimed"] == expected_non_claims

    keys = tuple(key.casefold() for key in _walk_keys(summary))
    prohibited_exact_keys = {
        "case_id",
        "case_ids",
        "candidate_id",
        "candidate_ids",
        "evidence_id",
        "evidence_ids",
        "resource_id",
        "resource_ids",
        "lock_id",
        "ranking_seal",
        "ranking_seals",
        "seal",
        "seals",
    }
    assert not prohibited_exact_keys.intersection(keys)
    assert summary["privacy"]["raw_rows"] == "OMITTED"
    assert not any(key.endswith("_path") or key.endswith("_url") for key in keys)

    wire = json.dumps(summary, allow_nan=False, separators=(",", ":"), sort_keys=True)
    private_values = {
        *RCAEVAL_RESOURCE_IDS,
        *RCAEVAL_OPAQUE_SLOTS,
        *answers.values(),
        *(item for refs in truth_refs.values() for item in refs),
        *(item for refs in decoy_refs.values() for item in refs),
        manifest.lock_id,
    }
    assert all(value not in wire for value in private_values)
    assert "https://" not in wire
    assert "file:" not in wire


@pytest.mark.parametrize(
    "arguments",
    (
        (
            "evaluate",
            RCAEVAL_PIPELINE_ID,
            "--overlap-threshold",
            "0.5",
        ),
        (
            "run",
            RCAEVAL_PIPELINE_ID,
            "--accept-license",
            "MIT",
            "--overlap-threshold",
            "0.5",
        ),
    ),
)
def test_cli_rejects_rcaeval_overlap_threshold_without_business_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
) -> None:
    calls: list[str] = []

    def bomb(*_args, **_kwargs):  # noqa: ANN002,ANN003,ANN202
        calls.append("business")
        raise AssertionError("RCA adjustable threshold must fail before business logic")

    monkeypatch.setattr(cli_module, "evaluate_cached_rcaeval", bomb)
    monkeypatch.setattr(cli_module, "fetch_and_evaluate_rcaeval", bomb)
    stdout, stderr = StringIO(), StringIO()

    code = cli_module.main(
        ("--workspace", str(tmp_path), *arguments),
        stdout=stdout,
        stderr=stderr,
        provider=PackageCatalogProvider(),
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "invalid_arguments"
    assert calls == []
    assert "0.5" not in stderr.getvalue()


def test_cli_rcaeval_run_and_offline_results_are_safe_and_report_pyarrow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_summary: dict[str, object] = {
        "schema_version": "1.0",
        "pipeline_id": RCAEVAL_PIPELINE_ID,
        "classification": RCAEVAL_UPSTREAM_CLASSIFICATION,
        "dataset": {
            "artifact_count": 16,
            "sample_count": 5,
            "total_bytes": RCAEVAL_TOTAL_BYTES,
        },
        "evaluation": {
            "ranked_count": 5,
            "inconclusive_count": 0,
            "candidate_ownership_validity_ppm": 1_000_000,
        },
        "not_claimed": ["PRODUCTION_RCA_ACCURACY"],
    }
    result = SimpleNamespace(summary=lambda: safe_summary)
    calls: list[tuple[str, str | None]] = []

    def evaluate_cached(_lab):  # noqa: ANN001,ANN202
        calls.append(("evaluate", None))
        return result

    def fetch_and_evaluate(_lab, *, accepted_license):  # noqa: ANN001,ANN202
        calls.append(("run", accepted_license))
        return result

    monkeypatch.setattr(cli_module, "evaluate_cached_rcaeval", evaluate_cached)
    monkeypatch.setattr(
        cli_module,
        "fetch_and_evaluate_rcaeval",
        fetch_and_evaluate,
    )

    run_stdout, run_stderr = StringIO(), StringIO()
    run_code = cli_module.main(
        (
            "--workspace",
            str(tmp_path),
            "run",
            RCAEVAL_PIPELINE_ID,
            "--accept-license",
            "MIT",
        ),
        stdout=run_stdout,
        stderr=run_stderr,
        provider=PackageCatalogProvider(),
    )
    offline_stdout, offline_stderr = StringIO(), StringIO()
    offline_code = cli_module.main(
        (
            "--workspace",
            str(tmp_path),
            "evaluate",
            RCAEVAL_PIPELINE_ID,
        ),
        stdout=offline_stdout,
        stderr=offline_stderr,
        provider=PackageCatalogProvider(),
    )

    run_payload = json.loads(run_stdout.getvalue())
    offline_payload = json.loads(offline_stdout.getvalue())
    assert run_code == offline_code == 0
    assert run_stderr.getvalue() == offline_stderr.getvalue() == ""
    assert calls == [("run", "MIT"), ("evaluate", None)]
    assert run_payload["result"] == offline_payload["result"] == safe_summary
    assert run_payload["execution"]["runtime"]["pyarrow"] == pyarrow.__version__
    assert offline_payload["execution"]["runtime"]["pyarrow"] == pyarrow.__version__
    wire = run_stdout.getvalue() + offline_stdout.getvalue()
    assert str(tmp_path) not in wire
    assert "https://" not in wire
