"""Render the A2A 0.2.16 compatibility fixture to standard output.

This script intentionally runs only in the isolated legacy SDK environment.
The normal P2b test environment contains exactly A2A SDK 0.3.11.
"""

from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from typing import Any

from a2a.types import (
    Artifact,
    DataPart,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


SDK_VERSION = "0.2.16"
CONTEXT_ID = "context-wire-01"
TASK_ID = "task-wire-01"
CHALLENGE_ID = "challenge-" + "c" * 32


def _dump(model: object) -> dict[str, Any]:
    return model.model_dump(  # type: ignore[attr-defined]
        by_alias=True,
        exclude_none=True,
        mode="json",
    )


def _text(value: str) -> Part:
    return Part(root=TextPart(text=value))


def _data(value: dict[str, Any]) -> Part:
    return Part(root=DataPart(data=value))


def _message(
    message_id: str,
    parts: list[Part],
    *,
    role: Role = Role.user,
    continuation: bool = False,
) -> Message:
    return Message(
        messageId=message_id,
        role=role,
        parts=parts,
        taskId=TASK_ID if continuation else None,
        contextId=CONTEXT_ID,
    )


def _response(result: object, request_id: str) -> dict[str, Any]:
    return _dump(
        SendStreamingMessageSuccessResponse(id=request_id, result=result)
    )


def render_fixture() -> dict[str, Any]:
    installed_version = version("a2a-sdk")
    if installed_version != SDK_VERSION:
        raise RuntimeError(
            f"legacy fixture requires a2a-sdk=={SDK_VERSION}, got {installed_version}"
        )

    common = {
        "schema_version": "1.0",
        "workflow_id": "workflow-wire-01",
        "trace_id": "trace-wire-01",
        "idempotency_key": "idempotency-wire-01",
        "sent_at": "2026-08-28T01:02:03Z",
    }
    scan = {
        **common,
        "message_type": "assurance_scan_request",
        "message_id": "message-scan-01",
        "window_start": None,
        "window_end": None,
        "resource_ids": [],
        "page_size": 1,
        "page_offset": 0,
    }
    candidate_page = {
        **common,
        "message_type": "assurance_candidate_page",
        "message_id": "message-preview-01",
        "request_message_id": "message-scan-01",
        "effective_window_start": "2022-01-01T00:00:00Z",
        "effective_window_end": "2022-01-01T23:59:59Z",
        "candidates": [
            {
                "candidate_id": "candidate-01",
                "title": "LTE KPI anomaly",
                "technology": "LTE",
                "window_start": "2022-01-01T00:00:00Z",
                "window_end": "2022-01-01T00:15:00Z",
                "affected_resources": [
                    {
                        "resource_id": "lte:enodeb:1:cell:1",
                        "resource_type": "CELL",
                        "technology": "LTE",
                    }
                ],
                "violated_kpis": [
                    {
                        "kpi_name": "DL_bitrate",
                        "observed_value": 1.0,
                        "threshold_value": 5.0,
                        "comparator": "LT",
                        "unit": "Mbps",
                        "sample_count": 1,
                    }
                ],
                "summary_zh": "One anomaly candidate.",
            }
        ],
        "page_size": 1,
        "page_offset": 0,
        "total_candidates": 1,
        "has_more": False,
        "challenge_id": CHALLENGE_ID,
        "snapshot_sha256": "a" * 64,
        "challenge_expires_at": "2026-08-28T01:12:03Z",
        "summary_zh": "Confirm incident creation.",
    }
    confirmation = {
        **common,
        "message_type": "assurance_confirmation_request",
        "message_id": "message-confirm-01",
        "preview_message_id": "message-preview-01",
        "candidate_id": "candidate-01",
        "challenge_id": CHALLENGE_ID,
        "snapshot_sha256": "a" * 64,
        "decision": "CONFIRM",
        "reason": "Candidate reviewed.",
    }
    confirmation_result = {
        **common,
        "message_type": "assurance_confirmation_result",
        "message_id": "message-result-01",
        "request_message_id": "message-confirm-01",
        "preview_message_id": "message-preview-01",
        "candidate_id": "candidate-01",
        "decision": "CONFIRM",
        "outcome": "created",
        "actor": "supervisor:user",
        "incident": {
            "incident_id": "incident-01",
            "trace_id": "trace-wire-01",
        },
        "summary_zh": "Incident created.",
    }
    analysis = {
        **common,
        "message_type": "assurance_analyze_request",
        "message_id": "message-analyze-01",
        "incident_id": "incident-01",
        "requested_report_version": 1,
    }
    assurance_error = {
        "schema_version": "1.0",
        "message_type": "assurance_error",
        "message_id": "message-error-01",
        "error_code": "INVALID_REQUEST",
        "summary_zh": "Request rejected.",
        "sent_at": "2026-08-28T01:02:03Z",
    }

    scan_message = _message(
        "message-scan-01",
        [_text("Scan local LTE performance data."), _data(scan)],
    )
    confirmation_message = _message(
        "message-confirm-01",
        [_data(confirmation), _text("Confirm incident creation.")],
        continuation=True,
    )
    analysis_message = _message(
        "message-analyze-01",
        [_text("Analyze the incident."), _data(analysis)],
    )
    submitted_task = Task(
        id=TASK_ID,
        contextId=CONTEXT_ID,
        status=TaskStatus(state=TaskState.submitted),
    )

    def status(
        state: TaskState,
        request_id: str,
        *,
        final: bool,
        message: Message | None = None,
    ) -> dict[str, Any]:
        return _response(
            TaskStatusUpdateEvent(
                taskId=TASK_ID,
                contextId=CONTEXT_ID,
                final=final,
                status=TaskStatus(state=state, message=message),
            ),
            request_id,
        )

    input_message = _message(
        "message-preview-01",
        [_text("Confirm the candidate."), _data(candidate_page)],
        role=Role.agent,
    )
    artifact_text_data = TaskArtifactUpdateEvent(
        taskId=TASK_ID,
        contextId=CONTEXT_ID,
        lastChunk=True,
        artifact=Artifact(
            artifactId="artifact-result-01",
            parts=[_text("Incident created."), _data(confirmation_result)],
        ),
    )
    artifact_data_text = TaskArtifactUpdateEvent(
        taskId=TASK_ID,
        contextId=CONTEXT_ID,
        lastChunk=True,
        artifact=Artifact(
            artifactId="artifact-result-02",
            parts=[_data(confirmation_result), _text("Incident created.")],
        ),
    )
    error_artifact = TaskArtifactUpdateEvent(
        taskId=TASK_ID,
        contextId=CONTEXT_ID,
        lastChunk=True,
        artifact=Artifact(
            artifactId="artifact-error-01",
            parts=[_text("Request rejected."), _data(assurance_error)],
        ),
    )

    return {
        "fixture_schema": "networkagent.a2a-wire-fixture/1",
        "generated_with": {"distribution": "a2a-sdk", "version": SDK_VERSION},
        "identifiers": {
            "context_id": CONTEXT_ID,
            "task_id": TASK_ID,
            "workflow_id": common["workflow_id"],
            "trace_id": common["trace_id"],
            "idempotency_key": common["idempotency_key"],
        },
        "requests": {
            "message_send": _dump(
                SendMessageRequest(
                    id="rpc-send-01",
                    params=MessageSendParams(message=scan_message),
                )
            ),
            "message_stream": _dump(
                SendStreamingMessageRequest(
                    id="rpc-stream-01",
                    params=MessageSendParams(message=scan_message),
                )
            ),
            "continuation": _dump(
                SendMessageRequest(
                    id="rpc-confirm-01",
                    params=MessageSendParams(message=confirmation_message),
                )
            ),
            "analysis": _dump(
                SendMessageRequest(
                    id="rpc-analyze-01",
                    params=MessageSendParams(message=analysis_message),
                )
            ),
        },
        "responses": {
            "task_submitted": _response(submitted_task, "rpc-stream-01"),
            "working": status(
                TaskState.working,
                "rpc-stream-01",
                final=False,
                message=_message(
                    "message-working-01",
                    [_text("Detecting anomalies.")],
                    role=Role.agent,
                ),
            ),
            "input_required": status(
                TaskState.input_required,
                "rpc-stream-01",
                final=True,
                message=input_message,
            ),
            "artifact_text_data": _response(
                artifact_text_data, "rpc-confirm-01"
            ),
            "artifact_data_text": _response(
                artifact_data_text, "rpc-confirm-01"
            ),
            "error_artifact": _response(error_artifact, "rpc-error-01"),
            "completed": status(
                TaskState.completed,
                "rpc-confirm-01",
                final=True,
                message=_message(
                    "message-completed-01",
                    [_text("Confirmation completed.")],
                    role=Role.agent,
                ),
            ),
            "canceled": status(
                TaskState.canceled, "rpc-cancel-01", final=True
            ),
            "rejected": status(
                TaskState.rejected, "rpc-reject-01", final=True
            ),
            "failed": status(TaskState.failed, "rpc-failed-01", final=True),
        },
        "attacks": {
            "duplicate_data": [
                {"kind": "data", "data": scan},
                {"kind": "data", "data": scan},
            ],
            "file_part": [
                {"kind": "file", "file": {"name": "secret.txt"}}
            ],
            "unknown_part": [{"kind": "future-kind", "value": "x"}],
            "message_id_mismatch": [
                {"kind": "data", "data": {**scan, "message_id": "other"}}
            ],
            "task_id_mismatch": {
                **_dump(artifact_text_data),
                "taskId": "other-task",
            },
            "context_id_mismatch": {
                **_dump(artifact_text_data),
                "contextId": "other-context",
            },
            "oversized_text": [
                {"kind": "text", "text_repeat": {"character": "x", "count": 256001}},
                {"kind": "data", "data": scan},
            ],
            "overdeep_data": {
                "part": {"kind": "data"},
                "nested_key": "child",
                "depth": 25,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        type=str,
        help="compare against a committed fixture instead of printing",
    )
    args = parser.parse_args()
    rendered = render_fixture()
    if args.check:
        from pathlib import Path

        committed = json.loads(Path(args.check).read_text(encoding="utf-8-sig"))
        if committed != rendered:
            raise SystemExit("legacy A2A fixture is stale")
        print(f"legacy A2A {SDK_VERSION} fixture verified")
        return 0
    print(
        json.dumps(
            rendered,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
