from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterator, Mapping
from types import MappingProxyType

import pytest
from pydantic import ValidationError

import telco_lab.rcaeval_commitment as commitment_module
import telco_lab.rcaeval_ranking as ranking_module
from telco_lab.adapters import AdapterError
from telco_lab.rcaeval_commitment import (
    RCA_RANKING_BATCH_COMMITMENT_SCHEMA,
    RcaRankingBatchCommitment,
    case_key_sha256,
    create_ranking_batch_commitment,
    verify_ranking_batch_commitment,
)
from telco_lab.rcaeval_models import (
    RCA_RANKING_ALGORITHM,
    EvidenceAggregate,
    RcaFeatureSet,
    RcaRankingSeal,
)
from telco_lab.rcaeval_ranking import rank_rca_features
from telco_lab.schema import canonical_json_bytes


def _slot(index: int) -> str:
    return f"rcaslot-{index:064x}"


def _aggregate(
    evidence_index: int,
    candidate_index: int,
    modality: str,
    observed: int,
) -> EvidenceAggregate:
    return EvidenceAggregate(
        evidence_id=f"rcaevidence-{evidence_index:064x}",
        candidate_id=f"service{candidate_index}",
        modality=modality,
        baseline_total=100,
        baseline_count=10,
        observed_total=observed,
        observed_count=10,
    )


def _feature(index: int) -> RcaFeatureSet:
    return RcaFeatureSet(
        aggregates=(
            _aggregate(index * 2, index, "METRIC", 150 + index),
            _aggregate(index * 2 + 1, index, "LOG", 175 + index),
        )
    )


def _batch() -> tuple[dict[str, RcaFeatureSet], dict[str, RcaRankingSeal]]:
    features = {_slot(index): _feature(index) for index in range(1, 6)}
    return features, {
        slot: rank_rca_features(feature) for slot, feature in features.items()
    }


def _identity() -> dict[str, str]:
    return {
        "catalog_id": "networkagent-open-data",
        "catalog_version": "1.1.0",
        "dataset_id": "rcaeval-re2ob-evaluation-slice",
        "dataset_version": "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e",
        "lock_id": "lablock-" + "a" * 64,
        "artifact_closure_sha256": "b" * 64,
    }


def _case_digests() -> dict[str, str]:
    return {
        _slot(index): case_key_sha256(f"private-case-{index}") for index in range(1, 6)
    }


def _commitment() -> tuple[
    RcaRankingBatchCommitment,
    dict[str, RcaFeatureSet],
    dict[str, RcaRankingSeal],
]:
    features, seals = _batch()
    value = create_ranking_batch_commitment(
        **_identity(),
        case_key_sha256_by_slot=_case_digests(),
        features_by_slot=features,
        sealed_rankings=seals,
    )
    return value, features, seals


class _DuplicateSlotMapping(Mapping[str, object]):
    def __init__(self, value: object) -> None:
        self._value = value

    def __getitem__(self, key: str) -> object:
        if key != _slot(1):
            raise KeyError(key)
        return self._value

    def __iter__(self) -> Iterator[str]:
        return iter((_slot(1),) * 5)

    def __len__(self) -> int:
        return 5


class _CanaryMapping(Mapping[str, object]):
    def __getitem__(self, _key: str) -> object:
        raise RuntimeError("PRIVATE-CANARY")

    def __iter__(self) -> Iterator[str]:
        return iter(_slot(index) for index in range(1, 6))

    def __len__(self) -> int:
        return 5


def test_case_key_digest_is_domain_separated_bounded_and_non_disclosing() -> None:
    case = "private-case-canary"
    digest = case_key_sha256(case)

    assert (
        digest
        == hashlib.sha256(
            b"networkagent-rcaeval-case-key-v1\0" + case.encode("utf-8")
        ).hexdigest()
    )
    assert len(digest) == 64
    assert case not in digest

    for invalid in (True, 1, "", " leading", "x" * 257):
        with pytest.raises(AdapterError) as error:
            case_key_sha256(invalid)  # type: ignore[arg-type]
        assert "private-case-canary" not in str(error.value)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


