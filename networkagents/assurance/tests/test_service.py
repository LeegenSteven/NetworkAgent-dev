from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from telco_assurance_agent.protocol import (
    AssuranceConfirmationRequest,
    AssuranceScanRequest,
)
from telco_assurance_agent.service import (
    AssuranceInterruption,
    AssuranceService,
    AssuranceServiceError,
)
from telco_assurance_agent.stores import (
    DuckDbPendingConfirmationStore,
    initialize_assurance_database,
)
from telco_domain import Incident, IncidentTrigger, Technology


NOW = datetime(2030, 1, 1, tzinfo=UTC)
WINDOW_START = NOW - timedelta(hours=1)


def _common(message_type: str, *, workflow_id: str, trace_id: str):
    return {
        "message_type": message_type,
        "message_id": uuid4().hex,
        "workflow_id": workflow_id,
        "trace_id": trace_id,
        "idempotency_key": uuid4().hex,
        "sent_at": NOW,
    }


class _Repository:
    def __init__(self) -> None:
        self.incidents: dict[str, Incident] = {}
        self.replays: dict[tuple[str, str], Incident] = {}

    async def get(self, incident_id: str):
        return self.incidents.get(incident_id)

    async def find_by_idempotency_key(self, incident_id, key, *, operation):
        assert operation == "create_or_correlate"
        return self.replays.get((incident_id, key))


class _Detector:
    def __init__(self, trigger: IncidentTrigger, repository: _Repository) -> None:
        self.trigger = trigger
        self.repository = repository
        self.confirm_calls = 0

    async def scan(self, trace_id, **scope):
        assert trace_id == self.trigger.trace_id
        assert scope["window_start"] == WINDOW_START
        assert scope["window_end"] == NOW
        return (self.trigger,)

    async def confirm(self, candidate_id, *, idempotency_key, **metadata):
        self.confirm_calls += 1
        incident = self.trigger.incident
        self.repository.incidents.setdefault(incident.incident_id, incident)
        self.repository.replays[(candidate_id, idempotency_key)] = incident
        return incident


