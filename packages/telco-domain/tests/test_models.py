from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from telco_domain.models import (
    ActionRun,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalType,
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentAuditEvent,
    IncidentSeverity,
    IncidentStatus,
    KpiComparator,
    KpiViolation,
    RemediationAction,
    ReportStatus,
    ResourceReference,
    ResourceType,
    RcaReport,
    Technology,
    VerificationRun,
    VerificationStatus,
)


BASE_TIME = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


def target(resource_id: str = "node-1") -> ResourceReference:
    return ResourceReference(resource_id=resource_id, resource_type="NETWORK_NODE")


def test_incident_has_canonical_defaults_and_is_frozen() -> None:
    incident = Incident(incident_id="inc-001", trace_id="trace-001")

    assert incident.schema_version == "1.0"
    assert incident.correlation_key is None
    assert incident.trace_id == "trace-001"
    assert incident.status is IncidentStatus.DETECTED
    assert incident.severity is IncidentSeverity.UNKNOWN
    assert incident.revision == 0

    with pytest.raises(ValidationError, match="frozen"):
        incident.status = IncidentStatus.TRIAGED  # type: ignore[misc]

    with pytest.raises(ValidationError, match="literal_error"):
        Incident(
            incident_id="inc-future", trace_id="trace-future", schema_version="2.0"
        )


