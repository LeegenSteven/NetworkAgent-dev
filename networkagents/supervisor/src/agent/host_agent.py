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

import asyncio
import httpx
from uuid import uuid4
import json
from collections.abc import AsyncGenerator, Mapping
from typing import Any
import logging
import os
import agent.prompts.supervisor as prompts
from agent_library.credentials.creds import get_credentials
import datetime
from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    SendStreamingMessageRequest,
    MessageSendParams,
    SendMessageRequest
)
from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from utils.error_handler import (
    SupervisorAgentError,
    RemoteAgentError,
    ErrorSeverity,
    send_error_message
)

from .a2a_parts import (
    A2AContentError,
    UiThreadOwnership,
    build_assurance_confirmation_request,
    build_assurance_scan_request,
    build_trusted_assurance_decision,
    decode_canonical_parts,
    parse_trusted_approval,
    tool_call_thread_id,
    validate_empty_assurance_candidate_page,
)
from .remote_agent_connection import ASSURANCE_AGENT_NAME, RemoteAgentConnections
from agent_library.agentmiddleware.adk import ADKAgent
from ag_ui.core import RunAgentInput

logger = logging.getLogger(__name__)

_UNSET = object()

class HostAgent:
    """
    The host agent.

    This is the agent responsible for choosing which remote agents to send
    tasks to and coordinate their work.
    """

    # static agent instance
    _instance = None

    @classmethod
    async def get_instance(cls):
        if HostAgent._instance is None:
            HostAgent._instance = cls()
            await HostAgent._instance.load_remote_agents()
        return HostAgent._instance

    def __init__(self):
        """
        Init agent and runner
        """
        self.credentials,self.projectid = get_credentials()

        self.app_name = "host_network_agent"

        self.remote_agent_addresses = []
        # Check if AGENTS_URL environment variable exists and parse it
        agents_url = os.environ.get('AGENTS_URL')
        if agents_url:
            self.remote_agent_addresses = [url.strip() for url in agents_url.split(',')]

        self.cards = {}

        # dict with sid->sio for sending messages back to dashboard socket session
        self.sio_sessions = {}
        self.ui_thread_ownership = UiThreadOwnership()
        self._tool_result_locks: dict[str, asyncio.Lock] = {}

        self.host_agent = self.create_agent()
        # list of loaded remote agents
        self.agents = None

        # Initialize ADKAgent wrapper for AG-UI protocol support
        # Let ADKAgent manage its own session and artifact services
        self.adk_agent = ADKAgent(
            adk_agent=self.host_agent,
            app_name=self.app_name,
            use_in_memory_services=True
        )


    async def load_remote_agents(self):
        """
        (Re)Load the list of agent urls
        """
        try:
            # Close any existing connections
            if hasattr(self, 'remote_agent_connections') and self.remote_agent_connections:
                for connection in self.remote_agent_connections.values():
                    await connection.aclose()
                        
            # Initialize new connections
            self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
            self.cards: dict[str, AgentCard] = {}

            for address in self.remote_agent_addresses:
                try:
                    async with httpx.AsyncClient() as httpx_client:
                        try:
                            card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=address)
                            card = await card_resolver.get_agent_card()
                            card.url=address
                            logger.info("Loaded A2A AgentCard name=%s", card.name)
                            remote_connection = RemoteAgentConnections(self, card, address)
                            await remote_connection.create_client()
                            self.remote_agent_connections[card.name] = remote_connection
                            self.cards[card.name] = card

                        except httpx.HTTPError as e:
                            logger.error(f"HTTP error loading remote agent at {address}: {str(e)}", exc_info=True)
                            # Continue to the next address
                            continue
                        except Exception as e:
                            logger.error(f"Error loading remote agent at {address}: {str(e)}", exc_info=True)
                            # Continue to the next address
                            continue
                except Exception as e:
                    logger.error(f"Unexpected error loading remote agent at {address}: {str(e)}", exc_info=True)
                    # Continue to the next address
                    continue

            agent_info = []
            for ra in self.list_remote_agents():
                agent_info.append(json.dumps(ra))

            self.agents = '\n'.join(agent_info)
        except Exception as e:
            logger.error(f"Error loading remote agents: {str(e)}", exc_info=True)
            raise SupervisorAgentError(
                message=f"Error loading remote agents: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

    def create_agent(self) -> Agent:
        """
        Create ADK Host Agent

        Returns:
            Gemini agent with list of remote agents and task to route
        """
        # Create the base agent
        base_agent = Agent(
            model='gemini-2.5-flash',
            name=self.app_name,
            instruction=self.root_instruction,
            description=(
                'This agent orchestrates the decomposition of the user request into'
                'tasks that can be performed by the child agents.'
            ),
            tools=[
                self.list_remote_agents,
                self.send_task,
            ],
        )
                
        return base_agent

    def root_instruction(self, context: ReadonlyContext) -> str:
        """
        Build the root instruction for the host agent
        """
        current_agent = self.check_state(context)
        return prompts.supervisor_prompt.format(agents=self.agents, current_agent=current_agent['active_agent'], current_time=datetime.datetime.now().isoformat(),current_task_status=current_agent['task_status'])

    def check_state(self, context: ReadonlyContext):
        state = context.state
        returnObj={
            'active_agent': 'Supervisor',
            'task_status': None
        }
        if ('agent' in state):
            returnObj['active_agent']=f'{state["agent"]}'
        if ('task_status' in state):
            returnObj['task_status']=f'{state["task_status"]}'

        return returnObj

    async def add_remote_agent(self, agent_url: str):
        """
        Add a new remote agent to the list

        Args:
            agent_url: valid url for the remote agent
        Returns:
            dict with description, name and url
        """
        logger.info("adding agent %s", agent_url)

        try:
            self.remote_agent_addresses.append(agent_url)
            response = {}
            async with httpx.AsyncClient() as httpx_client:
                try:
                    card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=agent_url)
                    card = await card_resolver.get_agent_card()
                    response['id'] = str(uuid4())  # Add an ID for the agent
                    response['name'] = card.name
                    response['description'] = card.description
                    response['url'] = agent_url
                
                    await self.load_remote_agents()

                    return response
                except httpx.HTTPError as e:
                    # Remove the agent URL since it failed
                    if agent_url in self.remote_agent_addresses:
                        self.remote_agent_addresses.remove(agent_url)
                    
                    logger.error(f"HTTP error adding remote agent at {agent_url}: {str(e)}", exc_info=True)
                    raise RemoteAgentError(
                        message=f"HTTP error adding remote agent: {str(e)}",
                        agent_name=agent_url,
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
                except Exception as e:
                    # Remove the agent URL since it failed
                    if agent_url in self.remote_agent_addresses:
                        self.remote_agent_addresses.remove(agent_url)
                    
                    logger.error(f"Error adding remote agent at {agent_url}: {str(e)}", exc_info=True)
                    raise RemoteAgentError(
                        message=f"Error adding remote agent: {str(e)}",
                        agent_name=agent_url,
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
        except SupervisorAgentError:
            # Re-raise SupervisorAgentError instances
            raise
        except Exception as e:
            logger.error(f"Unexpected error adding remote agent: {str(e)}", exc_info=True)
            raise SupervisorAgentError(
                message=f"Unexpected error adding remote agent: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

        return None
        
    async def delete_remote_agent(self, agent_url: str):
        """
        Delete a remote agent from the list

        Args:
            agent_url: url of the remote agent to delete
        """
        logger.info("deleting agent %s", agent_url)
        
        if agent_url in self.remote_agent_addresses:
            self.remote_agent_addresses.remove(agent_url)
            
            # Reload the remote agents to reflect the deletion
            await self.load_remote_agents()
            
            return True
        else:
            logger.warning("Agent URL %s not found in remote_agent_addresses", agent_url)
            return False


    def list_remote_agents(self):
        """
        List the available remote agents with chat skills you can use to delegate chat tasks.

        Returns:
            list of dicts
        """
        if not self.remote_agent_connections:
            return []

        remote_agent_info = []
        for card in self.cards.values():
            if card.name == ASSURANCE_AGENT_NAME or any(
                'chat' in skill.tags for skill in card.skills
            ):
                agent_id = str(uuid4())
                remote_agent_info.append(
                    {'id': agent_id, 'name': card.name, 'description': card.description, 'url': card.url}
                )
        return remote_agent_info

    def list_all_remote_agents(self):
        """
        List all the available remote agents with chat skills you can use to delegate chat tasks.

        Returns:
            list of dicts
        """
        if not self.remote_agent_connections:
            return []

        remote_agent_info = []
        for card in self.cards.values():
            for skill in card.skills:
                # Generate a unique ID for each agent if not already present
                agent_id = str(uuid4())
                remote_agent_info.append(
                    {'id': agent_id, 'name': card.name, 'description': card.description, 'url': card.url}
                )
                break
        return remote_agent_info

    def register_socket(self, sid: str, sio: object) -> None:
        if not isinstance(sid, str) or not sid:
            raise PermissionError("invalid socket session")
        self.sio_sessions[sid] = sio

    def bind_ui_thread(self, thread_id: str, sid: str, sio: object) -> None:
        if self.sio_sessions.get(sid) is not sio:
            raise PermissionError("socket session is not registered")
        self.ui_thread_ownership.bind(thread_id, sid)

    def remove_socket(self, sid: str) -> None:
        for thread_id in self.ui_thread_ownership.remove_sid(sid):
            self._tool_result_locks.pop(thread_id, None)
        self.sio_sessions.pop(sid, None)

    def socket_target(self, thread_id: str) -> tuple[str, object]:
        sid = self.ui_thread_ownership.sid_for(thread_id)
        sio = self.sio_sessions.get(sid)
        if sio is None:
            raise PermissionError("thread owner is disconnected")
        return sid, sio

    def create_send_message_payload(
        self,
        text: str,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> dict[str, Any]:
        """Create the legacy TextPart payload used by non-Assurance agents."""

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'text', 'text': text}],
                'messageId': uuid4().hex,
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id
        if context_id is not None:
            payload['message']['contextId'] = context_id
        return payload

    def create_data_message_payload(
        self,
        data: Mapping[str, object],
        *,
        task_id: str | None = None,
        context_id: str | None = None,
        display_text: str | None = None,
    ) -> dict[str, Any]:
        """Create and locally validate one canonical Assurance Message."""

        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise A2AContentError("canonical message_id is missing")
        parts: list[dict[str, object]] = [{'kind': 'data', 'data': dict(data)}]
        if display_text is not None:
            parts.append({'kind': 'text', 'text': display_text})
        message: dict[str, object] = {
            'role': 'user',
            'parts': parts,
            'messageId': message_id,
        }
        if task_id is not None:
            message['taskId'] = task_id
        if context_id is not None:
            message['contextId'] = context_id
        decode_canonical_parts(
            message,
            expected_task_id=task_id,
            expected_context_id=context_id,
        )
        return {'message': message}

    def _state_coordinates(self, session_id: str) -> tuple[object, str, str]:
        session_manager = self.adk_agent._session_manager
        app_name = self.app_name
        metadata = self.adk_agent._get_session_metadata(session_id)
        if metadata:
            app_name = metadata['app_name']
            user_id = metadata['user_id']
        else:
            user_id = f"thread_user_{session_id}"
        return session_manager, app_name, user_id

    async def _get_session_state(self, session_id: str) -> dict[str, object]:
        manager, app_name, user_id = self._state_coordinates(session_id)
        state = await manager.get_session_state(session_id, app_name, user_id)
        return dict(state or {})

    async def updateState(
        self,
        session_id: str,
        *,
        agent_name: object = _UNSET,
        task_status: object = _UNSET,
        task_id: object = _UNSET,
        a2a_context_id: object = _UNSET,
        assurance_workflow_id: object = _UNSET,
        assurance_trace_id: object = _UNSET,
        assurance_scan_message_id: object = _UNSET,
        assurance_scan_idempotency_key: object = _UNSET,
        pending_assurance: object = _UNSET,
        trusted_assurance_decision: object = _UNSET,
    ) -> None:
        """Persist only the explicitly supplied server-side session fields."""

        values = {
            'agent': agent_name,
            'task_status': task_status,
            'task_id': task_id,
            'a2a_context_id': a2a_context_id,
            'assurance_workflow_id': assurance_workflow_id,
            'assurance_trace_id': assurance_trace_id,
            'assurance_scan_message_id': assurance_scan_message_id,
            'assurance_scan_idempotency_key': assurance_scan_idempotency_key,
            'pending_assurance': pending_assurance,
            'trusted_assurance_decision': trusted_assurance_decision,
        }
        try:
            manager, app_name, user_id = self._state_coordinates(session_id)
            for key, value in values.items():
                if value is not _UNSET:
                    success = await manager.set_state_value(
                        session_id, app_name, user_id, key, value
                    )
                    if success is not True:
                        raise RuntimeError("session state update was rejected")
            logger.info("Updated server session state thread=%s", session_id)
        except Exception as exc:
            logger.error(
                "Failed to update server session state thread=%s type=%s",
                session_id,
                type(exc).__name__,
            )
            raise SupervisorAgentError(
                message="Server session state could not be updated",
                severity=ErrorSeverity.ERROR,
                original_exception=exc,
            ) from exc

    def _build_trusted_assurance_decision(
        self,
        *,
        tool_call_id: str,
        tool_name: str | None,
        content: str,
        state: Mapping[str, object],
    ) -> dict[str, object] | None:
        if state.get('agent') != ASSURANCE_AGENT_NAME:
            return None
        if state.get('task_status') != 'input_needed':
            raise PermissionError("Assurance task is not awaiting a decision")
        pending = state.get('pending_assurance')
        if not isinstance(pending, Mapping):
            raise PermissionError("Assurance candidate challenge is missing")
        approved = parse_trusted_approval(content)
        existing = state.get('trusted_assurance_decision')
        if existing is not None:
            if not isinstance(existing, Mapping) or set(existing) != {
                'tool_call_id', 'approved', 'confirmation_data'
            }:
                raise PermissionError("Persisted Assurance decision is invalid")
            confirmation = existing.get('confirmation_data')
            if (
                existing.get('tool_call_id') != tool_call_id
                or existing.get('approved') is not approved
                or not isinstance(confirmation, Mapping)
            ):
                raise PermissionError("Persisted Assurance decision does not match")
            sent_at = confirmation.get('sent_at')
            if not isinstance(sent_at, str):
                raise PermissionError("Persisted Assurance decision is invalid")
            try:
                parsed_sent_at = datetime.datetime.fromisoformat(
                    sent_at.replace('Z', '+00:00')
                )
                expected_confirmation = build_assurance_confirmation_request(
                    pending,
                    approved=approved,
                    reason=(
                        "用户已在受信任审批组件中确认。"
                        if approved
                        else "用户已在受信任审批组件中拒绝。"
                    ),
                    sent_at=parsed_sent_at,
                    message_id=confirmation.get('message_id'),
                    idempotency_key=confirmation.get('idempotency_key'),
                )
            except (A2AContentError, TypeError, ValueError):
                raise PermissionError(
                    "Persisted Assurance decision is invalid"
                ) from None
            if dict(confirmation) != expected_confirmation:
                raise PermissionError("Persisted Assurance decision does not match")
            return {
                'tool_call_id': tool_call_id,
                'approved': approved,
                'confirmation_data': dict(confirmation),
            }
        trusted = build_trusted_assurance_decision(
            pending,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=content,
        )
        return trusted

    async def handleToolResult(
        self,
        session_id: str,
        sid: str,
        tool_call_id: str,
        content: str,
    ) -> AsyncGenerator[object, None]:
        """Authorize, persist, and continue one real AG-UI tool result."""

        lock = self._tool_result_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            self.ui_thread_ownership.require_owner(session_id, sid)
            tool_call_thread_id(tool_call_id, expected_thread_id=session_id)
            if not await self.adk_agent.is_pending_tool_call(session_id, tool_call_id):
                raise PermissionError("tool call is not pending for this thread")
            state = await self._get_session_state(session_id)
            tool_name = await self.adk_agent.pending_tool_call_name(
                session_id, tool_call_id
            )
            trusted = self._build_trusted_assurance_decision(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=content,
                state=state,
            )
            if trusted is not None:
                await self.updateState(
                    session_id,
                    trusted_assurance_decision=trusted,
                )
                logger.info(
                    "Recorded trusted Assurance decision thread=%s", session_id
                )
            continuation = self.adk_agent.handle_tool_result(
                session_id, tool_call_id, content
            )
            try:
                first_event = await anext(continuation)
            except StopAsyncIteration:
                raise RuntimeError("ADK continuation returned no events") from None
            yield first_event
            async for event in continuation:
                yield event

    async def sendApproval(self, agent_name: str, approval: str, task_id: str, context_id: str):
        """
        Send non-streaming approval to background agents from thumbs up/down in UI.

        Args:
            approval: approve/reject
            task_id: id of the task to provide input
            context_id: context id of the session with the remote agent
        """
        if agent_name == ASSURANCE_AGENT_NAME:
            logger.warning("Ignored legacy free-text Assurance approval")
            return False
        logger.info("Sending legacy notification approval agent=%s", agent_name)
        try:
            # find the remote agent with name
            if agent_name not in self.remote_agent_connections:
                logger.error(f"Agent {agent_name} not found")
                return

            # build a send request with the approval text
            payload: dict[str, Any] = {
                'message': {
                    'role': 'user',
                    'parts': [{'kind': 'text', 'text': approval }],
                    'messageId': uuid4().hex,
                },
            }
            payload['message']['taskId'] = task_id
            payload['message']['contextId'] = context_id

            params = MessageSendParams(**payload)
            request = SendMessageRequest(id=uuid4().hex, params=params)

            client = self.remote_agent_connections[agent_name]
            if not client:
                logger.error(f"no client for agent {agent_name}")
                return 

            taskStatus = await client.send_task(request)
            logger.info("Legacy notification approval completed agent=%s", agent_name)
            return taskStatus

        except Exception as e:
            logger.error(f"Unexpected error in send_task: {str(e)}", exc_info=True)


    async def send_task(self, agent_name: str, message: str, tool_context: ToolContext):
        """
        Sends a task either streaming (if supported) or non-streaming.

        This will send a message to the remote agent named agent_name.

        Args:
          agent_name: The name of the agent to send the task to.
          message: The message to send to the agent for the task.
          tool_context: The tool context this method runs in.

        Returns:
          A dictionary of JSON data from the agent.
        """

        logger.info("Routing task agent=%s", agent_name)
        state = tool_context.state
        session_id = state.get('session_id')
        if not isinstance(session_id, str) or not session_id:
            return {'status': 'Task Error', 'text': '会话标识无效。'}

        try:
            socket_target = self.socket_target(session_id)
            client = self.remote_agent_connections.get(agent_name)
            if client is None:
                error = RemoteAgentError(
                    message="Requested remote agent is unavailable",
                    agent_name=agent_name,
                    severity=ErrorSeverity.ERROR,
                )
                await send_error_message(socket_target, error)
                return {'status': 'Task Error', 'text': '远程代理当前不可用。'}

            persisted = await self._get_session_state(session_id)
            expected_scan_message_id = persisted.get('assurance_scan_message_id')
            expected_workflow_id = persisted.get('assurance_workflow_id')
            expected_trace_id = persisted.get('assurance_trace_id')
            active_agent = persisted.get('agent')
            raw_task_id = persisted.get('task_id')
            task_id = raw_task_id if isinstance(raw_task_id, str) and raw_task_id else None
            if task_id is not None and active_agent not in {None, agent_name}:
                raise PermissionError("active task belongs to another agent")

            raw_context_id = persisted.get('a2a_context_id')
            a2a_context_id = (
                raw_context_id
                if isinstance(raw_context_id, str) and raw_context_id
                else None
            )
            starting_state = (
                'input_required'
                if task_id is not None and persisted.get('task_status') == 'input_needed'
                else None
            )

            if client.is_assurance_agent:
                if task_id is None:
                    scan = build_assurance_scan_request()
                    a2a_context_id = scan.a2a_context_id
                    send_payload = self.create_data_message_payload(
                        scan.data,
                        context_id=a2a_context_id,
                        display_text="开始检查已批准导入的本地网络数据。",
                    )
                    await self.updateState(
                        session_id,
                        agent_name=agent_name,
                        task_status='running',
                        task_id=None,
                        a2a_context_id=a2a_context_id,
                        assurance_workflow_id=scan.data['workflow_id'],
                        assurance_trace_id=scan.data['trace_id'],
                        assurance_scan_message_id=scan.data['message_id'],
                        assurance_scan_idempotency_key=scan.data['idempotency_key'],
                        pending_assurance=None,
                        trusted_assurance_decision=None,
                    )
                    state['assurance_workflow_id'] = scan.data['workflow_id']
                    state['assurance_trace_id'] = scan.data['trace_id']
                    state['assurance_scan_message_id'] = scan.data['message_id']
                    state['assurance_scan_idempotency_key'] = scan.data['idempotency_key']
                    expected_scan_message_id = scan.data['message_id']
                    expected_workflow_id = scan.data['workflow_id']
                    expected_trace_id = scan.data['trace_id']
                else:
                    pending = persisted.get('pending_assurance')
                    trusted = persisted.get('trusted_assurance_decision')
                    if (
                        starting_state != 'input_required'
                        or not isinstance(pending, Mapping)
                        or not isinstance(trusted, Mapping)
                        or not isinstance(a2a_context_id, str)
                    ):
                        raise PermissionError("trusted Assurance decision is missing")
                    confirmation = trusted.get('confirmation_data')
                    if not isinstance(confirmation, Mapping):
                        raise PermissionError("trusted Assurance decision is invalid")
                    expected = build_assurance_confirmation_request(
                        pending,
                        approved=trusted.get('approved'),
                        reason=str(confirmation.get('reason', '')),
                        message_id=str(confirmation.get('message_id', '')),
                        idempotency_key=str(confirmation.get('idempotency_key', '')),
                    )
                    stable_fields = set(expected) - {'sent_at'}
                    if any(confirmation.get(key) != expected[key] for key in stable_fields):
                        raise PermissionError("trusted Assurance decision was modified")
                    send_payload = self.create_data_message_payload(
                        confirmation,
                        task_id=task_id,
                        context_id=a2a_context_id,
                        display_text="提交受信任的确认决定。",
                    )
                    await self.updateState(session_id, task_status='running')
            else:
                if a2a_context_id is None:
                    a2a_context_id = f"context-{uuid4().hex}"
                send_payload = self.create_send_message_payload(
                    text=message,
                    context_id=a2a_context_id,
                    task_id=task_id,
                )
                await self.updateState(
                    session_id,
                    agent_name=agent_name,
                    task_status='running',
                    a2a_context_id=a2a_context_id,
                )

            state['agent'] = agent_name
            state['task_status'] = 'running'
            state['a2a_context_id'] = a2a_context_id
            request = SendStreamingMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(**send_payload),
            )
            outcome = await client.send_streaming_task(
                request,
                ui_session_id=session_id,
                socket_target=socket_target,
                expected_task_id=task_id,
                expected_context_id=a2a_context_id,
                starting_state=starting_state,
            )

            if outcome.state == 'input_required':
                if client.is_assurance_agent:
                    content = outcome.content
                    if content is None or content.data.get('message_type') != 'assurance_candidate_page':
                        raise A2AContentError("Assurance candidate page is missing")
                    page = content.data
                    if (
                        page.get('request_message_id') != expected_scan_message_id
                        or page.get('workflow_id') != expected_workflow_id
                        or page.get('trace_id') != expected_trace_id
                    ):
                        raise A2AContentError("Assurance candidate correlation mismatch")
                    build_assurance_confirmation_request(
                        page,
                        approved=False,
                        reason="候选页协议校验。",
                    )
                    await self.updateState(
                        session_id,
                        agent_name=agent_name,
                        task_status='input_needed',
                        task_id=outcome.task_id,
                        a2a_context_id=outcome.context_id,
                        pending_assurance=page,
                        trusted_assurance_decision=None,
                    )
                    text = content.text or '请在审批组件中确认或拒绝该候选事件。'
                else:
                    await self.updateState(
                        session_id,
                        agent_name=agent_name,
                        task_status='input_needed',
                        task_id=outcome.task_id,
                        a2a_context_id=outcome.context_id,
                    )
                    text = outcome.text or '需要用户补充输入。'
                return {
                    'status': 'Input Required from User',
                    'text': text,
                    'require_user_input': True,
                }

            if outcome.state == 'completed':
                if client.is_assurance_agent:
                    result = outcome.content
                    if task_id is None:
                        if result is None:
                            raise A2AContentError("empty Assurance scan artifact is missing")
                        page = validate_empty_assurance_candidate_page(result.data)
                        if (
                            page.get('request_message_id') != expected_scan_message_id
                            or page.get('workflow_id') != expected_workflow_id
                            or page.get('trace_id') != expected_trace_id
                        ):
                            raise A2AContentError("empty Assurance scan correlation mismatch")
                    else:
                        confirmation = persisted.get('trusted_assurance_decision', {})
                        confirmation_data = (
                            confirmation.get('confirmation_data')
                            if isinstance(confirmation, Mapping)
                            else None
                        )
                        if (
                            result is None
                            or not isinstance(confirmation_data, Mapping)
                            or result.data.get('message_type') != 'assurance_confirmation_result'
                            or result.data.get('request_message_id') != confirmation_data.get('message_id')
                            or result.data.get('preview_message_id') != confirmation_data.get('preview_message_id')
                            or result.data.get('candidate_id') != confirmation_data.get('candidate_id')
                            or result.data.get('decision') != confirmation_data.get('decision')
                            or result.data.get('workflow_id') != confirmation_data.get('workflow_id')
                            or result.data.get('trace_id') != confirmation_data.get('trace_id')
                            or result.data.get('outcome') not in {'created', 'correlated', 'replayed', 'rejected'}
                        ):
                            raise A2AContentError("Assurance confirmation result mismatch")
                        outcome_name = result.data['outcome']
                        incident = result.data.get('incident')
                        if (
                            confirmation_data.get('decision') == 'REJECT'
                            and (outcome_name != 'rejected' or incident is not None)
                        ) or (
                            confirmation_data.get('decision') == 'CONFIRM'
                            and (outcome_name == 'rejected' or not isinstance(incident, Mapping))
                        ):
                            raise A2AContentError("Assurance confirmation outcome mismatch")
                await self.updateState(
                    session_id,
                    agent_name=None,
                    task_status=None,
                    task_id=None,
                    a2a_context_id=None,
                    assurance_workflow_id=None,
                    assurance_trace_id=None,
                    assurance_scan_message_id=None,
                    assurance_scan_idempotency_key=None,
                    pending_assurance=None,
                    trusted_assurance_decision=None,
                )
                return {
                    'status': 'Task Completed',
                    'text': outcome.text or 'Completed',
                }

            await self.updateState(session_id, task_status=outcome.state)
            return {
                'status': 'Task Failed',
                'text': outcome.text or '远程任务未成功完成。',
            }
        except SupervisorAgentError:
            return {'status': 'Task Error', 'text': '远程任务处理失败。'}
        except Exception as exc:
            logger.error(
                "Supervisor routing rejected thread=%s agent=%s type=%s",
                session_id,
                agent_name,
                type(exc).__name__,
            )
            error = SupervisorAgentError(
                message="Supervisor rejected the remote task",
                severity=ErrorSeverity.ERROR,
                original_exception=exc,
            )
            try:
                await send_error_message(self.socket_target(session_id), error)
            except Exception:
                logger.warning("Unable to send bounded routing error thread=%s", session_id)
            return {'status': 'Task Error', 'text': '任务请求未通过安全校验。'}

    # reset_conversation and create_session methods removed - ADKAgent manages sessions automatically based on thread IDs

    async def send_message(self, sio, sid, text):
        """
        Utility function to send AG-UI events back to the dashboard ui
        """
        if text != '':
            # Send AG-UI TEXT_MESSAGE_CONTENT event instead of legacy chat_message
            agui_event = {
                'type': 'TEXT_MESSAGE_CONTENT',
                'messageId': f'response-{datetime.datetime.now().timestamp()}',
                'delta': text,
                'timestamp': datetime.datetime.now().isoformat()
            }
            await sio.emit('agui_event', agui_event, room=sid)

    async def run_agui(
        self,
        input: RunAgentInput,
        *,
        sid: str,
        sio: object,
    ):
        """
        Entry point to run AG-UI protocol conversation with the host agent.
        
        Args:
            input: AG-UI RunAgentInput containing messages, tools, context, etc.
            
        Yields:
            AG-UI protocol events (TEXT_MESSAGE_START/CONTENT/END, TOOL_CALL_*, etc.)
        """
        logger.info("AG-UI input from user - thread id %s, run id %s", input.thread_id, input.run_id)
        
        try:
            self.bind_ui_thread(input.thread_id, sid, sio)
            # Use the ADKAgent wrapper to handle the AG-UI protocol
            async for event in self.adk_agent.run(input):
                logger.info(f"AG-UI event: {type(event).__name__}")
                yield event
                
        except Exception as e:
            logger.error(
                "AG-UI run failed thread=%s type=%s",
                input.thread_id,
                type(e).__name__,
            )
            # Import here to avoid circular imports
            from ag_ui.core import RunErrorEvent, EventType
            yield RunErrorEvent(
                type=EventType.RUN_ERROR,
                message="请求处理失败，请稍后重试。",
                code="AGUI_PROCESSING_ERROR"
            )
