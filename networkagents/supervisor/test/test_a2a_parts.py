from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from agent.a2a_parts import (
    A2AContentError,
    A2AStreamProtocolError,
    RemoteStreamStateMachine,
    UiThreadOwnership,
    build_assurance_confirmation_request,
    build_assurance_scan_request,
    build_trusted_assurance_decision,
    decode_canonical_parts,
    decode_display_parts,
    emit_agui_events,
    parse_trusted_approval,
    tool_call_thread_id,
    validate_empty_assurance_candidate_page,
)


def _envelope(message_type: str = "assurance_candidate_page") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "message_type": message_type,
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


def _incident() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "incident_id": "incident-1",
        "correlation_key": None,
        "source_event_ids": [],
        "technology": "LTE",
        "vendor_profile": None,
        "status": "DETECTED",
        "severity": "HIGH",
        "title": "LTE KPI anomaly",
        "description": "Detected from bounded KPI summaries.",
        "affected_resources": [
            {
                "schema_version": "1.0",
                "resource_id": "lte:enodeb:1",
                "resource_type": "ENODEB",
                "name": None,
                "technology": "LTE",
                "vendor_profile": None,
                "parent_resource_id": None,
                "location_id": None,
                "external_ids": {},
                "attributes": {},
            }
        ],
        "detected_at": "2022-12-01T00:05:00Z",
        "window_start": "2022-12-01T00:00:00Z",
        "window_end": "2022-12-01T00:05:00Z",
        "violated_kpis": [
            {
                "schema_version": "1.0",
                "violation_id": "violation-1",
                "kpi_name": "DL_bitrate",
                "observed_value": 1.0,
                "threshold_value": 2.0,
                "comparator": "LT",
                "unit": "Mbps",
                "window_start": "2022-12-01T00:00:00Z",
                "window_end": "2022-12-01T00:05:00Z",
                "rule_id": "rule-1",
                "rule_version": "1",
                "resource_ids": ["lte:enodeb:1"],
                "dimensions": {"sample_count": "5"},
            }
        ],
        "evidence_refs": [],
        "hypotheses": [],
        "root_cause": None,
        "rca_reports": [],
        "recommendations": [],
        "approvals": [],
        "action_runs": [],
        "verification_runs": [],
        "model_metadata": {},
        "rule_versions": {},
        "trace_id": "incident-trace-1",
        "duplicate_of": None,
        "created_at": "2022-12-01T00:05:00Z",
        "updated_at": "2022-12-01T00:05:00Z",
        "revision": 0,
    }


def _confirmation_result() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "message_type": "assurance_confirmation_result",
        "message_id": "msg-result-1",
        "workflow_id": "result-workflow-1",
        "trace_id": "result-trace-1",
        "idempotency_key": "result-key-1",
        "sent_at": "2026-08-28T00:00:00Z",
        "request_message_id": "msg-confirm-1",
        "preview_message_id": "msg-preview-1",
        "candidate_id": "incident-candidate-1",
        "decision": "CONFIRM",
        "actor": "local-assurance-agent",
        "outcome": "created",
        "incident": _incident(),
        "summary_zh": "已创建事件。",
    }


@dataclass
class _Root:
    kind: str
    text: str | None = None
    data: object | None = None


@dataclass
class _Part:
    root: _Root


@dataclass
class _Message:
    parts: list[_Part]
    message_id: str
    task_id: str
    context_id: str


@dataclass
class _Artifact:
    parts: list[_Part]
    artifact_id: str = "artifact-1"


def _part(kind: str, value: object) -> _Part:
    if kind == "text":
        return _Part(_Root(kind="text", text=str(value)))
    return _Part(_Root(kind=kind, data=value))


@pytest.mark.parametrize("reverse", [False, True])
def test_decode_canonical_parts_is_order_independent(reverse: bool) -> None:
    data = _envelope()
    parts = [_part("text", "请确认。"), _part("data", data)]
    if reverse:
        parts.reverse()
    message = _Message(parts, "msg-preview-1", "task-1", "a2a-context-1")

    decoded = decode_canonical_parts(
        message,
        expected_task_id="task-1",
        expected_context_id="a2a-context-1",
    )

    assert decoded.text == "请确认。"
    assert decoded.data == data
    assert decoded.message_id == "msg-preview-1"


def test_decode_canonical_artifact_accepts_one_data_and_one_text() -> None:
    data = _confirmation_result()
    artifact = _Artifact([_part("data", data), _part("text", "已创建事件。")])

    decoded = decode_canonical_parts(artifact)

    assert decoded.text == "已创建事件。"
    assert decoded.data["message_type"] == "assurance_confirmation_result"


