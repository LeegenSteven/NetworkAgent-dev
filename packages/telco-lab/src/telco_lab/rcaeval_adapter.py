"""Answer-blind RCAEval telemetry aggregation for five opaque slots."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import BinaryIO, Literal

import pyarrow as pa
from pydantic import ValidationError

from .adapters import AdapterError
from .parquet_reader import ParquetContract, read_parquet_batches
from .rcaeval_case_index import CaseTiming
from .rcaeval_models import (
    MAX_TOTAL_ABSOLUTE,
    EvidenceAggregate,
    RcaFeatureSet,
)


_SLOT = re.compile(r"^rcaslot-[0-9a-f]{64}$")
_CANDIDATE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_METRIC_COLUMN = re.compile(
    r"^(?P<service>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)_"
    r"(?P<metric>cpu|mem|diskio|socket|workload|error|latency-(?:50|90))$"
)
_METRIC_TIME_COLUMN = "time"
_LOG_COLUMNS = ("timestamp", "container_name")
_TRACE_COLUMNS = (
    "startTime",
    "startTimeMillis",
    "duration",
    "statusCode",
    "serviceName",
)
_CASE_COUNT = 5
_METRIC_SECONDS = 1_441
_MICROSECONDS_PER_SECOND = 1_000_000
_METRIC_SCALE = 1_000_000
_EVIDENCE_DOMAIN = b"networkagent-rcaeval-evidence-v1\x00"


def _invalid() -> AdapterError:
    return AdapterError("adapter_invalid_input")


def _unsafe() -> AdapterError:
    return AdapterError("adapter_unsafe_field")


def _limit() -> AdapterError:
    return AdapterError("adapter_limit_exceeded")


def _validate_slot(value: object) -> str:
    if type(value) is not str or _SLOT.fullmatch(value) is None:
        raise _invalid()
    return value


def _candidate(value: object) -> str:
    if type(value) is not str:
        raise _invalid()
    normalized = "frontend" if value == "frontendservice" else value
    if len(normalized) > 64 or _CANDIDATE.fullmatch(normalized) is None:
        raise _unsafe()
    return normalized


@dataclass(frozen=True, slots=True, repr=False)
class RcaTelemetryCase:
    """Three held telemetry handles bound externally to one opaque slot."""

    opaque_slot: str
    timing: CaseTiming
    metrics_stream: BinaryIO
    metrics_contract: ParquetContract
    logs_stream: BinaryIO
    logs_contract: ParquetContract
    traces_stream: BinaryIO
    traces_contract: ParquetContract

    def __post_init__(self) -> None:
        slot = _validate_slot(self.opaque_slot)
        timing_matches = (
            type(self.timing) is CaseTiming and self.timing.opaque_slot == slot
        )
        if not timing_matches:
            raise _invalid()
        for contract in (
            self.metrics_contract,
            self.logs_contract,
            self.traces_contract,
        ):
            if type(contract) is not ParquetContract:
                raise _invalid()


def _field(contract: ParquetContract, name: str) -> pa.Field:
    try:
        return contract.expected_schema.field(name)
    except (KeyError, IndexError) as error:
        raise _unsafe() from error


def _normalized_case(value: object) -> RcaTelemetryCase:
    if type(value) is not RcaTelemetryCase:
        raise _invalid()
    try:
        timing = CaseTiming(
            opaque_slot=value.timing.opaque_slot,
            inject_time=value.timing.inject_time,
            time_start=value.timing.time_start,
            time_end=value.timing.time_end,
        )
        return RcaTelemetryCase(
            opaque_slot=value.opaque_slot,
            timing=timing,
            metrics_stream=value.metrics_stream,
            metrics_contract=value.metrics_contract,
            logs_stream=value.logs_stream,
            logs_contract=value.logs_contract,
            traces_stream=value.traces_stream,
            traces_contract=value.traces_contract,
        )
    except AdapterError:
        raise
    except Exception as error:
        raise _invalid() from error


def _require_schema(case: RcaTelemetryCase) -> tuple[tuple[str, str], ...]:
    timing = case.timing
    if timing.time_end - timing.time_start + 1 != _METRIC_SECONDS:
        raise _invalid()

    metric_names = tuple(case.metrics_contract.expected_schema.names)
    metric_time_type = _field(
        case.metrics_contract,
        _METRIC_TIME_COLUMN,
    ).type
    if (
        case.metrics_contract.expected_rows != _METRIC_SECONDS
        or not metric_names
        or metric_names[0] != _METRIC_TIME_COLUMN
        or case.metrics_contract.projected_columns != metric_names
        or metric_time_type != pa.int64()
    ):
        raise _invalid()
    parsed_metrics: list[tuple[str, str]] = []
    for name in metric_names[1:]:
        match = _METRIC_COLUMN.fullmatch(name)
        if match is None:
            raise _unsafe()
        if _field(case.metrics_contract, name).type != pa.float64():
            raise _unsafe()
        parsed_metrics.append(
            (_candidate(match.group("service")), match.group("metric"))
        )
    if not parsed_metrics:
        raise _invalid()
    if len(parsed_metrics) != len(set(parsed_metrics)):
        raise _unsafe()

    if case.logs_contract.projected_columns != _LOG_COLUMNS:
        raise _unsafe()
    if (
        _field(case.logs_contract, "timestamp").type != pa.int64()
        or _field(case.logs_contract, "container_name").type != pa.large_string()
    ):
        raise _unsafe()

    if case.traces_contract.projected_columns != _TRACE_COLUMNS:
        raise _unsafe()
    trace_types = {
        "startTime": pa.int64(),
        "startTimeMillis": pa.int64(),
        "duration": pa.int64(),
        "statusCode": pa.int64(),
        "serviceName": pa.large_string(),
    }
    for name, expected_type in trace_types.items():
        if _field(case.traces_contract, name).type != expected_type:
            raise _unsafe()
    return tuple(parsed_metrics)


def _scaled_metric(value: object) -> int:
    if type(value) is not float or not math.isfinite(value):
        raise _invalid()
    try:
        scaled = int(
            (Decimal.from_float(value) * _METRIC_SCALE).to_integral_value(
                rounding=ROUND_HALF_EVEN
            )
        )
    except (InvalidOperation, OverflowError, ValueError) as error:
        raise _invalid() from error
    if abs(scaled) > MAX_TOTAL_ABSOLUTE:
        raise _limit()
    return scaled


def _bounded_add(left: int, right: int) -> int:
    value = left + right
    if abs(value) > MAX_TOTAL_ABSOLUTE:
        raise _limit()
    return value


def _evidence_id(
    *,
    candidate_id: str,
    modality: str,
    signal: str,
    baseline_total: int,
    baseline_count: int,
    observed_total: int,
    observed_count: int,
) -> str:
    # Deliberately excludes opaque slots, paths, resource IDs and case keys.
    values = (
        candidate_id,
        modality,
        signal,
        str(baseline_total),
        str(baseline_count),
        str(observed_total),
        str(observed_count),
    )
    encoded = "\x00".join(values).encode("ascii")
    digest = hashlib.sha256(_EVIDENCE_DOMAIN + encoded).hexdigest()
    return "rcaevidence-" + digest


def _aggregate(
    *,
    candidate_id: str,
    modality: Literal["METRIC", "LOG", "TRACE"],
    signal: str,
    baseline_total: int,
    baseline_count: int,
    observed_total: int,
    observed_count: int,
) -> EvidenceAggregate:
    if baseline_count < 1 or observed_count < 1:
        raise _invalid()
    try:
        return EvidenceAggregate(
            evidence_id=_evidence_id(
                candidate_id=candidate_id,
                modality=modality,
                signal=signal,
                baseline_total=baseline_total,
                baseline_count=baseline_count,
                observed_total=observed_total,
                observed_count=observed_count,
            ),
            candidate_id=candidate_id,
            modality=modality,
            baseline_total=baseline_total,
            baseline_count=baseline_count,
            observed_total=observed_total,
            observed_count=observed_count,
        )
    except ValidationError as error:
        raise _limit() from error


def _batch_values(batch, names: tuple[str, ...]) -> tuple[list[object], ...]:
    try:
        return tuple(batch.column(name).to_pylist() for name in names)
    except Exception as error:
        raise _invalid() from error


def _metric_aggregates(
    case: RcaTelemetryCase,
    parsed_metrics: tuple[tuple[str, str], ...],
) -> list[EvidenceAggregate]:
    column_names = case.metrics_contract.projected_columns
    states = [[0, 0, 0, 0] for _item in parsed_metrics]
    seen_times: set[int] = set()
    for batch in read_parquet_batches(
        case.metrics_stream,
        contract=case.metrics_contract,
    ):
        values = _batch_values(batch, column_names)
        timestamps = values[0]
        metric_columns = values[1:]
        for row_index, timestamp_value in enumerate(timestamps):
            if (
                type(timestamp_value) is not int
                or timestamp_value < case.timing.time_start
                or timestamp_value > case.timing.time_end
                or timestamp_value in seen_times
            ):
                raise _invalid()
            seen_times.add(timestamp_value)
            baseline = timestamp_value < case.timing.inject_time
            for metric_index, metric_values in enumerate(metric_columns):
                metric_value = metric_values[row_index]
                if metric_value is None:
                    continue
                scaled = _scaled_metric(metric_value)
                state = states[metric_index]
                if baseline:
                    state[0] += scaled
                    state[1] += 1
                else:
                    state[2] += scaled
                    state[3] += 1
    first_second = case.timing.time_start
    after_last_second = case.timing.time_end + 1
    expected_times = set(range(first_second, after_last_second))
    if seen_times != expected_times:
        raise _invalid()
    result: list[EvidenceAggregate] = []
    for (candidate_id, signal), state in zip(
        parsed_metrics,
        states,
        strict=True,
    ):
        result.append(
            _aggregate(
                candidate_id=candidate_id,
                modality="METRIC",
                signal=signal,
                baseline_total=_bounded_add(0, state[0]),
                baseline_count=state[1],
                observed_total=_bounded_add(0, state[2]),
                observed_count=state[3],
            )
        )
    return result


def _log_aggregates(case: RcaTelemetryCase) -> list[EvidenceAggregate]:
    totals: dict[str, list[int]] = {}
    for batch in read_parquet_batches(
        case.logs_stream,
        contract=case.logs_contract,
    ):
        timestamps, containers = _batch_values(batch, _LOG_COLUMNS)
        for timestamp_value, container_value in zip(
            timestamps,
            containers,
            strict=True,
        ):
            if (
                type(timestamp_value) is not int
                or timestamp_value < case.timing.time_start
                or timestamp_value > case.timing.time_end
            ):
                raise _invalid()
            candidate_id = _candidate(container_value)
            state = totals.setdefault(candidate_id, [0, 0])
            state[0 if timestamp_value < case.timing.inject_time else 1] += 1
    baseline_seconds = case.timing.inject_time - case.timing.time_start
    observed_seconds = case.timing.time_end - case.timing.inject_time + 1
    return [
        _aggregate(
            candidate_id=candidate_id,
            modality="LOG",
            signal="event-rate",
            baseline_total=totals[candidate_id][0],
            baseline_count=baseline_seconds,
            observed_total=totals[candidate_id][1],
            observed_count=observed_seconds,
        )
        for candidate_id in sorted(totals)
    ]


def _trace_start_us(start: object, start_millis: object) -> int:
    if (
        type(start) is not int
        or start < 0
        or type(start_millis) is not int
        or start_millis < 0
    ):
        raise _invalid()
    if start // 1_000 != start_millis:
        raise _invalid()
    return start


def _trace_aggregates(case: RcaTelemetryCase) -> list[EvidenceAggregate]:
    # [baseline duration, baseline count, observed duration, observed count]
    totals: dict[str, list[int]] = {}
    inject_us = case.timing.inject_time * _MICROSECONDS_PER_SECOND
    minimum_us = case.timing.time_start * _MICROSECONDS_PER_SECOND
    maximum_us = (case.timing.time_end + 1) * _MICROSECONDS_PER_SECOND
    for batch in read_parquet_batches(
        case.traces_stream,
        contract=case.traces_contract,
    ):
        starts, millis, durations, statuses, services = _batch_values(
            batch,
            _TRACE_COLUMNS,
        )
        for start, start_millis, duration, status, service in zip(
            starts,
            millis,
            durations,
            statuses,
            services,
            strict=True,
        ):
            start_us = _trace_start_us(start, start_millis)
            if type(duration) is not int or duration <= 0:
                raise _invalid()
            end_us = start_us + duration
            if start_us < minimum_us or end_us >= maximum_us:
                raise _invalid()
            if status not in (None, 0) or type(status) is bool:
                raise _invalid()
            candidate_id = _candidate(service)
            state = totals.setdefault(candidate_id, [0, 0, 0, 0])
            offset = 0 if end_us < inject_us else 2
            state[offset] = _bounded_add(state[offset], duration)
            state[offset + 1] += 1
    result: list[EvidenceAggregate] = []
    for candidate_id in sorted(totals):
        state = totals[candidate_id]
        if state[1] < 1 or state[3] < 1:
            continue
        result.append(
            _aggregate(
                candidate_id=candidate_id,
                modality="TRACE",
                signal="duration-us",
                baseline_total=state[0],
                baseline_count=state[1],
                observed_total=state[2],
                observed_count=state[3],
            )
        )
    return result


def _adapt_rcaeval_case(
    case: RcaTelemetryCase,
) -> tuple[str, RcaFeatureSet]:
    """Aggregate one slot while keeping the slot outside the ranker input."""

    case = _normalized_case(case)
    parsed_metrics = _require_schema(case)
    aggregates = (
        _metric_aggregates(case, parsed_metrics)
        + _log_aggregates(case)
        + _trace_aggregates(case)
    )
    ordered = tuple(
        sorted(
            aggregates,
            key=lambda item: (
                item.candidate_id,
                item.modality,
                item.evidence_id,
            ),
        )
    )
    try:
        features = RcaFeatureSet(aggregates=ordered)
    except ValidationError as error:
        raise _limit() from error
    return (case.opaque_slot, features)


def adapt_rcaeval_case(
    case: RcaTelemetryCase,
) -> tuple[str, RcaFeatureSet]:
    """Aggregate one slot behind a fixed, fully detached error boundary."""

    failure_code = "adapter_invalid_input"
    try:
        return _adapt_rcaeval_case(case)
    except AdapterError as error:
        failure_code = error.code
    except MemoryError:
        failure_code = "adapter_limit_exceeded"
    except Exception:
        failure_code = "adapter_invalid_input"
    raise AdapterError(failure_code) from None  # type: ignore[arg-type]


def _adapt_rcaeval_cases(
    cases: tuple[RcaTelemetryCase, ...],
) -> tuple[tuple[str, RcaFeatureSet], ...]:
    """Convert exactly fifteen telemetry handles into five feature sets."""

    if type(cases) is not tuple or len(cases) != _CASE_COUNT:
        raise _invalid()
    if any(type(case) is not RcaTelemetryCase for case in cases):
        raise _invalid()
    slots = tuple(case.opaque_slot for case in cases)
    if len(set(slots)) != _CASE_COUNT:
        raise _invalid()
    return tuple(sorted((_adapt_rcaeval_case(case) for case in cases)))


def adapt_rcaeval_cases(
    cases: tuple[RcaTelemetryCase, ...],
) -> tuple[tuple[str, RcaFeatureSet], ...]:
    """Aggregate five slots behind a fixed, fully detached error boundary."""

    failure_code = "adapter_invalid_input"
    try:
        return _adapt_rcaeval_cases(cases)
    except AdapterError as error:
        failure_code = error.code
    except MemoryError:
        failure_code = "adapter_limit_exceeded"
    except Exception:
        failure_code = "adapter_invalid_input"
    raise AdapterError(failure_code) from None  # type: ignore[arg-type]


__all__ = [
    "RcaTelemetryCase",
    "adapt_rcaeval_case",
    "adapt_rcaeval_cases",
]
