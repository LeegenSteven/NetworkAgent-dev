"""Read-only, bounded Cloud Spanner telemetry and evidence adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from telco_domain.models import (
    EvidenceReference,
    Incident,
    KpiObservation,
    ResourceReference,
    Technology,
)
from telco_domain.ports import UnsafeIncidentWriteError

from ._common import (
    Clock,
    assert_safe,
    canonical_json,
    parse_json_model,
    require_non_empty,
    utc_now,
)
from ._spanner import execute_sql


MAX_QUERY_OBSERVATIONS = 50_000
MAX_EVIDENCE_REFERENCES = 1_000
MAX_KPI_SELECTORS = 16
MAX_RESOURCE_SELECTORS = 100
MAX_QUERY_WINDOW = timedelta(days=31)
DEFAULT_QUERY_WINDOW = timedelta(hours=1)
# Internal detector reads are not model/contract messages. Each individual
# domain object still obeys the shared 256 KiB boundary, while the streamed
# batch has a separate hard memory budget large enough for the 50k-row port.
MAX_TELEMETRY_BATCH_BYTES = 64 * 1024 * 1024


def _aware(name: str, value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _window(
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime,
    default: bool,
) -> tuple[datetime, datetime]:
    normalized_start = _aware("window_start", start)
    normalized_end = _aware("window_end", end)
    if normalized_start is None and normalized_end is None and default:
        normalized_end = now
        normalized_start = now - DEFAULT_QUERY_WINDOW
    elif (normalized_start is None) != (normalized_end is None):
        raise ValueError("window_start and window_end must be provided together")
    if normalized_start is None or normalized_end is None:
        raise ValueError("a bounded telemetry window is required")
    if normalized_end < normalized_start:
        raise ValueError("window_end must not be earlier than window_start")
    if normalized_end - normalized_start > MAX_QUERY_WINDOW:
        raise ValueError("telemetry window must not exceed 31 days")
    return normalized_start, normalized_end


def _selectors(
    name: str, values: Sequence[str], *, maximum: int, allow_empty: bool
) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise ValueError(f"{name} must be a sequence")
    if len(values) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} items")
    normalized = tuple(
        sorted(
            {
                require_non_empty(name, value, max_length=256)
                for value in values
            }
        )
    )
    if not normalized and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    assert_safe(normalized, boundary=name)
    return normalized


def _evidence_scope_time(name: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise RuntimeError("Spanner returned unverifiable evidence time scope")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError(
            "Spanner returned unverifiable evidence time scope"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("Spanner returned unverifiable evidence time scope")
    return parsed.astimezone(UTC)


def _append_bounded(
    values: list[Any],
    value: object,
    *,
    serialized_bytes: int,
    boundary: str,
) -> int:
    """Validate and account one streamed result before retaining the next."""

    assert_safe(value, boundary=boundary)
    next_size = serialized_bytes + len(canonical_json(value).encode("utf-8")) + 1
    if next_size > MAX_TELEMETRY_BATCH_BYTES:
        raise UnsafeIncidentWriteError(
            boundary,
            f"cumulative serialized payload exceeds "
            f"{MAX_TELEMETRY_BATCH_BYTES} bytes",
        )
    values.append(value)
    return next_size


class SpannerTelemetryRepository:
    """Return domain observations and references, never raw log or trace rows."""

    def __init__(self, database: Any, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return utc_now(self._clock)

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
        if limit < 1 or limit > MAX_QUERY_OBSERVATIONS:
            raise ValueError(
                f"limit must be between 1 and {MAX_QUERY_OBSERVATIONS}"
            )
        normalized_technology = Technology(technology)
        names = _selectors(
            "kpi_names",
            kpi_names,
            maximum=MAX_KPI_SELECTORS,
            allow_empty=True,
        )
        if not names:
            return ()
        resources = _selectors(
            "resource_ids",
            resource_ids,
            maximum=MAX_RESOURCE_SELECTORS,
            allow_empty=True,
        )
        start, end = _window(
            window_start, window_end, now=self._now(), default=True
        )

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                resource_filter = ""
                params: dict[str, object] = {
                    "technology": normalized_technology.value,
                    "kpi_names": names,
                    "window_start": start,
                    "window_end": end,
                    "limit": limit,
                }
                type_spec = {
                    "technology": "STRING",
                    "kpi_names": "STRING_ARRAY",
                    "window_start": "TIMESTAMP",
                    "window_end": "TIMESTAMP",
                    "limit": "INT64",
                }
                if resources:
                    resource_filter = (
                        "AND primary_resource_id IN UNNEST(@resource_ids)"
                    )
                    params["resource_ids"] = resources
                    type_spec["resource_ids"] = "STRING_ARRAY"
                rows = execute_sql(
                    snapshot,
                    f"""-- telco-cloud:query-kpis
                    SELECT observation_id, kpi_name, technology,
                           primary_resource_id, observed_at, payload
                    FROM RadioKpiObservationsV1
                    WHERE technology = @technology
                      AND kpi_name IN UNNEST(@kpi_names)
                      AND observed_at >= @window_start
                      AND observed_at <= @window_end
                      {resource_filter}
                    ORDER BY observed_at, observation_id
                    LIMIT @limit""",
                    params=params,
                    type_spec=type_spec,
                )
                streamed_records: list[Any] = []
                serialized_bytes = 2
                for row in rows:
                    if len(streamed_records) >= limit:
                        raise RuntimeError(
                            "Spanner returned more KPI rows than requested"
                        )
                    record = (
                        str(row[0]),
                        str(row[1]),
                        str(row[2]),
                        str(row[3]),
                        row[4],
                        parse_json_model(KpiObservation, row[5]),
                    )
                    serialized_bytes = _append_bounded(
                        streamed_records,
                        record,
                        serialized_bytes=serialized_bytes,
                        boundary="cloud-kpi-query",
                    )
                records = tuple(streamed_records)
                self._validate_observations(
                    records,
                    names=names,
                    technology=normalized_technology,
                    resources=frozenset(resources),
                    start=start,
                    end=end,
                    limit=limit,
                )
                observations = tuple(record[5] for record in records)
                return observations

        return await asyncio.to_thread(read)

    @staticmethod
    def _validate_observations(
        records: tuple[
            tuple[str, str, str, str, object, KpiObservation], ...
        ],
        *,
        names: tuple[str, ...],
        technology: Technology,
        resources: frozenset[str],
        start: datetime,
        end: datetime,
        limit: int,
    ) -> None:
        if len(records) > limit:
            raise RuntimeError("Spanner returned more KPI rows than requested")
        identities: set[str] = set()
        for (
            row_observation_id,
            row_kpi_name,
            row_technology,
            primary_resource_id,
            row_observed_at,
            observation,
        ) in records:
            if observation.observation_id in identities:
                raise RuntimeError("Spanner returned duplicate KPI observations")
            identities.add(observation.observation_id)
            if observation.observation_id != row_observation_id:
                raise RuntimeError("Spanner returned a mismatched KPI identity")
            if observation.kpi_name != row_kpi_name or row_kpi_name not in names:
                raise RuntimeError("Spanner returned an out-of-scope KPI")
            if row_technology != technology.value:
                raise RuntimeError("Spanner returned a cross-technology KPI row")
            if (
                observation.observed_at != row_observed_at
                or not start <= observation.observed_at <= end
            ):
                raise RuntimeError("Spanner returned an out-of-window KPI")
            if any(
                resource.technology is not technology
                for resource in observation.resources
            ):
                raise RuntimeError("Spanner returned a cross-technology KPI")
            if observation.resources[-1].resource_id != primary_resource_id:
                raise RuntimeError("Spanner KPI primary resource does not match payload")
            if resources and primary_resource_id not in resources:
                raise RuntimeError("Spanner returned an out-of-scope resource")

    async def collect_evidence(
        self,
        incident: Incident,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int = MAX_EVIDENCE_REFERENCES,
    ) -> tuple[EvidenceReference, ...]:
        if limit < 1 or limit > MAX_EVIDENCE_REFERENCES:
            raise ValueError(
                f"limit must be between 1 and {MAX_EVIDENCE_REFERENCES}"
            )
        assert_safe(incident, boundary=incident.incident_id)
        effective_start = incident.window_start if window_start is None else window_start
        effective_end = incident.window_end if window_end is None else window_end
        start, end = _window(
            effective_start,
            effective_end,
            now=self._now(),
            default=False,
        )
        if incident.window_start is not None and start < incident.window_start:
            raise ValueError("evidence window must not expand the Incident window")
        if incident.window_end is not None and end > incident.window_end:
            raise ValueError("evidence window must not expand the Incident window")
        if not incident.affected_resources:
            raise ValueError("Incident affected_resources are required for evidence")
        if any(
            resource.technology is not incident.technology
            for resource in incident.affected_resources
        ):
            raise ValueError("Incident contains a cross-technology resource scope")
        expected_scope = tuple(
            canonical_json(resource.stable_identity())
            for resource in sorted(
                incident.affected_resources,
                key=lambda item: item.resource_id,
            )
        )

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                rows = execute_sql(
                    snapshot,
                    """-- telco-cloud:collect-evidence
                    SELECT incident_id, evidence_id, evidence_type,
                           collected_at, payload
                    FROM SafeEvidenceReferencesV1
                    WHERE incident_id = @incident_id
                      AND collected_at >= @window_start
                      AND collected_at <= @window_end
                    ORDER BY collected_at, evidence_id
                    LIMIT @limit""",
                    params={
                        "incident_id": incident.incident_id,
                        "window_start": start,
                        "window_end": end,
                        "limit": limit,
                    },
                    type_spec={
                        "incident_id": "STRING",
                        "window_start": "TIMESTAMP",
                        "window_end": "TIMESTAMP",
                        "limit": "INT64",
                    },
                )
                streamed_records: list[Any] = []
                serialized_bytes = 2
                for row in rows:
                    if len(streamed_records) >= limit:
                        raise RuntimeError(
                            "Spanner returned more evidence than requested"
                        )
                    record = (
                        str(row[0]),
                        str(row[1]),
                        str(row[2]),
                        row[3],
                        parse_json_model(EvidenceReference, row[4]),
                    )
                    serialized_bytes = _append_bounded(
                        streamed_records,
                        record,
                        serialized_bytes=serialized_bytes,
                        boundary="cloud-evidence-query",
                    )
                records = tuple(streamed_records)
                identities: set[str] = set()
                for (
                    row_incident_id,
                    row_id,
                    row_type,
                    row_collected_at,
                    reference,
                ) in records:
                    if reference.evidence_id in identities:
                        raise RuntimeError("Spanner returned duplicate evidence")
                    identities.add(reference.evidence_id)
                    if row_incident_id != incident.incident_id:
                        raise RuntimeError("Spanner returned mismatched evidence Incident")
                    if reference.evidence_id != row_id:
                        raise RuntimeError("Spanner returned mismatched evidence identity")
                    if reference.evidence_type.value != row_type:
                        raise RuntimeError("Spanner returned mismatched evidence type")
                    if (
                        reference.collected_at != row_collected_at
                        or reference.collected_at is None
                        or not (start <= reference.collected_at <= end)
                    ):
                        raise RuntimeError("Spanner returned out-of-window evidence")
                    raw_scope = reference.attributes.get("resource_scope")
                    if (
                        not isinstance(raw_scope, Sequence)
                        or isinstance(raw_scope, (str, bytes, bytearray))
                        or not raw_scope
                        or any(not isinstance(item, Mapping) for item in raw_scope)
                    ):
                        raise RuntimeError(
                            "Spanner returned unverifiable evidence resource scope"
                        )
                    actual_scope = tuple(
                        canonical_json(dict(item)) for item in raw_scope
                    )
                    if actual_scope != expected_scope:
                        raise RuntimeError(
                            "Spanner returned out-of-scope evidence resource scope"
                        )
                    evidence_start = _evidence_scope_time(
                        "window_start", reference.attributes.get("window_start")
                    )
                    evidence_end = _evidence_scope_time(
                        "window_end", reference.attributes.get("window_end")
                    )
                    if evidence_end < evidence_start:
                        raise RuntimeError(
                            "Spanner returned invalid evidence time scope"
                        )
                    if (
                        evidence_start < start
                        or evidence_end > end
                        or not evidence_start
                        <= reference.collected_at
                        <= evidence_end
                    ):
                        raise RuntimeError(
                            "Spanner returned out-of-scope evidence time scope"
                        )
                evidence = tuple(record[4] for record in records)
                return evidence

        return await asyncio.to_thread(read)

    async def resolve_resource_references(
        self,
        *,
        resource_ids: Sequence[str],
        technology: Technology | None = None,
        limit: int = 100,
    ) -> tuple[ResourceReference, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        resources = _selectors(
            "resource_ids", resource_ids, maximum=100, allow_empty=True
        )
        if not resources:
            return ()
        normalized_technology = None if technology is None else Technology(technology)

        def read():
            with self._database.snapshot(multi_use=True) as snapshot:
                technology_filter = ""
                params: dict[str, object] = {
                    "resource_ids": resources,
                    "limit": limit,
                }
                type_spec = {
                    "resource_ids": "STRING_ARRAY",
                    "limit": "INT64",
                }
                if normalized_technology is not None:
                    technology_filter = "AND technology = @technology"
                    params["technology"] = normalized_technology.value
                    type_spec["technology"] = "STRING"
                rows = execute_sql(
                    snapshot,
                    f"""-- telco-cloud:resolve-resources
                    SELECT resource_id, technology, resource_type, payload
                    FROM CanonicalResourceReferencesV1
                    WHERE resource_id IN UNNEST(@resource_ids)
                    {technology_filter}
                    ORDER BY resource_id
                    LIMIT @limit""",
                    params=params,
                    type_spec=type_spec,
                )
                streamed_result: list[Any] = []
                serialized_bytes = 2
                for row in rows:
                    if len(streamed_result) >= limit:
                        raise RuntimeError(
                            "Spanner returned too many resource mappings"
                        )
                    record = (
                        str(row[0]),
                        str(row[1]),
                        str(row[2]),
                        parse_json_model(ResourceReference, row[3]),
                    )
                    serialized_bytes = _append_bounded(
                        streamed_result,
                        record,
                        serialized_bytes=serialized_bytes,
                        boundary="cloud-resource-mapping",
                    )
                result = tuple(streamed_result)
                requested = frozenset(resources)
                identities: set[str] = set()
                for row_id, row_technology, row_type, reference in result:
                    if reference.resource_id in identities:
                        raise RuntimeError("Spanner returned duplicate resource mappings")
                    identities.add(reference.resource_id)
                    if (
                        reference.resource_id != row_id
                        or (
                            None
                            if reference.technology is None
                            else reference.technology.value
                        )
                        != row_technology
                        or reference.resource_type.value != row_type
                    ):
                        raise RuntimeError("Spanner returned mismatched resource mapping")
                    if reference.resource_id not in requested:
                        raise RuntimeError("Spanner returned an unrequested resource")
                    if (
                        normalized_technology is not None
                        and reference.technology is not normalized_technology
                    ):
                        raise RuntimeError("Spanner returned a cross-technology resource")
                references = tuple(record[3] for record in result)
                return references

        return await asyncio.to_thread(read)


__all__ = [
    "MAX_EVIDENCE_REFERENCES",
    "MAX_QUERY_OBSERVATIONS",
    "MAX_TELEMETRY_BATCH_BYTES",
    "SpannerTelemetryRepository",
]