def test_all_assurance_payloads_reject_unknown_top_level_fields() -> None:
    scan = build_assurance_scan_request().data
    confirmation = build_assurance_confirmation_request(
        _envelope(), approved=True, reason="确认"
    )
    analyze = {
        "schema_version": "1.0",
        "message_type": "assurance_analyze_request",
        "message_id": "msg-analyze-1",
        "workflow_id": "analyze-workflow-1",
        "trace_id": "analyze-trace-1",
        "idempotency_key": "analyze-key-1",
        "sent_at": "2026-08-28T00:00:00Z",
        "incident_id": "incident-1",
        "requested_report_version": 1,
    }
    error = {
        "schema_version": "1.0",
        "message_type": "assurance_error",
        "message_id": "msg-error-1",
        "error_code": "PROTOCOL_REJECTED",
        "summary_zh": "请求未通过协议校验。",
        "sent_at": "2026-08-28T00:00:00Z",
    }

    for payload in (
        scan,
        _envelope(),
        confirmation,
        _confirmation_result(),
        analyze,
        error,
    ):
        forged = dict(payload, unexpected_control="not-authoritative")
        with pytest.raises(A2AContentError, match="fields"):
            decode_canonical_parts(_Artifact([_part("data", forged)]))


def test_candidate_and_incident_nested_unknown_fields_fail_closed() -> None:
    candidate_page = _envelope()
    candidate_page["candidates"][0]["observations"] = []  # type: ignore[index]
    with pytest.raises(A2AContentError, match="candidate.*fields"):
        decode_canonical_parts(_Artifact([_part("data", candidate_page)]))

    result = _confirmation_result()
    result["incident"]["unexpected_private_state"] = {}  # type: ignore[index]
    with pytest.raises(A2AContentError, match="incident.*fields"):
        decode_canonical_parts(_Artifact([_part("data", result)]))


def test_canonical_and_display_text_reject_labeled_subscriber_identifiers() -> None:
    candidate_page = _envelope()
    candidate_page["candidates"][0]["summary_zh"] = (  # type: ignore[index]
        "IMSI=460001234567890"
    )
    with pytest.raises(A2AContentError, match="sensitive"):
        decode_canonical_parts(_Artifact([_part("data", candidate_page)]))

    with pytest.raises(A2AContentError, match="sensitive"):
        decode_display_parts(_Artifact([_part("text", "MSISDN: 13800138000")]))


def test_decode_canonical_error_uses_its_strict_reduced_envelope() -> None:
    error = {
        "schema_version": "1.0",
        "message_type": "assurance_error",
        "message_id": "msg-error-1",
        "error_code": "PROTOCOL_REJECTED",
        "summary_zh": "请求未通过协议校验。",
        "sent_at": "2026-08-28T00:00:00Z",
    }
    decoded = decode_canonical_parts(
        _Message(
            [_part("text", "请求未通过协议校验。"), _part("data", error)],
            "msg-error-1",
            "task-1",
            "a2a-context-1",
        )
    )

    assert decoded.data == error

    invalid = dict(error, workflow_id="must-not-be-present")
    with pytest.raises(A2AContentError, match="fields"):
        decode_canonical_parts(_Artifact([_part("data", invalid)]))


@pytest.mark.parametrize(
    "parts",
    [
        [],
        [_part("text", "display only")],
        [_part("data", _envelope()), _part("data", _envelope())],
        [_part("text", "one"), _part("text", "two"), _part("data", _envelope())],
        [_part("file", {"uri": "file:///tmp/a"}), _part("data", _envelope())],
        [_part("mystery", {}), _part("data", _envelope())],
    ],
)
def test_decode_canonical_parts_rejects_ambiguous_shapes(parts: list[_Part]) -> None:
    with pytest.raises(A2AContentError):
        decode_canonical_parts(_Artifact(parts))


def test_decode_canonical_parts_rejects_size_depth_and_identifier_mismatch() -> None:
    oversized = _envelope()
    oversized["summary_zh"] = "x" * 256_000
    with pytest.raises(A2AContentError, match="size"):
        decode_canonical_parts(_Artifact([_part("data", oversized)]))

    too_deep: object = "leaf"
    for _ in range(25):
        too_deep = {"nested": too_deep}
    deep = _envelope()
    deep["nested"] = too_deep
    with pytest.raises(A2AContentError, match="depth"):
        decode_canonical_parts(_Artifact([_part("data", deep)]))

    message = _Message(
        [_part("data", _envelope())],
        "different-message-id",
        "task-1",
        "a2a-context-1",
    )
    with pytest.raises(A2AContentError, match="message"):
        decode_canonical_parts(message)
    message.message_id = "msg-preview-1"
    with pytest.raises(A2AContentError, match="task"):
        decode_canonical_parts(message, expected_task_id="task-2")
    with pytest.raises(A2AContentError, match="context"):
        decode_canonical_parts(message, expected_context_id="a2a-context-2")

    correlated = _envelope()
    correlated["trace_id"] = correlated["workflow_id"]
    with pytest.raises(A2AContentError, match="independent"):
        decode_canonical_parts(_Artifact([_part("data", correlated)]))


