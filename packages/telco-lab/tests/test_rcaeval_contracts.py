from __future__ import annotations

import pyarrow as pa

from telco_lab.rcaeval_contracts import (
    RCAEVAL_CASE_ANSWER_CONTRACT,
    RCAEVAL_CASE_KEY_SHA256_BY_SLOT,
    RCAEVAL_CASE_TIMING_CONTRACT,
    RCAEVAL_RESOURCE_CONTRACTS,
    RCAEVAL_RESOURCE_IDS,
    RCAEVAL_TELEMETRY_GROUPS,
    RCAEVAL_TELEMETRY_RESOURCE_IDS,
    RCAEVAL_TOTAL_BYTES,
    validate_frozen_rcaeval_contracts,
)


def test_upstream_contract_is_an_exact_sixteen_file_closure() -> None:
    validate_frozen_rcaeval_contracts()

    assert len(RCAEVAL_RESOURCE_IDS) == 16
    assert len(RCAEVAL_TELEMETRY_RESOURCE_IDS) == 15
    assert len(RCAEVAL_TELEMETRY_GROUPS) == 5
    assert (
        sum(item.size_bytes for item in RCAEVAL_RESOURCE_CONTRACTS.values())
        == RCAEVAL_TOTAL_BYTES
        == 53_433_532
    )
    assert all(
        item.parquet.expected_row_groups == 1
        and item.parquet.allowed_codecs == ("ZSTD",)
        and item.parquet.expected_created_by == "parquet-cpp-arrow version 25.0.0"
        and item.parquet.expected_format_version == "2.6"
        for item in RCAEVAL_RESOURCE_CONTRACTS.values()
    )


def test_case_index_has_separate_timing_and_post_seal_answer_projections() -> None:
    assert RCAEVAL_CASE_TIMING_CONTRACT.expected_schema.equals(
        RCAEVAL_CASE_ANSWER_CONTRACT.expected_schema,
        check_metadata=True,
    )
    assert RCAEVAL_CASE_TIMING_CONTRACT.projected_columns == (
        "case",
        "inject_time",
        "time_start",
        "time_end",
    )
    assert RCAEVAL_CASE_ANSWER_CONTRACT.projected_columns == (
        "case",
        "root_cause_service",
    )
    assert RCAEVAL_CASE_TIMING_CONTRACT.expected_schema.field("case").type == (
        pa.large_string()
    )
    assert set(RCAEVAL_CASE_KEY_SHA256_BY_SLOT) == {
        group[0] for group in RCAEVAL_TELEMETRY_GROUPS
    }
    assert all(
        len(value) == 64 and value == value.lower()
        for value in RCAEVAL_CASE_KEY_SHA256_BY_SLOT.values()
    )


def test_telemetry_contract_projects_no_log_or_trace_free_text() -> None:
    for _slot, metric_id, log_id, trace_id in RCAEVAL_TELEMETRY_GROUPS:
        metric = RCAEVAL_RESOURCE_CONTRACTS[metric_id].parquet
        logs = RCAEVAL_RESOURCE_CONTRACTS[log_id].parquet
        traces = RCAEVAL_RESOURCE_CONTRACTS[trace_id].parquet

        assert metric.projected_columns == tuple(metric.expected_schema.names)
        assert logs.projected_columns == ("timestamp", "container_name")
        assert traces.projected_columns == (
            "startTime",
            "startTimeMillis",
            "duration",
            "statusCode",
            "serviceName",
        )
        assert "message" not in logs.projected_columns
        assert {
            "traceID",
            "spanID",
            "methodName",
            "operationName",
            "parentSpanID",
            "time",
        }.isdisjoint(traces.projected_columns)
        assert traces.limits.max_row_group_rows >= traces.expected_rows


def test_public_resource_names_and_slots_are_opaque() -> None:
    public_wire = "\n".join(
        (
            *RCAEVAL_RESOURCE_IDS,
            *(group[0] for group in RCAEVAL_TELEMETRY_GROUPS),
        )
    ).lower()
    for forbidden in (
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "productcatalogservice",
        "recommendationservice",
        "_cpu_",
        ".cpu.",
    ):
        assert forbidden not in public_wire
