from __future__ import annotations

import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import telco_lab.rcaeval_adapter as adapter_module
from telco_lab.adapters import AdapterError
from telco_lab.parquet_reader import (
    ParquetContract,
    parquet_schema_fingerprint,
)
from telco_lab.rcaeval_adapter import (
    RcaTelemetryCase,
    adapt_rcaeval_case,
    adapt_rcaeval_cases,
)
from telco_lab.rcaeval_case_index import CaseTiming
from telco_lab.rcaeval_models import MAX_TOTAL_ABSOLUTE


_METRIC_COLUMNS = ("time", "alpha_cpu", "frontendservice_error")
_LOG_COLUMNS = ("timestamp", "container_name")
_TRACE_COLUMNS = (
    "startTime",
    "startTimeMillis",
    "duration",
    "statusCode",
    "serviceName",
)


def _slot(index: int) -> str:
    return f"rcaslot-{index:064x}"


def _bytes(table: pa.Table, *, row_group_size: int | None = None) -> bytes:
    stream = io.BytesIO()
    pq.write_table(
        table,
        stream,
        compression="snappy",
        row_group_size=row_group_size,
    )
    return stream.getvalue()


def _contract(
    table: pa.Table,
    projection: tuple[str, ...],
    *,
    row_groups: int = 1,
) -> ParquetContract:
    return ParquetContract(
        expected_schema=table.schema,
        expected_schema_fingerprint=parquet_schema_fingerprint(table.schema),
        projected_columns=projection,
        expected_rows=table.num_rows,
        expected_row_groups=row_groups,
        allowed_codecs=("SNAPPY",),
    )


def _metrics(*, reverse: bool = False) -> pa.Table:
    timestamps = list(range(1_000, 2_441))
    if reverse:
        timestamps.reverse()
    return pa.table(
        {
            "time": pa.array(timestamps, type=pa.int64()),
            "alpha_cpu": pa.array(
                [1.0 if value < 1_720 else 3.0 for value in timestamps],
                type=pa.float64(),
            ),
            "frontendservice_error": pa.array(
                [2.0 if value < 1_720 else 4.0 for value in timestamps],
                type=pa.float64(),
            ),
        }
    )


def _logs(*, reverse: bool = False) -> pa.Table:
    values = [
        (1_100, "alpha", "ROOT_LABEL_CANARY"),
        (1_800, "alpha", "ROOT_LABEL_CANARY"),
        (1_200, "frontendservice", "ROOT_LABEL_CANARY"),
        (1_900, "frontendservice", "ROOT_LABEL_CANARY"),
    ]
    if reverse:
        values.reverse()
    timestamps = [item[0] for item in values]
    return pa.table(
        {
            "timestamp": pa.array(timestamps, type=pa.int64()),
            "container_name": pa.array(
                [item[1] for item in values], type=pa.large_string()
            ),
            "message": [item[2] for item in values],
        }
    )


def _traces(*, reverse: bool = False) -> pa.Table:
    values = [
        # status 0 is explicitly not an error.
        (1_100_000_000, 1_100_000, 1_000, 0, "alpha", "TRACE_CANARY_A"),
        (1_800_000_000, 1_800_000, 2_000, 0, "alpha", "TRACE_CANARY_B"),
        # A null status is also explicitly not an error.
        (
            1_200_000_000,
            1_200_000,
            3_000,
            0,
            "frontendservice",
            "TRACE_CANARY_C",
        ),
        (
            1_900_000_000,
            1_900_000,
            4_000,
            None,
            "frontendservice",
            "TRACE_CANARY_D",
        ),
    ]
    if reverse:
        values.reverse()
    starts = [item[0] for item in values]
    start_millis = [item[1] for item in values]
    durations = [item[2] for item in values]
    statuses = [item[3] for item in values]
    return pa.table(
        {
            "startTime": pa.array(starts, type=pa.int64()),
            "startTimeMillis": pa.array(start_millis, type=pa.int64()),
            "duration": pa.array(durations, type=pa.int64()),
            "statusCode": pa.array(statuses, type=pa.int64()),
            "serviceName": pa.array(
                [item[4] for item in values], type=pa.large_string()
            ),
            "traceId": [item[5] for item in values],
            "name": ["ROOT_LABEL_CANARY"] * len(values),
        }
    )


