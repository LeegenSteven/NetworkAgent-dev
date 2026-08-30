from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

import pytest

import telco_lab.loopback_sink as sink_module
from telco_lab.loopback_sink import (
    LOCAL_REPLAY_OPERATION,
    MAX_REPLAY_HTTP_RESPONSE_BYTES,
    MAX_REPLAY_HTTP_TIMEOUT_SECONDS,
    MIN_REPLAY_HTTP_TIMEOUT_SECONDS,
    LoopbackHttpReplaySink,
    LoopbackHttpRequest,
    LoopbackHttpResponse,
    ReplayDeliveryCheckpoint,
    ReplayDeliveryError,
    deliver_replay_plan,
)
from telco_lab.replay import build_replay_plan
from telco_lab.schema import canonical_json_bytes

from test_replay import REPLAY_START, _plan, _policy, _source


class _FakeTransport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.requests: list[LoopbackHttpRequest] = []
        self.thread_ids: list[int] = []

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse:
        self.requests.append(request)
        self.thread_ids.append(threading.get_ident())
        response = (
            self.responses.pop(0)
            if self.responses
            else LoopbackHttpResponse(status_code=202, body=b"{}")
        )
        if isinstance(response, BaseException):
            raise response
        return response  # type: ignore[return-value]


def _emit(sink: LoopbackHttpReplaySink, event) -> object:  # noqa: ANN001
    return asyncio.run(sink.emit(event))


def test_emit_revalidates_event_and_builds_fixed_bounded_request(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    transport = _FakeTransport(LoopbackHttpResponse(status_code=202, body=b"{}"))
    sink = LoopbackHttpReplaySink(
        plan.policy,
        transport=transport,
        environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"},
        timeout_seconds=1,
        max_response_bytes=32,
    )
    caller_thread = threading.get_ident()

    receipt = _emit(sink, plan.events[0])

    assert receipt.status_code == 202
    assert receipt.sequence_number == 1
    assert receipt.response_bytes == 2
    assert transport.thread_ids != [caller_thread]
    request = transport.requests[0]
    assert request.scheme == "http"
    assert request.connect_host == "127.0.0.1"
    assert request.port == 9080
    assert request.target == "/v1/faults/replay"
    assert request.timeout_seconds == 1
    assert request.max_response_bytes == 32
    assert request.body == canonical_json_bytes(plan.events[0].sink_payload())
    headers = dict(request.headers)
    assert headers["Content-Type"] == "application/json"
    assert headers["Idempotency-Key"] == plan.events[0].idempotency_key
    assert headers["X-NetworkAgent-Local-Operation"] == LOCAL_REPLAY_OPERATION
    assert headers["Content-Length"] == str(len(request.body))
    assert headers["Connection"] == "close"


def test_sink_requires_policy_and_hard_timeout_response_budgets() -> None:
    invalid = (
        {"policy": "http://127.0.0.1:9080/replay"},
        {"policy": _policy(), "timeout_seconds": True},
        {
            "policy": _policy(),
            "timeout_seconds": MIN_REPLAY_HTTP_TIMEOUT_SECONDS - 0.01,
        },
        {
            "policy": _policy(),
            "timeout_seconds": MAX_REPLAY_HTTP_TIMEOUT_SECONDS + 0.01,
        },
        {"policy": _policy(), "max_response_bytes": 0},
        {
            "policy": _policy(),
            "max_response_bytes": MAX_REPLAY_HTTP_RESPONSE_BYTES + 1,
        },
    )
    for arguments in invalid:
        with pytest.raises(ReplayDeliveryError) as caught:
            LoopbackHttpReplaySink(**arguments)  # type: ignore[arg-type]
        assert caught.value.code == "replay_delivery_arguments_invalid"


def test_environment_and_event_are_revalidated_before_every_transport_call(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace"))
    environment: dict[str, str] = {}
    transport = _FakeTransport()
    sink = LoopbackHttpReplaySink(
        plan.policy,
        transport=transport,
        environ=environment,
    )

    _emit(sink, plan.events[0])
    environment["GOOGLE_CLOUD_PROJECT"] = "must-not-leak"
    with pytest.raises(ReplayDeliveryError) as unsafe_environment:
        _emit(sink, plan.events[1])
    assert unsafe_environment.value.code == "replay_delivery_environment_unsafe"
    assert "must-not-leak" not in str(unsafe_environment.value)
    assert len(transport.requests) == 1

    environment.clear()
    compromised = plan.events[1].model_copy(update={"payload_sha256": "0" * 64})
    with pytest.raises(ReplayDeliveryError) as unsafe_event:
        _emit(sink, compromised)
    assert unsafe_event.value.code == "replay_delivery_event_invalid"
    assert len(transport.requests) == 1


def test_explicit_environment_cannot_hide_unsafe_process_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    transport = _FakeTransport()
    sink = LoopbackHttpReplaySink(
        plan.policy,
        transport=transport,
        environ={},
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "must-not-leak.json")

    with pytest.raises(ReplayDeliveryError) as caught:
        _emit(sink, plan.events[0])

    assert caught.value.code == "replay_delivery_environment_unsafe"
    assert "must-not-leak" not in str(caught.value)
    assert transport.requests == []


def test_loopback_resolution_is_rechecked_and_must_remain_loopback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy(endpoint="http://localhost:9080/replay")
    plan = _plan(_source(tmp_path / "workspace"), policy=policy)
    transport = _FakeTransport()
    sink = LoopbackHttpReplaySink(policy, transport=transport, environ={})
    calls = 0

    def public_resolution(*_args, **_kwargs):  # noqa: ANN002,ANN003
        nonlocal calls
        calls += 1
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("203.0.113.10", 9080),
            )
        ]

    monkeypatch.setattr(sink_module.socket, "getaddrinfo", public_resolution)

    for event in plan.events[:2]:
        with pytest.raises(ReplayDeliveryError) as caught:
            _emit(sink, event)
        assert caught.value.code == "replay_delivery_endpoint_unsafe"
        assert "203.0.113.10" not in str(caught.value)
    assert calls == 2
    assert transport.requests == []


