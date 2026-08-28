"""Pure A2A 0.3.11 executor for the deterministic Assurance service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, Role, TaskNotCancelableError, TaskState, TextPart
from a2a.utils import new_task
from a2a.utils.errors import ServerError

from .messages import (
    safe_error_message,
    structured_artifact_parts,
    structured_message,
    text_message,
)
from .protocol import (
    AssuranceAnalyzeRequest,
    AssuranceConfirmationRequest,
    AssuranceProtocolError,
    AssuranceScanRequest,
    parse_request_message,
)
from .service import AssuranceService, AssuranceServiceError


class AssuranceAgentExecutor(AgentExecutor):
    def __init__(self, service: AssuranceService) -> None:
        self.service = service

    @staticmethod
    def _sanitized_request(incoming: Message) -> Message:
        return Message(
            role=Role.user,
            message_id=uuid4().hex,
            task_id=incoming.task_id,
            context_id=incoming.context_id,
            parts=[Part(root=TextPart(text="结构化请求已拒绝。"))],
        )

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        incoming = context.message
        if incoming is None:
            raise ValueError("A2A request message is required")

        current = context.current_task
        try:
            request = parse_request_message(incoming)
        except AssuranceProtocolError:
            sanitized = self._sanitized_request(incoming)
            if current is None:
                task = new_task(sanitized)
                await event_queue.enqueue_event(task)
            else:
                task = current
                history = list(task.history or ())
                if history and history[-1].message_id == incoming.message_id:
                    history[-1] = sanitized
                task.history = history
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await updater.start_work(
                text_message(
                    "正在校验结构化保障请求。",
                    task_id=task.id,
                    context_id=task.context_id,
                )
            )
            await updater.failed(
                safe_error_message(
                    error_code="ASSURANCE_PROTOCOL_INVALID",
                    summary_zh="请求不符合结构化保障协议。",
                    task_id=task.id,
                    context_id=task.context_id,
                    now=datetime.now(UTC),
                )
            )
            return

        if current is None:
            task = new_task(incoming)
            await event_queue.enqueue_event(task)
        else:
            task = current
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work(
            text_message(
                "正在执行本地确定性保障流程。",
                task_id=task.id,
                context_id=task.context_id,
            )
        )

        try:
            if current is None:
                if isinstance(request, AssuranceConfirmationRequest):
                    raise AssuranceServiceError(
                        "ASSURANCE_CONFIRMATION_TASK_REQUIRED",
                        "确认必须续接原始预览任务。",
                    )
                if isinstance(request, AssuranceScanRequest):
                    page = await self.service.scan(
                        request, task_id=task.id, context_id=task.context_id
                    )
                    if page.candidates:
                        await updater.requires_input(
                            structured_message(
                                page,
                                page.summary_zh,
                                task_id=task.id,
                                context_id=task.context_id,
                            ),
                            final=True,
                        )
                    else:
                        await updater.add_artifact(
                            structured_artifact_parts(page, page.summary_zh),
                            artifact_id=page.message_id,
                            name="assurance-candidate-page",
                        )
                        await updater.complete(
                            text_message(
                                "本地扫描已完成，未发现候选事件。",
                                task_id=task.id,
                                context_id=task.context_id,
                            )
                        )
                    return
                assert isinstance(request, AssuranceAnalyzeRequest)
                result = await self.service.analyze(
                    request, task_id=task.id, context_id=task.context_id
                )
                await updater.add_artifact(
                    structured_artifact_parts(result, result.summary_zh),
                    artifact_id=result.message_id,
                    name="assurance-rca-result",
                )
                await updater.complete(
                    text_message(
                        "本地只读根因分析已完成。",
                        task_id=task.id,
                        context_id=task.context_id,
                    )
                )
                return

            if (
                current.status.state
                not in {TaskState.input_required, TaskState.working}
                or not isinstance(request, AssuranceConfirmationRequest)
            ):
                raise AssuranceServiceError(
                    "ASSURANCE_CONTINUATION_INVALID",
                    "该任务仅接受与预览绑定的结构化确认请求。",
                )
            result = await self.service.confirm(
                request, task_id=task.id, context_id=task.context_id
            )
            await updater.add_artifact(
                structured_artifact_parts(result, result.summary_zh),
                artifact_id=result.message_id,
                name="assurance-confirmation-result",
            )
            await updater.complete(
                text_message(
                    "保障确认流程已完成。",
                    task_id=task.id,
                    context_id=task.context_id,
                )
            )
        except AssuranceServiceError as error:
            if error.error_code == "ASSURANCE_CONFIRMATION_CANCELLED":
                await updater.cancel(
                    text_message(
                        error.summary_zh,
                        task_id=task.id,
                        context_id=task.context_id,
                    )
                )
            else:
                await updater.failed(
                    safe_error_message(
                        error_code=error.error_code,
                        summary_zh=error.summary_zh,
                        task_id=task.id,
                        context_id=task.context_id,
                        now=self.service._now(),
                    )
                )
        except Exception:
            # Deliberately non-reflective: neither request data nor exception
            # strings cross the boundary.
            await updater.failed(
                safe_error_message(
                    error_code="ASSURANCE_INTERNAL_ERROR",
                    summary_zh="保障服务发生内部错误。",
                    task_id=task.id,
                    context_id=task.context_id,
                    now=datetime.now(UTC),
                )
            )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task = context.current_task
        if task is None:
            raise ServerError(error=TaskNotCancelableError())
        cancelled = await self.service.cancel(
            task_id=task.id, context_id=task.context_id
        )
        if not cancelled:
            raise ServerError(
                error=TaskNotCancelableError(
                    message="confirmation has already started"
                )
            )
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.cancel(
            text_message(
                "保障预览任务已取消，待确认记录已失效。",
                task_id=task.id,
                context_id=task.context_id,
            )
        )


__all__ = ["AssuranceAgentExecutor"]
