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

from a2a.server.events import EventQueue
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.utils import new_agent_text_message, new_task
from typing_extensions import override
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from google.genai import types
from resolveragents.agent import IncidentAgent
import os
import logging
from ag_ui.core import (
    BaseEvent, EventType,
    TextMessageStartEvent, TextMessageContentEvent, TextMessageEndEvent,
    ToolCallStartEvent, ToolCallArgsEvent, ToolCallEndEvent,
    ToolCallResultEvent, StateSnapshotEvent, StateDeltaEvent,
    CustomEvent
)
from agent_library.agentmiddleware.utils import convert_to_run_agent_input
import datetime
from agent_library.trace.trace_context import TracingContext

logger = logging.getLogger(__name__)


class ResolverAgentExecutor(AgentExecutor):
    """
    Resolver Agent Executor.
    """

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        The main execution logic.
        """
        logger.info(f"New incident {context.task_id}")
        incident_data = None

        # Create a new task with the task_id provided in the SendMessageRequest
        task = new_task(context.message)
        await event_queue.enqueue_event(task)

        # Set the trace ID to the A2A context_id for cross-agent correlation
        TracingContext.set_trace_id(context.context_id)

        # check message exists
        if not context.message:
            raise Exception("no message")

        root_message = context.message.parts[0].root

        if root_message.kind == 'data':
            incident_data = root_message.data
            logger.info(incident_data)

            # Get the agent instance (already wrapped) and session service
            agent = await IncidentAgent.get_instance()

            # Get the operating procedure manual from git
            toolset = MCPToolset(
                connection_params=SseConnectionParams(url=os.getenv("TOOLS_URL")),
                tool_filter=['getIncidentOperatingProcedure']
            )
            tools = await toolset.get_tools()
            doc = await tools[0].run_async(args={}, tool_context=None)
            logger.debug(doc)
            await toolset.close()

            logger.info("adding incident data to state")
            initial_state={
                "incident_data": incident_data,
                "operating_procedures_doc": doc.content[0].text
            }

            agent_input = await convert_to_run_agent_input(task.context_id, task.id, "New Fault", initial_state)

            async for event in agent.run_agui(agent_input):
                logger.debug("ADK RUNNER EVENT")
                logger.debug(event)

            status_event = TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.completed,
                    message=new_agent_text_message(
                        "ALL FIXED NOW",
                        context.context_id,
                        task.id
                    ),
                ),
                final=True,
                context_id=context.context_id,
                task_id=task.id,
            )
            await event_queue.enqueue_event(status_event)

            return
        else:
            logger.error('no data found')
            error_event = TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.failed,
                    message=new_agent_text_message(
                        "no incident data found",
                        context.context_id,
                        task.id, # Corrected from task.task_id
                    ),
                ),
                final=True,
                context_id=context.context_id,
                task_id=task.id,
            )
            await event_queue.enqueue_event(error_event)
            return

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise Exception('cancel not supported')
