"""Deterministic LTE threshold Detector built above framework-neutral ports."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from telco_domain import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentTrigger,
    KpiObservation,
    KpiViolation,
    ResourceReference,
    SensitiveDataError,
    Technology,
    assert_model_safe,
)
from telco_domain.ports import IncidentRepository

from .config import LocalProfileConfig
from .incident_repository import DuckDbIncidentRepository
from .rules import (
    JsonRuleRepository,
    RcaRule,
    compare_values,
    rule_content_sha256,
)
from .telemetry import DuckDbTelemetryRepository, MAX_QUERY_OBSERVATIONS


Clock = Callable[[], datetime]
MAX_CURRENT_RULES = 32
MAX_EPISODE_SAMPLES = 1_000
MAX_SCAN_CANDIDATES = 100


class DetectorCapacityError(RuntimeError):
    """A bounded local scan would exceed its safe response/evidence budget."""


def _stable_digest(*parts: object) -> str:
    encoded = json.dumps(
        parts,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_identifier(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > 256:
        raise ValueError(f"{name} exceeds 256 characters")
    _assert_privacy_safe(normalized, boundary=name)
    return normalized


def _require_text(name: str, value: str, *, max_length: int) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    _assert_privacy_safe(normalized, boundary=name)
    return normalized


def _assert_privacy_safe(value: object, *, boundary: str) -> None:
    try:
        assert_model_safe(value)
    except SensitiveDataError:
        raise SensitiveDataError(
            f"{boundary} rejected by privacy policy"
        ) from None


def _resource_identity(
    resources: Sequence[ResourceReference],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (resource.resource_type.value, resource.resource_id)
        for resource in resources
    )


def _deduplicate_resources(
    resources: Sequence[ResourceReference],
) -> tuple[ResourceReference, ...]:
    result: list[ResourceReference] = []
    seen: set[tuple[str, str]] = set()
    for resource in resources:
        identity = (resource.resource_type.value, resource.resource_id)
        if identity not in seen:
            seen.add(identity)
            result.append(resource)
    return tuple(result)


def _episodes(
    observations: Sequence[KpiObservation],
    *,
    max_gap_minutes: int,
) -> tuple[tuple[KpiObservation, ...], ...]:
    ordered = tuple(
        sorted(
            observations,
            key=lambda item: (item.observed_at, item.observation_id),
        )
    )
    if not ordered:
        return ()
    result: list[tuple[KpiObservation, ...]] = []
    current: list[KpiObservation] = [ordered[0]]
    for observation in ordered[1:]:
        gap_minutes = (
            observation.observed_at - current[-1].observed_at
        ).total_seconds() / 60
        if gap_minutes > max_gap_minutes:
            result.append(tuple(current))
            current = []
        current.append(observation)
    result.append(tuple(current))
    return tuple(result)


class LocalDetector:
    """Scan LTE KPI observations and commit only revalidated confirmations."""

    def __init__(
        self,
        config: LocalProfileConfig,
        *,
        rule_repository: JsonRuleRepository | None = None,
        incident_repository: IncidentRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rules = rule_repository or JsonRuleRepository(config.rules_dir)
        self._telemetry = DuckDbTelemetryRepository(config, clock=self._clock)
        self._incidents = incident_repository or DuckDbIncidentRepository(
            config, clock=self._clock
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("detector clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_rule_observation(
        rule: RcaRule,
        observation: KpiObservation,
    ) -> bool:
        if observation.kpi_name != rule.detection.kpi_name:
            return False
        if observation.unit != rule.detection.unit:
            raise ValueError(
                f"KPI unit mismatch for rule {rule.rule_id!r}"
            )
        if set(observation.quality_flags) - {"SOURCE_TIMEZONE_ASSUMED_UTC"}:
            return False
        return compare_values(
            observation.observed_value,
            rule.detection.comparator,
            rule.detection.threshold,
        )

    async def _trigger_for_episode(
        self,
        *,
        rule: RcaRule,
        episode: tuple[KpiObservation, ...],
        trace_id: str,
        workflow_id: str,
    ) -> IncidentTrigger:
        resources = _deduplicate_resources(episode[0].resources)
        start = episode[0].observed_at
        end = episode[-1].observed_at
        resource_identity = _resource_identity(resources)
        episode_digest = _stable_digest(
            "detector-episode-v1",
            rule.rule_id,
            rule.version,
            resource_identity,
            start.isoformat(),
        )
        correlation_key = f"correlation-{episode_digest}"
        source_event_ids = tuple(
            observation.observation_id for observation in episode
        )
        values = tuple(item.observed_value for item in episode)
        observed_value = math.fsum(values) / len(values)
        quality_flags = sorted(
            {flag for item in episode for flag in item.quality_flags}
        )
        violation_resource_ids = tuple(
            resource.resource_id for resource in resources
        )
        rule_content_digest = rule_content_sha256(rule)
        detection_snapshot = {
            "rule": {
                "rule_id": rule.rule_id,
                "version": rule.version,
                "content_sha256": rule_content_digest,
                "kpi_name": rule.detection.kpi_name,
                "comparator": rule.detection.comparator.value,
                "threshold": rule.detection.threshold,
                "unit": rule.detection.unit,
                "max_gap_minutes": rule.detection.max_gap_minutes,
            },
            "resources": [
                resource.stable_identity() for resource in resources
            ],
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "observations": [
                {
                    "observation_id": observation.observation_id,
                    "observed_at": observation.observed_at.isoformat(),
                    "observed_value": observation.observed_value,
                    "quality_flags": list(observation.quality_flags),
                }
                for observation in episode
            ],
            "source_event_ids": list(source_event_ids),
        }
        violation_digest = _stable_digest(
            "kpi-violation-v2",
            {
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "kpi_name": rule.detection.kpi_name,
                "comparator": rule.detection.comparator.value,
                "threshold": rule.detection.threshold,
                "unit": rule.detection.unit,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "source_event_ids": list(source_event_ids),
                "observed_value": observed_value,
                "resource_ids": list(violation_resource_ids),
                "aggregation": "arithmetic_mean_of_violating_samples",
                "max_gap_minutes": rule.detection.max_gap_minutes,
                "quality_flags": quality_flags,
                "sample_count": len(episode),
            },
        )
        violation = KpiViolation(
            violation_id=f"violation-{violation_digest}",
            kpi_name=rule.detection.kpi_name,
            observed_value=observed_value,
            threshold_value=rule.detection.threshold,
            comparator=rule.detection.comparator,
            unit=rule.detection.unit,
            window_start=start,
            window_end=end,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            resource_ids=violation_resource_ids,
            dimensions={
                "aggregation": "arithmetic_mean_of_violating_samples",
                "max_gap_minutes": str(rule.detection.max_gap_minutes),
                "quality_flags": ",".join(quality_flags),
                "sample_count": str(len(episode)),
            },
        )
        evidence_scope_digest = _stable_digest(
            "detector-evidence-scope-v3", detection_snapshot
        )
        incident = Incident(
            incident_id=f"preview-{evidence_scope_digest}",
            correlation_key=correlation_key,
            source_event_ids=source_event_ids,
            technology=Technology.LTE,
            status=IncidentStatus.DETECTED,
            severity=IncidentSeverity.UNKNOWN,
            title=f"{rule.detection.kpi_name} KPI 异常",
            description=rule.description_zh,
            affected_resources=resources,
            detected_at=end,
            window_start=start,
            window_end=end,
            violated_kpis=(violation,),
            rule_versions={rule.rule_id: rule.version},
            trace_id=trace_id,
            created_at=end,
            updated_at=end,
            model_metadata={
                "detector_algorithm": "deterministic-threshold-episodes-v3",
                "rule_content_hashes": {
                    rule.rule_id: rule_content_digest,
                },
            },
        )
        evidence = tuple(
            sorted(
                await self._telemetry.collect_evidence(incident),
                key=lambda item: (
                    item.evidence_type.value,
                    item.evidence_id,
                    item.uri,
                ),
            )
        )
        candidate_digest = _stable_digest(
            "detector-candidate-v3",
            detection_snapshot,
            {
                "evidence_refs": [
                    item.model_dump(mode="json", round_trip=True)
                    for item in evidence
                ]
            },
        )
        incident_id = f"incident-{candidate_digest}"
        payload = incident.model_dump(mode="python", round_trip=True)
        payload["incident_id"] = incident_id
        payload["evidence_refs"] = evidence
        incident = Incident.model_validate(payload)
        message_id = str(uuid.uuid4())
        envelope_idempotency_key = str(uuid.uuid4())
        return IncidentTrigger(
            message_id=message_id,
            workflow_id=workflow_id,
            incident_id=incident_id,
            trace_id=trace_id,
            idempotency_key=envelope_idempotency_key,
            sent_at=self._now(),
            incident=incident,
            summary_zh=(
                f"发现 {rule.detection.kpi_name} 异常："
                f"{start.isoformat()} 至 {end.isoformat()}，"
                f"共 {len(episode)} 个违规采样。"
            ),
        )

    async def scan(
        self,
        trace_id: str,
        *,
        workflow_id: str | None = None,
    ) -> tuple[IncidentTrigger, ...]:
        """Read telemetry and return deterministic, uncommitted candidates."""

        normalized_trace_id = _require_identifier("trace_id", trace_id)
        normalized_workflow_id = (
            _require_identifier("workflow_id", workflow_id)
            if workflow_id is not None
            else str(uuid.uuid4())
        )
        if normalized_workflow_id == normalized_trace_id:
            raise ValueError("workflow_id and trace_id must be independent")
        rules = tuple(
            rule
            for rule in self._rules.load_all()
            if rule.technology == Technology.LTE.value
        )
        if len(rules) > MAX_CURRENT_RULES:
            raise DetectorCapacityError(
                f"detector rule capacity exceeded {MAX_CURRENT_RULES}"
            )
        if not rules:
            return ()
        kpi_names = tuple(
            sorted({rule.detection.kpi_name for rule in rules})
        )
        observations = tuple(
            await self._telemetry.query_kpis(
                kpi_names=kpi_names,
                technology=Technology.LTE,
            )
        )
        if len(observations) == MAX_QUERY_OBSERVATIONS:
            raise DetectorCapacityError(
                "detector observation capacity reached "
                f"{MAX_QUERY_OBSERVATIONS}; the scan may be incomplete, so "
                "use a bounded time window or paginated scan"
            )

        episode_specs: list[tuple[RcaRule, tuple[KpiObservation, ...]]] = []
        for rule in rules:
            matched = tuple(
                observation
                for observation in observations
                if self._validate_rule_observation(rule, observation)
            )
            grouped: dict[
                tuple[tuple[str, str], ...], list[KpiObservation]
            ] = {}
            for observation in matched:
                grouped.setdefault(
                    _resource_identity(observation.resources), []
                ).append(observation)
            for resource_identity in sorted(grouped):
                for episode in _episodes(
                    grouped[resource_identity],
                    max_gap_minutes=rule.detection.max_gap_minutes,
                ):
                    if len(episode) > MAX_EPISODE_SAMPLES:
                        raise DetectorCapacityError(
                            "detector episode capacity exceeded "
                            f"{MAX_EPISODE_SAMPLES} samples; narrow the "
                            "telemetry time window"
                        )
                    episode_specs.append((rule, episode))
                    if len(episode_specs) > MAX_SCAN_CANDIDATES:
                        raise DetectorCapacityError(
                            "detector candidate capacity exceeded "
                            f"{MAX_SCAN_CANDIDATES}; narrow the telemetry "
                            "time window"
                        )

        # Capacity is validated for the entire response before the first N+1
        # evidence query, so an over-budget scan fails closed without partial
        # candidate construction.
        triggers = [
            await self._trigger_for_episode(
                rule=rule,
                episode=episode,
                trace_id=normalized_trace_id,
                workflow_id=normalized_workflow_id,
            )
            for rule, episode in episode_specs
        ]
        result = tuple(
            sorted(
                triggers,
                key=lambda item: (
                    item.incident.window_start,
                    item.incident.window_end,
                    item.incident_id,
                ),
            )
        )
        _assert_privacy_safe(result, boundary="detector result")
        return result

    async def confirm(
        self,
        candidate_id: str,
        *,
        trace_id: str,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> Incident:
        """Rescan and atomically create/correlate one server-owned candidate."""

        normalized_candidate_id = _require_identifier(
            "candidate_id", candidate_id
        )
        normalized_trace_id = _require_identifier("trace_id", trace_id)
        normalized_idempotency_key = _require_identifier(
            "idempotency_key", idempotency_key
        )
        normalized_actor = _require_identifier("actor", actor)
        normalized_reason = _require_text(
            "reason", reason, max_length=4_096
        )
        replay = await self._incidents.find_by_idempotency_key(
            normalized_candidate_id,
            normalized_idempotency_key,
            operation="create_or_correlate",
        )
        selected_incident: Incident | None = None
        if replay is not None:
            if replay.incident_id == normalized_candidate_id:
                if (
                    replay.status is not IncidentStatus.DETECTED
                    or replay.revision != 0
                    or replay.window_end is None
                    or replay.detected_at != replay.window_end
                ):
                    raise ValueError(
                        "candidate replay cannot reconstruct the original snapshot"
                    )
                payload = replay.model_dump(mode="python", round_trip=True)
                payload.update(
                    created_at=replay.detected_at,
                    updated_at=replay.detected_at,
                )
                selected_incident = Incident.model_validate(payload)
            else:
                # A correlated replay result is not the original request.  Its
                # fingerprint can only be checked after rebuilding the exact
                # candidate from current telemetry; disappearance fails closed.
                candidates = await self.scan(normalized_trace_id)
                selected = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.incident_id == normalized_candidate_id
                    ),
                    None,
                )
                if selected is not None:
                    selected_incident = selected.incident
        else:
            candidates = await self.scan(normalized_trace_id)
            selected = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.incident_id == normalized_candidate_id
                ),
                None,
            )
            if selected is not None:
                selected_incident = selected.incident
        if selected_incident is None:
            raise ValueError("candidate is unavailable after deterministic rescan")
        result = await self._incidents.create_or_correlate(
            selected_incident,
            idempotency_key=normalized_idempotency_key,
            actor=normalized_actor,
            reason=normalized_reason,
            trace_id=normalized_trace_id,
        )
        _assert_privacy_safe(result, boundary="confirmed incident")
        return result


__all__ = [
    "DetectorCapacityError",
    "LocalDetector",
    "MAX_CURRENT_RULES",
    "MAX_EPISODE_SAMPLES",
    "MAX_SCAN_CANDIDATES",
]
