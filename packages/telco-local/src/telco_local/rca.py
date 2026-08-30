"""Read-only RCA for LTE and exact-provenance controlled 5G replay."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from telco_domain import (
    DocumentRepository,
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentRepository,
    IncidentSeverity,
    KpiComparator,
    KpiViolation,
    RcaConclusion,
    RcaReport,
    RcaRequest,
    RcaResult,
    RuleRepository,
    Technology,
    TelemetryRepository,
    assert_model_safe,
)
from telco_domain.models import ReportStatus

from .rules import (
    JsonRuleRepository,
    PredicateGroup,
    RcaRule,
    RuleResolution,
    RuleResolutionStatus,
    compare_values,
    detection_matches,
    resolve_rules_for_incident,
)
from .similarity import SimilarIncident, rank_similar_incidents


RCA_ENGINE_NAME = "deterministic-lte-rca"
RCA_ENGINE_VERSION = "1.0.0"
_SEVERITY_RANK = {
    IncidentSeverity.UNKNOWN: 0,
    IncidentSeverity.INFO: 1,
    IncidentSeverity.LOW: 2,
    IncidentSeverity.MEDIUM: 3,
    IncidentSeverity.HIGH: 4,
    IncidentSeverity.CRITICAL: 5,
}
_Scalar = bool | int | float | str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_digest(value)[:32]}"


def _evidence_content_identity(item: EvidenceReference) -> dict[str, str]:
    """Bind report identity to evidence content, excluding collection time."""

    payload = item.model_dump(mode="json", exclude={"collected_at"})
    return {
        "evidence_id": item.evidence_id,
        "content_sha256": _digest(payload),
    }


def _parse_evidence_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _expected_resource_scope(incident: Incident) -> tuple[str, ...]:
    return tuple(
        _canonical_json(resource.stable_identity())
        for resource in sorted(
            incident.affected_resources,
            key=lambda item: item.resource_id,
        )
    )


def _telemetry_scope_issue(
    item: EvidenceReference,
    incident: Incident,
) -> str | None:
    raw_scope = item.attributes.get("resource_scope")
    if raw_scope is None:
        return "MISSING_RESOURCE_SCOPE"
    if not isinstance(raw_scope, Sequence) or isinstance(
        raw_scope, (str, bytes, bytearray)
    ):
        return "INVALID_RESOURCE_SCOPE"
    if not raw_scope or any(not isinstance(entry, Mapping) for entry in raw_scope):
        return "INVALID_RESOURCE_SCOPE"
    actual_scope = tuple(_canonical_json(entry) for entry in raw_scope)
    expected_scope = _expected_resource_scope(incident)
    if not expected_scope or actual_scope != expected_scope:
        return "RESOURCE_SCOPE_MISMATCH"

    raw_start = item.attributes.get("window_start")
    raw_end = item.attributes.get("window_end")
    if raw_start is None or raw_end is None:
        return "MISSING_TIME_SCOPE"
    evidence_start = _parse_evidence_time(raw_start)
    evidence_end = _parse_evidence_time(raw_end)
    if (
        evidence_start is None
        or evidence_end is None
        or evidence_end < evidence_start
    ):
        return "INVALID_TIME_SCOPE"
    if incident.window_start is None or incident.window_end is None:
        return "INCIDENT_TIME_SCOPE_MISSING"
    if (
        evidence_start < incident.window_start
        or evidence_end > incident.window_end
    ):
        return "TIME_SCOPE_MISMATCH"

    violation_windows = tuple(
        (violation.window_start, violation.window_end)
        for violation in incident.violated_kpis
        if violation.window_start is not None and violation.window_end is not None
    )
    if violation_windows and not any(
        evidence_start <= violation_end and evidence_end >= violation_start
        for violation_start, violation_end in violation_windows
    ):
        return "TIME_SCOPE_MISMATCH"
    if item.collected_at is not None and not (
        evidence_start <= item.collected_at <= evidence_end
    ):
        return "COLLECTED_AT_OUTSIDE_TIME_SCOPE"
    return None


def _validate_telemetry_evidence(
    evidence: Sequence[EvidenceReference],
    incident: Incident,
) -> tuple[tuple[EvidenceReference, ...], tuple[dict[str, str], ...]]:
    valid: list[EvidenceReference] = []
    issues: list[dict[str, str]] = []
    for index, item in enumerate(evidence):
        code = _telemetry_scope_issue(item, incident)
        if code is None:
            valid.append(item)
            continue
        issues.append(
            {
                "code": code,
                "content_sha256": _evidence_content_identity(item)[
                    "content_sha256"
                ],
                "evidence_key": f"telemetry-index-{index}",
                "evidence_type": item.evidence_type.value,
            }
        )
    return tuple(valid), tuple(issues)


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _is_scalar(value: object) -> bool:
    return isinstance(value, (bool, int, float, str)) and not (
        isinstance(value, float) and not math.isfinite(value)
    )


def _merge_fact_candidates(
    evidence: Sequence[EvidenceReference],
) -> dict[str, _Scalar]:
    """Merge facts independent of adapter ordering; omit conflicting values."""

    candidates: dict[str, dict[str, _Scalar]] = {}

    def add(name: object, value: object) -> None:
        if not isinstance(name, str) or not _is_scalar(value):
            return
        scalar = value
        candidates.setdefault(name, {})[_canonical_json(scalar)] = scalar  # type: ignore[assignment]

    for item in sorted(evidence, key=lambda ref: (ref.evidence_type.value, ref.evidence_id)):
        attributes = item.attributes
        facts = attributes.get("facts")
        if isinstance(facts, Mapping):
            for name, value in facts.items():
                add(name, value)

        outcome_counts = attributes.get("outcome_counts")
        if not isinstance(outcome_counts, Mapping):
            outcome_counts = attributes.get("connection_outcomes")
        if isinstance(outcome_counts, Mapping):
            normalized_counts: dict[str, int] = {}
            for name, count in outcome_counts.items():
                if (
                    isinstance(name, str)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                ):
                    normalized_counts[name] = count
                    add(f"outcome_count.{name.lower()}", count)
            security_failures = normalized_counts.get("FAILED_SECURITY_SETUP", 0)
            failure_count = security_failures + normalized_counts.get("FAILURE", 0)
            add("failed_security_setup_count", security_failures)
            add("failure_count", failure_count)
            if failure_count:
                add(
                    "failed_security_setup_ratio",
                    security_failures / failure_count,
                )

    return {
        name: next(iter(values.values()))
        for name, values in candidates.items()
        if len(values) == 1
    }


def _predicate_group_matches(
    group: PredicateGroup,
    facts: Mapping[str, _Scalar],
) -> bool:
    results = tuple(
        predicate.fact in facts
        and compare_values(
            facts[predicate.fact],
            predicate.comparator,
            predicate.value,
        )
        for predicate in group.predicates
    )
    return all(results) if group.operator == "ALL" else any(results)


def _matched_violation(rule: RcaRule, incident: Incident) -> KpiViolation | None:
    matching = [
        item for item in incident.violated_kpis if detection_matches(rule, item)
    ]
    if not matching:
        return None
    if rule.detection.comparator in {KpiComparator.LT, KpiComparator.LTE}:
        return min(matching, key=lambda item: (item.observed_value, item.violation_id or ""))
    if rule.detection.comparator in {KpiComparator.GT, KpiComparator.GTE}:
        return max(matching, key=lambda item: (item.observed_value, item.violation_id or ""))
    return sorted(
        matching,
        key=lambda item: (item.observed_value, item.violation_id or ""),
    )[0]


def _severity_for_rule(
    rule: RcaRule,
    facts: Mapping[str, _Scalar],
) -> IncidentSeverity:
    for case in rule.severity.cases:
        if _predicate_group_matches(case.when, facts):
            return case.severity
    return rule.severity.default


def _violation_evidence(incident: Incident) -> tuple[EvidenceReference, ...]:
    result: list[EvidenceReference] = []
    for index, violation in enumerate(
        sorted(
            incident.violated_kpis,
            key=lambda item: (item.kpi_name, item.observed_value, item.violation_id or ""),
        )
    ):
        payload = violation.model_dump(mode="json")
        digest = _digest(payload)
        unit = f" {violation.unit}" if violation.unit else ""
        result.append(
            EvidenceReference(
                evidence_id=f"metric-{digest[:32]}",
                evidence_type=EvidenceType.METRIC,
                uri=(
                    f"incident://{quote(incident.incident_id, safe='')}/"
                    f"violations/{quote(violation.kpi_name, safe='')}/{index}"
                ),
                source="canonical-incident",
                summary=(
                    f"KPI {violation.kpi_name} 的观测值为 "
                    f"{violation.observed_value:g}{unit}，规则阈值为 "
                    f"{violation.comparator.value} {violation.threshold_value:g}{unit}。"
                ),
                collected_at=violation.window_end or incident.detected_at,
                checksum_sha256=digest,
                attributes={
                    "fact_schema": "canonical-kpi-violation/1.0",
                    "facts": {
                        f"kpi.{violation.kpi_name}": violation.observed_value,
                    },
                    "kpi_name": violation.kpi_name,
                    "resource_scope": [
                        resource.stable_identity()
                        for resource in sorted(
                            incident.affected_resources,
                            key=lambda item: item.resource_id,
                        )
                    ],
                    "window_end": (
                        violation.window_end
                        or incident.window_end
                        or incident.detected_at
                    ).isoformat(),
                    "window_start": (
                        violation.window_start
                        or incident.window_start
                        or incident.detected_at
                    ).isoformat(),
                },
            )
        )
    return tuple(result)


def _rule_evidence(rule: RcaRule, collected_at: datetime) -> EvidenceReference:
    payload = rule.model_dump(mode="json")
    digest = _digest(payload)
    return EvidenceReference(
        evidence_id=f"rule-{digest[:32]}",
        evidence_type=EvidenceType.RULE,
        uri=f"rule://{quote(rule.rule_id, safe='')}/{quote(rule.version, safe='')}",
        source="local-json-rule-repository",
        summary=rule.description_zh,
        collected_at=collected_at,
        checksum_sha256=digest,
        attributes={
            "rule_id": rule.rule_id,
            "rule_version": rule.version,
            "technology": rule.technology,
        },
    )


def _deduplicate_evidence(
    evidence: Sequence[EvidenceReference],
) -> tuple[EvidenceReference, ...]:
    by_id: dict[str, EvidenceReference] = {}
    for item in evidence:
        previous = by_id.get(item.evidence_id)
        if previous is not None and previous != item:
            raise ValueError("conflicting evidence references share one evidence_id")
        by_id[item.evidence_id] = item
    return tuple(
        sorted(
            by_id.values(),
            key=lambda item: (item.evidence_type.value, item.evidence_id, item.uri),
        )
    )


async def _load_typed_rules(
    repository: RuleRepository | JsonRuleRepository,
    incident: Incident,
) -> RuleResolution:
    typed_resolution = getattr(repository, "resolve_typed", None)
    if callable(typed_resolution):
        resolved = typed_resolution(incident)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if not isinstance(resolved, RuleResolution):
            raise TypeError("resolve_typed must return RuleResolution")
        if any(
            rule.technology != incident.technology.value
            for rule in resolved.rules
        ):
            return resolve_rules_for_incident(incident, list(resolved.rules))
        return resolved

    typed_match = getattr(repository, "match_typed", None)
    if callable(typed_match):
        matched = typed_match(incident)
        if inspect.isawaitable(matched):
            matched = await matched
    else:
        matched = await repository.match(incident)
    rules = tuple(
        item if isinstance(item, RcaRule) else RcaRule.model_validate(item)
        for item in matched
    )
    applicable = tuple(
        rule
        for rule in rules
        if rule.technology == incident.technology.value
    )
    return resolve_rules_for_incident(incident, applicable)


class DeterministicRcaGateway:
    """Produce a proposed RCA artifact without writes, models, or actions."""

    def __init__(
        self,
        rule_repository: RuleRepository | JsonRuleRepository,
        telemetry_repository: TelemetryRepository,
        *,
        document_repository: DocumentRepository | None = None,
        incident_repository: IncidentRepository | None = None,
        clock: Callable[[], datetime] = _utc_now,
        evidence_limit: int = 100,
        document_limit: int = 3,
        history_scan_limit: int = 100,
        history_match_limit: int = 3,
        history_min_score: float = 0.1,
    ) -> None:
        if not 1 <= evidence_limit <= 1_000:
            raise ValueError("evidence_limit must be between 1 and 1000")
        if not 1 <= document_limit <= 10:
            raise ValueError("document_limit must be between 1 and 10")
        if not 1 <= history_scan_limit <= 1_000:
            raise ValueError("history_scan_limit must be between 1 and 1000")
        if not 1 <= history_match_limit <= 10:
            raise ValueError("history_match_limit must be between 1 and 10")
        if not 0 <= history_min_score <= 1:
            raise ValueError("history_min_score must be between 0 and 1")
        self._rule_repository = rule_repository
        self._telemetry_repository = telemetry_repository
        self._document_repository = document_repository
        self._incident_repository = incident_repository
        self._clock = clock
        self._evidence_limit = evidence_limit
        self._document_limit = document_limit
        self._history_scan_limit = history_scan_limit
        self._history_match_limit = history_match_limit
        self._history_min_score = history_min_score

    async def analyze(self, request: RcaRequest) -> RcaResult:
        """Analyze one immutable Incident snapshot and return one RcaResult."""

        if not isinstance(request, RcaRequest):
            raise TypeError("request must be an RcaRequest")
        assert_model_safe(request)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        incident = request.incident

        rule_resolution = RuleResolution(
            status=RuleResolutionStatus.NOT_APPLICABLE
        )
        telemetry: tuple[EvidenceReference, ...] = ()
        valid_telemetry: tuple[EvidenceReference, ...] = ()
        evidence_resolution_issues: tuple[dict[str, str], ...] = ()
        if incident.technology in {
            Technology.LTE,
            Technology.FIVE_G_SA,
        }:
            rule_resolution = await _load_typed_rules(
                self._rule_repository, incident
            )
        if incident.technology is Technology.LTE:
            missing_incident_scope = not incident.affected_resources
            missing_incident_window = (
                incident.window_start is None or incident.window_end is None
            )
            if missing_incident_scope or missing_incident_window:
                issue_code = (
                    "INCIDENT_RESOURCE_SCOPE_MISSING"
                    if missing_incident_scope
                    else "INCIDENT_TIME_SCOPE_MISSING"
                )
                evidence_resolution_issues = (
                    {
                        "code": issue_code,
                        "content_sha256": _digest(
                            {
                                "resource_scope": [
                                    resource.stable_identity()
                                    for resource in sorted(
                                        incident.affected_resources,
                                        key=lambda item: item.resource_id,
                                    )
                                ],
                                "window_end": (
                                    incident.window_end.isoformat()
                                    if incident.window_end is not None
                                    else None
                                ),
                                "window_start": (
                                    incident.window_start.isoformat()
                                    if incident.window_start is not None
                                    else None
                                ),
                            }
                        ),
                        "evidence_key": "incident-analysis-scope",
                        "evidence_type": "TELEMETRY",
                    },
                )
            else:
                collected = await self._telemetry_repository.collect_evidence(
                    incident,
                    window_start=incident.window_start,
                    window_end=incident.window_end,
                    limit=self._evidence_limit,
                )
                telemetry = _deduplicate_evidence(tuple(collected))
                if len(telemetry) > self._evidence_limit:
                    raise ValueError(
                        "telemetry repository exceeded the requested limit"
                    )
                for item in telemetry:
                    assert_model_safe(item)
                    if item.evidence_type not in {
                        EvidenceType.METRIC,
                        EvidenceType.TRACE,
                    }:
                        raise ValueError(
                            "telemetry repository returned non-telemetry evidence"
                        )
                valid_telemetry, evidence_resolution_issues = (
                    _validate_telemetry_evidence(telemetry, incident)
                )
        rules = rule_resolution.rules

        base_evidence = _violation_evidence(incident)
        fact_evidence = _deduplicate_evidence(
            (*base_evidence, *valid_telemetry)
        )
        available_evidence_types = {item.evidence_type for item in fact_evidence}

        hypotheses: list[str] = []
        root_causes: list[str] = []
        severities: list[IncidentSeverity] = []
        rule_evidence: list[EvidenceReference] = []
        conclusive_rule_identities: set[tuple[str, str]] = set()
        for rule in rules:
            violation = _matched_violation(rule, incident)
            if violation is None:
                continue
            hypotheses.append(rule.analysis.hypothesis_zh)
            required_types = set(rule.analysis.evidence_types)
            analysis_facts = _merge_fact_candidates(
                tuple(
                    item
                    for item in fact_evidence
                    if item.evidence_type in required_types
                )
            )
            if required_types.issubset(available_evidence_types) and _predicate_group_matches(
                rule.analysis.when, analysis_facts
            ):
                root_causes.append(rule.analysis.root_cause_zh)
                conclusive_rule_identities.add((rule.rule_id, rule.version))
            severity_facts = dict(analysis_facts)
            severity_facts["kpi_value"] = violation.observed_value
            severities.append(_severity_for_rule(rule, severity_facts))
            rule_evidence.append(_rule_evidence(rule, now))

        unique_hypotheses = tuple(dict.fromkeys(hypotheses))
        unique_root_causes = tuple(dict.fromkeys(root_causes))
        candidate_root_cause = (
            _clip("；".join(unique_root_causes), 4_096)
            if unique_root_causes
            else None
        )
        resolution_is_complete = (
            rule_resolution.status is RuleResolutionStatus.EXACT
            and bool(rules)
            and len(conclusive_rule_identities) == len(rules)
            and not evidence_resolution_issues
        )
        root_cause = candidate_root_cause if resolution_is_complete else None
        conclusion = (
            RcaConclusion.CONCLUSIVE
            if root_cause is not None
            else RcaConclusion.INCONCLUSIVE
        )
        severity = max(
            severities,
            key=lambda item: _SEVERITY_RANK[item],
            default=IncidentSeverity.UNKNOWN,
        )

        document_evidence, document_lines = await self._document_evidence(
            incident, now
        )
        history_evidence, similar_incidents = await self._history_evidence(
            incident, now
        )
        evidence = _deduplicate_evidence(
            (
                *fact_evidence,
                *rule_evidence,
                *document_evidence,
                *history_evidence,
            )
        )
        report_text = self._render_report(
            incident=incident,
            conclusion=conclusion,
            root_cause=root_cause,
            hypotheses=unique_hypotheses,
            severity=severity,
            similar_incidents=similar_incidents,
            document_lines=document_lines,
            evidence=evidence,
        )

        rule_resolution_issues = [
            issue.model_dump(mode="json")
            for issue in rule_resolution.issues
        ]
        evidence_resolution = (
            "EXACT" if not evidence_resolution_issues else "CONFLICT"
        )
        evidence_resolution_issue_payload = list(evidence_resolution_issues)
        report_identity = {
            "incident_id": incident.incident_id,
            "based_on_revision": request.based_on_revision,
            "requested_report_version": request.requested_report_version,
            "engine": RCA_ENGINE_NAME,
            "engine_version": RCA_ENGINE_VERSION,
            "rule_resolution": rule_resolution.status.value,
            "rule_resolution_issues": rule_resolution_issues,
            "evidence_resolution": evidence_resolution,
            "evidence_resolution_issues": evidence_resolution_issue_payload,
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "version": rule.version,
                    "content_sha256": _digest(rule),
                }
                for rule in rules
            ],
            "evidence": [
                _evidence_content_identity(item) for item in evidence
            ],
            "analysis": {
                "conclusion": conclusion.value,
                "root_cause": root_cause,
                "severity": severity.value,
                "hypotheses": unique_hypotheses,
            },
        }
        report = RcaReport(
            report_id=_stable_id("rca", report_identity),
            incident_id=incident.incident_id,
            version=request.requested_report_version,
            status=ReportStatus.PROPOSED,
            title=_clip(f"{incident.title or incident.description or 'LTE Incident'} RCA", 1_024),
            summary=report_text,
            hypotheses=unique_hypotheses,
            root_cause=root_cause,
            conclusion=conclusion,
            confidence=None,
            evidence_refs=evidence,
            recommendations=(),
            generated_by=f"{RCA_ENGINE_NAME}/{RCA_ENGINE_VERSION}",
            model_metadata={
                "engine": RCA_ENGINE_NAME,
                "engine_version": RCA_ENGINE_VERSION,
                "model_provider": "none",
                "severity": severity.value,
                "rule_resolution": rule_resolution.status.value,
                "rule_resolution_issues": rule_resolution_issues,
                "evidence_resolution": evidence_resolution,
                "evidence_resolution_issues": evidence_resolution_issue_payload,
                "matched_rule_ids": [rule.rule_id for rule in rules],
                "rule_versions": {
                    rule.rule_id: rule.version for rule in rules
                },
                "based_on_revision": request.based_on_revision,
            },
            created_at=now,
        )
        result = RcaResult(
            message_id=_stable_id("rca-result", report_identity),
            workflow_id=request.workflow_id,
            incident_id=request.incident_id,
            trace_id=request.trace_id,
            idempotency_key=_stable_id(
                "rca-result-key",
                {
                    "request_idempotency_key": request.idempotency_key,
                    "report": report_identity,
                },
            ),
            sent_at=now,
            request_message_id=request.message_id,
            report=report,
            based_on_revision=request.based_on_revision,
            requested_report_version=request.requested_report_version,
            summary_zh=report_text,
        )
        assert_model_safe(result)
        result.to_data_part()
        return result

    async def _document_evidence(
        self,
        incident: Incident,
        collected_at: datetime,
    ) -> tuple[tuple[EvidenceReference, ...], tuple[str, ...]]:
        if self._document_repository is None or incident.technology is not Technology.LTE:
            return (), ()
        query = _clip(
            " ".join(
                (
                    incident.title,
                    incident.description,
                    *(item.kpi_name for item in incident.violated_kpis),
                )
            ),
            2_048,
        )
        records = await self._document_repository.search(
            query or "LTE Incident",
            technology=incident.technology.value,
            limit=self._document_limit,
        )
        evidence: list[EvidenceReference] = []
        lines: list[str] = []
        for record in sorted(
            tuple(records)[: self._document_limit],
            key=lambda item: (str(item.get("uri", "")), str(item.get("title", ""))),
        ):
            assert_model_safe(record)
            uri = record.get("uri")
            if not isinstance(uri, str) or not uri:
                continue
            title = str(record.get("title") or "本地资料")
            excerpt = str(record.get("excerpt") or record.get("summary") or "")
            safe_record = {
                "uri": uri,
                "title": title,
                "excerpt": excerpt,
                "score": record.get("score"),
            }
            digest = _digest(safe_record)
            evidence.append(
                EvidenceReference(
                    evidence_id=f"document-{digest[:32]}",
                    evidence_type=EvidenceType.DOCUMENT,
                    uri=uri,
                    source="local-document-repository",
                    summary=_clip(excerpt or title, 1_024),
                    collected_at=collected_at,
                    checksum_sha256=digest,
                    attributes={
                        "title": _clip(title, 256),
                        "score": record.get("score")
                        if isinstance(record.get("score"), (int, float))
                        else None,
                    },
                )
            )
            lines.append(f"- {_clip(title, 120)}：{_clip(excerpt, 260)}")
        return tuple(evidence), tuple(lines)

    async def _history_evidence(
        self,
        incident: Incident,
        collected_at: datetime,
    ) -> tuple[tuple[EvidenceReference, ...], tuple[SimilarIncident, ...]]:
        if self._incident_repository is None or incident.technology is not Technology.LTE:
            return (), ()
        candidates = await self._incident_repository.list(
            limit=self._history_scan_limit,
            offset=0,
        )
        matches = rank_similar_incidents(
            incident,
            candidates,
            limit=self._history_match_limit,
            min_score=self._history_min_score,
        )
        evidence: list[EvidenceReference] = []
        for match in matches:
            payload = match.model_dump(mode="json")
            digest = _digest(payload)
            summary = match.summary
            if match.root_cause:
                summary = f"{summary}；历史根因：{match.root_cause}"
            evidence.append(
                EvidenceReference(
                    evidence_id=f"prior-{digest[:32]}",
                    evidence_type=EvidenceType.PRIOR_INCIDENT,
                    uri=f"incident://history/{quote(match.incident_id, safe='')}",
                    source="local-incident-repository",
                    summary=_clip(summary, 1_024),
                    collected_at=collected_at,
                    checksum_sha256=digest,
                    attributes={"similarity": match.similarity},
                )
            )
        return tuple(evidence), matches

    @staticmethod
    def _render_report(
        *,
        incident: Incident,
        conclusion: RcaConclusion,
        root_cause: str | None,
        hypotheses: Sequence[str],
        severity: IncidentSeverity,
        similar_incidents: Sequence[SimilarIncident],
        document_lines: Sequence[str],
        evidence: Sequence[EvidenceReference],
    ) -> str:
        kpis = "；".join(
            f"{item.kpi_name}={item.observed_value:g}"
            + (f" {item.unit}" if item.unit else "")
            for item in incident.violated_kpis
        ) or "暂无异常 KPI 明细"
        overview = _clip(
            f"Incident ID：{incident.incident_id}。"
            f"{incident.title or incident.description or '暂无补充描述'}。"
            f"异常 KPI：{kpis}。",
            520,
        )
        if conclusion is RcaConclusion.CONCLUSIVE:
            analysis = _clip(
                f"结论：CONCLUSIVE。Root Cause：{root_cause}。"
                + (
                    "候选假设：" + "；".join(hypotheses) + "。"
                    if hypotheses
                    else ""
                ),
                700,
            )
        else:
            analysis = _clip(
                "结论：INCONCLUSIVE。现有规则和聚合证据不足以确定 Root Cause。"
                + (
                    "已检查假设：" + "；".join(hypotheses) + "。"
                    if hypotheses
                    else "未找到适用的 LTE RCA 规则。"
                ),
                700,
            )
        history = (
            "\n".join(
                f"- {item.incident_id}（相似度 {item.similarity:.4f}）："
                f"{_clip(item.root_cause or item.summary, 180)}"
                for item in similar_incidents
            )
            or "暂无相关信息。"
        )
        internal_docs = "\n".join(document_lines) or "暂无相关信息。"
        references = (
            "\n".join(
                f"- [{item.evidence_type.value}] {item.evidence_id}：{item.uri}"
                for item in evidence[:20]
            )
            or "暂无相关信息。"
        )
        sections = (
            ("1. Incident 概述", overview),
            ("2. Root Cause Analysis", analysis),
            ("3. 严重程度", f"规则计算结果：{severity.value}。"),
            ("4. 相似历史 Incident", _clip(history, 520)),
            ("5. 内部资料", _clip(internal_docs, 520)),
            ("6. 外部资料", "Local Profile 默认关闭外部资料检索。"),
            (
                "7. 建议措施",
                "P2 只读 RCA 不生成或执行网络变更，暂无 RemediationAction。",
            ),
            ("8. 参考资料", _clip(references, 820)),
        )
        report = "\n\n".join(f"## {heading}\n{body}" for heading, body in sections)
        if len(report) > 4_096:
            raise ValueError("deterministic RCA report exceeded the contract text budget")
        return report


__all__ = [
    "DeterministicRcaGateway",
    "RCA_ENGINE_NAME",
    "RCA_ENGINE_VERSION",
]
