"""Explicit, side-effect-free configuration for the Assurance service."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from telco_domain import SensitiveDataError, assert_model_safe
from telco_local import LocalProfileConfig


def _is_loopback_host(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class AssuranceConfig:
    database_path: Path
    performance_csv_path: Path
    safe_trace_csv_path: Path
    rules_dir: Path
    public_url: str
    actor: str
    source_timezone: str = "UTC"
    documents_dir: Path | None = None
    challenge_ttl_seconds: int = 600
    pending_capacity: int = 1_000
    task_capacity: int = 1_000
    host: str = "127.0.0.1"
    port: int = 8085

    def __post_init__(self) -> None:
        profile = LocalProfileConfig(
            database_path=self.database_path,
            performance_csv_path=self.performance_csv_path,
            safe_trace_csv_path=self.safe_trace_csv_path,
            rules_dir=self.rules_dir,
            source_timezone=self.source_timezone,
            documents_dir=self.documents_dir,
        )
        object.__setattr__(self, "database_path", profile.database_path)
        object.__setattr__(self, "performance_csv_path", profile.performance_csv_path)
        object.__setattr__(self, "safe_trace_csv_path", profile.safe_trace_csv_path)
        object.__setattr__(self, "rules_dir", profile.rules_dir)
        object.__setattr__(self, "documents_dir", profile.documents_dir)
        object.__setattr__(self, "source_timezone", profile.source_timezone)

        supplied_url = self.public_url.strip()
        parsed = urlsplit(supplied_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not _is_loopback_host(parsed.hostname)
        ):
            raise ValueError(
                "public_url must be a trusted loopback HTTP(S) URL"
            )
        normalized_url = supplied_url.rstrip("/") + "/"
        object.__setattr__(self, "public_url", normalized_url)

        normalized_actor = self.actor.strip()
        if not normalized_actor or len(normalized_actor) > 256:
            raise ValueError("actor must contain between 1 and 256 characters")
        try:
            assert_model_safe(
                {"actor": normalized_actor, "public_url": normalized_url}
            )
        except SensitiveDataError:
            raise ValueError("Assurance public configuration is unsafe") from None
        object.__setattr__(self, "actor", normalized_actor)
        if not 1 <= self.challenge_ttl_seconds <= 900:
            raise ValueError("challenge_ttl_seconds must be between 1 and 900")
        if not 1 <= self.pending_capacity <= 100_000:
            raise ValueError("pending_capacity must be between 1 and 100000")
        if not 1 <= self.task_capacity <= 100_000:
            raise ValueError("task_capacity must be between 1 and 100000")
        normalized_host = self.host.strip()
        if not _is_loopback_host(normalized_host):
            raise ValueError("host must bind only to a loopback interface")
        object.__setattr__(self, "host", normalized_host)
        if not 1 <= self.port <= 65_535:
            raise ValueError("port must be between 1 and 65535")

    @property
    def local_profile_config(self) -> LocalProfileConfig:
        return LocalProfileConfig(
            database_path=self.database_path,
            performance_csv_path=self.performance_csv_path,
            safe_trace_csv_path=self.safe_trace_csv_path,
            rules_dir=self.rules_dir,
            source_timezone=self.source_timezone,
            documents_dir=self.documents_dir,
        )


__all__ = ["AssuranceConfig"]
