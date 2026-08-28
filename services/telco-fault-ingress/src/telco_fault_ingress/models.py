"""Small transport models that never retain the raw HTTP request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from telco_cloud import IngestResult, SourceEventEnvelope


class IngressError(ValueError):
    """A safe error carrying a fixed, non-reflective public code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PermanentIngressError(IngressError):
    """A poison event that should eventually be dead-lettered."""


class TransientIngressError(IngressError):
    """A dependency failure that should be retried."""


@dataclass(frozen=True, slots=True)
class ParsedPubSubPush:
    subscription: str
    message_id: str
    publish_time: datetime
    delivery_attempt: int | None
    attributes: Mapping[str, str]
    payload: Mapping[str, Any]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class IngressDecision:
    http_status: int
    code: str
    result: "IngestResult | None" = None


class EventIngestRepository(Protocol):
    async def ingest(
        self,
        envelope: "SourceEventEnvelope",
        *,
        shadow: bool = False,
        actor: str = "fault-ingress",
        reason: str = "source event ingestion",
        idempotency_key: str | None = None,
        outbox_payload: Mapping[str, object] | None = None,
    ) -> "IngestResult": ...


LegacyHandler = Callable[[ParsedPubSubPush], Awaitable[object]]


__all__ = [
    "EventIngestRepository",
    "IngressDecision",
    "IngressError",
    "LegacyHandler",
    "ParsedPubSubPush",
    "PermanentIngressError",
    "TransientIngressError",
]