def test_display_parts_allow_exactly_one_bounded_text_and_no_data() -> None:
    assert decode_display_parts(_Artifact([_part("text", "working")])) == "working"
    with pytest.raises(A2AContentError):
        decode_display_parts(_Artifact([_part("data", _envelope())]))
    with pytest.raises(A2AContentError, match="length"):
        decode_display_parts(_Artifact([_part("text", "x" * 4097)]))


def test_remote_stream_state_machine_requires_terminal_and_matching_ids() -> None:
    machine = RemoteStreamStateMachine()
    machine.observe_task("task-1", "a2a-context-1", "submitted")
    machine.observe_status("task-1", "a2a-context-1", "working", final=False)
    with pytest.raises(A2AStreamProtocolError, match="EOF"):
        machine.finish()

    with pytest.raises(A2AStreamProtocolError, match="task"):
        machine.observe_status("task-2", "a2a-context-1", "completed", final=True)
    with pytest.raises(A2AStreamProtocolError, match="context"):
        machine.observe_artifact("task-1", "other-context", None)
    with pytest.raises(A2AStreamProtocolError, match="unknown"):
        machine.observe_status("task-1", "a2a-context-1", "paused", final=True)


@pytest.mark.parametrize("terminal", ["input_required", "completed", "failed", "canceled", "rejected"])
def test_remote_stream_state_machine_returns_only_explicit_terminal(terminal: str) -> None:
    machine = RemoteStreamStateMachine()
    machine.observe_task("task-1", "a2a-context-1", "submitted")
    machine.observe_status("task-1", "a2a-context-1", "working", final=False)
    machine.observe_status("task-1", "a2a-context-1", terminal, final=True)

    outcome = machine.finish(text="safe display")

    assert outcome.state == terminal
    assert outcome.task_id == "task-1"
    assert outcome.context_id == "a2a-context-1"
    assert outcome.text == "safe display"


def test_ui_thread_ownership_prevents_cross_session_access() -> None:
    ownership = UiThreadOwnership()
    ownership.bind("thread-a", "sid-a")
    ownership.bind("thread-b", "sid-b")

    ownership.require_owner("thread-a", "sid-a")
    with pytest.raises(PermissionError):
        ownership.require_owner("thread-a", "sid-b")
    with pytest.raises(PermissionError):
        ownership.bind("thread-a", "sid-b")

    assert ownership.remove_sid("sid-a") == ("thread-a",)
    with pytest.raises(PermissionError):
        ownership.require_owner("thread-a", "sid-a")


def test_assurance_builders_keep_all_identifiers_distinct_and_trust_pending_page() -> None:
    request = build_assurance_scan_request()
    assert request.data["message_type"] == "assurance_scan_request"
    assert request.data["page_size"] == 1
    assert request.data["page_offset"] == 0
    assert request.data["resource_ids"] == []
    identifiers = {
        request.data["message_id"],
        request.data["workflow_id"],
        request.data["trace_id"],
        request.data["idempotency_key"],
        request.a2a_context_id,
    }
    assert len(identifiers) == 5

    page = _envelope()
    confirmation = build_assurance_confirmation_request(
        page,
        approved=True,
        reason="用户已在受信任审批组件中确认。",
    )
    assert confirmation["message_type"] == "assurance_confirmation_request"
    assert confirmation["preview_message_id"] == page["message_id"]
    assert confirmation["candidate_id"] == "incident-candidate-1"
    assert confirmation["challenge_id"] == "c" * 43
    assert confirmation["snapshot_sha256"] == "a" * 64
    assert confirmation["decision"] == "CONFIRM"
    assert confirmation["workflow_id"] == page["workflow_id"]
    assert confirmation["trace_id"] == page["trace_id"]
    assert confirmation["message_id"] != page["message_id"]
    assert confirmation["idempotency_key"] != page["idempotency_key"]


