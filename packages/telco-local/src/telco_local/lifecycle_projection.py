"""Privacy-safe projection of the completed canonical local lifecycle.

The projection is deliberately derived from durable records after the local
demo has completed.  It is not a runtime log, a distributed trace, or a new
source of lifecycle state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from telco_domain import (
    ActionRun,
    ActionRunStatus,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalType,
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentAuditEvent,
    IncidentSeverity,
    IncidentStatus,
    RcaConclusion,
    RcaReport,
    RemediationAction,
    RiskLevel,
    VerificationRun,
    VerificationStatus,
)
from telco_domain.models import ReportStatus

from .governance import (
    LOCAL_SIMULATION_ACTION_TYPE,
    LOCAL_SIMULATION_PARAMETERS,
    LOCAL_SIMULATION_ROLLBACK,
)
from .rca import RCA_ENGINE_NAME, RCA_ENGINE_VERSION


PROJECTION_SCHEMA = "networkagent-local-lifecycle-projection/1.0"
PROJECTION_CLASSIFICATION = "DERIVED_FROM_DURABLE_CANONICAL_RECORDS"
PROJECTION_ORDERING = "REVISION_GROUPED_ATOMIC_PROJECTION"

_EXPECTED_TO_STATUSES = (
    IncidentStatus.DETECTED,
    IncidentStatus.TRIAGED,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.RCA_COMPLETE,
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
)
_EXPECTED_FROM_STATUSES = (
    None,
    IncidentStatus.DETECTED,
    IncidentStatus.TRIAGED,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.RCA_COMPLETE,
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
)
_RCA_METADATA_KEYS = {
    "engine",
    "engine_version",
    "model_provider",
    "severity",
    "rule_resolution",
    "rule_resolution_issues",
    "evidence_resolution",
    "evidence_resolution_issues",
    "matched_rule_ids",
    "rule_versions",
    "based_on_revision",
    "local_simulation_policy",
}
_VERIFICATION_METADATA_KEYS = {
    "mode",
    "requested_outcome",
    "request_fingerprint",
    "side_effects",
}
_LOCAL_ACTION_DESCRIPTION = (
    "Simulate a governance recovery locally without contacting or "
    "changing any network resource."
)
_LOCAL_ACTION_OUTCOME = "Local deterministic verification completes."
_VERIFICATION_CHECKS = ("local deterministic post-remediation health check",)
_VERIFICATION_EVIDENCE_ATTRIBUTES = {
    "mode": "simulation",
    "side_effects": False,
}
_INCIDENT_METADATA_KEYS = {
    "detector_algorithm",
    "rule_content_hashes",
    "local_governance",
}
_LOCAL_GOVERNANCE_MARKER_KEYS = {
    "actor",
    "mode",
    "policy_version",
    "root_fingerprint",
    "request_fingerprint",
    "approval_ttl_seconds",
    "workflow_id",
}
_RUN_OUTPUT_PREFIX = "Local simulation completed; "
_ACTION_RUN_OUTPUT = _RUN_OUTPUT_PREFIX + "no network state was changed."
_DETECTOR_ALGORITHM = "deterministic-threshold-episodes-v3"
_APPROVAL_REASON = "approved fixed isolated local simulation"
_EXPECTED_AUDIT_ACTORS = (
    "local-stack-operator",
    "local-governance",
    "local-governance",
    "local-governance",
    "local-approval-gateway",
    "local-stack-operator",
    "local-simulator",
    "local-simulator",
)
_EXPECTED_AUDIT_REASONS = (
    "explicit local demo incident confirmation",
    "Triage incident for deterministic local governance",
    "Start deterministic local RCA",
    "Persist deterministic local RCA result",
    "Request explicit approval for the local simulation",
    "Record explicit local simulation decision: APPROVED",
    "Record side-effect-free local simulation run",
)


@dataclass(frozen=True, slots=True)
class _DemoChain:
    marker: dict[str, object]
    audit_keys: tuple[str, ...]
    execution_fingerprint: str
    pending_request_id: str
    pending_approval_id: str
    approved_approval_id: str
    action_run_id: str
    verification_id: str


class LifecycleProjectionError(ValueError):
    """A fixed-code failure that never echoes canonical record contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise LifecycleProjectionError(code)


