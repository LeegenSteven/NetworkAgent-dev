from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


pytest.importorskip("a2a")

from agent.remote_agent_connection import RemoteAgentConnections
from utils.error_handler import RemoteAgentError


@pytest.mark.parametrize(
    ("has_client", "streaming"),
    [(False, True), (True, False)],
)
def test_remote_preflight_errors_are_unicast(
    has_client: bool,
    streaming: bool,
) -> None:
    class _Sio:
        def __init__(self) -> None:
            self.emitted: list[tuple[str, object, str]] = []

        async def emit(self, name: str, payload: object, *, room: str) -> None:
            self.emitted.append((name, payload, room))

    card = SimpleNamespace(
        name="Local Assurance Agent",
        capabilities=SimpleNamespace(streaming=streaming),
    )
    connection = RemoteAgentConnections(object(), card, "http://agent.invalid")
    if has_client:
        connection.agent_client = object()  # type: ignore[assignment]
    sio = _Sio()

    async def invoke() -> None:
        await connection.send_streaming_task(
            object(),  # type: ignore[arg-type]
            ui_session_id="thread-a",
            socket_target=("sid-a", sio),
            expected_context_id="context-a",
        )

    with pytest.raises(RemoteAgentError):
        asyncio.run(invoke())

    assert len(sio.emitted) == 3
    assert {room for _, _, room in sio.emitted} == {"sid-a"}
    assert sum(room == "sid-b" for _, _, room in sio.emitted) == 0