def _case(index: int, *, reverse: bool = False) -> RcaTelemetryCase:
    metrics = _metrics(reverse=reverse)
    logs = _logs(reverse=reverse)
    traces = _traces(reverse=reverse)
    timing = CaseTiming(
        opaque_slot=_slot(index),
        inject_time=1_720,
        time_start=1_000,
        time_end=2_440,
    )
    return RcaTelemetryCase(
        opaque_slot=_slot(index),
        timing=timing,
        metrics_stream=io.BytesIO(_bytes(metrics, row_group_size=500)),
        metrics_contract=_contract(metrics, _METRIC_COLUMNS, row_groups=3),
        logs_stream=io.BytesIO(_bytes(logs)),
        logs_contract=_contract(logs, _LOG_COLUMNS),
        traces_stream=io.BytesIO(_bytes(traces)),
        traces_contract=_contract(traces, _TRACE_COLUMNS),
    )


def _case_with_metrics(
    metrics: pa.Table,
    *,
    row_group_size: int,
) -> RcaTelemetryCase:
    valid = _case(1)
    row_groups = (metrics.num_rows + row_group_size - 1) // row_group_size
    return RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=io.BytesIO(_bytes(metrics, row_group_size=row_group_size)),
        metrics_contract=_contract(
            metrics,
            _METRIC_COLUMNS,
            row_groups=row_groups,
        ),
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=valid.traces_stream,
        traces_contract=valid.traces_contract,
    )


def _aggregate_key(item) -> tuple[str, str, int, int, int, int]:
    return (
        item.candidate_id,
        item.modality,
        item.baseline_total,
        item.baseline_count,
        item.observed_total,
        item.observed_count,
    )


def _feature_dumps(adapted) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for slot, feature in adapted:
        result.append((slot, feature.model_dump_json()))
    return result


def test_fifteen_streams_make_five_label_free_feature_sets() -> None:
    cases = tuple(_case(index) for index in reversed(range(1, 6)))
    adapted = adapt_rcaeval_cases(cases)

    assert len(adapted) == 5
    assert tuple(slot for slot, _feature in adapted) == tuple(
        _slot(index) for index in range(1, 6)
    )
    for slot, feature in adapted:
        assert slot.startswith("rcaslot-")
        assert "slot_id" not in feature.model_dump(mode="python")
        assert {item.candidate_id for item in feature.aggregates} == {
            "alpha",
            "frontend",
        }
        assert {item.modality for item in feature.aggregates} == {
            "METRIC",
            "LOG",
            "TRACE",
        }
        rendered = json.dumps(feature.model_dump(mode="json"), sort_keys=True)
        assert "ROOT_LABEL_CANARY" not in rendered
        assert "TRACE_CANARY" not in rendered
        assert "private-case" not in rendered


def test_metric_log_and_trace_semantics_are_exact() -> None:
    _slot_id, feature = adapt_rcaeval_case(_case(1))
    values = {_aggregate_key(item) for item in feature.aggregates}

    assert (
        "alpha",
        "METRIC",
        720 * 1_000_000,
        720,
        721 * 3 * 1_000_000,
        721,
    ) in values
    assert ("alpha", "LOG", 1, 720, 1, 721) in values
    assert ("alpha", "TRACE", 1_000, 1, 2_000, 1) in values

    # frontendservice is normalized. Status 0/NULL produce no error evidence.
    assert ("frontend", "TRACE", 3_000, 1, 4_000, 1) in values
    modalities = tuple(item.modality for item in feature.aggregates)
    assert modalities.count("TRACE") == 2


