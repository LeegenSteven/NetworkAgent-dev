"""Bounded read services over canonical repository ports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from pydantic import BaseModel, ValidationError
from telco_domain import (
    EvidenceReference,
    EvidenceType,
    Incident,
    IncidentAuditEvent,
    IncidentRepository,
    IncidentStatus,
    KpiObservation,
    MetricRepository,
    ResourceReference,
    SensitiveDataError,
    Technology,
    TelemetryRepository,
    assert_model_safe,
)

from .models import CloudMcpInputError, ToolError, ToolResponse


MAX_RESPONSE_BYTES = 256_000
MAX_RESPONSE_DEPTH = 24
MAX_RESOURCES = 100
MAX_INCIDENTS = 100
MAX_DETAIL_RESULTS = 1_000
MAX_KPI_NAMES = 16
MAX_OFFSET = 100_000
MAX_WINDOW = timedelta(days=31)


def _identifier(value: object, *, code: str = "MCP_IDENTIFIER_INVALID") -> str:
    if not isinstance(value, str):
        raise CloudMcpInputError(code)
    normalized = value.strip()
    if not 1 <= len(normalized) <= 256:
        raise CloudMcpInputError(code)
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise CloudMcpInputError(code) from None
    return normalized


def _identifiers(
    values: object, *, maximum: int, code: str
) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise CloudMcpInputError(code)
    if len(values) > maximum:
        raise CloudMcpInputError(code)
    normalized = tuple(_identifier(value, code=code) for value in values)
    if len(normalized) != len(set(normalized)):
        raise CloudMcpInputError(code)
    return normalized


def _limit(value: int, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise CloudMcpInputError("MCP_LIMIT_INVALID")
    return value


def _offset(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_OFFSET:
        raise CloudMcpInputError("MCP_OFFSET_INVALID")
    return value


def _utc(value: str | datetime, *, code: str = "MCP_TIME_INVALID") -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CloudMcpInputError(code) from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise CloudMcpInputError(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CloudMcpInputError(code)
    return parsed.astimezone(UTC)


def _window(start: str | datetime, end: str | datetime) -> tuple[datetime, datetime]:
    window_start = _utc(start)
    window_end = _utc(end)
    if window_end < window_start or window_end - window_start > MAX_WINDOW:
        raise CloudMcpInputError("MCP_TIME_WINDOW_INVALID")
    return window_start, window_end


def _depth(value: object, maximum: int = MAX_RESPONSE_DEPTH) -> int:
    deepest = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, level = stack.pop()
        deepest = max(deepest, level)
        if level > maximum:
            return level
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((item, level + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((item, level + 1) for item in current)
    return deepest


def _json_value(value: object) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", round_trip=True)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _validated(model: type[BaseModel], value: object):
    try:
        return model.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise CloudMcpInputError("MCP_RESPONSE_INVALID") from None


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError, RecursionError):
        raise CloudMcpInputError("MCP_RESPONSE_INVALID") from None


def _bounded_models(
    model: type[BaseModel], values: object, *, maximum: int
) -> tuple[BaseModel, ...]:
    """Validate an untrusted result stream without fully materializing it first."""

    try:
        iterator = iter(values)
    except TypeError:
        raise CloudMcpInputError("MCP_RESPONSE_INVALID") from None
    items: list[BaseModel] = []
    encoded_items = 0
    for raw_item in iterator:
        if len(items) >= maximum:
            raise CloudMcpInputError("MCP_RESPONSE_LIMIT_VIOLATED")
        item = _validated(model, raw_item)
        plain = _json_value(item)
        try:
            assert_model_safe(plain)
        except SensitiveDataError:
            raise CloudMcpInputError("MCP_RESPONSE_PRIVACY_REJECTED") from None
        if _depth(plain) > MAX_RESPONSE_DEPTH:
            raise CloudMcpInputError("MCP_RESPONSE_TOO_DEEP")
        encoded_items += len(_encoded(plain))
        if encoded_items > MAX_RESPONSE_BYTES:
            raise CloudMcpInputError("MCP_RESPONSE_TOO_LARGE")
        items.append(item)
    return tuple(items)


def safe_success(data: object) -> dict[str, Any]:
    plain = _json_value(data)
    try:
        assert_model_safe(plain)
    except SensitiveDataError:
        raise CloudMcpInputError("MCP_RESPONSE_PRIVACY_REJECTED") from None
    response = ToolResponse(ok=True, data=plain).model_dump(mode="json")
    if _depth(response) > MAX_RESPONSE_DEPTH:
        raise CloudMcpInputError("MCP_RESPONSE_TOO_DEEP")
    encoded = _encoded(response)
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise CloudMcpInputError("MCP_RESPONSE_TOO_LARGE")
    return response


def safe_error(code: str) -> dict[str, Any]:
    return ToolResponse(ok=False, error=ToolError(code=code)).model_dump(mode="json")


class CloudMcpService:
    """Read-only operations with validation independent of FastMCP."""

    def __init__(
        self,
        incident_repository: IncidentRepository,
        telemetry_repository: TelemetryRepository | MetricRepository,
    ) -> None:
        self.incidents = incident_repository
        self.telemetry = telemetry_repository

    async def get_incident(self, incident_id: str) -> dict[str, Any]:
        normalized_id = _identifier(incident_id)
        raw_incident = await self.incidents.get(normalized_id)
        if raw_incident is None:
            return safe_error("MCP_INCIDENT_NOT_FOUND")
        incident = _validated(Incident, raw_incident)
        if incident.incident_id != normalized_id:
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        return safe_success({"incident": incident})

    async def list_incidents(
        self, *, status: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        bounded_limit = _limit(limit, maximum=MAX_INCIDENTS)
        bounded_offset = _offset(offset)
        try:
            normalized_status = None if status is None else IncidentStatus(status)
        except ValueError:
            raise CloudMcpInputError("MCP_STATUS_INVALID") from None
        incidents = _bounded_models(
            Incident,
            await self.incidents.list(
                status=normalized_status,
                limit=bounded_limit,
                offset=bounded_offset,
            ),
            maximum=bounded_limit,
        )
        if len({item.incident_id for item in incidents}) != len(incidents):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        if normalized_status is not None and any(
            item.status is not normalized_status for item in incidents
        ):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        return safe_success(
            {
                "incidents": incidents,
                "limit": bounded_limit,
                "offset": bounded_offset,
                "next_offset": (
                    bounded_offset + len(incidents)
                    if len(incidents) == bounded_limit
                    else None
                ),
            }
        )

    async def get_history(
        self, incident_id: str, *, limit: int, offset: int
    ) -> dict[str, Any]:
        normalized_id = _identifier(incident_id)
        bounded_limit = _limit(limit, maximum=MAX_DETAIL_RESULTS)
        bounded_offset = _offset(offset)
        page = _bounded_models(
            IncidentAuditEvent,
            await self.incidents.history(
                normalized_id,
                limit=bounded_limit,
                offset=bounded_offset,
            ),
            maximum=bounded_limit,
        )
        if any(event.incident_id != normalized_id for event in page):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        if len({event.event_id for event in page}) != len(page):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        next_offset = (
            bounded_offset + len(page)
            if len(page) == bounded_limit
            else None
        )
        return safe_success(
            {
                "events": page,
                "limit": bounded_limit,
                "offset": bounded_offset,
                "next_offset": next_offset,
            }
        )

    async def collect_evidence(
        self,
        incident_id: str,
        *,
        window_start: str | datetime,
        window_end: str | datetime,
        resource_ids: Sequence[str],
        evidence_types: Sequence[str],
        limit: int,
    ) -> dict[str, Any]:
        normalized_id = _identifier(incident_id)
        start, end = _window(window_start, window_end)
        resources = _identifiers(
            resource_ids, maximum=MAX_RESOURCES, code="MCP_RESOURCE_SCOPE_INVALID"
        )
        bounded_limit = _limit(limit, maximum=MAX_DETAIL_RESULTS)
        normalized_evidence_types = _identifiers(
            evidence_types,
            maximum=len(EvidenceType),
            code="MCP_EVIDENCE_TYPE_INVALID",
        )
        try:
            kinds = tuple(EvidenceType(item) for item in normalized_evidence_types)
        except ValueError:
            raise CloudMcpInputError("MCP_EVIDENCE_TYPE_INVALID") from None

        raw_incident = await self.incidents.get(normalized_id)
        if raw_incident is None:
            return safe_error("MCP_INCIDENT_NOT_FOUND")
        incident = _validated(Incident, raw_incident)
        if incident.incident_id != normalized_id:
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        scoped_incident = incident
        if resources:
            allowed = {
                resource.resource_id: resource
                for resource in incident.affected_resources
            }
            if not set(resources).issubset(allowed):
                raise CloudMcpInputError("MCP_RESOURCE_SCOPE_INVALID")
            scoped_incident = Incident.model_validate(
                {
                    **incident.model_dump(mode="python", round_trip=True),
                    "affected_resources": tuple(allowed[item] for item in resources),
                }
            )
        collector = getattr(self.telemetry, "collect_evidence", None)
        if not callable(collector):
            return safe_error("MCP_TOOL_UNAVAILABLE")
        collected = _bounded_models(
            EvidenceReference,
            await collector(
                scoped_incident,
                window_start=start,
                window_end=end,
                limit=bounded_limit,
            ),
            maximum=bounded_limit,
        )
        if kinds and any(item.evidence_type not in kinds for item in collected):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        selected = collected
        if len({item.evidence_id for item in selected}) != len(selected):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        expected_scope = tuple(
            resource.stable_identity()
            for resource in sorted(
                scoped_incident.affected_resources,
                key=lambda item: item.resource_id,
            )
        )
        for item in selected:
            if item.collected_at is None or not start <= item.collected_at <= end:
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            evidence_start = _utc(
                item.attributes.get("window_start"),
                code="MCP_RESPONSE_SCOPE_VIOLATED",
            )
            evidence_end = _utc(
                item.attributes.get("window_end"),
                code="MCP_RESPONSE_SCOPE_VIOLATED",
            )
            if not (
                start <= evidence_start <= item.collected_at <= evidence_end <= end
            ):
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            attributed = item.attributes.get("resource_scope")
            if (
                not isinstance(attributed, Sequence)
                or isinstance(attributed, (str, bytes, bytearray))
                or any(not isinstance(value, Mapping) for value in attributed)
            ):
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            actual_scope = tuple(dict(value) for value in attributed)
            if actual_scope != expected_scope:
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        return safe_success({"evidence": selected, "limit": bounded_limit})

    async def query_kpis(
        self,
        *,
        kpi_names: Sequence[str],
        technology: str,
        window_start: str | datetime,
        window_end: str | datetime,
        resource_ids: Sequence[str],
        limit: int,
    ) -> dict[str, Any]:
        names = _identifiers(
            kpi_names, maximum=MAX_KPI_NAMES, code="MCP_KPI_SCOPE_INVALID"
        )
        if not names:
            raise CloudMcpInputError("MCP_KPI_SCOPE_INVALID")
        resources = _identifiers(
            resource_ids, maximum=MAX_RESOURCES, code="MCP_RESOURCE_SCOPE_INVALID"
        )
        start, end = _window(window_start, window_end)
        bounded_limit = _limit(limit, maximum=MAX_DETAIL_RESULTS)
        try:
            normalized_technology = Technology(technology)
        except ValueError:
            raise CloudMcpInputError("MCP_TECHNOLOGY_INVALID") from None
        query = getattr(self.telemetry, "query_kpis", None)
        if not callable(query):
            return safe_error("MCP_TOOL_UNAVAILABLE")
        observations = _bounded_models(
            KpiObservation,
            await query(
                kpi_names=names,
                technology=normalized_technology,
                window_start=start,
                window_end=end,
                resource_ids=resources,
                limit=bounded_limit,
            ),
            maximum=bounded_limit,
        )
        requested_resources = frozenset(resources)
        identities: set[str] = set()
        for observation in observations:
            if observation.observation_id in identities:
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            identities.add(observation.observation_id)
            if observation.kpi_name not in names:
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            if not start <= observation.observed_at <= end:
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            if any(
                resource.technology is not normalized_technology
                for resource in observation.resources
            ):
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
            if requested_resources and (
                observation.resources[-1].resource_id not in requested_resources
            ):
                raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        return safe_success({"observations": observations, "limit": bounded_limit})

    async def resolve_resources(
        self,
        *,
        resource_ids: Sequence[str],
        technology: str | None,
        limit: int,
    ) -> dict[str, Any]:
        resources = _identifiers(
            resource_ids, maximum=MAX_RESOURCES, code="MCP_RESOURCE_SCOPE_INVALID"
        )
        if not resources:
            raise CloudMcpInputError("MCP_RESOURCE_SCOPE_INVALID")
        bounded_limit = _limit(limit, maximum=MAX_RESOURCES)
        try:
            normalized_technology = (
                None if technology is None else Technology(technology)
            )
        except ValueError:
            raise CloudMcpInputError("MCP_TECHNOLOGY_INVALID") from None
        resolver = getattr(self.telemetry, "resolve_resource_references", None)
        if not callable(resolver):
            return safe_error("MCP_TOOL_UNAVAILABLE")
        resolved: Sequence[ResourceReference] = await resolver(
            resource_ids=resources,
            technology=normalized_technology,
            limit=bounded_limit,
        )
        materialized = _bounded_models(
            ResourceReference, resolved, maximum=bounded_limit
        )
        requested = frozenset(resources)
        if len({item.resource_id for item in materialized}) != len(materialized):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        if any(item.resource_id not in requested for item in materialized):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        if normalized_technology is not None and any(
            item.technology is not normalized_technology for item in materialized
        ):
            raise CloudMcpInputError("MCP_RESPONSE_SCOPE_VIOLATED")
        return safe_success({"resources": materialized, "limit": bounded_limit})


__all__ = [
    "CloudMcpService",
    "MAX_DETAIL_RESULTS",
    "MAX_INCIDENTS",
    "MAX_OFFSET",
    "MAX_RESOURCES",
    "MAX_RESPONSE_BYTES",
    "safe_error",
    "safe_success",
]
