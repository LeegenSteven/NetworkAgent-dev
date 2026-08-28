from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from telco_domain import (
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentStatus,
    KpiComparator,
    KpiViolation,
    RcaConclusion,
    RcaRequest,
    ResourceReference,
    ResourceType,
    SensitiveDataError,
    Technology,
    assert_model_safe,
)
from telco_domain.models import ReportStatus
from telco_local.rca import DeterministicRcaGateway
from telco_local.rules import (
    JsonRuleRepository,
    RcaRule,
    RuleResolution,
    RuleResolutionIssue,
    RuleResolutionIssueCode,
    RuleResolutionStatus,
    rule_content_sha256,
)


NOW = datetime(2025, 11, 24, 18, 30, tzinfo=UTC)
WINDOW_START = datetime(2025, 11, 24, 15, 0, tzinfo=UTC)
WINDOW_END = datetime(2025, 11, 24, 18, 18, 40, tzinfo=UTC)
ENODEB_ID = "lte:enodeb:1"
CELL_ID = f"{ENODEB_ID}:cell:12314"


def _resources(
    technology: Technology = Technology.LTE,
) -> tuple[ResourceReference, ...]:
    return (
        ResourceReference(
            resource_id=ENODEB_ID,
            resource_type=ResourceType.ENODEB,
            technology=technology,
        ),
        ResourceReference(
            resource_id=CELL_ID,
            resource_type=ResourceType.CELL,
            technology=technology,
            parent_resource_id=ENODEB_ID,
        ),
    )


def _resource_scope() -> list[dict[str, object]]:
    return [resource.stable_identity() for resource in _resources()]


def _rule() -> RcaRule:
    return RcaRule.model_validate(
        {
            "schema_version": "1.0",
            "rule_id": "lte.erab.security-setup",
            "version": "1.0.0",
            "technology": "LTE",
            "is_current": True,
            "description_zh": "检查 S1 安全配置失败。",
            "detection": {
                "kpi_name": "erab_success_rate",
                "comparator": "LT",
                "threshold": 97.0,
                "unit": "%",
                "max_gap_minutes": 15,
            },
            "analysis": {
                "evidence_types": ["TRACE"],
                "when": {
                    "operator": "ALL",
                    "predicates": [
                        {
                            "fact": "failed_security_setup_ratio",
                            "comparator": "GTE",
                            "value": 0.5,
                        },
                        {
                            "fact": "failure_count",
                            "comparator": "GTE",
                            "value": 1,
                        },
                    ],
                },
                "hypothesis_zh": "S1 安全配置失败可能导致 ERAB 建立异常。",
                "root_cause_zh": "失败事件主要由 S1 安全配置失败造成。",
            },
            "severity": {
                "cases": [
                    {
                        "when": {
                            "operator": "ALL",
                            "predicates": [
                                {
                                    "fact": "kpi_value",
                                    "comparator": "LT",
                                    "value": 95.0,
                                }
                            ],
                        },
                        "severity": "HIGH",
                    }
                ],
                "default": "LOW",
            },
        }
    )


def _incident(*, technology: Technology = Technology.LTE) -> Incident:
    return Incident(
        incident_id="incident-erab",
        trace_id="trace-erab",
        technology=technology,
        status=IncidentStatus.INVESTIGATING,
        title="ERAB 成功率异常",
        description="小区 ERAB 成功率低于阈值。",
        affected_resources=_resources(technology),
        detected_at=NOW,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        violated_kpis=(
            KpiViolation(
                kpi_name="erab_success_rate",
                observed_value=94.5,
                threshold_value=97.0,
                comparator=KpiComparator.LT,
                unit="%",
                rule_id="lte.erab.security-setup",
                rule_version="1.0.0",
                resource_ids=(CELL_ID,),
                window_start=WINDOW_START,
                window_end=WINDOW_END,
            ),
        ),
        rule_versions={"lte.erab.security-setup": "1.0.0"},
        created_at=NOW,
        updated_at=NOW,
        revision=2,
    )


