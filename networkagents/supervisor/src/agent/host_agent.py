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

import httpx
from uuid import uuid4
import json
from typing import Any
import logging
import os
import agent.prompts.supervisor as prompts
from utils.k8s import get_credentials
import datetime
from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    SendStreamingMessageRequest,
    TaskState,
    MessageSendParams,
    SendMessageRequest
)
from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from google.adk import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from utils.error_handler import (
    SupervisorAgentError,
    RemoteAgentError,
    ErrorSeverity,
    send_error_message
)

from .remote_agent_connection import RemoteAgentConnections

logger = logging.getLogger(__name__)

class HostAgent:
    """
    The host agent.

    This is the agent responsible for choosing which remote agents to send
    tasks to and coordinate their work.
    """

    # static agent instance
    _instance = None

    # current session id
    _session_id = None

    @classmethod
    async def get_instance(cls):
        if HostAgent._instance is None:
            HostAgent._instance = cls()
            HostAgent._session_id = uuid4().hex
            await HostAgent._instance.load_remote_agents()
        return HostAgent._instance

    def __init__(self):
        """
        Init agent and runner
        """
        self.credentials = get_credentials()

        self.app_name = "host_network_agent"

        self.remote_agent_addresses = []
        # Check if AGENTS_URL environment variable exists and parse it
        agents_url = os.environ.get('AGENTS_URL')
        if agents_url:
            self.remote_agent_addresses = [url.strip() for url in agents_url.split(',')]

        self.cards = {}

        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        # dict with sid->sio for sending messages back to dashboard socket session
        self.sio_sessions = {}

        self.runner = Runner(
            app_name=self.app_name,
            agent=self.create_agent(),
            artifact_service=self.artifact_service,
            session_service=self.session_service,
        )

        self.host_agent = self.create_agent()

        # list of loaded remote agents
        self.agents = None

    async def load_remote_agents(self):
        """
        (Re)Load the list of agent urls
        """
        try:
            # Close any existing connections
            if hasattr(self, 'remote_agent_connections') and self.remote_agent_connections:
                for connection in self.remote_agent_connections.values():
                    # Close any httpx connections
                    if hasattr(connection, 'client') and connection.client:
                        await connection.client.aclose()
                        
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
                            logger.info(card)
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
        return Agent(
            model='gemini-2.0-flash-001',
            name=self.app_name,
            instruction=self.root_instruction,
            description=(
                'This agent orchestrates the decomposition of the user request into'
                ' tasks that can be performed by the child agents.'
            ),
            tools=[
                self.list_remote_agents,
                self.send_task,
            ],
        )

    def root_instruction(self, context: ReadonlyContext) -> str:
        """
        Build the root instruction for the host agent
        """
        current_agent = self.check_state(context)
        return prompts.supervisor_prompt.format(agents=self.agents, current_agent=current_agent['active_agent'], current_task_status=current_agent['task_status'])

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
            for skill in card.skills:
                if 'chat' in skill.tags:
                    # Generate a unique ID for each agent if not already present
                    agent_id = str(uuid4())
                    remote_agent_info.append(
                        {'id': agent_id, 'name': card.name, 'description': card.description, 'url': card.url}
                    )
                break
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

    def create_send_message_payload(self, text: str, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", text, task_id, context_id)

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'type': 'text', 'text': text}],
                'messageId': uuid4().hex,
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload


    async def updateState(self, session_id: str, agent_name: str | None = None, task_status: str | None = None, task_id: str | None = None):
        """
        Update the state of the current task, user, session.
        """

        state_changes = {}

        if agent_name is not None:
            state_changes['agent'] = agent_name

        if task_status is not None:
            state_changes['task_status'] = task_status

        if task_id is not None:
            state_changes['task_id'] = task_id

        actions_with_update = EventActions(state_delta=state_changes)
        system_event = Event(
            invocation_id=uuid4().hex,
            author="system", 
            actions=actions_with_update,
        )

        session = await self.session_service.get_session(app_name=self.app_name, user_id="agent", session_id=session_id)
        await self.session_service.append_event(session, system_event)


    async def sendApproval(self, agent_name: str, approval: str, task_id: str, context_id: str):
        """
        Send non-streaming approval to background agents from thumbs up/down in UI.

        Args:
            approval: approve/reject
            task_id: id of the task to provide input
            context_id: context id of the session with the remote agent
        """
        logger.info(f"send approval {approval} to {agent_name}")
        try:
            # find the remote agent with name
            if agent_name not in self.remote_agent_connections:
                logger.error(f"Agent {agent_name} not found")
                return

            # build a send request with the approval text
            payload: dict[str, Any] = {
                'message': {
                    'role': 'user',
                    'parts': [{'type': 'text', 'text': approval }],
                    'messageId': uuid4().hex,
                },
            }
            payload['message']['taskId'] = task_id
            payload['message']['contextId'] = context_id

            logger.info(payload)

            params = MessageSendParams(**payload)
            request = SendMessageRequest(params=params)

            client = self.remote_agent_connections[agent_name]
            if not client:
                logger.error(f"no client for agent {agent_name}")
                return 

            taskStatus = await client.send_task(request)
            logger.info(taskStatus)

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

        Yields:
          A dictionary of JSON data from the agent.
        """

        logger.info("send task %s to %s",message, agent_name)

        try:
            state = tool_context.state
            state['agent'] = agent_name
            session_id = state.get('session_id')

            if agent_name not in self.remote_agent_connections:
                error = RemoteAgentError(
                    message=f"Agent {agent_name} not found",
                    agent_name=agent_name,
                    severity=ErrorSeverity.ERROR
                )
                
                if session_id and session_id in self.sio_sessions:
                    await send_error_message(self.sio_sessions, error)
                
                return {
                        "status" : "Task Error",
                        "text": f'Agent {agent_name} not found'
                }


            client = self.remote_agent_connections[agent_name]
            if not client:
                error = RemoteAgentError(
                    message=f'Client not available for {agent_name}',
                    agent_name=agent_name,
                    severity=ErrorSeverity.ERROR
                )
                
                if session_id and session_id in self.sio_sessions:
                    await send_error_message(self.sio_sessions, error)
                
                return {
                        "status" : "Task Error",
                        "text": f'Client not available for {agent_name}'
                }

            task_id = None
            if state['task_id'] != 'None':
                task_id = state['task_id']

            state['task_status'] = "running"

            # create message payload with ids
            send_payload = self.create_send_message_payload(
                text=message,
                context_id=session_id,
                task_id=task_id
            )

            request = SendStreamingMessageRequest(
                params=MessageSendParams(**send_payload)
            )

            try:
                taskStatus = await client.send_streaming_task(request, session_id, self.sio_sessions)
                logger.info("TASK STATUS FROM CLIENT SEND")
                logger.info(taskStatus)

                if taskStatus is not None:
                    # if task is waiting for info update the state
                    if taskStatus.state == TaskState.input_required:
                        await self.updateState(session_id=session_id,task_status="input_needed")
                        return{
                            "status" : "Input Required from User",
                            "text": taskStatus.message.parts[0].root.text
                        }
                    elif taskStatus.state == TaskState.completed:
                        # task is done so remove the agent from the state and reset back to "None"
                        await self.updateState(session_id=session_id,agent_name="None", task_status="None")
                        return {
                                "status" : "Task Completed",
                                "text": taskStatus.message.parts[0].root.text
                        }
                    elif taskStatus.state == TaskState.failed:
                        # Task failed, update state and return error
                        await self.updateState(session_id=session_id,task_status="failed")
                        error_message = "Task failed"
                        if taskStatus.message and taskStatus.message.parts and taskStatus.message.parts[0].root.text:
                            error_message = taskStatus.message.parts[0].root.text
                        
                        return {
                                "status" : "Task Failed",
                                "text": error_message
                        }
                    else:
                        logger.error("Unexpected task state found")
                        return {
                                "status" : "Task Error",
                                "text": f"Unexpected task state: {taskStatus.state}"
                        }
                else:
                    return {
                            "status" : "Task Completed",
                            "text": "Completed"
                    }
            except SupervisorAgentError as e:
                # SupervisorAgentError instances are already handled by the remote_agent_connection
                return {
                        "status" : "Task Error",
                        "text": e.message
                }
        except Exception as e:
            logger.error(f"Unexpected error in send_task: {str(e)}", exc_info=True)
            error = SupervisorAgentError(
                message=f"Unexpected error in send_task: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            
            # Try to get the session ID to send the error message
            try:
                state = tool_context.state
                session_id = state.get('session_id')
                
                if session_id and session_id in self.sio_sessions:
                    await send_error_message(self.sio_sessions, error)
            except:
                # If we can't get the session ID, just log the error
                pass
            
            return {
                    "status" : "Task Error",
                    "text": f"Unexpected error: {str(e)}"
            }

    async def reset_conversation(self):
        """
        Remove any agent state and add a new conversation to in memory session
        """
        logger.info("Reset conversation")
        self.session_service.delete_session(app_name=self.app_name, user_id="agent", session_id=HostAgent._session_id)
        await self.create_session()

    async def create_session(self):
        """
        Create a new session using the socket id as the session id
        """
        # regenerate the session id
        HostAgent._session_id = uuid4().hex

        # create new session
        session = await self.session_service.create_session(
            app_name=self.app_name, 
            user_id="agent",
            session_id=HostAgent._session_id,
            state={"agent": "Supervisor", 
                   "task_status": "None", 
                   "task_id": "None", 
                   "session_id": HostAgent._session_id}
        )
        return session

    async def send_message(self, sio, sid, text):
        """
        Utility function to send a socketio message back to the dashboard ui
        """
        if text !='':
            response = {
                'id': f'response-{datetime.datetime.now().timestamp()}',
                'text': text,
                'source': "",
                'isUser': False,
                'timestamp': datetime.datetime.now().isoformat()
            }
            await sio.emit('chat_message', response, room=sid)

    async def run(self, input, sio, sid):
        """
        Entry point to run a conversation between a user and host agent. 
        """
        logger.info("input from user %s - session id %s", input, sid)

        try:
            content = types.Content(
                role='user', parts=[types.Part.from_text(text=input)]
            )

            # get the current conversation session
            session = await self.session_service.get_session(app_name=self.app_name, user_id="agent", session_id=HostAgent._session_id)
            if session is None:
                session = await self.create_session()

            logger.info('** User says:', content.model_dump(exclude_none=True))

            try:

                async for event in self.runner.run_async(user_id="agent", session_id=session.id, new_message=content):
                    logger.info("ADK RUNNER EVENT")
                    logger.info(event)

                    if event.content.parts and event.content.parts[0].text:
                        logger.info(f'** {event.author}: {event.content.parts[0].text}')  
                        await self.send_message(sio, sid, event.content.parts[0].text)

            except Exception as e:
                logger.error(f"Error in ADK runner: {str(e)}", exc_info=True)
                error = SupervisorAgentError(
                    message=f"Error processing your request: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(self.sio_sessions, error)
        except Exception as e:
            logger.error(f"Unexpected error in run: {str(e)}", exc_info=True)
            error = SupervisorAgentError(
                message=f"Unexpected error: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            await send_error_message(self.sio_sessions, error)
