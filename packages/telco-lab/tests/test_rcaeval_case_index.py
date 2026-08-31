from __future__ import annotations

import io
from collections.abc import Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import telco_lab.rcaeval_case_index as case_module
from telco_lab.adapters import AdapterError
from telco_lab.parquet_reader import (
    ParquetContract,
    parquet_schema_fingerprint,
)
from telco_lab.rcaeval_case_index import (
    CaseAnswer,
    CaseTiming,
    load_case_answers,
    load_case_timings,
)
from telco_lab.rcaeval_commitment import (
    RcaRankingBatchCommitment,
    case_key_sha256,
    create_ranking_batch_commitment,
)
from telco_lab.rcaeval_models import EvidenceAggregate, RcaFeatureSet, RcaRankingSeal
from telco_lab.rcaeval_ranking import rank_rca_features


_TIMING_COLUMNS = ("case", "inject_time", "time_start", "time_end")
_ANSWER_COLUMNS = ("case", "root_cause_service")
_EARLY_READ_MESSAGE = "answer stream touched before commitment validation"


def _slot(index: int) -> str:
    return f"rcaslot-{index:064x}"


def _case_keys() -> dict[str, str]:
    return {_slot(index): f"private-case-{index}" for index in range(1, 6)}


def _selected() -> dict[str, str]:
    return {slot: case_key_sha256(case_key) for slot, case_key in _case_keys().items()}


def _case_table(
    *,
    order: tuple[int, ...] = (1, 2, 3, 4, 5, 99),
) -> pa.Table:
    start_values = [1_000 + index for index in order]
    end_values = [2_440 + index for index in order]
    return pa.table(
        {
            "case": pa.array(
                [f"private-case-{index}" for index in order],
                type=pa.large_string(),
            ),
            "inject_time": pa.array(
                [1_720 + index for index in order], type=pa.int64()
            ),
            "time_start": pa.array(start_values, type=pa.int64()),
            "time_end": pa.array(end_values, type=pa.int64()),
            "root_cause_service": pa.array(
                [
                    "frontendservice" if index == 1 else f"service{index}"
                    for index in order
                ],
                type=pa.large_string(),
            ),
            "private_label_canary": ["LABEL_CANARY"] * len(order),
        }
    )


def _bytes(table: pa.Table) -> bytes:
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


def _feature(index: int) -> RcaFeatureSet:
    return RcaFeatureSet(
        aggregates=(
            EvidenceAggregate(
                evidence_id=f"rcaevidence-{index * 2:064x}",
                candidate_id=f"service{index}",
                modality="METRIC",
                baseline_total=100,
                baseline_count=10,
                observed_total=150 + index,
                observed_count=10,
            ),
            EvidenceAggregate(
                evidence_id=f"rcaevidence-{index * 2 + 1:064x}",
                candidate_id=f"service{index}",
                modality="LOG",
                baseline_total=100,
                baseline_count=10,
                observed_total=175 + index,
                observed_count=10,
            ),
        )
    )


def _batch() -> tuple[dict[str, RcaFeatureSet], dict[str, RcaRankingSeal]]:
    features = {_slot(index): _feature(index) for index in range(1, 6)}
    seals = {slot: rank_rca_features(feature) for slot, feature in features.items()}
    return features, seals


def _commitment() -> tuple[
    RcaRankingBatchCommitment,
    dict[str, RcaFeatureSet],
    dict[str, RcaRankingSeal],
]:
    features, seals = _batch()
    commitment = create_ranking_batch_commitment(
        catalog_id="networkagent-open-data",
        catalog_version="1.1.0",
        dataset_id="rcaeval-re2ob-evaluation-slice",
        dataset_version="afeacb11bcc94dadfd1c8f483ee4377b2b8b614e",
        lock_id="lablock-" + "a" * 64,
        artifact_closure_sha256="b" * 64,
        case_key_sha256_by_slot=_selected(),
        features_by_slot=features,
        sealed_rankings=seals,
    )
    return commitment, features, seals


