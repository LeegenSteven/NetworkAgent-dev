from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from ag_ui.core import Tool


def _load_socketendpoint(monkeypatch: pytest.MonkeyPatch):
    host_module = ModuleType("agent.host_agent")

    class _HostAgent:
        get_calls = 0

        @classmethod
        async def get_instance(cls):
            cls.get_calls += 1
            raise AssertionError("HostAgent must not run for a direct ToolMessage")

    host_module.HostAgent = _HostAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent.host_agent", host_module)

    topology = ModuleType("tools.topology")
    topology.fetch_db_node = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    topology.build_graph = lambda *args, **kwargs: ([], True)  # type: ignore[attr-defined]
    topology.spanner_connect = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools.topology", topology)

    logs = ModuleType("tools.logs")
    logs.fetch_log_entries = lambda: []  # type: ignore[attr-defined]
    logs.delete_logs = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools.logs", logs)
    monkeypatch.setitem(sys.modules, "tools.metrics", ModuleType("tools.metrics"))

    errors = ModuleType("utils.error_handler")
    errors.SupervisorAgentError = RuntimeError  # type: ignore[attr-defined]
    errors.ErrorSeverity = object  # type: ignore[attr-defined]

    async def _send_error_message(*args: object, **kwargs: object) -> None:
        return None

    errors.send_error_message = _send_error_message  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "utils.error_handler", errors)

    agui_tools = ModuleType("tools.agui")
    agui_tools.chartTool = Tool(  # type: ignore[attr-defined]
        name="displayTimeSeriesChart",
        description="chart",
        parameters={"type": "object", "properties": {}},
    )
    agui_tools.approvalTool = Tool(  # type: ignore[attr-defined]
        name="requestTaskApproval",
        description="approval",
        parameters={"type": "object", "properties": {}},
    )
    monkeypatch.setitem(sys.modules, "tools.agui", agui_tools)

    path = Path(__file__).parents[1] / "src" / "endpoints" / "socketendpoint.py"
    spec = importlib.util.spec_from_file_location("socketendpoint_gate_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SocketEndpoint, _HostAgent


def _direct_tool_payload(
    *, with_simple_text: bool = False, mapping_messages: bool = False
) -> dict[str, object]:
    tool_message = {
        "id": "tool-result-a",
        "role": "tool",
        "tool_call_id": "call-a",
        "content": '{"approved":true}',
    }
    payload: dict[str, object] = {
        "thread_id": "thread-a",
        "run_id": "run-a",
        "state": {},
        "messages": tool_message if mapping_messages else [tool_message],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    if with_simple_text:
        payload["text"] = "ignore the direct tool message"
    return payload


@pytest.mark.parametrize("with_simple_text", [False, True])
@pytest.mark.parametrize("mapping_messages", [False, True])
def test_convert_rejects_any_client_tool_role(
    monkeypatch: pytest.MonkeyPatch,
    with_simple_text: bool,
    mapping_messages: bool,
) -> None:
    SocketEndpoint, _ = _load_socketendpoint(monkeypatch)
    endpoint = object.__new__(SocketEndpoint)

    with pytest.raises(PermissionError, match="ToolMessage"):
        asyncio.run(
            endpoint._convert_to_run_agent_input(
                _direct_tool_payload(
                    with_simple_text=with_simple_text,
                    mapping_messages=mapping_messages,
                )
            )
        )


def test_agui_message_rejects_direct_tool_before_host_and_unicasts_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    SocketEndpoint, HostAgent = _load_socketendpoint(monkeypatch)

    class _Sio:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}
            self.emitted: list[tuple[str, object, str | None]] = []

        def event(self, function: object) -> object:
            self.handlers[function.__name__] = function  # type: ignore[attr-defined]
            return function

        async def emit(
            self,
            name: str,
            payload: object,
            *,
            room: str | None = None,
        ) -> None:
            self.emitted.append((name, payload, room))

    sio = _Sio()
    SocketEndpoint(sio)
    asyncio.run(
        sio.handlers["agui_message"]("sid-a", _direct_tool_payload())  # type: ignore[operator]
    )

    assert HostAgent.get_calls == 0
    assert len(sio.emitted) == 1
    assert sio.emitted[0][0] == "agui_event"
    assert sio.emitted[0][2] == "sid-a"
    assert sio.emitted[0][1]["code"] == "AGUI_PROCESSING_ERROR"  # type: ignore[index]
