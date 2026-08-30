from __future__ import annotations

from datetime import UTC, datetime, timedelta
from collections.abc import Iterator, Sequence
from typing import cast

import pytest

import telco_lab.evaluation as evaluation_module
from telco_lab.evaluation import EvaluationError, evaluate_episodes, temporal_iou
from telco_lab.schema import LabEpisode, PredictedEpisode, stable_content_id


BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
RESOURCE_ID = "lab:5g-sa:gnb:0123456789abcdef01234567"
SOURCE_SHA = "a" * 64


def _truth(start: int, end: int) -> LabEpisode:
    window_start = BASE_TIME + timedelta(seconds=start)
    window_end = BASE_TIME + timedelta(seconds=end)
    payload = {
        "dataset_id": "fixture",
        "dataset_version": "v1",
        "source_artifact_sha256": SOURCE_SHA,
        "resource_id": RESOURCE_ID,
        "label": "persistent_interference",
        "window_start": window_start,
        "window_end": window_end,
        "sample_count": end - start + 1,
        "first_observation_id": stable_content_id("obs", {"row": start}),
        "last_observation_id": stable_content_id("obs", {"row": end}),
    }
    return LabEpisode(
        episode_id=stable_content_id("truth", payload),
        **payload,
    )


def _prediction(start: int, end: int, item: int) -> PredictedEpisode:
    window_start = BASE_TIME + timedelta(seconds=start)
    window_end = BASE_TIME + timedelta(seconds=end)
    payload = {
        "dataset_id": "fixture",
        "dataset_version": "v1",
        "source_artifact_sha256": "b" * 64,
        "source_item_number": item,
        "resource_id": RESOURCE_ID,
        "label": "persistent_interference",
        "window_start": window_start,
        "window_end": window_end,
        "detected_at": window_end + timedelta(seconds=1),
        "detector_id": "fixture-detector-v1",
        "score": 1.0,
        "features": ("ran.mac.ul_bler",),
    }
    return PredictedEpisode(
        prediction_id=stable_content_id("pred", payload),
        **payload,
    )


def test_evaluation_is_one_to_one_deterministic_and_reports_duration_metrics() -> None:
    truth = (_truth(0, 9), _truth(20, 29))
    predictions = (
        _prediction(0, 9, 1),
        _prediction(20, 24, 2),
        _prediction(40, 49, 3),
    )

    report = evaluate_episodes(truth, predictions, overlap_threshold=0.5)
    replay = evaluate_episodes(
        tuple(reversed(truth)),
        tuple(reversed(predictions)),
        overlap_threshold=0.5,
    )

    assert report.evaluation_id == replay.evaluation_id
    assert report.true_positives == 2
    assert report.false_positives == 1
    assert report.false_negatives == 0
    assert report.precision == pytest.approx(2 / 3)
    assert report.recall == 1.0
    assert report.f1 == pytest.approx(0.8)
    assert report.duration_precision == pytest.approx(0.6)
    assert report.duration_recall == pytest.approx(0.75)
    assert report.duration_f1 == pytest.approx(2 / 3)


def test_temporal_iou_uses_inclusive_one_second_dataset_windows() -> None:
    assert temporal_iou(_truth(0, 0), _prediction(0, 0, 1)) == 1.0
    assert temporal_iou(_truth(0, 9), _prediction(5, 14, 2)) == pytest.approx(1 / 3)


def test_evaluator_never_turns_ground_truth_into_predictions() -> None:
    truth = _truth(0, 9)
    report = evaluate_episodes((truth,), ())
    assert report.prediction_count == 0
    assert report.true_positives == 0
    assert report.false_negatives == 1
    assert report.recall == 0.0

    with pytest.raises(EvaluationError) as caught:
        evaluate_episodes((truth,), cast(tuple[PredictedEpisode, ...], (truth,)))
    assert caught.value.code == "evaluation_type_confusion"


def test_empty_answer_and_empty_predictions_are_a_deterministic_perfect_absence() -> None:
    report = evaluate_episodes((), ())
    assert report.precision == report.recall == report.f1 == 1.0
    assert report.duration_precision == report.duration_recall == 1.0


@pytest.mark.parametrize("threshold", [0, -0.1, 1.1, float("nan"), True])
def test_evaluation_rejects_invalid_overlap_threshold(threshold: float) -> None:
    with pytest.raises(EvaluationError) as caught:
        evaluate_episodes((), (), overlap_threshold=threshold)
    assert caught.value.code == "evaluation_invalid_input"


def test_evaluation_fails_closed_before_materializing_unbounded_overlap_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluation_module, "MAX_EVALUATION_CANDIDATES", 2)
    truth = (_truth(0, 9), _truth(1, 9))
    predictions = (_prediction(0, 9, 1), _prediction(1, 9, 2))

    with pytest.raises(EvaluationError) as caught:
        evaluate_episodes(truth, predictions)

    assert caught.value.code == "evaluation_limit_exceeded"


def test_evaluation_rejects_unsized_or_declared_oversize_inputs_without_consuming() -> None:
    class CountingIterable:
        consumed = 0

        def __iter__(self) -> Iterator[LabEpisode]:
            while True:
                self.consumed += 1
                yield _truth(0, 0)

    unsized = CountingIterable()
    with pytest.raises(EvaluationError) as unsized_error:
        evaluate_episodes(unsized, ())  # type: ignore[arg-type]
    assert unsized_error.value.code == "evaluation_invalid_input"
    assert unsized.consumed == 0

    class DeclaredOversize(Sequence[LabEpisode]):
        accessed = 0

        def __len__(self) -> int:
            return 10_001

        def __getitem__(self, _index: int) -> LabEpisode:
            self.accessed += 1
            return _truth(0, 0)

    oversize = DeclaredOversize()
    with pytest.raises(EvaluationError) as oversize_error:
        evaluate_episodes(oversize, ())
    assert oversize_error.value.code == "evaluation_limit_exceeded"
    assert oversize.accessed == 0
