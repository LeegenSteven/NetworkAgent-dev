"""Evaluate sealed RCA rankings against a private opaque-slot answer map."""

from __future__ import annotations

import re
from collections.abc import Mapping
from fractions import Fraction
from itertools import islice

from .rcaeval_models import (
    MAX_FEATURE_AGGREGATES,
    MAX_FEATURE_CANDIDATES,
    PPM_SCALE,
    EvaluationError,
    RcaEvaluationReport,
    RcaRankedCandidate,
    RcaRanking,
    RcaRankingSeal,
    RcaTruth,
)


MAX_EVALUATION_SEALS = 10_000
MAX_EVIDENCE_REFERENCES = 100_000
_EXTERNAL_SLOT_PATTERN = re.compile(r"rcaslot-[0-9a-f]{64}\Z", re.ASCII)


def _ppm(value: Fraction) -> int:
    if value <= 0:
        return 0
    if value >= 1:
        return PPM_SCALE
    return value.numerator * PPM_SCALE // value.denominator


def _seal_evidence_count(seal: RcaRankingSeal) -> int:
    try:
        ranking = seal.ranking
        if type(ranking) is not RcaRanking:
            raise EvaluationError("evaluation_invalid_input")
        candidates = ranking.candidates
        candidate_count = len(candidates)
    except EvaluationError:
        raise
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    if candidate_count > MAX_FEATURE_CANDIDATES:
        raise EvaluationError("evaluation_limit_exceeded")
    if type(candidates) is not tuple:
        raise EvaluationError("evaluation_invalid_input")

    evidence_count = 0
    for item in candidates:
        if type(item) is not RcaRankedCandidate:
            raise EvaluationError("evaluation_invalid_input")
        try:
            item_evidence_count = len(item.evidence_ids)
        except Exception as error:
            raise EvaluationError("evaluation_invalid_input") from error
        if item_evidence_count > MAX_FEATURE_AGGREGATES:
            raise EvaluationError("evaluation_limit_exceeded")
        if type(item.evidence_ids) is not tuple:
            raise EvaluationError("evaluation_invalid_input")
        evidence_count += item_evidence_count
        if evidence_count > MAX_EVIDENCE_REFERENCES:
            raise EvaluationError("evaluation_limit_exceeded")
    return evidence_count