def _request(incident: Incident | None = None) -> RcaRequest:
    incident = incident or _incident()
    return RcaRequest(
        message_id="request-1",
        workflow_id="workflow-1",
        incident_id=incident.incident_id,
        trace_id=incident.trace_id,
        idempotency_key="rca-request-1",
        sent_at=NOW,
        incident=incident,
        based_on_revision=incident.revision,
        requested_report_version=1,
    )


class RuleRepositoryStub:
    async def match_typed(self, incident: Incident):
        return (_rule(),) if incident.technology is Technology.LTE else ()


class TelemetryRepositoryStub:
    def __init__(self, evidence: tuple[EvidenceReference, ...]):
        self.evidence = evidence
        self.calls = 0

    async def collect_evidence(self, incident: Incident, **kwargs):
        self.calls += 1
        assert kwargs["window_start"] == incident.window_start
        assert kwargs["window_end"] == incident.window_end
        return self.evidence


class DocumentRepositoryStub:
    async def search(self, query: str, *, technology=None, limit=10):
        assert technology == "LTE"
        return (
            {
                "document_id": "doc-erab",
                "uri": "document://local/erab.md#0",
                "title": "erab.md",
                "excerpt": "S1 安全配置失败会影响 ERAB 建立。",
                "score": 0.9,
            },
        )


class IncidentRepositoryStub:
    def __init__(self, incidents: tuple[Incident, ...]):
        self.incidents = incidents
        self.list_calls = 0

    async def list(self, *, status=None, limit=100, offset=0):
        self.list_calls += 1
        return self.incidents


def _trace_evidence(**attribute_updates: object) -> EvidenceReference:
    attributes = {
        "facts": {
            "failed_security_setup_count": 21,
            "failure_count": 27,
            "failed_security_setup_ratio": 21 / 27,
        },
        "outcome_counts": {
            "SUCCESS": 144,
            "FAILURE": 6,
            "FAILED_SECURITY_SETUP": 21,
            "OTHER": 408,
        },
        "resource_scope": _resource_scope(),
        "window_end": WINDOW_END.isoformat(),
        "window_start": WINDOW_START.isoformat(),
    }
    attributes.update(attribute_updates)
    return EvidenceReference(
        evidence_id="trace-outcomes",
        evidence_type=EvidenceType.TRACE,
        uri="evidence://local/trace-outcomes",
        source="local-duckdb",
        summary="S1 结果聚合：安全配置失败 21/27。",
        collected_at=WINDOW_END,
        attributes=attributes,
    )


def _metric_evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id="metric-erab",
        evidence_type=EvidenceType.METRIC,
        uri="evidence://local/metric-erab",
        source="local-duckdb",
        summary="ERAB 成功率为 94.5%。",
        collected_at=WINDOW_END,
        attributes={
            "facts": {"erab_success_rate": 94.5},
            "resource_scope": _resource_scope(),
            "window_end": WINDOW_END.isoformat(),
            "window_start": WINDOW_START.isoformat(),
        },
    )