def test_commitment_binds_identity_closure_algorithm_features_and_rankings() -> None:
    commitment, features, seals = _commitment()

    assert commitment.schema_version == RCA_RANKING_BATCH_COMMITMENT_SCHEMA
    assert commitment.catalog_id == _identity()["catalog_id"]
    assert commitment.catalog_version == _identity()["catalog_version"]
    assert commitment.dataset_id == _identity()["dataset_id"]
    assert commitment.dataset_version == _identity()["dataset_version"]
    assert commitment.lock_id == _identity()["lock_id"]
    assert commitment.artifact_closure_count == 16
    assert commitment.artifact_closure_sha256 == _identity()["artifact_closure_sha256"]
    assert commitment.ranking_algorithm == RCA_RANKING_ALGORITHM
    assert commitment.externally_timestamped is False
    assert tuple(item.opaque_slot for item in commitment.items) == tuple(
        sorted(features)
    )
    for item in commitment.items:
        assert item.case_key_sha256 == _case_digests()[item.opaque_slot]
        assert (
            item.feature_sha256
            == hashlib.sha256(
                canonical_json_bytes(features[item.opaque_slot])
            ).hexdigest()
        )
        assert item.feature_sha256 == seals[item.opaque_slot].feature_sha256
        assert item.ranking_sha256 == seals[item.opaque_slot].ranking_sha256
    assert (
        commitment.commitment_sha256
        == hashlib.sha256(commitment.canonical_body_bytes()).hexdigest()
    )
    assert (
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=MappingProxyType(features),
            sealed_rankings=MappingProxyType(seals),
        )
        == commitment
    )

    with pytest.raises(ValidationError):
        commitment.catalog_id = "changed"  # type: ignore[misc]


def test_commitment_creation_and_verification_never_rerank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features, seals = _batch()

    def _rerank_forbidden(_features: RcaFeatureSet) -> RcaRankingSeal:
        raise AssertionError("commitment attempted to rerank")

    monkeypatch.setattr(ranking_module, "rank_rca_features", _rerank_forbidden)
    assert not hasattr(commitment_module, "rank_rca_features")
    source = inspect.getsource(commitment_module)
    assert "rcaeval_ranking" not in source
    assert "rank_rca_features" not in source
    commitment = create_ranking_batch_commitment(
        **_identity(),
        case_key_sha256_by_slot=_case_digests(),
        features_by_slot=features,
        sealed_rankings=seals,
    )

    assert (
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=seals,
        )
        == commitment
    )


def test_commitment_rejects_cross_slot_seal_and_feature_swaps() -> None:
    commitment, features, seals = _commitment()
    first, second = tuple(sorted(features))[:2]

    swapped_seals = dict(seals)
    swapped_seals[first], swapped_seals[second] = (
        swapped_seals[second],
        swapped_seals[first],
    )
    with pytest.raises(AdapterError) as seal_error:
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=swapped_seals,
        )
    assert seal_error.value.code == "adapter_invalid_input"

    swapped_features = dict(features)
    swapped_features[first], swapped_features[second] = (
        swapped_features[second],
        swapped_features[first],
    )
    with pytest.raises(AdapterError) as feature_error:
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=swapped_features,
            sealed_rankings=seals,
        )
    assert feature_error.value.code == "adapter_invalid_input"

    swapped_cases = _case_digests()
    swapped_cases[first], swapped_cases[second] = (
        swapped_cases[second],
        swapped_cases[first],
    )
    with pytest.raises(AdapterError) as case_error:
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=swapped_cases,
            features_by_slot=features,
            sealed_rankings=seals,
        )
    assert case_error.value.code == "adapter_invalid_input"


@pytest.mark.parametrize("target", ["features", "seals"])
@pytest.mark.parametrize("mutation", ["missing", "added"])
def test_commitment_rejects_missing_or_added_slots(
    target: str,
    mutation: str,
) -> None:
    commitment, features, seals = _commitment()
    changed_features: dict[str, object] = dict(features)
    changed_seals: dict[str, object] = dict(seals)
    selected = changed_features if target == "features" else changed_seals
    if mutation == "missing":
        selected.pop(_slot(1))
    else:
        selected[_slot(6)] = (
            _feature(6) if target == "features" else rank_rca_features(_feature(6))
        )

    with pytest.raises(AdapterError):
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=changed_features,
            sealed_rankings=changed_seals,
        )