def _timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_exact_string(value: object, expected: object) -> bool:
    return type(value) is str and value == expected


def _is_local_step_key(value: object, step: str) -> bool:
    prefix = f"local-governance-{step}-"
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value.removeprefix(prefix)
    return len(digest) == 32 and all(
        character in "0123456789abcdef" for character in digest
    )


def _canonical_digest(*parts: object) -> str:
    encoded = json.dumps(
        parts,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_canonical_digest(prefix, *parts)[:32]}"


def _step_key(root_key: str, step: str, *request_parts: object) -> str:
    return _stable_id(
        f"local-governance-{step}",
        _canonical_digest(root_key),
        *request_parts,
    )


def _local_stack_roots(incident_id: str) -> tuple[str, str, str, str, str]:
    digest = hashlib.sha256(incident_id.encode("utf-8")).hexdigest()[:16]
    return (
        digest,
        f"local-stack-confirm-key-{digest}",
        f"local-stack-prepare-key-{digest}",
        f"local-stack-decision-key-{digest}",
        f"local-stack-execute-key-{digest}",
    )


def _expected_prepare_marker(incident_id: str) -> dict[str, object]:
    _, _, prepare_key, _, _ = _local_stack_roots(incident_id)
    root_fingerprint = _canonical_digest(prepare_key)
    request_fingerprint = _canonical_digest(
        "local-governance-prepare-v1",
        root_fingerprint,
        900.0,
        "local-governance",
    )
    return {
        "actor": "local-governance",
        "mode": "simulation",
        "policy_version": "1.0",
        "root_fingerprint": root_fingerprint,
        "request_fingerprint": request_fingerprint,
        "approval_ttl_seconds": 900.0,
        "workflow_id": _stable_id(
            "local-governance-workflow",
            root_fingerprint,
        ),
    }


def _build_demo_chain(
    incident: Incident,
    report: RcaReport,
    action: RemediationAction,
    approved: ApprovalDecision,
    *,
    expected_verification: VerificationStatus,
) -> _DemoChain:
    (
        _,
        confirm_key,
        prepare_key,
        decision_key,
        execute_key,
    ) = _local_stack_roots(incident.incident_id)
    marker = _expected_prepare_marker(incident.incident_id)
    pending_request_id = _stable_id(
        "local-approval-request",
        marker["request_fingerprint"],
        report.report_id,
        action.action_hash,
    )
    pending_approval_id = _stable_id(
        "local-approval-pending",
        pending_request_id,
    )
    decision_fingerprint = _canonical_digest(
        "approval-decision-v1",
        True,
        action.action_hash,
        4,
        "local-stack-operator",
        _APPROVAL_REASON,
    )
    approved_approval_id = _stable_id(
        "local-approval-decision",
        _canonical_digest(decision_key),
        decision_fingerprint,
    )
    execution_root = _canonical_digest(execute_key)
    execution_fingerprint = _canonical_digest(
        "local-governance-execute-v2",
        incident.incident_id,
        execution_root,
        "local-simulator",
        expected_verification is VerificationStatus.PASSED,
        action.action_hash,
        approved.approval_id,
    )
    action_key = _step_key(
        execute_key,
        "simulate-action",
        execution_fingerprint,
        action.action_hash,
    )
    verification_key = _step_key(
        execute_key,
        "verify",
        execution_fingerprint,
    )
    audit_keys = (
        confirm_key,
        _step_key(prepare_key, "triage"),
        _step_key(prepare_key, "investigate"),
        _step_key(prepare_key, "rca-complete"),
        _step_key(prepare_key, "approval-request"),
        _step_key(decision_key, "approval-decision"),
        action_key,
        verification_key,
    )
    return _DemoChain(
        marker=marker,
        audit_keys=audit_keys,
        execution_fingerprint=execution_fingerprint,
        pending_request_id=pending_request_id,
        pending_approval_id=pending_approval_id,
        approved_approval_id=approved_approval_id,
        action_run_id=_stable_id(
            "local-simulation-run",
            incident.incident_id,
            action_key,
            action.action_hash,
        ),
        verification_id=_stable_id(
            "local-verification",
            execution_root,
            incident.incident_id,
        ),
    )