def _copied_mapping_items(
    value: object,
    *,
    require_nonempty: bool,
) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, Mapping):
        raise EvaluationError("evaluation_invalid_input")
    try:
        count = len(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise EvaluationError("evaluation_invalid_input") from error
    if require_nonempty and count < 1:
        raise EvaluationError("evaluation_invalid_input")
    if count > MAX_EVALUATION_SEALS:
        raise EvaluationError("evaluation_limit_exceeded")
    try:
        keys = tuple(islice(iter(value), count + 1))
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    if len(keys) != count:
        raise EvaluationError("evaluation_invalid_input")
    if any(type(key) is not str for key in keys):
        raise EvaluationError("evaluation_type_confusion")
    if len(keys) != len(set(keys)):
        raise EvaluationError("evaluation_invalid_input")
    if any(_EXTERNAL_SLOT_PATTERN.fullmatch(key) is None for key in keys):
        raise EvaluationError("evaluation_invalid_input")
    try:
        supplied = tuple((key, value[key]) for key in keys)
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    return tuple(sorted(supplied, key=lambda item: item[0]))


def _validated_seals(value: object) -> dict[str, RcaRankingSeal]:
    supplied = _copied_mapping_items(value, require_nonempty=True)
    if any(type(item) is not RcaRankingSeal for _, item in supplied):
        raise EvaluationError("evaluation_type_confusion")
    evidence_count = 0
    for _, item in supplied:
        evidence_count += _seal_evidence_count(item)
        if evidence_count > MAX_EVIDENCE_REFERENCES:
            raise EvaluationError("evaluation_limit_exceeded")
    normalized: dict[str, RcaRankingSeal] = {}
    try:
        for slot_id, item in supplied:
            normalized[slot_id] = RcaRankingSeal.model_validate(
                item.model_dump(mode="python"),
                strict=True,
            )
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    return normalized


def _validated_truths(
    value: object,
    *,
    slot_ids: tuple[str, ...],
) -> dict[str, RcaTruth]:
    supplied = _copied_mapping_items(value, require_nonempty=False)
    supplied_slot_ids = tuple(slot_id for slot_id, _ in supplied)
    if supplied_slot_ids != slot_ids:
        raise EvaluationError("evaluation_invalid_input")
    if any(type(item) is not RcaTruth for _, item in supplied):
        raise EvaluationError("evaluation_type_confusion")
    evidence_count = 0
    for _, item in supplied:
        try:
            item_evidence_count = len(item.valid_evidence_ids)
        except Exception as error:
            raise EvaluationError("evaluation_invalid_input") from error
        if item_evidence_count > MAX_FEATURE_AGGREGATES:
            raise EvaluationError("evaluation_limit_exceeded")
        if type(item.valid_evidence_ids) is not tuple:
            raise EvaluationError("evaluation_invalid_input")
        evidence_count += item_evidence_count
        if evidence_count > MAX_EVIDENCE_REFERENCES:
            raise EvaluationError("evaluation_limit_exceeded")
    normalized: dict[str, RcaTruth] = {}
    try:
        for slot_id, item in supplied:
            normalized[slot_id] = RcaTruth.model_validate(
                item.model_dump(mode="python"),
                strict=True,
            )
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    return normalized


def _evaluate_rca_rankings(
    seals_by_slot: Mapping[str, RcaRankingSeal],
    truth_by_slot: Mapping[str, RcaTruth],
) -> RcaEvaluationReport:
    """Score sealed rankings; private answers never flow back into ranking."""

    normalized_seals = _validated_seals(seals_by_slot)
    slot_ids = tuple(normalized_seals)
    truths = _validated_truths(truth_by_slot, slot_ids=slot_ids)
    hit_counts = [0, 0, 0, 0, 0]
    reciprocal_sum = Fraction(0, 1)
    ranked_count = 0
    evidence_reference_count = 0
    valid_evidence_reference_count = 0

    for slot_id, seal in normalized_seals.items():
        ranking = seal.ranking
        truth = truths[slot_id]
        if ranking.outcome == "RANKED":
            ranked_count += 1
        answer_rank: int | None = None
        valid_evidence = set(truth.valid_evidence_ids)
        for item in ranking.candidates:
            if item.candidate_id == truth.candidate_id:
                answer_rank = item.rank
            evidence_reference_count += len(item.evidence_ids)
            if evidence_reference_count > MAX_EVIDENCE_REFERENCES:
                raise EvaluationError("evaluation_limit_exceeded")
            for evidence_id in item.evidence_ids:
                if evidence_id in valid_evidence:
                    valid_evidence_reference_count += 1
        if answer_rank is not None:
            reciprocal_sum += Fraction(1, answer_rank)
            for index in range(answer_rank - 1, 5):
                hit_counts[index] += 1

    sample_count = len(normalized_seals)
    accuracies = tuple(Fraction(value, sample_count) for value in hit_counts)
    average_at_five = sum(accuracies, Fraction(0, 1)) / 5
    evidence_validity = (
        Fraction(valid_evidence_reference_count, evidence_reference_count)
        if evidence_reference_count
        else Fraction(0, 1)
    )
    try:
        return RcaEvaluationReport(
            sample_count=sample_count,
            ranked_count=ranked_count,
            inconclusive_count=sample_count - ranked_count,
            ac_at_1_ppm=_ppm(accuracies[0]),
            ac_at_2_ppm=_ppm(accuracies[1]),
            ac_at_3_ppm=_ppm(accuracies[2]),
            ac_at_4_ppm=_ppm(accuracies[3]),
            ac_at_5_ppm=_ppm(accuracies[4]),
            avg_at_5_ppm=_ppm(average_at_five),
            mrr_ppm=_ppm(reciprocal_sum / sample_count),
            evidence_reference_count=evidence_reference_count,
            valid_evidence_reference_count=valid_evidence_reference_count,
            evidence_validity_ppm=_ppm(evidence_validity),
        )
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error


def evaluate_rca_rankings(
    seals_by_slot: Mapping[str, RcaRankingSeal],
    truth_by_slot: Mapping[str, RcaTruth],
) -> RcaEvaluationReport:
    """Score sealed rankings behind a fixed, fully detached error boundary."""

    failure_code = "evaluation_invalid_input"
    try:
        return _evaluate_rca_rankings(seals_by_slot, truth_by_slot)
    except EvaluationError as error:
        failure_code = error.code
    except MemoryError:
        failure_code = "evaluation_limit_exceeded"
    except Exception:
        failure_code = "evaluation_invalid_input"
    raise EvaluationError(failure_code) from None  # type: ignore[arg-type]


__all__ = [
    "MAX_EVALUATION_SEALS",
    "MAX_EVIDENCE_REFERENCES",
    "evaluate_rca_rankings",
]
