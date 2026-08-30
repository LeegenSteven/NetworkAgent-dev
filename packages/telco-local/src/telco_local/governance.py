"""Deterministic, side-effect-free governance for the Local Profile.

This module deliberately closes the canonical incident lifecycle without
pretending to manage a real network.  The only proposed action is a fixed local
simulation, every write goes through :class:`IncidentRepository`, and the
approval is re-resolved against append-only incident history immediately before
the simulated action is produced.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from telco_domain import (
    ActionRun,
    ActionRunStatus,
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalReference,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
    ApprovalType,
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentRepository,
    IncidentStatus,
    NetworkChangeRequest,
    RcaConclusion,
    RcaGateway,
    RcaReport,
    RcaRequest,
    RcaResult,
    RemediationAction,
    RiskLevel,
    VerificationRequest,
    VerificationResult,
    VerificationRun,
    VerificationStatus,
    validate_approval_reference,
)
from telco_domain.models import ReportStatus


Clock = Callable[[], datetime]
LOCAL_SIMULATION_ACTION_TYPE = "LOCAL_SIMULATION"
LOCAL_SIMULATION_PARAMETERS = {
    "scenario": "local-governance-recovery",
    "version": "1.0",
}
LOCAL_SIMULATION_ROLLBACK = (
    "No rollback is required because the local simulation never changes network state."
)
_MARKER_KEY = "local_governance"
_EXECUTION_FAILURE_MARKER_KEY = "local_governance_execution_failure"
_EXECUTION_APPROVAL_EXPIRED_CODE = "APPROVAL_NO_LONGER_EFFECTIVE"
_EXECUTION_APPROVAL_EXPIRED_REASON = (
    "Fail local simulation because its approval is no longer effective"
)
_DEFAULT_APPROVAL_TTL = timedelta(minutes=15)


class LocalGovernanceError(RuntimeError):
    """Base class for deterministic local-governance failures."""


class GovernanceNotFoundError(LocalGovernanceError):
    """The requested canonical Incident does not exist."""


class GovernanceStateError(LocalGovernanceError):
    """The Incident is not in the state required by the requested operation."""


class GovernanceAuthorizationError(LocalGovernanceError):
    """The exact scoped simulation action is not currently authorized."""


class GovernanceIdempotencyConflictError(LocalGovernanceError):
    """An idempotency key was retried with a different immutable request."""


class GovernanceClockError(LocalGovernanceError):
    """A trusted governance clock returned an invalid instant."""


def _json_safe(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", round_trip=True)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    value_member = getattr(value, "value", None)
    return value_member if isinstance(value_member, (str, int, float, bool)) else value


def _digest(*parts: Any) -> str:
    encoded = json.dumps(
        _json_safe(parts),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_digest(prefix, *parts)[:32]}"


def _step_key(root_idempotency_key: str, step: str, *request_parts: Any) -> str:
    return _stable_id(
        f"local-governance-{step}",
        _digest(root_idempotency_key),
        *request_parts,
    )


def _trusted_now(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceClockError(
            "local governance clock must return a timezone-aware datetime"
        )
    return value.astimezone(UTC)


def _require_text(name: str, value: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return normalized


def _require_action_hash(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("expected_action_hash must be a lowercase SHA-256 digest")
    return value


def _latest_report(incident: Incident) -> RcaReport | None:
    if not incident.rca_reports:
        return None
    return max(incident.rca_reports, key=lambda report: report.version)


def _latest_approval(incident: Incident) -> ApprovalDecision | None:
    if not incident.approvals:
        return None
    return max(
        incident.approvals,
        key=lambda decision: (decision.requested_at, decision.request_id, decision.sequence),
    )


@dataclass(frozen=True, slots=True)
class GovernanceResult:
    """One detached view of the current local governance aggregate."""

    incident: Incident
    replayed: bool = False

    @property
    def report(self) -> RcaReport | None:
        return _latest_report(self.incident)

    @property
    def action(self) -> RemediationAction | None:
        report = self.report
        if report is None or not report.recommendations:
            return None
        return report.recommendations[0]

    @property
    def approval(self) -> ApprovalDecision | None:
        return _latest_approval(self.incident)

    @property
    def action_runs(self) -> tuple[ActionRun, ...]:
        return self.incident.action_runs

    @property
    def verification(self) -> VerificationRun | None:
        if not self.incident.verification_runs:
            return None
        return self.incident.verification_runs[-1]

    @property
    def awaiting_approval(self) -> bool:
        return (
            self.incident.status is IncidentStatus.AWAITING_APPROVAL
            and self.approval is not None
            and self.approval.status is ApprovalStatus.PENDING
        )


class LocalSimulationPolicy:
    """Create the sole action permitted by the local governance profile."""

    action_type = LOCAL_SIMULATION_ACTION_TYPE
    parameters = LOCAL_SIMULATION_PARAMETERS
    version = "1.0"

    def attach_action(
        self,
        incident: Incident,
        report: RcaReport,
        *,
        workflow_fingerprint: str,
        created_at: datetime,
    ) -> RcaReport:
        if report.conclusion is not RcaConclusion.CONCLUSIVE:
            raise GovernanceStateError(
                "only a CONCLUSIVE RCA may propose a local simulation"
            )
        if not (report.root_cause or "").strip() or not report.evidence_refs:
            raise GovernanceStateError(
                "a conclusive local simulation requires root cause and evidence"
            )
        if not incident.affected_resources:
            raise GovernanceStateError(
                "a local simulation requires at least one affected resource"
            )
        if report.recommendations:
            raise GovernanceStateError(
                "the read-only RCA gateway must not propose network actions"
            )

        policy_identity = {
            "policy": "local-simulation-policy",
            "version": self.version,
            "workflow_fingerprint": workflow_fingerprint,
            "incident_id": incident.incident_id,
            "base_report": report.model_dump(
                mode="json",
                round_trip=True,
                exclude={"report_id", "recommendations"},
            ),
            "targets": [
                resource.stable_identity()
                for resource in incident.affected_resources
            ],
        }
        action = RemediationAction(
            action_id=_stable_id("local-simulation-action", policy_identity),
            action_type=self.action_type,
            description=(
                "Simulate a governance recovery locally without contacting or "
                "changing any network resource."
            ),
            target_resources=incident.affected_resources,
            parameters=dict(self.parameters),
            risk_level=RiskLevel.LOW,
            requires_approval=True,
            reversible=True,
            rollback_plan=LOCAL_SIMULATION_ROLLBACK,
            expected_outcome="Local deterministic verification completes.",
            idempotency_key=_stable_id(
                "local-simulation-action-key", policy_identity
            ),
            created_at=created_at,
        )
        report_identity = {
            "base_report": policy_identity["base_report"],
            "action": action.model_dump(mode="json", round_trip=True),
            "policy": "local-simulation-policy",
            "policy_version": self.version,
        }
        metadata = dict(report.model_metadata)
        metadata["local_simulation_policy"] = {
            "action_type": self.action_type,
            "side_effects": False,
            "version": self.version,
        }
        payload = report.model_dump(mode="python", round_trip=True)
        payload.update(
            {
                "report_id": _stable_id(
                    "local-simulation-report", report_identity
                ),
                "recommendations": (action,),
                "model_metadata": metadata,
            }
        )
        return RcaReport.model_validate(payload)


class LocalApprovalGateway:
    """Persist and resolve append-only approvals with a trusted local clock."""

    def __init__(self, repository: IncidentRepository, *, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def _now(self) -> datetime:
        return _trusted_now(self._clock)

    @staticmethod
    def _pending_from_request(
        request: ApprovalRequest,
        *,
        requested_at: datetime,
    ) -> ApprovalDecision:
        if request.action is None or request.expires_at is None:
            raise GovernanceStateError(
                "local simulation approval requires action and expiry"
            )
        return ApprovalDecision(
            approval_id=_stable_id("local-approval-pending", request.request_id),
            request_id=request.request_id,
            sequence=0,
            incident_id=request.incident_id,
            report_id=request.report.report_id,
            report_version=request.report.version,
            subject_id=request.action.action_id,
            status=ApprovalStatus.PENDING,
            approval_type=ApprovalType.NETWORK_ACTION,
            action_hash=request.action.action_hash,
            scope=request.scope,
            requested_by="local-governance-engine",
            requested_at=requested_at,
            expires_at=request.expires_at,
            idempotency_key=request.idempotency_key,
        )

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        trusted_now = self._now()
        if request.approval_type is not ApprovalType.NETWORK_ACTION:
            raise GovernanceStateError(
                "local governance only supports network-action approval records"
            )
        if request.expires_at is None or request.expires_at <= trusted_now:
            raise GovernanceAuthorizationError(
                "approval expiry must be later than the trusted gateway time"
            )
        current = await self._repository.get(request.incident_id)
        if current is None:
            raise GovernanceNotFoundError(request.incident_id)

        existing = tuple(
            decision
            for decision in current.approvals
            if decision.request_id == request.request_id
        )
        if existing:
            pending = min(existing, key=lambda decision: decision.sequence)
            expected_binding = (
                request.incident_id,
                request.report.report_id,
                request.report.version,
                request.action.action_id if request.action else None,
                request.action.action_hash if request.action else None,
                request.scope,
                request.expires_at,
                request.idempotency_key,
            )
            actual_binding = (
                pending.incident_id,
                pending.report_id,
                pending.report_version,
                pending.subject_id,
                pending.action_hash,
                pending.scope,
                pending.expires_at,
                pending.idempotency_key,
            )
            if pending.sequence != 0 or expected_binding != actual_binding:
                raise GovernanceIdempotencyConflictError(
                    "approval request identifier was reused with another binding"
                )
            return self._approval_result(request, pending, trusted_now)

        if current.status is not IncidentStatus.RCA_COMPLETE:
            raise GovernanceStateError(
                "approval requests require an RCA_COMPLETE incident"
            )
        if current.revision != request.based_on_revision:
            raise GovernanceStateError(
                "approval request revision does not match the current incident revision"
            )
        latest = _latest_report(current)
        if latest != request.report or request.action not in request.report.recommendations:
            raise GovernanceAuthorizationError(
                "approval request does not bind the latest persisted report action"
            )

        pending = self._pending_from_request(request, requested_at=trusted_now)
        committed = await self._repository.transition(
            request.incident_id,
            IncidentStatus.AWAITING_APPROVAL,
            expected_revision=current.revision,
            idempotency_key=request.idempotency_key,
            actor="local-approval-gateway",
            reason="Request explicit approval for the local simulation",
            trace_id=current.trace_id,
            updates={"approvals": (*current.approvals, pending)},
        )
        committed_pending = committed.latest_approval_decision(request.request_id)
        if committed_pending is None:
            raise GovernanceStateError("pending approval was not persisted")
        return self._approval_result(request, committed_pending, trusted_now)

    @staticmethod
    def _approval_result(
        request: ApprovalRequest,
        decision: ApprovalDecision,
        sent_at: datetime,
    ) -> ApprovalResult:
        return ApprovalResult(
            message_id=_stable_id(
                "local-approval-result",
                request.message_id,
                decision.approval_id,
                decision.sequence,
            ),
            workflow_id=request.workflow_id,
            incident_id=request.incident_id,
            trace_id=request.trace_id,
            idempotency_key=_stable_id(
                "local-approval-result-key",
                request.idempotency_key,
                decision.sequence,
            ),
            sent_at=sent_at,
            request_id=request.request_id,
            decision=decision,
            summary_zh="本地模拟动作等待显式审批。",
        )

    async def decide(
        self,
        incident_id: str,
        *,
        approve: bool,
        expected_action_hash: str,
        expected_revision: int,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> Incident:
        if type(approve) is not bool:
            raise ValueError("approve must be a boolean")
        expected_action_hash = _require_action_hash(expected_action_hash)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        trusted_now = self._now()
        actor = _require_text("actor", actor)
        reason = _require_text("reason", reason, maximum=4_096)
        idempotency_key = _require_text("idempotency_key", idempotency_key)
        request_fingerprint = _digest(
            "approval-decision-v1",
            approve,
            expected_action_hash,
            expected_revision,
            actor,
            reason,
        )
        event_key = _step_key(idempotency_key, "approval-decision")
        approval_id = _stable_id(
            "local-approval-decision",
            _digest(idempotency_key),
            request_fingerprint,
        )

        current = await self._repository.get(incident_id)
        if current is None:
            raise GovernanceNotFoundError(incident_id)
        replay = next(
            (
                decision
                for decision in current.approvals
                if decision.idempotency_key == event_key
            ),
            None,
        )
        if replay is not None:
            if replay.approval_id != approval_id:
                raise GovernanceIdempotencyConflictError(
                    "approval decision key was retried with another request"
                )
            return current

        if current.status is not IncidentStatus.AWAITING_APPROVAL:
            raise GovernanceStateError(
                "an explicit decision requires an AWAITING_APPROVAL incident"
            )
        if current.revision != expected_revision:
            raise GovernanceStateError(
                f"approval preview revision {expected_revision} is stale; "
                f"current revision is {current.revision}"
            )
        report = _latest_report(current)
        if report is None or len(report.recommendations) != 1:
            raise GovernanceStateError(
                "local governance requires exactly one persisted simulation action"
            )
        action = report.recommendations[0]
        if action.action_hash != expected_action_hash:
            raise GovernanceAuthorizationError(
                "approved action hash does not match the current local simulation"
            )
        pending = _latest_approval(current)
        if (
            pending is None
            or pending.sequence != 0
            or pending.status is not ApprovalStatus.PENDING
            or not pending.covers_action(action, current.incident_id, report)
        ):
            raise GovernanceAuthorizationError(
                "the latest pending approval does not cover the simulation action"
            )

        expired = pending.expires_at is None or trusted_now >= pending.expires_at
        status = (
            ApprovalStatus.EXPIRED
            if approve and expired
            else ApprovalStatus.APPROVED
            if approve
            else ApprovalStatus.REJECTED
        )
        payload = pending.model_dump(mode="python", round_trip=True)
        payload.update(
            {
                "approval_id": approval_id,
                "sequence": 1,
                "status": status,
                "decided_by": actor,
                "decided_at": trusted_now,
                "reason": reason,
                "idempotency_key": event_key,
            }
        )
        terminal = ApprovalDecision.model_validate(payload)
        target = (
            IncidentStatus.REMEDIATING
            if status is ApprovalStatus.APPROVED
            else IncidentStatus.REJECTED
        )
        return await self._repository.transition(
            incident_id,
            target,
            expected_revision=current.revision,
            idempotency_key=event_key,
            actor=actor,
            reason=f"Record explicit local simulation decision: {status.value}",
            trace_id=current.trace_id,
            updates={"approvals": (*current.approvals, terminal)},
        )

    async def resolve_for_execution(
        self, request: NetworkChangeRequest
    ) -> ApprovalDecision:
        current = await self._repository.get(request.incident_id)
        if current is None:
            raise GovernanceNotFoundError(request.incident_id)
        if current.status is not IncidentStatus.REMEDIATING:
            raise ApprovalAuthorizationError(
                "local action execution requires a REMEDIATING incident"
            )
        if current.revision != request.based_on_revision + 1:
            raise ApprovalAuthorizationError(
                "incident revision changed after the approved preview"
            )
        report = _latest_report(current)
        if report != request.report or request.action not in current.recommendations:
            raise ApprovalAuthorizationError(
                "execution request does not match the frozen incident recommendation"
            )
        return validate_approval_reference(
            request.approval_reference,
            current.approvals,
            action=request.action,
            report=request.report,
            trusted_now=self._now(),
        )


class SimulatedActionGateway:
    """Produce an ActionRun only; never contact a network or another process."""

    def __init__(self, approval_gateway: LocalApprovalGateway, *, clock: Clock) -> None:
        self._approval_gateway = approval_gateway
        self._clock = clock

    async def execute(self, request: NetworkChangeRequest) -> ActionRun:
        action = request.action
        if (
            action.action_type != LOCAL_SIMULATION_ACTION_TYPE
            or action.parameters != LOCAL_SIMULATION_PARAMETERS
            or not action.reversible
            or action.rollback_plan != LOCAL_SIMULATION_ROLLBACK
        ):
            raise GovernanceAuthorizationError(
                "the local action gateway accepts only the fixed local simulation"
            )
        await self._approval_gateway.resolve_for_execution(request)
        trusted_now = _trusted_now(self._clock)
        return ActionRun(
            action_run_id=_stable_id(
                "local-simulation-run",
                request.incident_id,
                request.idempotency_key,
                action.action_hash,
            ),
            incident_id=request.incident_id,
            action_id=action.action_id,
            action_hash=action.action_hash,
            status=ActionRunStatus.SUCCEEDED,
            idempotency_key=request.idempotency_key,
            attempt=1,
            started_at=trusted_now,
            finished_at=trusted_now,
            output_summary="Local simulation completed; no network state was changed.",
            metadata={"mode": "simulation", "side_effects": False},
        )


class LocalVerificationGateway:
    """Return deterministic local TEST_RESULT evidence without external I/O."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        pending = request.verification
        if pending.status is not VerificationStatus.PENDING:
            raise GovernanceStateError("local verification input must be PENDING")
        requested_outcome = pending.metadata.get("requested_outcome")
        if requested_outcome not in {"PASSED", "FAILED"}:
            raise GovernanceStateError(
                "local verification requires an explicit deterministic outcome"
            )
        trusted_now = _trusted_now(self._clock)
        status = VerificationStatus(requested_outcome)
        evidence = EvidenceReference(
            evidence_id=_stable_id(
                "local-verification-evidence", pending.verification_id, status.value
            ),
            evidence_type=EvidenceType.TEST_RESULT,
            uri=f"local-simulation://verification/{pending.verification_id}",
            source="local-verification-gateway",
            summary=(
                "Local simulated health checks passed."
                if status is VerificationStatus.PASSED
                else "Local simulated health checks failed."
            ),
            collected_at=trusted_now,
            attributes={"mode": "simulation", "side_effects": False},
        )
        payload = pending.model_dump(mode="python", round_trip=True)
        payload.update(
            {
                "status": status,
                "evidence_refs": (evidence,),
                "summary": evidence.summary,
                "error": (
                    "Local simulated verification failed"
                    if status is VerificationStatus.FAILED
                    else None
                ),
                "started_at": trusted_now,
                "finished_at": trusted_now,
                "metadata": {
                    **pending.metadata,
                    "mode": "simulation",
                    "side_effects": False,
                },
            }
        )
        verification = VerificationRun.model_validate(payload)
        return VerificationResult(
            message_id=_stable_id(
                "local-verification-result",
                request.message_id,
                verification.verification_id,
                verification.status.value,
            ),
            workflow_id=request.workflow_id,
            incident_id=request.incident_id,
            trace_id=request.trace_id,
            idempotency_key=_stable_id(
                "local-verification-result-key", request.idempotency_key
            ),
            sent_at=trusted_now,
            verification=verification,
            summary_zh=evidence.summary or "",
        )


