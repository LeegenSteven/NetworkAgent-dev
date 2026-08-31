from __future__ import annotations

import hashlib
import inspect
from fractions import Fraction

import pytest

import telco_lab.rcaeval_ranking as ranking_module
from telco_lab.rcaeval_models import (
    MAX_FEATURE_AGGREGATES,
    MAX_SHIFT_PPM,
    PPM_SCALE,
    EvaluationError,
    EvidenceAggregate,
    RcaFeatureSet,
)
from telco_lab.rcaeval_ranking import rank_rca_features
from telco_lab.schema import canonical_json_bytes


def _aggregate(
    index: int,
    candidate_id: str,
    modality: str,
    *,
    baseline_total: int = 100,
    observed_total: int = 150,
) -> EvidenceAggregate:
    return EvidenceAggregate(
        evidence_id=f"rcaevidence-{index:064x}",
        candidate_id=candidate_id,
        modality=modality,
        baseline_total=baseline_total,
        baseline_count=10,
        observed_total=observed_total,
        observed_count=10,
    )


def _features(*aggregates: EvidenceAggregate) -> RcaFeatureSet:
    return RcaFeatureSet(aggregates=aggregates)


def test_ranker_is_exact_deterministic_and_uses_stable_ties() -> None:
    values = (
        _aggregate(6, "gamma", "LOG"),
        _aggregate(1, "alpha", "METRIC", observed_total=200),
        _aggregate(4, "beta", "LOG"),
        _aggregate(2, "alpha", "LOG"),
        _aggregate(5, "gamma", "METRIC"),
        _aggregate(3, "beta", "METRIC"),
    )
    first = rank_rca_features(_features(*values))
    replay = rank_rca_features(_features(*reversed(values)))

    assert first.ranking.outcome == "RANKED"
    assert [item.candidate_id for item in first.ranking.candidates] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert (
        first.ranking.candidates[0].score_numerator,
        first.ranking.candidates[0].score_denominator,
    ) == (3, 4)
    assert (
        first.ranking.candidates[1].score_numerator,
        first.ranking.candidates[1].score_denominator,
    ) == (1, 2)
    assert first.canonical_bytes() == replay.canonical_bytes()
    assert first.ranking_sha256 == replay.ranking_sha256
    assert (
        first.feature_sha256
        == hashlib.sha256(canonical_json_bytes(_features(*values))).hexdigest()
    )
    assert first.feature_sha256 != replay.feature_sha256
    assert first.ranking.candidates[0].evidence_ids == tuple(
        sorted(first.ranking.candidates[0].evidence_ids)
    )


def test_external_slot_binding_cannot_change_seal_bytes() -> None:
    features = _features(
        _aggregate(1, "alpha", "METRIC", observed_total=200),
        _aggregate(2, "alpha", "LOG"),
    )
    first = rank_rca_features(features)
    second = rank_rca_features(features)
    seals_by_slot = {
        "rcaslot-" + "1" * 64: first,
        "rcaslot-" + "2" * 64: second,
    }

    assert len(seals_by_slot) == 2
    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.feature_sha256 == second.feature_sha256
    assert first.ranking_sha256 == second.ranking_sha256


@pytest.mark.parametrize(
    "features",
    [
        RcaFeatureSet(aggregates=()),
        RcaFeatureSet(
            aggregates=(_aggregate(1, "alpha", "METRIC"),),
        ),
        RcaFeatureSet(
            aggregates=(
                _aggregate(1, "alpha", "METRIC", observed_total=100),
                _aggregate(2, "alpha", "LOG", observed_total=100),
            ),
        ),
    ],
)
def test_ranker_abstains_without_two_positive_modalities(
    features: RcaFeatureSet,
) -> None:
    seal = rank_rca_features(features)
    assert seal.ranking.outcome == "INCONCLUSIVE"
    assert seal.ranking.candidates == ()


def test_ranker_revalidates_type_duplicates_and_budget() -> None:
    with pytest.raises(EvaluationError) as wrong_type:
        rank_rca_features(True)  # type: ignore[arg-type]
    assert wrong_type.value.code == "evaluation_type_confusion"

    duplicate = _aggregate(1, "alpha", "METRIC")
    bypassed_duplicate = RcaFeatureSet.model_construct(
        aggregates=(duplicate, duplicate),
    )
    with pytest.raises(EvaluationError) as duplicate_error:
        rank_rca_features(bypassed_duplicate)
    assert duplicate_error.value.code == "evaluation_invalid_input"

    bypassed_budget = RcaFeatureSet.model_construct(
        aggregates=(duplicate,) * (MAX_FEATURE_AGGREGATES + 1),
    )
    with pytest.raises(EvaluationError) as budget_error:
        rank_rca_features(bypassed_budget)
    assert budget_error.value.code == "evaluation_limit_exceeded"


def test_ranker_detaches_malformed_aggregate_length_failure() -> None:
    class _LenBomb:
        def __len__(self) -> int:
            raise RuntimeError("PRIVATE-AGGREGATE-LEN-CANARY")

    malformed = RcaFeatureSet.model_construct(aggregates=_LenBomb())

    with pytest.raises(EvaluationError) as error:
        rank_rca_features(malformed)

    assert error.value.code == "evaluation_invalid_input"
    assert "PRIVATE-AGGREGATE" not in str(error.value)
    assert "PRIVATE-AGGREGATE" not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_ranker_bounds_extreme_integer_shifts() -> None:
    extreme = (
        _aggregate(
            1,
            "alpha",
            "METRIC",
            baseline_total=1,
            observed_total=10**15,
        ).model_copy(update={"baseline_count": 10**9, "observed_count": 1}),
        _aggregate(
            2,
            "alpha",
            "LOG",
            baseline_total=1,
            observed_total=10**15,
        ).model_copy(update={"baseline_count": 10**9, "observed_count": 1}),
    )
    seal = rank_rca_features(_features(*extreme))
    score = seal.ranking.candidates[0]
    expected = Fraction(MAX_SHIFT_PPM, PPM_SCALE)
    assert (score.score_numerator, score.score_denominator) == (
        expected.numerator,
        expected.denominator,
    )


def test_ranker_surface_contains_no_answer_or_origin_vocabulary() -> None:
    source = inspect.getsource(ranking_module).lower()
    for token in (
        "case",
        "path",
        "url",
        "resource",
        "inject",
        "root",
        "fault",
        "label",
        "slot",
    ):
        assert token not in source
    parameters = tuple(inspect.signature(rank_rca_features).parameters)
    assert parameters == ("features",)