def test_every_datetime_is_normalized_to_utc() -> None:
    plus_eight = timezone(timedelta(hours=8))
    local_time = datetime(2026, 8, 28, 18, 0, tzinfo=plus_eight)
    incident = Incident(
        incident_id="inc-utc",
        trace_id="trace-utc",
        detected_at=local_time,
        created_at=local_time,
        updated_at=local_time,
        window_start=local_time - timedelta(minutes=5),
        window_end=local_time,
    )

    assert incident.detected_at == BASE_TIME
    assert incident.detected_at.tzinfo is UTC
    assert incident.window_end == BASE_TIME


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (Incident, {"incident_id": "i", "detected_at": datetime(2026, 1, 1)}),
        (
            EvidenceReference,
            {
                "evidence_id": "e",
                "evidence_type": EvidenceType.LOG,
                "uri": "log://e",
                "collected_at": datetime(2026, 1, 1),
            },
        ),
        (
            ApprovalDecision,
            {
                "approval_id": "p",
                "subject_id": "a",
                "requested_at": datetime(2026, 1, 1),
            },
        ),
        (
            VerificationRun,
            {"verification_id": "v", "started_at": datetime(2026, 1, 1)},
        ),
    ],
)
def test_naive_datetime_is_rejected(model: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        model(**kwargs)


def test_resource_and_evidence_accept_kind_aliases() -> None:
    resource = ResourceReference(resource_id="cell-1", kind="CELL")
    evidence = EvidenceReference(
        evidence_id="ev-1", kind="METRIC", uri="duckdb://performance/1"
    )

    assert resource.resource_type is ResourceType.CELL
    assert resource.kind is ResourceType.CELL
    assert evidence.evidence_type is EvidenceType.METRIC
    assert evidence.kind is EvidenceType.METRIC


def test_kpi_violation_validates_window_and_alias() -> None:
    violation = KpiViolation(
        kpi_name="ERAB success rate",
        observed_value=95.5,
        threshold=97,
        comparator=KpiComparator.LESS_THAN,
        window_start=BASE_TIME,
        window_end=BASE_TIME + timedelta(minutes=15),
    )

    assert violation.threshold_value == 97
    assert violation.threshold == 97

    with pytest.raises(ValidationError, match="provided together"):
        KpiViolation(
            kpi_name="Retainability",
            observed_value=4,
            threshold_value=3,
            comparator=KpiComparator.GREATER_THAN,
            window_start=BASE_TIME,
        )

    with pytest.raises(ValidationError, match="earlier"):
        KpiViolation(
            kpi_name="Retainability",
            observed_value=4,
            threshold_value=3,
            comparator=KpiComparator.GREATER_THAN,
            window_start=BASE_TIME,
            window_end=BASE_TIME - timedelta(seconds=1),
        )


def test_action_parameter_hash_is_stable_and_sensitive_to_values() -> None:
    first = RemediationAction(
        action_id="action-1",
        action_type="restart_network_function",
        parameters={"replicas": 2, "options": {"force": False, "zone": "cn-a"}},
        target_resources=(target(),),
    )
    reordered = RemediationAction(
        action_id="action-2",
        action_type="restart_network_function",
        parameters={"options": {"zone": "cn-a", "force": False}, "replicas": 2},
        target_resources=(target(),),
    )
    changed = RemediationAction(
        action_id="action-3",
        action_type="restart_network_function",
        parameters={"replicas": 3, "options": {"force": False, "zone": "cn-a"}},
        target_resources=(target(),),
    )

    assert first.parameter_hash == reordered.parameter_hash
    assert first.parameters_hash == reordered.parameters_hash
    assert first.action_hash == reordered.action_hash
    assert first.parameter_hash != changed.parameter_hash
    assert first.action_hash != changed.action_hash
    assert first.parameter_hash == hashlib.sha256(
        b'{"options":{"force":false,"zone":"cn-a"},"replicas":2}'
    ).hexdigest()


def test_action_hash_is_independent_of_target_order() -> None:
    cell = ResourceReference(resource_id="cell-1", resource_type="CELL")
    node = ResourceReference(resource_id="node-1", resource_type="NETWORK_NODE")

    first = RemediationAction(
        action_id="a-1", action_type="restart", target_resources=(cell, node)
    )
    second = RemediationAction(
        action_id="a-2", action_type="restart", target_resources=(node, cell)
    )

    assert first.compute_action_hash() == second.compute_action_hash()


def test_report_approval_and_runs_round_trip() -> None:
    action = RemediationAction(
        action_id="action-1", action_type="restart", target_resources=(target(),)
    )
    report = RcaReport(
        report_id="report-1",
        incident_id="inc-1",
        version=2,
        status=ReportStatus.PROPOSED,
        recommendations=(action,),
        created_at=BASE_TIME,
    )
    request = ApprovalDecision(
        approval_id="approval-request",
        request_id="request-1",
        sequence=0,
        incident_id="inc-1",
        report_id=report.report_id,
        report_version=report.version,
        subject_id=action.action_id,
        approval_type=ApprovalType.NETWORK_ACTION,
        action_hash=action.action_hash,
        scope=action.target_resources,
        status=ApprovalStatus.PENDING,
        requested_at=BASE_TIME,
        expires_at=BASE_TIME + timedelta(minutes=10),
        idempotency_key="request-action-1",
    )
    approval = ApprovalDecision(
        **{
            **request.model_dump(mode="python"),
            "approval_id": "approval-decision",
            "sequence": 1,
            "status": ApprovalStatus.APPROVED,
            "idempotency_key": "approve-action-1",
            "decided_by": "operator-1",
            "decided_at": BASE_TIME + timedelta(minutes=1),
        },
    )
    incident = Incident(
        incident_id="inc-1",
        trace_id="trace-1",
        rca_reports=(report,),
        recommendations=report.recommendations,
        approvals=(request, approval),
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    restored = Incident.model_validate_json(incident.model_dump_json())

    assert restored == incident
    assert restored.latest_approval_decision(request.request_id) == approval


def test_approved_decision_is_bound_and_effective_at_injected_time() -> None:
    action = RemediationAction(
        action_id="action-bound",
        action_type="restart",
        target_resources=(target(),),
    )
    report = RcaReport(report_id="report-bound", incident_id="inc-bound")

    with pytest.raises(ValidationError, match="terminal approval decisions require"):
        ApprovalDecision(
            approval_id="approval-invalid",
            request_id="request-invalid",
            sequence=1,
            incident_id="inc-bound",
            report_id=report.report_id,
            report_version=report.version,
            subject_id=action.action_id,
            status=ApprovalStatus.APPROVED,
            action_hash=action.action_hash,
            scope=action.target_resources,
            expires_at=BASE_TIME + timedelta(minutes=5),
            idempotency_key="approve-invalid",
        )

    approval = ApprovalDecision(
        approval_id="approval-valid",
        request_id="request-valid",
        sequence=1,
        incident_id="inc-bound",
        report_id=report.report_id,
        report_version=report.version,
        subject_id=action.action_id,
        status=ApprovalStatus.APPROVED,
        action_hash=action.action_hash.upper(),
        scope=action.target_resources,
        decided_by="operator-1",
        requested_at=BASE_TIME,
        decided_at=BASE_TIME + timedelta(minutes=1),
        expires_at=BASE_TIME + timedelta(minutes=5),
        idempotency_key="approve-valid",
    )

    assert approval.subject_hash == action.action_hash
    assert not approval.is_effective(BASE_TIME)
    assert approval.is_effective(BASE_TIME + timedelta(minutes=2))
    assert not approval.is_effective(BASE_TIME + timedelta(minutes=5))

    with pytest.raises(ValidationError, match="expires_at"):
        ApprovalDecision(
            approval_id="approval-expiry",
            request_id="request-expiry",
            sequence=0,
            incident_id="inc-bound",
            report_id=report.report_id,
            report_version=report.version,
            subject_id=action.action_id,
            action_hash=action.action_hash,
            scope=action.target_resources,
            expires_at=BASE_TIME,
            requested_at=BASE_TIME,
            idempotency_key="request-expiry",
        )


def test_reversible_action_requires_rollback_plan() -> None:
    with pytest.raises(ValidationError, match="rollback_plan"):
        RemediationAction(
            action_id="action-risky",
            action_type="restart",
            target_resources=(target(),),
            reversible=True,
        )


def test_incident_rca_versions_and_parent_ids_are_consistent() -> None:
    report = RcaReport(report_id="report-1", incident_id="inc-1", version=1)

    with pytest.raises(ValidationError, match="rca_reports"):
        Incident(
            incident_id="inc-1",
            trace_id="trace-1",
            rca_reports=(report, report),
        )

    with pytest.raises(ValidationError, match="incident_id"):
        Incident(
            incident_id="inc-2",
            trace_id="trace-2",
            rca_reports=(report,),
        )

    with pytest.raises(ValidationError, match="different incident"):
        Incident(incident_id="inc-1", trace_id="trace-1", duplicate_of="inc-1")


def test_audit_event_normalizes_time_and_requires_correlation_ids() -> None:
    plus_eight = timezone(timedelta(hours=8))
    event = IncidentAuditEvent(
        event_id="event-1",
        incident_id="inc-1",
        from_status=IncidentStatus.DETECTED,
        to_status=IncidentStatus.TRIAGED,
        revision=1,
        occurred_at=datetime(2026, 8, 28, 18, 0, tzinfo=plus_eight),
        idempotency_key="triage-inc-1",
        trace_id="trace-1",
        actor="resolver-agent",
        reason="triage completed",
    )

    assert event.occurred_at == BASE_TIME
    assert event.occurred_at.tzinfo is UTC


def test_timeline_and_uniqueness_invariants_are_enforced() -> None:
    with pytest.raises(ValidationError, match="earlier"):
        ActionRun(
            action_run_id="run-1",
            action_id="action-1",
            started_at=BASE_TIME,
            finished_at=BASE_TIME - timedelta(seconds=1),
        )

    with pytest.raises(ValidationError, match="earlier"):
        VerificationRun(
            verification_id="verify-1",
            status=VerificationStatus.FAILED,
            started_at=BASE_TIME,
            finished_at=BASE_TIME - timedelta(seconds=1),
        )

    with pytest.raises(ValidationError, match="source_event_ids"):
        Incident(
            incident_id="inc-dup",
            trace_id="trace-dup",
            source_event_ids=("event-1", "event-1"),
        )


def test_raw_evidence_payload_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvidenceReference(
            evidence_id="ev-1",
            evidence_type="TRACE",
            uri="trace://1",
            raw_payload={"imsi": "sensitive"},
        )


def test_incident_serialization_uses_string_enums_and_utc_iso8601() -> None:
    incident = Incident(
        incident_id="inc-json",
        trace_id="trace-json",
        technology=Technology.FIVE_G_SA,
        status=IncidentStatus.INVESTIGATING,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
        detected_at=BASE_TIME,
    )

    payload = incident.model_dump(mode="json")

    assert payload["technology"] == "5G_SA"
    assert payload["status"] == "INVESTIGATING"
    assert payload["detected_at"] == "2026-08-28T10:00:00Z"


def test_incident_requires_explicit_trace_and_does_not_infer_correlation() -> None:
    with pytest.raises(ValidationError, match="trace_id"):
        Incident(incident_id="inc-no-trace")

    incident = Incident(incident_id="inc-explicit", trace_id="trace-explicit")
    assert incident.trace_id == "trace-explicit"
    assert incident.correlation_key is None


def test_network_action_cannot_disable_approval_or_omit_targets() -> None:
    with pytest.raises(ValidationError, match="target_resources"):
        RemediationAction(action_id="action-no-target", action_type="restart")

    target = ResourceReference(resource_id="upf-1", resource_type="NETWORK_NODE")
    with pytest.raises(ValidationError, match="requires_approval"):
        RemediationAction(
            action_id="action-bypass",
            action_type="restart",
            target_resources=(target,),
            requires_approval=False,
        )


def test_nested_models_are_revalidated_after_unsafe_model_copy() -> None:
    action = RemediationAction(
        action_id="action-copied",
        action_type="restart",
        target_resources=(target(),),
    )
    crafted = action.model_copy(update={"requires_approval": False})

    with pytest.raises(ValidationError, match="requires_approval"):
        RcaReport(
            report_id="report-crafted",
            incident_id="inc-crafted",
            recommendations=(crafted,),
        )


def test_action_hash_covers_full_target_identity_and_recomputes_deep_parameters() -> None:
    target = ResourceReference(
        resource_id="cell-1",
        resource_type="CELL",
        technology="LTE",
        vendor_profile="vendor-a",
        location_id="site-1",
        parent_resource_id="enodeb-1",
        external_ids={"oss": "123"},
    )
    action = RemediationAction(
        action_id="action-full-hash",
        action_type="retune",
        target_resources=(target,),
        parameters={"nested": {"limits": [1, 2]}},
    )
    original = action.action_hash

    action.parameters["nested"]["limits"].append(3)  # type: ignore[index,union-attr]
    assert action.action_hash != original

    changed_target = target.model_copy(update={"location_id": "site-2"})
    changed = RemediationAction(
        action_id="action-full-hash-2",
        action_type="retune",
        target_resources=(changed_target,),
        parameters={"nested": {"limits": [1, 2]}},
    )
    baseline = RemediationAction(
        action_id="action-full-hash-3",
        action_type="retune",
        target_resources=(target,),
        parameters={"nested": {"limits": [1, 2]}},
    )
    assert changed.action_hash != baseline.action_hash


def test_network_approval_binds_incident_report_action_and_exact_resource_scope() -> None:
    target = ResourceReference(resource_id="upf-1", resource_type="NETWORK_NODE")
    action = RemediationAction(
        action_id="action-bound-v2",
        action_type="restart",
        target_resources=(target,),
    )
    report = RcaReport(
        report_id="report-v2",
        incident_id="inc-v2",
        version=2,
        status="PROPOSED",
        evidence_refs=(
            EvidenceReference(
                evidence_id="e-v2", evidence_type="LOG", uri="log://v2"
            ),
        ),
        root_cause="UPF crash",
    )
    approval = ApprovalDecision(
        approval_id="approval-event-2",
        request_id="approval-request-1",
        sequence=1,
        incident_id="inc-v2",
        report_id=report.report_id,
        report_version=report.version,
        subject_id=action.action_id,
        action_hash=action.action_hash,
        scope=action.target_resources,
        status=ApprovalStatus.APPROVED,
        decided_by="operator-1",
        requested_at=BASE_TIME,
        decided_at=BASE_TIME + timedelta(minutes=1),
        expires_at=BASE_TIME + timedelta(minutes=10),
        idempotency_key="approve-action-v2",
    )

    assert approval.covers_action(action, "inc-v2", report)
    assert not approval.covers_action(
        action, "another-incident", report
    )
    assert approval.is_effective(BASE_TIME + timedelta(minutes=2))


def test_terminal_approval_decisions_require_actor_and_decision_time() -> None:
    target = ResourceReference(resource_id="upf-1", resource_type="NETWORK_NODE")
    action = RemediationAction(
        action_id="action-rejected",
        action_type="restart",
        target_resources=(target,),
    )
    with pytest.raises(ValidationError, match="decided_by"):
        ApprovalDecision(
            approval_id="approval-rejected",
            request_id="request-rejected",
            sequence=1,
            incident_id="inc-1",
            report_id="report-1",
            report_version=1,
            subject_id=action.action_id,
            action_hash=action.action_hash,
            scope=action.target_resources,
            status=ApprovalStatus.REJECTED,
            expires_at=BASE_TIME + timedelta(hours=1),
            idempotency_key="reject-action",
        )


def test_run_terminal_states_require_complete_audit_binding() -> None:
    with pytest.raises(ValidationError, match="incident_id"):
        ActionRun(
            action_run_id="run-incomplete",
            action_id="action-1",
            status="SUCCEEDED",
        )

    with pytest.raises(ValidationError, match="checks"):
        VerificationRun(
            verification_id="verify-incomplete",
            incident_id="inc-1",
            action_run_ids=("run-1",),
            status="PASSED",
            started_at=BASE_TIME,
            finished_at=BASE_TIME + timedelta(minutes=1),
        )


def test_action_hash_binds_user_visible_risk_and_rollback_semantics() -> None:
    action = RemediationAction(
        action_id="action-semantics",
        action_type="restart",
        target_resources=(target(),),
        description="Restart one UPF",
        reversible=True,
        rollback_plan="Restore previous pod revision",
        expected_outcome="Traffic recovers",
    )

    assert action.action_hash != action.model_copy(
        update={"description": "Restart every UPF"}
    ).action_hash
    assert action.action_hash != action.model_copy(
        update={"risk_level": type(action.risk_level).CRITICAL}
    ).action_hash


def test_expired_approval_event_is_recorded_at_or_after_expiry() -> None:
    action = RemediationAction(
        action_id="action-expiry-event",
        target_resources=(target(),),
    )
    expired = ApprovalDecision(
        approval_id="approval-expired",
        request_id="request-expired",
        sequence=1,
        incident_id="inc-expired",
        report_id="report-expired",
        report_version=1,
        subject_id=action.action_id,
        action_hash=action.action_hash,
        scope=action.target_resources,
        status=ApprovalStatus.EXPIRED,
        requested_at=BASE_TIME,
        expires_at=BASE_TIME + timedelta(minutes=5),
        decided_at=BASE_TIME + timedelta(minutes=5),
        decided_by="approval-service",
        idempotency_key="expire-action",
    )
    assert not expired.is_effective(BASE_TIME + timedelta(minutes=6))

    with pytest.raises(ValidationError, match="at or after"):
        expired.model_validate(
            {
                **expired.model_dump(mode="python"),
                "decided_at": BASE_TIME + timedelta(minutes=4),
            }
        )


def test_incident_window_must_end_no_later_than_detection() -> None:
    with pytest.raises(ValidationError, match="detected_at"):
        Incident(
            incident_id="inc-future-window",
            trace_id="trace-future-window",
            detected_at=BASE_TIME,
            window_start=BASE_TIME - timedelta(minutes=1),
            window_end=BASE_TIME + timedelta(seconds=1),
        )