def test_gateway_builds_safe_proposed_conclusive_report_without_writes() -> None:
    prior = Incident(
        incident_id="prior-erab",
        trace_id="prior-trace",
        technology=Technology.LTE,
        status=IncidentStatus.RCA_COMPLETE,
        description="历史 ERAB 建立失败",
        root_cause="S1 安全配置失败",
        detected_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    telemetry = TelemetryRepositoryStub((_trace_evidence(), _metric_evidence()))
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        telemetry,
        document_repository=DocumentRepositoryStub(),
        incident_repository=IncidentRepositoryStub((prior,)),
        clock=lambda: NOW,
    )
    request = _request()

    result = asyncio.run(gateway.analyze(request))

    assert result.report.status is ReportStatus.PROPOSED
    assert result.report.conclusion is RcaConclusion.CONCLUSIVE
    assert result.report.root_cause == "失败事件主要由 S1 安全配置失败造成。"
    assert result.report.recommendations == ()
    assert result.report.model_metadata["severity"] == "HIGH"
    assert result.based_on_revision == request.based_on_revision
    assert result.requested_report_version == request.requested_report_version
    assert request.incident.status is IncidentStatus.INVESTIGATING
    assert telemetry.calls == 1
    assert {item.evidence_type for item in result.report.evidence_refs} >= {
        EvidenceType.METRIC,
        EvidenceType.TRACE,
        EvidenceType.RULE,
        EvidenceType.DOCUMENT,
        EvidenceType.PRIOR_INCIDENT,
    }
    for heading in (
        "1. Incident 概述",
        "2. Root Cause Analysis",
        "3. 严重程度",
        "4. 相似历史 Incident",
        "5. 内部资料",
        "6. 外部资料",
        "7. 建议措施",
        "8. 参考资料",
    ):
        assert heading in result.report.summary
    assert result.summary_zh == result.report.summary
    assert_model_safe(result)


def test_gateway_is_byte_stable_with_injected_clock() -> None:
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((_metric_evidence(), _trace_evidence())),
        clock=lambda: NOW,
    )
    request = _request()

    first = asyncio.run(gateway.analyze(request))
    second = asyncio.run(gateway.analyze(request))

    assert first.model_dump_json() == second.model_dump_json()


