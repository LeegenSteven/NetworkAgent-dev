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
import os
import requests
from agent.network_engineer_agent import NetworkEngineerAgent
from typing_extensions import override
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import new_task, new_agent_text_message
from utils.error_handler import (
    EngineerAgentError,
    ErrorSeverity,
    create_error_status_event,
)
from agent_library.trace.trace_context import TracingContext

logger = logging.getLogger(__name__)

class EngineerAgentExecutor(AgentExecutor):
    """Engineer AgentExecutor Example."""

    async def send_notification(self, task, event):
        logger.info("Sending notification to supervisor for user input")
        supervisor_url = os.getenv("SUPERVISOR_URL", "http://127.0.0.1:9000")
        if not supervisor_url:
            logger.error("SUPERVISOR_URL environment variable not set")
        else:
            notification_url = f"{supervisor_url}/pushnotification"
            # Create the payload
            payload = {
                "name": "Network Engineer Agent",
                "state": "input_required",
                "task_id": task.id,
                "context_id": task.context_id,
                "content": event['content'],
                "input_data": task.metadata['input_data']
            }
            
            try:
                # Send the POST request
                logger.info(f"Sending notification to {notification_url}")
                response = requests.post(notification_url, json=payload)
                
                # Check if the request was successful
                if response.status_code == 200:
                    logger.info("Notification sent successfully")
                else:
                    logger.error(f"Failed to send notification. Status code: {response.status_code}")
                    logger.error(f"Response: {response.text}")
            except Exception as e:
                logger.error(f"Error sending notification: {str(e)}", exc_info=True)


    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Handler for 'message/stream' requests.
        """
        logger.debug("A2A EXECUTE")
        logger.debug(context)

        # Set the trace ID to the A2A context_id for cross-agent correlation
        TracingContext.set_trace_id(context.context_id)

        config = context.configuration
        is_streaming = False            
        if config and hasattr(config, 'streaming'):
            is_streaming = bool(config.streaming)
        
        try:
            agent = await NetworkEngineerAgent().get_instance()
            task = context.current_task

            if not context.message:
                raise EngineerAgentError(
                    message='No message provided',
                    severity=ErrorSeverity.ERROR
                )

            if not task:
                logger.info("Creating new task!!")
                task = new_task(context.message)
                await event_queue.enqueue_event(task)

            # check if this is a background task or chat based
            # if message part has text message its chat, if data its background received from another agent
            background_task=False
            query_text = ""
            query_data = None

            root_message = context.message.parts[0].root

            if root_message.kind == 'text':
                query_text = root_message.text
            elif root_message.kind == 'data':
                background_task=True
                query_data = root_message.data
                if 'objective' not in query_data or isinstance(query_data['objective'],str)==False: 
                    logger.error("no objective or non-string objective found")
                    # TODO cancel the task
                    return

                query_text = query_data['objective']

            if not task:
                logger.info("Creating new task!!")
                task = new_task(context.message)

                # if this is a background task then also attach the accommpanying details for the request
                if background_task:
                    task.metadata={"input_data": query_data}

                await event_queue.enqueue_event(task)

            logger.info("Processing continuation for task %s, with id %s", query_text, task.context_id)
            async for event in agent.stream(query_text, task.context_id):
                logger.info("in main event stream")
                logger.info(event)

                try:
                    # Check if the event contains an error
                    if 'error' in event:
                        error = event['error']
                        error_event = create_error_status_event(
                            error=error,
                            context_id=task.context_id,
                            task_id=task.id,
                            final=event.get('is_task_complete', False)
                        )
                        await event_queue.enqueue_event(error_event)
                        
                        # If this is a critical error that should end the task
                        if error.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL] and event.get('is_task_complete', False):
                            return
                    elif event['is_task_complete']:
                        await event_queue.enqueue_event(
                            TaskStatusUpdateEvent(
                                status=TaskStatus(
                                    state=TaskState.completed,
                                    message=new_agent_text_message(
                                        event['content'],
                                        task.context_id,
                                        task.id,
                                    ),
                                ),
                                final=True,
                                context_id=task.context_id,
                                taskId=task.id,
                            )
                        )
                    elif event['require_user_input']:

                        # if background send event to supervisor
                        if background_task:
                            await self.send_notification(task, event)

                        # send back to user chat
                        await event_queue.enqueue_event(
                            TaskStatusUpdateEvent(
                                status=TaskStatus(
                                    state=TaskState.input_required,
                                    message=new_agent_text_message(
                                        event['content'],
                                        task.context_id,
                                        task.id,
                                    ),
                                ),
                                final=True,
                                context_id=task.context_id,
                                taskId=task.id,
                            )
                        )
                    else:
                        await event_queue.enqueue_event(
                            TaskStatusUpdateEvent(
                                status=TaskStatus(
                                    state=TaskState.working,
                                    message=new_agent_text_message(
                                        event['content'],
                                        task.context_id,
                                        task.id,
                                    ),
                                ),
                                final=False,
                                context_id=task.context_id,
                                taskId=task.id,
                            )
                        )
                except Exception as queue_error:
                    logger.warning(f"Failed to enqueue event: {queue_error}")
                    # If we can't enqueue events, we should probably stop processing to avoid more errors
                    # Check if the error indicates the queue is closed/unavailable
                    error_str = str(queue_error).lower()
                    if "closed" in error_str or "shutdown" in error_str:
                        logger.info("Event queue appears closed, stopping execution loop.")
                        break
        except EngineerAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                try:
                    error_event = create_error_status_event(
                        error=e,
                        context_id=task.context_id,
                        task_id=task.id,
                        final=True
                    )
                    await event_queue.enqueue_event(error_event)
                except Exception as queue_error:
                    logger.warning(f"Failed to enqueue error event: {queue_error}")
            
            # Truncate message if needed before re-raising
            if len(e.message) > 1000:
                e.message = e.message[:1000] + "... [truncated]"
            raise

        except Exception as e:
            # Convert generic exceptions to EngineerAgentError and handle
            error_msg = str(e)
            if len(error_msg) > 1000:
                error_msg = error_msg[:1000] + "... [truncated]"

            error = EngineerAgentError(
                message=f"Error during agent streaming: {error_msg}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            if task:
                try:
                    error_event = create_error_status_event(
                        error=error,
                        context_id=task.context_id,
                        task_id=task.id,
                        final=True
                    )
                    await event_queue.enqueue_event(error_event)
                except Exception as queue_error:
                    logger.warning(f"Failed to enqueue error event: {queue_error}")

            logger.error(f"Error during agent streaming: {error_msg}", exc_info=True)
            # Re-raise for the decorator to handle
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
            agent = await NetworkEngineerAgent().get_instance()
            
            # Check if we have a valid task
            if not task:
                raise EngineerAgentError(
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
                            task.context_id,
                            task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    taskId=task.id,
                )
            )
        except EngineerAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            # Re-raise for the decorator to handle
            raise
        except Exception as e:
            # Convert generic exceptions to EngineerAgentError and handle
            error = EngineerAgentError(
                message=f"Unexpected error in cancel: {str(e)}",
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
            logger.error(f"Unexpected error in cancel: {str(e)}", exc_info=True)
            # Re-raise for the decorator to handle
            raise error