@pytest.mark.parametrize(
    ("feature_value", "seal_value"),
    [
        (True, None),
        (1, None),
        ("feature", None),
        (None, True),
        (None, 1),
        (None, "seal"),
    ],
)
def test_commitment_revalidates_exact_feature_and_seal_types(
    feature_value: object | None,
    seal_value: object | None,
) -> None:
    commitment, features, seals = _commitment()
    changed_features: dict[str, object] = dict(features)
    changed_seals: dict[str, object] = dict(seals)
    if feature_value is not None:
        changed_features[_slot(1)] = feature_value
    if seal_value is not None:
        changed_seals[_slot(1)] = seal_value

    with pytest.raises(AdapterError) as error:
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=changed_features,
            sealed_rankings=changed_seals,
        )
    assert error.value.code == "adapter_invalid_input"


def test_commitment_rejects_forged_nested_models_and_digest() -> None:
    commitment, features, seals = _commitment()

    forged_feature = RcaFeatureSet.model_construct(aggregates=[])
    changed_features = dict(features)
    changed_features[_slot(1)] = forged_feature
    with pytest.raises(AdapterError):
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=changed_features,
            sealed_rankings=seals,
        )

    forged_seal = RcaRankingSeal.model_construct(
        feature_sha256=seals[_slot(1)].feature_sha256,
        ranking=seals[_slot(1)].ranking,
        ranking_sha256="0" * 64,
    )
    changed_seals = dict(seals)
    changed_seals[_slot(1)] = forged_seal
    with pytest.raises(AdapterError):
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=changed_seals,
        )

    forged_commitment = RcaRankingBatchCommitment.model_construct(
        **{
            **commitment.model_dump(mode="python"),
            "commitment_sha256": "0" * 64,
        }
    )
    with pytest.raises(AdapterError):
        verify_ranking_batch_commitment(
            forged_commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=seals,
        )


def test_commitment_strict_mapping_copy_rejects_duplicates_types_and_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commitment, features, seals = _commitment()

    with pytest.raises(AdapterError):
        create_ranking_batch_commitment(
            **_identity(),
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=True,  # type: ignore[arg-type]
            sealed_rankings=seals,
        )
    with pytest.raises(AdapterError):
        create_ranking_batch_commitment(
            **_identity(),
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=1,  # type: ignore[arg-type]
        )
    with pytest.raises(AdapterError):
        create_ranking_batch_commitment(
            **_identity(),
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=_DuplicateSlotMapping(features[_slot(1)]),
            sealed_rankings=seals,
        )
    with pytest.raises(AdapterError) as canary:
        create_ranking_batch_commitment(
            **_identity(),
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=_CanaryMapping(),
            sealed_rankings=seals,
        )
    assert "PRIVATE-CANARY" not in str(canary.value)
    assert canary.value.__cause__ is None
    assert canary.value.__context__ is None

    monkeypatch.setattr(commitment_module, "MAX_COMMITMENT_MAPPING_ITEMS", 4)
    with pytest.raises(AdapterError) as budget:
        verify_ranking_batch_commitment(
            commitment,
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=seals,
        )
    assert budget.value.code == "adapter_limit_exceeded"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("catalog_id", True),
        ("catalog_version", 1),
        ("dataset_id", False),
        ("dataset_version", 1),
        ("lock_id", True),
        ("artifact_closure_sha256", 1),
        ("artifact_closure_count", True),
        ("artifact_closure_count", 16.0),
        ("ranking_algorithm", True),
        ("externally_timestamped", True),
        ("externally_timestamped", 0),
        ("externally_timestamped", 1),
    ],
)
def test_commitment_rejects_bool_int_and_canary_without_disclosure(
    field: str,
    value: object,
) -> None:
    features, seals = _batch()
    identity: dict[str, object] = _identity()
    identity[field] = value
    identity["catalog_id"] = "PRIVATE-CANARY" if field != "catalog_id" else value

    with pytest.raises(AdapterError) as error:
        create_ranking_batch_commitment(
            **identity,  # type: ignore[arg-type]
            case_key_sha256_by_slot=_case_digests(),
            features_by_slot=features,
            sealed_rankings=seals,
        )
    assert "PRIVATE-CANARY" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