def _profile() -> tuple[SimpleNamespace, _Detector]:
    workflow_id, trace_id = uuid4().hex, uuid4().hex
    incident = Incident(
        incident_id="incident-candidate-1",
        technology=Technology.LTE,
        title="本地 KPI 异常",
        description="安全摘要",
        trace_id=trace_id,
        detected_at=NOW,
        window_start=WINDOW_START,
        window_end=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    trigger = IncidentTrigger(
        message_id=uuid4().hex,
        workflow_id=workflow_id,
        incident_id=incident.incident_id,
        trace_id=trace_id,
        idempotency_key=uuid4().hex,
        sent_at=NOW,
        incident=incident,
        summary_zh="发现一个候选事件。",
    )
    repository = _Repository()
    detector = _Detector(trigger, repository)
    return (
        SimpleNamespace(
            detector=detector,
            incident_repository=repository,
            rca_gateway=SimpleNamespace(),
            config=SimpleNamespace(database_path=Path("unused.duckdb")),
        ),
        detector,
    )


def _service(tmp_path: Path):
    database = tmp_path / "assurance.duckdb"
    initialize_assurance_database(database)
    profile, detector = _profile()
    profile.config.database_path = database
    service = AssuranceService(
        profile,
        DuckDbPendingConfirmationStore(database),
        actor="assurance-service",
        challenge_ttl_seconds=600,
        clock=lambda: NOW,
    )
    return service, detector


def test_scan_is_preview_only_and_confirm_reject_is_zero_write(tmp_path: Path) -> None:
    service, detector = _service(tmp_path)
    workflow_id = detector.trigger.workflow_id
    trace_id = detector.trigger.trace_id
    scan = AssuranceScanRequest(
        **_common("assurance_scan_request", workflow_id=workflow_id, trace_id=trace_id),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=1,
        page_offset=0,
    )
    page = asyncio.run(service.scan(scan, task_id="task-1", context_id="context-1"))
    assert page.challenge_id
    assert page.candidates[0].candidate_id == "incident-candidate-1"
    assert "observations" not in page.model_dump_json()

    reject = AssuranceConfirmationRequest(
        **_common(
            "assurance_confirmation_request",
            workflow_id=workflow_id,
            trace_id=trace_id,
        ),
        preview_message_id=page.message_id,
        candidate_id=page.candidates[0].candidate_id,
        challenge_id=page.challenge_id,
        snapshot_sha256=page.snapshot_sha256,
        decision="REJECT",
        reason="用户拒绝创建事件",
    )
    result = asyncio.run(
        service.confirm(reject, task_id="task-1", context_id="context-1")
    )
    assert result.outcome == "rejected"
    assert result.incident is None
    assert detector.confirm_calls == 0


def test_confirm_rescans_exact_snapshot_and_writes_once(tmp_path: Path) -> None:
    service, detector = _service(tmp_path)
    scan = AssuranceScanRequest(
        **_common(
            "assurance_scan_request",
            workflow_id=detector.trigger.workflow_id,
            trace_id=detector.trigger.trace_id,
        ),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=1,
        page_offset=0,
    )
    page = asyncio.run(service.scan(scan, task_id="task-1", context_id="context-1"))
    request = AssuranceConfirmationRequest(
        **_common(
            "assurance_confirmation_request",
            workflow_id=scan.workflow_id,
            trace_id=scan.trace_id,
        ),
        preview_message_id=page.message_id,
        candidate_id=page.candidates[0].candidate_id,
        challenge_id=page.challenge_id,
        snapshot_sha256=page.snapshot_sha256,
        decision="CONFIRM",
        reason="用户确认创建事件",
    )
    result = asyncio.run(
        service.confirm(request, task_id="task-1", context_id="context-1")
    )
    assert result.outcome == "created"
    assert result.actor == "assurance-service"
    assert result.incident is not None
    assert detector.confirm_calls == 1


def test_crash_recovery_allows_same_business_key_with_new_transport_message(
    tmp_path: Path,
) -> None:
    service, detector = _service(tmp_path)
    scan = AssuranceScanRequest(
        **_common(
            "assurance_scan_request",
            workflow_id=detector.trigger.workflow_id,
            trace_id=detector.trigger.trace_id,
        ),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=1,
        page_offset=0,
    )
    page = asyncio.run(service.scan(scan, task_id="task-1", context_id="context-1"))
    original = AssuranceConfirmationRequest(
        **_common(
            "assurance_confirmation_request",
            workflow_id=scan.workflow_id,
            trace_id=scan.trace_id,
        ),
        preview_message_id=page.message_id,
        candidate_id=page.candidates[0].candidate_id,
        challenge_id=page.challenge_id,
        snapshot_sha256=page.snapshot_sha256,
        decision="CONFIRM",
        reason="用户确认创建事件",
    )

    def crash_after_write(_incident: Incident) -> None:
        raise AssuranceInterruption("simulated crash")

    crashed = AssuranceService(
        service.profile,
        service.pending_store,
        actor="assurance-service",
        challenge_ttl_seconds=600,
        clock=lambda: NOW,
        after_incident_write=crash_after_write,
    )
    with pytest.raises(AssuranceInterruption):
        asyncio.run(
            crashed.confirm(original, task_id="task-1", context_id="context-1")
        )

    retried = original.model_copy(
        update={"message_id": uuid4().hex, "sent_at": NOW + timedelta(seconds=1)}
    )
    result = asyncio.run(
        service.confirm(retried, task_id="task-1", context_id="context-1")
    )
    assert result.outcome == "replayed"
    assert detector.confirm_calls == 2

    changed_reason = retried.model_copy(
        update={"message_id": uuid4().hex, "reason": "改为另一条理由"}
    )
    with pytest.raises(AssuranceServiceError, match="该确认已被处理"):
        asyncio.run(
            service.confirm(
                changed_reason, task_id="task-1", context_id="context-1"
            )
        )
    assert detector.confirm_calls == 2

    changed_decision = retried.model_copy(
        update={"message_id": uuid4().hex, "decision": "REJECT"}
    )
    with pytest.raises(AssuranceServiceError, match="该确认已被处理"):
        asyncio.run(
            service.confirm(
                changed_decision, task_id="task-1", context_id="context-1"
            )
        )
    assert detector.confirm_calls == 2


def test_sensitive_actor_is_rejected_before_any_incident_write(tmp_path: Path) -> None:
    service, detector = _service(tmp_path)
    with pytest.raises(ValueError, match="actor is unsafe"):
        AssuranceService(
            service.profile,
            service.pending_store,
            actor="IMSI:310410000000001",
            challenge_ttl_seconds=600,
            clock=lambda: NOW,
        )
    assert detector.confirm_calls == 0


def test_confirm_and_cancel_are_linearized_across_store_and_service_instances(
    tmp_path: Path,
) -> None:
    first, detector = _service(tmp_path)
    second = AssuranceService(
        first.profile,
        DuckDbPendingConfirmationStore(first.profile.config.database_path),
        actor="assurance-service",
        challenge_ttl_seconds=600,
        clock=lambda: NOW,
    )

    async def scenario() -> None:
        scan = AssuranceScanRequest(
            **_common(
                "assurance_scan_request",
                workflow_id=detector.trigger.workflow_id,
                trace_id=detector.trigger.trace_id,
            ),
            window_start=WINDOW_START,
            window_end=NOW,
            page_size=1,
            page_offset=0,
        )
        page = await first.scan(scan, task_id="task-race", context_id="context-race")
        request = AssuranceConfirmationRequest(
            **_common(
                "assurance_confirmation_request",
                workflow_id=scan.workflow_id,
                trace_id=scan.trace_id,
            ),
            preview_message_id=page.message_id,
            candidate_id=page.candidates[0].candidate_id,
            challenge_id=page.challenge_id,
            snapshot_sha256=page.snapshot_sha256,
            decision="CONFIRM",
            reason="用户确认并发候选",
        )

        entered_write = asyncio.Event()
        release_write = asyncio.Event()
        original_confirm = detector.confirm

        async def blocking_confirm(*args, **kwargs):
            entered_write.set()
            await release_write.wait()
            return await original_confirm(*args, **kwargs)

        detector.confirm = blocking_confirm
        confirming = asyncio.create_task(
            first.confirm(
                request, task_id="task-race", context_id="context-race"
            )
        )
        await entered_write.wait()
        assert not await second.cancel(
            task_id="task-race", context_id="context-race"
        )
        release_write.set()
        assert (await confirming).outcome == "created"
        assert detector.confirm_calls == 1

        detector.confirm = original_confirm
        cancel_scan = scan.model_copy(
            update={
                "message_id": uuid4().hex,
                "idempotency_key": uuid4().hex,
            }
        )
        cancel_page = await first.scan(
            cancel_scan, task_id="task-cancel", context_id="context-cancel"
        )
        cancel_request = request.model_copy(
            update={
                "message_id": uuid4().hex,
                "idempotency_key": uuid4().hex,
                "preview_message_id": cancel_page.message_id,
                "challenge_id": cancel_page.challenge_id,
                "snapshot_sha256": cancel_page.snapshot_sha256,
            }
        )
        assert await second.cancel(
            task_id="task-cancel", context_id="context-cancel"
        )
        with pytest.raises(AssuranceServiceError, match="原始预览任务已取消"):
            await first.confirm(
                cancel_request,
                task_id="task-cancel",
                context_id="context-cancel",
            )
        assert detector.confirm_calls == 1

    asyncio.run(scenario())