def test_partial_null_metric_cells_skip_only_that_metric_and_row() -> None:
    metrics = _metrics()
    timestamps = metrics.column("time").to_pylist()
    alpha_values = [
        None if timestamp in (1_100, 1_800) else (1.0 if timestamp < 1_720 else 3.0)
        for timestamp in timestamps
    ]
    metrics = metrics.set_column(
        metrics.schema.get_field_index("alpha_cpu"),
        "alpha_cpu",
        pa.array(alpha_values, type=pa.float64()),
    )

    _slot_id, feature = adapt_rcaeval_case(
        _case_with_metrics(metrics, row_group_size=137)
    )
    metric_values = {
        item.candidate_id: item
        for item in feature.aggregates
        if item.modality == "METRIC"
    }

    alpha = metric_values["alpha"]
    assert (
        alpha.baseline_total,
        alpha.baseline_count,
        alpha.observed_total,
        alpha.observed_count,
    ) == (719 * 1_000_000, 719, 720 * 3 * 1_000_000, 720)
    frontend = metric_values["frontend"]
    assert (
        frontend.baseline_count,
        frontend.observed_count,
    ) == (720, 721)


@pytest.mark.parametrize("missing_phase", ["baseline", "observed"])
def test_metric_rejects_a_side_with_only_null_cells(missing_phase: str) -> None:
    metrics = _metrics()
    timestamps = metrics.column("time").to_pylist()
    alpha_values = [
        (
            None
            if (timestamp < 1_720) == (missing_phase == "baseline")
            else (1.0 if timestamp < 1_720 else 3.0)
        )
        for timestamp in timestamps
    ]
    metrics = metrics.set_column(
        metrics.schema.get_field_index("alpha_cpu"),
        "alpha_cpu",
        pa.array(alpha_values, type=pa.float64()),
    )

    with pytest.raises(AdapterError) as error:
        adapt_rcaeval_case(_case_with_metrics(metrics, row_group_size=211))

    assert error.value.code == "adapter_invalid_input"


@pytest.mark.parametrize(
    "value",
    [True, 1, "1", float("nan"), float("inf"), float("-inf")],
)
def test_metric_scaling_keeps_rejecting_non_finite_and_wrong_types(
    value: object,
) -> None:
    with pytest.raises(AdapterError) as error:
        adapter_module._scaled_metric(value)

    assert error.value.code == "adapter_invalid_input"


@pytest.mark.parametrize("sign", [-1, 1])
def test_metric_accepts_exact_total_boundary(sign: int) -> None:
    metrics = _metrics()
    timestamps = metrics.column("time").to_pylist()
    alpha_values = [None] * len(timestamps)
    alpha_values[timestamps.index(1_000)] = sign * 1_000_000_000_000.0
    alpha_values[timestamps.index(1_720)] = sign * 1_000_000_000_000.0
    metrics = metrics.set_column(
        metrics.schema.get_field_index("alpha_cpu"),
        "alpha_cpu",
        pa.array(alpha_values, type=pa.float64()),
    )

    _slot_id, feature = adapt_rcaeval_case(
        _case_with_metrics(metrics, row_group_size=173)
    )
    alpha = next(
        item
        for item in feature.aggregates
        if item.modality == "METRIC" and item.candidate_id == "alpha"
    )
    assert alpha.baseline_total == sign * MAX_TOTAL_ABSOLUTE
    assert alpha.baseline_count == 1
    assert alpha.observed_total == sign * MAX_TOTAL_ABSOLUTE
    assert alpha.observed_count == 1


def test_metric_rejects_single_cell_and_cumulative_total_overflow() -> None:
    timestamps = _metrics().column("time").to_pylist()

    single_values = [1.0] * len(timestamps)
    single_values[timestamps.index(1_000)] = 1_000_000_000_001.0
    single = _metrics().set_column(
        1,
        "alpha_cpu",
        pa.array(single_values, type=pa.float64()),
    )
    with pytest.raises(AdapterError) as single_error:
        adapt_rcaeval_case(_case_with_metrics(single, row_group_size=233))
    assert single_error.value.code == "adapter_limit_exceeded"

    cumulative_values = [None] * len(timestamps)
    cumulative_values[timestamps.index(1_000)] = 600_000_000_000.0
    cumulative_values[timestamps.index(1_001)] = 600_000_000_000.0
    cumulative_values[timestamps.index(1_720)] = 1.0
    cumulative = _metrics().set_column(
        1,
        "alpha_cpu",
        pa.array(cumulative_values, type=pa.float64()),
    )
    with pytest.raises(AdapterError) as cumulative_error:
        adapt_rcaeval_case(_case_with_metrics(cumulative, row_group_size=199))
    assert cumulative_error.value.code == "adapter_limit_exceeded"