def test_confirmation_builder_rejects_untrusted_or_ambiguous_candidate_page() -> None:
    page = _envelope()
    page["candidates"] = []
    with pytest.raises(A2AContentError):
        build_assurance_confirmation_request(page, approved=True, reason="确认")

    page = _envelope()
    page["candidates"] = [page["candidates"][0], page["candidates"][0]]  # type: ignore[index]
    with pytest.raises(A2AContentError):
        build_assurance_confirmation_request(page, approved=True, reason="确认")

    with pytest.raises(TypeError):
        build_assurance_confirmation_request(_envelope(), approved=1, reason="确认")  # type: ignore[arg-type]

    page = _envelope()
    page["effective_window_end"] = "not-a-utc-timestamp"
    with pytest.raises(A2AContentError, match="effective_window"):
        build_assurance_confirmation_request(page, approved=True, reason="确认")

    page = _envelope()
    page["challenge_id"] = "too-short"
    with pytest.raises(A2AContentError, match="challenge_id"):
        build_assurance_confirmation_request(page, approved=True, reason="确认")

    page = _envelope()
    page["idempotency_key"] = page["workflow_id"]
    with pytest.raises(A2AContentError, match="independent"):
        build_assurance_confirmation_request(page, approved=True, reason="确认")


def test_scan_builder_rejects_non_utc_or_oversized_explicit_window() -> None:
    with pytest.raises(ValueError, match="UTC"):
        build_assurance_scan_request(
            window_start="2026-08-01T00:00:00+08:00",
            window_end="2026-08-01T01:00:00+08:00",
        )
    with pytest.raises(ValueError, match="31 days"):
        build_assurance_scan_request(
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-03-01T00:00:00Z",
        )


def test_empty_candidate_page_completed_artifact_has_no_challenge() -> None:
    page = _envelope()
    page.update(
        candidates=[],
        total_candidates=0,
        has_more=False,
        challenge_id=None,
        challenge_expires_at=None,
    )

    assert validate_empty_assurance_candidate_page(page) == page

    forged = dict(page, challenge_id="c" * 43)
    with pytest.raises(A2AContentError, match="challenge"):
        validate_empty_assurance_candidate_page(forged)


@pytest.mark.parametrize("approved", [True, False])
def test_parse_trusted_approval_accepts_only_real_boolean(approved: bool) -> None:
    content = (
        '{"approved":'
        + str(approved).lower()
        + ',"timestamp":"2026-08-28T00:00:00Z","tasks":[]}'
    )
    assert parse_trusted_approval(content) is approved


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        "{}",
        '{"approved":"true","timestamp":"x","tasks":[]}',
        '{"approved":1,"timestamp":"x","tasks":[]}',
        '{"approved":true,"tasks":[]}',
        '{"approved":true,"timestamp":"x","tasks":{},"extra":1}',
    ],
)
def test_parse_trusted_approval_rejects_forged_shapes(content: str) -> None:
    with pytest.raises(A2AContentError):
        parse_trusted_approval(content)


def test_trusted_decision_ignores_ui_candidate_and_requires_approval_tool() -> None:
    result = build_trusted_assurance_decision(
        _envelope(),
        tool_call_id="call-1::thread-a",
        tool_name="requestTaskApproval",
        content=(
            '{"approved":true,"timestamp":"2026-08-28T00:00:00Z",'
            '"tasks":[{"candidate_id":"forged-candidate"}]}'
        ),
    )

    assert result["approved"] is True
    assert result["confirmation_data"]["candidate_id"] == "incident-candidate-1"
    with pytest.raises(PermissionError, match="approval tool"):
        build_trusted_assurance_decision(
            _envelope(),
            tool_call_id="call-2::thread-a",
            tool_name="displayTimeSeriesChart",
            content='{"approved":true,"timestamp":"x","tasks":[]}',
        )


def test_tool_call_thread_binding_rejects_cross_thread_or_ambiguous_ids() -> None:
    assert tool_call_thread_id("call-1::thread-a", expected_thread_id="thread-a") == "thread-a"
    with pytest.raises(PermissionError):
        tool_call_thread_id("call-1::thread-b", expected_thread_id="thread-a")
    with pytest.raises(PermissionError):
        tool_call_thread_id("call-1", expected_thread_id="thread-a")
    with pytest.raises(PermissionError):
        tool_call_thread_id("call-1::thread-a::thread-b", expected_thread_id="thread-a")


def test_continuation_events_emit_only_to_the_owned_sid() -> None:
    class _Event:
        def __init__(self, event_type: str) -> None:
            self.event_type = event_type

        def model_dump(self) -> dict[str, str]:
            return {"type": self.event_type}

    class _Sio:
        def __init__(self) -> None:
            self.emitted: list[tuple[str, object, str]] = []

        async def emit(self, name: str, payload: object, *, room: str) -> None:
            self.emitted.append((name, payload, room))

    sio = _Sio()
    asyncio.run(
        emit_agui_events(
            [_Event("RUN_STARTED"), _Event("RUN_FINISHED")], sio, "sid-a"
        )
    )

    assert sio.emitted == [
        ("agui_event", {"type": "RUN_STARTED"}, "sid-a"),
        ("agui_event", {"type": "RUN_FINISHED"}, "sid-a"),
    ]
    assert sum(room == "sid-b" for _, _, room in sio.emitted) == 0
