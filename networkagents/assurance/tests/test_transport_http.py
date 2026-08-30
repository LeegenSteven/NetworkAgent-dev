from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import uvicorn
import pytest

from telco_assurance_agent.transport_http import (
    LOCAL_HTTP_CONNECTION_CAP,
    LOCAL_HTTP_HEADER_DEADLINE_SECONDS,
    BoundedH11Protocol,
)
from telco_assurance_agent.boundary import SafeA2ARequestBoundary


def _receive_response(
    connection: socket.socket,
    buffered: bytes = b"",
) -> tuple[int, dict[str, str], bytes, bytes]:
    data = buffered
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            raise AssertionError("connection closed before response headers")
        data += chunk
    header_block, data = data.split(b"\r\n\r\n", 1)
    lines = header_block.split(b"\r\n")
    status = int(lines[0].split(b" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, value = line.split(b":", 1)
        headers[key.decode("ascii").lower()] = value.decode("ascii").strip()
    length = int(headers["content-length"])
    while len(data) < length:
        chunk = connection.recv(4096)
        if not chunk:
            raise AssertionError("connection closed before response body")
        data += chunk
    return status, headers, data[:length], data[length:]


async def _ok_application(scope, receive, send) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect" or not message.get("more_body", False):
            break
    body = b'{"ok":true}'
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


@contextmanager
def _serve(application=_ok_application) -> Iterator[tuple[int, uvicorn.Server]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            interface="asgi3",
            lifespan="off",
            http=BoundedH11Protocol,
            ws="none",
            access_log=False,
            log_level="critical",
            server_header=False,
            date_header=False,
            proxy_headers=False,
            forwarded_allow_ips="",
            limit_concurrency=None,
            timeout_keep_alive=5,
            h11_max_incomplete_event_size=16_384,
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        listener.close()
        raise AssertionError("bounded h11 server did not start")
    try:
        yield port, server
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=2)
        assert not thread.is_alive()


def _connect(port: int) -> socket.socket:
    connection = socket.create_connection(("127.0.0.1", port), timeout=2)
    connection.settimeout(LOCAL_HTTP_HEADER_DEADLINE_SECONDS + 2)
    return connection


def _assert_fixed_error(
    response: tuple[int, dict[str, str], bytes, bytes],
    *,
    status: int,
    code: str,
) -> None:
    actual_status, headers, body, remainder = response
    assert actual_status == status
    assert headers["content-type"] == "application/json"
    assert headers["connection"] == "close"
    assert remainder == b""
    assert json.loads(body) == {
        "ok": False,
        "error": {
            "code": code,
            "message": (
                "The HTTP connection limit was reached."
                if status == 503
                else "The HTTP request headers timed out."
            ),
        },
    }


def test_real_tcp_caps_40_partial_headers_times_out_and_reuses_slots() -> None:
    assert LOCAL_HTTP_CONNECTION_CAP == 32
    with _serve() as (port, server):
        connections: list[socket.socket] = []
        try:
            for index in range(40):
                connection = _connect(port)
                connection.sendall(
                    f"GET /partial-{index} HTTP/1.1\r\nHost: 127.0.0.1".encode("ascii")
                )
                connections.append(connection)

            assert len(server.server_state.connections) <= LOCAL_HTTP_CONNECTION_CAP
            responses = []
            overflow_closed_without_body = 0
            for connection in connections:
                try:
                    responses.append(_receive_response(connection))
                except (AssertionError, ConnectionError):
                    # Windows can turn the immediate over-cap close into a
                    # reset when request bytes were already queued. Retaining
                    # an extra transport to improve error delivery would make
                    # the live-connection cap bypassable.
                    overflow_closed_without_body += 1
            statuses = [response[0] for response in responses]
            assert statuses.count(408) == 32
            assert statuses.count(503) + overflow_closed_without_body == 8
            for response in responses:
                _assert_fixed_error(
                    response,
                    status=response[0],
                    code=(
                        "LOCAL_HTTP_CONNECTION_BUSY"
                        if response[0] == 503
                        else "LOCAL_HTTP_HEADER_TIMEOUT"
                    ),
                )
                if response[0] == 503:
                    assert response[1]["retry-after"] == "1"

            # Every one of the forty client-side sockets has now observed a
            # fixed close or an immediate TCP reset; no hidden overflow cohort
            # remains live outside Uvicorn's admitted connection set.
            for connection in connections:
                connection.settimeout(0.25)
                try:
                    assert connection.recv(1) == b""
                except ConnectionError:
                    pass
        finally:
            for connection in connections:
                connection.close()

        deadline = time.monotonic() + 2
        while server.server_state.connections and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not server.server_state.connections

        reused = _connect(port)
        try:
            reused.sendall(
                b"GET /reused HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            )
            status, headers, body, _ = _receive_response(reused)
            assert status == 200
            assert headers["content-type"] == "application/json"
            assert body == b'{"ok":true}'
        finally:
            reused.close()


def test_real_tcp_keepalive_header_deadline_cancels_and_reuses_slot() -> None:
    with _serve() as (port, _server):
        connection = _connect(port)
        try:
            connection.sendall(
                b"GET /first HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Connection: keep-alive\r\n\r\n"
            )
            first = _receive_response(connection)
            assert first[0] == 200
            assert first[2] == b'{"ok":true}'

            # Idle keep-alive remains governed by the configured five-second
            # budget; the one-second header timer starts at the next byte.
            time.sleep(LOCAL_HTTP_HEADER_DEADLINE_SECONDS + 0.2)
            connection.settimeout(0.05)
            with pytest.raises(socket.timeout):
                connection.recv(1)
            connection.settimeout(LOCAL_HTTP_HEADER_DEADLINE_SECONDS + 2)

            connection.sendall(b"GET /second HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: ")
            _assert_fixed_error(
                _receive_response(connection, first[3]),
                status=408,
                code="LOCAL_HTTP_HEADER_TIMEOUT",
            )
        finally:
            connection.close()

        reused = _connect(port)
        try:
            reused.sendall(
                b"GET /after-timeout HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Connection: close\r\n\r\n"
            )
            assert _receive_response(reused)[0] == 200
        finally:
            reused.close()


def test_real_tcp_wrong_method_with_unfinished_body_is_json_and_closes() -> None:
    app = SafeA2ARequestBoundary(_ok_application)
    with _serve(app) as (port, server):
        connection = _connect(port)
        try:
            connection.sendall(
                b"PUT / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                b"4\r\nslow\r\n"
            )
            status, headers, body, remainder = _receive_response(connection)
            assert status == 405
            assert headers["content-type"] == "application/json"
            assert headers["connection"] == "close"
            assert headers["allow"] == "POST"
            assert remainder == b""
            assert json.loads(body) == {
                "ok": False,
                "error": {
                    "code": "A2A_HTTP_METHOD_NOT_ALLOWED",
                    "message": "Method not allowed.",
                },
            }
            assert connection.recv(1) == b""
        finally:
            connection.close()

        deadline = time.monotonic() + 1
        while server.server_state.connections and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not server.server_state.connections
