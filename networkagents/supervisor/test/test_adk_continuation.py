from __future__ import annotations

import asyncio

import pytest


pytest.importorskip("google.adk")

from ag_ui.core import (
    AssistantMessage,
    FunctionCall,
    RunAgentInput,
    ToolCall,
    ToolMessage,
)
from agent_library.agentmiddleware.adk import ADKAgent


def _tool_input(
    *,
    thread_id: str = "thread-a",
    call_id: str = "call-a",
    tool_name: str = "requestTaskApproval",
    content: str = '{"approved":true}',
) -> RunAgentInput:
    return RunAgentInput(
        thread_id=thread_id,
        run_id="run-tool-result",
        state={},
        messages=[
            AssistantMessage(
                id="assistant-tool-context",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        function=FunctionCall(name=tool_name, arguments="{}"),
                    )
                ],
            ),
            ToolMessage(
                id=f"tool-result-{call_id}",
                tool_call_id=call_id,
                content=content,
            ),
        ],
        tools=[],
        context=[],
        forwardedProps={},
    )


def test_handle_tool_result_consumes_exact_named_call_and_yields_all_events() -> None:
    class _FakeAgent:
        def __init__(self) -> None:
            self.removed: list[tuple[str, str]] = []
            self.captured: list[tuple[RunAgentInput, dict[str, str]]] = []
            self.public_run_calls = 0

        async def is_pending_tool_call(self, thread_id: str, call_id: str) -> bool:
            return (thread_id, call_id) == (
                "thread-a",
                "call-a::thread-a",
            )

        async def pending_tool_call_name(
            self, thread_id: str, call_id: str
        ) -> str | None:
            return "requestTaskApproval"

        def _get_session_metadata(self, thread_id: str) -> dict[str, str]:
            return {"app_name": "app", "user_id": "user"}

        async def _remove_pending_tool_call(
            self, thread_id: str, call_id: str
        ) -> bool:
            self.removed.append((thread_id, call_id))
            return True

        async def _handle_tool_result_submission(
            self,
            run_input: RunAgentInput,
            *,
            trusted_tool_names: dict[str, str] | None = None,
        ):
            self.captured.append((run_input, dict(trusted_tool_names or {})))
            yield {"type": "RUN_STARTED"}
            yield {"type": "RUN_FINISHED"}

        async def run(self, run_input: RunAgentInput):
            self.public_run_calls += 1
            yield {"type": "FORBIDDEN_PUBLIC_RUN"}

    fake = _FakeAgent()

    async def collect() -> list[object]:
        return [
            event
            async for event in ADKAgent.handle_tool_result(
                fake,  # type: ignore[arg-type]
                "thread-a",
                "call-a::thread-a",
                '{"approved":true}',
            )
        ]

    assert asyncio.run(collect()) == [
        {"type": "RUN_STARTED"},
        {"type": "RUN_FINISHED"},
    ]
    assert fake.removed == [("thread-a", "call-a::thread-a")]
    assert fake.public_run_calls == 0
    run_input, trusted_names = fake.captured[0]
    assert trusted_names == {"call-a": "requestTaskApproval"}
    assert run_input.thread_id == "thread-a"
    assert run_input.messages[-1].tool_call_id == "call-a"
    assistant = run_input.messages[-2]
    assert assistant.tool_calls[0].id == "call-a"
    assert assistant.tool_calls[0].function.name == "requestTaskApproval"


@pytest.mark.parametrize(
    ("session_id", "encoded_call_id", "pending", "pending_name"),
    [
        ("thread-a", "missing::thread-a", False, "requestTaskApproval"),
        ("thread-a", "call-a::thread-b", True, "requestTaskApproval"),
        ("thread-a", "call-a::thread-a", True, None),
        ("thread-a", "call-a::thread-a", True, "unknown"),
    ],
)
def test_handle_tool_result_rejects_untrusted_binding_before_consumption(
    session_id: str,
    encoded_call_id: str,
    pending: bool,
    pending_name: str | None,
) -> None:
    class _FakeAgent:
        def __init__(self) -> None:
            self.remove_calls = 0
            self.continuation_calls = 0

        async def is_pending_tool_call(self, thread_id: str, call_id: str) -> bool:
            return pending

        async def pending_tool_call_name(
            self, thread_id: str, call_id: str
        ) -> str | None:
            return pending_name

        def _get_session_metadata(self, thread_id: str) -> dict[str, str]:
            return {"app_name": "app", "user_id": "user"}

        async def _remove_pending_tool_call(
            self, thread_id: str, call_id: str
        ) -> bool:
            self.remove_calls += 1
            return True

        async def _handle_tool_result_submission(self, *args: object, **kwargs: object):
            self.continuation_calls += 1
            yield {"type": "MUST_NOT_RUN"}

        async def run(self, run_input: RunAgentInput):
            self.continuation_calls += 1
            yield {"type": "MUST_NOT_RUN"}

    fake = _FakeAgent()

    async def collect() -> None:
        async for _ in ADKAgent.handle_tool_result(
            fake,  # type: ignore[arg-type]
            session_id,
            encoded_call_id,
            '{"approved":true}',
        ):
            pass

    with pytest.raises(PermissionError):
        asyncio.run(collect())
    assert fake.remove_calls == 0
    assert fake.continuation_calls == 0


