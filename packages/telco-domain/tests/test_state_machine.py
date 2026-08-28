from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from telco_domain.models import (
    ActionRun,
    ActionRunStatus,
    ApprovalDecision,
    ApprovalStatus,
    EvidenceReference,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    RemediationAction,
    ResourceReference,
    RcaReport,
    VerificationRun,
    VerificationStatus,
)
from telco_domain.state_machine import (
    ACTIVE_STATUSES,
    CLOSED_STATUSES,
    InvalidTransitionError,
    InvalidTransitionUpdateError,
    RevisionConflictError,
    REOPENABLE_STATUSES,
    SETTLED_STATUSES,
    TERMINAL_STATUSES,
    TransitionGuardError,
    TransitionTimeError,
    allowed_transitions,
    can_transition,
    transition,
    transition_incident,
)


BASE_TIME = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


EXPECTED_TRANSITIONS = {
    IncidentStatus.DETECTED: {
        IncidentStatus.TRIAGED,
        IncidentStatus.DUPLICATE,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.TRIAGED: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.DUPLICATE,
        IncidentStatus.REJECTED,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.INVESTIGATING: {
        IncidentStatus.RCA_COMPLETE,
        IncidentStatus.DUPLICATE,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.RCA_COMPLETE: {
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.REJECTED,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.AWAITING_APPROVAL: {
        IncidentStatus.REMEDIATING,
        IncidentStatus.REJECTED,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.REMEDIATING: {
        IncidentStatus.VERIFYING,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.VERIFYING: {
        IncidentStatus.RESOLVED,
        IncidentStatus.REOPENED,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
    IncidentStatus.RESOLVED: {IncidentStatus.CLOSED, IncidentStatus.REOPENED},
    IncidentStatus.CLOSED: {IncidentStatus.REOPENED},
    IncidentStatus.DUPLICATE: set(),
    IncidentStatus.REJECTED: set(),
    IncidentStatus.FAILED: set(),
    IncidentStatus.CANCELLED: set(),
    IncidentStatus.REOPENED: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    },
}


def make_incident(
    status: IncidentStatus = IncidentStatus.DETECTED, revision: int = 0
) -> Incident:
    return Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=status,
        revision=revision,
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def test_transition_table_covers_every_status_exactly() -> None:
    assert set(EXPECTED_TRANSITIONS) == set(IncidentStatus)
    for status in IncidentStatus:
        assert allowed_transitions(status) == frozenset(EXPECTED_TRANSITIONS[status])


def test_public_status_sets_match_graph_semantics() -> None:
    strict_terminal = {
        status for status in IncidentStatus if not allowed_transitions(status)
    }
    assert TERMINAL_STATUSES == strict_terminal
    assert IncidentStatus.CLOSED not in TERMINAL_STATUSES
    assert IncidentStatus.CLOSED in CLOSED_STATUSES
    assert IncidentStatus.CLOSED in SETTLED_STATUSES
    assert IncidentStatus.CLOSED in REOPENABLE_STATUSES
    assert ACTIVE_STATUSES.isdisjoint(SETTLED_STATUSES)
    assert ACTIVE_STATUSES | SETTLED_STATUSES == set(IncidentStatus)


@pytest.mark.parametrize("current", list(IncidentStatus))
@pytest.mark.parametrize("target", list(IncidentStatus))
def test_can_transition_matches_explicit_cartesian_product(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    assert can_transition(current, target) is (target in EXPECTED_TRANSITIONS[current])


def test_happy_path_is_immutable_and_increments_revision_once_per_step() -> None:
    original = make_incident()
    action = secure_action("action-1")
    report = secure_report(recommendations=(action,))
    request, approval = approval_lifecycle(action, report)
    action_run = ActionRun(
        action_run_id="run-1",
        incident_id=original.incident_id,
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="execute-action-1",
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE_TIME + timedelta(minutes=5),
        finished_at=BASE_TIME + timedelta(minutes=5, seconds=30),
    )
    verification = VerificationRun(
        verification_id="verify-1",
        incident_id=original.incident_id,
        action_run_ids=(action_run.action_run_id,),
        status=VerificationStatus.PASSED,
        checks=("end-to-end traffic",),
        evidence_refs=(
            EvidenceReference(
                evidence_id="verification-evidence",
                evidence_type="TEST_RESULT",
                uri="test://verify-1",
            ),
        ),
        started_at=BASE_TIME + timedelta(minutes=6),
        finished_at=BASE_TIME + timedelta(minutes=6, seconds=30),
    )
    statuses = [
        IncidentStatus.TRIAGED,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.RCA_COMPLETE,
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.REMEDIATING,
        IncidentStatus.VERIFYING,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    ]

    current = original
    for index, status in enumerate(statuses, start=1):
        updates = {
            IncidentStatus.RCA_COMPLETE: {
                "root_cause": "UPF process unavailable",
                "rca_reports": (report,),
                "recommendations": (action,),
            },
            IncidentStatus.REMEDIATING: {"approvals": (request, approval)},
            IncidentStatus.VERIFYING: {"action_runs": (action_run,)},
            IncidentStatus.RESOLVED: {"verification_runs": (verification,)},
        }.get(status)
        current = transition_incident(
            current,
            status,
            expected_revision=index - 1,
            transitioned_at=BASE_TIME + timedelta(minutes=index),
            now=BASE_TIME + timedelta(minutes=index),
            updates=updates,
        )
        assert current.status is status
        assert current.revision == index

    assert original.status is IncidentStatus.DETECTED
    assert original.revision == 0
    assert current.updated_at == BASE_TIME + timedelta(minutes=len(statuses))


def test_transition_applies_validated_domain_updates_without_mutation() -> None:
    original = make_incident()

    updated = transition(
        original,
        "TRIAGED",
        0,
        transitioned_at=BASE_TIME + timedelta(minutes=1),
        updates={
            "severity": IncidentSeverity.HIGH,
            "title": "Cell availability degradation",
        },
    )

    assert updated.status is IncidentStatus.TRIAGED
    assert updated.severity is IncidentSeverity.HIGH
    assert updated.title == "Cell availability degradation"
    assert original.severity is IncidentSeverity.UNKNOWN
    assert original.title == ""


def test_stale_revision_is_rejected_before_transition_validation() -> None:
    incident = make_incident(status=IncidentStatus.DETECTED, revision=3)

    with pytest.raises(RevisionConflictError) as captured:
        transition_incident(
            incident,
            IncidentStatus.CLOSED,  # also illegal, but the stale write wins
            expected_revision=2,
        )

    assert captured.value.expected_revision == 2
    assert captured.value.actual_revision == 3
    assert captured.value.incident_id == "inc-1"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (IncidentStatus.DETECTED, IncidentStatus.REMEDIATING),
        (IncidentStatus.DETECTED, IncidentStatus.DETECTED),
        (IncidentStatus.DUPLICATE, IncidentStatus.REOPENED),
        (IncidentStatus.CLOSED, IncidentStatus.TRIAGED),
    ],
)
def test_illegal_and_noop_transitions_raise_custom_error(
    current: IncidentStatus, target: IncidentStatus
) -> None:
    incident = make_incident(status=current)

    with pytest.raises(InvalidTransitionError) as captured:
        transition_incident(incident, target, 0)

    assert captured.value.current_status is current
    assert captured.value.target_status is target


def test_reopened_flow_is_explicit_and_exception_states_are_terminal() -> None:
    closed = make_incident(status=IncidentStatus.CLOSED)
    reopened = transition_incident(
        closed,
        IncidentStatus.REOPENED,
        0,
        transitioned_at=BASE_TIME + timedelta(minutes=1),
    )
    investigating = transition_incident(
        reopened,
        IncidentStatus.INVESTIGATING,
        1,
        transitioned_at=BASE_TIME + timedelta(minutes=2),
    )

    assert investigating.status is IncidentStatus.INVESTIGATING
    assert investigating.revision == 2

    for terminal in (
        IncidentStatus.DUPLICATE,
        IncidentStatus.REJECTED,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    ):
        with pytest.raises(InvalidTransitionError):
            transition_incident(
                make_incident(status=terminal),
                IncidentStatus.REOPENED,
                0,
            )


def test_duplicate_transition_requires_canonical_incident_reference() -> None:
    incident = make_incident()

    with pytest.raises(TransitionGuardError, match="duplicate_of"):
        transition_incident(incident, IncidentStatus.DUPLICATE, 0)

    duplicate = transition_incident(
        incident,
        IncidentStatus.DUPLICATE,
        0,
        updates={"duplicate_of": "inc-canonical"},
    )
    assert duplicate.duplicate_of == "inc-canonical"


def test_rca_and_approval_states_have_content_guards() -> None:
    investigating = make_incident(status=IncidentStatus.INVESTIGATING)
    with pytest.raises(TransitionGuardError, match="RCA report"):
        transition_incident(investigating, IncidentStatus.RCA_COMPLETE, 0)

    report = secure_report()
    rca_complete = transition_incident(
        investigating,
        IncidentStatus.RCA_COMPLETE,
        0,
        updates={"rca_reports": (report,)},
    )
    with pytest.raises(TransitionGuardError, match="remediation action"):
        transition_incident(
            rca_complete,
            IncidentStatus.AWAITING_APPROVAL,
            1,
        )


def test_remediation_recomputes_action_hash_and_checks_scope_and_expiry() -> None:
    action = secure_action("action-approval")
    report = secure_report(recommendations=(action,))
    awaiting = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        rca_reports=(report,),
        recommendations=(action,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="matching effective approval"):
        transition_incident(
            awaiting,
            IncidentStatus.REMEDIATING,
            0,
            transitioned_at=BASE_TIME + timedelta(minutes=1),
            now=BASE_TIME + timedelta(minutes=1),
        )

    request, valid = approval_lifecycle(action, report)
    remediating = transition_incident(
        awaiting,
        IncidentStatus.REMEDIATING,
        0,
        transitioned_at=BASE_TIME + timedelta(minutes=1),
        now=BASE_TIME + timedelta(minutes=2),
        updates={"approvals": (request, valid)},
    )
    assert remediating.status is IncidentStatus.REMEDIATING


def test_verifying_and_resolved_require_success_evidence() -> None:
    action = secure_action("action-verify")
    remediating = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.REMEDIATING,
        recommendations=(action,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="every recommended action"):
        transition_incident(remediating, IncidentStatus.VERIFYING, 0)

    succeeded = ActionRun(
        action_run_id="run-1",
        incident_id="inc-1",
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="execute-action-verify",
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )
    verifying = transition_incident(
        remediating,
        IncidentStatus.VERIFYING,
        0,
        updates={"action_runs": (succeeded,)},
    )
    with pytest.raises(TransitionGuardError, match="latest VerificationRun"):
        transition_incident(verifying, IncidentStatus.RESOLVED, 1)

    passed = VerificationRun(
        verification_id="verification-1",
        incident_id="inc-1",
        action_run_ids=(succeeded.action_run_id,),
        status=VerificationStatus.PASSED,
        checks=("traffic restored",),
        evidence_refs=(
            EvidenceReference(
                evidence_id="verification-result",
                evidence_type="TEST_RESULT",
                uri="test://verification-1",
            ),
        ),
        started_at=BASE_TIME + timedelta(minutes=2),
        finished_at=BASE_TIME + timedelta(minutes=3),
    )
    resolved = transition_incident(
        verifying,
        IncidentStatus.RESOLVED,
        1,
        updates={"verification_runs": (passed,)},
    )
    assert resolved.status is IncidentStatus.RESOLVED


@pytest.mark.parametrize("field", ["incident_id", "status", "revision", "updated_at"])
def test_transition_cannot_override_protected_fields(field: str) -> None:
    incident = make_incident()

    with pytest.raises(InvalidTransitionUpdateError) as captured:
        transition_incident(
            incident,
            IncidentStatus.TRIAGED,
            0,
            updates={field: "attacker-controlled"},
        )

    assert field in captured.value.protected_fields


def test_transition_rejects_naive_or_backdated_timestamp() -> None:
    incident = make_incident()

    with pytest.raises(TransitionTimeError):
        transition_incident(
            incident,
            IncidentStatus.TRIAGED,
            0,
            transitioned_at=datetime(2026, 8, 28, 10, 1),
        )

    with pytest.raises(TransitionTimeError):
        transition_incident(
            incident,
            IncidentStatus.TRIAGED,
            0,
            transitioned_at=BASE_TIME - timedelta(seconds=1),
        )


def test_transition_normalizes_non_utc_timestamp() -> None:
    offset = timezone(timedelta(hours=8))
    incident = make_incident()

    updated = transition_incident(
        incident,
        IncidentStatus.TRIAGED,
        0,
        transitioned_at=datetime(2026, 8, 28, 18, 1, tzinfo=offset),
    )

    assert updated.updated_at == BASE_TIME + timedelta(minutes=1)
    assert updated.updated_at.tzinfo is UTC


def secure_target(resource_id: str = "upf-1") -> ResourceReference:
    return ResourceReference(resource_id=resource_id, resource_type="NETWORK_NODE")


def secure_action(action_id: str = "action-secure") -> RemediationAction:
    return RemediationAction(
        action_id=action_id,
        action_type="restart",
        target_resources=(secure_target(action_id + "-target"),),
    )


def secure_report(
    *,
    incident_id: str = "inc-1",
    report_id: str = "report-secure",
    version: int = 1,
    recommendations: tuple[RemediationAction, ...] = (),
) -> RcaReport:
    return RcaReport(
        report_id=report_id,
        incident_id=incident_id,
        version=version,
        status="PROPOSED",
        root_cause="UPF crash",
        recommendations=recommendations,
        evidence_refs=(
            EvidenceReference(
                evidence_id=f"evidence-{version}",
                evidence_type="LOG",
                uri=f"log://{incident_id}/{version}",
            ),
        ),
    )


def approval_lifecycle(
    action: RemediationAction,
    report: RcaReport,
    *,
    final_status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> tuple[ApprovalDecision, ApprovalDecision]:
    request = ApprovalDecision(
        approval_id="approval-request-event",
        request_id="approval-request-1",
        sequence=0,
        incident_id=report.incident_id,
        report_id=report.report_id,
        report_version=report.version,
        subject_id=action.action_id,
        action_hash=action.action_hash,
        scope=action.target_resources,
        status=ApprovalStatus.PENDING,
        requested_at=BASE_TIME,
        expires_at=BASE_TIME + timedelta(hours=1),
        idempotency_key="request-action",
    )
    decision = ApprovalDecision(
        approval_id="approval-decision-event",
        request_id=request.request_id,
        sequence=1,
        incident_id=report.incident_id,
        report_id=report.report_id,
        report_version=report.version,
        subject_id=action.action_id,
        action_hash=action.action_hash,
        scope=action.target_resources,
        status=final_status,
        requested_at=request.requested_at,
        decided_by="operator-1",
        decided_at=BASE_TIME + timedelta(minutes=1),
        expires_at=BASE_TIME + timedelta(hours=1),
        idempotency_key=f"decide-{final_status.value.lower()}",
    )
    return request, decision


def full_history_incident() -> Incident:
    action = secure_action("action-history")
    report = secure_report(recommendations=(action,))
    request, approved = approval_lifecycle(action, report)
    action_run = ActionRun(
        action_run_id="run-history",
        incident_id="inc-1",
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="execute-history",
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )
    verification = VerificationRun(
        verification_id="verification-history",
        incident_id="inc-1",
        action_run_ids=(action_run.action_run_id,),
        status=VerificationStatus.PASSED,
        checks=("traffic restored",),
        evidence_refs=(
            EvidenceReference(
                evidence_id="verification-history-evidence",
                evidence_type="TEST_RESULT",
                uri="test://history",
            ),
        ),
        started_at=BASE_TIME + timedelta(minutes=2),
        finished_at=BASE_TIME + timedelta(minutes=3),
    )
    evidence = EvidenceReference(
        evidence_id="incident-history-evidence",
        evidence_type="LOG",
        uri="log://history",
    )
    return Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        source_event_ids=("source-event-1",),
        status=IncidentStatus.VERIFYING,
        evidence_refs=(evidence,),
        rca_reports=(report,),
        recommendations=(action,),
        approvals=(request, approved),
        action_runs=(action_run,),
        verification_runs=(verification,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


@pytest.mark.parametrize(
    "field",
    (
        "approvals",
        "rca_reports",
        "action_runs",
        "verification_runs",
        "evidence_refs",
        "source_event_ids",
    ),
)
def test_every_history_collection_rejects_deletion_and_replacement(field: str) -> None:
    incident = full_history_incident()
    current = tuple(getattr(incident, field))

    with pytest.raises(TransitionGuardError, match="append-only"):
        transition_incident(
            incident,
            IncidentStatus.REOPENED,
            0,
            updates={field: ()},
        )

    first = current[0]
    tamper_fields = {
        "approvals": {"reason": "tampered approval"},
        "rca_reports": {"summary": "tampered report"},
        "action_runs": {"output_summary": "tampered action output"},
        "verification_runs": {"summary": "tampered verification"},
        "evidence_refs": {"summary": "tampered evidence"},
    }
    tampered = (
        first.model_copy(update=tamper_fields[field])
        if field in tamper_fields
        else "tampered-source-event"
    )
    with pytest.raises(TransitionGuardError, match="append-only"):
        transition_incident(
            incident,
            IncidentStatus.REOPENED,
            0,
            updates={field: (tampered, *current[1:])},
        )


def test_rca_complete_requires_eligible_latest_report_with_evidence() -> None:
    draft = RcaReport(
        report_id="report-draft",
        incident_id="inc-1",
        status="DRAFT",
    )
    investigating = make_incident(status=IncidentStatus.INVESTIGATING)

    with pytest.raises(TransitionGuardError, match="latest RCA report"):
        transition_incident(
            investigating,
            IncidentStatus.RCA_COMPLETE,
            0,
            updates={"rca_reports": (draft,)},
        )


def test_rca_complete_uses_latest_version_and_allows_explicit_inconclusive() -> None:
    investigating = make_incident(status=IncidentStatus.INVESTIGATING)
    older = secure_report(report_id="report-family", version=1)
    latest_draft = RcaReport(
        report_id="report-family",
        incident_id="inc-1",
        version=2,
        status="DRAFT",
        evidence_refs=(
            EvidenceReference(
                evidence_id="evidence-draft-v2",
                evidence_type="LOG",
                uri="log://draft-v2",
            ),
        ),
        root_cause="unreviewed",
    )
    with pytest.raises(TransitionGuardError, match="latest RCA report"):
        transition_incident(
            investigating,
            IncidentStatus.RCA_COMPLETE,
            0,
            updates={"rca_reports": (older, latest_draft)},
        )

    inconclusive = RcaReport(
        report_id="report-inconclusive",
        incident_id="inc-1",
        status="PROPOSED",
        conclusion="INCONCLUSIVE",
        evidence_refs=(
            EvidenceReference(
                evidence_id="evidence-inconclusive",
                evidence_type="TRACE",
                uri="trace://inconclusive",
            ),
        ),
    )
    complete = transition_incident(
        investigating,
        IncidentStatus.RCA_COMPLETE,
        0,
        updates={"rca_reports": (inconclusive,)},
    )
    assert complete.status is IncidentStatus.RCA_COMPLETE


def test_transition_histories_are_append_only_and_identity_is_protected() -> None:
    evidence = EvidenceReference(
        evidence_id="evidence-history", evidence_type="LOG", uri="log://history"
    )
    triaged = Incident(
        incident_id="inc-history",
        trace_id="trace-history",
        status=IncidentStatus.TRIAGED,
        evidence_refs=(evidence,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="append-only"):
        transition_incident(
            triaged,
            IncidentStatus.INVESTIGATING,
            0,
            updates={"evidence_refs": ()},
        )

    with pytest.raises(InvalidTransitionUpdateError):
        transition_incident(
            triaged,
            IncidentStatus.INVESTIGATING,
            0,
            updates={"detected_at": BASE_TIME + timedelta(minutes=1)},
        )


def test_verifying_requires_successful_exact_run_for_every_action() -> None:
    first = secure_action("action-1")
    second = secure_action("action-2")
    successful = ActionRun(
        action_run_id="run-1",
        incident_id="inc-1",
        action_id=first.action_id,
        action_hash=first.action_hash,
        idempotency_key="execute-action-1",
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )
    remediating = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.REMEDIATING,
        recommendations=(first, second),
        action_runs=(successful,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="every recommended action"):
        transition_incident(remediating, IncidentStatus.VERIFYING, 0)


def test_latest_action_attempt_must_succeed() -> None:
    action = secure_action("action-retried")
    succeeded = ActionRun(
        action_run_id="run-success-old",
        incident_id="inc-1",
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="execute-retry-1",
        attempt=1,
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )
    failed = ActionRun(
        action_run_id="run-failed-latest",
        incident_id="inc-1",
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="execute-retry-2",
        attempt=2,
        status=ActionRunStatus.FAILED,
        started_at=BASE_TIME + timedelta(minutes=2),
        finished_at=BASE_TIME + timedelta(minutes=3),
        error="network function did not restart",
    )
    incident = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.REMEDIATING,
        recommendations=(action,),
        action_runs=(succeeded, failed),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="every recommended action"):
        transition_incident(incident, IncidentStatus.VERIFYING, 0)


def test_resolved_requires_complete_latest_verification_covering_successful_runs() -> None:
    action = secure_action()
    successful = ActionRun(
        action_run_id="run-secure",
        incident_id="inc-1",
        action_id=action.action_id,
        action_hash=action.action_hash,
        idempotency_key="execute-secure",
        status=ActionRunStatus.SUCCEEDED,
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )
    incomplete = VerificationRun(
        verification_id="verification-incomplete",
        incident_id="inc-1",
        action_run_ids=(successful.action_run_id,),
        status=VerificationStatus.PENDING,
        started_at=BASE_TIME + timedelta(minutes=2),
        finished_at=BASE_TIME + timedelta(minutes=3),
        checks=(),
    )
    verifying = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.VERIFYING,
        recommendations=(action,),
        action_runs=(successful,),
        verification_runs=(incomplete,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="latest VerificationRun"):
        transition_incident(verifying, IncidentStatus.RESOLVED, 0)


def test_revoked_latest_approval_cannot_reuse_older_grant() -> None:
    action = secure_action()
    report = secure_report(recommendations=(action,))
    request, approved = approval_lifecycle(action, report)
    revoked = approved.model_copy(
        update={
            "approval_id": "approval-revocation-event",
            "sequence": 2,
            "status": ApprovalStatus.CANCELLED,
            "decided_at": BASE_TIME + timedelta(minutes=2),
            "idempotency_key": "cancel-action",
        }
    )
    awaiting = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        rca_reports=(report,),
        recommendations=(action,),
        approvals=(request, approved, revoked),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="effective approval"):
        transition_incident(
            awaiting,
            IncidentStatus.REMEDIATING,
            0,
            now=BASE_TIME + timedelta(minutes=3),
        )


def test_every_recommendation_requires_its_own_current_approval() -> None:
    first = secure_action("action-approved")
    second = secure_action("action-unapproved")
    report = secure_report(recommendations=(first, second))
    request, approved = approval_lifecycle(first, report)
    awaiting = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        rca_reports=(report,),
        recommendations=(first, second),
        approvals=(request, approved),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="action-unapproved"):
        transition_incident(
            awaiting,
            IncidentStatus.REMEDIATING,
            0,
            now=BASE_TIME + timedelta(minutes=2),
        )


def test_approval_uses_trusted_now_not_backdated_transition_time() -> None:
    action = secure_action("action-expired")
    report = secure_report(recommendations=(action,))
    request, approved = approval_lifecycle(action, report)
    awaiting = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        rca_reports=(report,),
        recommendations=(action,),
        approvals=(request, approved),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="effective approval"):
        transition_incident(
            awaiting,
            IncidentStatus.REMEDIATING,
            0,
            transitioned_at=BASE_TIME + timedelta(minutes=2),
            now=BASE_TIME + timedelta(hours=2),
        )


def test_recommendations_are_frozen_after_awaiting_approval() -> None:
    action = secure_action()
    report = secure_report(recommendations=(action,))
    awaiting = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        rca_reports=(report,),
        recommendations=(action,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="recommendations are frozen"):
        transition_incident(
            awaiting,
            IncidentStatus.REJECTED,
            0,
            updates={"recommendations": (secure_action("replacement"),)},
        )


def test_recommendations_are_already_frozen_when_rca_completes() -> None:
    action = secure_action("action-rca-frozen")
    report = secure_report(recommendations=(action,))
    incident = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        status=IncidentStatus.RCA_COMPLETE,
        rca_reports=(report,),
        recommendations=(action,),
        detected_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    with pytest.raises(TransitionGuardError, match="recommendations are frozen"):
        transition_incident(
            incident,
            IncidentStatus.AWAITING_APPROVAL,
            0,
            updates={"recommendations": (secure_action("replacement-rca"),)},
        )
