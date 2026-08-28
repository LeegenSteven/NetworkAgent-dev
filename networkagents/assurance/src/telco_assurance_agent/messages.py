"""A2A presentation helpers that keep structured data canonical."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from a2a.types import DataPart, Message, Part, Role, TextPart

from .protocol import AssuranceError, MAX_DISPLAY_TEXT


class StructuredPayload(Protocol):
    message_id: str

    def to_data_part(self) -> dict[str, Any]: ...


def _text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("display text is required")
    if len(stripped) > MAX_DISPLAY_TEXT:
        raise ValueError("display text exceeds 4096 characters")
    return stripped


def text_message(text: str, *, task_id: str, context_id: str) -> Message:
    return Message(
        role=Role.agent,
        message_id=uuid4().hex,
        task_id=task_id,
        context_id=context_id,
        parts=[Part(root=TextPart(text=_text(text)))],
    )


def structured_message(
    payload: StructuredPayload,
    text: str,
    *,
    task_id: str,
    context_id: str,
) -> Message:
    data = payload.to_data_part()
    if data.get("message_id") != payload.message_id:
        raise ValueError("structured payload identifier mismatch")
    return Message(
        role=Role.agent,
        message_id=payload.message_id,
        task_id=task_id,
        context_id=context_id,
        parts=[
            Part(root=TextPart(text=_text(text))),
            Part(root=DataPart(data=data)),
        ],
    )


def structured_artifact_parts(
    payload: StructuredPayload, text: str
) -> list[Part]:
    data = payload.to_data_part()
    if data.get("message_id") != payload.message_id:
        raise ValueError("structured payload identifier mismatch")
    return [
        Part(root=TextPart(text=_text(text))),
        Part(root=DataPart(data=data)),
    ]


def safe_error_message(
    *,
    error_code: str,
    summary_zh: str,
    task_id: str,
    context_id: str,
    now: datetime | None = None,
) -> Message:
    error = AssuranceError(
        message_id=uuid4().hex,
        error_code=error_code,
        summary_zh=summary_zh,
        sent_at=(datetime.now(UTC) if now is None else now.astimezone(UTC)),
    )
    return structured_message(
        error,
        error.summary_zh,
        task_id=task_id,
        context_id=context_id,
    )


__all__ = [
    "safe_error_message",
    "structured_artifact_parts",
    "structured_message",
    "text_message",
]