def test_partial_null_metrics_are_deterministic_across_rows_and_row_groups() -> None:
    def _partial(reverse: bool) -> pa.Table:
        timestamps = list(range(1_000, 2_441))
        if reverse:
            timestamps.reverse()
        return pa.table(
            {
                "time": pa.array(timestamps, type=pa.int64()),
                "alpha_cpu": pa.array(
                    [
                        (
                            None
                            if timestamp in (1_100, 1_800)
                            else (1.0 if timestamp < 1_720 else 3.0)
                        )
                        for timestamp in timestamps
                    ],
                    type=pa.float64(),
                ),
                "frontendservice_error": pa.array(
                    [
                        (
                            None
                            if timestamp in (1_200, 1_900)
                            else (2.0 if timestamp < 1_720 else 4.0)
                        )
                        for timestamp in timestamps
                    ],
                    type=pa.float64(),
                ),
            }
        )

    first = adapt_rcaeval_case(_case_with_metrics(_partial(False), row_group_size=97))
    replay = adapt_rcaeval_case(_case_with_metrics(_partial(True), row_group_size=113))

    assert first == replay


def test_metric_cancellation_is_deterministic_across_rows_and_row_groups() -> None:
    values_by_time = {
        1_000: 1_000_000_000_000.0,
        1_001: 1_000_000_000_000.0,
        1_002: -1_000_000_000_000.0,
        1_720: 1.0,
    }

    def _table(prefix: tuple[int, ...]) -> pa.Table:
        remaining = [
            timestamp for timestamp in range(1_000, 2_441) if timestamp not in prefix
        ]
        timestamps = [*prefix, *remaining]
        return pa.table(
            {
                "time": pa.array(timestamps, type=pa.int64()),
                "alpha_cpu": pa.array(
                    [values_by_time.get(timestamp) for timestamp in timestamps],
                    type=pa.float64(),
                ),
                "frontendservice_error": pa.array(
                    [2.0 if timestamp < 1_720 else 4.0 for timestamp in timestamps],
                    type=pa.float64(),
                ),
            }
        )

    first = adapt_rcaeval_case(
        _case_with_metrics(_table((1_000, 1_001, 1_002)), row_group_size=97)
    )
    replay = adapt_rcaeval_case(
        _case_with_metrics(_table((1_000, 1_002, 1_001)), row_group_size=113)
    )

    assert first == replay
    alpha = next(
        item
        for item in first[1].aggregates
        if item.modality == "METRIC" and item.candidate_id == "alpha"
    )
    assert alpha.baseline_total == MAX_TOTAL_ABSOLUTE
    assert alpha.baseline_count == 3


def test_metric_final_overflow_is_order_independent() -> None:
    values_by_time = {
        1_000: 1_000_000_000_000.0,
        1_001: 1_000_000_000_000.0,
        1_002: -500_000_000_000.0,
        1_720: 1.0,
    }

    for prefix, row_group_size in (
        ((1_000, 1_001, 1_002), 97),
        ((1_002, 1_001, 1_000), 113),
    ):
        remaining = [
            timestamp for timestamp in range(1_000, 2_441) if timestamp not in prefix
        ]
        timestamps = [*prefix, *remaining]
        metrics = pa.table(
            {
                "time": pa.array(timestamps, type=pa.int64()),
                "alpha_cpu": pa.array(
                    [values_by_time.get(timestamp) for timestamp in timestamps],
                    type=pa.float64(),
                ),
                "frontendservice_error": pa.array(
                    [2.0 if timestamp < 1_720 else 4.0 for timestamp in timestamps],
                    type=pa.float64(),
                ),
            }
        )

        with pytest.raises(AdapterError) as error:
            adapt_rcaeval_case(
                _case_with_metrics(metrics, row_group_size=row_group_size)
            )

        assert error.value.code == "adapter_limit_exceeded"