def _event(
    sequence: int,
    *,
    occurred_at: datetime,
    record_type: str,
    component: str,
    operation: str,
    outcome: str,
) -> dict[str, object]:
    return {
        "sequence": sequence,
        "occurred_at": _timestamp(occurred_at),
        "record_type": record_type,
        "component": component,
        "operation": operation,
        "outcome": outcome,
    }


def _audit_event(
    sequence: int,
    event: IncidentAuditEvent,
) -> dict[str, object]:
    return _event(
        sequence,
        occurred_at=event.occurred_at,
        record_type="INCIDENT_AUDIT_EVENT",
        component="INCIDENT_REPOSITORY",
        operation="RECORD_STATE_TRANSITION",
        outcome=event.to_status.value,
    )


def _validate_incident(incident: object, expected: IncidentStatus) -> Incident:
    if not isinstance(incident, Incident):
        _fail("INCIDENT_CONTRACT_MISMATCH")
    metadata = incident.model_metadata
    marker = metadata.get("local_governance")
    rule_hashes = metadata.get("rule_content_hashes")
    expected_marker = _expected_prepare_marker(incident.incident_id)
    marker_is_exact = (
        isinstance(marker, dict)
        and set(marker) == _LOCAL_GOVERNANCE_MARKER_KEYS
        and all(
            _is_exact_string(marker.get(key), expected_marker[key])
            for key in (
                "actor",
                "mode",
                "policy_version",
                "root_fingerprint",
                "request_fingerprint",
                "workflow_id",
            )
        )
        and type(marker.get("approval_ttl_seconds")) is float
        and marker.get("approval_ttl_seconds") == 900.0
    )
    digest, _, _, _, _ = _local_stack_roots(incident.incident_id)
    if (
        incident.status is not expected
        or type(incident.revision) is not int
        or incident.revision != 7
        or incident.trace_id != f"local-stack-confirm-trace-{digest}"
        or set(metadata) != _INCIDENT_METADATA_KEYS
        or metadata.get("detector_algorithm") != _DETECTOR_ALGORITHM
        or not isinstance(rule_hashes, dict)
        or set(rule_hashes) != set(incident.rule_versions)
        or not all(_is_sha256_digest(value) for value in rule_hashes.values())
        or not marker_is_exact
    ):
        _fail("INCIDENT_CONTRACT_MISMATCH")
    return incident


def _validate_history(
    history: object,
    *,
    incident: Incident,
    expected: IncidentStatus,
) -> tuple[IncidentAuditEvent, ...]:
    if not isinstance(history, Sequence) or isinstance(
        history, (str, bytes, bytearray)
    ):
        _fail("HISTORY_CONTRACT_MISMATCH")
    if len(history) != 8:
        _fail("HISTORY_CONTRACT_MISMATCH")
    events = tuple(history)
    expected_to = (*_EXPECTED_TO_STATUSES, expected)
    event_ids: set[str] = set()
    idempotency_keys: set[str] = set()
    for revision, event in enumerate(events):
        if not isinstance(event, IncidentAuditEvent):
            _fail("HISTORY_CONTRACT_MISMATCH")
        if (
            type(event.revision) is not int
            or event.revision != revision
            or event.incident_id != incident.incident_id
            or event.trace_id != incident.trace_id
            or event.from_status is not _EXPECTED_FROM_STATUSES[revision]
            or event.to_status is not expected_to[revision]
            or event.event_id in event_ids
            or event.idempotency_key in idempotency_keys
        ):
            _fail("HISTORY_CONTRACT_MISMATCH")
        event_ids.add(event.event_id)
        idempotency_keys.add(event.idempotency_key)
    return events