def test_timing_projection_selects_exactly_five_and_hides_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = _case_table()
    observed_projection: list[tuple[str, ...]] = []
    original = case_module.read_parquet_batches

    def _observed(stream, *, contract):
        observed_projection.append(contract.projected_columns)
        return original(stream, contract=contract)

    monkeypatch.setattr(case_module, "read_parquet_batches", _observed)
    timings = load_case_timings(
        io.BytesIO(_bytes(table)),
        contract=_contract(table, _TIMING_COLUMNS),
        case_key_sha256_by_slot=_selected(),
    )

    assert len(timings) == 5
    expected_slots = tuple(sorted(_selected()))
    assert tuple(item.opaque_slot for item in timings) == expected_slots
    assert all(type(item) is CaseTiming for item in timings)
    assert observed_projection == [_TIMING_COLUMNS]
    rendered = repr(timings)
    assert "root_cause" not in rendered
    assert "LABEL_CANARY" not in rendered
    assert "private-case" not in rendered


def test_timing_projection_is_deterministic_under_row_permutation() -> None:
    first_table = _case_table(order=(1, 2, 3, 4, 5, 99))
    replay_table = _case_table(order=(99, 5, 2, 4, 1, 3))
    first = load_case_timings(
        io.BytesIO(_bytes(first_table)),
        contract=_contract(first_table, _TIMING_COLUMNS),
        case_key_sha256_by_slot=_selected(),
    )
    replay = load_case_timings(
        io.BytesIO(_bytes(replay_table)),
        contract=_contract(replay_table, _TIMING_COLUMNS),
        case_key_sha256_by_slot=_selected(),
    )
    assert first == replay


@pytest.mark.parametrize(
    ("selected", "expected_code"),
    [
        (
            {
                _slot(index): case_key_sha256(f"private-case-{index}")
                for index in range(1, 5)
            },
            "adapter_invalid_input",
        ),
        (
            {
                **_selected(),
                _slot(6): case_key_sha256("private-case-6"),
            },
            "adapter_limit_exceeded",
        ),
        (
            {_slot(index): case_key_sha256("private-case-1") for index in range(1, 6)},
            "adapter_invalid_input",
        ),
        (_case_keys(), "adapter_invalid_input"),
    ],
)
def test_index_requires_five_unique_opaque_selections(
    selected: Mapping[str, str],
    expected_code: str,
) -> None:
    table = _case_table()
    with pytest.raises(AdapterError) as error:
        load_case_timings(
            io.BytesIO(_bytes(table)),
            contract=_contract(table, _TIMING_COLUMNS),
            case_key_sha256_by_slot=selected,
        )
    assert error.value.code == expected_code


def test_index_rejects_missing_duplicate_or_invalid_selected_rows() -> None:
    missing = _case_table(order=(1, 2, 3, 4, 99))
    with pytest.raises(AdapterError) as missing_error:
        load_case_timings(
            io.BytesIO(_bytes(missing)),
            contract=_contract(missing, _TIMING_COLUMNS),
            case_key_sha256_by_slot=_selected(),
        )
    assert missing_error.value.code == "adapter_invalid_input"

    duplicate = _case_table(order=(1, 1, 2, 3, 4, 5))
    with pytest.raises(AdapterError) as duplicate_error:
        load_case_timings(
            io.BytesIO(_bytes(duplicate)),
            contract=_contract(duplicate, _TIMING_COLUMNS),
            case_key_sha256_by_slot=_selected(),
        )
    assert duplicate_error.value.code == "adapter_invalid_input"

    invalid = _case_table().set_column(
        2,
        "time_start",
        pa.array([2_000, 1_002, 1_003, 1_004, 1_005, 1_099], type=pa.int64()),
    )
    with pytest.raises(AdapterError) as invalid_error:
        load_case_timings(
            io.BytesIO(_bytes(invalid)),
            contract=_contract(invalid, _TIMING_COLUMNS),
            case_key_sha256_by_slot=_selected(),
        )
    assert invalid_error.value.code == "adapter_invalid_input"


