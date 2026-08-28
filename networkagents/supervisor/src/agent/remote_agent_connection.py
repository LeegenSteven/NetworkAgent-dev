# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fail-closed A2A client bridge used by the Supervisor chat path."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import A2AClient
from a2a.types import (
    AgentCard,
    SendMessageRequest,
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

from agent.a2a_parts import (
    A2AContentError,
    A2AStreamProtocolError,
    CanonicalContent,
    RemoteStreamOutcome,
    RemoteStreamStateMachine,
    decode_canonical_parts,
    decode_display_parts,
)
from utils.error_handler import (
    ErrorSeverity,
    RemoteAgentError,
    SupervisorAgentError,
    send_error_message,
)


logger = logging.getLogger(__name__)

ASSURANCE_AGENT_NAME = "Local Assurance Agent"
MAX_STREAM_EVENTS = 256


def _attribute(value: object, *names: str, default: object = None) -> object:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _state_name(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise A2AStreamProtocolError("unknown task state")
    normalized = raw.lower().replace("-", "_")
    if normalized.startswith("taskstate."):
        normalized = normalized.split(".", 1)[1]
    return normalized


def _event_identifiers(event: object) -> tuple[object, object]:
    return (
        _attribute(event, "task_id", "taskId"),
        _attribute(event, "context_id", "contextId"),
    )


class RemoteAgentConnections:
    """One reusable, explicitly closed connection to a discovered remote Agent."""

    def __init__(self, host_agent: object, agent_card: AgentCard, address: str):
        self.host_agent = host_agent
        self.card = agent_card
        self.address = address
        self.agent_client: A2AClient | None = None
        self._httpx_client: httpx.AsyncClient | None = None

    @property
    def is_assurance_agent(self) -> bool:
        return self.card.name == ASSURANCE_AGENT_NAME

    async def create_client(self) -> None:
        logger.info("Creating A2A client agent=%s", self.card.name)
        await self.aclose()
        self._httpx_client = httpx.AsyncClient(timeout=30.0)
        self.agent_client = A2AClient(
            httpx_client=self._httpx_client,
            agent_card=self.card,
        )
        # The discovered card can advertise an internal URL.  The configured
        # address is the Supervisor-owned routing decision.
        self.agent_client.url = self.address

    async def aclose(self) -> None:
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
        self._httpx_client = None
        self.agent_client = None

    def get_agent(self) -> AgentCard:
        return self.card

    async def send_display_message(
        self,
        socket_target: tuple[str, object],
        text: str | None,
    ) -> None:
        """Send one bounded display string to exactly one authorized room."""

        if text is None or not text:
            return
        if len(text) > 4_096:
            raise A2AContentError("display text exceeds the limit")
        sid, sio = socket_target
        message_id = str(uuid4())
        events = (
            {
                "type": "TEXT_MESSAGE_START",
                "timestamp": None,
                "raw_event": None,
                "message_id": message_id,
                "role": "assistant",
            },
            {
                "type": "TEXT_MESSAGE_CONTENT",
                "timestamp": None,
                "raw_event": None,
                "message_id": message_id,
                "delta": text,
            },
            {
                "type": "TEXT_MESSAGE_END",
                "timestamp": None,
                "raw_event": None,
                "message_id": message_id,
            },
        )
        for event in events:
            await sio.emit("agui_event", event, room=sid)

    async def send_task(self, request: SendMessageRequest) -> object:
        """Legacy non-streaming path; never logs the request payload."""

        if self.agent_client is None:
            raise RemoteAgentError(
                message="Remote agent client is unavailable",
                agent_name=self.card.name,
            )
        logger.info("Sending non-streaming A2A request agent=%s", self.card.name)
        try:
            return await self.agent_client.send_message(request)
        except httpx.HTTPError as exc:
            logger.error(
                "Remote non-streaming HTTP failure agent=%s type=%s",
                self.card.name,
                type(exc).__name__,
            )
            raise RemoteAgentError(
                message="Remote agent request failed",
                agent_name=self.card.name,
                original_exception=exc,
            ) from exc

    @staticmethod
    def _canonical_type(
        content: CanonicalContent,
        expected: set[str],
    ) -> CanonicalContent:
        if content.data.get("message_type") not in expected:
            raise A2AContentError("unexpected assurance payload type")
        return content

    def _status_content(
        self,
        state: str,
        message: object | None,
        *,
        task_id: str,
        context_id: str,
    ) -> tuple[str | None, CanonicalContent | None]:
        if message is None:
            if self.is_assurance_agent and state == "input_required":
                raise A2AContentError("assurance input_required is missing its DataPart")
            return None, None

        if self.is_assurance_agent and state == "input_required":
            content = self._canonical_type(
                decode_canonical_parts(
                    message,
                    expected_task_id=task_id,
                    expected_context_id=context_id,
                ),
                {"assurance_candidate_page"},
            )
            return content.text, content

        if self.is_assurance_agent and state in {"failed", "rejected"}:
            try:
                content = self._canonical_type(
                    decode_canonical_parts(
                        message,
                        expected_task_id=task_id,
                        expected_context_id=context_id,
                    ),
                    {"assurance_error"},
                )
                return content.text, content
            except A2AContentError:
                return (
                    decode_display_parts(
                        message,
                        expected_task_id=task_id,
                        expected_context_id=context_id,
                    ),
                    None,
                )

        return (
            decode_display_parts(
                message,
                expected_task_id=task_id,
                expected_context_id=context_id,
            ),
            None,
        )

    async def send_streaming_task(
        self,
        request: SendStreamingMessageRequest,
        *,
        ui_session_id: str,
        socket_target: tuple[str, object],
        expected_task_id: str | None = None,
        expected_context_id: str | None = None,
        starting_state: str | None = None,
    ) -> RemoteStreamOutcome:
        """Consume one A2A stream and return only an explicit final outcome."""

        if self.agent_client is None:
            error = RemoteAgentError(
                message="Remote agent client is unavailable",
                agent_name=self.card.name,
            )
            await send_error_message(socket_target, error)
            raise error
        if not bool(_attribute(self.card.capabilities, "streaming", default=False)):
            error = RemoteAgentError(
                message="Remote agent does not support streaming",
                agent_name=self.card.name,
            )
            await send_error_message(socket_target, error)
            raise error

        machine = RemoteStreamStateMachine(
            expected_task_id=expected_task_id,
            expected_context_id=expected_context_id,
            starting_state=starting_state,
        )
        logger.info(
            "Starting A2A stream agent=%s continuation=%s",
            self.card.name,
            expected_task_id is not None,
        )
        try:
            event_count = 0
            async for chunk in self.agent_client.send_message_streaming(request):
                event_count += 1
                if event_count > MAX_STREAM_EVENTS:
                    raise A2AStreamProtocolError("remote stream event limit exceeded")
                root = _attribute(chunk, "root")
                if not isinstance(root, SendStreamingMessageSuccessResponse):
                    raise A2AStreamProtocolError("remote stream returned an error response")
                result = root.result

                if isinstance(result, Task):
                    task_state = _state_name(result.status.state)
                    if expected_task_id is None:
                        machine.observe_task(result.id, result.context_id, task_state)
                    else:
                        if result.id != expected_task_id:
                            raise A2AStreamProtocolError("task identifier mismatch")
                        if result.context_id != expected_context_id:
                            raise A2AStreamProtocolError("context identifier mismatch")
                    await self.host_agent.updateState(
                        session_id=ui_session_id,
                        agent_name=self.card.name,
                        task_status="submitted",
                        task_id=result.id,
                        a2a_context_id=result.context_id,
                    )
                    logger.info(
                        "Observed A2A Task agent=%s task=%s",
                        self.card.name,
                        result.id,
                    )
                    continue

                if isinstance(result, TaskArtifactUpdateEvent):
                    task_id, context_id = _event_identifiers(result)
                    artifact = result.artifact
                    canonical: CanonicalContent | None = None
                    if self.is_assurance_agent:
                        canonical = self._canonical_type(
                            decode_canonical_parts(artifact),
                            {
                                "assurance_candidate_page",
                                "assurance_confirmation_result",
                                "assurance_error",
                            },
                        )
                        text = canonical.text
                    else:
                        text = decode_display_parts(artifact)
                    machine.observe_artifact(task_id, context_id, canonical)
                    await self.send_display_message(socket_target, text)
                    logger.info(
                        "Observed A2A artifact agent=%s task=%s",
                        self.card.name,
                        task_id,
                    )
                    continue

                if isinstance(result, TaskStatusUpdateEvent):
                    task_id, context_id = _event_identifiers(result)
                    if not isinstance(task_id, str) or not isinstance(context_id, str):
                        raise A2AStreamProtocolError("status identifiers are missing")
                    state = _state_name(result.status.state)
                    display_text, canonical = self._status_content(
                        state,
                        result.status.message,
                        task_id=task_id,
                        context_id=context_id,
                    )
                    machine.observe_status(
                        task_id,
                        context_id,
                        state,
                        final=bool(result.final),
                        content=canonical,
                    )
                    logger.info(
                        "Observed A2A status agent=%s task=%s state=%s final=%s",
                        self.card.name,
                        task_id,
                        state,
                        bool(result.final),
                    )
                    if state == "working":
                        await self.send_display_message(socket_target, display_text)
                    if state in {
                        "input_required",
                        "completed",
                        "failed",
                        "canceled",
                        "rejected",
                    }:
                        return machine.finish(text=display_text)
                    continue

                raise A2AStreamProtocolError("unsupported remote stream event")

            return machine.finish()
        except SupervisorAgentError as exc:
            logger.error(
                "Remote stream aborted agent=%s type=%s",
                self.card.name,
                type(exc).__name__,
            )
            await send_error_message(socket_target, exc)
            raise
        except (A2AContentError, A2AStreamProtocolError, httpx.HTTPError) as exc:
            logger.error(
                "Remote stream rejected agent=%s type=%s",
                self.card.name,
                type(exc).__name__,
            )
            error = RemoteAgentError(
                message="Remote agent stream failed validation",
                agent_name=self.card.name,
                severity=ErrorSeverity.ERROR,
                original_exception=exc,
            )
            await send_error_message(socket_target, error)
            raise error from exc
        except Exception as exc:
            logger.error(
                "Remote stream failed agent=%s type=%s",
                self.card.name,
                type(exc).__name__,
            )
            error = RemoteAgentError(
                message="Remote agent stream failed",
                agent_name=self.card.name,
                severity=ErrorSeverity.ERROR,
                original_exception=exc,
            )
            await send_error_message(socket_target, error)
            raise error from exc


__all__ = ["ASSURANCE_AGENT_NAME", "RemoteAgentConnections"]
