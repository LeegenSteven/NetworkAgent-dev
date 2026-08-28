from __future__ import annotations

import asyncio

import pytest

from telco_cloud_mcp.server import create_server


EXPECTED_TOOLS = {
    "get_canonical_incident",
    "list_canonical_incidents",
    "get_incident_history",
    "collect_incident_evidence",
    "query_kpi_observations",
    "resolve_resource_references",
}


class FakeMcp:
    def __init__(self, *, name, instructions) -> None:
        self.name = name
        self.instructions = instructions
        self.tools = {}
        self.annotations = {}

    def tool(self, *, annotations):
        def register(function):
            self.tools[function.__name__] = function
            self.annotations[function.__name__] = annotations
            return function

        return register


def annotation_factory(**kwargs):
    return kwargs


def test_factory_registers_exactly_six_read_only_tools(repositories) -> None:
    server = create_server(
        *repositories,
        mcp_factory=FakeMcp,
        annotations_factory=annotation_factory,
    )
    assert set(server.tools) == EXPECTED_TOOLS
    assert all(
        value
        == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
        for value in server.annotations.values()
    )
    assert not any("update" in name or "delete" in name for name in server.tools)


@pytest.mark.asyncio
async def test_real_fastmcp_registers_exactly_six_read_only_tools(
    repositories,
) -> None:
    pytest.importorskip("fastmcp")
    server = create_server(*repositories)

    registered = await server.get_tools()

    assert set(registered) == EXPECTED_TOOLS
    assert all(tool.annotations.readOnlyHint is True for tool in registered.values())
    assert all(tool.annotations.destructiveHint is False for tool in registered.values())
    assert all(tool.annotations.idempotentHint is True for tool in registered.values())
    assert all(tool.annotations.openWorldHint is False for tool in registered.values())
    assert server.http_app(
        transport="streamable-http", stateless_http=True
    ) is not None


@pytest.mark.asyncio
async def test_boundary_masks_dependency_exception_text(repositories, caplog) -> None:
    incidents, telemetry = repositories
    incidents.failure = RuntimeError("credential=top-secret")
    server = create_server(
        incidents,
        telemetry,
        mcp_factory=FakeMcp,
        annotations_factory=annotation_factory,
    )
    result = await server.tools["get_canonical_incident"]("incident-1")
    assert result["error"]["code"] == "MCP_DEPENDENCY_UNAVAILABLE"
    assert "top-secret" not in str(result)
    assert "top-secret" not in caplog.text


@pytest.mark.asyncio
async def test_boundary_converts_input_errors_to_fixed_codes(repositories) -> None:
    server = create_server(
        *repositories,
        mcp_factory=FakeMcp,
        annotations_factory=annotation_factory,
    )
    result = await server.tools["list_canonical_incidents"](None, 101, 0)
    assert result["ok"] is False
    assert result["error"]["code"] == "MCP_LIMIT_INVALID"
