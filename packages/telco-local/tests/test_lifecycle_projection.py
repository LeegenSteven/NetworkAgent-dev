"""Fail-closed tests for the canonical local lifecycle projection."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from telco_domain import (
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentAuditEvent,
    IncidentStatus,
    InMemoryIncidentRepository,
    RcaConclusion,
    RcaReport,
    RcaResult,
    ResourceReference,
    ResourceType,
)
from telco_domain.models import ReportStatus
from telco_local import LifecycleProjectionError, build_lifecycle_projection
from telco_local.governance import LocalGovernanceEngine
from telco_local.rca import RCA_ENGINE_NAME, RCA_ENGINE_VERSION


NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
EXPECTED_GROUP_SIZES = (1, 1, 1, 3, 2, 2, 2, 2)
EXPECTED_SCHEMA = "networkagent-local-lifecycle-projection/1.0"
EXPECTED_CLASSIFICATION = "DERIVED_FROM_DURABLE_CANONICAL_RECORDS"
VERIFICATION_MISMATCH = "VERIFICATION_CONTRACT_MISMATCH"
TOP_LEVEL_KEYS = {
    "schema",
    "classification",
    "read_only",
    "distributed_trace",
    "ordering",
    "scenario",
    "terminal_status",
    "record_counts",
    "invariants",
    "revision_groups",
}
EVENT_KEYS = {
    "sequence",
    "occurred_at",
    "record_type",
    "component",
    "operation",
    "outcome",
}
EXPECTED_EVENT_NODES = (
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "DETECTED",
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "TRIAGED",
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "INVESTIGATING",
    ),
    ("RCA_REPORT", "RCA_GATEWAY", "PROPOSE_REPORT", "CONCLUSIVE"),
    (
        "REMEDIATION_ACTION",
        "GOVERNANCE_ENGINE",
        "PROPOSE_ACTION",
        "LOCAL_SIMULATION",
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "RCA_COMPLETE",
    ),
    (
        "APPROVAL_DECISION",
        "APPROVAL_GATEWAY",
        "REQUEST_NETWORK_ACTION_APPROVAL",
        "PENDING",
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "AWAITING_APPROVAL",
    ),
    (
        "APPROVAL_DECISION",
        "APPROVAL_GATEWAY",
        "DECIDE_NETWORK_ACTION_APPROVAL",
        "APPROVED",
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "REMEDIATING",
    ),
    (
        "ACTION_RUN",
        "SIMULATED_ACTION_GATEWAY",
        "EXECUTE_LOCAL_SIMULATION",
        "SUCCEEDED",
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        "VERIFYING",
    ),
    (
        "VERIFICATION_RUN",
        "LOCAL_VERIFICATION_GATEWAY",
        "VERIFY_LOCAL_SIMULATION",
        None,
    ),
    (
        "INCIDENT_AUDIT_EVENT",
        "INCIDENT_REPOSITORY",
        "RECORD_STATE_TRANSITION",
        None,
    ),
)


class RcaGatewayStub:
    async def analyze(self, request):
        report = RcaReport(
            report_id=f"report-{request.incident_id}",
            incident_id=request.incident_id,
            version=request.requested_report_version,
            status=ReportStatus.PROPOSED,
            title="Local deterministic RCA",
            summary="Bounded local result",
            root_cause="privacy-canary-root-cause",
            conclusion=RcaConclusion.CONCLUSIVE,
            evidence_refs=(
                EvidenceReference(
                    evidence_id=f"evidence-{request.incident_id}",
                    evidence_type=EvidenceType.TEST_RESULT,
                    uri="local-private://privacy-canary-evidence-uri",
                    source="privacy-canary-source",
                    collected_at=NOW,
                ),
            ),
            generated_by=f"{RCA_ENGINE_NAME}/{RCA_ENGINE_VERSION}",
            model_metadata={
                "engine": RCA_ENGINE_NAME,
                "engine_version": RCA_ENGINE_VERSION,
                "model_provider": "none",
                "severity": "HIGH",
                "rule_resolution": "EXACT",
                "rule_resolution_issues": [],
                "evidence_resolution": "EXACT",
                "evidence_resolution_issues": [],
                "matched_rule_ids": ["privacy-canary-rule"],
                "rule_versions": {"privacy-canary-rule": "1.0"},
                "based_on_revision": request.based_on_revision,
            },
            created_at=NOW,
        )
        return RcaResult(
            message_id=f"result-{request.incident_id}",
            workflow_id=request.workflow_id,
            incident_id=request.incident_id,
            trace_id=request.trace_id,
            idempotency_key=f"result-key-{request.incident_id}",
            sent_at=NOW,
            request_message_id=request.message_id,
            report=report,
            based_on_revision=request.based_on_revision,
            requested_report_version=request.requested_report_version,
        )


def _completed_lifecycle(*, terminal: str = "RESOLVED"):
    async def scenario():
        repository = InMemoryIncidentRepository(clock=lambda: NOW)
        incident_id = "privacy-canary-incident-id"
        digest = hashlib.sha256(incident_id.encode("utf-8")).hexdigest()[:16]
        incident = Incident(
            incident_id=incident_id,
            trace_id=f"local-stack-confirm-trace-{digest}",
            title="privacy-canary-title",
            affected_resources=(
                ResourceReference(
                    resource_id="privacy-canary-resource-id",
                    resource_type=ResourceType.CELL,
                ),
            ),
            detected_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            rule_versions={"privacy-canary-rule": "1.0"},
            model_metadata={
                "detector_algorithm": "deterministic-threshold-episodes-v3",
                "rule_content_hashes": {
                    "privacy-canary-rule": "a" * 64,
                },
            },
        )
        await repository.create(
            incident,
            idempotency_key=f"local-stack-confirm-key-{digest}",
            actor="local-stack-operator",
            reason="explicit local demo incident confirmation",
            trace_id=incident.trace_id,
        )
        engine = LocalGovernanceEngine(
            repository,
            RcaGatewayStub(),
            clock=lambda: NOW,
        )
        prepared = await engine.prepare(
            incident.incident_id,
            idempotency_key=f"local-stack-prepare-key-{digest}",
            actor="local-governance",
        )
        assert prepared.action is not None
        decided = await engine.decide(
            incident.incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="local-stack-operator",
            reason="approved fixed isolated local simulation",
            idempotency_key=f"local-stack-decision-key-{digest}",
        )
        completed = await engine.execute(
            incident.incident_id,
            idempotency_key=f"local-stack-execute-key-{digest}",
            actor="local-simulator",
            verification_passed=terminal == "RESOLVED",
        )
        assert decided.incident.status is IncidentStatus.REMEDIATING
        history = tuple(await repository.history(incident.incident_id))
        return completed.incident, history

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    ("terminal", "verification", "scenario"),
    [
        ("RESOLVED", "PASSED", "LOCAL_SIMULATION_RESOLVED"),
        ("REOPENED", "FAILED", "LOCAL_SIMULATION_REOPENED"),
    ],
)
def test_builds_exact_revision_grouped_projection(
    terminal: str,
    verification: str,
    scenario: str,
) -> None:
    incident, history = _completed_lifecycle(terminal=terminal)
    incident_before = incident.model_dump(mode="json")
    history_before = tuple(event.model_dump(mode="json") for event in history)

    projection = build_lifecycle_projection(
        incident,
        history,
        expected_status=terminal,
    )

    assert set(projection) == TOP_LEVEL_KEYS
    assert projection["schema"] == EXPECTED_SCHEMA
    assert projection["classification"] == EXPECTED_CLASSIFICATION
    assert projection["read_only"] is True
    assert projection["distributed_trace"] is False
    assert projection["ordering"] == "REVISION_GROUPED_ATOMIC_PROJECTION"
    assert projection["scenario"] == scenario
    assert projection["terminal_status"] == terminal
    assert projection["record_counts"] == {
        "incidents": 1,
        "incident_audit_events": 8,
        "rca_reports": 1,
        "remediation_actions": 1,
        "approval_decisions": 2,
        "action_runs": 1,
        "verification_runs": 1,
        "projected_events": 14,
    }
    assert projection["invariants"] == {
        "single_incident": True,
        "bindings_exact": True,
        "revision_contiguous": True,
        "single_execution_attempt": True,
        "side_effects": False,
    }
    groups = projection["revision_groups"]
    assert [group["revision"] for group in groups] == list(range(8))
    actual_group_sizes = [len(group["events"]) for group in groups]
    assert actual_group_sizes == list(EXPECTED_GROUP_SIZES)
    events = [event for group in groups for event in group["events"]]
    assert [event["sequence"] for event in events] == list(range(1, 15))
    assert all(set(event) == EVENT_KEYS for event in events)
    expected_nodes = list(EXPECTED_EVENT_NODES)
    expected_nodes[-2] = (*expected_nodes[-2][:3], verification)
    expected_nodes[-1] = (*expected_nodes[-1][:3], terminal)
    actual_nodes = [
        (
            event["record_type"],
            event["component"],
            event["operation"],
            event["outcome"],
        )
        for event in events
    ]
    assert actual_nodes == expected_nodes
    assert events[-2]["record_type"] == "VERIFICATION_RUN"
    assert events[-2]["outcome"] == verification
    assert events[-1]["outcome"] == terminal
    assert incident.model_dump(mode="json") == incident_before
    history_after = tuple(event.model_dump(mode="json") for event in history)
    assert history_after == history_before


def test_projection_is_an_allowlisted_privacy_safe_view() -> None:
    incident, history = _completed_lifecycle()

    projection = build_lifecycle_projection(
        incident,
        history,
        expected_status="RESOLVED",
    )

    serialized = json.dumps(projection, sort_keys=True)
    assert "privacy-canary" not in serialized
    forbidden_keys = {
        "incident_id",
        "event_id",
        "report_id",
        "action_id",
        "approval_id",
        "request_id",
        "action_run_id",
        "verification_id",
        "trace_id",
        "correlation_id",
        "scenario_projection_id",
        "idempotency_key",
        "action_hash",
        "resource",
        "root_cause",
        "evidence_uri",
        "actor",
        "reason",
        "path",
        "environment",
        "stdout",
        "stderr",
        "labels",
    }

    def all_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from all_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from all_keys(child)

    assert forbidden_keys.isdisjoint(all_keys(projection))
    group_keys = (set(group) for group in projection["revision_groups"])
    assert all(keys == {"revision", "events"} for keys in group_keys)


def test_timestamps_are_attributes_not_monotonic_ordering_claims() -> None:
    incident, history = _completed_lifecycle()
    report = incident.rca_reports[0].model_copy(
        update={"created_at": datetime(2026, 8, 31, 12, 0, tzinfo=UTC)}
    )
    action = incident.recommendations[0].model_copy(
        update={"created_at": datetime(2026, 8, 31, 8, 0, tzinfo=UTC)}
    )
    report = report.model_copy(update={"recommendations": (action,)})
    incident = incident.model_copy(
        update={"rca_reports": (report,), "recommendations": (action,)}
    )

    projection = build_lifecycle_projection(
        incident,
        history,
        expected_status="RESOLVED",
    )

    rca_events = projection["revision_groups"][3]["events"]
    assert rca_events[0]["occurred_at"] > rca_events[1]["occurred_at"]
    assert projection["ordering"] == "REVISION_GROUPED_ATOMIC_PROJECTION"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing_history", "HISTORY_CONTRACT_MISMATCH"),
        ("duplicate_history", "HISTORY_CONTRACT_MISMATCH"),
        ("wrong_revision", "HISTORY_CONTRACT_MISMATCH"),
        ("wrong_status", "HISTORY_CONTRACT_MISMATCH"),
        ("wrong_audit_binding", "HISTORY_CONTRACT_MISMATCH"),
        ("wrong_report_status", "REPORT_CONTRACT_MISMATCH"),
        ("missing_report", "REPORT_CONTRACT_MISMATCH"),
        ("duplicate_report", "REPORT_CONTRACT_MISMATCH"),
        ("missing_action", "ACTION_CONTRACT_MISMATCH"),
        ("wrong_action", "ACTION_CONTRACT_MISMATCH"),
        ("missing_approval", "APPROVAL_CONTRACT_MISMATCH"),
        ("wrong_approval_sequence", "APPROVAL_CONTRACT_MISMATCH"),
        ("wrong_approval_binding", "APPROVAL_CONTRACT_MISMATCH"),
        ("non_positive_approval_ttl", "APPROVAL_CONTRACT_MISMATCH"),
        ("missing_action_run", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("duplicate_action_run", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("wrong_action_run_binding", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("wrong_execution_attempt", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("action_side_effect", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("missing_verification", "VERIFICATION_CONTRACT_MISMATCH"),
        ("wrong_verification_binding", "VERIFICATION_CONTRACT_MISMATCH"),
        ("wrong_verification", "VERIFICATION_CONTRACT_MISMATCH"),
        ("verification_side_effect", "VERIFICATION_CONTRACT_MISMATCH"),
    ],
)
def test_projection_fails_closed_for_invalid_durable_graph(
    mutation: str,
    code: str,
) -> None:
    incident, original_history = _completed_lifecycle()
    history = original_history
    if mutation == "missing_history":
        history = history[:-1]
    elif mutation == "duplicate_history":
        history = (*history, history[-1])
    elif mutation == "wrong_revision":
        history = (
            history[0],
            history[1].model_copy(update={"revision": 7}),
            *history[2:],
        )
    elif mutation == "wrong_status":
        history = (
            *history[:2],
            history[2].model_copy(update={"to_status": IncidentStatus.FAILED}),
            *history[3:],
        )
    elif mutation == "wrong_audit_binding":
        other_incident = "privacy-canary-other"
        first = history[0].model_copy(update={"incident_id": other_incident})
        history = (
            first,
            *history[1:],
        )
    elif mutation == "wrong_report_status":
        report = incident.rca_reports[0].model_copy(
            update={"status": ReportStatus.DRAFT}
        )
        incident = incident.model_copy(update={"rca_reports": (report,)})
    elif mutation == "missing_report":
        incident = incident.model_copy(update={"rca_reports": ()})
    elif mutation == "duplicate_report":
        incident = incident.model_copy(
            update={"rca_reports": (incident.rca_reports[0],) * 2}
        )
    elif mutation == "missing_action":
        incident = incident.model_copy(update={"recommendations": ()})
    elif mutation == "wrong_action":
        action = incident.recommendations[0].model_copy(
            update={"action_type": "NETWORK_WRITE"}
        )
        report = incident.rca_reports[0].model_copy(
            update={"recommendations": (action,)}
        )
        incident = incident.model_copy(
            update={"recommendations": (action,), "rca_reports": (report,)}
        )
    elif mutation == "missing_approval":
        incident = incident.model_copy(update={"approvals": ()})
    elif mutation == "wrong_approval_sequence":
        approvals = (
            incident.approvals[0].model_copy(update={"sequence": 1}),
            incident.approvals[1],
        )
        incident = incident.model_copy(update={"approvals": approvals})
    elif mutation == "wrong_approval_binding":
        approvals = (
            incident.approvals[0].model_copy(
                update={"report_id": "privacy-canary-other-report"}
            ),
            incident.approvals[1],
        )
        incident = incident.model_copy(update={"approvals": approvals})
    elif mutation == "non_positive_approval_ttl":
        expires_at = incident.approvals[0].requested_at
        approvals = tuple(
            approval.model_copy(update={"expires_at": expires_at})
            for approval in incident.approvals
        )
        incident = incident.model_copy(update={"approvals": approvals})
    elif mutation == "missing_action_run":
        incident = incident.model_copy(update={"action_runs": ()})
    elif mutation == "duplicate_action_run":
        incident = incident.model_copy(
            update={"action_runs": (incident.action_runs[0],) * 2}
        )
    elif mutation == "wrong_action_run_binding":
        run = incident.action_runs[0].model_copy(
            update={"action_id": "privacy-canary-other-action"}
        )
        incident = incident.model_copy(update={"action_runs": (run,)})
    elif mutation == "wrong_execution_attempt":
        run = incident.action_runs[0].model_copy(update={"attempt": 2})
        incident = incident.model_copy(update={"action_runs": (run,)})
    elif mutation == "action_side_effect":
        run = incident.action_runs[0].model_copy(
            update={"metadata": {"mode": "simulation", "side_effects": True}}
        )
        incident = incident.model_copy(update={"action_runs": (run,)})
    elif mutation == "missing_verification":
        incident = incident.model_copy(update={"verification_runs": ()})
    elif mutation == "wrong_verification_binding":
        verification = incident.verification_runs[0].model_copy(
            update={"action_run_ids": ("privacy-canary-other-run",)}
        )
        runs = (verification,)
        incident = incident.model_copy(update={"verification_runs": runs})
    elif mutation == "wrong_verification":
        verification = incident.verification_runs[0].model_copy(
            update={"status": "FAILED"}
        )
        runs = (verification,)
        incident = incident.model_copy(update={"verification_runs": runs})
    elif mutation == "verification_side_effect":
        metadata = dict(incident.verification_runs[0].metadata)
        metadata["side_effects"] = True
        verification = incident.verification_runs[0].model_copy(
            update={"metadata": metadata}
        )
        runs = (verification,)
        incident = incident.model_copy(update={"verification_runs": runs})

    with pytest.raises(LifecycleProjectionError) as captured:
        build_lifecycle_projection(
            incident,
            history,
            expected_status="RESOLVED",
        )

    assert captured.value.code == code
    assert str(captured.value) == code
    assert "privacy-canary" not in str(captured.value)


@pytest.mark.parametrize(
    ("attack", "code"),
    [
        ("verification_metadata_injection", "VERIFICATION_CONTRACT_MISMATCH"),
        ("verification_evidence_side_effect", VERIFICATION_MISMATCH),
        ("verification_evidence_side_effect_zero", VERIFICATION_MISMATCH),
        ("verification_arbitrary_checks", "VERIFICATION_CONTRACT_MISMATCH"),
        ("verification_external_log", "VERIFICATION_CONTRACT_MISMATCH"),
        ("verification_changed_summary", "VERIFICATION_CONTRACT_MISMATCH"),
        ("verification_evidence_id_forgery", VERIFICATION_MISMATCH),
        ("verification_invalid_fingerprint", "VERIFICATION_CONTRACT_MISMATCH"),
        ("rca_missing_root_cause", "REPORT_CONTRACT_MISMATCH"),
        ("rca_missing_evidence", "REPORT_CONTRACT_MISMATCH"),
        ("rca_wrong_based_revision", "REPORT_CONTRACT_MISMATCH"),
        ("rca_policy_side_effect", "REPORT_CONTRACT_MISMATCH"),
        ("rca_policy_side_effect_zero", "REPORT_CONTRACT_MISMATCH"),
        ("incident_marker_network_mode", "INCIDENT_CONTRACT_MISMATCH"),
        ("incident_marker_fingerprint_forgery", "INCIDENT_CONTRACT_MISMATCH"),
        ("pending_audit_key_mismatch", "APPROVAL_CONTRACT_MISMATCH"),
        ("pending_requested_by_forgery", "APPROVAL_CONTRACT_MISMATCH"),
        ("approved_audit_key_mismatch", "APPROVAL_CONTRACT_MISMATCH"),
        ("approved_reason_forgery", "APPROVAL_CONTRACT_MISMATCH"),
        ("approval_ttl_too_long", "APPROVAL_CONTRACT_MISMATCH"),
        ("action_run_audit_key_mismatch", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("action_run_changed_output", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("action_run_side_effect_zero", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("coordinated_pending_key_forgery", "APPROVAL_CONTRACT_MISMATCH"),
        ("coordinated_action_key_forgery", "ACTION_RUN_CONTRACT_MISMATCH"),
        ("audit_actor_reason_forgery", "DEMO_CHAIN_CONTRACT_MISMATCH"),
    ],
)
def test_rejects_attacks_that_remain_valid_complete_domain_models(
    attack: str,
    code: str,
) -> None:
    incident, original_history = _completed_lifecycle()
    payload = incident.model_dump(mode="python", round_trip=True)
    history_payloads = []
    for event in original_history:
        dumped = event.model_dump(mode="python", round_trip=True)
        history_payloads.append(dumped)
    verification = payload["verification_runs"][0]
    report = payload["rca_reports"][0]
    if attack == "verification_metadata_injection":
        verification["metadata"]["real_network_side_effects"] = True
    elif attack == "verification_evidence_side_effect":
        evidence = verification["evidence_refs"][0]
        evidence["attributes"]["side_effects"] = True
    elif attack == "verification_evidence_side_effect_zero":
        evidence = verification["evidence_refs"][0]
        evidence["attributes"]["side_effects"] = 0
    elif attack == "verification_arbitrary_checks":
        verification["checks"] = ("privacy-canary-network-write",)
    elif attack == "verification_external_log":
        evidence = verification["evidence_refs"][0]
        evidence["evidence_type"] = "LOG"
        evidence["source"] = "privacy-canary-external-source"
        evidence["uri"] = "https://privacy-canary.invalid/evidence"
    elif attack == "verification_changed_summary":
        verification["summary"] = "privacy-canary-unsafe-summary"
    elif attack == "verification_evidence_id_forgery":
        evidence = verification["evidence_refs"][0]
        evidence["evidence_id"] = "privacy-canary-forged-evidence"
    elif attack == "verification_invalid_fingerprint":
        verification["metadata"]["request_fingerprint"] = "privacy-canary"
    elif attack == "rca_missing_root_cause":
        report["root_cause"] = None
        payload["root_cause"] = None
    elif attack == "rca_missing_evidence":
        report["evidence_refs"] = ()
    elif attack == "rca_wrong_based_revision":
        report["model_metadata"]["based_on_revision"] = 1
    elif attack == "rca_policy_side_effect":
        policy = report["model_metadata"]["local_simulation_policy"]
        policy["side_effects"] = True
    elif attack == "rca_policy_side_effect_zero":
        policy = report["model_metadata"]["local_simulation_policy"]
        policy["side_effects"] = 0
    elif attack == "incident_marker_network_mode":
        marker = payload["model_metadata"]["local_governance"]
        marker["mode"] = "network"
    elif attack == "incident_marker_fingerprint_forgery":
        marker = payload["model_metadata"]["local_governance"]
        marker["request_fingerprint"] = "f" * 64
    elif attack == "pending_audit_key_mismatch":
        forged = "privacy-canary-pending-key"
        payload["approvals"][0]["idempotency_key"] = forged
    elif attack == "pending_requested_by_forgery":
        payload["approvals"][0]["requested_by"] = "privacy-canary-requester"
    elif attack == "approved_audit_key_mismatch":
        forged = "privacy-canary-approved-key"
        payload["approvals"][1]["idempotency_key"] = forged
    elif attack == "approved_reason_forgery":
        payload["approvals"][1]["reason"] = "privacy-canary-approval"
    elif attack == "approval_ttl_too_long":
        requested_at = payload["approvals"][0]["requested_at"]
        expires_at = requested_at + timedelta(seconds=901)
        payload["approvals"][0]["expires_at"] = expires_at
        payload["approvals"][1]["expires_at"] = expires_at
    elif attack == "action_run_audit_key_mismatch":
        forged = "privacy-canary-action-run-key"
        payload["action_runs"][0]["idempotency_key"] = forged
    elif attack == "action_run_changed_output":
        changed_output = "Real network state was changed."
        payload["action_runs"][0]["output_summary"] = changed_output
    elif attack == "action_run_side_effect_zero":
        payload["action_runs"][0]["metadata"]["side_effects"] = 0
    elif attack == "coordinated_pending_key_forgery":
        forged = "privacy-canary-coordinated-pending-key"
        payload["approvals"][0]["idempotency_key"] = forged
        history_payloads[4]["idempotency_key"] = forged
    elif attack == "coordinated_action_key_forgery":
        forged = "privacy-canary-coordinated-action-key"
        payload["action_runs"][0]["idempotency_key"] = forged
        history_payloads[6]["idempotency_key"] = forged
    elif attack == "audit_actor_reason_forgery":
        history_payloads[6]["actor"] = "privacy-canary-actor"
        history_payloads[6]["reason"] = "privacy-canary-reason"

    attacked = Incident.model_validate(payload)
    history = tuple(
        IncidentAuditEvent.model_validate(event) for event in history_payloads
    )

    with pytest.raises(LifecycleProjectionError) as captured:
        build_lifecycle_projection(
            attacked,
            history,
            expected_status="RESOLVED",
        )

    assert captured.value.code == code
    assert str(captured.value) == code
    assert "privacy-canary" not in str(captured.value)


@pytest.mark.parametrize("expected_status", ["PASSED", "resolved", "", None])
def test_rejects_invalid_expected_status_without_echo(expected_status) -> None:
    incident, history = _completed_lifecycle()

    with pytest.raises(LifecycleProjectionError) as captured:
        build_lifecycle_projection(
            incident,
            history,
            expected_status=expected_status,
        )

    assert captured.value.code == "INVALID_EXPECTED_STATUS"
    assert str(captured.value) == "INVALID_EXPECTED_STATUS"


def test_projection_api_is_exported() -> None:
    from telco_local import (
        LifecycleProjectionError as ExportedError,
        build_lifecycle_projection as exported_builder,
    )

    assert ExportedError is LifecycleProjectionError
    assert exported_builder is build_lifecycle_projection
