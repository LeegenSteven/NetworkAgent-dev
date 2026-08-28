"""Raw A2A 0.2.16 -> 0.3.11 compatibility and hostile-wire tests."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
from a2a.types import (
    SendMessageRequest,
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
)


from agent.a2a_parts import (
    A2AContentError,
    A2AStreamProtocolError,
    RemoteStreamStateMachine,
    UiThreadOwnership,
    decode_canonical_parts,
    decode_display_parts,
)


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "legacy-0.2.16.json"
CHALLENGE_ID = "challenge-" + "c" * 32


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _dump(model: object) -> dict[str, Any]:
    return model.model_dump(  # type: ignore[attr-defined]
        by_alias=True,
        exclude_none=True,
        mode="json",
    )


def test_legacy_fixture_is_consumed_only_by_exact_current_sdk() -> None:
    assert version("a2a-sdk") == "0.3.11"
    assert _fixture()["generated_with"] == {
        "distribution": "a2a-sdk",
        "version": "0.2.16",
    }


@pytest.mark.parametrize(
    ("name", "request_type"),
    [
        ("message_send", SendMessageRequest),
        ("message_stream", SendStreamingMessageRequest),
        ("continuation", SendMessageRequest),
        ("analysis", SendMessageRequest),
    ],
)
def test_0216_message_and_stream_requests_round_trip_in_0311(
    name: str,
    request_type: type[SendMessageRequest] | type[SendStreamingMessageRequest],
) -> None:
    raw = _fixture()["requests"][name]

    current = request_type.model_validate(raw)

    assert _dump(current) == raw


@pytest.mark.parametrize(
    "name",
    [
        "task_submitted",
        "working",
        "input_required",
        "artifact_text_data",
        "artifact_data_text",
        "error_artifact",
        "completed",
        "canceled",
        "rejected",
        "failed",
    ],
)
def test_0216_task_artifact_and_all_supported_states_round_trip_in_0311(
    name: str,
) -> None:
    raw = _fixture()["responses"][name]

    current = SendStreamingMessageSuccessResponse.model_validate(raw)

    assert _dump(current) == raw


@pytest.mark.parametrize("name", ["artifact_text_data", "artifact_data_text"])
def test_legacy_artifact_part_order_is_semantically_identical(name: str) -> None:
    result = _fixture()["responses"][name]["result"]

    decoded = decode_canonical_parts(
        result["artifact"],
        expected_task_id=result["taskId"],
        expected_context_id=result["contextId"],
    )

    assert decoded.text == "Incident created."
    assert decoded.data["message_type"] == "assurance_confirmation_result"
    assert decoded.message_id == "message-result-01"


def test_legacy_input_required_and_completed_text_have_strict_roles() -> None:
    responses = _fixture()["responses"]
    interrupted = responses["input_required"]["result"]
    content = decode_canonical_parts(
        interrupted["status"]["message"],
        expected_task_id=interrupted["taskId"],
        expected_context_id=interrupted["contextId"],
    )
    assert content.data["message_type"] == "assurance_candidate_page"
    assert content.data["challenge_id"] == CHALLENGE_ID
    assert content.data["effective_window_start"] < content.data["effective_window_end"]

    completed = responses["completed"]["result"]
    assert (
        decode_display_parts(completed["status"]["message"])
        == "Confirmation completed."
    )


def test_fixture_covers_all_six_assurance_message_types() -> None:
    fixture = _fixture()
    payloads: list[dict[str, Any]] = []
    for request in fixture["requests"].values():
        payloads.extend(
            part["data"]
            for part in request["params"]["message"]["parts"]
            if part["kind"] == "data"
        )
    for response in fixture["responses"].values():
        result = response["result"]
        if result.get("kind") == "artifact-update":
            payloads.extend(
                part["data"]
                for part in result["artifact"]["parts"]
                if part["kind"] == "data"
            )
        message = result.get("status", {}).get("message")
        if message:
            payloads.extend(
                part["data"]
                for part in message["parts"]
                if part["kind"] == "data"
            )

    assert {payload["message_type"] for payload in payloads} == {
        "assurance_scan_request",
        "assurance_candidate_page",
        "assurance_confirmation_request",
        "assurance_confirmation_result",
        "assurance_analyze_request",
        "assurance_error",
    }


def test_legacy_assurance_error_is_strict_structured_content() -> None:
    result = _fixture()["responses"]["error_artifact"]["result"]

    decoded = decode_canonical_parts(result["artifact"])

    assert decoded.text == "Request rejected."
    assert decoded.data == {
        "schema_version": "1.0",
        "message_type": "assurance_error",
        "message_id": "message-error-01",
        "error_code": "INVALID_REQUEST",
        "summary_zh": "Request rejected.",
        "sent_at": "2026-08-28T01:02:03Z",
    }


def test_legacy_lifecycle_requires_explicit_final_and_preserves_bindings() -> None:
    responses = _fixture()["responses"]
    task = responses["task_submitted"]["result"]
    working = responses["working"]["result"]
    interrupted = responses["input_required"]["result"]
    content = decode_canonical_parts(interrupted["status"]["message"])

    machine = RemoteStreamStateMachine()
    machine.observe_task(task["id"], task["contextId"], task["status"]["state"])
    machine.observe_status(
        working["taskId"],
        working["contextId"],
        working["status"]["state"],
        final=working["final"],
    )
    with pytest.raises(A2AStreamProtocolError, match="EOF"):
        machine.finish()
    machine.observe_status(
        interrupted["taskId"],
        interrupted["contextId"],
        interrupted["status"]["state"],
        final=interrupted["final"],
        content=content,
    )

    outcome = machine.finish()

    assert outcome.state == "input_required"
    assert outcome.task_id == _fixture()["identifiers"]["task_id"]
    assert outcome.context_id == _fixture()["identifiers"]["context_id"]
    assert outcome.content == content


def test_transport_and_business_correlation_identifiers_are_independent() -> None:
    fixture = _fixture()
    values = set(fixture["identifiers"].values())
    scan = fixture["requests"]["message_stream"]["params"]["message"]
    data = next(part["data"] for part in scan["parts"] if part["kind"] == "data")

    assert len(values) == 5
    assert scan["messageId"] == data["message_id"]
    assert fixture["identifiers"]["context_id"] not in {
        data["workflow_id"],
        data["trace_id"],
        data["idempotency_key"],
        data["message_id"],
    }
    assert len(values | {data["message_id"]}) == 6


@pytest.mark.parametrize(
    "attack_name",
    ["duplicate_data", "file_part", "unknown_part", "message_id_mismatch"],
)
def test_hostile_legacy_parts_fail_closed_without_echo(attack_name: str) -> None:
    attacks = _fixture()["attacks"]
    parts = attacks[attack_name]
    expected_message_id = (
        "message-scan-01" if attack_name == "message_id_mismatch" else None
    )

    with pytest.raises(A2AContentError) as caught:
        decode_canonical_parts(parts, expected_message_id=expected_message_id)

    assert CHALLENGE_ID not in str(caught.value)
    assert "Candidate reviewed" not in str(caught.value)


def test_hostile_legacy_size_and_depth_descriptors_fail_closed() -> None:
    fixture = _fixture()
    oversized = fixture["attacks"]["oversized_text"]
    repeat = oversized[0].pop("text_repeat")
    oversized[0]["text"] = repeat["character"] * repeat["count"]
    with pytest.raises(A2AContentError, match="length"):
        decode_canonical_parts(oversized)

    descriptor = fixture["attacks"]["overdeep_data"]
    nested: object = "leaf"
    for _ in range(descriptor["depth"]):
        nested = {descriptor["nested_key"]: nested}
    scan = fixture["requests"]["message_send"]["params"]["message"]
    data = next(part["data"] for part in scan["parts"] if part["kind"] == "data")
    deep_part = {**descriptor["part"], "data": {**data, "nested": nested}}
    with pytest.raises(A2AContentError, match="depth"):
        decode_canonical_parts([deep_part])


@pytest.mark.parametrize(
    ("attack_name", "expected"),
    [("task_id_mismatch", "task"), ("context_id_mismatch", "context")],
)
def test_hostile_legacy_event_container_ids_fail_closed(
    attack_name: str, expected: str
) -> None:
    event = _fixture()["attacks"][attack_name]
    machine = RemoteStreamStateMachine(
        expected_task_id="task-wire-01",
        expected_context_id="context-wire-01",
        starting_state="working",
    )

    with pytest.raises(A2AStreamProtocolError, match=expected):
        machine.observe_artifact(event["taskId"], event["contextId"], None)


def test_two_ui_sessions_cannot_cross_read_or_rebind_threads() -> None:
    ownership = UiThreadOwnership()
    ownership.bind("thread-a", "session-a")
    ownership.bind("thread-b", "session-b")

    ownership.require_owner("thread-a", "session-a")
    ownership.require_owner("thread-b", "session-b")
    with pytest.raises(PermissionError):
        ownership.require_owner("thread-a", "session-b")
    with pytest.raises(PermissionError):
        ownership.bind("thread-b", "session-a")
