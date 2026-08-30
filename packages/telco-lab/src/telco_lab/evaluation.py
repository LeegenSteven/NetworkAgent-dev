"""Deterministic evaluation of explicit detector predictions against labels."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, TypeVar

from .schema import (
    MAX_BUNDLE_EPISODES,
    EpisodeEvaluation,
    EpisodeMatch,
    LabEpisode,
    PredictedEpisode,
    stable_content_id,
)


MAX_EVALUATION_CANDIDATES = 100_000
_EpisodeT = TypeVar("_EpisodeT", LabEpisode, PredictedEpisode)


class EvaluationError(ValueError):
    """A fixed-message error for invalid or type-confused evaluation input."""

    _MESSAGES = {
        "evaluation_invalid_input": "The evaluation input is invalid.",
        "evaluation_type_confusion": (
            "Ground truth and detector predictions must use distinct types."
        ),
        "evaluation_limit_exceeded": "The evaluation input exceeds a safety limit.",
    }

    def __init__(self, code: Literal[
        "evaluation_invalid_input",
        "evaluation_type_confusion",
        "evaluation_limit_exceeded",
    ]) -> None:
        self.code = code
        super().__init__(self._MESSAGES[code])


def _inclusive_interval(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Convert inclusive one-second dataset bounds to a half-open interval."""

    return start, end + timedelta(seconds=1)


def temporal_iou(truth: LabEpisode, prediction: PredictedEpisode) -> float:
    """Return temporal IoU for matching resource/label intervals, otherwise 0."""

    if truth.resource_id != prediction.resource_id or truth.label != prediction.label:
        return 0.0
    truth_start, truth_end = _inclusive_interval(
        truth.window_start,
        truth.window_end,
    )
    prediction_start, prediction_end = _inclusive_interval(
        prediction.window_start,
        prediction.window_end,
    )
    intersection_seconds = max(
        0.0,
        (
            min(truth_end, prediction_end)
            - max(truth_start, prediction_start)
        ).total_seconds(),
    )
    if intersection_seconds == 0:
        return 0.0
    union_seconds = (
        max(truth_end, prediction_end) - min(truth_start, prediction_start)
    ).total_seconds()
    return intersection_seconds / union_seconds


def _metric(numerator: float, denominator: float, *, both_empty: bool) -> float:
    if denominator == 0:
        return 1.0 if both_empty else 0.0
    return numerator / denominator


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


type _Group = tuple[str, str]
type _Interval = tuple[datetime, datetime]


def _merged_intervals(
    episodes: Sequence[LabEpisode] | Sequence[PredictedEpisode],
) -> dict[_Group, tuple[_Interval, ...]]:
    grouped: dict[_Group, list[_Interval]] = defaultdict(list)
    for item in episodes:
        grouped[(item.resource_id, item.label)].append(
            _inclusive_interval(item.window_start, item.window_end)
        )

    result: dict[_Group, tuple[_Interval, ...]] = {}
    for key, intervals in grouped.items():
        merged: list[list[datetime]] = []
        for start, end in sorted(intervals):
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        result[key] = tuple((start, end) for start, end in merged)
    return result


def _total_seconds(groups: dict[_Group, tuple[_Interval, ...]]) -> float:
    return sum(
        (end - start).total_seconds()
        for intervals in groups.values()
        for start, end in intervals
    )


def _intersection_seconds(
    truth: dict[_Group, tuple[_Interval, ...]],
    predictions: dict[_Group, tuple[_Interval, ...]],
) -> float:
    result = 0.0
    for key in sorted(set(truth) & set(predictions)):
        truth_items = truth[key]
        prediction_items = predictions[key]
        truth_index = prediction_index = 0
        while truth_index < len(truth_items) and prediction_index < len(
            prediction_items
        ):
            truth_start, truth_end = truth_items[truth_index]
            prediction_start, prediction_end = prediction_items[prediction_index]
            result += max(
                0.0,
                (
                    min(truth_end, prediction_end)
                    - max(truth_start, prediction_start)
                ).total_seconds(),
            )
            if truth_end <= prediction_end:
                truth_index += 1
            else:
                prediction_index += 1
    return result


