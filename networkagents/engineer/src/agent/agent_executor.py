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
                "context_id": task.contextId,
                "content": event['content'],
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
        logger.info("on execute")

        config = context.configuration
        is_streaming = False            
        if config and hasattr(config, 'streaming'):
            is_streaming = bool(config.streaming)
        logger.info(f"is streaming = {is_streaming}")
        
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
                event_queue.enqueue_event(task)

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
                query_text = query_data['objective']

            logger.info("Processing continuation for task %s, with id %s", query_text, task.contextId)
            async for event in agent.stream(query_text, task.contextId):
                logger.info("in main event stream")
                logger.info(event)

                # Check if the event contains an error
                if 'error' in event:
                    error = event['error']
                    error_event = create_error_status_event(
                        error=error,
                        context_id=task.contextId,
                        task_id=task.id,
                        final=event.get('is_task_complete', False)
                    )
                    event_queue.enqueue_event(error_event)
                    
                    # If this is a critical error that should end the task
                    if error.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL] and event.get('is_task_complete', False):
                        return
                elif event['is_task_complete']:
                    event_queue.enqueue_event(
                        TaskStatusUpdateEvent(
                            status=TaskStatus(
                                state=TaskState.completed,
                                message=new_agent_text_message(
                                    event['content'],
                                    task.contextId,
                                    task.id,
                                ),
                            ),
                            final=True,
                            contextId=task.contextId,
                            taskId=task.id,
                        )
                    )
                elif event['require_user_input']:

                    # if background send event to supervisor
                    if background_task:
                        await self.send_notification(task, event)

                    # send back to user chat
                    event_queue.enqueue_event(
                        TaskStatusUpdateEvent(
                            status=TaskStatus(
                                state=TaskState.input_required,
                                message=new_agent_text_message(
                                    event['content'],
                                    task.contextId,
                                    task.id,
                                ),
                            ),
                            final=True,
                            contextId=task.contextId,
                            taskId=task.id,
                        )
                    )
                else:
                    event_queue.enqueue_event(
                        TaskStatusUpdateEvent(
                            status=TaskStatus(
                                state=TaskState.working,
                                message=new_agent_text_message(
                                    event['content'],
                                    task.contextId,
                                    task.id,
                                ),
                            ),
                            final=False,
                            contextId=task.contextId,
                            taskId=task.id,
                        )
                    )
        except EngineerAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                event_queue.enqueue_event(error_event)
            # Re-raise for the decorator to handle
            raise
        except Exception as e:
            # Convert generic exceptions to EngineerAgentError and handle
            error = EngineerAgentError(
                message=f"Error during agent streaming: {str(e)}",
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
                event_queue.enqueue_event(error_event)
            logger.error(f"Error during agent streaming: {str(e)}", exc_info=True)
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
            event_queue.enqueue_event(
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
        except EngineerAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                event_queue.enqueue_event(error_event)
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
                    context_id=task.contextId,
                    task_id=task.id,
                    final=True
                )
                event_queue.enqueue_event(error_event)
            logger.error(f"Unexpected error in cancel: {str(e)}", exc_info=True)
            # Re-raise for the decorator to handle
            raise error
