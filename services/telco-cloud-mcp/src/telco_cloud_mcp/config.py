"""Side-effect-free configuration for the read-only MCP process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os


def _port(values: Mapping[str, str]) -> int:
    try:
        value = int(values.get("PORT", "8080"))
    except ValueError:
        raise ValueError("PORT must be an integer") from None
    if not 1 <= value <= 65_535:
        raise ValueError("PORT is outside the valid range")
    return value


@dataclass(frozen=True, slots=True)
class CloudMcpConfig:
    host: str = "127.0.0.1"
    port: int = 8080

    def __post_init__(self) -> None:
        if not self.host or self.host.strip() != self.host:
            raise ValueError("host is invalid")
        if not 1 <= self.port <= 65_535:
            raise ValueError("port is outside the valid range")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "CloudMcpConfig":
        values = os.environ if environ is None else environ
        return cls(
            host=values.get("TELCO_CLOUD_MCP_HOST", "127.0.0.1"),
            port=_port(values),
        )


__all__ = ["CloudMcpConfig"]
