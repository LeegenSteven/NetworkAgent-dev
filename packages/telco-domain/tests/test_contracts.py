"""Offline security and compatibility tests for inter-agent contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from telco_domain.contracts import (
    ApprovalAuthorizationError,
    ApprovalReference,
    ApprovalRequest,
    ApprovalResult,
    ContractDecodeError,
    ContractEncodeError,
    ContractPayloadLimitError,
    IncidentTrigger,
    NetworkChangeRequest,
    RcaRequest,
    RcaResult,
    VerificationRequest,
    VerificationResult,
    parse_contract_message,
    validate_approval_reference,
)
from telco_domain.models import (
    ActionRun,
    ActionRunStatus,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalType,
    EvidenceReference,
    EvidenceType,
    Incident,
    RcaReport,
    RemediationAction,
    ResourceReference,
    ResourceType,
    VerificationRun,
    VerificationStatus,
)


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _envelope(incident_id: str = "incident-1") -> dict[str, object]:
    return {
        "message_id": "message-1",
        "workflow_id": "workflow-1",
        "incident_id": incident_id,
        "trace_id": "trace-1",
        "idempotency_key": "intent-1",
        "sent_at": NOW,
    }


def _resource(resource_id: str = "upf-1") -> ResourceReference:
    return ResourceReference(
        resource_id=resource_id,
        resource_type=ResourceType.NETWORK_NODE,
    )


def _action() -> RemediationAction:
    return RemediationAction(
        action_id="action-1",
        action_type="RESTART_NETWORK_FUNCTION",
        target_resources=(_resource(),),
        parameters={"network_function": "upf-1"},
    )


def _report(action: RemediationAction | None = None) -> RcaReport:
    recommendation = _action() if action is None else action
    return RcaReport(
        report_id="report-1",
        incident_id="incident-1",
        version=2,
        root_cause="UPF process is unavailable",
        recommendations=(recommendation,),
    )


def _incident() -> Incident:
    return Incident(incident_id="incident-1", trace_id="trace-1")


def _approval_decision(
    action: RemediationAction,
    *,
    status: ApprovalStatus,
    sequence: int,
    approval_id: str,
) -> ApprovalDecision:
    terminal = status is not ApprovalStatus.PENDING
    return ApprovalDecision(
        approval_id=approval_id,
        request_id="approval-request-1",
        sequence=sequence,
        incident_id="incident-1",
        report_id="report-1",
        report_version=2,
        subject_id=action.action_id,
        status=status,
        approval_type=ApprovalType.NETWORK_ACTION,
        action_hash=action.action_hash,
        scope=action.target_resources,
        requested_by="resolver-agent",
        requested_at=NOW - timedelta(hours=2),
        decided_by="operator-1" if terminal else None,
        decided_at=NOW - timedelta(hours=1) if terminal else None,
        expires_at=NOW + timedelta(hours=1),
        idempotency_key=f"approval-event-{sequence}",
    )


def _approval_reference(action: RemediationAction) -> ApprovalReference:
    return ApprovalReference(
        approval_id="approval-approved",
        request_id="approval-request-1",
        decision_sequence=1,
        incident_id="incident-1",
        report_id="report-1",
        report_version=2,
        subject_id=action.action_id,
        action_hash=action.action_hash,
        based_on_revision=0,
        validated_at=NOW - timedelta(minutes=5),
        validator_id="approval-gateway",
    )


def _action_run(action: RemediationAction) -> ActionRun:
    return ActionRun(
        action_run_id="action-run-1",
        incident_id="incident-1",
        action_id=action.action_id,
        action_hash=action.action_hash,
        status=ActionRunStatus.SUCCEEDED,
        idempotency_key="execute-action-1",
        started_at=NOW - timedelta(minutes=20),
        finished_at=NOW - timedelta(minutes=10),
    )


def _pending_verification(action_run: ActionRun) -> VerificationRun:
    return VerificationRun(
        verification_id="verification-1",
        incident_id="incident-1",
        action_run_ids=(action_run.action_run_id,),
        status=VerificationStatus.PENDING,
        checks=("UPF process is ready",),
    )


def _messages():
    incident = _incident()
    action = _action()
    report = _report(action)
    pending = _approval_decision(
        action,
        status=ApprovalStatus.PENDING,
        sequence=0,
        approval_id="approval-pending",
    )
    approved = _approval_decision(
        action,
        status=ApprovalStatus.APPROVED,
        sequence=1,
        approval_id="approval-approved",
    )
    action_run = _action_run(action)
    verification = _pending_verification(action_run)
    envelope = _envelope()
    return (
        IncidentTrigger(**envelope, incident=incident),
        RcaRequest(
            **envelope,
            incident=incident,
            based_on_revision=incident.revision,
            requested_report_version=report.version,
        ),
        RcaResult(
            **envelope,
            request_message_id="rca-request-message-1",
            report=report,
            based_on_revision=incident.revision,
            requested_report_version=report.version,
        ),
        ApprovalRequest(
            **envelope,
            request_id=pending.request_id,
            approval_type=ApprovalType.NETWORK_ACTION,
            report=report,
            action=action,
            scope=action.target_resources,
            based_on_revision=incident.revision,
            expires_at=pending.expires_at,
        ),
        ApprovalResult(
            **envelope,
            request_id=approved.request_id,
            decision=approved,
        ),
        NetworkChangeRequest(
            **envelope,
            action=action,
            report=report,
            based_on_revision=incident.revision,
            approval_reference=_approval_reference(action),
        ),
        VerificationRequest(
            **envelope,
            action_run=action_run,
            verification=verification,
        ),
        VerificationResult(**envelope, verification=verification),
    )


@pytest.mark.parametrize("message", _messages())
def test_contract_round_trip_is_json_safe_and_strict(message) -> None:
    data = message.to_data_part()

    json.dumps(data, ensure_ascii=False)
    assert data["schema_version"] == "1.0"
    assert data["message_type"] == message.message_type
    assert data["trace_id"] == "trace-1"
    assert "task_id" not in data

    parsed = parse_contract_message(data)
    assert type(parsed) is type(message)
    assert parsed == message
    assert type(message).model_validate_json(message.model_dump_json()) == message


def test_contract_schema_requires_business_correlation_ids() -> None:
    schema = IncidentTrigger.model_json_schema()
    required = set(schema["required"])

    assert {
        "message_id",
        "workflow_id",
        "incident_id",
        "trace_id",
        "idempotency_key",
    } <= required
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert schema["properties"]["message_type"]["const"] == "incident_trigger"
    assert "task_id" not in schema["properties"]


def test_missing_trace_id_and_transport_task_id_are_rejected() -> None:
    envelope = _envelope()
    envelope.pop("trace_id")
    with pytest.raises(ValidationError):
        IncidentTrigger(**envelope, incident=_incident())

    with pytest.raises(ValidationError):
        IncidentTrigger(
            **_envelope(),
            incident=_incident(),
            task_id="must-remain-in-transport",
        )


@pytest.mark.parametrize(
    "payload",
    (
        {"incident": {"id": "legacy-1"}},
        {"schema_version": "2.0", "message_type": "incident_trigger"},
        {"schema_version": "1.0", "message_type": "unknown_type"},
    ),
)
def test_unknown_or_legacy_wire_payload_is_rejected(payload) -> None:
    with pytest.raises(ContractDecodeError):
        parse_contract_message(payload)


def test_non_structured_payload_has_no_text_fallback() -> None:
    with pytest.raises(ContractDecodeError, match="structured mapping"):
        parse_contract_message("incident failed")  # type: ignore[arg-type]


def test_unknown_wire_field_is_rejected_without_echoing_input() -> None:
    data = _messages()[0].to_data_part()
    data["trace_id"] = ""
    data["subscriber-secret-field"] = "do-not-log-this-value"

    with pytest.raises(ContractDecodeError) as error:
        parse_contract_message(data)

    rendered = str(error.value)
    assert "do-not-log-this-value" not in rendered
    assert "subscriber-secret-field" not in rendered
    assert "<unknown_field>" in rendered
    assert error.value.__cause__ is None


def test_contract_boundaries_reject_subscriber_identifiers() -> None:
    incident = Incident(
        incident_id="incident-private",
        trace_id="trace-private",
        model_metadata={"imsi": "208930000000001"},
    )
    envelope = _envelope("incident-private")
    envelope["trace_id"] = "trace-private"
    message = IncidentTrigger(**envelope, incident=incident)
    with pytest.raises(ContractEncodeError) as outbound_error:
        message.to_data_part()
    assert "imsi" not in str(outbound_error.value).lower()
    assert "208930000000001" not in str(outbound_error.value)

    inbound = _messages()[0].to_data_part()
    inbound["incident"]["model_metadata"] = {"imsi": "208930000000001"}
    with pytest.raises(ContractDecodeError) as inbound_error:
        parse_contract_message(inbound)
    assert "imsi" not in str(inbound_error.value).lower()
    assert "208930000000001" not in str(inbound_error.value)


def test_contract_boundaries_enforce_serialized_size_and_depth_budget() -> None:
    oversized = _messages()[0].to_data_part()
    oversized["oversized"] = "x" * 300_000
    with pytest.raises(ContractPayloadLimitError, match="serialized size"):
        parse_contract_message(oversized)

    huge_incident = Incident(
        incident_id="incident-huge",
        trace_id="trace-huge",
        model_metadata={"safe_blob": "x" * 300_000},
    )
    envelope = _envelope("incident-huge")
    envelope["trace_id"] = "trace-huge"
    with pytest.raises(ContractPayloadLimitError, match="serialized size"):
        IncidentTrigger(**envelope, incident=huge_incident).to_data_part()

    deeply_nested: dict[str, object] = {"leaf": "safe"}
    for _ in range(40):
        deeply_nested = {"nested": deeply_nested}
    too_deep = _messages()[0].to_data_part()
    too_deep["nested"] = deeply_nested
    with pytest.raises(ContractPayloadLimitError, match="depth"):
        parse_contract_message(too_deep)


def test_incident_report_and_rca_revision_bindings_are_enforced() -> None:
    incident = _incident()
    report = _report()
    with pytest.raises(ValidationError, match="based_on_revision"):
        RcaRequest(
            **_envelope(),
            incident=incident,
            based_on_revision=1,
            requested_report_version=2,
        )

    with pytest.raises(ValidationError, match="requested_report_version"):
        RcaResult(
            **_envelope(),
            request_message_id="request-message",
            report=report,
            based_on_revision=0,
            requested_report_version=1,
        )


def test_network_change_uses_reference_not_wire_approval_decision() -> None:
    schema = NetworkChangeRequest.model_json_schema()["properties"]
    assert "approval_reference" in schema
    assert "approval" not in schema

    action = _action()
    report = _report(action)
    reference_data = _approval_reference(action).model_dump(mode="python")
    reference_data["incident_id"] = "another-incident"
    with pytest.raises(ValidationError, match="another incident"):
        NetworkChangeRequest(
            **_envelope(),
            action=action,
            report=report,
            based_on_revision=0,
            approval_reference=ApprovalReference.model_validate(reference_data),
        )


def test_latest_trusted_approval_decision_controls_execution() -> None:
    action = _action()
    report = _report(action)
    pending = _approval_decision(
        action,
        status=ApprovalStatus.PENDING,
        sequence=0,
        approval_id="approval-pending",
    )
    approved = _approval_decision(
        action,
        status=ApprovalStatus.APPROVED,
        sequence=1,
        approval_id="approval-approved",
    )
    reference = _approval_reference(action)

    assert (
        validate_approval_reference(
            reference,
            (pending, approved),
            action=action,
            report=report,
            trusted_now=NOW,
        )
        == approved
    )

    # Backdating sent_at/validated_at cannot revive an expired grant because the
    # gateway supplies trusted_now independently of the wire payload.
    with pytest.raises(ApprovalAuthorizationError, match="not effective"):
        validate_approval_reference(
            reference,
            (pending, approved),
            action=action,
            report=report,
            trusted_now=NOW + timedelta(hours=2),
        )

    cancelled = _approval_decision(
        action,
        status=ApprovalStatus.CANCELLED,
        sequence=2,
        approval_id="approval-cancelled",
    )
    with pytest.raises(ApprovalAuthorizationError, match="latest decision"):
        validate_approval_reference(
            reference,
            (pending, approved, cancelled),
            action=action,
            report=report,
            trusted_now=NOW,
        )


def test_approval_request_requires_exact_action_scope() -> None:
    action = _action()
    report = _report(action)
    with pytest.raises(ValidationError, match="non-empty scope"):
        ApprovalRequest(
            **_envelope(),
            request_id="approval-request",
            approval_type=ApprovalType.NETWORK_ACTION,
            report=report,
            action=action,
            scope=(),
            based_on_revision=0,
            expires_at=NOW + timedelta(hours=1),
        )


def test_verification_request_is_bound_to_succeeded_action_run() -> None:
    action = _action()
    succeeded = _action_run(action)
    pending = ActionRun(
        action_run_id="pending-run",
        action_id=action.action_id,
        status=ActionRunStatus.PENDING,
    )
    verification = _pending_verification(succeeded)
    with pytest.raises(ValidationError, match="SUCCEEDED"):
        VerificationRequest(
            **_envelope(),
            action_run=pending,
            verification=verification,
        )

    wrong_verification = VerificationRun(
        verification_id="verification-wrong",
        incident_id="incident-1",
        action_run_ids=("another-run",),
    )
    with pytest.raises(ValidationError, match="action_run_id"):
        VerificationRequest(
            **_envelope(),
            action_run=succeeded,
            verification=wrong_verification,
        )


def test_display_text_is_one_way_and_metadata_only() -> None:
    text = _messages()[0].to_display_text()
    assert "incident_trigger" in text
    assert "incident_id=incident-1" in text
    assert "trace_id=trace-1" in text
