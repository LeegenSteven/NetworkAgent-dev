"""Answer-blind deterministic multi-modal shift ranking."""

from __future__ import annotations

import hashlib
from fractions import Fraction

from .rcaeval_models import (
    MAX_FEATURE_AGGREGATES,
    MAX_FEATURE_CANDIDATES,
    MAX_SHIFT_PPM,
    PPM_SCALE,
    EvaluationError,
    RcaFeatureSet,
    RcaRankedCandidate,
    RcaRanking,
    RcaRankingSeal,
)
from .schema import canonical_json_bytes


def _normalized(features: RcaFeatureSet) -> RcaFeatureSet:
    if type(features) is not RcaFeatureSet:
        raise EvaluationError("evaluation_type_confusion")
    try:
        count = len(features.aggregates)
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    if count > MAX_FEATURE_AGGREGATES:
        raise EvaluationError("evaluation_limit_exceeded")
    try:
        return RcaFeatureSet.model_validate(
            features.model_dump(mode="python"),
            strict=True,
        )
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error


def _shift(
    baseline_total: int,
    baseline_count: int,
    observed_total: int,
    observed_count: int,
) -> int:
    observed_cross = observed_total * baseline_count
    baseline_cross = baseline_total * observed_count
    delta = abs(observed_cross - baseline_cross)
    if abs(baseline_total) >= baseline_count:
        denominator = observed_count * abs(baseline_total)
    else:
        denominator = observed_count * baseline_count
    value = delta * PPM_SCALE // denominator
    return min(value, MAX_SHIFT_PPM)


def _rank_rca_features(features: RcaFeatureSet) -> RcaRankingSeal:
    """Return a sealed ranking using only bounded aggregates."""

    normalized = _normalized(features)
    feature_sha256 = hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()
    grouped: dict[str, dict[str, list[tuple[int, str]]]] = {}
    ordered = sorted(
        normalized.aggregates,
        key=lambda value: value.evidence_id,
    )
    for item in ordered:
        value = _shift(
            item.baseline_total,
            item.baseline_count,
            item.observed_total,
            item.observed_count,
        )
        if value <= 0:
            continue
        if item.candidate_id not in grouped:
            grouped[item.candidate_id] = {}
        candidate_values = grouped[item.candidate_id]
        if item.modality not in candidate_values:
            candidate_values[item.modality] = []
        candidate_values[item.modality].append((value, item.evidence_id))

    scored: list[tuple[Fraction, str, tuple[str, ...]]] = []
    for candidate_id in sorted(grouped):
        modality_values = grouped[candidate_id]
        if len(modality_values) < 2:
            continue
        modality_scores: list[int] = []
        evidence_ids: list[str] = []
        for modality in sorted(modality_values):
            entries = modality_values[modality]
            total = sum(entry[0] for entry in entries)
            modality_scores.append(total // len(entries))
            evidence_ids.extend(entry[1] for entry in entries)
        score = Fraction(
            sum(modality_scores),
            len(modality_scores) * PPM_SCALE,
        )
        if score > 0:
            scored.append((score, candidate_id, tuple(sorted(evidence_ids))))

    scored.sort(key=lambda item: (-item[0], item[1]))
    if len(scored) > MAX_FEATURE_CANDIDATES:
        raise EvaluationError("evaluation_limit_exceeded")
    numbered = enumerate(scored, start=1)
    candidates = tuple(
        RcaRankedCandidate(
            candidate_id=candidate_id,
            rank=index,
            score_numerator=score.numerator,
            score_denominator=score.denominator,
            evidence_ids=evidence_ids,
        )
        for index, (score, candidate_id, evidence_ids) in numbered
    )
    try:
        ranking = RcaRanking(
            outcome="RANKED" if candidates else "INCONCLUSIVE",
            candidates=candidates,
        )
        body = ranking.canonical_bytes()
        return RcaRankingSeal(
            feature_sha256=feature_sha256,
            ranking=ranking,
            ranking_sha256=hashlib.sha256(body).hexdigest(),
        )
    except EvaluationError:
        raise
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error


def rank_rca_features(features: RcaFeatureSet) -> RcaRankingSeal:
    """Rank bounded aggregates behind a fixed, fully detached error boundary."""

    failure_code = "evaluation_invalid_input"
    try:
        return _rank_rca_features(features)
    except EvaluationError as error:
        failure_code = error.code
    except MemoryError:
        failure_code = "evaluation_limit_exceeded"
    except Exception:
        failure_code = "evaluation_invalid_input"
    raise EvaluationError(failure_code) from None  # type: ignore[arg-type]


__all__ = ["rank_rca_features"]
