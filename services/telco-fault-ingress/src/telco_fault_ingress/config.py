"""Side-effect-free configuration for the fault ingress service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import os

from telco_domain import SensitiveDataError, assert_model_safe


class FaultPipelineMode(StrEnum):
    """Exactly one owner for each inbound fault event."""

    LEGACY = "legacy"
    SHADOW = "shadow"
    CANONICAL = "canonical"
    PAUSED = "paused"


def _positive_int(
    values: Mapping[str, str], name: str, default: int, *, maximum: int
) -> int:
    raw = values.get(name)
    try:
        value = default if raw is None else int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class FaultIngressConfig:
    """Validated public configuration; credentials are never represented here."""

    allowed_subscriptions: frozenset[str]
    mode: FaultPipelineMode = FaultPipelineMode.SHADOW
    actor: str = "fault-ingress"
    max_request_bytes: int = 262_144
    max_decoded_bytes: int = 262_144
    max_json_depth: int = 16
    max_event_age_seconds: int = 7 * 24 * 60 * 60
    max_future_skew_seconds: int = 5 * 60
    host: str = "127.0.0.1"
    port: int = 8080

    def __post_init__(self) -> None:
        if isinstance(self.allowed_subscriptions, (str, bytes, bytearray)):
            raise ValueError("allowed_subscriptions must be a collection")
        subscriptions = frozenset(
            item.strip() for item in self.allowed_subscriptions if item.strip()
        )
        if not subscriptions:
            raise ValueError("at least one Pub/Sub subscription must be allowed")
        if any(len(item) > 1024 for item in subscriptions):
            raise ValueError("Pub/Sub subscription identifiers are too long")
        object.__setattr__(self, "allowed_subscriptions", subscriptions)

        try:
            mode = FaultPipelineMode(self.mode)
        except ValueError:
            raise ValueError("unsupported fault pipeline mode") from None
        object.__setattr__(self, "mode", mode)

        actor = self.actor.strip()
        if not 1 <= len(actor) <= 256:
            raise ValueError("actor must contain between 1 and 256 characters")
        try:
            assert_model_safe(
                {"actor": actor, "subscriptions": sorted(subscriptions)}
            )
        except SensitiveDataError:
            raise ValueError("fault ingress configuration is unsafe") from None
        object.__setattr__(self, "actor", actor)

        if not 1 <= self.max_request_bytes <= 10_000_000:
            raise ValueError("max_request_bytes is outside the safe range")
        if not 1 <= self.max_decoded_bytes <= self.max_request_bytes:
            raise ValueError("max_decoded_bytes is outside the safe range")
        if not 2 <= self.max_json_depth <= 32:
            raise ValueError("max_json_depth must be between 2 and 32")
        if not 1 <= self.max_event_age_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("max_event_age_seconds must be between 1 and 604800")
        if not 1 <= self.max_future_skew_seconds <= 5 * 60:
            raise ValueError("max_future_skew_seconds must be between 1 and 300")
        if self.host.strip() != self.host or not self.host:
            raise ValueError("host is invalid")
        if not 1 <= self.port <= 65_535:
            raise ValueError("port is outside the valid range")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "FaultIngressConfig":
        values = os.environ if environ is None else environ
        raw_subscriptions = values.get("FAULT_ALLOWED_SUBSCRIPTIONS", "")
        subscriptions = frozenset(raw_subscriptions.split(","))
        raw_mode = values.get("FAULT_PIPELINE_MODE", FaultPipelineMode.SHADOW.value)
        try:
            mode = FaultPipelineMode(raw_mode.strip().lower())
        except ValueError:
            raise ValueError("FAULT_PIPELINE_MODE is unsupported") from None
        return cls(
            allowed_subscriptions=subscriptions,
            mode=mode,
            actor=values.get("FAULT_INGRESS_ACTOR", "fault-ingress"),
            max_request_bytes=_positive_int(
                values, "FAULT_MAX_REQUEST_BYTES", 262_144, maximum=10_000_000
            ),
            max_decoded_bytes=_positive_int(
                values, "FAULT_MAX_DECODED_BYTES", 262_144, maximum=10_000_000
            ),
            max_json_depth=_positive_int(
                values, "FAULT_MAX_JSON_DEPTH", 16, maximum=32
            ),
            max_event_age_seconds=_positive_int(
                values,
                "FAULT_MAX_EVENT_AGE_SECONDS",
                7 * 24 * 60 * 60,
                maximum=7 * 24 * 60 * 60,
            ),
            max_future_skew_seconds=_positive_int(
                values,
                "FAULT_MAX_FUTURE_SKEW_SECONDS",
                5 * 60,
                maximum=5 * 60,
            ),
            host=values.get("FAULT_INGRESS_HOST", "127.0.0.1"),
            port=_positive_int(values, "PORT", 8080, maximum=65_535),
        )


__all__ = ["FaultIngressConfig", "FaultPipelineMode"]
