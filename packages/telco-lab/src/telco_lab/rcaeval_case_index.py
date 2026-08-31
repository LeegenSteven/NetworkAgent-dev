"""Private RCAEval case timing and post-seal answer projections."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from typing import BinaryIO

import pyarrow as pa

from .adapters import AdapterError
from .parquet_reader import ParquetContract, read_parquet_batches
from .rcaeval_commitment import (
    RcaRankingBatchCommitment,
    case_key_sha256,
    verify_ranking_batch_commitment,
)
from .rcaeval_models import RcaFeatureSet, RcaRankingSeal


_SLOT = re.compile(r"^rcaslot-[0-9a-f]{64}$")
_CASE_KEY_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_TIMING_COLUMNS = ("case", "inject_time", "time_start", "time_end")
_ANSWER_COLUMNS = ("case", "root_cause_service")
_CASE_COUNT = 5
_MAX_TIME = 10**18
MAX_CASE_INDEX_MAPPING_ITEMS = _CASE_COUNT


def _invalid() -> AdapterError:
    return AdapterError("adapter_invalid_input")


def _unsafe() -> AdapterError:
    return AdapterError("adapter_unsafe_field")


def _limit() -> AdapterError:
    return AdapterError("adapter_limit_exceeded")


def _detached(error: AdapterError) -> AdapterError:
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = False
    error.__traceback__ = None
    return error


def _validate_time(value: object) -> int:
    if type(value) is not int or value < 0 or value > _MAX_TIME:
        raise _invalid()
    return value


def _normalize_candidate(value: object) -> str:
    if type(value) is not str:
        raise _invalid()
    candidate = "frontend" if value == "frontendservice" else value
    if len(candidate) > 64 or _CANDIDATE.fullmatch(candidate) is None:
        raise _unsafe()
    return candidate


def _validate_opaque_slot(value: object) -> str:
    if type(value) is not str or _SLOT.fullmatch(value) is None:
        raise _invalid()
    return value


@dataclass(frozen=True, slots=True)
class CaseTiming:
    """Label-free window for one private opaque evaluation slot."""

    opaque_slot: str
    inject_time: int
    time_start: int
    time_end: int

    def __post_init__(self) -> None:
        _validate_opaque_slot(self.opaque_slot)
        start = _validate_time(self.time_start)
        inject = _validate_time(self.inject_time)
        end = _validate_time(self.time_end)
        if not start < inject <= end:
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class CaseAnswer:
    """Evaluator-only label opened only after rankings are sealed."""

    opaque_slot: str
    candidate_id: str

    def __post_init__(self) -> None:
        _validate_opaque_slot(self.opaque_slot)
        normalized = _normalize_candidate(self.candidate_id)
        if normalized != self.candidate_id:
            raise _invalid()


def _selected_case_digests(
    case_key_sha256_by_slot: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    if not isinstance(case_key_sha256_by_slot, Mapping):
        raise _invalid()
    try:
        count = len(case_key_sha256_by_slot)
    except Exception:
        raise _invalid() from None
    if type(count) is not int or count < 0:
        raise _invalid()
    if count > MAX_CASE_INDEX_MAPPING_ITEMS:
        raise _limit()
    if count != _CASE_COUNT:
        raise _invalid()
    try:
        slots = tuple(islice(iter(case_key_sha256_by_slot), count + 1))
    except Exception:
        raise _invalid() from None
    if len(slots) != count:
        raise _invalid()
    if any(type(slot) is not str for slot in slots):
        raise _invalid()
    if len(slots) != len(set(slots)):
        raise _invalid()
    try:
        values = tuple(
            (
                _validate_opaque_slot(slot),
                case_key_sha256_by_slot[slot],
            )
            for slot in slots
        )
    except AdapterError:
        raise
    except Exception:
        raise _invalid() from None
    if any(
        type(digest) is not str or _CASE_KEY_SHA256.fullmatch(digest) is None
        for _slot, digest in values
    ):
        raise _invalid()
    if len({item[1] for item in values}) != _CASE_COUNT:
        raise _invalid()
    return tuple(sorted(values))


def _require_projection(
    contract: ParquetContract,
    expected: tuple[str, ...],
) -> None:
    if type(contract) is not ParquetContract:
        raise _invalid()
    if contract.projected_columns != expected:
        raise _unsafe()
    required_types = {
        "case": pa.large_string(),
        "inject_time": pa.int64(),
        "time_start": pa.int64(),
        "time_end": pa.int64(),
        "root_cause_service": pa.large_string(),
    }
    for column in expected:
        try:
            field = contract.expected_schema.field(column)
        except (KeyError, IndexError):
            raise _unsafe() from None
        if field.type != required_types[column]:
            raise _unsafe()


def _columns(batch, names: tuple[str, ...]):
    try:
        return tuple(batch.column(name) for name in names)
    except Exception:
        raise _invalid() from None


def _value(column, index: int) -> object:
    try:
        return column[index].as_py()
    except Exception:
        raise _invalid() from None


def _load_case_timings(
    stream: BinaryIO,
    *,
    contract: ParquetContract,
    case_key_sha256_by_slot: Mapping[str, str],
) -> tuple[CaseTiming, ...]:
    """Project timing fields for exactly five selected cases."""

    selected = _selected_case_digests(case_key_sha256_by_slot)
    _require_projection(contract, _TIMING_COLUMNS)
    slots_by_digest = {digest: slot for slot, digest in selected}
    found: dict[str, CaseTiming] = {}
    for batch in read_parquet_batches(stream, contract=contract):
        case_keys, inject_values, start_values, end_values = _columns(
            batch,
            _TIMING_COLUMNS,
        )
        for index in range(batch.num_rows):
            digest = case_key_sha256(_value(case_keys, index))  # type: ignore[arg-type]
            slot = slots_by_digest.get(digest)
            if slot is None:
                continue
            if slot in found:
                raise _invalid()
            found[slot] = CaseTiming(
                opaque_slot=slot,
                inject_time=_validate_time(_value(inject_values, index)),
                time_start=_validate_time(_value(start_values, index)),
                time_end=_validate_time(_value(end_values, index)),
            )
    if set(found) != {item[0] for item in selected}:
        raise _invalid()
    return tuple(found[slot] for slot, _digest in selected)


def load_case_timings(
    stream: BinaryIO,
    *,
    contract: ParquetContract,
    case_key_sha256_by_slot: Mapping[str, str],
) -> tuple[CaseTiming, ...]:
    """Project timing fields for exactly five selected cases."""

    try:
        return _load_case_timings(
            stream,
            contract=contract,
            case_key_sha256_by_slot=case_key_sha256_by_slot,
        )
    except AdapterError as error:
        failure = error
    except Exception:
        failure = _invalid()
    raise _detached(failure)


def _load_case_answers(
    stream: BinaryIO,
    *,
    contract: ParquetContract,
    case_key_sha256_by_slot: Mapping[str, str],
    commitment: RcaRankingBatchCommitment,
    features_by_slot: Mapping[str, RcaFeatureSet],
    sealed_rankings: Mapping[str, RcaRankingSeal],
) -> tuple[CaseAnswer, ...]:
    """Open answers only after the complete batch commitment validates."""

    selected = _selected_case_digests(case_key_sha256_by_slot)
    selected_mapping = dict(selected)
    selected_slots = tuple(slot for slot, _digest in selected)
    normalized_commitment = verify_ranking_batch_commitment(
        commitment,
        case_key_sha256_by_slot=selected_mapping,
        features_by_slot=features_by_slot,
        sealed_rankings=sealed_rankings,
    )
    committed = tuple(
        (item.opaque_slot, item.case_key_sha256) for item in normalized_commitment.items
    )
    if committed != selected:
        raise _invalid()
    _require_projection(contract, _ANSWER_COLUMNS)
    slots_by_digest = {digest: slot for slot, digest in selected}
    found: dict[str, CaseAnswer] = {}
    for batch in read_parquet_batches(stream, contract=contract):
        case_keys, candidate_values = _columns(batch, _ANSWER_COLUMNS)
        for index in range(batch.num_rows):
            digest = case_key_sha256(_value(case_keys, index))  # type: ignore[arg-type]
            slot = slots_by_digest.get(digest)
            if slot is None:
                continue
            if slot in found:
                raise _invalid()
            found[slot] = CaseAnswer(
                opaque_slot=slot,
                candidate_id=_normalize_candidate(_value(candidate_values, index)),
            )
    if set(found) != set(selected_slots):
        raise _invalid()
    return tuple(found[slot] for slot in selected_slots)


def load_case_answers(
    stream: BinaryIO,
    *,
    contract: ParquetContract,
    case_key_sha256_by_slot: Mapping[str, str],
    commitment: RcaRankingBatchCommitment,
    features_by_slot: Mapping[str, RcaFeatureSet],
    sealed_rankings: Mapping[str, RcaRankingSeal],
) -> tuple[CaseAnswer, ...]:
    """Open answers only after the complete batch commitment validates."""

    try:
        return _load_case_answers(
            stream,
            contract=contract,
            case_key_sha256_by_slot=case_key_sha256_by_slot,
            commitment=commitment,
            features_by_slot=features_by_slot,
            sealed_rankings=sealed_rankings,
        )
    except AdapterError as error:
        failure = error
    except Exception:
        failure = _invalid()
    raise _detached(failure)


__all__ = [
    "CaseAnswer",
    "CaseTiming",
    "load_case_answers",
    "load_case_timings",
]