def test_gateway_returns_explicit_inconclusive_when_required_facts_are_missing() -> None:
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((_metric_evidence(),)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert "证据不足" in result.report.summary
    assert result.report.recommendations == ()


def test_trace_rule_never_consumes_same_named_metric_facts() -> None:
    metric = EvidenceReference(
        evidence_id="metric-spoofed-trace-facts",
        evidence_type=EvidenceType.METRIC,
        uri="evidence://local/metric-spoofed-trace-facts",
        collected_at=WINDOW_END,
        attributes={
            "facts": {
                "failed_security_setup_ratio": 1.0,
                "failure_count": 1,
            },
            "resource_scope": _resource_scope(),
            "window_end": WINDOW_END.isoformat(),
            "window_start": WINDOW_START.isoformat(),
        },
    )
    empty_trace = EvidenceReference(
        evidence_id="trace-empty",
        evidence_type=EvidenceType.TRACE,
        uri="evidence://local/trace-empty",
        collected_at=WINDOW_END,
        attributes={
            "resource_scope": _resource_scope(),
            "window_end": WINDOW_END.isoformat(),
            "window_start": WINDOW_START.isoformat(),
        },
    )
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((metric, empty_trace)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None


def test_gateway_marks_foreign_resource_evidence_inconclusive() -> None:
    foreign_enodeb = ResourceReference(
        resource_id="lte:enodeb:999",
        resource_type=ResourceType.ENODEB,
        technology=Technology.LTE,
    )
    foreign_cell = ResourceReference(
        resource_id="lte:enodeb:999:cell:99999",
        resource_type=ResourceType.CELL,
        technology=Technology.LTE,
        parent_resource_id=foreign_enodeb.resource_id,
    )
    foreign = _trace_evidence(
        resource_scope=[
            foreign_enodeb.stable_identity(),
            foreign_cell.stable_identity(),
        ]
    ).model_copy(
        update={
            "evidence_id": "foreign-trace",
            "uri": "evidence://local/foreign-enodeb-999",
        }
    )
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((foreign,)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert result.report.model_metadata["evidence_resolution"] == "CONFLICT"
    issue = result.report.model_metadata["evidence_resolution_issues"][0]
    assert {key: issue[key] for key in ("code", "evidence_key", "evidence_type")} == {
        "code": "RESOURCE_SCOPE_MISMATCH",
        "evidence_key": "telemetry-index-0",
        "evidence_type": "TRACE",
    }
    assert len(issue["content_sha256"]) == 64
    assert "999" not in json.dumps(
        result.report.model_metadata["evidence_resolution_issues"]
    )
    assert "foreign-trace" not in {
        item.evidence_id for item in result.report.evidence_refs
    }
    assert all(
        item.uri != "evidence://local/foreign-enodeb-999"
        for item in result.report.evidence_refs
    )
    assert "foreign-enodeb-999" not in result.report.summary


def test_gateway_marks_missing_resource_scope_inconclusive() -> None:
    scoped = _trace_evidence()
    attributes = dict(scoped.attributes)
    attributes.pop("resource_scope")
    missing = scoped.model_copy(update={"attributes": attributes})
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((missing,)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.model_metadata["evidence_resolution_issues"][0][
        "code"
    ] == "MISSING_RESOURCE_SCOPE"


@pytest.mark.parametrize(
    ("incident_updates", "expected_code"),
    [
        ({"affected_resources": ()}, "INCIDENT_RESOURCE_SCOPE_MISSING"),
        (
            {"window_start": None, "window_end": None},
            "INCIDENT_TIME_SCOPE_MISSING",
        ),
    ],
)
def test_gateway_fails_closed_before_unscoped_telemetry_query(
    incident_updates: dict[str, object],
    expected_code: str,
) -> None:
    payload = _incident().model_dump(mode="python", round_trip=True)
    payload.update(incident_updates)
    incident = Incident.model_validate(payload)
    telemetry = TelemetryRepositoryStub((_trace_evidence(),))
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(), telemetry, clock=lambda: NOW
    )

    result = asyncio.run(gateway.analyze(_request(incident)))

    assert telemetry.calls == 0
    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert result.report.model_metadata["evidence_resolution"] == "CONFLICT"
    issue = result.report.model_metadata["evidence_resolution_issues"][0]
    assert issue["code"] == expected_code
    assert issue["evidence_key"] == "incident-analysis-scope"
    assert issue["evidence_type"] == "TELEMETRY"
    assert len(issue["content_sha256"]) == 64


@pytest.mark.parametrize(
    ("updates", "removed_attribute", "expected_code"),
    [
        ({"resource_scope": "not-a-scope"}, None, "INVALID_RESOURCE_SCOPE"),
        ({}, "window_start", "MISSING_TIME_SCOPE"),
        ({"window_end": "not-a-time"}, None, "INVALID_TIME_SCOPE"),
    ],
)
def test_gateway_rejects_malformed_scope_and_time_metadata(
    updates: dict[str, object],
    removed_attribute: str | None,
    expected_code: str,
) -> None:
    scoped = _trace_evidence(**updates)
    if removed_attribute is not None:
        attributes = dict(scoped.attributes)
        attributes.pop(removed_attribute)
        scoped = scoped.model_copy(update={"attributes": attributes})
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((scoped,)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert result.report.model_metadata["evidence_resolution"] == "CONFLICT"
    issue = result.report.model_metadata["evidence_resolution_issues"][0]
    assert {key: issue[key] for key in ("code", "evidence_key", "evidence_type")} == {
        "code": expected_code,
        "evidence_key": "telemetry-index-0",
        "evidence_type": "TRACE",
    }
    assert len(issue["content_sha256"]) == 64


def test_gateway_marks_stale_evidence_window_inconclusive() -> None:
    stale_start = WINDOW_START - timedelta(days=30)
    stale_end = WINDOW_END - timedelta(days=30)
    stale = _trace_evidence(
        window_start=stale_start.isoformat(),
        window_end=stale_end.isoformat(),
    ).model_copy(update={"collected_at": stale_end})
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((stale,)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert result.report.model_metadata["evidence_resolution_issues"][0][
        "code"
    ] == "TIME_SCOPE_MISMATCH"


def test_gateway_returns_inconclusive_for_non_lte_without_running_telemetry() -> None:
    telemetry = TelemetryRepositoryStub(())
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(), telemetry, clock=lambda: NOW
    )

    result = asyncio.run(
        gateway.analyze(_request(_incident(technology=Technology.FIVE_G_SA)))
    )

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert telemetry.calls == 0


def test_gateway_rejects_unsafe_evidence_instead_of_leaking_it() -> None:
    unsafe = _trace_evidence(imsi="208930000000001")
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((unsafe,)),
        clock=lambda: NOW,
    )

    with pytest.raises(SensitiveDataError) as error:
        asyncio.run(gateway.analyze(_request()))

    assert "208930000000001" not in str(error.value)


def test_conflicting_fact_values_do_not_depend_on_repository_order() -> None:
    one = _trace_evidence()
    two = EvidenceReference(
        evidence_id="trace-conflict",
        evidence_type=EvidenceType.TRACE,
        uri="evidence://local/trace-conflict",
        attributes={
            "facts": {
                "failed_security_setup_ratio": 0.1,
                "failure_count": 27,
            },
            "resource_scope": _resource_scope(),
            "window_end": WINDOW_END.isoformat(),
            "window_start": WINDOW_START.isoformat(),
        },
        collected_at=WINDOW_END,
    )

    first = DeterministicRcaGateway(
        RuleRepositoryStub(), TelemetryRepositoryStub((one, two)), clock=lambda: NOW
    )
    second = DeterministicRcaGateway(
        RuleRepositoryStub(), TelemetryRepositoryStub((two, one)), clock=lambda: NOW
    )

    first_result = asyncio.run(first.analyze(_request()))
    second_result = asyncio.run(second.analyze(_request()))

    assert first_result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert first_result.model_dump_json() == second_result.model_dump_json()


def test_redelivery_message_id_does_not_change_logical_rca_identity() -> None:
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((_trace_evidence(), _metric_evidence())),
        clock=lambda: NOW,
    )
    first_request = _request()
    second_payload = first_request.model_dump(mode="python", round_trip=True)
    second_payload.update(
        message_id="request-redelivery",
        sent_at=NOW + timedelta(seconds=1),
    )
    second_request = RcaRequest.model_validate(second_payload)

    first = asyncio.run(gateway.analyze(first_request))
    second = asyncio.run(gateway.analyze(second_request))

    assert first.report.report_id == second.report.report_id
    assert first.message_id == second.message_id
    assert first.idempotency_key == second.idempotency_key
    assert first.request_message_id == first_request.message_id
    assert second.request_message_id == second_request.message_id


def test_changed_evidence_changes_content_bound_rca_identity() -> None:
    class MutableTelemetry:
        def __init__(self) -> None:
            self.evidence = (_trace_evidence(),)

        async def collect_evidence(self, incident: Incident, **kwargs):
            return self.evidence

    telemetry = MutableTelemetry()
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(), telemetry, clock=lambda: NOW
    )

    first = asyncio.run(gateway.analyze(_request()))
    telemetry.evidence = (
        _trace_evidence(
            facts={
                "failed_security_setup_count": 1,
                "failure_count": 27,
                "failed_security_setup_ratio": 0.1,
            }
        ),
    )
    second = asyncio.run(gateway.analyze(_request()))

    assert first.report.conclusion is RcaConclusion.CONCLUSIVE
    assert second.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert first.report.report_id != second.report.report_id
    assert first.message_id != second.message_id


def test_changed_rule_resolution_issue_changes_content_bound_identity() -> None:
    class ResolutionRepository:
        def __init__(self) -> None:
            self.code = RuleResolutionIssueCode.RULE_VERSION_NOT_FOUND

        async def resolve_typed(self, incident: Incident) -> RuleResolution:
            return RuleResolution(
                status=RuleResolutionStatus.CONFLICT,
                issues=(
                    RuleResolutionIssue(
                        violation_key="violation-index-0",
                        code=self.code,
                        rule_id="lte.erab.security-setup",
                        rule_version="1.0.0",
                    ),
                ),
            )

    repository = ResolutionRepository()
    gateway = DeterministicRcaGateway(
        repository,
        TelemetryRepositoryStub((_trace_evidence(),)),
        clock=lambda: NOW,
    )

    first = asyncio.run(gateway.analyze(_request()))
    repository.code = RuleResolutionIssueCode.UNIT_MISMATCH
    second = asyncio.run(gateway.analyze(_request()))

    assert first.report.model_metadata != second.report.model_metadata
    assert first.report.report_id != second.report.report_id
    assert first.message_id != second.message_id


def test_gateway_uses_exact_historical_rule_version(tmp_path) -> None:
    historical = _rule().model_dump(mode="json")
    historical["is_current"] = False
    current = json.loads(json.dumps(historical))
    current.update(version="2.0.0", is_current=True)
    current["analysis"]["root_cause_zh"] = "新版本根因，不得重解释历史 Incident。"
    current["detection"]["threshold"] = 90.0
    (tmp_path / "historical.json").write_text(
        json.dumps(historical, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "current.json").write_text(
        json.dumps(current, ensure_ascii=False), encoding="utf-8"
    )
    gateway = DeterministicRcaGateway(
        JsonRuleRepository(tmp_path),
        TelemetryRepositoryStub((_trace_evidence(),)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request()))

    assert result.report.conclusion is RcaConclusion.CONCLUSIVE
    assert result.report.root_cause == _rule().analysis.root_cause_zh
    assert result.report.model_metadata["rule_resolution"] == "EXACT"
    assert result.report.model_metadata["rule_versions"] == {
        "lte.erab.security-setup": "1.0.0"
    }


def test_gateway_refuses_same_version_rule_content_drift(tmp_path) -> None:
    original = _rule()
    changed = original.model_dump(mode="json", round_trip=True)
    changed["analysis"]["hypothesis_zh"] = "同版本下被替换的假设。"
    changed["analysis"]["root_cause_zh"] = "同版本下被替换的根因。"
    changed["severity"]["default"] = "MEDIUM"
    (tmp_path / "changed.json").write_text(
        json.dumps(changed, ensure_ascii=False),
        encoding="utf-8",
    )
    incident_payload = _incident().model_dump(mode="python", round_trip=True)
    incident_payload["model_metadata"] = {
        "detector_algorithm": "deterministic-threshold-episodes-v3",
        "rule_content_hashes": {
            original.rule_id: rule_content_sha256(original),
        },
    }
    incident = Incident.model_validate(incident_payload)
    gateway = DeterministicRcaGateway(
        JsonRuleRepository(tmp_path),
        TelemetryRepositoryStub((_trace_evidence(),)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request(incident)))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert result.report.model_metadata["rule_resolution"] == "CONFLICT"
    assert result.report.model_metadata["rule_resolution_issues"][0]["code"] == (
        "RULE_CONTENT_MISMATCH"
    )


def test_gateway_marks_legacy_provenance_and_refuses_current_fallback() -> None:
    incident = _incident()
    violation_payload = incident.violated_kpis[0].model_dump(
        mode="python", round_trip=True
    )
    violation_payload.update(rule_id=None, rule_version=None)
    incident_payload = incident.model_dump(mode="python", round_trip=True)
    incident_payload.update(
        violated_kpis=(EvidenceFreeViolation := KpiViolation.model_validate(violation_payload),),
        rule_versions={},
    )
    legacy = Incident.model_validate(incident_payload)
    assert EvidenceFreeViolation.rule_id is None
    gateway = DeterministicRcaGateway(
        RuleRepositoryStub(),
        TelemetryRepositoryStub((_trace_evidence(),)),
        clock=lambda: NOW,
    )

    result = asyncio.run(gateway.analyze(_request(legacy)))

    assert result.report.conclusion is RcaConclusion.INCONCLUSIVE
    assert result.report.root_cause is None
    assert result.report.model_metadata["rule_resolution"] == (
        "LEGACY_UNVERSIONED"
    )
    assert result.report.model_metadata["matched_rule_ids"] == []