def test_answer_projection_is_commitment_gated_before_any_stream_read() -> None:
    class _BombStream:
        def __init__(self) -> None:
            self.touch_count = 0

        def read(self, *_args, **_kwargs):
            self.touch_count += 1
            raise AssertionError(_EARLY_READ_MESSAGE)

        def seek(self, *_args, **_kwargs):
            self.touch_count += 1
            raise AssertionError(_EARLY_READ_MESSAGE)

        def tell(self):
            self.touch_count += 1
            raise AssertionError(_EARLY_READ_MESSAGE)

        def readable(self):
            return True

        def seekable(self):
            return True

    class _ChangingSelection(Mapping[str, str]):
        def __init__(self) -> None:
            self._stable = _selected()
            self._first = dict(self._stable)
            first, second = tuple(sorted(self._stable))[:2]
            self._first[first], self._first[second] = (
                self._first[second],
                self._first[first],
            )
            self._lookups = 0

        def __getitem__(self, key: str) -> str:
            self._lookups += 1
            values = self._first if self._lookups <= 5 else self._stable
            return values[key]

        def __iter__(self):
            return iter(self._stable)

        def __len__(self) -> int:
            return len(self._stable)

    table = _case_table()
    commitment, features, seals = _commitment()
    first, second = tuple(sorted(seals))[:2]
    swapped_seals = dict(seals)
    swapped_seals[first], swapped_seals[second] = (
        swapped_seals[second],
        swapped_seals[first],
    )
    swapped_features = dict(features)
    swapped_features[first], swapped_features[second] = (
        swapped_features[second],
        swapped_features[first],
    )
    forged_commitment = commitment.model_copy(update={"commitment_sha256": "0" * 64})
    mismatched_selection = _selected()
    mismatched_selection.pop(_slot(5))
    mismatched_selection[_slot(6)] = case_key_sha256("private-case-6")
    missing_features = dict(features)
    missing_features.pop(first)
    swapped_selection = _selected()
    swapped_selection[first], swapped_selection[second] = (
        swapped_selection[second],
        swapped_selection[first],
    )

    invalid_inputs = (
        (commitment, features, seals, swapped_selection),
        (commitment, features, seals, _ChangingSelection()),
        (commitment, features, swapped_seals, _selected()),
        (commitment, swapped_features, seals, _selected()),
        (forged_commitment, features, seals, _selected()),
        (commitment, features, seals, mismatched_selection),
        (commitment, missing_features, seals, _selected()),
    )
    for invalid_commitment, invalid_features, invalid_seals, selected in invalid_inputs:
        stream = _BombStream()
        with pytest.raises(AdapterError) as error:
            load_case_answers(
                stream,
                contract=_contract(table, _ANSWER_COLUMNS),
                case_key_sha256_by_slot=selected,
                commitment=invalid_commitment,
                features_by_slot=invalid_features,
                sealed_rankings=invalid_seals,
            )
        assert error.value.code == "adapter_invalid_input"
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        assert stream.touch_count == 0


def test_answer_projection_runs_only_after_all_five_rankings_are_sealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = _case_table()
    commitment, features, seals = _commitment()
    observed_projection: list[tuple[str, ...]] = []
    original = case_module.read_parquet_batches

    def _observed(stream, *, contract):
        observed_projection.append(contract.projected_columns)
        return original(stream, contract=contract)

    monkeypatch.setattr(case_module, "read_parquet_batches", _observed)
    answers = load_case_answers(
        io.BytesIO(_bytes(table)),
        contract=_contract(table, _ANSWER_COLUMNS),
        case_key_sha256_by_slot=_selected(),
        commitment=commitment,
        features_by_slot=features,
        sealed_rankings=seals,
    )

    assert len(answers) == 5
    assert all(type(item) is CaseAnswer for item in answers)
    assert observed_projection == [_ANSWER_COLUMNS]
    assert answers[0].opaque_slot == _slot(1)
    assert answers[0].candidate_id == "frontend"
    assert "private-case" not in repr(answers)
    assert "LABEL_CANARY" not in repr(answers)


