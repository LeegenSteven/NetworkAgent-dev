"""Bind the raw cross-version fixture to the current P2b business models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent import a2a_parts
from telco_domain import (
    ActionRun,
    ApprovalDecision,
    EvidenceReference,
    Incident,
    KpiViolation,
    RcaReport,
    RemediationAction,
    ResourceReference,
    VerificationRun,
)
from telco_assurance_agent.protocol import (
    AssuranceAnalysisRequest,
    AssuranceCandidatePage,
    AssuranceConfirmationRequest,
    AssuranceConfirmationResult,
    AssuranceError,
    AssuranceScanRequest,
)


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "legacy-0.2.16.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))


def _request_data(name: str) -> dict[str, Any]:
    message = _fixture()["requests"][name]["params"]["message"]
    return next(part["data"] for part in message["parts"] if part["kind"] == "data")


def _response_data(name: str) -> dict[str, Any]:
    result = _fixture()["responses"][name]["result"]
    if result["kind"] == "artifact-update":
        parts = result["artifact"]["parts"]
    else:
        parts = result["status"]["message"]["parts"]
    return next(part["data"] for part in parts if part["kind"] == "data")


def test_legacy_scan_confirmation_and_analysis_payloads_are_current_contracts() -> None:
    scan = AssuranceScanRequest.model_validate(_request_data("message_send"))
    confirmation = AssuranceConfirmationRequest.model_validate(
        _request_data("continuation")
    )
    analysis = AssuranceAnalysisRequest.model_validate(_request_data("analysis"))

    assert scan.window_start is scan.window_end is None
    assert confirmation.decision == "CONFIRM"
    assert confirmation.challenge_id == "challenge-" + "c" * 32
    assert analysis.incident_id == "incident-01"


def test_legacy_candidate_result_and_error_payloads_are_current_contracts() -> None:
    page = AssuranceCandidatePage.model_validate(_response_data("input_required"))
    result = AssuranceConfirmationResult.model_validate(
        _response_data("artifact_text_data")
    )
    error = AssuranceError.model_validate(_response_data("error_artifact"))

    assert len(page.candidates) == 1
    assert page.candidates[0].affected_resources[0].resource_type == "CELL"
    assert page.candidates[0].violated_kpis[0].kpi_name == "DL_bitrate"
    assert result.outcome == "created"
    assert result.incident is not None
    assert error.error_code == "INVALID_REQUEST"


def test_supervisor_incident_schema_mirror_matches_domain_models() -> None:
    """Make protocol-field drift fail before a remote payload reaches state."""

    mirrored_models = (
        (a2a_parts._INCIDENT_FIELDS, Incident),
        (a2a_parts._RESOURCE_REFERENCE_FIELDS, ResourceReference),
        (a2a_parts._KPI_VIOLATION_FIELDS, KpiViolation),
        (a2a_parts._EVIDENCE_REFERENCE_FIELDS, EvidenceReference),
        (a2a_parts._RCA_REPORT_FIELDS, RcaReport),
        (a2a_parts._REMEDIATION_FIELDS, RemediationAction),
        (a2a_parts._APPROVAL_FIELDS, ApprovalDecision),
        (a2a_parts._ACTION_RUN_FIELDS, ActionRun),
        (a2a_parts._VERIFICATION_RUN_FIELDS, VerificationRun),
    )

    for mirrored_fields, model in mirrored_models:
        assert mirrored_fields == frozenset(model.model_fields)