def test_handle_tool_result_stops_when_exact_consumption_fails() -> None:
    class _FakeAgent:
        async def is_pending_tool_call(self, thread_id: str, call_id: str) -> bool:
            return True

        async def pending_tool_call_name(
            self, thread_id: str, call_id: str
        ) -> str | None:
            return "requestTaskApproval"

        def _get_session_metadata(self, thread_id: str) -> dict[str, str]:
            return {"app_name": "app", "user_id": "user"}

        async def _remove_pending_tool_call(
            self, thread_id: str, call_id: str
        ) -> bool:
            return False

        async def _handle_tool_result_submission(self, *args: object, **kwargs: object):
            raise AssertionError("continuation must not start")
            yield

        async def run(self, run_input: RunAgentInput):
            raise AssertionError("continuation must not start")
            yield

    async def collect() -> None:
        async for _ in ADKAgent.handle_tool_result(
            _FakeAgent(),  # type: ignore[arg-type]
            "thread-a",
            "call-a::thread-a",
            '{"approved":true}',
        ):
            pass

    with pytest.raises(PermissionError, match="consumed"):
        asyncio.run(collect())


def test_public_run_and_untrusted_private_submission_reject_direct_tool_message() -> None:
    class _FakeAgent:
        def __init__(self) -> None:
            self.started = 0

        async def _handle_tool_result_submission(self, *args: object, **kwargs: object):
            self.started += 1
            yield {"type": "MUST_NOT_RUN"}

        async def _start_new_execution(self, *args: object, **kwargs: object):
            self.started += 1
            yield {"type": "MUST_NOT_RUN"}

        async def _extract_tool_results(self, *args: object, **kwargs: object):
            return []

    fake = _FakeAgent()
    run_input = _tool_input()

    async def public_collect() -> None:
        async for _ in ADKAgent.run(fake, run_input):  # type: ignore[arg-type]
            pass

    async def private_collect() -> None:
        async for _ in ADKAgent._handle_tool_result_submission(  # type: ignore[arg-type]
            fake,
            run_input,
        ):
            pass

    with pytest.raises(PermissionError, match="tool"):
        asyncio.run(public_collect())
    with pytest.raises(PermissionError, match="trusted"):
        asyncio.run(private_collect())
    assert fake.started == 0


def test_function_response_uses_trusted_name_and_invalid_json_is_non_reflective(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = object.__new__(ADKAgent)
    secret = "not-json SECRET_TOOL_RESULT_7f93"
    run_input = _tool_input(content=secret)

    content = asyncio.run(
        agent._build_tool_response_content(
            run_input,
            trusted_tool_names={"call-a": "requestTaskApproval"},
        )
    )

    function_response = content.parts[0].function_response
    assert function_response.name == "requestTaskApproval"
    serialized = str(function_response.response)
    assert "unknown" not in serialized
    assert secret not in serialized
    assert secret not in caplog.text


def test_function_response_rejects_forged_or_missing_tool_name() -> None:
    agent = object.__new__(ADKAgent)
    forged = _tool_input(tool_name="displayTimeSeriesChart")

    with pytest.raises(PermissionError, match="name"):
        asyncio.run(
            agent._build_tool_response_content(
                forged,
                trusted_tool_names={"call-a": "requestTaskApproval"},
            )
        )
    with pytest.raises(PermissionError, match="trusted"):
        asyncio.run(
            agent._build_tool_response_content(
                _tool_input(),
                trusted_tool_names={},
            )
        )


@pytest.mark.parametrize("write_result", [False, None])
def test_failed_pending_list_write_preserves_server_observed_tool_name(
    write_result: bool | None,
) -> None:
    encoded_call_id = "call-a::thread-a"

    class _SessionManager:
        def __init__(self) -> None:
            self.name_writes = 0

        async def get_state_value(self, *, key: str, **kwargs: object) -> object:
            if key == "pending_tool_calls":
                return [encoded_call_id]
            if key == "pending_tool_call_names":
                return {encoded_call_id: "requestTaskApproval"}
            raise AssertionError(key)

        async def set_state_value(
            self, *, key: str, value: object, **kwargs: object
        ) -> bool | None:
            if key == "pending_tool_calls":
                return write_result
            if key == "pending_tool_call_names":
                self.name_writes += 1
                return True
            raise AssertionError(key)

    agent = object.__new__(ADKAgent)
    manager = _SessionManager()
    agent._session_manager = manager  # type: ignore[attr-defined]
    agent._get_session_metadata = lambda thread_id: {  # type: ignore[method-assign]
        "app_name": "app",
        "user_id": "user",
    }

    removed = asyncio.run(
        agent._remove_pending_tool_call("thread-a", encoded_call_id)
    )

    assert removed is False
    assert manager.name_writes == 0
