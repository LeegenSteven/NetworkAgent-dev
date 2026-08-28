"""Explicit Cloud Profile configuration with no credential or I/O side effects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
import os


_PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_RESOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,28}[a-z0-9]$")
_EMULATOR_PATTERN = re.compile(
    r"^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\]):[1-9][0-9]{0,4}$"
)
_DATABASE_ROLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _required(name: str, value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be explicitly configured")
    normalized = value.strip()
    if not pattern.fullmatch(normalized):
        raise ValueError(f"{name} has an invalid Google Cloud resource ID")
    return normalized


@dataclass(frozen=True, slots=True)
class CloudProfileConfig:
    """Identifiers required to compose an injected Spanner database handle.

    The class deliberately does not construct a Google client, load credentials,
    or modify ``SPANNER_EMULATOR_HOST``. Process entry points own those actions.
    """

    project_id: str
    instance_id: str
    database_id: str
    database_role: str | None = None
    emulator_host: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            _required("project_id", self.project_id, _PROJECT_PATTERN),
        )
        object.__setattr__(
            self,
            "instance_id",
            _required("instance_id", self.instance_id, _RESOURCE_PATTERN),
        )
        object.__setattr__(
            self,
            "database_id",
            _required("database_id", self.database_id, _RESOURCE_PATTERN),
        )
        if self.emulator_host is not None:
            normalized = str(self.emulator_host).strip().lower()
            if not _EMULATOR_PATTERN.fullmatch(normalized):
                raise ValueError(
                    "emulator_host must be an explicit loopback host and port"
                )
            port = int(normalized.rsplit(":", 1)[1])
            if port > 65535:
                raise ValueError("emulator_host port must be between 1 and 65535")
            object.__setattr__(self, "emulator_host", normalized)
        if self.database_role is not None:
            normalized_role = str(self.database_role).strip()
            if (
                not _DATABASE_ROLE_PATTERN.fullmatch(normalized_role)
                or normalized_role.lower() == "public"
                or normalized_role.lower().startswith("spanner_")
            ):
                raise ValueError("database_role is invalid or reserved")
            object.__setattr__(self, "database_role", normalized_role)
        if self.emulator_host is None and self.database_role is None:
            raise ValueError(
                "database_role (TELCO_SPANNER_DATABASE_ROLE) must be explicitly "
                "configured outside the emulator"
            )

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "CloudProfileConfig":
        """Parse an explicit mapping without mutating the process environment."""

        source = os.environ if environ is None else environ

        def require_env(name: str, *fallbacks: str) -> str:
            for candidate in (name, *fallbacks):
                value = source.get(candidate)
                if value is not None and value.strip():
                    return value
            raise ValueError(f"{name} must be explicitly configured")

        return cls(
            project_id=require_env("GOOGLE_PROJECT", "GOOGLE_CLOUD_PROJECT"),
            instance_id=require_env("GOOGLE_SPANNER_INSTANCE"),
            database_id=require_env("GOOGLE_SPANNER_DATABASE"),
            database_role=source.get("TELCO_SPANNER_DATABASE_ROLE") or None,
            emulator_host=source.get("SPANNER_EMULATOR_HOST") or None,
        )


@dataclass(frozen=True, slots=True)
class CloudSchemaAdminConfig:
    """Resource-only configuration for the explicit schema administration CLI."""

    project_id: str
    instance_id: str
    database_id: str
    emulator_host: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "project_id", _required("project_id", self.project_id, _PROJECT_PATTERN)
        )
        object.__setattr__(
            self,
            "instance_id",
            _required("instance_id", self.instance_id, _RESOURCE_PATTERN),
        )
        object.__setattr__(
            self,
            "database_id",
            _required("database_id", self.database_id, _RESOURCE_PATTERN),
        )
        if self.emulator_host is not None:
            normalized = str(self.emulator_host).strip().lower()
            if not _EMULATOR_PATTERN.fullmatch(normalized):
                raise ValueError(
                    "emulator_host must be an explicit loopback host and port"
                )
            port = int(normalized.rsplit(":", 1)[1])
            if port > 65535:
                raise ValueError("emulator_host port must be between 1 and 65535")
            object.__setattr__(self, "emulator_host", normalized)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "CloudSchemaAdminConfig":
        source = os.environ if environ is None else environ
        if (source.get("TELCO_SPANNER_DATABASE_ROLE") or "").strip():
            raise ValueError(
                "schema administration rejects TELCO_SPANNER_DATABASE_ROLE"
            )

        def require_env(name: str, *fallbacks: str) -> str:
            for candidate in (name, *fallbacks):
                value = source.get(candidate)
                if value is not None and value.strip():
                    return value
            raise ValueError(f"{name} must be explicitly configured")

        return cls(
            project_id=require_env("GOOGLE_PROJECT", "GOOGLE_CLOUD_PROJECT"),
            instance_id=require_env("GOOGLE_SPANNER_INSTANCE"),
            database_id=require_env("GOOGLE_SPANNER_DATABASE"),
            emulator_host=source.get("SPANNER_EMULATOR_HOST") or None,
        )


def compose_spanner_database(
    config: CloudProfileConfig, *, client: object | None = None
) -> object:
    """Create a role-scoped database handle at an explicit composition root.

    Importing this module and constructing ``CloudProfileConfig`` remain inert.
    When an emulator is selected, its endpoint must already be present in the
    process environment so the Google client consistently routes both data and
    admin APIs without this helper mutating global state.
    """

    profile = config
    if profile.emulator_host is not None:
        process_host = os.environ.get("SPANNER_EMULATOR_HOST")
        if (
            process_host is None
            or process_host.strip().lower() != profile.emulator_host
        ):
            raise ValueError(
                "SPANNER_EMULATOR_HOST must exist and match CloudProfileConfig"
            )
    if client is None:
        from google.cloud import spanner

        client = spanner.Client(project=profile.project_id)
    instance = client.instance(profile.instance_id)
    return instance.database(
        profile.database_id,
        database_role=profile.database_role,
    )


def compose_spanner_admin_database(
    config: CloudSchemaAdminConfig, *, client: object | None = None
) -> object:
    """Create an unscoped handle only for an explicit schema-admin process."""

    if config.emulator_host is not None:
        process_host = os.environ.get("SPANNER_EMULATOR_HOST")
        if (
            process_host is None
            or process_host.strip().lower() != config.emulator_host
        ):
            raise ValueError(
                "SPANNER_EMULATOR_HOST must exist and match CloudSchemaAdminConfig"
            )
    if client is None:
        from google.cloud import spanner

        client = spanner.Client(project=config.project_id)
    instance = client.instance(config.instance_id)
    return instance.database(config.database_id, database_role=None)


__all__ = [
    "CloudProfileConfig",
    "CloudSchemaAdminConfig",
    "compose_spanner_admin_database",
    "compose_spanner_database",
]
