"""Privacy-safe DuckDB metric and aggregate evidence adapters."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import duckdb

from telco_domain import (
    EvidenceReference,
    EvidenceType,
    Incident,
    KpiObservation,
    ResourceReference,
    ResourceType,
    SensitiveDataError,
    Technology,
    assert_model_safe,
)

from .config import LocalProfileConfig
from .lte_identifiers import (
    canonical_lte_resource_id,
    normalize_lte_identifier,
    parse_lte_resource_id,
)


Clock = Callable[[], datetime]
MAX_QUERY_OBSERVATIONS = 50_000
_MAX_KPI_SELECTORS = 16
_MAX_RESOURCE_SELECTORS = 1_000
_NON_BLOCKING_QUALITY_FLAGS = {"SOURCE_TIMEZONE_ASSUMED_UTC"}
_KPI_UNITS = {
    "erab_success_rate": "%",
    "retainability": "releases/hour",
    "uplink_rssi_avg": "dBm",
}


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


def _require_aware(name: str, value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _assert_privacy_safe(value: object, *, boundary: str) -> None:
    try:
        assert_model_safe(value)
    except SensitiveDataError:
        raise SensitiveDataError(
            f"{boundary} rejected by privacy policy"
        ) from None


def _resources(enodeb_id: object, cell_id: object) -> tuple[ResourceReference, ...]:
    enodeb = normalize_lte_identifier(enodeb_id, component="eNodeB")
    cell = normalize_lte_identifier(cell_id, component="Cell")
    enodeb_resource_id = canonical_lte_resource_id(enodeb)
    return (
        ResourceReference(
            resource_id=enodeb_resource_id,
            resource_type=ResourceType.ENODEB,
            technology=Technology.LTE,
            external_ids={"enodeb_id": enodeb},
        ),
        ResourceReference(
            resource_id=canonical_lte_resource_id(enodeb, cell),
            resource_type=ResourceType.CELL,
            technology=Technology.LTE,
            parent_resource_id=enodeb_resource_id,
            external_ids={"enodeb_id": enodeb, "cell_id": cell},
        ),
    )


def _normalized_lte_resource(
    resource: ResourceReference,
) -> ResourceReference:
    if resource.technology is not Technology.LTE:
        raise ValueError("Local Profile resources must use LTE technology")
    if resource.resource_type not in {ResourceType.ENODEB, ResourceType.CELL}:
        raise ValueError("Local Profile supports only LTE eNodeB/Cell resources")

    enodeb, cell = parse_lte_resource_id(resource.resource_id)
    if resource.resource_type is ResourceType.ENODEB:
        if cell is not None or resource.parent_resource_id is not None:
            raise ValueError("invalid canonical LTE eNodeB resource")
        canonical_id = canonical_lte_resource_id(enodeb)
        canonical_parent = None
    else:
        if cell is None:
            raise ValueError("invalid canonical LTE Cell resource")
        canonical_id = canonical_lte_resource_id(enodeb, cell)
        canonical_parent = canonical_lte_resource_id(enodeb)
        if resource.parent_resource_id is not None:
            parent_enodeb, parent_cell = parse_lte_resource_id(
                resource.parent_resource_id
            )
            if parent_cell is not None or parent_enodeb != enodeb:
                raise ValueError("invalid canonical LTE Cell parent")

    external_ids = dict(resource.external_ids)
    if "enodeb_id" in external_ids:
        external_enodeb = normalize_lte_identifier(
            external_ids["enodeb_id"], component="eNodeB"
        )
        if external_enodeb != enodeb:
            raise ValueError("inconsistent LTE eNodeB resource identity")
    external_ids["enodeb_id"] = enodeb
    if resource.resource_type is ResourceType.CELL:
        assert cell is not None
        if "cell_id" in external_ids:
            external_cell = normalize_lte_identifier(
                external_ids["cell_id"], component="Cell"
            )
            if external_cell != cell:
                raise ValueError("inconsistent LTE Cell resource identity")
        external_ids["cell_id"] = cell
    elif "cell_id" in external_ids:
        raise ValueError("invalid LTE eNodeB external identifiers")

    payload = resource.model_dump(mode="python", round_trip=True)
    payload.update(
        resource_id=canonical_id,
        parent_resource_id=canonical_parent,
        external_ids=external_ids,
    )
    return ResourceReference.model_validate(payload)


def _cell_scope(
    resources: Sequence[ResourceReference],
) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for resource in resources:
        normalized = _normalized_lte_resource(resource)
        if normalized.resource_type is not ResourceType.CELL:
            continue
        enodeb, cell = parse_lte_resource_id(normalized.resource_id)
        assert cell is not None
        pairs.add((enodeb, cell))
    return tuple(sorted(pairs))


def _evidence_resource_scope(
    resources: Sequence[ResourceReference],
) -> tuple[dict[str, Any], ...]:
    """Canonical authorization scope shared by metric and trace evidence."""

    normalized = tuple(_normalized_lte_resource(item) for item in resources)
    identities = tuple(item.resource_id for item in normalized)
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate canonical LTE resource identities")
    return tuple(
        resource.stable_identity()
        for resource in sorted(normalized, key=lambda item: item.resource_id)
    )


def _quality_flags(kpi_name: str, value: float) -> tuple[str, ...]:
    flags = {"SOURCE_TIMEZONE_ASSUMED_UTC"}
    if kpi_name == "erab_success_rate":
        if value > 100:
            flags.add("ERAB_SUCCESS_RATE_ABOVE_100_PERCENT")
        if value < 0:
            flags.add("ERAB_SUCCESS_RATE_BELOW_ZERO_PERCENT")
    elif kpi_name == "retainability" and value < 0:
        flags.add("RETAINABILITY_BELOW_ZERO")
    return tuple(sorted(flags))


class DuckDbTelemetryRepository:
    """Implement both pre-Incident KPI queries and post-Incident evidence reads.

    The adapter exposes normalized aggregates only.  It never selects or returns
    subscriber identifiers, even when the source CSV originally contained them.
    """

    def __init__(
        self,
        config_or_path: LocalProfileConfig | str | Path,
        *,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(config_or_path, LocalProfileConfig):
            self._database_path = config_or_path.database_path
        else:
            self._database_path = Path(config_or_path).expanduser().resolve(
                strict=False
            )
        self._clock = clock or (lambda: datetime.now(UTC))

    def _connect(self):
        if not self._database_path.is_file():
            raise FileNotFoundError(self._database_path)
        return duckdb.connect(str(self._database_path), read_only=True)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("telemetry clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_window(
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> tuple[datetime | None, datetime | None]:
        start = _require_aware("window_start", window_start)
        end = _require_aware("window_end", window_end)
        if start is not None and end is not None and end < start:
            raise ValueError("window_end must not be earlier than window_start")
        return start, end

    async def query_kpis(
        self,
        *,
        kpi_names: Sequence[str],
        technology: Technology,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        resource_ids: Sequence[str] = (),
        limit: int = MAX_QUERY_OBSERVATIONS,
    ) -> tuple[KpiObservation, ...]:
        """Return ordered rule-neutral LTE KPI observations."""

        if limit < 1 or limit > MAX_QUERY_OBSERVATIONS:
            raise ValueError(
                f"limit must be between 1 and {MAX_QUERY_OBSERVATIONS}"
            )
        try:
            normalized_technology = Technology(technology)
        except ValueError:
            return ()
        if normalized_technology is not Technology.LTE:
            return ()

        if isinstance(kpi_names, (str, bytes)):
            raise ValueError("kpi_names must be a sequence of KPI names")
        supplied_kpi_names = tuple(kpi_names)
        if len(supplied_kpi_names) > _MAX_KPI_SELECTORS:
            raise ValueError(
                f"kpi_names must contain at most {_MAX_KPI_SELECTORS} items"
            )
        if isinstance(resource_ids, (str, bytes)):
            raise ValueError("resource_ids must be a sequence of identifiers")
        supplied_resource_ids = tuple(resource_ids)
        if len(supplied_resource_ids) > _MAX_RESOURCE_SELECTORS:
            raise ValueError(
                "resource_ids must contain at most "
                f"{_MAX_RESOURCE_SELECTORS} items"
            )
        _assert_privacy_safe(
            {
                "kpi_names": supplied_kpi_names,
                "resource_ids": supplied_resource_ids,
            },
            boundary="telemetry query",
        )

        requested = tuple(
            sorted(set(str(name).strip() for name in supplied_kpi_names))
        )
        if any(not name for name in requested):
            raise ValueError("kpi_names must not contain empty values")
        unsupported = set(requested) - set(_KPI_UNITS)
        if unsupported:
            raise ValueError("unsupported KPI name")
        if not requested:
            return ()

        start, end = self._validate_window(window_start, window_end)
        filters: list[str] = ["observed_value IS NOT NULL"]
        parameters: list[object] = []
        if start is not None:
            filters.append("measurement_end >= ?")
            parameters.append(start)
        if end is not None:
            filters.append("measurement_end <= ?")
            parameters.append(end)
        enodeb_selectors: set[str] = set()
        cell_selectors: set[tuple[str, str]] = set()
        for raw_resource_id in supplied_resource_ids:
            resource_id = str(raw_resource_id).strip()
            if not resource_id or len(resource_id) > 256:
                raise ValueError("resource_ids contain an invalid identifier")
            enodeb_id, cell_id = parse_lte_resource_id(resource_id)
            if cell_id is None:
                enodeb_selectors.add(enodeb_id)
            else:
                cell_selectors.add((enodeb_id, cell_id))
        # A child selector is authoritative for its parent.  Keeping the
        # broader eNodeB predicate would silently union sibling cells into an
        # exact-cell request.
        enodebs_with_cells = {enodeb_id for enodeb_id, _ in cell_selectors}
        effective_enodebs = sorted(enodeb_selectors - enodebs_with_cells)
        resource_clauses = ["(enodeb_id = ?)"] * len(effective_enodebs)
        parameters.extend(effective_enodebs)
        for enodeb_id, cell_id in sorted(cell_selectors):
            resource_clauses.append("(enodeb_id = ? AND cell_id = ?)")
            parameters.extend((enodeb_id, cell_id))
        if resource_clauses:
            filters.append("(" + " OR ".join(resource_clauses) + ")")
        where = "WHERE " + " AND ".join(filters)
        metric_queries = " UNION ALL ".join(
            f"""
            SELECT enodeb_id, cell_id, measurement_end, source_row_id,
                   '{kpi_name}' AS kpi_name,
                   {kpi_name} AS observed_value
            FROM performance_kpi
            """
            for kpi_name in requested
        )

        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                WITH normalized_observations AS (
                    {metric_queries}
                )
                SELECT enodeb_id, cell_id, measurement_end, source_row_id,
                       kpi_name, observed_value
                FROM normalized_observations
                {where}
                ORDER BY measurement_end, enodeb_id, cell_id, kpi_name,
                         source_row_id
                LIMIT ?
                """,
                [*parameters, limit],
            ).fetchall()
        finally:
            connection.close()

        observations: list[KpiObservation] = []
        for (
            enodeb_raw,
            cell_raw,
            observed_at,
            source_row_id,
            kpi_name,
            raw_value,
        ) in rows:
            raw_enodeb_id = str(enodeb_raw)
            raw_cell_id = str(cell_raw)
            _assert_privacy_safe(
                {
                    "enodeb_id": raw_enodeb_id,
                    "cell_id": raw_cell_id,
                },
                boundary="telemetry resource",
            )
            enodeb_id = normalize_lte_identifier(
                raw_enodeb_id, component="eNodeB"
            )
            cell_id = normalize_lte_identifier(
                raw_cell_id, component="Cell"
            )
            resources = _resources(enodeb_id, cell_id)
            normalized_time = _require_aware("observed_at", observed_at)
            assert normalized_time is not None
            value = float(raw_value)
            if not math.isfinite(value):
                continue
            kpi_name = str(kpi_name)
            digest = _stable_digest(
                "kpi-observation-v1",
                kpi_name,
                enodeb_id,
                cell_id,
                normalized_time.isoformat(),
                int(source_row_id),
            )
            observations.append(
                KpiObservation(
                    observation_id=f"observation-{digest}",
                    kpi_name=kpi_name,
                    observed_value=value,
                    observed_at=normalized_time,
                    resources=resources,
                    unit=_KPI_UNITS[kpi_name],
                    source_uri=f"duckdb://performance_kpi/{digest}",
                    quality_flags=_quality_flags(kpi_name, value),
                    dimensions={
                        "aggregation": (
                            "source_interval_average"
                            if kpi_name == "uplink_rssi_avg"
                            else "counter_ratio"
                        ),
                        "cell_id": cell_id,
                        "enodeb_id": enodeb_id,
                        "source_timezone": "UTC",
                        "source_row_id": str(source_row_id),
                    },
                )
            )
        result = tuple(observations)
        _assert_privacy_safe(result, boundary="telemetry result")
        return result

    @staticmethod
    def _effective_window(
        incident: Incident,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> tuple[datetime, datetime]:
        explicit_start = _require_aware("window_start", window_start)
        explicit_end = _require_aware("window_end", window_end)
        incident_start = incident.window_start
        incident_end = incident.window_end
        start = explicit_start or incident_start
        end = explicit_end or incident_end
        if start is None or end is None:
            raise ValueError("a bounded evidence window is required")
        if end < start:
            raise ValueError("window_end must not be earlier than window_start")
        if incident_start is not None and start < incident_start:
            raise ValueError("evidence window must not expand the Incident window")
        if incident_end is not None and end > incident_end:
            raise ValueError("evidence window must not expand the Incident window")
        return start, end

    @staticmethod
    def _scope_predicate(
        pairs: Sequence[tuple[str, str]],
        *,
        enodeb_column: str,
        cell_column: str,
    ) -> tuple[str, list[object]]:
        clauses = [
            f"({enodeb_column} = ? AND {cell_column} = ?)" for _ in pairs
        ]
        parameters: list[object] = []
        for enodeb_id, cell_id in pairs:
            parameters.extend((enodeb_id, cell_id))
        return "(" + " OR ".join(clauses) + ")", parameters

    def _uplink_rssi_average(
        self,
        pairs: Sequence[tuple[str, str]],
        start: datetime,
        end: datetime,
    ) -> float | None:
        if not pairs:
            return None
        connection = self._connect()
        try:
            columns = {
                str(row[1]).lower()
                for row in connection.execute(
                    "PRAGMA table_info('performance')"
                ).fetchall()
            }
            if "ul_rssi" not in columns:
                return None
            scope, scope_parameters = self._scope_predicate(
                pairs,
                enodeb_column="CAST(enodeb_id AS VARCHAR)",
                cell_column="CAST(cell_id AS VARCHAR)",
            )
            row = connection.execute(
                f"""
                SELECT AVG(TRY_CAST(UL_RSSI AS DOUBLE))
                FROM performance
                WHERE {scope}
                  AND measurement_end >= ?
                  AND measurement_end <= ?
                """,
                [*scope_parameters, start, end],
            ).fetchone()
        finally:
            connection.close()
        if row is None or row[0] is None:
            return None
        value = float(row[0])
        return value if math.isfinite(value) else None

    def _trace_evidence(
        self,
        incident: Incident,
        pairs: Sequence[tuple[str, str]],
        resource_scope: Sequence[dict[str, Any]],
        start: datetime,
        end: datetime,
    ) -> EvidenceReference | None:
        if not pairs:
            return None
        scope, scope_parameters = self._scope_predicate(
            pairs,
            enodeb_column="start_enodeb_id",
            cell_column="start_cell_id",
        )
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT CASE UPPER(TRIM(s1_sig_conn_setup_sig_conn_result))
                           WHEN 'SUCCESS' THEN 'SUCCESS'
                           WHEN 'FAILURE' THEN 'FAILURE'
                           WHEN 'FAILED_SECURITY_SETUP'
                               THEN 'FAILED_SECURITY_SETUP'
                           ELSE 'OTHER'
                       END AS outcome,
                       COUNT(*) AS outcome_count
                FROM cell_traces
                WHERE {scope}
                  AND starttime >= ?
                  AND endtime <= ?
                GROUP BY outcome
                ORDER BY outcome
                """,
                [*scope_parameters, start, end],
            ).fetchall()
        finally:
            connection.close()
        if not rows:
            return None

        outcome_counts = {str(outcome): int(count) for outcome, count in rows}
        failure_count = sum(
            count
            for outcome, count in outcome_counts.items()
            if outcome.upper().startswith("FAIL")
        )
        security_count = outcome_counts.get("FAILED_SECURITY_SETUP", 0)
        security_ratio = (
            security_count / failure_count if failure_count else 0.0
        )
        facts: dict[str, bool | int | float | str] = {
            "failed_security_setup_count": security_count,
            "failure_count": failure_count,
            "failed_security_setup_ratio": security_ratio,
        }
        attributes: dict[str, Any] = {
            "facts": facts,
            "outcome_counts": outcome_counts,
            "resource_scope": list(resource_scope),
            "sample_count": sum(outcome_counts.values()),
            "window_end": end.isoformat(),
            "window_start": start.isoformat(),
        }
        digest = _stable_digest(
            "trace-evidence-v1",
            pairs,
            start.isoformat(),
            end.isoformat(),
            attributes,
        )
        return EvidenceReference(
            evidence_id=f"evidence-{digest}",
            evidence_type=EvidenceType.TRACE,
            uri=f"duckdb://cell_traces/aggregate/{digest}",
            source="local-duckdb",
            summary="同一 LTE Cell 与时间窗内的安全 Trace 聚合统计。",
            collected_at=end,
            content_type="application/vnd.telco.trace-summary+json",
            checksum_sha256=digest,
            attributes=attributes,
        )

    async def collect_evidence(
        self,
        incident: Incident,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int = 1_000,
    ) -> tuple[EvidenceReference, ...]:
        """Return small metric/trace summaries scoped to one LTE Incident."""

        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        _assert_privacy_safe(incident, boundary="evidence request")
        if incident.technology is not Technology.LTE:
            return ()
        start, end = self._effective_window(
            incident, window_start, window_end
        )
        pairs = _cell_scope(incident.affected_resources)
        resource_scope = _evidence_resource_scope(
            incident.affected_resources
        )
        resource_ids = tuple(
            _normalized_lte_resource(resource).resource_id
            for resource in incident.affected_resources
        )
        kpi_names = tuple(
            sorted({violation.kpi_name for violation in incident.violated_kpis})
        )

        query_limit = min(limit + 1, MAX_QUERY_OBSERVATIONS)
        observations = tuple(
            await self.query_kpis(
                kpi_names=kpi_names,
                technology=Technology.LTE,
                window_start=start,
                window_end=end,
                resource_ids=resource_ids,
                limit=query_limit,
            )
        ) if kpi_names else ()
        truncated = len(observations) > limit
        selected = observations[:limit]
        uplink_rssi_avg = self._uplink_rssi_average(pairs, start, end)

        evidence: list[EvidenceReference] = []
        for kpi_name in kpi_names:
            samples = tuple(
                item for item in selected if item.kpi_name == kpi_name
            )
            if not samples:
                continue
            valid_samples = tuple(
                item
                for item in samples
                if not (
                    set(item.quality_flags) - _NON_BLOCKING_QUALITY_FLAGS
                )
            )
            values = tuple(item.observed_value for item in valid_samples)
            facts: dict[str, bool | int | float | str] = {}
            if values:
                facts[f"kpi.{kpi_name}"] = math.fsum(values) / len(values)
            if uplink_rssi_avg is not None:
                facts["uplink_rssi_avg"] = uplink_rssi_avg
            quality_flags = sorted(
                {
                    flag
                    for item in samples
                    for flag in item.quality_flags
                }
            )
            attributes: dict[str, Any] = {
                "aggregation": "arithmetic_mean",
                "data_quality_only": not bool(valid_samples),
                "facts": facts,
                "kpi_name": kpi_name,
                "maximum": max(values) if values else None,
                "minimum": min(values) if values else None,
                "quality_flags": quality_flags,
                "resource_scope": list(resource_scope),
                "sample_count": len(valid_samples),
                "total_sample_count": len(samples),
                "valid_sample_count": len(valid_samples),
                "invalid_sample_count": len(samples) - len(valid_samples),
                "truncated": truncated,
                "window_end": end.isoformat(),
                "window_start": start.isoformat(),
            }
            digest = _stable_digest(
                "metric-evidence-v1",
                kpi_name,
                tuple(item.observation_id for item in samples),
                attributes,
            )
            evidence.append(
                EvidenceReference(
                    evidence_id=f"evidence-{digest}",
                    evidence_type=EvidenceType.METRIC,
                    uri=f"duckdb://performance_kpi/aggregate/{digest}",
                    source="local-duckdb",
                    summary=f"{kpi_name} 的 LTE Cell 时间窗聚合统计。",
                    collected_at=end,
                    content_type="application/vnd.telco.metric-summary+json",
                    checksum_sha256=digest,
                    attributes=attributes,
                )
            )

        trace = self._trace_evidence(
            incident, pairs, resource_scope, start, end
        )
        if trace is not None:
            evidence.append(trace)
        result = tuple(evidence[:limit])
        _assert_privacy_safe(result, boundary="evidence result")
        return result


__all__ = ["DuckDbTelemetryRepository", "MAX_QUERY_OBSERVATIONS"]