def _validate_report(incident: Incident) -> RcaReport:
    if len(incident.rca_reports) != 1:
        _fail("REPORT_CONTRACT_MISMATCH")
    report = incident.rca_reports[0]
    if not isinstance(report, RcaReport):
        _fail("REPORT_CONTRACT_MISMATCH")
    evidence = tuple(report.evidence_refs)
    metadata = report.model_metadata
    matched_rule_ids = metadata.get("matched_rule_ids")
    rule_versions = metadata.get("rule_versions")
    policy = metadata.get("local_simulation_policy")
    policy_is_exact = (
        isinstance(policy, dict)
        and set(policy) == {"action_type", "side_effects", "version"}
        and type(policy.get("action_type")) is str
        and policy.get("action_type") == LOCAL_SIMULATION_ACTION_TYPE
        and type(policy.get("side_effects")) is bool
        and policy.get("side_effects") is False
        and type(policy.get("version")) is str
        and policy.get("version") == "1.0"
    )
    valid_rule_bindings = (
        isinstance(matched_rule_ids, list)
        and bool(matched_rule_ids)
        and all(
            isinstance(rule_id, str) and bool(rule_id.strip())
            for rule_id in matched_rule_ids
        )
        and len(matched_rule_ids) == len(set(matched_rule_ids))
        and isinstance(rule_versions, dict)
        and set(rule_versions) == set(matched_rule_ids)
        and all(
            isinstance(version, str) and bool(version.strip())
            for version in rule_versions.values()
        )
    )
    evidence_ids = tuple(item.evidence_id for item in evidence)
    if (
        report.incident_id != incident.incident_id
        or report.version != 1
        or report.status is not ReportStatus.PROPOSED
        or report.conclusion is not RcaConclusion.CONCLUSIVE
        or not (report.root_cause or "").strip()
        or incident.root_cause != report.root_cause
        or incident.hypotheses != report.hypotheses
        or not report.summary.strip()
        or not evidence
        or not all(isinstance(item, EvidenceReference) for item in evidence)
        or len(evidence_ids) != len(set(evidence_ids))
        or not all(item.source is not None for item in evidence)
        or report.generated_by != f"{RCA_ENGINE_NAME}/{RCA_ENGINE_VERSION}"
        or set(metadata) != _RCA_METADATA_KEYS
        or metadata.get("engine") != RCA_ENGINE_NAME
        or metadata.get("engine_version") != RCA_ENGINE_VERSION
        or metadata.get("model_provider") != "none"
        or metadata.get("severity")
        not in {severity.value for severity in IncidentSeverity}
        or metadata.get("rule_resolution") != "EXACT"
        or metadata.get("rule_resolution_issues") != []
        or metadata.get("evidence_resolution") != "EXACT"
        or metadata.get("evidence_resolution_issues") != []
        or type(metadata.get("based_on_revision")) is not int
        or metadata.get("based_on_revision") != 2
        or rule_versions != incident.rule_versions
        or not policy_is_exact
        or not valid_rule_bindings
        or len(report.recommendations) != 1
    ):
        _fail("REPORT_CONTRACT_MISMATCH")
    return report


def _validate_action(
    incident: Incident,
    report: RcaReport,
) -> RemediationAction:
    if len(incident.recommendations) != 1:
        _fail("ACTION_CONTRACT_MISMATCH")
    action = incident.recommendations[0]
    if not isinstance(action, RemediationAction):
        _fail("ACTION_CONTRACT_MISMATCH")
    if (
        report.recommendations[0] != action
        or action.action_type != LOCAL_SIMULATION_ACTION_TYPE
        or action.description != _LOCAL_ACTION_DESCRIPTION
        or action.parameters != LOCAL_SIMULATION_PARAMETERS
        or action.risk_level is not RiskLevel.LOW
        or action.requires_approval is not True
        or action.reversible is not True
        or action.rollback_plan != LOCAL_SIMULATION_ROLLBACK
        or action.expected_outcome != _LOCAL_ACTION_OUTCOME
        or action.idempotency_key is None
        or action.target_resources != incident.affected_resources
    ):
        _fail("ACTION_CONTRACT_MISMATCH")
    return action


