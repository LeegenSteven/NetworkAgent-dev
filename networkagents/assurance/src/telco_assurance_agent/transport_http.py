"""Fail-closed Uvicorn h11 transport for the loopback Assurance server."""

from __future__ import annotations

import asyncio
import json
from typing import Final

import h11
from uvicorn.protocols.http.flow_control import FlowControl
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.protocols.utils import get_local_addr, get_remote_addr, is_ssl


LOCAL_HTTP_CONNECTION_CAP: Final = 32
LOCAL_HTTP_HEADER_DEADLINE_SECONDS: Final = 1.0

_ERRORS: Final = {
    400: (
        "Bad Request",
        "LOCAL_HTTP_BAD_REQUEST",
        "The HTTP request was invalid.",
    ),
    408: (
        "Request Timeout",
        "LOCAL_HTTP_HEADER_TIMEOUT",
        "The HTTP request headers timed out.",
    ),
    503: (
        "Service Unavailable",
        "LOCAL_HTTP_CONNECTION_BUSY",
        "The HTTP connection limit was reached.",
    ),
}


def _fixed_body(status_code: int) -> bytes:
    _reason, code, message = _ERRORS[status_code]
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


_FIXED_BODIES: Final = {
    status_code: _fixed_body(status_code) for status_code in _ERRORS
}


def _fixed_response(status_code: int) -> bytes:
    reason, _code, _message = _ERRORS[status_code]
    body = _FIXED_BODIES[status_code]
    headers = [
        f"HTTP/1.1 {status_code} {reason}\r\n".encode("ascii"),
        b"content-type: application/json\r\n",
        f"content-length: {len(body)}\r\n".encode("ascii"),
        b"cache-control: no-store\r\n",
        b"connection: close\r\n",
    ]
    if status_code == 503:
        headers.append(b"retry-after: 1\r\n")
    headers.extend((b"\r\n", body))
    return b"".join(headers)


_FIXED_RESPONSES: Final = {
    status_code: _fixed_response(status_code) for status_code in _ERRORS
}


class BoundedH11Protocol(H11Protocol):
    """Bound connections and apply an absolute deadline to every header."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._header_deadline: asyncio.TimerHandle | None = None
        self._fixed_response_sent = False
        self._over_limit = False
        self._awaiting_keepalive_header = False

    def _initialize_rejected_transport(self, transport: asyncio.Transport) -> None:
        """Initialize an over-cap transport without entering Uvicorn's set."""

        self.transport = transport
        self.flow = FlowControl(transport)
        self.server = get_local_addr(transport)
        self.client = get_remote_addr(transport)
        self.scheme = "https" if is_ssl(transport) else "http"

    def _cancel_header_deadline(self) -> None:
        deadline = self._header_deadline
        if deadline is not None:
            deadline.cancel()
            self._header_deadline = None

    def _start_header_deadline(self) -> None:
        self._cancel_header_deadline()
        if self.transport is None or self.transport.is_closing():
            return
        self._header_deadline = self.loop.call_at(
            self.loop.time() + LOCAL_HTTP_HEADER_DEADLINE_SECONDS,
            self._header_deadline_expired,
        )

    def _header_deadline_expired(self) -> None:
        self._header_deadline = None
        self._send_fixed_error(408)

    def _send_fixed_error(self, status_code: int) -> None:
        if (
            self._fixed_response_sent
            or self.transport is None
            or self.transport.is_closing()
        ):
            return
        self._fixed_response_sent = True
        self._awaiting_keepalive_header = False
        self._cancel_header_deadline()
        self._unset_keepalive_if_required()
        reason, _code, _message = _ERRORS[status_code]
        body = _FIXED_BODIES[status_code]
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"connection", b"close"),
        ]
        if status_code == 503:
            headers.append((b"retry-after", b"1"))
        try:
            output = self.conn.send(
                h11.Response(
                    status_code=status_code,
                    headers=headers,
                    reason=reason.encode("ascii"),
                )
            )
            output += self.conn.send(h11.Data(data=body))
            output += self.conn.send(h11.EndOfMessage())
        except h11.LocalProtocolError:
            # A malformed peer can put h11 into ERROR before this fail-closed
            # path runs. The same constant envelope is safe to send raw.
            output = _FIXED_RESPONSES[status_code]
        self.transport.write(output)
        self.transport.close()

    def connection_made(  # type: ignore[override]
        self,
        transport: asyncio.Transport,
    ) -> None:
        was_full = len(self.connections) >= LOCAL_HTTP_CONNECTION_CAP
        if was_full:
            # Never enter the shared admitted set, even transiently. A small,
            # best-effort fixed response is written and the transport closes
            # immediately. On some TCP stacks a peer that has already queued
            # unread bytes may observe a reset instead of the response; no
            # rejected socket is retained merely to improve error delivery.
            self._initialize_rejected_transport(transport)
            self._over_limit = True
            self._send_fixed_error(503)
            return
        super().connection_made(transport)
        self._awaiting_keepalive_header = False
        self._start_header_deadline()

    def connection_lost(self, exc: Exception | None) -> None:
        self._awaiting_keepalive_header = False
        self._cancel_header_deadline()
        self._unset_keepalive_if_required()
        super().connection_lost(exc)

    def data_received(self, data: bytes) -> None:
        if self._fixed_response_sent:
            return
        if self._over_limit:
            self._send_fixed_error(503)
            return
        if self._awaiting_keepalive_header:
            # Idle keep-alive is governed by Uvicorn's five-second timer. The
            # absolute header budget begins only with the next request byte.
            self._awaiting_keepalive_header = False
            self._start_header_deadline()
        previous_cycle = self.cycle
        super().data_received(data)
        if self.cycle is not previous_cycle:
            self._cancel_header_deadline()

    def on_response_complete(self) -> None:
        if self.conn.their_state not in (h11.DONE, h11.MUST_CLOSE):
            # An application may reject a method or path without consuming a
            # declared/chunked request body. Never leave that body able to
            # drip indefinitely after the response has completed.
            self.server_state.total_requests += 1
            self._awaiting_keepalive_header = False
            self._cancel_header_deadline()
            self._unset_keepalive_if_required()
            self.transport.close()
            return
        previous_cycle = self.cycle
        super().on_response_complete()
        if self.cycle is not previous_cycle:
            # A pipelined keep-alive request may have been parsed synchronously
            # by the parent implementation while completing this response.
            self._awaiting_keepalive_header = False
            self._cancel_header_deadline()
        elif (
            not self.transport.is_closing()
            and self.conn.our_state is h11.IDLE
            and self.conn.their_state is h11.IDLE
        ):
            buffered, _closed = self.conn.trailing_data
            if buffered:
                # A partial pipelined header was already buffered behind the
                # completed response. Its actionable absolute budget begins
                # now and is not reset by later chunks.
                self._awaiting_keepalive_header = False
                self._start_header_deadline()
            else:
                self._awaiting_keepalive_header = True
                self._cancel_header_deadline()
        else:
            self._awaiting_keepalive_header = False
            self._cancel_header_deadline()

    def send_400_response(self, _message: str) -> None:
        self._send_fixed_error(400)

    def shutdown(self) -> None:
        self._awaiting_keepalive_header = False
        self._cancel_header_deadline()
        super().shutdown()

    def timeout_keep_alive_handler(self) -> None:
        self._awaiting_keepalive_header = False
        self._cancel_header_deadline()
        super().timeout_keep_alive_handler()


__all__ = [
    "BoundedH11Protocol",
    "LOCAL_HTTP_CONNECTION_CAP",
    "LOCAL_HTTP_HEADER_DEADLINE_SECONDS",
]
