from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import telco_lab.pipeline as pipeline_module
from telco_lab.catalog import FixtureCatalogProvider
from telco_lab.downloader import DownloadReceipt
from telco_lab.parquet_reader import ParquetContract, parquet_schema_fingerprint
from telco_lab.pipeline import (
    _RcaEvalPipelineProfile,
    _evaluate_cached_rcaeval_for_test,
    _fetch_and_evaluate_rcaeval_for_test,
)
from telco_lab.rcaeval_adapter import adapt_rcaeval_cases
from telco_lab.rcaeval_case_index import load_case_answers, load_case_timings
from telco_lab.rcaeval_commitment import (
    case_key_sha256,
    create_ranking_batch_commitment,
    verify_ranking_batch_commitment,
)
from telco_lab.rcaeval_contracts import (
    RCAEVAL_FIXTURE_CLASSIFICATION,
    RCAEVAL_PIPELINE_ID,
    RcaEvalResourceContract,
)
from telco_lab.rcaeval_evaluation import evaluate_rca_rankings
from telco_lab.rcaeval_ranking import rank_rca_features
from telco_lab.workspace import TelcoLab


_CATALOG_ID = "rcaeval-code-generated-fixture"
_CATALOG_VERSION = "1.0.0"
_DATASET_ID = "rcaeval-code-generated-slice"
_DATASET_VERSION = "fixture-v1"
_LICENSE_ID = "MIT"
_HOST = "private-fixture.example.test"
_TIMING_COLUMNS = ("case", "inject_time", "time_start", "time_end")
_ANSWER_COLUMNS = ("case", "root_cause_service")
_LOG_COLUMNS = ("timestamp", "container_name")
_TRACE_COLUMNS = (
    "startTime",
    "startTimeMillis",
    "duration",
    "statusCode",
    "serviceName",
)
_FORBIDDEN_KEYS = {
    "candidate_id",
    "case",
    "case_id",
    "evidence_id",
    "evidence_ids",
    "lock_id",
    "path",
    "raw",
    "resource_id",
    "seal",
    "source_url",
}


def _slot(index: int) -> str:
    digest = hashlib.sha256(f"private-fixture-slot-{index}".encode()).hexdigest()
    return f"rcaslot-{digest}"


def _case_key(index: int) -> str:
    return f"private-fixture-case-{index}"


def _candidate(index: int) -> str:
    return f"service{index}"


def _parquet(table: pa.Table) -> bytes:
    stream = io.BytesIO()
    pq.write_table(table, stream, compression="snappy")
    return stream.getvalue()


def _contract(
    table: pa.Table,
    projection: tuple[str, ...],
) -> ParquetContract:
    return ParquetContract(
        expected_schema=table.schema,
        expected_schema_fingerprint=parquet_schema_fingerprint(table.schema),
        projected_columns=projection,
        expected_rows=table.num_rows,
        expected_row_groups=1,
        allowed_codecs=("SNAPPY",),
    )


def _index_table() -> pa.Table:
    indices = tuple(range(1, 6))
    return pa.table(
        {
            "case": pa.array(
                [_case_key(index) for index in indices],
                type=pa.large_string(),
            ),
            "inject_time": pa.array([1_720] * 5, type=pa.int64()),
            "time_start": pa.array([1_000] * 5, type=pa.int64()),
            "time_end": pa.array([2_440] * 5, type=pa.int64()),
            "root_cause_service": pa.array(
                [_candidate(index) for index in indices],
                type=pa.large_string(),
            ),
            # This column proves the timing/answer readers do not materialize
            # arbitrary private source values.
            "private_raw_canary": pa.array(
                [f"PRIVATE_RAW_CASE_{index}" for index in indices],
                type=pa.large_string(),
            ),
        }
    )


def _metrics_table(index: int) -> pa.Table:
    timestamps = tuple(range(1_000, 2_441))
    values = tuple(1.0 if value < 1_720 else 4.0 for value in timestamps)
    return pa.table(
        {
            "time": pa.array(timestamps, type=pa.int64()),
            f"{_candidate(index)}_cpu": pa.array(values, type=pa.float64()),
        }
    )


