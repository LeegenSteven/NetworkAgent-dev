from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

pytest.importorskip("google.adk")

from agent.host_agent import HostAgent


def _candidate_page() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "message_type": "assurance_candidate_page",
        "message_id": "msg-preview-1",
        "workflow_id": "workflow-1",
        "trace_id": "trace-1",
        "idempotency_key": "preview-key-1",
        "sent_at": "2026-08-28T00:00:00Z",
        "request_message_id": "msg-scan-1",
        "candidates": [
            {
                "candidate_id": "incident-candidate-1",
                "title": "LTE KPI anomaly",
                "technology": "LTE",
                "window_start": "2022-12-01T00:00:00Z",
                "window_end": "2022-12-01T00:05:00Z",
                "affected_resources": [
                    {
                        "resource_id": "lte:enodeb:1",
                        "resource_type": "ENODEB",
                        "technology": "LTE",
                    }
                ],
                "violated_kpis": [
                    {
                        "kpi_name": "DL_bitrate",
                        "observed_value": 1.0,
                        "threshold_value": 2.0,
                        "comparator": "LT",
                        "unit": "Mbps",
                        "sample_count": 5,
                    }
                ],
                "summary_zh": "发现一个候选异常。",
            }
        ],
        "page_size": 1,
        "page_offset": 0,
        "total_candidates": 1,
        "has_more": False,
        "challenge_id": "c" * 43,
        "snapshot_sha256": "a" * 64,
        "challenge_expires_at": "2026-08-28T00:15:00Z",
        "effective_window_start": "2022-12-01T00:00:00Z",
        "effective_window_end": "2022-12-01T00:05:00Z",
        "summary_zh": "请确认是否创建事件。",
    }


class _Ownership:
    def require_owner(self, thread_id: str, sid: str) -> None:
        if (thread_id, sid) != ("thread-a", "sid-a"):
            raise PermissionError("not owner")


class _FakeAdk:
    def __init__(self, *, fail_first_continuation: bool = False) -> None:
        self.fail_first_continuation = fail_first_continuation
        self.continuation_starts = 0

    async def is_pending_tool_call(self, thread_id: str, call_id: str) -> bool:
        return (thread_id, call_id) == ("thread-a", "call-a::thread-a")

    async def pending_tool_call_name(
        self, thread_id: str, call_id: str
    ) -> str | None:
        return "requestTaskApproval"

    async def handle_tool_result(
        self, session_id: str, tool_call_id: str, content: str
    ):
        self.continuation_starts += 1
        if self.fail_first_continuation and self.continuation_starts == 1:
            raise PermissionError("simulated pending consume failure")
        yield {"type": "RUN_STARTED"}
        yield {"type": "RUN_FINISHED"}


def _host(state: dict[str, object], adk: _FakeAdk) -> HostAgent:
    host = object.__new__(HostAgent)
    host._tool_result_locks = {}  # type: ignore[attr-defined]
    host.ui_thread_ownership = _Ownership()  # type: ignore[attr-defined]
    host.adk_agent = adk  # type: ignore[attr-defined]

    async def get_state(session_id: str) -> dict[str, object]:
        return deepcopy(state)

    host._get_session_state = get_state  # type: ignore[method-assign]
    return host


def _initial_state() -> dict[str, object]:
    return {
        "agent": "Local Assurance Agent",
        "task_status": "input_needed",
        "pending_assurance": _candidate_page(),
        "trusted_assurance_decision": None,
    }


def test_persistence_failure_does_not_start_or_consume_continuation() -> None:
    state = _initial_state()
    adk = _FakeAdk()
    host = _host(state, adk)

    async def fail_update(session_id: str, **values: object) -> None:
        raise RuntimeError("simulated state write failure")

    host.updateState = fail_update  # type: ignore[method-assign]

    async def collect() -> None:
        async for _ in host.handleToolResult(
            "thread-a",
            "sid-a",
            "call-a::thread-a",
            '{"approved":true,"timestamp":"2026-08-28T00:00:00Z","tasks":[]}',
        ):
            pass

    with pytest.raises(RuntimeError, match="state write"):
        asyncio.run(collect())
    assert adk.continuation_starts == 0


def test_consume_failure_retry_reuses_persisted_confirmation_identifiers() -> None:
    state = _initial_state()
    adk = _FakeAdk(fail_first_continuation=True)
    host = _host(state, adk)

    async def update(session_id: str, **values: object) -> None:
        state.update(deepcopy(values))

    host.updateState = update  # type: ignore[method-assign]
    content = (
        '{"approved":true,"timestamp":"2026-08-28T00:00:00Z","tasks":[]}'
    )

    async def collect() -> list[object]:
        return [
            event
            async for event in host.handleToolResult(
                "thread-a", "sid-a", "call-a::thread-a", content
            )
        ]

    with pytest.raises(PermissionError, match="consume"):
        asyncio.run(collect())
    first = deepcopy(state["trusted_assurance_decision"])
    assert isinstance(first, dict)

    assert asyncio.run(collect()) == [
        {"type": "RUN_STARTED"},
        {"type": "RUN_FINISHED"},
    ]
    assert state["trusted_assurance_decision"] == first
