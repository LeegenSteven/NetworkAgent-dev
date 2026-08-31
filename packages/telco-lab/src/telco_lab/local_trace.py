"""Small, privacy-bounded runtime trace events for the local replay path.

The trace channel is deliberately best-effort.  It cannot change replay or
assurance business outcomes, and it does not persist data by itself.  A caller
may inject a synchronous sink to collect the fixed event contract as evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from typing import Literal, TypeAlias

from telco_domain import SensitiveDataError, assert_model_safe


LOCAL_RUNTIME_TRACE_SCHEMA = "networkagent-local-runtime-trace-event/1.0"
LOCAL_REPLAY_TRACE_HEADER = "X-NetworkAgent-Trace-Id"
MAX_LOCAL_RUNTIME_TRACE_ID_BYTES = 256
MAX_LOCAL_RUNTIME_TRACE_EVENT_BYTES = 1024

LOCAL_RUNTIME_TRACE_COMPONENTS = frozenset({"sender", "receiver", "repository", "a2a"})
LOCAL_RUNTIME_TRACE_OPERATIONS = frozenset(
    {
        "REPLAY_REQUEST_VALIDATED",
        "INCIDENT_DURABLE_READBACK",
        "REPLAY_RESPONSE_ACCEPTED",
        "REPLAY_DELIVERY_ACKNOWLEDGED",
        "ANALYZE_REQUEST_VALIDATED",
        "ANALYZE_COMPLETED",
    }
)
LOCAL_RUNTIME_TRACE_OUTCOMES = frozenset({"OK", "ERROR"})
LOCAL_RUNTIME_TRACE_ERROR_CODES = frozenset(
    {
        "LOCAL_FAULT_TRACE_CONFLICT",
        "REPLAY_DELIVERY_REJECTED",
        "ASSURANCE_ANALYZE_FAILED",
    }
)

_SOURCE_EVENT_ID = re.compile(r"^labevent-[0-9a-f]{64}$")
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_EMITTED_AT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T" r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_TRACE_DERIVATION_DOMAIN = b"telco-assurance:local-replay-trace:v1\0"

LocalRuntimeTraceComponent: TypeAlias = Literal[
    "sender", "receiver", "repository", "a2a"
]
LocalRuntimeTraceOperation: TypeAlias = Literal[
    "REPLAY_REQUEST_VALIDATED",
    "INCIDENT_DURABLE_READBACK",
    "REPLAY_RESPONSE_ACCEPTED",
    "REPLAY_DELIVERY_ACKNOWLEDGED",
    "ANALYZE_REQUEST_VALIDATED",
    "ANALYZE_COMPLETED",
]
LocalRuntimeTraceOutcome: TypeAlias = Literal["OK", "ERROR"]


class LocalRuntimeTraceError(ValueError):
    """Fixed-code validation failure that never reflects input values."""

    _CODES = frozenset(
        {
            "local_runtime_trace_event_invalid",
            "local_runtime_trace_source_event_invalid",
        }
    )

    def __init__(self, code: str) -> None:
        normalized = (
            code if code in self._CODES else "local_runtime_trace_event_invalid"
        )
        self.code = normalized
        super().__init__(normalized)


def _validate_text(value: object, allowed: frozenset[str] | None = None) -> str:
    if type(value) is not str:
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid") from None
    if (
        not encoded
        or len(encoded) > MAX_LOCAL_RUNTIME_TRACE_ID_BYTES
        or value != value.strip()
        or any(byte < 0x21 or byte > 0x7E for byte in encoded)
        or (allowed is not None and value not in allowed)
    ):
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
    return value


def _validate_emitted_at(value: object) -> str:
    if type(value) is not str or _EMITTED_AT.fullmatch(value) is None:
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid") from None
    if parsed.utcoffset() != timedelta(0):
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
    return value


@dataclass(frozen=True, slots=True)
class LocalRuntimeTraceEvent:
    """Exact seven-field local runtime event with fixed cardinality."""

    schema: str
    emitted_at: str
    trace_id: str
    component: LocalRuntimeTraceComponent
    operation: LocalRuntimeTraceOperation
    outcome: LocalRuntimeTraceOutcome
    error_code: str | None

    def __post_init__(self) -> None:
        if self.schema != LOCAL_RUNTIME_TRACE_SCHEMA:
            raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
        _validate_emitted_at(self.emitted_at)
        trace_id = _validate_text(self.trace_id)
        if _TRACE_ID.fullmatch(trace_id) is None:
            raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
        _validate_text(self.component, LOCAL_RUNTIME_TRACE_COMPONENTS)
        _validate_text(self.operation, LOCAL_RUNTIME_TRACE_OPERATIONS)
        _validate_text(self.outcome, LOCAL_RUNTIME_TRACE_OUTCOMES)
        if self.outcome == "OK":
            if self.error_code is not None:
                raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
        elif (
            type(self.error_code) is not str
            or self.error_code not in LOCAL_RUNTIME_TRACE_ERROR_CODES
        ):
            raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
        try:
            projection = self.as_dict()
            assert_model_safe(projection)
            encoded = json.dumps(
                projection,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
        except (SensitiveDataError, TypeError, ValueError):
            raise LocalRuntimeTraceError("local_runtime_trace_event_invalid") from None
        if len(encoded) > MAX_LOCAL_RUNTIME_TRACE_EVENT_BYTES:
            raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")

    def as_dict(self) -> dict[str, object]:
        """Return the exact wire/evidence projection in deterministic order."""

        return {
            "schema": self.schema,
            "emitted_at": self.emitted_at,
            "trace_id": self.trace_id,
            "component": self.component,
            "operation": self.operation,
            "outcome": self.outcome,
            "error_code": self.error_code,
        }


LocalRuntimeTraceSink: TypeAlias = Callable[[LocalRuntimeTraceEvent], object]
LocalRuntimeTraceClock: TypeAlias = Callable[[], datetime]


def derive_local_replay_trace_id(source_event_id: str) -> str:
    """Derive the frozen local trace identifier from a validated event ID."""

    if (
        type(source_event_id) is not str
        or _SOURCE_EVENT_ID.fullmatch(source_event_id) is None
    ):
        raise LocalRuntimeTraceError("local_runtime_trace_source_event_invalid")
    digest = hashlib.sha256(_TRACE_DERIVATION_DOMAIN + source_event_id.encode("ascii"))
    return f"local-replay-trace-{digest.hexdigest()}"


def _emitted_at(clock: LocalRuntimeTraceClock | None) -> str:
    value = datetime.now(UTC) if clock is None else clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
    if value.utcoffset() != timedelta(0):
        raise LocalRuntimeTraceError("local_runtime_trace_event_invalid")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def emit_local_runtime_trace_event(
    sink: LocalRuntimeTraceSink | None,
    *,
    trace_id: str,
    component: LocalRuntimeTraceComponent,
    operation: LocalRuntimeTraceOperation,
    outcome: LocalRuntimeTraceOutcome = "OK",
    error_code: str | None = None,
    clock: LocalRuntimeTraceClock | None = None,
) -> bool:
    """Best-effort synchronous emission; no ordinary failure escapes."""

    if sink is None:
        return False
    try:
        event = LocalRuntimeTraceEvent(
            schema=LOCAL_RUNTIME_TRACE_SCHEMA,
            emitted_at=_emitted_at(clock),
            trace_id=trace_id,
            component=component,
            operation=operation,
            outcome=outcome,
            error_code=error_code,
        )
        sink(event)
    except BaseException:
        return False
    return True


__all__ = [
    "LOCAL_REPLAY_TRACE_HEADER",
    "LOCAL_RUNTIME_TRACE_COMPONENTS",
    "LOCAL_RUNTIME_TRACE_ERROR_CODES",
    "LOCAL_RUNTIME_TRACE_OPERATIONS",
    "LOCAL_RUNTIME_TRACE_OUTCOMES",
    "LOCAL_RUNTIME_TRACE_SCHEMA",
    "MAX_LOCAL_RUNTIME_TRACE_ID_BYTES",
    "MAX_LOCAL_RUNTIME_TRACE_EVENT_BYTES",
    "LocalRuntimeTraceClock",
    "LocalRuntimeTraceComponent",
    "LocalRuntimeTraceError",
    "LocalRuntimeTraceEvent",
    "LocalRuntimeTraceOperation",
    "LocalRuntimeTraceOutcome",
    "LocalRuntimeTraceSink",
    "derive_local_replay_trace_id",
    "emit_local_runtime_trace_event",
]