def test_policy_and_raw_endpoint_are_revalidated_before_transport(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    transport = _FakeTransport()
    sink = LoopbackHttpReplaySink(plan.policy, transport=transport, environ={})
    object.__setattr__(
        sink,
        "_policy",
        plan.policy.model_copy(
            update={"endpoint": "http://example.invalid:9080/replay"}
        ),
    )
    with pytest.raises(ReplayDeliveryError) as changed_policy:
        _emit(sink, plan.events[0])
    assert changed_policy.value.code == "replay_delivery_policy_invalid"
    assert "example.invalid" not in str(changed_policy.value)

    raw_control_policy = _policy(endpoint="http://127.0.0.1:9080/re\r\nplay")
    controlled = LoopbackHttpReplaySink(
        raw_control_policy,
        transport=transport,
        environ={},
    )
    with pytest.raises(ReplayDeliveryError) as raw_control:
        _emit(controlled, plan.events[0])
    assert raw_control.value.code == "replay_delivery_endpoint_unsafe"
    assert transport.requests == []


@pytest.mark.parametrize(
    ("response", "code"),
    (
        (
            LoopbackHttpResponse(status_code=301, body=b"redirect"),
            "replay_delivery_redirect",
        ),
        (LoopbackHttpResponse(status_code=200, body=b"ok"), "replay_delivery_status"),
        (
            LoopbackHttpResponse(status_code=500, body=b"secret"),
            "replay_delivery_status",
        ),
        (
            LoopbackHttpResponse(
                status_code=202,
                body=b"x" * (MAX_REPLAY_HTTP_RESPONSE_BYTES + 1),
            ),
            "replay_delivery_response_limit",
        ),
        (
            LoopbackHttpResponse(
                status_code=202,
                headers=(("X-Too-Large", "x" * 40_000),),
            ),
            "replay_delivery_response_limit",
        ),
        (
            LoopbackHttpResponse(
                status_code=202,
                headers=(("malformed",),),  # type: ignore[arg-type]
            ),
            "replay_delivery_transport_invalid",
        ),
        (TimeoutError("secret timeout target"), "replay_delivery_timeout"),
        (OSError("secret network target"), "replay_delivery_network"),
        (object(), "replay_delivery_transport_invalid"),
    ),
)
def test_delivery_failures_have_fixed_non_reflective_codes(
    tmp_path,
    response: object,
    code: str,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / code))
    sink = LoopbackHttpReplaySink(
        plan.policy,
        transport=_FakeTransport(response),
        environ={},
        max_response_bytes=MAX_REPLAY_HTTP_RESPONSE_BYTES,
    )

    with pytest.raises(ReplayDeliveryError) as caught:
        _emit(sink, plan.events[0])

    assert caught.value.code == code
    rendered = str(caught.value)
    assert "secret" not in rendered
    assert plan.policy.endpoint not in rendered
    assert plan.events[0].source_event_id not in rendered