def _validate_approvals(
    incident: Incident,
    report: RcaReport,
    action: RemediationAction,
    audit: tuple[IncidentAuditEvent, ...],
) -> tuple[ApprovalDecision, ApprovalDecision]:
    if len(incident.approvals) != 2:
        _fail("APPROVAL_CONTRACT_MISMATCH")
    pending, approved = incident.approvals
    if not isinstance(pending, ApprovalDecision) or not isinstance(
        approved, ApprovalDecision
    ):
        _fail("APPROVAL_CONTRACT_MISMATCH")
    approval_ttl = (
        pending.expires_at - pending.requested_at
        if pending.expires_at is not None
        else timedelta(0)
    )
    if (
        pending.status is not ApprovalStatus.PENDING
        or type(pending.sequence) is not int
        or pending.sequence != 0
        or pending.decided_at is not None
        or pending.decided_by is not None
        or pending.reason is not None
        or pending.requested_by != "local-governance-engine"
        or pending.expires_at is None
        or approval_ttl <= timedelta(0)
        or approval_ttl > timedelta(seconds=900)
        or approved.status is not ApprovalStatus.APPROVED
        or type(approved.sequence) is not int
        or approved.sequence != 1
        or approved.decided_at is None
        or approved.requested_by != "local-governance-engine"
        or approved.decided_by != "local-stack-operator"
        or approved.reason != _APPROVAL_REASON
        or pending.approval_type is not ApprovalType.NETWORK_ACTION
        or approved.approval_type is not ApprovalType.NETWORK_ACTION
        or pending.request_id != approved.request_id
        or pending.approval_id == approved.approval_id
        or pending.idempotency_key == approved.idempotency_key
        or pending.idempotency_key != audit[4].idempotency_key
        or approved.idempotency_key != audit[5].idempotency_key
        or not _is_local_step_key(
            pending.idempotency_key,
            "approval-request",
        )
        or not _is_local_step_key(
            approved.idempotency_key,
            "approval-decision",
        )
        or pending.requested_at != approved.requested_at
        or pending.expires_at != approved.expires_at
        or not pending.covers_action(action, incident.incident_id, report)
        or not approved.covers_action(action, incident.incident_id, report)
    ):
        _fail("APPROVAL_CONTRACT_MISMATCH")
    return pending, approved


def _validate_action_run(
    incident: Incident,
    action: RemediationAction,
    audit: tuple[IncidentAuditEvent, ...],
    chain: _DemoChain,
) -> ActionRun:
    if len(incident.action_runs) != 1:
        _fail("ACTION_RUN_CONTRACT_MISMATCH")
    run = incident.action_runs[0]
    if not isinstance(run, ActionRun):
        _fail("ACTION_RUN_CONTRACT_MISMATCH")
    metadata = run.metadata
    metadata_is_exact = (
        isinstance(metadata, dict)
        and set(metadata) == {"mode", "side_effects"}
        and type(metadata.get("mode")) is str
        and metadata.get("mode") == "simulation"
        and type(metadata.get("side_effects")) is bool
        and metadata.get("side_effects") is False
    )
    if (
        run.incident_id != incident.incident_id
        or run.action_run_id != chain.action_run_id
        or run.action_id != action.action_id
        or run.action_hash != action.action_hash
        or run.status is not ActionRunStatus.SUCCEEDED
        or type(run.attempt) is not int
        or run.attempt != 1
        or run.idempotency_key != audit[6].idempotency_key
        or run.idempotency_key != chain.audit_keys[6]
        or not _is_local_step_key(run.idempotency_key, "simulate-action")
        or run.started_at is None
        or run.finished_at is None
        or run.error is not None
        or run.output_summary != _ACTION_RUN_OUTPUT
        or not metadata_is_exact
    ):
        _fail("ACTION_RUN_CONTRACT_MISMATCH")
    return run