def _logs_table(index: int) -> pa.Table:
    return pa.table(
        {
            "timestamp": pa.array(
                [1_100, 1_800, 1_801, 1_802],
                type=pa.int64(),
            ),
            "container_name": pa.array(
                [_candidate(index)] * 4,
                type=pa.large_string(),
            ),
            "message": pa.array(
                [f"PRIVATE_LOG_CASE_{index}"] * 4,
                type=pa.large_string(),
            ),
        }
    )


def _traces_table(index: int) -> pa.Table:
    return pa.table(
        {
            "startTime": pa.array(
                [1_100_000_000, 1_800_000_000],
                type=pa.int64(),
            ),
            "startTimeMillis": pa.array(
                [1_100_000, 1_800_000],
                type=pa.int64(),
            ),
            "duration": pa.array([1_000, 8_000], type=pa.int64()),
            "statusCode": pa.array([0, 0], type=pa.int64()),
            "serviceName": pa.array(
                [_candidate(index)] * 2,
                type=pa.large_string(),
            ),
            "traceID": pa.array(
                [f"PRIVATE_TRACE_CASE_{index}"] * 2,
                type=pa.large_string(),
            ),
        }
    )


def _fixture() -> tuple[
    FixtureCatalogProvider,
    dict[str, bytes],
    _RcaEvalPipelineProfile,
]:
    payloads: dict[str, bytes] = {}
    contracts: dict[str, RcaEvalResourceContract] = {}
    resources: list[dict[str, object]] = []
    groups: list[tuple[str, str, str, str]] = []

    index_id = "fixture.private.rcaeval.index.v1"
    index_table = _index_table()
    index_payload = _parquet(index_table)
    timing_contract = _contract(index_table, _TIMING_COLUMNS)
    answer_contract = _contract(index_table, _ANSWER_COLUMNS)

    definitions: list[tuple[str, str, bytes, ParquetContract]] = [
        (
            index_id,
            "rcaeval-fixture-index.parquet",
            index_payload,
            timing_contract,
        )
    ]
    for index in range(1, 6):
        prefix = f"fixture.private.rcaeval.slot-{index:02d}"
        metric_id = f"{prefix}.metrics.v1"
        log_id = f"{prefix}.logs.v1"
        trace_id = f"{prefix}.traces.v1"
        metric_table = _metrics_table(index)
        log_table = _logs_table(index)
        trace_table = _traces_table(index)
        definitions.extend(
            (
                (
                    metric_id,
                    f"rcaeval-fixture-slot-{index:02d}-metrics.parquet",
                    _parquet(metric_table),
                    _contract(metric_table, tuple(metric_table.schema.names)),
                ),
                (
                    log_id,
                    f"rcaeval-fixture-slot-{index:02d}-logs.parquet",
                    _parquet(log_table),
                    _contract(log_table, _LOG_COLUMNS),
                ),
                (
                    trace_id,
                    f"rcaeval-fixture-slot-{index:02d}-traces.parquet",
                    _parquet(trace_table),
                    _contract(trace_table, _TRACE_COLUMNS),
                ),
            )
        )
        groups.append((_slot(index), metric_id, log_id, trace_id))

    adapter_by_suffix = {
        ".index.v1": "rcaeval_case_index_fixture_v1",
        ".metrics.v1": "rcaeval_metrics_fixture_v1",
        ".logs.v1": "rcaeval_logs_fixture_v1",
        ".traces.v1": "rcaeval_traces_fixture_v1",
    }
    for resource_id, filename, payload, parquet_contract in definitions:
        adapter = next(
            value
            for suffix, value in adapter_by_suffix.items()
            if resource_id.endswith(suffix)
        )
        digest = hashlib.sha256(payload).hexdigest()
        payloads[resource_id] = payload
        contracts[resource_id] = RcaEvalResourceContract(
            resource_id=resource_id,
            size_bytes=len(payload),
            sha256=digest,
            adapter=adapter,
            parquet=parquet_contract,
        )
        resources.append(
            {
                "resource_id": resource_id,
                "dataset_id": _DATASET_ID,
                "dataset_version": _DATASET_VERSION,
                "filename": filename,
                "source_url": f"https://{_HOST}/{filename}",
                "allowed_hosts": [_HOST],
                "sha256": digest,
                "size_bytes": len(payload),
                "media_type": "application/vnd.apache.parquet",
                "adapter": adapter,
                "license": {
                    "id": _LICENSE_ID,
                    "name": "MIT License",
                    "url": "https://opensource.org/license/mit/",
                    "evidence_url": f"https://{_HOST}/LICENSE",
                    "evidence_sha256": "a" * 64,
                    "attribution": "Code-generated RCAEval schema fixture",
                    "reviewed_at": "2026-08-31",
                    "acceptance_required": True,
                },
            }
        )

    resource_ids = tuple(item[0] for item in definitions)
    profile = _RcaEvalPipelineProfile(
        catalog_id=_CATALOG_ID,
        catalog_version=_CATALOG_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=_DATASET_VERSION,
        license_id=_LICENSE_ID,
        resource_ids=resource_ids,
        resource_contracts=contracts,
        index_resource_id=index_id,
        case_timing_contract=timing_contract,
        case_answer_contract=answer_contract,
        case_key_sha256_by_slot={
            _slot(index): case_key_sha256(_case_key(index)) for index in range(1, 6)
        },
        telemetry_groups=tuple(groups),
        classification=RCAEVAL_FIXTURE_CLASSIFICATION,
        sample_count=5,
        total_bytes=sum(len(payload) for payload in payloads.values()),
    )
    provider = FixtureCatalogProvider(
        {
            "schema_version": "1.0",
            "catalog_id": _CATALOG_ID,
            "catalog_version": _CATALOG_VERSION,
            "resources": resources,
        }
    )
    return provider, payloads, profile


