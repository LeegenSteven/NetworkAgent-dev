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

import logging
import datetime
from agent.agent import OrderAgent
from typing_extensions import override
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.genai import types
from a2a.utils import new_task, new_agent_text_message
from utils.error_handler import (
    OrderAgentError,
    ErrorSeverity,
    create_error_status_event
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from agent_library.trace.trace_context import TracingContext

logger = logging.getLogger(__name__)

class OrderAgentExecutor(AgentExecutor):
    """Order AgentExecutor Example."""

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        The main execution logic for the agent.
        """
        logger.info("on execute")        

        try:
            task = context.current_task            
            root_message = context.message.parts[0].root

            # Set the trace ID to the A2A context_id for cross-agent correlation
            TracingContext.set_trace_id(context.context_id)

            # check message exists
            if not context.message:
                raise OrderAgentError(
                    message='No message provided',
                    severity=ErrorSeverity.ERROR
                )

            # create a task if it doesnt exist
            if not task:
                logger.info("Creating new task!!")
                task = new_task(context.message)
                await event_queue.enqueue_event(task)

            order_data = root_message.data
            if order_data is None: 
                logger.error("no order found")
                return

            logger.info(f"Order {order_data} received")
            content = types.Content(
                role='user', parts=[types.Part.from_text(text="New order received")]
            )

            try:
                agent = await OrderAgent.get_instance()

                session = await agent.session_service.get_session(app_name="OrderAgent",user_id="agent", session_id=context.context_id)
                if session is None:
                    logger.info("creating new session")
                    session = await agent.session_service.create_session(
                        app_name="OrderAgent",
                        user_id="agent",
                        session_id=context.context_id,
                        state={
                            'order_data': order_data
                        }
                    )

                async for event in agent.runner.run_async(
                        user_id="agent", 
                        session_id=context.context_id, 
                        new_message=content
                    ):
                    logger.info("ADK RUNNER EVENT")
                    logger.info(event)

                    if event.content.parts and event.content.parts[0].text:
                        logger.info(f'** {event.author}: {event.content.parts[0].text}')

                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.completed,
                            message=new_agent_text_message(
                                "Completed order",
                                task.context_id,
                                task.id,
                            ),
                            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
                        ),
                        final=True,
                        context_id=task.context_id,
                        task_id=task.id,
                    )
                )

            except Exception as e:
                error = OrderAgentError(
                    message=f"Error during agent streaming: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
                logger.error(f"Error during agent streaming: {str(e)}", exc_info=True)
                
        except OrderAgentError as e:
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            raise
        except Exception as e:
            error = OrderAgentError(
                message=f"Unexpected error in execute: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            if task:
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            logger.error(f"Unexpected error in execute: {str(e)}", exc_info=True)
            raise error

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise Exception('cancel not supported')