def _validate_verification(
    incident: Incident,
    action_run: ActionRun,
    chain: _DemoChain,
    audit: tuple[IncidentAuditEvent, ...],
    *,
    expected: VerificationStatus,
) -> VerificationRun:
    if len(incident.verification_runs) != 1:
        _fail("VERIFICATION_CONTRACT_MISMATCH")
    verification = incident.verification_runs[0]
    if not isinstance(verification, VerificationRun):
        _fail("VERIFICATION_CONTRACT_MISMATCH")
    expected_evidence_summary = (
        "Local simulated health checks passed."
        if expected is VerificationStatus.PASSED
        else "Local simulated health checks failed."
    )
    expected_error = (
        None
        if expected is VerificationStatus.PASSED
        else "Local simulated verification failed"
    )
    evidence_refs = verification.evidence_refs
    evidence = evidence_refs[0] if len(evidence_refs) == 1 else None
    metadata = verification.metadata
    request_fingerprint = metadata.get("request_fingerprint")
    metadata_is_exact = (
        isinstance(metadata, dict)
        and set(metadata) == _VERIFICATION_METADATA_KEYS
        and type(metadata.get("mode")) is str
        and metadata.get("mode") == "simulation"
        and type(metadata.get("requested_outcome")) is str
        and metadata.get("requested_outcome") == expected.value
        and type(request_fingerprint) is str
        and request_fingerprint == chain.execution_fingerprint
        and type(metadata.get("side_effects")) is bool
        and metadata.get("side_effects") is False
    )
    evidence_attributes_are_exact = (
        isinstance(evidence, EvidenceReference)
        and isinstance(evidence.attributes, dict)
        and set(evidence.attributes) == {"mode", "side_effects"}
        and type(evidence.attributes.get("mode")) is str
        and evidence.attributes.get("mode") == "simulation"
        and type(evidence.attributes.get("side_effects")) is bool
        and evidence.attributes.get("side_effects") is False
    )
    evidence_is_exact = (
        isinstance(evidence, EvidenceReference)
        and evidence.evidence_type is EvidenceType.TEST_RESULT
        and evidence.evidence_id
        == _stable_id(
            "local-verification-evidence",
            verification.verification_id,
            expected.value,
        )
        and evidence.source == "local-verification-gateway"
        and evidence.uri
        == f"local-simulation://verification/{verification.verification_id}"
        and evidence.summary == expected_evidence_summary
        and evidence.collected_at == verification.finished_at
        and evidence_attributes_are_exact
        and evidence.content_type is None
        and evidence.checksum_sha256 is None
    )
    if (
        verification.incident_id != incident.incident_id
        or verification.verification_id != chain.verification_id
        or audit[7].idempotency_key != chain.audit_keys[7]
        or verification.action_run_ids != (action_run.action_run_id,)
        or verification.status is not expected
        or verification.checks != _VERIFICATION_CHECKS
        or verification.observed_kpis
        or verification.started_at is None
        or verification.finished_at is None
        or verification.started_at != verification.finished_at
        or verification.summary != expected_evidence_summary
        or verification.error != expected_error
        or not metadata_is_exact
        or not evidence_is_exact
    ):
        _fail("VERIFICATION_CONTRACT_MISMATCH")
    return verification


def _validate_demo_chain(
    chain: _DemoChain,
    audit: tuple[IncidentAuditEvent, ...],
    pending: ApprovalDecision,
    approved: ApprovalDecision,
    *,
    expected_verification: VerificationStatus,
) -> None:
    actual_audit_keys = tuple(event.idempotency_key for event in audit[:6])
    actual_actors = tuple(event.actor for event in audit)
    actual_reasons = tuple(event.reason for event in audit[:7])
    expected_final_reason = (
        f"Record local simulated verification: {expected_verification.value}"
    )
    if (
        actual_audit_keys != chain.audit_keys[:6]
        or actual_actors != _EXPECTED_AUDIT_ACTORS
        or actual_reasons != _EXPECTED_AUDIT_REASONS
        or audit[7].reason != expected_final_reason
        or pending.request_id != chain.pending_request_id
        or approved.request_id != chain.pending_request_id
        or pending.approval_id != chain.pending_approval_id
        or approved.approval_id != chain.approved_approval_id
    ):
        _fail("DEMO_CHAIN_CONTRACT_MISMATCH")