class _FixtureDownloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads
        self.calls: list[str] = []

    def download(self, resource, target: Path) -> DownloadReceipt:
        self.calls.append(resource.resource_id)
        payload = self._payloads[resource.resource_id]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return DownloadReceipt(
            resource_id=resource.resource_id,
            filename=resource.filename,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            cached=False,
        )


class _BombDownloader:
    def download(self, *_args, **_kwargs):
        raise AssertionError("offline RCAEval evaluation attempted a download")


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_nested_keys(item))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for item in value:
            keys.update(_nested_keys(item))
        return keys
    return set()


def _observe_real_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    events: list[str] = []

    def observed(name: str, real: Callable):
        def wrapper(*args, **kwargs):
            events.append(name)
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        pipeline_module,
        "load_case_timings",
        observed("timing", load_case_timings),
    )
    monkeypatch.setattr(
        pipeline_module,
        "adapt_rcaeval_cases",
        observed("adapt", adapt_rcaeval_cases),
    )
    monkeypatch.setattr(
        pipeline_module,
        "rank_rca_features",
        observed("rank", rank_rca_features),
    )
    monkeypatch.setattr(
        pipeline_module,
        "create_ranking_batch_commitment",
        observed("commit", create_ranking_batch_commitment),
    )
    monkeypatch.setattr(
        pipeline_module,
        "load_case_answers",
        observed("reveal", load_case_answers),
    )
    monkeypatch.setattr(
        pipeline_module,
        "verify_ranking_batch_commitment",
        observed("post-reveal-verify", verify_ranking_batch_commitment),
    )
    monkeypatch.setattr(
        pipeline_module,
        "evaluate_rca_rankings",
        observed("evaluate", evaluate_rca_rankings),
    )
    return events