def test_204_is_success_and_request_policy_budget_is_rechecked(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    accepted = LoopbackHttpReplaySink(
        plan.policy,
        transport=_FakeTransport(LoopbackHttpResponse(status_code=204)),
        environ={},
    )
    receipt = _emit(accepted, plan.events[0])
    assert receipt.status_code == 204
    assert receipt.response_bytes == 0

    too_small = LoopbackHttpReplaySink(
        _policy(max_payload_bytes=1),
        transport=_FakeTransport(),
        environ={},
    )
    with pytest.raises(ReplayDeliveryError) as caught:
        _emit(too_small, plan.events[0])
    assert caught.value.code == "replay_delivery_payload_limit"


class _RecordingHandler(BaseHTTPRequestHandler):
    status_code = 202
    response_body = b"{}"
    include_content_length = True
    calls: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).calls.append(
            {
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )
        self.send_response(type(self).status_code)
        if 300 <= type(self).status_code < 400:
            self.send_header("Location", "https://example.invalid/must-not-follow")
        if type(self).include_content_length:
            self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _local_server(
    *,
    status_code: int = 202,
    response_body: bytes = b"{}",
    include_content_length: bool = True,
) -> Iterator[tuple[str, type[_RecordingHandler]]]:
    handler = type("ReplayHandler", (_RecordingHandler,), {})
    handler.status_code = status_code
    handler.response_body = response_body
    handler.include_content_length = include_content_length
    handler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/events", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_transport_posts_directly_without_proxy_or_redirect(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with _local_server() as (endpoint, handler):
        policy = _policy(endpoint=endpoint)
        plan = _plan(_source(tmp_path / "direct"), policy=policy)
        sink = LoopbackHttpReplaySink(policy, environ={}, timeout_seconds=2)
        receipt = _emit(sink, plan.events[0])

        assert receipt.status_code == 202
        assert len(handler.calls) == 1
        request = handler.calls[0]
        assert request["path"] == "/events"
        assert request["body"] == canonical_json_bytes(plan.events[0].sink_payload())
        headers = request["headers"]
        assert headers["Content-Type"] == "application/json"
        assert headers["Idempotency-Key"] == plan.events[0].idempotency_key
        assert headers["X-NetworkAgent-Local-Operation"] == LOCAL_REPLAY_OPERATION

    with _local_server(status_code=302) as (endpoint, handler):
        policy = _policy(endpoint=endpoint)
        plan = _plan(_source(tmp_path / "redirect"), policy=policy)
        sink = LoopbackHttpReplaySink(policy, environ={}, timeout_seconds=2)
        with pytest.raises(ReplayDeliveryError) as caught:
            _emit(sink, plan.events[0])
        assert caught.value.code == "replay_delivery_redirect"
        assert len(handler.calls) == 1


def test_default_transport_bounds_streamed_response_without_content_length(
    tmp_path,
) -> None:  # noqa: ANN001
    with _local_server(
        response_body=b"123456789",
        include_content_length=False,
    ) as (endpoint, handler):
        policy = _policy(endpoint=endpoint)
        plan = _plan(_source(tmp_path / "response-budget"), policy=policy)
        sink = LoopbackHttpReplaySink(
            policy,
            environ={},
            timeout_seconds=2,
            max_response_bytes=8,
        )
        with pytest.raises(ReplayDeliveryError) as caught:
            _emit(sink, plan.events[0])
        assert caught.value.code == "replay_delivery_response_limit"
        assert len(handler.calls) == 1


def test_plan_helper_supports_order_resume_and_bounded_duplicate_selection(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace"))

    ordered_transport = _FakeTransport()
    ordered_sink = LoopbackHttpReplaySink(
        plan.policy, transport=ordered_transport, environ={}
    )
    ordered = asyncio.run(deliver_replay_plan(plan, ordered_sink))
    assert ordered.selected_count == 3
    assert ordered.attempted_count == 3
    assert ordered.delivered_count == 3
    assert ordered.checkpoint.sequence_number == 3
    assert ordered.checkpoint.plan_id == plan.plan_id
    assert ordered.checkpoint.source_event_id == plan.events[-1].source_event_id
    assert ordered.checkpoint.payload_sha256 == plan.events[-1].payload_sha256
    assert ordered.selection_complete
    assert ordered.plan_complete
    assert [
        dict(request.headers)["Idempotency-Key"]
        for request in ordered_transport.requests
    ] == [event.idempotency_key for event in plan.events]

    resume_transport = _FakeTransport()
    resumed = asyncio.run(
        deliver_replay_plan(
            plan,
            LoopbackHttpReplaySink(plan.policy, transport=resume_transport, environ={}),
            checkpoint=ReplayDeliveryCheckpoint(
                plan_id=plan.plan_id,
                sequence_number=1,
                source_event_id=plan.events[0].source_event_id,
                payload_sha256=plan.events[0].payload_sha256,
            ),
        )
    )
    assert resumed.selected_count == 2
    assert resumed.checkpoint.sequence_number == 3
    assert [request.body for request in resume_transport.requests] == [
        canonical_json_bytes(event.sink_payload()) for event in plan.events[1:]
    ]

    duplicate_transport = _FakeTransport()
    duplicate = asyncio.run(
        deliver_replay_plan(
            plan,
            LoopbackHttpReplaySink(
                plan.policy, transport=duplicate_transport, environ={}
            ),
            sequence_numbers=[2, 1, 2],
        )
    )
    assert duplicate.selected_count == 3
    assert duplicate.delivered_count == 3
    assert duplicate.checkpoint.sequence_number == 2
    assert duplicate.selection_complete
    assert not duplicate.plan_complete


def test_plan_helper_stops_once_and_returns_continuation_checkpoint_on_failure(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace"))
    failing_transport = _FakeTransport(
        LoopbackHttpResponse(status_code=202),
        LoopbackHttpResponse(status_code=500, body=b"must-not-leak"),
        LoopbackHttpResponse(status_code=202),
    )
    failed = asyncio.run(
        deliver_replay_plan(
            plan,
            LoopbackHttpReplaySink(
                plan.policy, transport=failing_transport, environ={}
            ),
        )
    )

    assert failed.selected_count == 3
    assert failed.attempted_count == 2
    assert failed.delivered_count == 1
    assert failed.checkpoint.sequence_number == 1
    assert failed.checkpoint.plan_id == plan.plan_id
    assert failed.checkpoint.source_event_id == plan.events[0].source_event_id
    assert failed.checkpoint.payload_sha256 == plan.events[0].payload_sha256
    assert failed.failed_sequence_number == 2
    assert failed.error_code == "replay_delivery_status"
    assert not failed.selection_complete
    assert not failed.plan_complete
    assert len(failing_transport.requests) == 2

    recovered_transport = _FakeTransport()
    recovered = asyncio.run(
        deliver_replay_plan(
            plan,
            LoopbackHttpReplaySink(
                plan.policy, transport=recovered_transport, environ={}
            ),
            checkpoint=failed.checkpoint,
        )
    )
    assert recovered.checkpoint.sequence_number == 3
    assert recovered.plan_complete
    assert len(recovered_transport.requests) == 2


def test_first_failure_returns_canonical_zero_checkpoint(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    transport = _FakeTransport(LoopbackHttpResponse(status_code=500))
    result = asyncio.run(
        deliver_replay_plan(
            plan,
            LoopbackHttpReplaySink(plan.policy, transport=transport, environ={}),
        )
    )

    assert result.checkpoint == ReplayDeliveryCheckpoint(
        plan_id=plan.plan_id,
        sequence_number=0,
        source_event_id=None,
        payload_sha256=None,
    )
    assert result.attempted_count == 1
    assert result.delivered_count == 0
    assert result.failed_sequence_number == 1
    assert result.error_code == "replay_delivery_status"
    assert len(transport.requests) == 1


def test_checkpoint_rejects_cross_plan_and_old_replay_window_before_transport(
    tmp_path,
) -> None:  # noqa: ANN001
    source = _source(tmp_path / "workspace")
    plan = _plan(source)
    checkpoint = ReplayDeliveryCheckpoint(
        plan_id=plan.plan_id,
        sequence_number=1,
        source_event_id=plan.events[0].source_event_id,
        payload_sha256=plan.events[0].payload_sha256,
    )
    cross_policy = _policy(endpoint="http://127.0.0.1:9081/v1/faults/replay")
    cross_plan = _plan(source, policy=cross_policy)
    old_window_plan = build_replay_plan(
        source.lab,
        source.bundle,
        scenario="detector-demo",
        replay_window_start=REPLAY_START - timedelta(days=1),
        policy=plan.policy,
        environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"},
    )

    for candidate in (cross_plan, old_window_plan):
        transport = _FakeTransport()
        sink = LoopbackHttpReplaySink(
            candidate.policy,
            transport=transport,
            environ={},
        )
        with pytest.raises(ReplayDeliveryError) as caught:
            asyncio.run(deliver_replay_plan(candidate, sink, checkpoint=checkpoint))
        assert caught.value.code == "replay_delivery_checkpoint_invalid"
        assert transport.requests == []


@pytest.mark.parametrize(
    "checkpoint_factory",
    (
        lambda plan: len(plan.events),
        lambda _plan: 0,
        lambda plan: ReplayDeliveryCheckpoint(
            plan_id=plan.plan_id,
            sequence_number=len(plan.events),
            source_event_id=None,
            payload_sha256=None,
        ),
        lambda plan: ReplayDeliveryCheckpoint(
            plan_id=plan.plan_id,
            sequence_number=len(plan.events),
            source_event_id=plan.events[0].source_event_id,
            payload_sha256=plan.events[-1].payload_sha256,
        ),
        lambda plan: ReplayDeliveryCheckpoint(
            plan_id=plan.plan_id,
            sequence_number=1,
            source_event_id=plan.events[0].source_event_id,
            payload_sha256="0" * 64,
        ),
        lambda plan: ReplayDeliveryCheckpoint(
            plan_id=plan.plan_id,
            sequence_number=0,
            source_event_id=plan.events[0].source_event_id,
            payload_sha256=plan.events[0].payload_sha256,
        ),
    ),
)
def test_forged_checkpoint_and_bare_integer_fail_before_transport(
    tmp_path,
    checkpoint_factory,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace"))
    checkpoint = checkpoint_factory(plan)
    transport = _FakeTransport()
    sink = LoopbackHttpReplaySink(plan.policy, transport=transport, environ={})

    with pytest.raises(ReplayDeliveryError) as caught:
        asyncio.run(deliver_replay_plan(plan, sink, checkpoint=checkpoint))

    assert caught.value.code == "replay_delivery_checkpoint_invalid"
    assert transport.requests == []


def test_helper_rejects_mismatched_policy_and_exports_public_api(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    mismatched = LoopbackHttpReplaySink(
        _policy(endpoint="http://127.0.0.1:9081/replay"),
        transport=_FakeTransport(),
        environ={},
    )

    with pytest.raises(ReplayDeliveryError) as caught:
        asyncio.run(deliver_replay_plan(plan, mismatched))
    assert caught.value.code == "replay_delivery_plan_invalid"

    from telco_lab import (
        LoopbackHttpReplaySink as ExportedSink,
        ReplayDeliveryCheckpoint as ExportedCheckpoint,
        ReplayDeliveryError as ExportedError,
        deliver_replay_plan as exported_deliver,
    )

    assert ExportedSink is LoopbackHttpReplaySink
    assert ExportedCheckpoint is ReplayDeliveryCheckpoint
    assert ExportedError is ReplayDeliveryError
    assert exported_deliver is deliver_replay_plan