def _build_projection(
    incident: object,
    history: object,
    *,
    expected_status: object,
) -> dict[str, object]:
    if not isinstance(expected_status, str) or expected_status not in {
        "RESOLVED",
        "REOPENED",
    }:
        _fail("INVALID_EXPECTED_STATUS")
    terminal = IncidentStatus(expected_status)
    expected_verification = (
        VerificationStatus.PASSED
        if terminal is IncidentStatus.RESOLVED
        else VerificationStatus.FAILED
    )
    canonical_incident = _validate_incident(incident, terminal)
    audit = _validate_history(
        history,
        incident=canonical_incident,
        expected=terminal,
    )
    report = _validate_report(canonical_incident)
    action = _validate_action(canonical_incident, report)
    pending, approved = _validate_approvals(
        canonical_incident,
        report,
        action,
        audit,
    )
    chain = _build_demo_chain(
        canonical_incident,
        report,
        action,
        approved,
        expected_verification=expected_verification,
    )
    _validate_demo_chain(
        chain,
        audit,
        pending,
        approved,
        expected_verification=expected_verification,
    )
    action_run = _validate_action_run(
        canonical_incident,
        action,
        audit,
        chain,
    )
    verification = _validate_verification(
        canonical_incident,
        action_run,
        chain,
        audit,
        expected=expected_verification,
    )

    revision_groups = [
        {"revision": 0, "events": [_audit_event(1, audit[0])]},
        {"revision": 1, "events": [_audit_event(2, audit[1])]},
        {"revision": 2, "events": [_audit_event(3, audit[2])]},
        {
            "revision": 3,
            "events": [
                _event(
                    4,
                    occurred_at=report.created_at,
                    record_type="RCA_REPORT",
                    component="RCA_GATEWAY",
                    operation="PROPOSE_REPORT",
                    outcome=report.conclusion.value,
                ),
                _event(
                    5,
                    occurred_at=action.created_at,
                    record_type="REMEDIATION_ACTION",
                    component="GOVERNANCE_ENGINE",
                    operation="PROPOSE_ACTION",
                    outcome=action.action_type,
                ),
                _audit_event(6, audit[3]),
            ],
        },
        {
            "revision": 4,
            "events": [
                _event(
                    7,
                    occurred_at=pending.requested_at,
                    record_type="APPROVAL_DECISION",
                    component="APPROVAL_GATEWAY",
                    operation="REQUEST_NETWORK_ACTION_APPROVAL",
                    outcome=pending.status.value,
                ),
                _audit_event(8, audit[4]),
            ],
        },
        {
            "revision": 5,
            "events": [
                _event(
                    9,
                    occurred_at=approved.decided_at,
                    record_type="APPROVAL_DECISION",
                    component="APPROVAL_GATEWAY",
                    operation="DECIDE_NETWORK_ACTION_APPROVAL",
                    outcome=approved.status.value,
                ),
                _audit_event(10, audit[5]),
            ],
        },
        {
            "revision": 6,
            "events": [
                _event(
                    11,
                    occurred_at=action_run.finished_at,
                    record_type="ACTION_RUN",
                    component="SIMULATED_ACTION_GATEWAY",
                    operation="EXECUTE_LOCAL_SIMULATION",
                    outcome=action_run.status.value,
                ),
                _audit_event(12, audit[6]),
            ],
        },
        {
            "revision": 7,
            "events": [
                _event(
                    13,
                    occurred_at=verification.finished_at,
                    record_type="VERIFICATION_RUN",
                    component="LOCAL_VERIFICATION_GATEWAY",
                    operation="VERIFY_LOCAL_SIMULATION",
                    outcome=verification.status.value,
                ),
                _audit_event(14, audit[7]),
            ],
        },
    ]
    return {
        "schema": PROJECTION_SCHEMA,
        "classification": PROJECTION_CLASSIFICATION,
        "read_only": True,
        "distributed_trace": False,
        "ordering": PROJECTION_ORDERING,
        "scenario": f"LOCAL_SIMULATION_{terminal.value}",
        "terminal_status": terminal.value,
        "record_counts": {
            "incidents": 1,
            "incident_audit_events": 8,
            "rca_reports": 1,
            "remediation_actions": 1,
            "approval_decisions": 2,
            "action_runs": 1,
            "verification_runs": 1,
            "projected_events": 14,
        },
        "invariants": {
            "single_incident": True,
            "bindings_exact": True,
            "revision_contiguous": True,
            "single_execution_attempt": True,
            "side_effects": False,
        },
        "revision_groups": revision_groups,
    }


def build_lifecycle_projection(
    incident: Incident,
    history: Sequence[IncidentAuditEvent],
    *,
    expected_status: str,
) -> dict[str, object]:
    """Build one bounded, read-only projection or fail with a safe code."""

    try:
        return _build_projection(
            incident,
            history,
            expected_status=expected_status,
        )
    except LifecycleProjectionError:
        raise
    except Exception:
        raise LifecycleProjectionError("INVALID_PROJECTION_INPUT") from None


__all__ = [
    "LifecycleProjectionError",
    "PROJECTION_CLASSIFICATION",
    "PROJECTION_ORDERING",
    "PROJECTION_SCHEMA",
    "build_lifecycle_projection",
]
