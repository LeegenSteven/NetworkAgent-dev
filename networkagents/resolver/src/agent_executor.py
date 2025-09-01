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

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.submitted,
                    message=new_agent_text_message(
                        "Task received and acknowledged",
                        context.context_id,
                        task.id,
                    ),
                ),
                final=True,
                contextId=context.context_id,
                taskId=task.id,
            )
        )

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise Exception('cancel not supported')