def _assert_safe_summary(
    summary: dict[str, object],
    *,
    profile: _RcaEvalPipelineProfile,
    workspace: Path,
) -> None:
    assert summary["pipeline_id"] == RCAEVAL_PIPELINE_ID
    assert summary["classification"] == RCAEVAL_FIXTURE_CLASSIFICATION
    assert summary["dataset"] == {
        "artifact_count": 16,
        "artifact_closure_sha256": summary["dataset"]["artifact_closure_sha256"],
        "catalog_id": _CATALOG_ID,
        "catalog_version": _CATALOG_VERSION,
        "dataset_id": _DATASET_ID,
        "dataset_version": _DATASET_VERSION,
        "license": {
            "attribution": "Code-generated RCAEval schema fixture",
            "evidence_sha256": "a" * 64,
            "id": _LICENSE_ID,
        },
        "sample_count": 5,
        "total_bytes": profile.total_bytes,
    }
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        summary["dataset"]["artifact_closure_sha256"],
    )

    evaluation = summary["evaluation"]
    assert isinstance(evaluation, dict)
    assert evaluation["sample_count"] == 5
    assert evaluation["ranked_count"] == 5
    assert evaluation["inconclusive_count"] == 0
    assert evaluation["ac_at_1_ppm"] == 1_000_000
    assert evaluation["ac_at_5_ppm"] == 1_000_000
    assert evaluation["mean_reciprocal_rank_ppm"] == 1_000_000
    assert evaluation["ranked_reference_count"] == 15
    assert evaluation["truth_owned_reference_count"] == 15
    assert evaluation["candidate_ownership_validity_ppm"] == 1_000_000
    assert "evidence_validity_ppm" not in evaluation

    protocol = summary["protocol"]
    assert isinstance(protocol, dict)
    assert protocol["answer_blind_ranking"] is True
    assert protocol["commitment_created_before_answer_reveal"] is True
    assert protocol["post_reveal_commitment_validation"] == "PASS"
    assert protocol["ranking_reused_after_reveal"] is True
    assert protocol["sealed_ranking_count"] == 5
    assert protocol["ranking_algorithm"] == ("networkagent-multisource-shift-v1")
    assert protocol["externally_timestamped"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", protocol["batch_commitment_sha256"])
    privacy = summary["privacy"]
    assert isinstance(privacy, dict)
    assert privacy["status"] == "PASS"

    not_claimed = summary["not_claimed"]
    assert isinstance(not_claimed, list)
    assert not_claimed == [
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

    assert _nested_keys(summary).isdisjoint(_FORBIDDEN_KEYS)
    rendered = json.dumps(summary, allow_nan=False, sort_keys=True).lower()
    assert "rcaslot-" not in rendered
    assert "rcaevidence-" not in rendered
    assert "lablock-" not in rendered
    assert "https://" not in rendered
    assert _HOST not in rendered
    assert ".parquet" not in rendered
    assert str(workspace).lower() not in rendered
    for index in range(1, 6):
        assert _case_key(index) not in rendered
        assert _candidate(index) not in rendered
        assert f"private_raw_case_{index}" not in rendered
        assert f"private_log_case_{index}" not in rendered
        assert f"private_trace_case_{index}" not in rendered
    for resource_id in profile.resource_ids:
        assert resource_id.lower() not in rendered


def test_code_generated_exact16_fetch_commit_reveal_and_offline_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, payloads, profile = _fixture()
    downloader = _FixtureDownloader(payloads)
    events = _observe_real_pipeline(monkeypatch)
    lab = TelcoLab(
        provider,
        tmp_path,
        downloader=downloader,  # type: ignore[arg-type]
    )

    fetched = _fetch_and_evaluate_rcaeval_for_test(
        lab,
        accepted_license=_LICENSE_ID,
        profile=profile,
    )

    assert downloader.calls == list(profile.resource_ids)
    manifest = lab.verified_manifest()
    assert manifest.catalog_id == _CATALOG_ID
    assert manifest.catalog_version == _CATALOG_VERSION
    assert len(manifest.artifacts) == 16
    assert {item.resource_id for item in manifest.artifacts} == set(
        profile.resource_ids
    )
    expected_flow = [
        "timing",
        "adapt",
        *("rank" for _ in range(5)),
        "commit",
        "reveal",
        "post-reveal-verify",
        "evaluate",
    ]
    assert events == expected_flow

    fetched_summary = fetched.summary()
    _assert_safe_summary(fetched_summary, profile=profile, workspace=tmp_path)

    events.clear()
    offline_lab = TelcoLab(
        provider,
        tmp_path,
        downloader=_BombDownloader(),  # type: ignore[arg-type]
    )
    replayed = _evaluate_cached_rcaeval_for_test(
        offline_lab,
        profile=profile,
    )

    assert events == expected_flow
    replayed_summary = replayed.summary()
    assert replayed_summary == fetched_summary
    _assert_safe_summary(replayed_summary, profile=profile, workspace=tmp_path)