class LocalGovernanceEngine:
    """Orchestrate a resumable local-only Incident governance lifecycle."""

    def __init__(
        self,
        repository: IncidentRepository,
        rca_gateway: RcaGateway,
        *,
        clock: Clock,
    ) -> None:
        self.repository = repository
        self.rca_gateway = rca_gateway
        self.clock = clock
        self.simulation_policy = LocalSimulationPolicy()
        self.approval_gateway = LocalApprovalGateway(repository, clock=clock)
        self.action_gateway = SimulatedActionGateway(
            self.approval_gateway, clock=clock
        )
        self.verification_gateway = LocalVerificationGateway(clock=clock)

    def _now(self) -> datetime:
        return _trusted_now(self.clock)

    async def _get(self, incident_id: str) -> Incident:
        incident_id = _require_text("incident_id", incident_id)
        incident = await self.repository.get(incident_id)
        if incident is None:
            raise GovernanceNotFoundError(incident_id)
        return incident

    @staticmethod
    def _marker(
        root_idempotency_key: str,
        approval_ttl: timedelta,
        actor: str,
    ) -> dict[str, Any]:
        root_fingerprint = _digest(root_idempotency_key)
        request_fingerprint = _digest(
            "local-governance-prepare-v1",
            root_fingerprint,
            approval_ttl.total_seconds(),
            actor,
        )
        return {
            "actor": actor,
            "mode": "simulation",
            "policy_version": LocalSimulationPolicy.version,
            "root_fingerprint": root_fingerprint,
            "request_fingerprint": request_fingerprint,
            "approval_ttl_seconds": approval_ttl.total_seconds(),
            "workflow_id": _stable_id("local-governance-workflow", root_fingerprint),
        }

    @staticmethod
    def _require_marker(
        incident: Incident,
        expected: Mapping[str, Any],
    ) -> None:
        actual = incident.model_metadata.get(_MARKER_KEY)
        if not isinstance(actual, Mapping):
            raise GovernanceStateError(
                "incident is already owned by another lifecycle"
            )
        if actual.get("root_fingerprint") != expected["root_fingerprint"]:
            raise GovernanceStateError(
                "incident is already owned by another governance request"
            )
        if actual.get("request_fingerprint") != expected["request_fingerprint"]:
            raise GovernanceIdempotencyConflictError(
                "prepare idempotency key was retried with another request"
            )
        if dict(actual) != dict(expected):
            raise GovernanceStateError(
                "persisted local governance binding is inconsistent"
            )

    async def prepare(
        self,
        incident_id: str,
        *,
        idempotency_key: str,
        actor: str = "local-governance",
        approval_ttl: timedelta = _DEFAULT_APPROVAL_TTL,
    ) -> GovernanceResult:
        idempotency_key = _require_text("idempotency_key", idempotency_key)
        actor = _require_text("actor", actor)
        if not isinstance(approval_ttl, timedelta):
            raise ValueError("approval_ttl must be a timedelta")
        if approval_ttl <= timedelta(0) or approval_ttl > timedelta(hours=24):
            raise ValueError("approval_ttl must be greater than zero and at most 24 hours")
        marker = self._marker(idempotency_key, approval_ttl, actor)
        current = await self._get(incident_id)
        wrote = False

        if current.status is IncidentStatus.DETECTED:
            metadata = dict(current.model_metadata)
            metadata[_MARKER_KEY] = marker
            current = await self.repository.transition(
                current.incident_id,
                IncidentStatus.TRIAGED,
                expected_revision=current.revision,
                idempotency_key=_step_key(idempotency_key, "triage"),
                actor=actor,
                reason="Triage incident for deterministic local governance",
                trace_id=current.trace_id,
                updates={"model_metadata": metadata},
            )
            wrote = True
        else:
            self._require_marker(current, marker)

        if current.status is IncidentStatus.TRIAGED:
            current = await self.repository.transition(
                current.incident_id,
                IncidentStatus.INVESTIGATING,
                expected_revision=current.revision,
                idempotency_key=_step_key(idempotency_key, "investigate"),
                actor=actor,
                reason="Start deterministic local RCA",
                trace_id=current.trace_id,
            )
            wrote = True

        if current.status is IncidentStatus.INVESTIGATING:
            now = self._now()
            report_version = max(
                (report.version for report in current.rca_reports), default=0
            ) + 1
            request = RcaRequest(
                message_id=_stable_id(
                    "local-rca-request", marker["request_fingerprint"], current.revision
                ),
                workflow_id=str(marker["workflow_id"]),
                incident_id=current.incident_id,
                trace_id=current.trace_id,
                idempotency_key=_step_key(idempotency_key, "rca-request"),
                sent_at=now,
                incident=current,
                based_on_revision=current.revision,
                requested_report_version=report_version,
            )
            result = await self.rca_gateway.analyze(request)
            self._validate_rca_result(request, result)
            report = result.report
            if report.conclusion is RcaConclusion.CONCLUSIVE:
                report = self.simulation_policy.attach_action(
                    current,
                    report,
                    workflow_fingerprint=str(marker["request_fingerprint"]),
                    created_at=now,
                )
            updates: dict[str, object] = {
                "root_cause": report.root_cause,
                "hypotheses": report.hypotheses,
                "rca_reports": (*current.rca_reports, report),
                "recommendations": report.recommendations,
            }
            current = await self.repository.transition(
                current.incident_id,
                IncidentStatus.RCA_COMPLETE,
                expected_revision=current.revision,
                idempotency_key=_step_key(idempotency_key, "rca-complete"),
                actor=actor,
                reason="Persist deterministic local RCA result",
                trace_id=current.trace_id,
                updates=updates,
            )
            wrote = True

        if current.status is IncidentStatus.RCA_COMPLETE:
            report = _latest_report(current)
            if report is None:
                raise GovernanceStateError("RCA_COMPLETE incident has no report")
            if report.conclusion is RcaConclusion.INCONCLUSIVE:
                return GovernanceResult(current, replayed=not wrote)
            if len(report.recommendations) != 1:
                raise GovernanceStateError(
                    "conclusive local RCA must contain one simulation action"
                )
            action = report.recommendations[0]
            now = self._now()
            approval_request = ApprovalRequest(
                message_id=_stable_id(
                    "local-approval-request-message",
                    marker["request_fingerprint"],
                    action.action_hash,
                ),
                workflow_id=str(marker["workflow_id"]),
                incident_id=current.incident_id,
                trace_id=current.trace_id,
                idempotency_key=_step_key(idempotency_key, "approval-request"),
                sent_at=now,
                request_id=_stable_id(
                    "local-approval-request",
                    marker["request_fingerprint"],
                    report.report_id,
                    action.action_hash,
                ),
                approval_type=ApprovalType.NETWORK_ACTION,
                report=report,
                action=action,
                scope=action.target_resources,
                based_on_revision=current.revision,
                expires_at=now + approval_ttl,
                summary_zh="请显式审批隔离环境中的本地模拟动作。",
            )
            await self.approval_gateway.request_approval(approval_request)
            current = await self._get(current.incident_id)
            wrote = True

        if current.status is IncidentStatus.FAILED:
            self._require_complete_execution_failure_binding(current)
            return GovernanceResult(current, replayed=True)

        if current.status in {
            IncidentStatus.AWAITING_APPROVAL,
            IncidentStatus.REMEDIATING,
            IncidentStatus.VERIFYING,
            IncidentStatus.RESOLVED,
            IncidentStatus.REOPENED,
            IncidentStatus.REJECTED,
        }:
            self._require_marker(current, marker)
            return GovernanceResult(current, replayed=not wrote)
        raise GovernanceStateError(
            f"prepare cannot resume incident in {current.status.value}"
        )

    @staticmethod
    def _validate_rca_result(request: RcaRequest, result: RcaResult) -> None:
        if (
            result.incident_id != request.incident_id
            or result.trace_id != request.trace_id
            or result.based_on_revision != request.based_on_revision
            or result.requested_report_version != request.requested_report_version
            or result.report.incident_id != request.incident_id
            or result.report.version != request.requested_report_version
        ):
            raise GovernanceStateError(
                "RCA result is not bound to the requested incident revision"
            )
        if result.report.status not in {
            ReportStatus.PROPOSED,
            ReportStatus.APPROVED,
            ReportStatus.PERSISTED,
        }:
            raise GovernanceStateError("RCA result is not eligible for persistence")
        if not result.report.evidence_refs:
            raise GovernanceStateError("RCA result must include local evidence")
        if result.report.recommendations:
            raise GovernanceStateError(
                "the read-only local RCA gateway returned an action"
            )

    async def decide(
        self,
        incident_id: str,
        *,
        approve: bool,
        expected_action_hash: str,
        expected_revision: int,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> GovernanceResult:
        if type(approve) is not bool:
            raise ValueError("approve must be a boolean")
        current_before = await self._get(incident_id)
        event_key = _step_key(
            _require_text("idempotency_key", idempotency_key),
            "approval-decision",
        )
        had_event = any(
            decision.idempotency_key == event_key
            for decision in current_before.approvals
        )
        committed = await self.approval_gateway.decide(
            incident_id,
            approve=approve,
            expected_action_hash=expected_action_hash,
            expected_revision=expected_revision,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        return GovernanceResult(committed, replayed=had_event)

    @staticmethod
    def _execution_failure_binding(
        *,
        root_fingerprint: str,
        request_fingerprint: str,
    ) -> dict[str, object]:
        return {
            "code": _EXECUTION_APPROVAL_EXPIRED_CODE,
            "request_fingerprint": request_fingerprint,
            "root_fingerprint": root_fingerprint,
            "side_effects": False,
        }

    @staticmethod
    def _require_complete_execution_failure_binding(
        incident: Incident,
    ) -> dict[str, object]:
        raw = incident.model_metadata.get(_EXECUTION_FAILURE_MARKER_KEY)
        report = _latest_report(incident)
        action = (
            report.recommendations[0]
            if report is not None and len(report.recommendations) == 1
            else None
        )
        approval = _latest_approval(incident)
        required_keys = {
            "code",
            "request_fingerprint",
            "root_fingerprint",
            "side_effects",
        }
        if (
            not isinstance(raw, Mapping)
            or set(raw) != required_keys
            or raw.get("code") != _EXECUTION_APPROVAL_EXPIRED_CODE
            or raw.get("side_effects") is not False
            or not _is_sha256_digest(raw.get("request_fingerprint"))
            or not _is_sha256_digest(raw.get("root_fingerprint"))
            or incident.action_runs
            or incident.verification_runs
            or action is None
            or action.action_type != LOCAL_SIMULATION_ACTION_TYPE
            or action.parameters != LOCAL_SIMULATION_PARAMETERS
            or approval is None
            or approval.status is not ApprovalStatus.APPROVED
            or report is None
            or not approval.covers_action(action, incident.incident_id, report)
        ):
            raise GovernanceStateError(
                "FAILED incident lacks a complete local execution failure binding"
            )
        return dict(raw)

    async def _fail_expired_execution(
        self,
        incident: Incident,
        *,
        idempotency_key: str,
        actor: str,
        root_fingerprint: str,
        request_fingerprint: str,
    ) -> GovernanceResult:
        if incident.status is not IncidentStatus.REMEDIATING:
            raise GovernanceStateError(
                "expired execution recovery requires a REMEDIATING incident"
            )
        metadata = dict(incident.model_metadata)
        metadata[_EXECUTION_FAILURE_MARKER_KEY] = self._execution_failure_binding(
            root_fingerprint=root_fingerprint,
            request_fingerprint=request_fingerprint,
        )
        committed = await self.repository.transition(
            incident.incident_id,
            IncidentStatus.FAILED,
            expected_revision=incident.revision,
            idempotency_key=_step_key(
                idempotency_key,
                "approval-no-longer-effective",
                request_fingerprint,
            ),
            actor=actor,
            reason=_EXECUTION_APPROVAL_EXPIRED_REASON,
            trace_id=incident.trace_id,
            updates={"model_metadata": metadata},
        )
        return GovernanceResult(committed, replayed=False)

    async def execute(
        self,
        incident_id: str,
        *,
        idempotency_key: str,
        actor: str = "local-simulator",
        verification_passed: bool = True,
    ) -> GovernanceResult:
        if type(verification_passed) is not bool:
            raise ValueError("verification_passed must be a boolean")
        idempotency_key = _require_text("idempotency_key", idempotency_key)
        actor = _require_text("actor", actor)
        root_fingerprint = _digest(idempotency_key)
        current = await self._get(incident_id)
        report = _latest_report(current)
        if report is None or len(report.recommendations) != 1:
            raise GovernanceStateError(
                "execution requires one frozen local simulation action"
            )
        action = report.recommendations[0]
        approval = _latest_approval(current)
        request_fingerprint = _digest(
            "local-governance-execute-v2",
            current.incident_id,
            root_fingerprint,
            actor,
            verification_passed,
            action.action_hash,
            approval.approval_id if approval is not None else None,
        )
        verification_id = _stable_id(
            "local-verification", root_fingerprint, incident_id
        )
        existing_verification = next(
            (
                run
                for run in current.verification_runs
                if run.verification_id == verification_id
            ),
            None,
        )
        if existing_verification is not None:
            if (
                existing_verification.metadata.get("request_fingerprint")
                != request_fingerprint
            ):
                raise GovernanceIdempotencyConflictError(
                    "execution key was retried with another request"
                )
            return GovernanceResult(current, replayed=True)

        if current.status is IncidentStatus.FAILED:
            try:
                failure_binding = (
                    self._require_complete_execution_failure_binding(current)
                )
            except GovernanceStateError:
                raise GovernanceStateError(
                    "FAILED incident was not produced by expired execution recovery"
                ) from None
            if (
                failure_binding.get("root_fingerprint")
                != root_fingerprint
            ):
                raise GovernanceStateError(
                    "FAILED incident belongs to another execution request"
                )
            expected_binding = self._execution_failure_binding(
                root_fingerprint=root_fingerprint,
                request_fingerprint=request_fingerprint,
            )
            if failure_binding.get("request_fingerprint") != request_fingerprint:
                raise GovernanceIdempotencyConflictError(
                    "execution key was retried with another request"
                )
            if dict(failure_binding) != expected_binding:
                raise GovernanceStateError(
                    "persisted execution failure binding is inconsistent"
                )
            return GovernanceResult(current, replayed=True)

        action_key = _step_key(
            idempotency_key,
            "simulate-action",
            request_fingerprint,
            action.action_hash,
        )
        expected_run_id = _stable_id(
            "local-simulation-run",
            current.incident_id,
            action_key,
            action.action_hash,
        )
        if current.status is IncidentStatus.REMEDIATING:
            if (
                approval is None
                or approval.status is not ApprovalStatus.APPROVED
                or not approval.covers_action(action, current.incident_id, report)
            ):
                raise GovernanceAuthorizationError(
                    "latest approval does not cover the simulation action"
                )
            now = self._now()
            if not approval.is_effective(now):
                return await self._fail_expired_execution(
                    current,
                    idempotency_key=idempotency_key,
                    actor=actor,
                    root_fingerprint=root_fingerprint,
                    request_fingerprint=request_fingerprint,
                )
            reference = ApprovalReference(
                approval_id=approval.approval_id,
                request_id=approval.request_id,
                decision_sequence=approval.sequence,
                incident_id=current.incident_id,
                report_id=report.report_id,
                report_version=report.version,
                subject_id=action.action_id,
                action_hash=action.action_hash,
                based_on_revision=current.revision - 1,
                validated_at=now,
                validator_id="local-governance-engine",
            )
            request = NetworkChangeRequest(
                message_id=_stable_id(
                    "local-change-request", request_fingerprint, action.action_hash
                ),
                workflow_id=_stable_id(
                    "local-execution-workflow", request_fingerprint
                ),
                incident_id=current.incident_id,
                trace_id=current.trace_id,
                idempotency_key=action_key,
                sent_at=now,
                action=action,
                report=report,
                based_on_revision=current.revision - 1,
                approval_reference=reference,
            )
            try:
                action_run = await self.action_gateway.execute(request)
            except ApprovalAuthorizationError as exc:
                if not approval.is_effective(self._now()):
                    return await self._fail_expired_execution(
                        current,
                        idempotency_key=idempotency_key,
                        actor=actor,
                        root_fingerprint=root_fingerprint,
                        request_fingerprint=request_fingerprint,
                    )
                raise GovernanceAuthorizationError(str(exc)) from None
            current = await self.repository.transition(
                current.incident_id,
                IncidentStatus.VERIFYING,
                expected_revision=current.revision,
                idempotency_key=action_key,
                actor=actor,
                reason="Record side-effect-free local simulation run",
                trace_id=current.trace_id,
                updates={"action_runs": (*current.action_runs, action_run)},
            )
        if current.status is not IncidentStatus.VERIFYING:
            raise GovernanceStateError(
                "local execution requires a REMEDIATING or resumable VERIFYING incident"
            )
        action_run = next(
            (
                run
                for run in current.action_runs
                if run.action_run_id == expected_run_id
            ),
            None,
        )
        if action_run is None:
            if current.action_runs:
                raise GovernanceIdempotencyConflictError(
                    "another execution request already produced the action run"
                )
            raise GovernanceStateError("simulated action run was not persisted")
        if (
            action_run.status is not ActionRunStatus.SUCCEEDED
            or action_run.action_hash != action.action_hash
        ):
            raise GovernanceStateError("simulated action run is not successful")

        now = self._now()
        pending_verification = VerificationRun(
            verification_id=verification_id,
            incident_id=current.incident_id,
            action_run_ids=(action_run.action_run_id,),
            status=VerificationStatus.PENDING,
            checks=("local deterministic post-remediation health check",),
            metadata={
                "mode": "simulation",
                "requested_outcome": (
                    "PASSED" if verification_passed else "FAILED"
                ),
                "request_fingerprint": request_fingerprint,
            },
        )
        verification_key = _step_key(
            idempotency_key, "verify", request_fingerprint
        )
        verification_request = VerificationRequest(
            message_id=_stable_id(
                "local-verification-request", request_fingerprint, verification_id
            ),
            workflow_id=_stable_id(
                "local-execution-workflow", request_fingerprint
            ),
            incident_id=current.incident_id,
            trace_id=current.trace_id,
            idempotency_key=verification_key,
            sent_at=now,
            action_run=action_run,
            verification=pending_verification,
        )
        verification_result = await self.verification_gateway.verify(
            verification_request
        )
        verification = verification_result.verification
        target = (
            IncidentStatus.RESOLVED
            if verification.status is VerificationStatus.PASSED
            else IncidentStatus.REOPENED
        )
        current = await self.repository.transition(
            current.incident_id,
            target,
            expected_revision=current.revision,
            idempotency_key=verification_key,
            actor=actor,
            reason=f"Record local simulated verification: {verification.status.value}",
            trace_id=current.trace_id,
            updates={
                "verification_runs": (*current.verification_runs, verification)
            },
        )
        return GovernanceResult(current, replayed=False)


__all__ = [
    "GovernanceAuthorizationError",
    "GovernanceClockError",
    "GovernanceIdempotencyConflictError",
    "GovernanceNotFoundError",
    "GovernanceResult",
    "GovernanceStateError",
    "LOCAL_SIMULATION_ACTION_TYPE",
    "LocalApprovalGateway",
    "LocalGovernanceEngine",
    "LocalGovernanceError",
    "LocalSimulationPolicy",
    "LocalVerificationGateway",
    "SimulatedActionGateway",
]
