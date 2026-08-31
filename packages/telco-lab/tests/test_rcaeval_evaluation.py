from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType

import pytest

import telco_lab.rcaeval_evaluation as evaluation_module
from telco_lab.rcaeval_evaluation import evaluate_rca_rankings
from telco_lab.rcaeval_models import (
    EvaluationError,
    EvidenceAggregate,
    RcaFeatureSet,
    RcaRankingSeal,
    RcaTruth,
)
from telco_lab.rcaeval_ranking import rank_rca_features


def _slot(value: str) -> str:
    return "rcaslot-" + value * 64


def _aggregate(
    sample_index: int,
    evidence_index: int,
    candidate_id: str,
    modality: str,
    observed_total: int,
) -> EvidenceAggregate:
    return EvidenceAggregate(
        evidence_id=f"rcaevidence-{sample_index:02x}{evidence_index:062x}",
        candidate_id=candidate_id,
        modality=modality,
        baseline_total=100,
        baseline_count=10,
        observed_total=observed_total,
        observed_count=10,
    )


def _seal(sample_index: int) -> RcaRankingSeal:
    features = RcaFeatureSet(
        aggregates=(
            _aggregate(sample_index, 1, "alpha", "METRIC", 200),
            _aggregate(sample_index, 2, "alpha", "LOG", 150),
            _aggregate(sample_index, 3, "beta", "METRIC", 150),
            _aggregate(sample_index, 4, "beta", "LOG", 150),
        ),
    )
    return rank_rca_features(features)


def _truth(
    sample_index: int,
    candidate_id: str,
    *,
    valid: int = 4,
) -> RcaTruth:
    evidence_prefix = f"rcaevidence-{sample_index:02x}"
    return RcaTruth(
        candidate_id=candidate_id,
        valid_evidence_ids=tuple(
            f"{evidence_prefix}{index:062x}" for index in range(1, valid + 1)
        ),
    )


class _DuplicateSlotMapping(Mapping[str, RcaRankingSeal]):
    def __init__(self, slot_id: str, seal: RcaRankingSeal) -> None:
        self._slot_id = slot_id
        self._seal = seal

    def __getitem__(self, key: str) -> RcaRankingSeal:
        if key != self._slot_id:
            raise KeyError(key)
        return self._seal

    def __iter__(self) -> Iterator[str]:
        return iter((self._slot_id, self._slot_id))

    def __len__(self) -> int:
        return 2


class _TruthLenBomb(Mapping[str, RcaTruth]):
    def __getitem__(self, _key: str) -> RcaTruth:
        raise AssertionError("PRIVATE-TRUTH-GETITEM-CANARY")

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        raise RuntimeError("PRIVATE-TRUTH-LEN-CANARY")


class _TruthGetItemBomb(Mapping[str, RcaTruth]):
    def __init__(self, slot_id: str) -> None:
        self._slot_id = slot_id

    def __getitem__(self, _key: str) -> RcaTruth:
        raise RuntimeError("PRIVATE-TRUTH-GETITEM-CANARY")

    def __iter__(self) -> Iterator[str]:
        return iter((self._slot_id,))

    def __len__(self) -> int:
        return 1


def test_evaluator_computes_ac_avg_mrr_and_evidence_ppm_exactly() -> None:
    first_slot = _slot("1")
    second_slot = _slot("2")
    first = _seal(1)
    second = _seal(2)
    report = evaluate_rca_rankings(
        {first_slot: first, second_slot: second},
        {
            first_slot: _truth(1, "alpha"),
            second_slot: _truth(2, "beta"),
        },
    )

    assert report.sample_count == 2
    assert report.ranked_count == 2
    assert report.inconclusive_count == 0
    assert report.ac_at_1_ppm == 500_000
    assert report.ac_at_2_ppm == 1_000_000
    assert report.ac_at_3_ppm == 1_000_000
    assert report.ac_at_4_ppm == 1_000_000
    assert report.ac_at_5_ppm == 1_000_000
    assert report.avg_at_5_ppm == 900_000
    assert report.mrr_ppm == 750_000
    assert report.evidence_reference_count == 8
    assert report.valid_evidence_reference_count == 8
    assert report.evidence_validity_ppm == 1_000_000


def test_answer_perturbation_changes_only_evaluation() -> None:
    slot_id = _slot("3")
    seal = _seal(3)
    seals_by_slot = {slot_id: seal}
    before_bytes = seal.canonical_bytes()
    before_digest = seal.ranking_sha256

    alpha = evaluate_rca_rankings(
        seals_by_slot,
        {slot_id: _truth(3, "alpha")},
    )
    beta = evaluate_rca_rankings(
        seals_by_slot,
        {slot_id: _truth(3, "beta")},
    )

    assert alpha.ac_at_1_ppm == 1_000_000
    assert beta.ac_at_1_ppm == 0
    assert alpha.mrr_ppm == 1_000_000
    assert beta.mrr_ppm == 500_000
    assert seal.canonical_bytes() == before_bytes
    assert seal.ranking_sha256 == before_digest


def test_evidence_validity_counts_references() -> None:
    slot_id = _slot("4")
    seal = _seal(4)
    report = evaluate_rca_rankings(
        {slot_id: seal},
        {slot_id: _truth(4, "alpha", valid=2)},
    )
    assert report.evidence_reference_count == 4
    assert report.valid_evidence_reference_count == 2
    assert report.evidence_validity_ppm == 500_000