def test_adapter_is_deterministic_under_case_and_row_permutations() -> None:
    first = tuple(_case(index, reverse=index == 3) for index in range(1, 6))
    reverse_indices = reversed(range(1, 6))
    replay_list: list[RcaTelemetryCase] = []
    for index in reverse_indices:
        replay_list.append(_case(index, reverse=index != 3))
    replay = tuple(replay_list)
    first_output = adapt_rcaeval_cases(first)
    replay_output = adapt_rcaeval_cases(replay)
    first_dump = _feature_dumps(first_output)
    replay_dump = _feature_dumps(replay_output)
    assert first_dump == replay_dump


def test_metrics_require_exact_regex_and_1441_second_grid() -> None:
    valid = _case(1)
    short_table = _metrics().slice(0, 1_440)
    short = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=io.BytesIO(_bytes(short_table)),
        metrics_contract=_contract(short_table, _METRIC_COLUMNS),
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=valid.traces_stream,
        traces_contract=valid.traces_contract,
    )
    with pytest.raises(AdapterError) as short_error:
        adapt_rcaeval_case(short)
    assert short_error.value.code == "adapter_invalid_input"

    invalid_table = _metrics().rename_columns(
        ["time", "alpha_cpu_label", "frontendservice_error"]
    )
    invalid = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=io.BytesIO(_bytes(invalid_table)),
        metrics_contract=_contract(
            invalid_table,
            ("time", "alpha_cpu_label", "frontendservice_error"),
        ),
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=valid.traces_stream,
        traces_contract=valid.traces_contract,
    )
    with pytest.raises(AdapterError) as regex_error:
        adapt_rcaeval_case(invalid)
    assert regex_error.value.code == "adapter_unsafe_field"


def test_public_adapter_detaches_missing_time_field_failure() -> None:
    valid = _case(1)
    missing_time = _metrics().rename_columns(
        ["missing_time", "alpha_cpu", "frontendservice_error"]
    )
    changed = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=io.BytesIO(_bytes(missing_time)),
        metrics_contract=_contract(
            missing_time,
            ("missing_time", "alpha_cpu", "frontendservice_error"),
        ),
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=valid.traces_stream,
        traces_contract=valid.traces_contract,
    )

    with pytest.raises(AdapterError) as error:
        adapt_rcaeval_case(changed)

    assert error.value.code == "adapter_unsafe_field"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize("batched", [False, True])
def test_public_adapter_detaches_private_stream_failures(batched: bool) -> None:
    class _BombStream:
        closed = False

        def read(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-ADAPTER-STREAM-CANARY")

        def seek(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-ADAPTER-STREAM-CANARY")

        def tell(self):
            raise RuntimeError("PRIVATE-ADAPTER-STREAM-CANARY")

        def readable(self):
            return True

        def seekable(self):
            return True

    valid = _case(1)
    changed = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=_BombStream(),
        metrics_contract=valid.metrics_contract,
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=valid.traces_stream,
        traces_contract=valid.traces_contract,
    )

    with pytest.raises(AdapterError) as error:
        if batched:
            adapt_rcaeval_cases(
                (changed,) + tuple(_case(index) for index in range(2, 6))
            )
        else:
            adapt_rcaeval_case(changed)

    assert error.value.code == "adapter_invalid_input"
    assert "PRIVATE-ADAPTER" not in str(error.value)
    assert "PRIVATE-ADAPTER" not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_adapter_forbids_log_message_and_trace_identifier_projection() -> None:
    valid = _case(1)
    logs = _logs()
    unsafe_logs = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=valid.metrics_stream,
        metrics_contract=valid.metrics_contract,
        logs_stream=io.BytesIO(_bytes(logs)),
        logs_contract=_contract(logs, _LOG_COLUMNS + ("message",)),
        traces_stream=valid.traces_stream,
        traces_contract=valid.traces_contract,
    )
    with pytest.raises(AdapterError) as log_error:
        adapt_rcaeval_case(unsafe_logs)
    assert log_error.value.code == "adapter_unsafe_field"

    valid = _case(1)
    traces = _traces()
    unsafe_traces = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=valid.metrics_stream,
        metrics_contract=valid.metrics_contract,
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=io.BytesIO(_bytes(traces)),
        traces_contract=_contract(traces, _TRACE_COLUMNS + ("traceId",)),
    )
    with pytest.raises(AdapterError) as trace_error:
        adapt_rcaeval_case(unsafe_traces)
    assert trace_error.value.code == "adapter_unsafe_field"


