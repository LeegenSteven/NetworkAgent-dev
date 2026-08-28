"""FastMCP factory registering exactly six read-only tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
import logging
from typing import Any

from .models import CloudMcpInputError
from .service import CloudMcpService, safe_error


logger = logging.getLogger(__name__)


async def _safe_call(operation: Callable[[], Awaitable[dict[str, Any]]]):
    try:
        return await operation()
    except CloudMcpInputError as exc:
        logger.warning("cloud MCP request rejected code=%s", exc.code)
        return safe_error(exc.code)
    except Exception:
        logger.error("cloud MCP dependency failed")
        return safe_error("MCP_DEPENDENCY_UNAVAILABLE")


def create_server(
    incident_repository,
    telemetry_repository,
    *,
    mcp_factory=None,
    annotations_factory=None,
):
    """Create the registry without connecting to Spanner or loading credentials."""

    if mcp_factory is None:
        from fastmcp import FastMCP

        mcp_factory = FastMCP
    if annotations_factory is None:
        from mcp.types import ToolAnnotations

        annotations_factory = ToolAnnotations
    server = mcp_factory(
        name="Telco Cloud Read-Only MCP",
        instructions="Bounded read-only access to canonical telco incidents and evidence",
    )
    annotations = annotations_factory(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    service = CloudMcpService(incident_repository, telemetry_repository)

    @server.tool(annotations=annotations)
    async def get_canonical_incident(incident_id: str) -> dict[str, Any]:
        return await _safe_call(lambda: service.get_incident(incident_id))

    @server.tool(annotations=annotations)
    async def list_canonical_incidents(
        status: str | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        return await _safe_call(
            lambda: service.list_incidents(status=status, limit=limit, offset=offset)
        )

    @server.tool(annotations=annotations)
    async def get_incident_history(
        incident_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        return await _safe_call(
            lambda: service.get_history(incident_id, limit=limit, offset=offset)
        )

    @server.tool(annotations=annotations)
    async def collect_incident_evidence(
        incident_id: str,
        window_start: str,
        window_end: str,
        resource_ids: Sequence[str] = (),
        evidence_types: Sequence[str] = (),
        limit: int = 1_000,
    ) -> dict[str, Any]:
        return await _safe_call(
            lambda: service.collect_evidence(
                incident_id,
                window_start=window_start,
                window_end=window_end,
                resource_ids=resource_ids,
                evidence_types=evidence_types,
                limit=limit,
            )
        )

    @server.tool(annotations=annotations)
    async def query_kpi_observations(
        kpi_names: Sequence[str],
        technology: str,
        window_start: str,
        window_end: str,
        resource_ids: Sequence[str] = (),
        limit: int = 1_000,
    ) -> dict[str, Any]:
        return await _safe_call(
            lambda: service.query_kpis(
                kpi_names=kpi_names,
                technology=technology,
                window_start=window_start,
                window_end=window_end,
                resource_ids=resource_ids,
                limit=limit,
            )
        )

    @server.tool(annotations=annotations)
    async def resolve_resource_references(
        resource_ids: Sequence[str],
        technology: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await _safe_call(
            lambda: service.resolve_resources(
                resource_ids=resource_ids,
                technology=technology,
                limit=limit,
            )
        )

    return server


__all__ = ["create_server"]
