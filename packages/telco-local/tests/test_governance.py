"""Closed-loop governance tests for the strictly local simulation profile."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from telco_domain import (
    ActionGateway,
    ApprovalGateway,
    ApprovalStatus,
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentStatus,
    InMemoryIncidentRepository,
    RcaConclusion,
    RcaReport,
    RcaResult,
    ResourceReference,
    ResourceType,
    VerificationGateway,
)
from telco_domain.models import ReportStatus
from telco_local.governance import (
    GovernanceAuthorizationError,
    GovernanceIdempotencyConflictError,
    GovernanceStateError,
    LocalGovernanceEngine,
)


NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def test_governance_api_is_exported_from_telco_local() -> None:
    from telco_local import (
        LOCAL_SIMULATION_ACTION_TYPE,
        LocalGovernanceEngine as ExportedEngine,
        SimulatedActionGateway,
    )

    assert ExportedEngine is LocalGovernanceEngine
    assert LOCAL_SIMULATION_ACTION_TYPE == "LOCAL_SIMULATION"
    assert SimulatedActionGateway.__module__ == "telco_local.governance"


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class RcaGatewayStub:
    def __init__(self, *, conclusive: bool = True) -> None:
        self.conclusive = conclusive
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        report = RcaReport(
            report_id=f"upstream-report-{request.incident_id}",
            incident_id=request.incident_id,
            version=request.requested_report_version,
            status=ReportStatus.PROPOSED,
            title="Local deterministic RCA",
            summary="A local, evidence-bound RCA result.",
            hypotheses=("A deterministic local hypothesis",),
            root_cause=(
                "A deterministic local root cause" if self.conclusive else None
            ),
            conclusion=(
                RcaConclusion.CONCLUSIVE
                if self.conclusive
                else RcaConclusion.INCONCLUSIVE
            ),
            evidence_refs=(
                EvidenceReference(
                    evidence_id=f"evidence-{request.incident_id}",
                    evidence_type=EvidenceType.METRIC,
                    uri=f"local-evidence://{request.incident_id}/metric",
                    source="local-test",
                    collected_at=NOW,
                ),
            ),
            recommendations=(),
            generated_by="deterministic-local-test/1.0",
            created_at=NOW,
        )
        return RcaResult(
            message_id=f"rca-result-{request.incident_id}",
            workflow_id=request.workflow_id,
            incident_id=request.incident_id,
            trace_id=request.trace_id,
            idempotency_key=f"rca-result-key-{request.incident_id}",
            sent_at=NOW,
            request_message_id=request.message_id,
            report=report,
            based_on_revision=request.based_on_revision,
            requested_report_version=request.requested_report_version,
        )


def _incident(incident_id: str = "incident-local-governance") -> Incident:
    resource = ResourceReference(
        resource_id="cell-local-1",
        resource_type=ResourceType.CELL,
    )
    return Incident(
        incident_id=incident_id,
        trace_id=f"trace-{incident_id}",
        title="Local KPI degradation",
        affected_resources=(resource,),
        detected_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


async def _seed(repository, incident: Incident | None = None) -> Incident:
    candidate = incident or _incident()
    return await repository.create(
        candidate,
        idempotency_key=f"create-{candidate.incident_id}",
        actor="local-test",
        reason="Seed a local test incident",
        trace_id=candidate.trace_id,
    )


async def _prepared(
    *,
    clock: MutableClock | None = None,
    conclusive: bool = True,
    incident_id: str = "incident-local-governance",
):
    trusted_clock = clock or MutableClock()
    repository = InMemoryIncidentRepository(clock=trusted_clock)
    gateway = RcaGatewayStub(conclusive=conclusive)
    await _seed(repository, _incident(incident_id))
    engine = LocalGovernanceEngine(repository, gateway, clock=trusted_clock)
    result = await engine.prepare(
        incident_id,
        idempotency_key="prepare-root-1",
        approval_ttl=timedelta(minutes=10),
    )
    return engine, repository, gateway, result


def test_happy_path_is_explicit_scoped_and_exactly_once() -> None:
    async def scenario() -> None:
        engine, repository, gateway, prepared = await _prepared()

        assert isinstance(engine.approval_gateway, ApprovalGateway)
        assert isinstance(engine.action_gateway, ActionGateway)
        assert isinstance(engine.verification_gateway, VerificationGateway)
        assert prepared.incident.status is IncidentStatus.AWAITING_APPROVAL
        assert prepared.awaiting_approval is True
        assert prepared.action is not None
        assert prepared.action.action_type == "LOCAL_SIMULATION"
        assert prepared.action.parameters == {
            "scenario": "local-governance-recovery",
            "version": "1.0",
        }
        assert prepared.action.reversible is True
        assert prepared.action.requires_approval is True
        assert prepared.report is not None
        assert prepared.report.report_id.startswith("local-simulation-report-")
        assert prepared.approval is not None
        assert prepared.approval.status is ApprovalStatus.PENDING
        assert prepared.approval.sequence == 0
        assert prepared.approval.action_hash == prepared.action.action_hash
        assert prepared.approval.scope == prepared.action.target_resources
        assert gateway.calls == 1

        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-1",
            reason="Approved for the isolated local simulation",
            idempotency_key="decision-root-1",
        )
        assert decided.incident.status is IncidentStatus.REMEDIATING
        assert decided.approval is not None
        assert decided.approval.status is ApprovalStatus.APPROVED
        assert decided.approval.sequence == 1

        completed = await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-root-1",
            verification_passed=True,
        )
        assert completed.incident.status is IncidentStatus.RESOLVED
        assert len(completed.action_runs) == 1
        assert completed.action_runs[0].action_hash == prepared.action.action_hash
        assert completed.action_runs[0].metadata == {
            "mode": "simulation",
            "side_effects": False,
        }
        assert completed.verification is not None
        assert completed.verification.status.value == "PASSED"

        history_before = await repository.history(completed.incident.incident_id)
        replay_prepare = await engine.prepare(
            completed.incident.incident_id,
            idempotency_key="prepare-root-1",
            approval_ttl=timedelta(minutes=10),
        )
        replay_decision = await engine.decide(
            completed.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-1",
            reason="Approved for the isolated local simulation",
            idempotency_key="decision-root-1",
        )
        replay_execute = await engine.execute(
            completed.incident.incident_id,
            idempotency_key="execute-root-1",
            verification_passed=True,
        )
        history_after = await repository.history(completed.incident.incident_id)

        assert replay_prepare.replayed is True
        assert replay_decision.replayed is True
        assert replay_execute.replayed is True
        assert replay_execute.incident == completed.incident
        assert history_after == history_before
        assert len(replay_execute.incident.action_runs) == 1
        assert len(replay_execute.incident.verification_runs) == 1

    asyncio.run(scenario())


def test_rejection_is_terminal_and_produces_zero_actions() -> None:
    async def scenario() -> None:
        engine, repository, _, prepared = await _prepared(
            incident_id="incident-rejected"
        )
        assert prepared.action is not None

        rejected = await engine.decide(
            prepared.incident.incident_id,
            approve=False,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-2",
            reason="Simulation not authorized",
            idempotency_key="decision-reject",
        )
        assert rejected.incident.status is IncidentStatus.REJECTED
        assert rejected.approval is not None
        assert rejected.approval.status is ApprovalStatus.REJECTED
        assert rejected.incident.action_runs == ()
        before = await repository.history(rejected.incident.incident_id)

        with pytest.raises(GovernanceStateError):
            await engine.execute(
                rejected.incident.incident_id,
                idempotency_key="execute-after-reject",
            )
        assert await repository.history(rejected.incident.incident_id) == before

    asyncio.run(scenario())


def test_expired_approval_fails_closed_with_zero_actions() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        engine, repository, _, prepared = await _prepared(
            clock=clock,
            incident_id="incident-expired",
        )
        assert prepared.action is not None
        clock.value += timedelta(minutes=11)

        expired = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-3",
            reason="This late decision must not authorize execution",
            idempotency_key="decision-expired",
        )
        assert expired.incident.status is IncidentStatus.REJECTED
        assert expired.approval is not None
        assert expired.approval.status is ApprovalStatus.EXPIRED
        assert expired.incident.action_runs == ()

        with pytest.raises(GovernanceStateError):
            await engine.execute(
                expired.incident.incident_id,
                idempotency_key="execute-expired",
            )
        assert (await repository.get(expired.incident.incident_id)).action_runs == ()

    asyncio.run(scenario())


def test_expired_approved_grant_closes_remediating_as_failed() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        engine, repository, _, prepared = await _prepared(
            clock=clock,
            incident_id="incident-expired-before-execute",
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expiry-check",
            reason="Approve only within the bounded local window",
            idempotency_key="decision-before-expiry",
        )
        assert decided.incident.status is IncidentStatus.REMEDIATING
        history_before = await repository.history(decided.incident.incident_id)
        clock.value += timedelta(minutes=11)

        failed = await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-after-approval-expiry",
        )
        persisted = await repository.get(decided.incident.incident_id)
        assert persisted is not None
        assert failed.incident.status is IncidentStatus.FAILED
        assert persisted.status is IncidentStatus.FAILED
        assert persisted.revision == decided.incident.revision + 1
        assert persisted.action_runs == ()
        assert persisted.verification_runs == ()
        history_after = await repository.history(decided.incident.incident_id)
        assert len(history_after) == len(history_before) + 1
        assert history_after[-1].from_status is IncidentStatus.REMEDIATING
        assert history_after[-1].to_status is IncidentStatus.FAILED
        assert history_after[-1].reason == (
            "Fail local simulation because its approval is no longer effective"
        )
        failure = persisted.model_metadata["local_governance_execution_failure"]
        assert failure["code"] == "APPROVAL_NO_LONGER_EFFECTIVE"
        assert failure["side_effects"] is False

    asyncio.run(scenario())


def test_expired_execution_exact_replay_is_read_only() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        engine, repository, _, prepared = await _prepared(
            clock=clock,
            incident_id="incident-expired-replay",
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expired-replay",
            reason="Bounded approval",
            idempotency_key="decision-expired-replay",
        )
        clock.value += timedelta(minutes=11)
        first = await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-expired-replay",
            actor="simulator-expired-replay",
            verification_passed=True,
        )
        history = await repository.history(decided.incident.incident_id)

        replay = await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-expired-replay",
            actor="simulator-expired-replay",
            verification_passed=True,
        )
        assert replay.replayed is True
        assert replay.incident == first.incident
        assert await repository.history(decided.incident.incident_id) == history
        assert replay.incident.action_runs == ()
        assert replay.incident.verification_runs == ()

    asyncio.run(scenario())


def test_expired_execution_changed_fingerprint_is_conflict() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        engine, repository, _, prepared = await _prepared(
            clock=clock,
            incident_id="incident-expired-conflict",
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expired-conflict",
            reason="Bounded approval",
            idempotency_key="decision-expired-conflict",
        )
        clock.value += timedelta(minutes=11)
        await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-expired-conflict",
            actor="simulator-expired-conflict",
            verification_passed=True,
        )
        history = await repository.history(decided.incident.incident_id)

        with pytest.raises(GovernanceIdempotencyConflictError):
            await engine.execute(
                decided.incident.incident_id,
                idempotency_key="execute-expired-conflict",
                actor="simulator-expired-conflict",
                verification_passed=False,
            )
        assert await repository.history(decided.incident.incident_id) == history

    asyncio.run(scenario())


def test_expired_execution_other_root_is_state_error() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        engine, repository, _, prepared = await _prepared(
            clock=clock,
            incident_id="incident-expired-other-root",
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expired-other-root",
            reason="Bounded approval",
            idempotency_key="decision-expired-other-root",
        )
        clock.value += timedelta(minutes=11)
        await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-expired-original-root",
        )
        history = await repository.history(decided.incident.incident_id)

        with pytest.raises(GovernanceStateError, match="another execution request"):
            await engine.execute(
                decided.incident.incident_id,
                idempotency_key="execute-expired-another-root",
            )
        assert await repository.history(decided.incident.incident_id) == history

    asyncio.run(scenario())


def test_unrelated_failed_incident_is_not_treated_as_execution_replay() -> None:
    async def scenario() -> None:
        engine, repository, _, prepared = await _prepared(
            incident_id="incident-unrelated-failed"
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-unrelated-failed",
            reason="Approve simulation",
            idempotency_key="decision-unrelated-failed",
        )
        unrelated = await repository.transition(
            decided.incident.incident_id,
            IncidentStatus.FAILED,
            expected_revision=decided.incident.revision,
            idempotency_key="external-failure",
            actor="external-local-test",
            reason="Unrelated local workflow failure",
            trace_id=decided.incident.trace_id,
        )
        history = await repository.history(unrelated.incident_id)

        with pytest.raises(GovernanceStateError, match="execution failure binding"):
            await engine.prepare(
                unrelated.incident_id,
                idempotency_key="prepare-root-1",
                approval_ttl=timedelta(minutes=10),
            )
        with pytest.raises(GovernanceStateError, match="not produced"):
            await engine.execute(
                unrelated.incident_id,
                idempotency_key="execute-unrelated-failed",
            )
        assert await repository.history(unrelated.incident_id) == history

    asyncio.run(scenario())


def test_expired_commit_response_loss_replays_full_cli_chain_read_only() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        inner = InMemoryIncidentRepository(clock=clock)
        await _seed(inner, _incident("incident-expired-full-chain"))
        gateway = RcaGatewayStub()
        unreliable = CommitThenRaiseRepository(inner, IncidentStatus.FAILED)
        engine = LocalGovernanceEngine(unreliable, gateway, clock=clock)
        prepared = await engine.prepare(
            "incident-expired-full-chain",
            idempotency_key="prepare-expired-full-chain",
            actor="governance-full-chain",
            approval_ttl=timedelta(minutes=10),
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expired-full-chain",
            reason="Bounded approval for full-chain replay",
            idempotency_key="decision-expired-full-chain",
        )
        clock.value += timedelta(minutes=11)
        with pytest.raises(RuntimeError, match="response loss"):
            await engine.execute(
                decided.incident.incident_id,
                idempotency_key="execute-expired-full-chain",
                actor="simulator-expired-full-chain",
                verification_passed=True,
            )
        failed = await inner.get(decided.incident.incident_id)
        assert failed is not None
        assert failed.status is IncidentStatus.FAILED
        history = await inner.history(failed.incident_id)

        replay_prepare = await engine.prepare(
            failed.incident_id,
            idempotency_key="prepare-expired-full-chain",
            actor="governance-full-chain",
            approval_ttl=timedelta(minutes=10),
        )
        replay_decide = await engine.decide(
            failed.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expired-full-chain",
            reason="Bounded approval for full-chain replay",
            idempotency_key="decision-expired-full-chain",
        )
        replay_execute = await engine.execute(
            failed.incident_id,
            idempotency_key="execute-expired-full-chain",
            actor="simulator-expired-full-chain",
            verification_passed=True,
        )

        assert replay_prepare.replayed is True
        assert replay_decide.replayed is True
        assert replay_execute.replayed is True
        assert replay_prepare.incident == failed
        assert replay_decide.incident == failed
        assert replay_execute.incident == failed
        assert await inner.history(failed.incident_id) == history
        assert failed.action_runs == ()
        assert failed.verification_runs == ()

    asyncio.run(scenario())


def test_expired_execution_recovers_after_failed_commit_response_loss() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        base_engine, inner, gateway, prepared = await _prepared(
            clock=clock,
            incident_id="incident-expired-response-loss",
        )
        assert prepared.action is not None
        decided = await base_engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-expired-response-loss",
            reason="Bounded approval",
            idempotency_key="decision-expired-response-loss",
        )
        clock.value += timedelta(minutes=11)
        unreliable = CommitThenRaiseRepository(inner, IncidentStatus.FAILED)
        engine = LocalGovernanceEngine(unreliable, gateway, clock=clock)

        with pytest.raises(RuntimeError, match="response loss"):
            await engine.execute(
                decided.incident.incident_id,
                idempotency_key="execute-expired-response-loss",
            )
        history = await inner.history(decided.incident.incident_id)
        resumed = await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-expired-response-loss",
        )
        assert resumed.replayed is True
        assert resumed.incident.status is IncidentStatus.FAILED
        assert resumed.incident.action_runs == ()
        assert resumed.incident.verification_runs == ()
        assert await inner.history(decided.incident.incident_id) == history

    asyncio.run(scenario())


def test_failed_verification_reopens_without_real_side_effects() -> None:
    async def scenario() -> None:
        engine, _, _, prepared = await _prepared(
            incident_id="incident-reopened"
        )
        assert prepared.action is not None
        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-4",
            reason="Approve simulation",
            idempotency_key="decision-reopen",
        )

        reopened = await engine.execute(
            decided.incident.incident_id,
            idempotency_key="execute-reopen",
            verification_passed=False,
        )
        assert reopened.incident.status is IncidentStatus.REOPENED
        assert reopened.verification is not None
        assert reopened.verification.status.value == "FAILED"
        assert reopened.verification.error == "Local simulated verification failed"
        assert all(
            run.metadata.get("side_effects") is False
            for run in reopened.action_runs
        )

    asyncio.run(scenario())


def test_stale_revision_wrong_hash_and_changed_replay_payload_write_nothing() -> None:
    async def scenario() -> None:
        engine, repository, _, prepared = await _prepared(
            incident_id="incident-guarded"
        )
        assert prepared.action is not None
        history = await repository.history(prepared.incident.incident_id)

        with pytest.raises(GovernanceAuthorizationError):
            await engine.decide(
                prepared.incident.incident_id,
                approve=True,
                expected_action_hash="0" * 64,
                expected_revision=prepared.incident.revision,
                actor="operator-5",
                reason="Wrong preview",
                idempotency_key="decision-guarded",
            )
        with pytest.raises(GovernanceStateError, match="revision"):
            await engine.decide(
                prepared.incident.incident_id,
                approve=True,
                expected_action_hash=prepared.action.action_hash,
                expected_revision=prepared.incident.revision - 1,
                actor="operator-5",
                reason="Stale preview",
                idempotency_key="decision-guarded-stale",
            )
        assert await repository.history(prepared.incident.incident_id) == history

        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-5",
            reason="Exact preview",
            idempotency_key="decision-exact",
        )
        before_conflict = await repository.history(prepared.incident.incident_id)
        with pytest.raises(GovernanceIdempotencyConflictError):
            await engine.decide(
                prepared.incident.incident_id,
                approve=False,
                expected_action_hash=prepared.action.action_hash,
                expected_revision=prepared.incident.revision,
                actor="operator-5",
                reason="Changed retry",
                idempotency_key="decision-exact",
            )
        assert await repository.history(decided.incident.incident_id) == before_conflict

    asyncio.run(scenario())


def test_string_booleans_are_rejected_before_any_governance_write() -> None:
    async def scenario() -> None:
        engine, repository, _, prepared = await _prepared(
            incident_id="incident-strict-bools"
        )
        assert prepared.action is not None
        before_decision = await repository.history(prepared.incident.incident_id)
        with pytest.raises(ValueError, match="approve must be a boolean"):
            await engine.decide(
                prepared.incident.incident_id,
                approve="false",  # type: ignore[arg-type]
                expected_action_hash=prepared.action.action_hash,
                expected_revision=prepared.incident.revision,
                actor="operator-strict",
                reason="must not coerce a string",
                idempotency_key="decision-strict-bool",
            )
        assert await repository.history(prepared.incident.incident_id) == before_decision

        decided = await engine.decide(
            prepared.incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-strict",
            reason="exact boolean approval",
            idempotency_key="decision-strict-bool-valid",
        )
        before_execute = await repository.history(decided.incident.incident_id)
        with pytest.raises(ValueError, match="verification_passed must be a boolean"):
            await engine.execute(
                decided.incident.incident_id,
                idempotency_key="execute-strict-bool",
                verification_passed="false",  # type: ignore[arg-type]
            )
        assert await repository.history(decided.incident.incident_id) == before_execute
        assert decided.incident.action_runs == ()

    asyncio.run(scenario())


def test_prepare_changed_actor_after_partial_commit_is_an_idempotency_conflict() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        inner = InMemoryIncidentRepository(clock=clock)
        await _seed(inner, _incident("incident-actor-binding"))
        gateway = RcaGatewayStub()
        unreliable = CommitThenRaiseRepository(inner, IncidentStatus.TRIAGED)
        engine = LocalGovernanceEngine(unreliable, gateway, clock=clock)

        with pytest.raises(RuntimeError, match="response loss"):
            await engine.prepare(
                "incident-actor-binding",
                idempotency_key="prepare-actor-bound",
                actor="actor-a",
            )
        before = await inner.history("incident-actor-binding")
        with pytest.raises(GovernanceIdempotencyConflictError):
            await engine.prepare(
                "incident-actor-binding",
                idempotency_key="prepare-actor-bound",
                actor="actor-b",
            )
        assert await inner.history("incident-actor-binding") == before

        resumed = await engine.prepare(
            "incident-actor-binding",
            idempotency_key="prepare-actor-bound",
            actor="actor-a",
        )
        assert resumed.incident.status is IncidentStatus.AWAITING_APPROVAL

    asyncio.run(scenario())


def test_inconclusive_rca_stops_before_approval_and_is_replay_safe() -> None:
    async def scenario() -> None:
        engine, repository, gateway, result = await _prepared(
            conclusive=False,
            incident_id="incident-inconclusive",
        )
        assert result.incident.status is IncidentStatus.RCA_COMPLETE
        assert result.awaiting_approval is False
        assert result.action is None
        assert result.incident.approvals == ()
        history = await repository.history(result.incident.incident_id)

        replay = await engine.prepare(
            result.incident.incident_id,
            idempotency_key="prepare-root-1",
            approval_ttl=timedelta(minutes=10),
        )
        assert replay.replayed is True
        assert gateway.calls == 1
        assert await repository.history(result.incident.incident_id) == history

        with pytest.raises(GovernanceStateError):
            await engine.decide(
                result.incident.incident_id,
                approve=True,
                expected_action_hash="0" * 64,
                expected_revision=result.incident.revision,
                actor="operator-6",
                reason="Must not approve inconclusive RCA",
                idempotency_key="decision-inconclusive",
            )

    asyncio.run(scenario())


class CommitThenRaiseRepository:
    """Simulate response loss after one atomic repository commit."""

    def __init__(self, inner, fail_target: IncidentStatus) -> None:
        self.inner = inner
        self.fail_target = fail_target
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def transition(self, incident_id, target_status, **kwargs):
        result = await self.inner.transition(incident_id, target_status, **kwargs)
        if target_status is self.fail_target and not self.failed:
            self.failed = True
            raise RuntimeError("simulated response loss after commit")
        return result


def test_prepare_and_execute_resume_after_committed_response_loss() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        inner = InMemoryIncidentRepository(clock=clock)
        await _seed(inner, _incident("incident-resume"))
        gateway = RcaGatewayStub()
        unreliable = CommitThenRaiseRepository(inner, IncidentStatus.RCA_COMPLETE)
        engine = LocalGovernanceEngine(unreliable, gateway, clock=clock)

        with pytest.raises(RuntimeError, match="response loss"):
            await engine.prepare(
                "incident-resume",
                idempotency_key="prepare-resume",
            )
        prepared = await engine.prepare(
            "incident-resume",
            idempotency_key="prepare-resume",
        )
        assert prepared.incident.status is IncidentStatus.AWAITING_APPROVAL
        assert gateway.calls == 1
        assert prepared.action is not None

        decided = await engine.decide(
            "incident-resume",
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="operator-resume",
            reason="Resume test approval",
            idempotency_key="decision-resume",
        )
        unreliable.fail_target = IncidentStatus.VERIFYING
        unreliable.failed = False
        with pytest.raises(RuntimeError, match="response loss"):
            await engine.execute(
                "incident-resume",
                idempotency_key="execute-resume",
            )
        completed = await engine.execute(
            "incident-resume",
            idempotency_key="execute-resume",
        )
        assert completed.incident.status is IncidentStatus.RESOLVED
        assert len(completed.action_runs) == 1
        assert len(completed.incident.verification_runs) == 1
        assert decided.incident.revision + 2 == completed.incident.revision

    asyncio.run(scenario())
