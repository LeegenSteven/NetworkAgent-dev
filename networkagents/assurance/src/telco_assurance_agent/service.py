"""Deterministic application service behind the Assurance A2A executor."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import duckdb
from telco_domain import (
    Incident,
    IncidentTrigger,
    RcaRequest,
    RcaResult,
    SensitiveDataError,
    assert_model_safe,
)
from telco_local import LocalProfile

from .protocol import (
    AssuranceAnalyzeRequest,
    AssuranceCandidatePage,
    AssuranceCandidateSummary,
    AssuranceConfirmationRequest,
    AssuranceConfirmationResult,
    AssuranceScanRequest,
    CandidateKpiSummary,
    CandidateResourceSummary,
)
from .stores import (
    DuckDbPendingConfirmationStore,
    PendingConfirmationConflictError,
    PendingConfirmationExpiredError,
    PendingConfirmationNotFoundError,
    PendingConfirmationRecord,
)


Clock = Callable[[], datetime]
AfterIncidentWriteHook = Callable[[Incident], object | Awaitable[object]]


class AssuranceServiceError(RuntimeError):
    """Safe error intended for conversion to an ``assurance_error`` part."""

    def __init__(self, error_code: str, summary_zh: str) -> None:
        super().__init__(summary_zh)
        self.error_code = error_code
        self.summary_zh = summary_zh


class AssuranceInterruption(BaseException):
    """Test-safe simulated crash after the unique Incident write.

    This deliberately bypasses ordinary executor error completion so an HTTP
    integration test can prove restart recovery from the real crash window.
    Production composition does not install the hook that raises it.
    """


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AssuranceServiceError("ASSURANCE_TIME_INVALID", "服务时间必须包含 UTC 时区。")
    return value.astimezone(UTC)


class AssuranceService:
    """Preview, confirm and analyze with one explicit Local Profile."""

    def __init__(
        self,
        profile: LocalProfile,
        pending_store: DuckDbPendingConfirmationStore,
        *,
        actor: str,
        challenge_ttl_seconds: int,
        clock: Clock | None = None,
        after_incident_write: AfterIncidentWriteHook | None = None,
    ) -> None:
        if not 1 <= challenge_ttl_seconds <= 900:
            raise ValueError("challenge_ttl_seconds must be between 1 and 900")
        if not actor.strip():
            raise ValueError("actor is required")
        try:
            assert_model_safe({"actor": actor.strip()})
        except SensitiveDataError:
            raise ValueError("Assurance actor is unsafe") from None
        self.profile = profile
        self.pending_store = pending_store
        self.actor = actor.strip()
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._after_incident_write = after_incident_write
        self._decision_lock = asyncio.Lock()

    def _now(self) -> datetime:
        return _utc(self._clock(), name="clock")

    async def _effective_window(
        self, request: AssuranceScanRequest
    ) -> tuple[datetime, datetime]:
        if request.window_start is not None and request.window_end is not None:
            return request.window_start, request.window_end
        database_path = Path(self.profile.config.database_path)
        try:
            connection = duckdb.connect(str(database_path), read_only=True)
            try:
                row = connection.execute(
                    "SELECT MIN(measurement_end), MAX(measurement_end) FROM performance"
                ).fetchone()
            finally:
                connection.close()
        except duckdb.Error:
            raise AssuranceServiceError(
                "ASSURANCE_DATA_UNAVAILABLE", "本地性能数据不可用。"
            ) from None
        if row is None or row[0] is None or row[1] is None:
            raise AssuranceServiceError(
                "ASSURANCE_DATA_EMPTY", "本地性能数据为空，无法确定扫描窗口。"
            )

        def normalize(value: datetime) -> datetime:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

        start, end = normalize(row[0]), normalize(row[1])
        if end - start > timedelta(days=31):
            raise AssuranceServiceError(
                "ASSURANCE_WINDOW_TOO_LARGE",
                "本地数据跨度超过 31 天，请提供明确的 UTC 扫描窗口。",
            )
        return start, end

    @staticmethod
    def _snapshot(
        candidates: Sequence[IncidentTrigger],
        *,
        window_start: datetime,
        window_end: datetime,
        resource_ids: Sequence[str],
    ) -> str:
        # IncidentTrigger has random transport envelope IDs.  Only the exact
        # server-owned Incident snapshots and approved scan scope are hashed.
        payload = {
            "scope": {
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "resource_ids": list(resource_ids),
            },
            "incidents": [
                item.incident.model_dump(mode="json", round_trip=True)
                for item in candidates
            ],
        }
        return _canonical_sha256(payload)

    @staticmethod
    def _candidate(trigger: IncidentTrigger) -> AssuranceCandidateSummary:
        incident = trigger.incident
        if incident.window_start is None or incident.window_end is None:
            raise AssuranceServiceError(
                "ASSURANCE_CANDIDATE_INVALID", "候选事件缺少时间窗口。"
            )
        resources = tuple(
            CandidateResourceSummary(
                resource_id=item.resource_id,
                resource_type=item.resource_type.value,
                technology=(item.technology.value if item.technology else None),
            )
            for item in incident.affected_resources
        )
        violations = tuple(
            CandidateKpiSummary(
                kpi_name=item.kpi_name,
                observed_value=item.observed_value,
                threshold_value=item.threshold_value,
                comparator=item.comparator.value,
                unit=item.unit,
                sample_count=int(item.dimensions.get("sample_count", "0")),
            )
            for item in incident.violated_kpis
        )
        return AssuranceCandidateSummary(
            candidate_id=incident.incident_id,
            title=incident.title,
            technology=incident.technology.value,
            window_start=incident.window_start,
            window_end=incident.window_end,
            affected_resources=resources,
            violated_kpis=violations,
            summary_zh=trigger.summary_zh,
        )

    @staticmethod
    def _message_fields(request: Any, *, now: datetime) -> dict[str, object]:
        message_id = uuid4().hex
        idempotency_key = uuid4().hex
        while message_id in {request.workflow_id, request.trace_id, idempotency_key}:
            message_id = uuid4().hex
        while idempotency_key in {request.workflow_id, request.trace_id, message_id}:
            idempotency_key = uuid4().hex
        return {
            "message_id": message_id,
            "workflow_id": request.workflow_id,
            "trace_id": request.trace_id,
            "idempotency_key": idempotency_key,
            "sent_at": now,
        }

    async def scan(
        self,
        request: AssuranceScanRequest,
        *,
        task_id: str,
        context_id: str,
    ) -> AssuranceCandidatePage:
        start, end = await self._effective_window(request)
        try:
            triggers = tuple(
                await self.profile.detector.scan(
                    request.trace_id,
                    workflow_id=request.workflow_id,
                    window_start=start,
                    window_end=end,
                    resource_ids=request.resource_ids,
                )
            )
        except (ValueError, RuntimeError):
            raise AssuranceServiceError(
                "ASSURANCE_SCAN_FAILED", "本地异常扫描失败，请检查窗口和资源范围。"
            ) from None
        snapshot = self._snapshot(
            triggers,
            window_start=start,
            window_end=end,
            resource_ids=request.resource_ids,
        )
        total = len(triggers)
        selected = triggers[
            request.page_offset : request.page_offset + request.page_size
        ]
        candidates = tuple(self._candidate(item) for item in selected)
        now = self._now()
        challenge_id = secrets.token_urlsafe(32) if candidates else None
        expires_at = (
            now + timedelta(seconds=self.challenge_ttl_seconds)
            if candidates
            else None
        )
        fields = self._message_fields(request, now=now)
        page = AssuranceCandidatePage(
            **fields,
            request_message_id=request.message_id,
            candidates=candidates,
            page_size=request.page_size,
            page_offset=request.page_offset,
            total_candidates=total,
            has_more=request.page_offset + len(candidates) < total,
            challenge_id=challenge_id,
            snapshot_sha256=snapshot,
            challenge_expires_at=expires_at,
            effective_window_start=start,
            effective_window_end=end,
            summary_zh=(
                f"发现 {total} 个候选事件，本页返回 {len(candidates)} 个。"
                if candidates
                else "当前范围内未发现候选事件。"
            ),
        )
        if candidates:
            assert challenge_id is not None and expires_at is not None
            await self.pending_store.create(
                PendingConfirmationRecord.create(
                    preview_message_id=page.message_id,
                    request_message_id=request.message_id,
                    task_id=task_id,
                    context_id=context_id,
                    workflow_id=request.workflow_id,
                    trace_id=request.trace_id,
                    challenge_id=challenge_id,
                    snapshot_sha256=snapshot,
                    candidate_ids=tuple(item.candidate_id for item in candidates),
                    effective_window_start=start,
                    effective_window_end=end,
                    resource_ids=request.resource_ids,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
        return page

    @staticmethod
    def _confirmation_fingerprint(
        request: AssuranceConfirmationRequest, *, task_id: str, context_id: str
    ) -> str:
        # Transport attempts may legitimately use a new message_id/sent_at.
        # Idempotency is instead bound to the durable business decision and
        # every server-owned preview binding.
        return _canonical_sha256(
            {
                "task_id": task_id,
                "context_id": context_id,
                "workflow_id": request.workflow_id,
                "trace_id": request.trace_id,
                "idempotency_key": request.idempotency_key,
                "preview_message_id": request.preview_message_id,
                "candidate_id": request.candidate_id,
                "challenge_id": request.challenge_id,
                "snapshot_sha256": request.snapshot_sha256,
                "decision": request.decision,
                "reason": request.reason,
            }
        )

    @staticmethod
    def _validate_confirmation_binding(
        record: PendingConfirmationRecord,
        request: AssuranceConfirmationRequest,
        *,
        task_id: str,
        context_id: str,
    ) -> None:
        valid = (
            record.task_id == task_id
            and record.context_id == context_id
            and record.workflow_id == request.workflow_id
            and record.trace_id == request.trace_id
            and record.preview_message_id == request.preview_message_id
            and request.candidate_id in record.candidate_ids
            and record.snapshot_sha256 == request.snapshot_sha256
            and record.challenge_matches(request.challenge_id)
        )
        if not valid:
            raise AssuranceServiceError(
                "ASSURANCE_CONFIRMATION_INVALID", "确认请求与服务端预览不匹配。"
            )

    async def confirm(
        self,
        request: AssuranceConfirmationRequest,
        *,
        task_id: str,
        context_id: str,
    ) -> AssuranceConfirmationResult:
        fingerprint = self._confirmation_fingerprint(
            request, task_id=task_id, context_id=context_id
        )
        async with self._decision_lock:
            record = await self.pending_store.get(request.preview_message_id)
            if record is None:
                raise AssuranceServiceError(
                    "ASSURANCE_CONFIRMATION_NOT_FOUND", "未找到待确认的候选事件。"
                )
            if record.state == "cancelled":
                raise AssuranceServiceError(
                    "ASSURANCE_CONFIRMATION_CANCELLED", "原始预览任务已取消。"
                )
            self._validate_confirmation_binding(
                record, request, task_id=task_id, context_id=context_id
            )
            recovery_replay = None
            allow_expired_processing = False
            if record.state == "processing" and record.expires_at <= self._now():
                if request.decision == "CONFIRM":
                    recovery_replay = (
                        await self.profile.incident_repository.find_by_idempotency_key(
                            request.candidate_id,
                            request.idempotency_key,
                            operation="create_or_correlate",
                        )
                    )
                allow_expired_processing = recovery_replay is not None
            try:
                claimed = await self.pending_store.claim(
                    request.preview_message_id,
                    fingerprint,
                    candidate_id=request.candidate_id,
                    idempotency_key=request.idempotency_key,
                    decision=request.decision,
                    now=self._now(),
                    allow_expired_processing=allow_expired_processing,
                )
            except PendingConfirmationExpiredError:
                raise AssuranceServiceError(
                    "ASSURANCE_CONFIRMATION_EXPIRED", "确认挑战已过期，请重新扫描。"
                ) from None
            except (PendingConfirmationConflictError, PendingConfirmationNotFoundError):
                raise AssuranceServiceError(
                    "ASSURANCE_CONFIRMATION_CONFLICT", "该确认已被处理或取消。"
                ) from None

            if claimed.state in {"completed", "rejected"}:
                if claimed.result_payload is None:
                    raise AssuranceServiceError(
                        "ASSURANCE_CONFIRMATION_CONFLICT", "确认结果不可恢复。"
                    )
                stored = AssuranceConfirmationResult.model_validate(
                    claimed.result_payload
                )
                if stored.decision == "REJECT":
                    return stored
                return stored.model_copy(
                    update={
                        **self._message_fields(request, now=self._now()),
                        "request_message_id": request.message_id,
                        "outcome": "replayed",
                    }
                )

            fields = self._message_fields(request, now=self._now())
            if request.decision == "REJECT":
                result = AssuranceConfirmationResult(
                    **fields,
                    request_message_id=request.message_id,
                    preview_message_id=request.preview_message_id,
                    candidate_id=request.candidate_id,
                    decision="REJECT",
                    actor=self.actor,
                    outcome="rejected",
                    incident=None,
                    summary_zh="已拒绝创建 Incident；本次操作未写入事件。",
                )
                await self.pending_store.finish(
                    request.preview_message_id,
                    fingerprint,
                    state="rejected",
                    result_payload=result.to_data_part(),
                    now=self._now(),
                )
                return result

            try:
                current = tuple(
                    await self.profile.detector.scan(
                        request.trace_id,
                        workflow_id=request.workflow_id,
                        window_start=claimed.effective_window_start,
                        window_end=claimed.effective_window_end,
                        resource_ids=claimed.resource_ids,
                    )
                )
            except (ValueError, RuntimeError):
                raise AssuranceServiceError(
                    "ASSURANCE_RESCAN_FAILED", "确认前的本地重扫失败，未写入 Incident。"
                ) from None
            current_snapshot = self._snapshot(
                current,
                window_start=claimed.effective_window_start,
                window_end=claimed.effective_window_end,
                resource_ids=claimed.resource_ids,
            )
            if current_snapshot != claimed.snapshot_sha256 or not any(
                item.incident_id == request.candidate_id for item in current
            ):
                raise AssuranceServiceError(
                    "ASSURANCE_SNAPSHOT_CHANGED",
                    "候选快照已变化，请重新扫描后再确认。",
                )

            repository = self.profile.incident_repository
            replay = recovery_replay or await repository.find_by_idempotency_key(
                request.candidate_id,
                request.idempotency_key,
                operation="create_or_correlate",
            )
            existed = await repository.get(request.candidate_id)
            try:
                incident = await self.profile.detector.confirm(
                    request.candidate_id,
                    trace_id=request.trace_id,
                    idempotency_key=request.idempotency_key,
                    actor=self.actor,
                    reason=request.reason,
                    window_start=claimed.effective_window_start,
                    window_end=claimed.effective_window_end,
                    resource_ids=claimed.resource_ids,
                )
            except (ValueError, RuntimeError):
                raise AssuranceServiceError(
                    "ASSURANCE_CONFIRM_FAILED", "候选确认失败，未完成 Incident 写入。"
                ) from None

            if replay is not None:
                outcome = "replayed"
            elif existed is not None or incident.incident_id != request.candidate_id:
                outcome = "correlated"
            else:
                outcome = "created"

            if replay is None and self._after_incident_write is not None:
                hook_result = self._after_incident_write(incident)
                if inspect.isawaitable(hook_result):
                    await cast(Awaitable[object], hook_result)

            result = AssuranceConfirmationResult(
                **fields,
                request_message_id=request.message_id,
                preview_message_id=request.preview_message_id,
                candidate_id=request.candidate_id,
                decision="CONFIRM",
                actor=self.actor,
                outcome=outcome,
                incident=incident,
                summary_zh=(
                    "已确认并创建 Incident。"
                    if outcome == "created"
                    else "已确认并关联现有 Incident。"
                    if outcome == "correlated"
                    else "已安全重放既有确认结果。"
                ),
            )
            await self.pending_store.finish(
                request.preview_message_id,
                fingerprint,
                state="completed",
                result_payload=result.to_data_part(),
                now=self._now(),
            )
            return result

    async def analyze(
        self,
        request: AssuranceAnalyzeRequest,
        *,
        task_id: str,
        context_id: str,
    ) -> RcaResult:
        del task_id, context_id
        incident = await self.profile.incident_repository.get(request.incident_id)
        if incident is None:
            raise AssuranceServiceError(
                "ASSURANCE_INCIDENT_NOT_FOUND", "未找到待分析的 Incident。"
            )
        if incident.trace_id != request.trace_id:
            raise AssuranceServiceError(
                "ASSURANCE_TRACE_MISMATCH", "分析请求与 Incident 追踪标识不匹配。"
            )
        rca_request = RcaRequest(
            message_id=uuid4().hex,
            workflow_id=request.workflow_id,
            incident_id=incident.incident_id,
            trace_id=incident.trace_id,
            idempotency_key=request.idempotency_key,
            sent_at=self._now(),
            incident=incident,
            based_on_revision=incident.revision,
            requested_report_version=request.requested_report_version,
        )
        try:
            return await self.profile.rca_gateway.analyze(rca_request)
        except (ValueError, RuntimeError):
            raise AssuranceServiceError(
                "ASSURANCE_ANALYSIS_FAILED", "本地只读根因分析失败。"
            ) from None

    async def cancel(self, *, task_id: str, context_id: str) -> bool:
        async with self._decision_lock:
            return await self.pending_store.cancel(
                task_id, context_id, now=self._now()
            )


__all__ = [
    "AfterIncidentWriteHook",
    "AssuranceInterruption",
    "AssuranceService",
    "AssuranceServiceError",
]