def _rounded(value: float) -> float:
    # Keep the report wire representation stable across harmless platform-level
    # floating-point noise while retaining more precision than the source data.
    return round(value, 12)


def _bounded_sequence(
    value: object,
    *,
    expected_type: type[_EpisodeT],
) -> tuple[_EpisodeT, ...]:
    """Copy at most the declared episode budget without consuming iterables."""

    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise EvaluationError("evaluation_invalid_input")
    try:
        item_count = len(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise EvaluationError("evaluation_invalid_input") from error
    if item_count > MAX_BUNDLE_EPISODES:
        raise EvaluationError("evaluation_limit_exceeded")
    try:
        items = tuple(value[index] for index in range(item_count))
    except Exception as error:
        raise EvaluationError("evaluation_invalid_input") from error
    if any(not isinstance(item, expected_type) for item in items):
        raise EvaluationError("evaluation_type_confusion")
    return items


def _candidate_matches(
    truth_items: tuple[LabEpisode, ...],
    prediction_items: tuple[PredictedEpisode, ...],
    *,
    threshold: float,
) -> list[tuple[float, str, str, LabEpisode, PredictedEpisode]]:
    """Enumerate only time-overlapping pairs with a strict work budget."""

    groups: dict[_Group, dict[str, list[LabEpisode | PredictedEpisode]]] = defaultdict(
        lambda: {"truth": [], "prediction": []}
    )
    for truth in truth_items:
        groups[(truth.resource_id, truth.label)]["truth"].append(truth)
    for prediction in prediction_items:
        groups[(prediction.resource_id, prediction.label)]["prediction"].append(
            prediction
        )

    candidates: list[tuple[float, str, str, LabEpisode, PredictedEpisode]] = []
    evaluated_pairs = 0
    for group in sorted(groups):
        events: list[
            tuple[datetime, int, str, LabEpisode | PredictedEpisode]
        ] = []
        for truth in groups[group]["truth"]:
            start, end = _inclusive_interval(truth.window_start, truth.window_end)
            events.append((end, 0, truth.episode_id, truth))
            events.append((start, 1, truth.episode_id, truth))
        for prediction in groups[group]["prediction"]:
            start, end = _inclusive_interval(
                prediction.window_start,
                prediction.window_end,
            )
            events.append((end, 0, prediction.prediction_id, prediction))
            events.append((start, 2, prediction.prediction_id, prediction))
        events.sort(key=lambda item: (item[0], item[1], item[2]))

        active_truth: dict[str, LabEpisode] = {}
        active_predictions: dict[str, PredictedEpisode] = {}

        def consider(truth: LabEpisode, prediction: PredictedEpisode) -> None:
            nonlocal evaluated_pairs
            if evaluated_pairs >= MAX_EVALUATION_CANDIDATES:
                raise EvaluationError("evaluation_limit_exceeded")
            evaluated_pairs += 1
            overlap = temporal_iou(truth, prediction)
            if overlap >= threshold:
                candidates.append(
                    (
                        overlap,
                        truth.episode_id,
                        prediction.prediction_id,
                        truth,
                        prediction,
                    )
                )

        for _instant, phase, item_id, item in events:
            if phase == 0:
                if isinstance(item, LabEpisode):
                    active_truth.pop(item_id, None)
                else:
                    active_predictions.pop(item_id, None)
            elif phase == 1:
                if not isinstance(item, LabEpisode):  # pragma: no cover - internal
                    raise EvaluationError("evaluation_invalid_input")
                for prediction_id in sorted(active_predictions):
                    consider(item, active_predictions[prediction_id])
                active_truth[item_id] = item
            else:
                if not isinstance(item, PredictedEpisode):  # pragma: no cover
                    raise EvaluationError("evaluation_invalid_input")
                for truth_id in sorted(active_truth):
                    consider(active_truth[truth_id], item)
                active_predictions[item_id] = item
    return candidates


def evaluate_episodes(
    ground_truth: Sequence[LabEpisode],
    predictions: Sequence[PredictedEpisode],
    *,
    overlap_threshold: float = 0.5,
) -> EpisodeEvaluation:
    """Evaluate only caller-supplied predictions with deterministic matching.

    No prediction is derived from a ground-truth label.  Runtime type checks are
    intentional: passing ``LabEpisode`` objects in the prediction position is
    rejected rather than silently treating the answer key as detector output.
    Candidate matches are sorted by descending temporal IoU and stable content
    identifiers, then greedily selected one-to-one.
    """

    if isinstance(overlap_threshold, bool) or not isinstance(
        overlap_threshold, (int, float)
    ):
        raise EvaluationError("evaluation_invalid_input")
    threshold = float(overlap_threshold)
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise EvaluationError("evaluation_invalid_input")
    truth_items = _bounded_sequence(ground_truth, expected_type=LabEpisode)
    prediction_items = _bounded_sequence(
        predictions,
        expected_type=PredictedEpisode,
    )

    truth_ids = tuple(item.episode_id for item in truth_items)
    prediction_ids = tuple(item.prediction_id for item in prediction_items)
    if len(truth_ids) != len(set(truth_ids)) or len(prediction_ids) != len(
        set(prediction_ids)
    ):
        raise EvaluationError("evaluation_invalid_input")

    ordered_truth = tuple(sorted(truth_items, key=lambda item: item.episode_id))
    ordered_predictions = tuple(
        sorted(prediction_items, key=lambda item: item.prediction_id)
    )
    candidates = _candidate_matches(
        ordered_truth,
        ordered_predictions,
        threshold=threshold,
    )
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    matched_truth: set[str] = set()
    matched_predictions: set[str] = set()
    matches: list[EpisodeMatch] = []
    for overlap, truth_id, prediction_id, _truth, _prediction in candidates:
        if truth_id in matched_truth or prediction_id in matched_predictions:
            continue
        matched_truth.add(truth_id)
        matched_predictions.add(prediction_id)
        matches.append(
            EpisodeMatch(
                ground_truth_episode_id=truth_id,
                prediction_id=prediction_id,
                temporal_iou=_rounded(overlap),
            )
        )
    matches.sort(
        key=lambda item: (item.ground_truth_episode_id, item.prediction_id)
    )

    true_positives = len(matches)
    false_positives = len(prediction_items) - true_positives
    false_negatives = len(truth_items) - true_positives
    both_empty = not truth_items and not prediction_items
    precision = _metric(
        true_positives,
        true_positives + false_positives,
        both_empty=both_empty,
    )
    recall = _metric(
        true_positives,
        true_positives + false_negatives,
        both_empty=both_empty,
    )

    truth_intervals = _merged_intervals(truth_items)
    prediction_intervals = _merged_intervals(prediction_items)
    truth_seconds = _total_seconds(truth_intervals)
    prediction_seconds = _total_seconds(prediction_intervals)
    overlap_seconds = _intersection_seconds(truth_intervals, prediction_intervals)
    duration_precision = _metric(
        overlap_seconds,
        prediction_seconds,
        both_empty=both_empty,
    )
    duration_recall = _metric(
        overlap_seconds,
        truth_seconds,
        both_empty=both_empty,
    )
    mean_iou = (
        sum(item.temporal_iou for item in matches) / len(matches)
        if matches
        else (1.0 if both_empty else 0.0)
    )

    report_payload = {
        "algorithm": "temporal-iou-one-to-one-v1",
        "overlap_threshold": _rounded(threshold),
        "ground_truth_count": len(truth_items),
        "prediction_count": len(prediction_items),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": _rounded(precision),
        "recall": _rounded(recall),
        "f1": _rounded(_f1(precision, recall)),
        "duration_precision": _rounded(duration_precision),
        "duration_recall": _rounded(duration_recall),
        "duration_f1": _rounded(_f1(duration_precision, duration_recall)),
        "mean_matched_iou": _rounded(mean_iou),
        "matches": tuple(matches),
    }
    identity_payload = {
        **report_payload,
        "matches": [item.model_dump(mode="json") for item in matches],
        "ground_truth_ids": sorted(truth_ids),
        "prediction_ids": sorted(prediction_ids),
    }
    return EpisodeEvaluation(
        evaluation_id=stable_content_id("eval", identity_payload),
        **report_payload,
    )


evaluate_predictions = evaluate_episodes


__all__ = [
    "EvaluationError",
    "MAX_EVALUATION_CANDIDATES",
    "evaluate_episodes",
    "evaluate_predictions",
    "temporal_iou",
]
