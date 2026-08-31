"""Strict loopback-only HTTP delivery for an already validated replay plan.

This module is a transport boundary only, not a Canonical Fault business
receiver.  It does not create incidents, call the local governance engine, or
connect replay events to any Cloud ingress.
Every emission revalidates the policy, process/environment guard, event
identity and resolved socket address before one bounded HTTP request is made.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import http.client
from ipaddress import ip_address
import math
import socket
import ssl
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import ValidationError

from .local_trace import (
    LOCAL_REPLAY_TRACE_HEADER,
    LocalRuntimeTraceClock,
    LocalRuntimeTraceSink,
    derive_local_replay_trace_id,
    emit_local_runtime_trace_event,
)
from .replay import (
    HARD_MAX_PAYLOAD_BYTES,
    ReplayError,
    ReplayEvent,
    ReplayPlan,
    ReplayPolicy,
    validate_replay_environment,
)
from .schema import canonical_json_bytes


LOCAL_REPLAY_OPERATION = "replay-v1"
MIN_REPLAY_HTTP_TIMEOUT_SECONDS = 1.0
MAX_REPLAY_HTTP_TIMEOUT_SECONDS = 30.0
MAX_REPLAY_HTTP_REQUEST_BYTES = HARD_MAX_PAYLOAD_BYTES
MAX_REPLAY_HTTP_RESPONSE_BYTES = 64 * 1024
MAX_REPLAY_HTTP_RESPONSE_HEADERS_BYTES = 32 * 1024

_DELIVERY_MESSAGES: Mapping[str, str] = {
    "replay_delivery_arguments_invalid": "replay delivery arguments are invalid",
    "replay_delivery_policy_invalid": "replay delivery policy validation failed",
    "replay_delivery_environment_unsafe": "replay delivery environment is not local-only",
    "replay_delivery_event_invalid": "replay delivery event validation failed",
    "replay_delivery_endpoint_unsafe": "replay delivery endpoint is not loopback-only",
    "replay_delivery_payload_limit": "replay delivery payload exceeds its fixed limit",
    "replay_delivery_response_limit": "replay delivery response exceeds its fixed limit",
    "replay_delivery_timeout": "the local replay request timed out",
    "replay_delivery_network": "the local replay request failed",
    "replay_delivery_redirect": "the local replay endpoint returned a redirect",
    "replay_delivery_status": "the local replay endpoint rejected the event",
    "replay_delivery_transport_invalid": "the replay transport returned an invalid response",
    "replay_delivery_plan_invalid": "replay delivery plan validation failed",
    "replay_delivery_checkpoint_invalid": "replay delivery checkpoint validation failed",
    "replay_delivery_sequence_invalid": "replay delivery sequence is invalid",
    "replay_pacing_arguments_invalid": "replay pacing arguments are invalid",
    "replay_pacing_clock_invalid": "replay pacing clock validation failed",
    "replay_pacing_cancelled": "the paced replay run was cancelled",
    "replay_pacing_deadline_exceeded": "the paced replay deadline was reached",
}


class ReplayDeliveryError(RuntimeError):
    """A fixed-code transport error that never reflects a URL or body."""

    def __init__(self, code: str) -> None:
        if code not in _DELIVERY_MESSAGES:
            code = "replay_delivery_transport_invalid"
        self.code = code
        super().__init__(_DELIVERY_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class LoopbackHttpRequest:
    """Fully resolved, immutable input for a synchronous HTTP transport."""

    scheme: Literal["http", "https"]
    connect_host: str
    server_hostname: str
    port: int
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    timeout_seconds: float
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class LoopbackHttpResponse:
    """Bounded response material returned by an injected transport."""

    status_code: int
    body: bytes = b""
    headers: tuple[tuple[str, str], ...] = ()


class LoopbackHttpTransport(Protocol):
    """Synchronous transport protocol used through :func:`asyncio.to_thread`."""

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse: ...


@dataclass(frozen=True, slots=True)
class ReplayDeliveryReceipt:
    """Non-sensitive acknowledgement for one accepted replay event."""

    sequence_number: int
    source_event_id: str
    status_code: Literal[202, 204]
    response_bytes: int


@dataclass(frozen=True, slots=True)
class ReplayDeliveryCheckpoint:
    """Caller-owned claim for the highest contiguous accepted plan event.

    The plan/event binding prevents accidental continuation across a different
    endpoint, replay window, or event sequence.  It is not an authenticated
    acknowledgement from the receiving process.
    """

    plan_id: str
    sequence_number: int
    source_event_id: str | None
    payload_sha256: str | None


@dataclass(frozen=True, slots=True)
class ReplayDeliveryResult:
    """Bounded outcome for one finite delivery selection.

    Event delivery errors are returned here with the caller's continuation
    checkpoint.  The helper never retries automatically; the caller must
    explicitly resume.  Invalid plan/helper arguments still raise
    :class:`ReplayDeliveryError`.
    """

    checkpoint: ReplayDeliveryCheckpoint
    selected_count: int
    attempted_count: int
    delivered_count: int
    failed_sequence_number: int | None
    error_code: str | None
    selection_complete: bool
    plan_complete: bool


@dataclass(frozen=True, slots=True)
class _PinnedEndpoint:
    scheme: Literal["http", "https"]
    connect_host: str
    server_hostname: str
    port: int
    target: str
    host_header: str


def _number_in_range(value: object, minimum: float, maximum: float) -> float:
    if type(value) not in {int, float}:
        raise ReplayDeliveryError("replay_delivery_arguments_invalid")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ReplayDeliveryError("replay_delivery_arguments_invalid")
    return normalized


def _integer_in_range(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise ReplayDeliveryError("replay_delivery_arguments_invalid")
    if not minimum <= value <= maximum:
        raise ReplayDeliveryError("replay_delivery_arguments_invalid")
    return value


def _normalized_policy(value: object, *, constructor: bool = False) -> ReplayPolicy:
    code = (
        "replay_delivery_arguments_invalid"
        if constructor
        else "replay_delivery_policy_invalid"
    )
    if type(value) is not ReplayPolicy:
        raise ReplayDeliveryError(code)
    try:
        return ReplayPolicy.model_validate(value.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise ReplayDeliveryError(code) from None


def _pinned_loopback_endpoint(policy: ReplayPolicy) -> _PinnedEndpoint:
    """Resolve once for this request and reject every non-loopback answer."""

    try:
        raw_endpoint = policy.endpoint
        raw_endpoint.encode("ascii", errors="strict")
        if any(
            ord(character) <= 0x20 or ord(character) == 0x7F
            for character in raw_endpoint
        ):
            raise ValueError("endpoint contains a control character")
        parsed = urlsplit(raw_endpoint)
        hostname = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or hostname is None
            or port is None
            or not 1 <= port <= 65_535
        ):
            raise ValueError("invalid endpoint")
        target = parsed.path or "/"
        target.encode("ascii", errors="strict")
        if (
            not target.startswith("/")
            or target.startswith("//")
            or "\\" in target
            or any(
                ord(character) < 0x21 or ord(character) > 0x7E for character in target
            )
        ):
            raise ValueError("invalid request target")

        answers = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        if type(answers) is not list or not 1 <= len(answers) <= 16:
            raise ValueError("endpoint resolution is not bounded")
        resolved: set[tuple[int, str]] = set()
        for family, socktype, _protocol, _canonical, sockaddr in answers:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                raise ValueError("invalid address family")
            if socktype != socket.SOCK_STREAM or not sockaddr:
                raise ValueError("invalid socket type")
            address_text = str(sockaddr[0])
            if "%" in address_text:
                raise ValueError("scoped addresses are not permitted")
            address = ip_address(address_text)
            if not address.is_loopback:
                raise ValueError("address is not loopback")
            resolved.add((address.version, address.compressed))
        if not resolved:
            raise ValueError("endpoint did not resolve")
        # Prefer IPv4 only for deterministic cross-platform tests.  Both
        # families have already been proven loopback-only.
        _version, connect_host = sorted(
            resolved, key=lambda item: (0 if item[0] == 4 else 1, item[1])
        )[0]
        normalized_hostname = hostname.rstrip(".").lower()
        host_header = (
            f"[{normalized_hostname}]:{port}"
            if ":" in normalized_hostname
            else f"{normalized_hostname}:{port}"
        )
        return _PinnedEndpoint(
            scheme=parsed.scheme,  # type: ignore[arg-type]
            connect_host=connect_host,
            server_hostname=normalized_hostname,
            port=port,
            target=target,
            host_header=host_header,
        )
    except ReplayDeliveryError:
        raise
    except Exception:
        raise ReplayDeliveryError("replay_delivery_endpoint_unsafe") from None


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    """TLS connection pinned to the address validated for this emission."""

    def __init__(self, endpoint: _PinnedEndpoint, timeout: float) -> None:
        super().__init__(
            endpoint.server_hostname,
            endpoint.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_connect_host = endpoint.connect_host

    def connect(self) -> None:
        if self._tunnel_host is not None:
            raise ReplayDeliveryError("replay_delivery_endpoint_unsafe")
        self.sock = socket.create_connection(
            (self._pinned_connect_host, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self.host,
        )


def _response_header_bytes(headers: Sequence[tuple[str, str]]) -> int:
    if type(headers) is not tuple:
        raise ReplayDeliveryError("replay_delivery_transport_invalid")
    if len(headers) > 100:
        raise ReplayDeliveryError("replay_delivery_response_limit")
    total = 0
    for item in headers:
        if type(item) is not tuple or len(item) != 2:
            raise ReplayDeliveryError("replay_delivery_transport_invalid")
        name, value = item
        if type(name) is not str or type(value) is not str:
            raise ReplayDeliveryError("replay_delivery_transport_invalid")
        try:
            total += len(name.encode("ascii")) + len(value.encode("latin-1")) + 4
        except (UnicodeEncodeError, ValueError):
            raise ReplayDeliveryError("replay_delivery_transport_invalid") from None
        if total > MAX_REPLAY_HTTP_RESPONSE_HEADERS_BYTES:
            raise ReplayDeliveryError("replay_delivery_response_limit")
    return total


class _StdlibLoopbackTransport:
    """Direct stdlib transport: no proxy discovery and no redirect handler."""

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse:
        if type(request) is not LoopbackHttpRequest:
            raise ReplayDeliveryError("replay_delivery_transport_invalid")
        endpoint = _PinnedEndpoint(
            scheme=request.scheme,
            connect_host=request.connect_host,
            server_hostname=request.server_hostname,
            port=request.port,
            target=request.target,
            host_header=dict(request.headers).get("Host", ""),
        )
        connection: http.client.HTTPConnection
        if request.scheme == "https":
            connection = _PinnedHttpsConnection(endpoint, request.timeout_seconds)
        else:
            connection = http.client.HTTPConnection(
                request.connect_host,
                request.port,
                timeout=request.timeout_seconds,
            )
        try:
            connection.putrequest(
                "POST",
                request.target,
                skip_host=True,
                skip_accept_encoding=True,
            )
            for name, value in request.headers:
                connection.putheader(name, value)
            connection.endheaders(request.body)
            response = connection.getresponse()
            headers = tuple(
                (str(name), str(value)) for name, value in response.getheaders()
            )
            _response_header_bytes(headers)
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length, 10)
                except (TypeError, ValueError):
                    raise ReplayDeliveryError(
                        "replay_delivery_transport_invalid"
                    ) from None
                if declared_length < 0:
                    raise ReplayDeliveryError("replay_delivery_transport_invalid")
                if declared_length > request.max_response_bytes:
                    raise ReplayDeliveryError("replay_delivery_response_limit")
            body = response.read(request.max_response_bytes + 1)
            if type(body) is not bytes:
                raise ReplayDeliveryError("replay_delivery_transport_invalid")
            if len(body) > request.max_response_bytes:
                raise ReplayDeliveryError("replay_delivery_response_limit")
            return LoopbackHttpResponse(
                status_code=response.status,
                body=body,
                headers=headers,
            )
        finally:
            connection.close()


class LoopbackHttpReplaySink:
    """HTTP replay sink restricted to the endpoint embedded in a policy."""

    def __init__(
        self,
        policy: ReplayPolicy,
        *,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = MAX_REPLAY_HTTP_RESPONSE_BYTES,
        transport: LoopbackHttpTransport | None = None,
        environ: Mapping[str, str] | None = None,
        runtime_trace_sink: LocalRuntimeTraceSink | None = None,
        runtime_trace_clock: LocalRuntimeTraceClock | None = None,
    ) -> None:
        self._policy = _normalized_policy(policy, constructor=True)
        self._timeout_seconds = _number_in_range(
            timeout_seconds,
            MIN_REPLAY_HTTP_TIMEOUT_SECONDS,
            MAX_REPLAY_HTTP_TIMEOUT_SECONDS,
        )
        self._max_response_bytes = _integer_in_range(
            max_response_bytes,
            1,
            MAX_REPLAY_HTTP_RESPONSE_BYTES,
        )
        if transport is None:
            self._transport: LoopbackHttpTransport = _StdlibLoopbackTransport()
        else:
            try:
                send = getattr(transport, "send")
            except Exception:
                raise ReplayDeliveryError("replay_delivery_arguments_invalid") from None
            if not callable(send):
                raise ReplayDeliveryError("replay_delivery_arguments_invalid")
            self._transport = transport
        self._environ = environ
        self._runtime_trace_sink = runtime_trace_sink
        self._runtime_trace_clock = runtime_trace_clock

    @property
    def endpoint(self) -> str:
        return _normalized_policy(self._policy).endpoint

    @property
    def policy(self) -> ReplayPolicy:
        return _normalized_policy(self._policy)

    def _request(self, event: ReplayEvent) -> LoopbackHttpRequest:
        policy = _normalized_policy(self._policy)
        try:
            # An explicit test/host mapping may add stricter local assertions,
            # but it can never hide unsafe variables in the real process.
            validate_replay_environment(policy)
            if self._environ is not None:
                validate_replay_environment(policy, self._environ)
        except ReplayError:
            raise ReplayDeliveryError("replay_delivery_environment_unsafe") from None
        except Exception:
            raise ReplayDeliveryError("replay_delivery_environment_unsafe") from None

        if type(event) is not ReplayEvent:
            raise ReplayDeliveryError("replay_delivery_event_invalid")
        try:
            payload = ReplayEvent.sink_payload(event)
            body = canonical_json_bytes(payload)
        except Exception:
            raise ReplayDeliveryError("replay_delivery_event_invalid") from None
        request_limit = min(
            policy.max_payload_bytes,
            MAX_REPLAY_HTTP_REQUEST_BYTES,
        )
        if len(body) > request_limit:
            raise ReplayDeliveryError("replay_delivery_payload_limit")

        endpoint = _pinned_loopback_endpoint(policy)
        trace_id = derive_local_replay_trace_id(event.source_event_id)
        headers = (
            ("Host", endpoint.host_header),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Idempotency-Key", event.idempotency_key),
            ("X-NetworkAgent-Local-Operation", LOCAL_REPLAY_OPERATION),
            (LOCAL_REPLAY_TRACE_HEADER, trace_id),
            ("Connection", "close"),
        )
        emit_local_runtime_trace_event(
            self._runtime_trace_sink,
            trace_id=trace_id,
            component="sender",
            operation="REPLAY_REQUEST_VALIDATED",
            clock=self._runtime_trace_clock,
        )
        return LoopbackHttpRequest(
            scheme=endpoint.scheme,
            connect_host=endpoint.connect_host,
            server_hostname=endpoint.server_hostname,
            port=endpoint.port,
            target=endpoint.target,
            headers=headers,
            body=body,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )

    async def emit(self, event: ReplayEvent) -> ReplayDeliveryReceipt:
        """Deliver exactly one event once; no redirect or retry is attempted."""

        request = self._request(event)
        try:
            response = await asyncio.to_thread(self._transport.send, request)
        except asyncio.CancelledError:
            raise
        except ReplayDeliveryError:
            raise
        except (TimeoutError, socket.timeout):
            raise ReplayDeliveryError("replay_delivery_timeout") from None
        except (http.client.HTTPException, OSError, ssl.SSLError):
            raise ReplayDeliveryError("replay_delivery_network") from None
        except Exception:
            raise ReplayDeliveryError("replay_delivery_network") from None

        if type(response) is not LoopbackHttpResponse:
            raise ReplayDeliveryError("replay_delivery_transport_invalid")
        if (
            type(response.status_code) is not int
            or not 100 <= response.status_code <= 599
            or type(response.body) is not bytes
            or type(response.headers) is not tuple
        ):
            raise ReplayDeliveryError("replay_delivery_transport_invalid")
        _response_header_bytes(response.headers)
        if len(response.body) > self._max_response_bytes:
            raise ReplayDeliveryError("replay_delivery_response_limit")
        if 300 <= response.status_code <= 399:
            raise ReplayDeliveryError("replay_delivery_redirect")
        if response.status_code not in {202, 204}:
            raise ReplayDeliveryError("replay_delivery_status")
        emit_local_runtime_trace_event(
            self._runtime_trace_sink,
            trace_id=derive_local_replay_trace_id(event.source_event_id),
            component="sender",
            operation="REPLAY_DELIVERY_ACKNOWLEDGED",
            clock=self._runtime_trace_clock,
        )
        return ReplayDeliveryReceipt(
            sequence_number=event.sequence_number,
            source_event_id=event.source_event_id,
            status_code=response.status_code,  # type: ignore[arg-type]
            response_bytes=len(response.body),
        )


def _validated_plan(value: object) -> ReplayPlan:
    if type(value) is not ReplayPlan:
        raise ReplayDeliveryError("replay_delivery_plan_invalid")
    try:
        return value._validated_for_public_use()
    except Exception:
        raise ReplayDeliveryError("replay_delivery_plan_invalid") from None


def _checkpoint_for_sequence(
    plan: ReplayPlan,
    sequence_number: int,
) -> ReplayDeliveryCheckpoint:
    """Build the canonical continuation claim for one validated plan."""

    if type(sequence_number) is not int or not 0 <= sequence_number <= len(plan.events):
        raise ReplayDeliveryError("replay_delivery_checkpoint_invalid")
    if sequence_number == 0:
        return ReplayDeliveryCheckpoint(
            plan_id=plan.plan_id,
            sequence_number=0,
            source_event_id=None,
            payload_sha256=None,
        )
    event = plan.events[sequence_number - 1]
    return ReplayDeliveryCheckpoint(
        plan_id=plan.plan_id,
        sequence_number=sequence_number,
        source_event_id=event.source_event_id,
        payload_sha256=event.payload_sha256,
    )


def _validated_checkpoint(
    plan: ReplayPlan,
    value: object,
) -> ReplayDeliveryCheckpoint:
    """Fail closed unless every continuation field binds to this exact plan."""

    if value is None:
        return _checkpoint_for_sequence(plan, 0)
    if type(value) is not ReplayDeliveryCheckpoint:
        raise ReplayDeliveryError("replay_delivery_checkpoint_invalid")
    try:
        if (
            type(value.plan_id) is not str
            or type(value.sequence_number) is not int
            or (
                value.source_event_id is not None
                and type(value.source_event_id) is not str
            )
            or (
                value.payload_sha256 is not None
                and type(value.payload_sha256) is not str
            )
        ):
            raise ReplayDeliveryError("replay_delivery_checkpoint_invalid")
        expected = _checkpoint_for_sequence(plan, value.sequence_number)
        if value != expected:
            raise ReplayDeliveryError("replay_delivery_checkpoint_invalid")
        return expected
    except ReplayDeliveryError:
        raise
    except Exception:
        raise ReplayDeliveryError("replay_delivery_checkpoint_invalid") from None


async def deliver_replay_plan(
    plan: ReplayPlan,
    sink: LoopbackHttpReplaySink,
    *,
    checkpoint: ReplayDeliveryCheckpoint | None = None,
    sequence_numbers: Sequence[int] | None = None,
) -> ReplayDeliveryResult:
    """Deliver one finite plan selection immediately with no implicit retry.

    With no explicit selection, events after the exact plan-bound ``checkpoint``
    are sent in canonical plan order.  ``sequence_numbers`` delegates to the
    plan's already bounded duplicate/out-of-order selection contract.  The
    returned checkpoint advances only over a contiguous set of successful plan
    sequences, so it is safe to persist even after fault-injection orderings.
    It remains a caller-owned continuation claim, not an authenticated receiver
    acknowledgement.  This helper is intentionally serial but does not sleep
    until ``scheduled_offset_seconds``; plan speed/rate scheduling remains
    metadata for a future wall-clock runner.
    """

    normalized = _validated_plan(plan)
    if type(sink) is not LoopbackHttpReplaySink:
        raise ReplayDeliveryError("replay_delivery_plan_invalid")
    try:
        sink_policy = sink.policy
        if canonical_json_bytes(sink_policy) != canonical_json_bytes(normalized.policy):
            raise ReplayDeliveryError("replay_delivery_plan_invalid")
    except ReplayDeliveryError:
        raise
    except Exception:
        raise ReplayDeliveryError("replay_delivery_plan_invalid") from None

    normalized_checkpoint = _validated_checkpoint(normalized, checkpoint)
    checkpoint_sequence = normalized_checkpoint.sequence_number
    try:
        if sequence_numbers is None:
            selected = normalized.resume_after(checkpoint_sequence)
        else:
            selected = normalized.delivery_order(sequence_numbers)
    except ReplayError:
        raise ReplayDeliveryError("replay_delivery_sequence_invalid") from None
    except Exception:
        raise ReplayDeliveryError("replay_delivery_sequence_invalid") from None

    checkpoint = checkpoint_sequence
    successful_out_of_order: set[int] = set()
    attempted = 0
    delivered = 0
    for event in selected:
        attempted += 1
        try:
            receipt = await sink.emit(event)
            if type(receipt) is not ReplayDeliveryReceipt:
                raise ReplayDeliveryError("replay_delivery_transport_invalid")
        except asyncio.CancelledError:
            raise
        except ReplayDeliveryError as error:
            return ReplayDeliveryResult(
                checkpoint=_checkpoint_for_sequence(normalized, checkpoint),
                selected_count=len(selected),
                attempted_count=attempted,
                delivered_count=delivered,
                failed_sequence_number=event.sequence_number,
                error_code=error.code,
                selection_complete=False,
                plan_complete=checkpoint == len(normalized.events),
            )
        delivered += 1
        if event.sequence_number > checkpoint:
            successful_out_of_order.add(event.sequence_number)
        while checkpoint + 1 in successful_out_of_order:
            successful_out_of_order.remove(checkpoint + 1)
            checkpoint += 1

    return ReplayDeliveryResult(
        checkpoint=_checkpoint_for_sequence(normalized, checkpoint),
        selected_count=len(selected),
        attempted_count=attempted,
        delivered_count=delivered,
        failed_sequence_number=None,
        error_code=None,
        selection_complete=True,
        plan_complete=checkpoint == len(normalized.events),
    )


__all__ = [
    "LOCAL_REPLAY_OPERATION",
    "MAX_REPLAY_HTTP_REQUEST_BYTES",
    "MAX_REPLAY_HTTP_RESPONSE_BYTES",
    "MAX_REPLAY_HTTP_RESPONSE_HEADERS_BYTES",
    "MAX_REPLAY_HTTP_TIMEOUT_SECONDS",
    "MIN_REPLAY_HTTP_TIMEOUT_SECONDS",
    "LoopbackHttpReplaySink",
    "LoopbackHttpRequest",
    "LoopbackHttpResponse",
    "LoopbackHttpTransport",
    "ReplayDeliveryCheckpoint",
    "ReplayDeliveryError",
    "ReplayDeliveryReceipt",
    "ReplayDeliveryResult",
    "deliver_replay_plan",
]
