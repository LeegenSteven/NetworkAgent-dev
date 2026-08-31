from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import threading

import pytest

from telco_lab.local_trace import (
    LOCAL_RUNTIME_TRACE_COMPONENTS,
    LOCAL_RUNTIME_TRACE_OPERATIONS,
    LOCAL_RUNTIME_TRACE_SCHEMA,
    LocalRuntimeTraceError,
    LocalRuntimeTraceEvent,
    derive_local_replay_trace_id,
    emit_local_runtime_trace_event,
)


NOW = datetime(2026, 8, 31, 9, 10, 11, 123456, tzinfo=UTC)
SOURCE_EVENT_ID = "labevent-" + "a" * 64
TRACE_ID = (
    "local-replay-trace-"
    "5a330af5bc6cc4478f0404f66f0dd9f14d4d727ab570021d077c862d07e19605"
)


def test_trace_derivation_is_exact_and_rejects_unvalidated_source_ids() -> None:
    assert derive_local_replay_trace_id(SOURCE_EVENT_ID) == TRACE_ID
    for invalid in (
        SOURCE_EVENT_ID.upper(),
        "labevent-" + "a" * 63,
        "labevent-" + "g" * 64,
        SOURCE_EVENT_ID + " ",
        b"labevent-" + b"a" * 64,
    ):
        with pytest.raises(LocalRuntimeTraceError) as caught:
            derive_local_replay_trace_id(invalid)  # type: ignore[arg-type]
        assert caught.value.code == "local_runtime_trace_source_event_invalid"


def test_event_has_exact_bounded_privacy_safe_schema() -> None:
    delivered: list[LocalRuntimeTraceEvent] = []

    assert emit_local_runtime_trace_event(
        delivered.append,
        trace_id=TRACE_ID,
        component="sender",
        operation="REPLAY_REQUEST_VALIDATED",
        clock=lambda: NOW,
    )
    event = delivered[0]
    assert event.as_dict() == {
        "schema": LOCAL_RUNTIME_TRACE_SCHEMA,
        "emitted_at": "2026-08-31T09:10:11.123456Z",
        "trace_id": TRACE_ID,
        "component": "sender",
        "operation": "REPLAY_REQUEST_VALIDATED",
        "outcome": "OK",
        "error_code": None,
    }
    assert tuple(event.as_dict()) == (
        "schema",
        "emitted_at",
        "trace_id",
        "component",
        "operation",
        "outcome",
        "error_code",
    )
    encoded = json.dumps(event.as_dict(), separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= 1024
    assert not any(
        sensitive in encoded.lower()
        for sensitive in (b"imsi", b"msisdn", b"password", b"secret", b"token")
    )


def test_event_rejects_unknown_or_inconsistent_cardinality() -> None:
    base = {
        "schema": LOCAL_RUNTIME_TRACE_SCHEMA,
        "emitted_at": "2026-08-31T09:10:11.123456Z",
        "trace_id": TRACE_ID,
        "component": "sender",
        "operation": "REPLAY_REQUEST_VALIDATED",
        "outcome": "OK",
        "error_code": None,
    }
    invalid = (
        {**base, "schema": "unknown/1.0"},
        {**base, "trace_id": "秘密"},
        {**base, "trace_id": "IMSI:310410000000001"},
        {**base, "trace_id": "x" * 257},
        {**base, "component": "unknown"},
        {**base, "operation": "UNKNOWN"},
        {**base, "outcome": "UNKNOWN"},
        {**base, "outcome": "OK", "error_code": "LOCAL_FAULT_TRACE_CONFLICT"},
        {**base, "outcome": "ERROR", "error_code": None},
        {**base, "outcome": "ERROR", "error_code": "UNBOUNDED_DYNAMIC_ERROR"},
        {**base, "outcome": "ERROR", "error_code": []},
        {**base, "outcome": "ERROR", "error_code": {}},
        {**base, "outcome": "ERROR", "error_code": b"LOCAL_FAULT_TRACE_CONFLICT"},
        {**base, "outcome": "ERROR", "error_code": True},
        {**base, "outcome": "ERROR", "error_code": 1},
    )
    for values in invalid:
        with pytest.raises(LocalRuntimeTraceError):
            LocalRuntimeTraceEvent(**values)  # type: ignore[arg-type]

    assert LOCAL_RUNTIME_TRACE_COMPONENTS == frozenset(
        {"sender", "receiver", "repository", "a2a"}
    )
    assert LOCAL_RUNTIME_TRACE_OPERATIONS == frozenset(
        {
            "REPLAY_REQUEST_VALIDATED",
            "INCIDENT_DURABLE_READBACK",
            "REPLAY_RESPONSE_ACCEPTED",
            "REPLAY_DELIVERY_ACKNOWLEDGED",
            "ANALYZE_REQUEST_VALIDATED",
            "ANALYZE_COMPLETED",
        }
    )


def test_sink_absence_and_exception_are_non_throwing() -> None:
    def broken_sink(_event: LocalRuntimeTraceEvent) -> None:
        raise RuntimeError("must not alter the replay result")

    arguments = {
        "trace_id": TRACE_ID,
        "component": "sender",
        "operation": "REPLAY_REQUEST_VALIDATED",
        "clock": lambda: NOW,
    }
    assert not emit_local_runtime_trace_event(None, **arguments)
    assert not emit_local_runtime_trace_event(broken_sink, **arguments)
    for failure in (SystemExit("sink exit"), KeyboardInterrupt("sink interrupt")):

        def base_failure_sink(
            _event: LocalRuntimeTraceEvent,
            *,
            selected: BaseException = failure,
        ) -> None:
            raise selected

        assert not emit_local_runtime_trace_event(  # type: ignore[arg-type]
            base_failure_sink,
            **arguments,
        )


def test_concurrent_emission_never_crosses_trace_identity() -> None:
    events: list[LocalRuntimeTraceEvent] = []
    lock = threading.Lock()

    def collect(event: LocalRuntimeTraceEvent) -> None:
        with lock:
            events.append(event)

    source_ids = tuple(f"labevent-{index:064x}" for index in range(64))

    def emit(source_event_id: str) -> None:
        trace_id = derive_local_replay_trace_id(source_event_id)
        assert emit_local_runtime_trace_event(
            collect,
            trace_id=trace_id,
            component="sender",
            operation="REPLAY_REQUEST_VALIDATED",
            clock=lambda: NOW,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        tuple(pool.map(emit, source_ids))

    assert len(events) == len(source_ids)
    assert {event.trace_id for event in events} == {
        derive_local_replay_trace_id(source_event_id) for source_event_id in source_ids
    }
    assert all(event.operation == "REPLAY_REQUEST_VALIDATED" for event in events)