def test_answer_permutation_does_not_change_pre_reveal_commitment() -> None:
    table = _case_table()
    permuted = table.set_column(
        table.schema.get_field_index("root_cause_service"),
        "root_cause_service",
        pa.array(
            (
                "service5",
                "service4",
                "service3",
                "service2",
                "frontendservice",
                "service99",
            ),
            type=pa.large_string(),
        ),
    )
    commitment, features, seals = _commitment()
    before = commitment.commitment_sha256

    answers = load_case_answers(
        io.BytesIO(_bytes(permuted)),
        contract=_contract(permuted, _ANSWER_COLUMNS),
        case_key_sha256_by_slot=_selected(),
        commitment=commitment,
        features_by_slot=features,
        sealed_rankings=seals,
    )
    replayed = create_ranking_batch_commitment(
        catalog_id=commitment.catalog_id,
        catalog_version=commitment.catalog_version,
        dataset_id=commitment.dataset_id,
        dataset_version=commitment.dataset_version,
        lock_id=commitment.lock_id,
        artifact_closure_sha256=commitment.artifact_closure_sha256,
        case_key_sha256_by_slot=_selected(),
        features_by_slot=features,
        sealed_rankings=seals,
    )

    assert tuple(answer.candidate_id for answer in answers) == (
        "service5",
        "service4",
        "service3",
        "service2",
        "frontend",
    )
    assert commitment.commitment_sha256 == before
    assert replayed == commitment


def test_projection_contract_cannot_mix_timing_and_answer_columns() -> None:
    table = _case_table()
    commitment, features, seals = _commitment()
    unsafe_timing_projection = _TIMING_COLUMNS + ("root_cause_service",)
    with pytest.raises(AdapterError) as timing_error:
        load_case_timings(
            io.BytesIO(_bytes(table)),
            contract=_contract(table, unsafe_timing_projection),
            case_key_sha256_by_slot=_selected(),
        )
    assert timing_error.value.code == "adapter_unsafe_field"

    with pytest.raises(AdapterError) as answer_error:
        load_case_answers(
            io.BytesIO(_bytes(table)),
            contract=_contract(table, _ANSWER_COLUMNS + ("inject_time",)),
            case_key_sha256_by_slot=_selected(),
            commitment=commitment,
            features_by_slot=features,
            sealed_rankings=seals,
        )
    assert answer_error.value.code == "adapter_unsafe_field"


def test_projection_rejects_narrow_string_case_and_answer_fields() -> None:
    table = _case_table()
    narrow_case = table.set_column(
        table.schema.get_field_index("case"),
        "case",
        pa.array(table.column("case").to_pylist(), type=pa.string()),
    )
    with pytest.raises(AdapterError) as case_error:
        load_case_timings(
            io.BytesIO(_bytes(narrow_case)),
            contract=_contract(narrow_case, _TIMING_COLUMNS),
            case_key_sha256_by_slot=_selected(),
        )
    assert case_error.value.code == "adapter_unsafe_field"

    narrow_answers = table.set_column(
        table.schema.get_field_index("root_cause_service"),
        "root_cause_service",
        pa.array(
            table.column("root_cause_service").to_pylist(),
            type=pa.string(),
        ),
    )
    commitment, features, seals = _commitment()
    with pytest.raises(AdapterError) as answer_error:
        load_case_answers(
            io.BytesIO(_bytes(narrow_answers)),
            contract=_contract(narrow_answers, _ANSWER_COLUMNS),
            case_key_sha256_by_slot=_selected(),
            commitment=commitment,
            features_by_slot=features,
            sealed_rankings=seals,
        )
    assert answer_error.value.code == "adapter_unsafe_field"
