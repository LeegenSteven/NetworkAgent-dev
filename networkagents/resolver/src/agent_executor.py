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

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
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

logger = logging.getLogger(__name__)

class ResolverAgentExecutor(AgentExecutor):
    """Resolver Agent Executor."""

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        logger.info(f"New incident {context.task_id}")
        incident_data=None

        # create a new task with the task_id provided in the SendMessageRequest
        task = new_task(context.message)
        await event_queue.enqueue_event(task)

        if not context.message:
            raise Exception("no message")

        root_message = context.message.parts[0].root

        if root_message.kind == 'data':
            incident_data = root_message.data
            logger.info(incident_data)

            content = types.Content(
                role='user', parts=[types.Part.from_text(text="New network incident reported")]
            )

            # get the agent and session
            agent = await IncidentAgent.get_instance()
            session = await agent.session_service.get_session(app_name="IncidentSupervisorAgent", user_id="agent", session_id=context.context_id)
            if session is None:
                # get the operating procedure manual from git
                toolset=MCPToolset(
                        connection_params=SseConnectionParams(
                            url=os.getenv("TOOLS_URL")
                        ),
                        tool_filter=['getIncidentOperatingProcedure']
                    )
                tools = await toolset.get_tools()    
                doc = await tools[0].run_async(args={}, tool_context=None)
                logger.info(doc)
                await toolset.close()

                logger.info("adding incident data to state")
                logger.info(incident_data)

                session = await agent.session_service.create_session(
                    app_name="IncidentSupervisorAgent",
                    user_id="agent",
                    session_id=context.context_id,
                    state={
                        "incident_data": incident_data,
                        "operating_procedures_doc": doc.content[0].text
                        }
                )

            async for event in agent.runner.run_async(user_id="agent", session_id=context.context_id, new_message=content):
                logger.info("ADK RUNNER EVENT")
                logger.info(event)

                if event.content.parts and event.content.parts[0].text:
                    logger.info(f'** {event.author}: {event.content.parts[0].text}')

            status_event=TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.completed,
                    message=new_agent_text_message(
                        "ALL FIXED NOW",
                        context.context_id,
                        task.id
                    ),
                ),
                final=True,
                contextId=context.context_id,
                taskId=task.id,
            )
            await event_queue.enqueue_event(status_event)
            return
        else:
            logger.error('no data found')
            error_event=TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(
                            "no incident data found",
                            context.context_id,
                            task.task_id
                        ),
                    ),
                    final=True,
                    contextId=context.context_id,
                    taskId=task.id,
                )
            await event_queue.enqueue_event(error_event)
            return

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise Exception('cancel not supported')
