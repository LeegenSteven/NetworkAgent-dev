from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from telco_lab.rcaeval_models import (
    MAX_FEATURE_AGGREGATES,
    MAX_SAMPLE_COUNT,
    MAX_TOTAL_ABSOLUTE,
    EvidenceAggregate,
    RcaFeatureSet,
    RcaRankedCandidate,
    RcaRanking,
    RcaRankingSeal,
    RcaTruth,
)


def _evidence(index: int, *, candidate_id: str = "alpha") -> EvidenceAggregate:
    return EvidenceAggregate(
        evidence_id=f"rcaevidence-{index:064x}",
        candidate_id=candidate_id,
        modality="METRIC",
        baseline_total=100,
        baseline_count=10,
        observed_total=200,
        observed_count=10,
    )


def _candidate(
    candidate_id: str = "alpha",
    *,
    rank: int = 1,
    evidence_id: str = "rcaevidence-" + "1" * 64,
) -> RcaRankedCandidate:
    return RcaRankedCandidate(
        candidate_id=candidate_id,
        rank=rank,
        score_numerator=3,
        score_denominator=4,
        evidence_ids=(evidence_id,),
    )


def test_feature_contract_is_strict_frozen_label_free_and_bounded() -> None:
    aggregate = _evidence(1)
    features = RcaFeatureSet(aggregates=(aggregate,))

    assert features.aggregates == (aggregate,)
    with pytest.raises(ValidationError):
        aggregate.observed_total = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EvidenceAggregate(
            **{
                **aggregate.model_dump(mode="python"),
                "baseline_total": True,
            }
        )
    with pytest.raises(ValidationError):
        EvidenceAggregate(
            **{
                **aggregate.model_dump(mode="python"),
                "unexpected": 1,
            }
        )

    public_fields = {
        *RcaFeatureSet.model_fields,
        *EvidenceAggregate.model_fields,
    }
    forbidden = {
        "case",
        "path",
        "url",
        "resource",
        "source_artifact_sha256",
        "inject",
        "root",
        "fault",
        "label",
    }
    assert public_fields.isdisjoint(forbidden)
    for model_type in (RcaFeatureSet, RcaRanking, RcaRankingSeal):
        field_names = tuple(model_type.model_fields)
        assert all("slot" not in name.lower() for name in field_names)

    with pytest.raises(ValidationError):
        RcaFeatureSet(aggregates=(_evidence(1), _evidence(1)))

    oversized = tuple(
        _evidence(index + 1) for index in range(MAX_FEATURE_AGGREGATES + 1)
    )
    with pytest.raises(ValidationError):
        RcaFeatureSet(aggregates=oversized)


def test_aggregate_total_boundary_is_exact_and_below_signed_int64() -> None:
    assert MAX_TOTAL_ABSOLUTE == 10**18
    assert MAX_TOTAL_ABSOLUTE < 2**63

    for value in (-MAX_TOTAL_ABSOLUTE, MAX_TOTAL_ABSOLUTE):
        aggregate = EvidenceAggregate(
            **{
                **_evidence(1).model_dump(mode="python"),
                "baseline_total": value,
                "observed_total": value,
            }
        )
        assert aggregate.baseline_total == value
        assert aggregate.observed_total == value

    for value in (-MAX_TOTAL_ABSOLUTE - 1, MAX_TOTAL_ABSOLUTE + 1):
        with pytest.raises(ValidationError):
            EvidenceAggregate(
                **{
                    **_evidence(1).model_dump(mode="python"),
                    "baseline_total": value,
                }
            )


def test_aggregate_count_boundary_is_strict_and_positive() -> None:
    for value in (1, MAX_SAMPLE_COUNT):
        aggregate = EvidenceAggregate(
            **{
                **_evidence(1).model_dump(mode="python"),
                "baseline_count": value,
                "observed_count": value,
            }
        )
        assert aggregate.baseline_count == value
        assert aggregate.observed_count == value

    for value in (0, MAX_SAMPLE_COUNT + 1, True):
        with pytest.raises(ValidationError):
            EvidenceAggregate(
                **{
                    **_evidence(1).model_dump(mode="python"),
                    "baseline_count": value,
                }
            )


def test_ranking_contract_enforces_order_and_unique_references() -> None:
    first = _candidate()
    second = _candidate(
        "beta",
        rank=2,
        evidence_id="rcaevidence-" + "2" * 64,
    ).model_copy(update={"score_numerator": 1, "score_denominator": 2})
    ranking = RcaRanking(
        outcome="RANKED",
        candidates=(first, second),
    )
    assert ranking.algorithm == "networkagent-multisource-shift-v1"

    with pytest.raises(ValidationError):
        RcaRankedCandidate(
            **{
                **first.model_dump(mode="python"),
                "score_numerator": 6,
                "score_denominator": 8,
            }
        )
    with pytest.raises(ValidationError):
        RcaRanking(
            outcome="RANKED",
            candidates=(second, first),
        )
    with pytest.raises(ValidationError):
        RcaRanking(
            outcome="RANKED",
            candidates=(first, first),
        )
    with pytest.raises(ValidationError):
        RcaRanking(
            outcome="INCONCLUSIVE",
            candidates=(first,),
        )


def test_seal_binds_exact_canonical_bytes_and_digest() -> None:
    ranking = RcaRanking(
        outcome="RANKED",
        candidates=(_candidate(),),
    )
    body = ranking.canonical_bytes()
    digest = hashlib.sha256(body).hexdigest()
    feature_digest = "f" * 64
    seal = RcaRankingSeal(
        feature_sha256=feature_digest,
        ranking=ranking,
        ranking_sha256=digest,
    )

    assert seal.canonical_bytes() == body
    assert seal.feature_sha256 == feature_digest
    assert seal.ranking_sha256 == digest
    with pytest.raises(ValidationError):
        RcaRankingSeal(
            feature_sha256=feature_digest,
            ranking=ranking,
            ranking_sha256="0" * 64,
        )


def test_truth_is_private_strict_and_has_unique_sorted_evidence() -> None:
    truth = RcaTruth(
        candidate_id="alpha",
        valid_evidence_ids=(
            "rcaevidence-" + "1" * 64,
            "rcaevidence-" + "2" * 64,
        ),
    )
    assert truth.candidate_id == "alpha"
    with pytest.raises(ValidationError):
        RcaTruth(
            candidate_id="alpha",
            valid_evidence_ids=(
                "rcaevidence-" + "2" * 64,
                "rcaevidence-" + "1" * 64,
            ),
        )
    with pytest.raises(ValidationError):
        RcaTruth(
            candidate_id="alpha",
            valid_evidence_ids=("rcaevidence-" + "1" * 64,) * 2,
        )
