"""Strict, versioned models for catalogs and reproducible workspace locks."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import PurePath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from telco_domain import assert_model_safe
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)


CATALOG_SCHEMA_VERSION = "1.0"
LOCK_SCHEMA_VERSION = "1.0"
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024 * 1024

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=64,
        pattern=(
            r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
            r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
            r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    ),
]
LockId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^lablock-[0-9a-fA-F]{64}$",
    ),
]


_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError("timestamp must be RFC 3339 text")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC 3339 text") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


UtcDatetime = Annotated[
    datetime,
    BeforeValidator(_parse_datetime),
    AfterValidator(_utc),
]


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError("review date must not include a time")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("review date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("review date must use YYYY-MM-DD") from exc


ReviewedDate = Annotated[date, BeforeValidator(_parse_date)]


def _safe_human_text(value: str) -> str:
    if any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("text contains prohibited characters")
    assert_model_safe(value)
    return value


HumanText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
    AfterValidator(_safe_human_text),
]


def _safe_filename_value(value: str) -> str:
    if (
        value in {".", ".."}
        or PurePath(value).name != value
        or "/" in value
        or "\\" in value
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)
    ):
        raise ValueError("filename must be a safe basename")
    return value


SafeFilename = Annotated[
    str,
    StringConstraints(min_length=1, max_length=180),
    AfterValidator(_safe_filename_value),
]


def validate_https_url(value: str, *, allowed_hosts: tuple[str, ...] | None = None) -> str:
    """Validate an HTTPS URL without performing DNS or network operations."""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are prohibited")
    if parsed.fragment:
        raise ValueError("URL fragments are prohibited")
    if port not in (None, 443):
        raise ValueError("URL port is prohibited")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("local URL hosts are prohibited")
    legacy_ipv4_parts = host.split(".")
    if 1 <= len(legacy_ipv4_parts) <= 4 and all(
        re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", part)
        for part in legacy_ipv4_parts
    ):
        # urllib/socket accept decimal, octal, hexadecimal and shortened IPv4
        # spellings. Reject all such ambiguous forms instead of trying to
        # maintain an equivalent parser and risking a loopback/private bypass.
        raise ValueError("legacy IP URL hosts are prohibited")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("non-public URL addresses are prohibited")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError("URL host is not allowlisted")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class LicenseSpec(_StrictModel):
    id: Identifier
    name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    evidence_url: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    evidence_sha256: Sha256Digest
    attribution: HumanText
    reviewed_at: ReviewedDate
    acceptance_required: StrictBool

    @field_validator("acceptance_required")
    @classmethod
    def _requires_acceptance(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("license acceptance must be required")
        return value

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        return _safe_human_text(value)

    @field_validator("url", "evidence_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        validated = validate_https_url(value)
        if urlsplit(validated).query:
            raise ValueError("license URLs must not contain a query")
        return validated


class CatalogResource(_StrictModel):
    resource_id: Identifier
    dataset_id: Identifier
    dataset_version: Identifier
    filename: SafeFilename
    source_url: Annotated[
        str, StringConstraints(min_length=1, max_length=4096)
    ] = Field(repr=False)
    allowed_hosts: tuple[Annotated[str, StringConstraints(min_length=1, max_length=253)], ...]
    sha256: Sha256Digest
    size_bytes: int = Field(ge=1, le=MAX_DOWNLOAD_BYTES)
    media_type: Annotated[
        str,
        StringConstraints(
            min_length=3,
            max_length=128,
            pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
        ),
    ]
    adapter: Identifier
    license: LicenseSpec

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _hosts_from_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("allowed_hosts")
    @classmethod
    def _safe_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(host.rstrip(".").lower() for host in hosts)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("allowed_hosts must be non-empty and unique")
        for host in normalized:
            if (
                "*" in host
                or not re.fullmatch(
                    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    host,
                )
            ):
                raise ValueError("allowed host is invalid")
        return normalized

    @model_validator(mode="after")
    def _safe_source(self) -> "CatalogResource":
        validate_https_url(self.source_url, allowed_hosts=self.allowed_hosts)
        return self


class DatasetCatalog(_StrictModel):
    schema_version: Literal["1.0"]
    catalog_id: Identifier
    catalog_version: SemanticVersion
    resources: tuple[CatalogResource, ...]

    @field_validator("resources", mode="before")
    @classmethod
    def _resources_from_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _unique_resources(self) -> "DatasetCatalog":
        ids = [resource.resource_id for resource in self.resources]
        filenames = [resource.filename.casefold() for resource in self.resources]
        if len(ids) != len(set(ids)) or len(filenames) != len(set(filenames)):
            raise ValueError("catalog resources and filenames must be unique")
        return self

    def resource(self, resource_id: str) -> CatalogResource | None:
        return next(
            (item for item in self.resources if item.resource_id == resource_id), None
        )


class LockedArtifact(_StrictModel):
    resource_id: Identifier
    dataset_id: Identifier
    dataset_version: Identifier
    filename: SafeFilename
    sha256: Sha256Digest
    size_bytes: int = Field(ge=1, le=MAX_DOWNLOAD_BYTES)
    media_type: Annotated[str, StringConstraints(min_length=3, max_length=128)]
    adapter: Identifier
    catalog_resource_sha256: Sha256Digest
    source_url_sha256: Sha256Digest
    allowed_hosts: tuple[Annotated[str, StringConstraints(min_length=1, max_length=253)], ...]
    license_id: Identifier
    license_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    license_url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    license_evidence_url: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    license_evidence_sha256: Sha256Digest
    license_attribution: HumanText
    license_reviewed_at: ReviewedDate
    fetched_at: UtcDatetime

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _hosts_from_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("license_url", "license_evidence_url")
    @classmethod
    def _valid_license_url(cls, value: str) -> str:
        validated = validate_https_url(value)
        if urlsplit(validated).query:
            raise ValueError("license URLs must not contain a query")
        return validated

    @field_validator("allowed_hosts")
    @classmethod
    def _valid_allowed_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(host.rstrip(".").lower() for host in hosts)
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("allowed_hosts must be non-empty and unique")
        for host in normalized:
            if (
                "*" in host
                or not re.fullmatch(
                    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    host,
                )
            ):
                raise ValueError("allowed host is invalid")
        return normalized


def _canonical_digest(domain: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    digest.update(b"\0")
    digest.update(encoded)
    return digest.hexdigest()


def catalog_resource_sha256(resource: CatalogResource) -> str:
    """Return a domain-separated fingerprint of the full catalog resource."""

    return _canonical_digest(
        "telco-lab:catalog-resource:v1",
        resource.model_dump(mode="json"),
    )


def source_url_sha256(source_url: str) -> str:
    """Bind a lock to a source URL without persisting URL query material."""

    return _canonical_digest("telco-lab:source-url:v1", source_url)


def workspace_lock_id(
    catalog_id: str,
    catalog_version: str,
    artifacts: tuple[LockedArtifact, ...],
) -> str:
    """Return a stable identity for catalog provenance and locked artifacts."""

    payload = {
        "catalog_id": catalog_id,
        "catalog_version": catalog_version,
        "artifacts": [
            artifact.model_dump(mode="json", exclude={"fetched_at"})
            for artifact in sorted(artifacts, key=lambda item: item.resource_id)
        ],
    }
    return f"lablock-{_canonical_digest('telco-lab:workspace-lock:v1', payload)}"


class WorkspaceLock(_StrictModel):
    schema_version: Literal["1.0"]
    lock_id: LockId
    catalog_id: Identifier
    catalog_version: SemanticVersion
    generated_at: UtcDatetime
    artifacts: tuple[LockedArtifact, ...]

    @field_validator("artifacts", mode="before")
    @classmethod
    def _artifacts_from_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _unique_artifacts(self) -> "WorkspaceLock":
        ids = [item.resource_id for item in self.artifacts]
        names = [item.filename.casefold() for item in self.artifacts]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("locked resources and filenames must be unique")
        if self.lock_id != workspace_lock_id(
            self.catalog_id,
            self.catalog_version,
            self.artifacts,
        ):
            raise ValueError("workspace lock identity is inconsistent")
        return self


# Public terminology alias: the workspace lock is the persisted artifact manifest.
WorkspaceManifest = WorkspaceLock


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "LOCK_SCHEMA_VERSION",
    "MAX_DOWNLOAD_BYTES",
    "CatalogResource",
    "DatasetCatalog",
    "LicenseSpec",
    "LockedArtifact",
    "WorkspaceLock",
    "WorkspaceManifest",
    "catalog_resource_sha256",
    "source_url_sha256",
    "validate_https_url",
    "workspace_lock_id",
]