def test_inconclusive_seal_is_a_miss_with_zero_evidence() -> None:
    slot_id = _slot("5")
    seal = rank_rca_features(
        RcaFeatureSet(
            aggregates=(_aggregate(5, 1, "alpha", "METRIC", 200),),
        )
    )
    report = evaluate_rca_rankings(
        {slot_id: seal},
        {slot_id: _truth(5, "alpha", valid=1)},
    )
    assert report.inconclusive_count == 1
    assert report.ac_at_5_ppm == 0
    assert report.mrr_ppm == 0
    assert report.evidence_reference_count == 0
    assert report.evidence_validity_ppm == 0


def test_evaluator_rejects_type_slot_seal_and_budget(monkeypatch) -> None:
    slot_id = _slot("6")
    seal = _seal(6)
    seals_by_slot = {slot_id: seal}
    truths = {slot_id: _truth(6, "alpha")}

    with pytest.raises(EvaluationError) as raw_ranking:
        evaluate_rca_rankings({slot_id: seal.ranking}, truths)
    assert raw_ranking.value.code == "evaluation_type_confusion"

    with pytest.raises(EvaluationError) as bool_mapping:
        evaluate_rca_rankings(True, truths)  # type: ignore[arg-type]
    assert bool_mapping.value.code == "evaluation_invalid_input"

    with pytest.raises(EvaluationError) as wrong_truth_type:
        evaluate_rca_rankings(seals_by_slot, {slot_id: "alpha"})
    assert wrong_truth_type.value.code == "evaluation_type_confusion"

    duplicate_slots = _DuplicateSlotMapping(slot_id, seal)
    with pytest.raises(EvaluationError) as duplicate_slot:
        evaluate_rca_rankings(duplicate_slots, truths)
    assert duplicate_slot.value.code == "evaluation_invalid_input"

    with pytest.raises(EvaluationError) as missing_truth:
        evaluate_rca_rankings(seals_by_slot, {})
    assert missing_truth.value.code == "evaluation_invalid_input"

    forged = RcaRankingSeal.model_construct(
        feature_sha256=seal.feature_sha256,
        ranking=seal.ranking,
        ranking_sha256="0" * 64,
    )
    with pytest.raises(EvaluationError) as forged_error:
        evaluate_rca_rankings({slot_id: forged}, truths)
    assert forged_error.value.code == "evaluation_invalid_input"

    monkeypatch.setattr(evaluation_module, "MAX_EVALUATION_SEALS", 1)
    other_slot = _slot("7")
    other = _seal(7)
    with pytest.raises(EvaluationError) as over_budget:
        evaluate_rca_rankings(
            {slot_id: seal, other_slot: other},
            {
                slot_id: _truth(6, "alpha"),
                other_slot: _truth(7, "alpha"),
            },
        )
    assert over_budget.value.code == "evaluation_limit_exceeded"


def test_evaluator_accepts_read_only_slot_mappings() -> None:
    slot_id = _slot("8")
    seal = _seal(8)
    seals_by_slot = MappingProxyType({slot_id: seal})
    truths = MappingProxyType({slot_id: _truth(8, "alpha")})

    report = evaluate_rca_rankings(seals_by_slot, truths)

    assert report.ac_at_1_ppm == 1_000_000


@pytest.mark.parametrize("truths", [_TruthLenBomb(), _TruthGetItemBomb(_slot("b"))])
def test_evaluator_detaches_private_truth_mapping_failures(
    truths: Mapping[str, RcaTruth],
) -> None:
    slot_id = _slot("b")

    with pytest.raises(EvaluationError) as error:
        evaluate_rca_rankings({slot_id: _seal(11)}, truths)

    assert error.value.code == "evaluation_invalid_input"
    assert "PRIVATE-TRUTH" not in str(error.value)
    assert "PRIVATE-TRUTH" not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_evaluator_preflights_nested_evidence_budgets(monkeypatch) -> None:
    slot_id = _slot("9")
    seal = _seal(9)
    truths = {slot_id: _truth(9, "alpha")}
    with monkeypatch.context() as seal_patch:
        seal_patch.setattr(
            evaluation_module,
            "MAX_EVIDENCE_REFERENCES",
            1,
        )
        seal_patch.setattr(
            RcaRankingSeal,
            "model_dump",
            lambda *_args, **_kwargs: pytest.fail("seal dump before budget"),
        )

        with pytest.raises(EvaluationError) as seal_budget:
            evaluate_rca_rankings({slot_id: seal}, truths)
        assert seal_budget.value.code == "evaluation_limit_exceeded"

    empty_features = RcaFeatureSet(aggregates=())
    inconclusive = rank_rca_features(empty_features)
    private_slot = _slot("a")
    truth = _truth(10, "alpha", valid=1)
    private_truth = {private_slot: truth}
    with monkeypatch.context() as truth_patch:
        truth_patch.setattr(
            evaluation_module,
            "MAX_EVIDENCE_REFERENCES",
            0,
        )
        truth_patch.setattr(
            RcaTruth,
            "model_dump",
            lambda *_args, **_kwargs: pytest.fail("truth dump before budget"),
        )

        with pytest.raises(EvaluationError) as truth_budget:
            evaluate_rca_rankings(
                {private_slot: inconclusive},
                private_truth,
            )
        assert truth_budget.value.code == "evaluation_limit_exceeded"
