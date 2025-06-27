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
import traceback
from agent.agent import LogsAgent
from typing_extensions import override
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import new_task, new_text_artifact, new_agent_text_message
from utils.error_handler import (
    LogsAgentError,
    ErrorSeverity,
    create_error_status_event
)

logger = logging.getLogger(__name__)

class LogsAgentExecutor(AgentExecutor):
    """Logs AgentExecutor Example."""

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Handler for 'message/stream' requests.
        """
        logger.info("on execute")
        task = None
        
        try:
            agent = await LogsAgent().get_instance()

            query = context.get_user_input()
            task = context.current_task

            if not context.message:
                raise LogsAgentError(
                    message='No message provided',
                    severity=ErrorSeverity.ERROR
                )

            if not task:
                logger.info("Creating new task!!")
                task = new_task(context.message)
                await event_queue.enqueue_event(task)

            logger.info("start stream %s, with id %s", query, task.contextId)
            
            try:
                response = await agent.stream(query, task.contextId)
                logger.info(response)

                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.completed,
                            message=new_agent_text_message(
                                response,
                                task.contextId,
                                task.id,
                            ),
                        ),
                        final=True,
                        contextId=task.contextId,
                        taskId=task.id,
                    )
                )

            except Exception as e:
                # Handle any exceptions that occur during streaming
                error = LogsAgentError(
                    message=f"Error during agent streaming: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
                logger.error(f"Error during agent streaming: {str(e)}", exc_info=True)
                
        except LogsAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            # Re-raise the error
            raise
        except Exception as e:
            # Convert generic exceptions to LogsAgentError and handle
            error = LogsAgentError(
                message=f"Unexpected error in execute: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            if task:
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            logger.error(f"Unexpected error in execute: {str(e)}", exc_info=True)
            # Re-raise the error
            raise error

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """
        Handler for cancel requests.
        """
        task = context.current_task
        
        try:
            # Attempt to get the agent instance
            agent = await LogsAgent().get_instance()
            
            # Check if we have a valid task
            if not task:
                raise LogsAgentError(
                    message='Cannot cancel: No active task found',
                    severity=ErrorSeverity.WARNING
                )
            
            # Report that cancellation is not supported but we're handling it gracefully
            logger.warning(f"Cancel requested for task {task.id}, but cancellation is not fully supported")
            
            # Send a status update to inform the user
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.cancelled,
                        message=new_agent_text_message(
                            "Task cancellation requested. Note that some operations may continue in the background.",
                            task.contextId,
                            task.id,
                        ),
                    ),
                    final=True,
                    contextId=task.contextId,
                    taskId=task.id,
                )
            )
        except LogsAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            # Re-raise the error
            raise
        except Exception as e:
            # Convert generic exceptions to LogsAgentError and handle
            error = LogsAgentError(
                message=f"Unexpected error in cancel: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            if task:
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            logger.error(f"Unexpected error in cancel: {str(e)}", exc_info=True)
            # Re-raise the error
            raise error