def test_trace_phase_uses_end_time_and_equal_injection_is_observed() -> None:
    valid = _case(1)
    # Ending exactly at injection belongs to the observed phase.
    starts = [1_719_999_000, 1_100_000_000]
    start_millis = [1_719_999, 1_100_000]
    boundary = pa.table(
        {
            "startTime": pa.array(starts, type=pa.int64()),
            "startTimeMillis": pa.array(start_millis, type=pa.int64()),
            "duration": pa.array([1_000, 2_000], type=pa.int64()),
            "statusCode": pa.array([0, 0], type=pa.int64()),
            "serviceName": pa.array(["alpha", "alpha"], type=pa.large_string()),
            "traceId": ["LABEL_CANARY", "LABEL_CANARY"],
            "name": ["LABEL_CANARY", "LABEL_CANARY"],
        }
    )
    changed = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=valid.metrics_stream,
        metrics_contract=valid.metrics_contract,
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=io.BytesIO(_bytes(boundary)),
        traces_contract=_contract(boundary, _TRACE_COLUMNS),
    )
    _slot_id, feature = adapt_rcaeval_case(changed)
    trace_durations = [
        item
        for item in feature.aggregates
        if item.candidate_id == "alpha"
        and item.modality == "TRACE"
        and item.baseline_total == 2_000
        and item.observed_total == 1_000
    ]
    assert len(trace_durations) == 1


@pytest.mark.parametrize(
    ("start", "start_millis", "duration", "status"),
    [
        (999_999_500, 999_999, 1_000, 0),
        (2_440_999_000, 2_440_999, 1_000, 0),
        (1_100_000_000, 1_100_000, 0, 0),
        (None, 1_100_000, 1_000, 0),
        (1_100_000_000, None, 1_000, 0),
        (1_100_000_000, 1_100_001, 1_000, 0),
        (1_100_000_000, 1_100_000, 1_000, 1),
    ],
)
def test_traces_fail_closed_on_window_timing_and_status_domain(
    start: int | None,
    start_millis: int | None,
    duration: int,
    status: int,
) -> None:
    valid = _case(1)
    invalid_trace = pa.table(
        {
            "startTime": pa.array([start], type=pa.int64()),
            "startTimeMillis": pa.array([start_millis], type=pa.int64()),
            "duration": pa.array([duration], type=pa.int64()),
            "statusCode": pa.array([status], type=pa.int64()),
            "serviceName": pa.array(["alpha"], type=pa.large_string()),
            "traceId": ["LABEL_CANARY"],
            "name": ["LABEL_CANARY"],
        }
    )
    changed = RcaTelemetryCase(
        opaque_slot=valid.opaque_slot,
        timing=valid.timing,
        metrics_stream=valid.metrics_stream,
        metrics_contract=valid.metrics_contract,
        logs_stream=valid.logs_stream,
        logs_contract=valid.logs_contract,
        traces_stream=io.BytesIO(_bytes(invalid_trace)),
        traces_contract=_contract(invalid_trace, _TRACE_COLUMNS),
    )
    with pytest.raises(AdapterError) as error:
        adapt_rcaeval_case(changed)
    assert error.value.code == "adapter_invalid_input"


def test_slice_requires_exactly_five_unique_opaque_slots() -> None:
    with pytest.raises(AdapterError) as count_error:
        adapt_rcaeval_cases(tuple(_case(index) for index in range(1, 5)))
    assert count_error.value.code == "adapter_invalid_input"

    duplicate = tuple(_case(1) for _index in range(5))
    with pytest.raises(AdapterError) as duplicate_error:
        adapt_rcaeval_cases(duplicate)
    assert duplicate_error.value.code == "adapter_invalid_input"
