"""Deterministic, fail-closed plans for bounded local dataset replay.

This module has no transport implementation and performs no network I/O.  It
turns a fully verified :class:`~telco_lab.schema.LabBundle` into immutable
events that a caller-owned loopback sink may consume.  Ground truth is never
projected into a replay event.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
import hashlib
from ipaddress import ip_address
import math
import os
import re
from typing import Annotated, Any, Literal, Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from telco_domain import SensitiveDataError, Technology, assert_model_safe

from .adapters import (
    ADAPTER_VERSION,
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
    BUBBLERAN_SOURCE_LICENSE,
    adapt_bubbleran_persistent_interference_csv,
)
from .models import WorkspaceLock
from .schema import (
    MAX_METRICS_PER_OBSERVATION,
    MAX_QUALITY_FLAGS,
    LabBundle,
    MetricName,
    MetricUnit,
    SafeName,
    canonical_json_bytes,
)
from .workspace import TelcoLab


REPLAY_SCHEMA_VERSION = "1.0"

HARD_MAX_EVENTS = 10_000
HARD_MAX_RATE_PER_SECOND = 1_000.0
HARD_MAX_DURATION_SECONDS = 24 * 60 * 60
HARD_MAX_PAYLOAD_BYTES = 262_144
HARD_MAX_TOTAL_PAYLOAD_BYTES = 64 * 1024 * 1024
HARD_MAX_RESOURCES = 1_000
HARD_MAX_CONCURRENCY = 16
HARD_MAX_SPEED = 1_000.0

# Public names describe the supported contract; ``HARD_*`` remains an internal
# implementation spelling retained for readability in validation expressions.
MAX_REPLAY_EVENTS = HARD_MAX_EVENTS
MAX_REPLAY_RATE_PER_SECOND = HARD_MAX_RATE_PER_SECOND
MAX_REPLAY_DURATION_SECONDS = HARD_MAX_DURATION_SECONDS
MAX_REPLAY_PAYLOAD_BYTES = HARD_MAX_PAYLOAD_BYTES
MAX_REPLAY_TOTAL_PAYLOAD_BYTES = HARD_MAX_TOTAL_PAYLOAD_BYTES
MAX_REPLAY_RESOURCES = HARD_MAX_RESOURCES
MAX_REPLAY_CONCURRENCY = HARD_MAX_CONCURRENCY
MAX_REPLAY_SPEED = HARD_MAX_SPEED

_SAFE_SCENARIO = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$")
_REPLAY_ID = re.compile(r"^labreplay-[0-9a-f]{64}$")
_SOURCE_EVENT_ID = re.compile(r"^labevent-[0-9a-f]{64}$")
_IDEMPOTENCY_ID = re.compile(r"^labidempotency-[0-9a-f]{64}$")
_LOCK_ID = re.compile(r"^lablock-[0-9a-f]{64}$")
_BUNDLE_ID = re.compile(r"^labbundle-[0-9a-f]{64}$")
_OBSERVATION_ID = re.compile(r"^labobs-[0-9a-f]{64}$")

_CLOUD_ENVIRONMENT_KEYS = frozenset(
    {
        "CLOUD_PROFILE",
        "GCLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_QUOTA_PROJECT",
        "GOOGLE_PROJECT",
        "GOOGLE_REGION",
        "GOOGLE_SPANNER_DATABASE",
        "GOOGLE_SPANNER_INSTANCE",
        "GOOGLE_ZONE",
        "GCP_PROJECT",
        "K_CONFIGURATION",
        "K_REVISION",
        "K_SERVICE",
        "KUBERNETES_PORT",
        "KUBERNETES_SERVICE_HOST",
        "NETWORK_AGENT_FILE",
        "PUBSUB_EMULATOR_HOST",
        "SPANNER_DATABASE_ID",
        "SPANNER_EMULATOR_HOST",
        "SPANNER_INSTANCE_ID",
    }
)
_CLOUD_ENVIRONMENT_PREFIXES = (
    "CLOUD_RUN_",
    "CLOUDSDK_",
    "GCP_",
    "GOOGLE_",
    "PUBSUB_",
    "SPANNER_",
    "TELCO_CLOUD_",
    "TELCO_SPANNER_",
)
_PROFILE_KEYS = frozenset(
    {"NETWORKAGENT_PROFILE", "RUNTIME_PROFILE", "TELCO_RUNTIME_PROFILE"}
)
_AGENT_LOCATION_MARKERS = frozenset(
    {
        "ADDRESS",
        "A2A",
        "ENDPOINT",
        "FILE",
        "HOST",
        "PORT",
        "SERVICE",
        "SOCKET",
        "TARGET",
        "URI",
        "URL",
    }
)
_SAFE_EXPLICIT_ENVIRONMENT_KEYS = _PROFILE_KEYS | frozenset({"ACTION_MODE"})
_PRODUCTION_PROFILE_KEYS = frozenset(
    {"APP_ENV", "DEPLOYMENT_ENV", "ENV", "ENVIRONMENT", "NODE_ENV", "STAGE"}
)
_PRODUCTION_PROFILE_VALUES = frozenset(
    {"cloud", "gcp", "prod", "production", "staging"}
)
_CONTROL_PLANE_KEY_MARKERS = (
    "ENGINEER",
    "GITOPS",
    "OPERATOR",
    "RESOLVER",
)
_MAX_ENVIRONMENT_KEYS = 512
_MAX_ENVIRONMENT_KEY_LENGTH = 256
_MAX_ENVIRONMENT_VALUE_LENGTH = 4_096

# Replay is an explicit publication boundary, so an adapter is denied until
# its scalar projection is reviewed here.  This prevents a syntactically valid
# metric name (including a label-like source column) from becoming replay data.
_APPROVED_ADAPTER_PROJECTIONS: Mapping[tuple[str, str], Mapping[str, str]] = {
    (BUBBLERAN_CSV_ADAPTER_ID, ADAPTER_VERSION): {
        column.metric_name: column.unit
        for column in BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP.values()
    }
}

# These are the only non-payload annotations emitted by the approved adapter.
# Replay never accepts an arbitrary ``SafeName`` here because a source label is
# also syntactically a safe name and must not cross the publication boundary.
_APPROVED_ADAPTER_QUALITY_FLAGS: Mapping[tuple[str, str], frozenset[str]] = {
    (BUBBLERAN_CSV_ADAPTER_ID, ADAPTER_VERSION): frozenset({"MISSING_METRIC_VALUES"})
}

# An approved adapter is bound to its audited dataset identity and license.
# The callable always reads the verified artifact path again; a caller-supplied
# bundle is comparison material, never the replay source of truth.
_APPROVED_BUNDLE_ADAPTERS = {
    (BUBBLERAN_CSV_ADAPTER_ID, ADAPTER_VERSION): (
        BUBBLERAN_DATASET_ID,
        BUBBLERAN_DATASET_VERSION,
        BUBBLERAN_SOURCE_LICENSE,
        adapt_bubbleran_persistent_interference_csv,
    )
}

_ERROR_MESSAGES = {
    "replay_arguments_invalid": "replay arguments are invalid",
    "replay_artifact_unverified": "replay artifacts are not fully verified",
    "replay_bundle_invalid": "replay bundle is invalid",
    "replay_bundle_unbound": "replay bundle is not bound to the verified lock",
    "replay_duration_limit": "replay duration exceeds its fixed limit",
    "replay_environment_unsafe": "replay environment is not local-only",
    "replay_event_limit": "replay event count exceeds its fixed limit",
    "replay_payload_limit": "replay payload exceeds its fixed limit",
    "replay_plan_invalid": "replay plan integrity validation failed",
    "replay_resource_limit": "replay resource count exceeds its fixed limit",
    "replay_sequence_invalid": "replay delivery sequence is invalid",
    "replay_wire_invalid": "replay wire payload validation failed",
}


class ReplayError(ValueError):
    """A fixed-code error that never reflects configuration or dataset values."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            code = "replay_arguments_invalid"
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


class _FrozenMapping(Mapping[str, Any]):
    """Small, deterministic mapping with no mutable ``dict`` base to bypass.

    A ``dict`` subclass can always be changed with ``dict.__setitem__`` even
    when it overrides ``__setitem__``.  Storing a sorted tuple is both deeply
    immutable for the scalar replay values and stable across Pydantic versions.
    """

    __slots__ = ("_items",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_items", tuple(sorted(values.items())))

    def __getitem__(self, key: str) -> Any:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("replay mappings are immutable")

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("replay mappings are immutable")

    __delitem__ = _immutable
    __ior__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include a timezone")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]
Sha256Digest = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]


def _digest(domain: str, value: object) -> str:
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    digest.update(b"\0")
    digest.update(canonical_json_bytes(value))
    return digest.hexdigest()


def _safe_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("a finite number is required")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError("a finite number is required")
    return normalized


def _validate_loopback_endpoint(value: str) -> str:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or not value
        or len(value) > 2_048
    ):
        raise ValueError("endpoint is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("endpoint is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.startswith("//")
    ):
        raise ValueError("endpoint must be an explicit loopback URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost":
        return value
    if "%" in hostname:
        raise ValueError("endpoint must be an explicit loopback URL")
    try:
        address = ip_address(hostname)
    except ValueError:
        raise ValueError("endpoint must be an explicit loopback URL") from None
    if not address.is_loopback:
        raise ValueError("endpoint must be an explicit loopback URL")
    return value


class _ReplayModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
        revalidate_instances="always",
        str_strip_whitespace=True,
        validate_default=True,
    )


class ReplayPolicy(_ReplayModel):
    """Hard local-only execution budgets for one replay plan."""

    schema_version: Literal["1.0"] = REPLAY_SCHEMA_VERSION
    endpoint: str
    action_mode: Literal["disabled", "simulate"]
    speed: float = Field(default=1.0, gt=0, le=HARD_MAX_SPEED)
    max_events: int = Field(default=10_000, ge=1, le=HARD_MAX_EVENTS)
    max_rate_per_second: float = Field(
        default=50.0,
        gt=0,
        le=HARD_MAX_RATE_PER_SECOND,
    )
    max_duration_seconds: float = Field(
        default=3_600.0,
        gt=0,
        le=HARD_MAX_DURATION_SECONDS,
    )
    max_payload_bytes: int = Field(
        default=64 * 1024,
        ge=1,
        le=HARD_MAX_PAYLOAD_BYTES,
    )
    max_total_payload_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1,
        le=HARD_MAX_TOTAL_PAYLOAD_BYTES,
    )
    max_resources: int = Field(default=1_000, ge=1, le=HARD_MAX_RESOURCES)
    max_concurrency: int = Field(default=1, ge=1, le=HARD_MAX_CONCURRENCY)

    @field_validator("endpoint")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        return _validate_loopback_endpoint(value)

    @field_validator(
        "speed",
        "max_rate_per_second",
        "max_duration_seconds",
        mode="before",
    )
    @classmethod
    def _finite_numbers(cls, value: object) -> float:
        return _safe_float(value)

    @model_validator(mode="after")
    def _privacy_safe(self) -> Self:
        assert_model_safe(self.model_dump(mode="python"))
        return self


def _event_payload_identity(values: Mapping[str, object]) -> dict[str, object]:
    """Return only the allowlisted scalar projection sent to a sink adapter."""

    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "dataset_id": values["dataset_id"],
        "dataset_version": values["dataset_version"],
        "scenario": values["scenario"],
        "replay_observed_at": values["replay_observed_at"],
        "resource_id": values["resource_id"],
        "technology": values["technology"],
        "metrics": values["metrics"],
        "units": values["units"],
        "quality_flags": values["quality_flags"],
    }


def _source_event_id(values: Mapping[str, object], payload_sha256: str) -> str:
    identity = {
        "dataset_id": values["dataset_id"],
        "dataset_version": values["dataset_version"],
        "lock_id": values["lock_id"],
        "scenario": values["scenario"],
        "source_observation_id": values["source_observation_id"],
        "payload_sha256": payload_sha256,
    }
    return f"labevent-{_digest('telco-lab:replay-source-event:v1', identity)}"


def _is_approved_event_projection(
    metrics: Mapping[str, float],
    units: Mapping[str, str],
    quality_flags: Sequence[str],
) -> bool:
    """Return whether one event matches one complete audited adapter contract."""

    metric_names = set(metrics)
    flags = set(quality_flags)
    for key, projection in _APPROVED_ADAPTER_PROJECTIONS.items():
        approved_flags = _APPROVED_ADAPTER_QUALITY_FLAGS.get(key)
        if approved_flags is None:
            continue
        if (
            metric_names.issubset(projection)
            and all(units.get(name) == projection[name] for name in metric_names)
            and flags.issubset(approved_flags)
        ):
            return True
    return False


class ReplayWirePayload(_ReplayModel):
    """Strict, versioned JSON contract shared by replay sender and receiver."""

    schema_version: Literal["1.0"] = REPLAY_SCHEMA_VERSION
    source_event_id: str
    idempotency_key: str
    payload_sha256: Sha256Digest
    lock_id: str
    bundle_id: str
    source_observation_id: str
    dataset_id: SafeName
    dataset_version: SafeName
    scenario: SafeName
    replay_observed_at: UtcDatetime
    resource_id: str
    technology: Technology
    metrics: Mapping[MetricName, float] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )
    units: Mapping[MetricName, MetricUnit] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )
    quality_flags: tuple[SafeName, ...] = ()

    @field_validator("metrics", "units", mode="after")
    @classmethod
    def _freeze_scalar_mapping(cls, value: Mapping[str, object]) -> _FrozenMapping:
        return _FrozenMapping(value)

    @field_serializer("metrics", "units")
    def _serialize_scalar_mapping(
        self, value: Mapping[str, object]
    ) -> dict[str, object]:
        return {key: value[key] for key in sorted(value)}

    @model_validator(mode="after")
    def _validate_wire_identity(self) -> Self:
        if not _SOURCE_EVENT_ID.fullmatch(self.source_event_id):
            raise ValueError("source event identifier is invalid")
        if not _IDEMPOTENCY_ID.fullmatch(self.idempotency_key):
            raise ValueError("idempotency identifier is invalid")
        if not _LOCK_ID.fullmatch(self.lock_id):
            raise ValueError("workspace lock identifier is invalid")
        if not _BUNDLE_ID.fullmatch(self.bundle_id):
            raise ValueError("bundle identifier is invalid")
        if not _OBSERVATION_ID.fullmatch(self.source_observation_id):
            raise ValueError("observation identifier is invalid")
        if not _IDENTIFIER.fullmatch(self.resource_id):
            raise ValueError("resource identifier is invalid")
        if not _SAFE_SCENARIO.fullmatch(self.scenario):
            raise ValueError("scenario is invalid")
        if set(self.metrics) != set(self.units):
            raise ValueError("metric values and units must have identical names")
        if self.quality_flags != tuple(sorted(set(self.quality_flags))):
            raise ValueError("quality flags must be unique and sorted")
        if not _is_approved_event_projection(
            self.metrics,
            self.units,
            self.quality_flags,
        ):
            raise ValueError("replay event projection is not approved")

        values = self.model_dump(mode="python")
        expected_payload = _digest(
            "telco-lab:replay-payload:v1",
            _event_payload_identity(values),
        )
        expected_source_event = _source_event_id(values, expected_payload)
        expected_idempotency = "labidempotency-" + _digest(
            "telco-lab:replay-idempotency:v1",
            {"source_event_id": expected_source_event},
        )
        if (
            self.payload_sha256 != expected_payload
            or self.source_event_id != expected_source_event
            or self.idempotency_key != expected_idempotency
        ):
            raise ValueError("replay wire identity is inconsistent")
        try:
            assert_model_safe(values)
        except SensitiveDataError:
            raise ValueError("replay wire payload is not privacy safe") from None
        return self

    @classmethod
    def from_event(cls, event: ReplayEvent) -> ReplayWirePayload:
        """Revalidate and project exactly one immutable replay event."""

        if type(event) is not ReplayEvent:
            raise ReplayError("replay_wire_invalid")
        try:
            normalized = ReplayEvent.model_validate(event.model_dump(mode="python"))
            return cls.model_validate(
                {
                    "schema_version": normalized.schema_version,
                    "source_event_id": normalized.source_event_id,
                    "idempotency_key": normalized.idempotency_key,
                    "payload_sha256": normalized.payload_sha256,
                    "lock_id": normalized.lock_id,
                    "bundle_id": normalized.bundle_id,
                    "source_observation_id": normalized.source_observation_id,
                    "dataset_id": normalized.dataset_id,
                    "dataset_version": normalized.dataset_version,
                    "scenario": normalized.scenario,
                    "replay_observed_at": normalized.replay_observed_at,
                    "resource_id": normalized.resource_id,
                    "technology": normalized.technology,
                    "metrics": {
                        key: normalized.metrics[key]
                        for key in sorted(normalized.metrics)
                    },
                    "units": {
                        key: normalized.units[key] for key in sorted(normalized.units)
                    },
                    "quality_flags": normalized.quality_flags,
                }
            )
        except ReplayError:
            raise
        except (
            AttributeError,
            OverflowError,
            SensitiveDataError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            raise ReplayError("replay_wire_invalid") from None

    def to_sink_payload(self) -> dict[str, object]:
        """Return the legacy-compatible safe Mapping consumed by transports."""

        try:
            normalized = ReplayWirePayload.model_validate(
                self.model_dump(mode="python")
            )
            payload = {
                "schema_version": normalized.schema_version,
                "source_event_id": normalized.source_event_id,
                "idempotency_key": normalized.idempotency_key,
                "payload_sha256": normalized.payload_sha256,
                "lock_id": normalized.lock_id,
                "bundle_id": normalized.bundle_id,
                "source_observation_id": normalized.source_observation_id,
                "dataset_id": normalized.dataset_id,
                "dataset_version": normalized.dataset_version,
                "scenario": normalized.scenario,
                "replay_observed_at": normalized.replay_observed_at,
                "resource_id": normalized.resource_id,
                "technology": normalized.technology,
                "metrics": {
                    key: normalized.metrics[key] for key in sorted(normalized.metrics)
                },
                "units": {
                    key: normalized.units[key] for key in sorted(normalized.units)
                },
                "quality_flags": normalized.quality_flags,
            }
            assert_model_safe(payload)
            return payload
        except ReplayError:
            raise
        except (
            AttributeError,
            OverflowError,
            SensitiveDataError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            raise ReplayError("replay_wire_invalid") from None

    @property
    def request_fingerprint_sha256(self) -> str:
        """SHA-256 of the complete canonical wire body; not a wire field."""

        return hashlib.sha256(canonical_json_bytes(self.to_sink_payload())).hexdigest()


def replay_wire_payload_from_event(event: ReplayEvent) -> ReplayWirePayload:
    """Public sender boundary for constructing the strict wire model."""

    try:
        return ReplayWirePayload.from_event(event)
    except ReplayError:
        raise
    except Exception:
        raise ReplayError("replay_wire_invalid") from None


def _wire_json_shape_is_bounded(value: dict[object, object]) -> bool:
    string_limits = {
        "schema_version": 8,
        "source_event_id": 96,
        "idempotency_key": 96,
        "payload_sha256": 64,
        "lock_id": 96,
        "bundle_id": 96,
        "source_observation_id": 96,
        "dataset_id": 128,
        "dataset_version": 128,
        "scenario": 128,
        "replay_observed_at": 64,
        "resource_id": 256,
        "technology": 32,
    }
    if any(
        type(value.get(name)) is not str
        or not 1 <= len(value[name]) <= maximum  # type: ignore[arg-type]
        for name, maximum in string_limits.items()
    ):
        return False
    metrics = value.get("metrics")
    units = value.get("units")
    quality_flags = value.get("quality_flags")
    if (
        type(metrics) is not dict
        or type(units) is not dict
        or type(quality_flags) is not list
        or not 1 <= len(metrics) <= MAX_METRICS_PER_OBSERVATION
        or not 1 <= len(units) <= MAX_METRICS_PER_OBSERVATION
        or len(quality_flags) > MAX_QUALITY_FLAGS
    ):
        return False
    if any(
        type(name) is not str
        or not 1 <= len(name) <= 128
        or type(metric) not in {int, float}
        or not math.isfinite(float(metric))
        for name, metric in metrics.items()
    ):
        return False
    if any(
        type(name) is not str
        or not 1 <= len(name) <= 128
        or type(unit) is not str
        or not 1 <= len(unit) <= 32
        for name, unit in units.items()
    ):
        return False
    return all(type(flag) is str and 1 <= len(flag) <= 128 for flag in quality_flags)


def validate_replay_wire_payload(value: object) -> ReplayWirePayload:
    """Validate one already JSON-decoded object without parsing loose text."""

    if type(value) is not dict:
        raise ReplayError("replay_wire_invalid")
    try:
        expected_fields = set(ReplayWirePayload.model_fields)
        if len(value) != len(expected_fields) or set(value) != expected_fields:
            raise ReplayError("replay_wire_invalid")
        if not _wire_json_shape_is_bounded(value):
            raise ReplayError("replay_wire_invalid")
        replay_observed_at = value.get("replay_observed_at")
        if type(replay_observed_at) is not str or not replay_observed_at.endswith(
            ("Z", "+00:00")
        ):
            raise ReplayError("replay_wire_invalid")
        wire_bytes = canonical_json_bytes(value)
        if len(wire_bytes) > HARD_MAX_PAYLOAD_BYTES:
            raise ReplayError("replay_wire_invalid")
        return ReplayWirePayload.model_validate_json(
            wire_bytes,
            strict=True,
        )
    except ReplayError:
        raise
    except (
        AttributeError,
        OverflowError,
        SensitiveDataError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        raise ReplayError("replay_wire_invalid") from None


class ReplayEvent(_ReplayModel):
    """One label-free, replay-safe KPI event plus local-only provenance."""

    schema_version: Literal["1.0"] = REPLAY_SCHEMA_VERSION
    sequence_number: int = Field(ge=1, le=HARD_MAX_EVENTS)
    source_event_id: str
    idempotency_key: str
    dataset_id: SafeName
    dataset_version: SafeName
    scenario: SafeName
    lock_id: str
    bundle_id: str
    source_observation_id: str
    source_observed_at: UtcDatetime
    replay_observed_at: UtcDatetime
    scheduled_offset_seconds: float = Field(ge=0, le=HARD_MAX_DURATION_SECONDS)
    resource_id: str
    technology: Technology
    metrics: Mapping[MetricName, float] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )
    units: Mapping[MetricName, MetricUnit] = Field(
        min_length=1,
        max_length=MAX_METRICS_PER_OBSERVATION,
    )
    quality_flags: tuple[SafeName, ...] = ()
    payload_sha256: Sha256Digest

    @field_validator("scheduled_offset_seconds", mode="before")
    @classmethod
    def _finite_offset(cls, value: object) -> float:
        return _safe_float(value)

    @field_validator("metrics", "units", mode="after")
    @classmethod
    def _freeze_scalar_mapping(cls, value: Mapping[str, object]) -> _FrozenMapping:
        return _FrozenMapping(value)

    @field_serializer("metrics", "units")
    def _serialize_scalar_mapping(
        self, value: Mapping[str, object]
    ) -> dict[str, object]:
        # The immutable runtime type is deliberately private.  Both Python and
        # JSON dumps expose a fresh ordinary mapping with deterministic order.
        return {key: value[key] for key in sorted(value)}

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        if not _SOURCE_EVENT_ID.fullmatch(self.source_event_id):
            raise ValueError("source event identifier is invalid")
        if not _IDEMPOTENCY_ID.fullmatch(self.idempotency_key):
            raise ValueError("idempotency identifier is invalid")
        if not _LOCK_ID.fullmatch(self.lock_id):
            raise ValueError("workspace lock identifier is invalid")
        if not _BUNDLE_ID.fullmatch(self.bundle_id):
            raise ValueError("bundle identifier is invalid")
        if not _OBSERVATION_ID.fullmatch(self.source_observation_id):
            raise ValueError("observation identifier is invalid")
        if not _IDENTIFIER.fullmatch(self.resource_id):
            raise ValueError("resource identifier is invalid")
        if not _SAFE_SCENARIO.fullmatch(self.scenario):
            raise ValueError("scenario is invalid")
        if set(self.metrics) != set(self.units):
            raise ValueError("metric values and units must have identical names")
        if self.quality_flags != tuple(sorted(set(self.quality_flags))):
            raise ValueError("quality flags must be unique and sorted")
        if not _is_approved_event_projection(
            self.metrics,
            self.units,
            self.quality_flags,
        ):
            raise ValueError("replay event projection is not approved")

        values = self.model_dump(mode="python")
        expected_payload = _digest(
            "telco-lab:replay-payload:v1",
            _event_payload_identity(values),
        )
        if self.payload_sha256 != expected_payload:
            raise ValueError("replay payload checksum is inconsistent")
        expected_source_event = _source_event_id(values, expected_payload)
        if self.source_event_id != expected_source_event:
            raise ValueError("source event identifier is inconsistent")
        expected_idempotency = "labidempotency-" + _digest(
            "telco-lab:replay-idempotency:v1",
            {"source_event_id": self.source_event_id},
        )
        if self.idempotency_key != expected_idempotency:
            raise ValueError("idempotency identifier is inconsistent")
        try:
            assert_model_safe(values)
        except SensitiveDataError:
            raise ValueError("replay event is not privacy safe") from None
        return self

    def sink_payload(self) -> dict[str, object]:
        """Return the safe projection for a caller-owned transport adapter.

        ``source_observed_at`` and delivery scheduling are deliberately local
        provenance.  Ground truth cannot enter this projection because it is
        not represented by this model.
        """

        try:
            return replay_wire_payload_from_event(self).to_sink_payload()
        except ReplayError:
            raise ReplayError("replay_bundle_invalid") from None


def _plan_identity(values: Mapping[str, object]) -> dict[str, object]:
    events = values["events"]
    return {
        "policy": values["policy"],
        "dataset_id": values["dataset_id"],
        "dataset_version": values["dataset_version"],
        "scenario": values["scenario"],
        "lock_id": values["lock_id"],
        "bundle_id": values["bundle_id"],
        "bundle_content_sha256": values["bundle_content_sha256"],
        "source_artifact_sha256": values["source_artifact_sha256"],
        "source_window_start": values["source_window_start"],
        "source_window_end": values["source_window_end"],
        "replay_window_start": values["replay_window_start"],
        "replay_window_end": values["replay_window_end"],
        "events": [
            {
                "sequence_number": event.sequence_number,
                "source_event_id": event.source_event_id,
                "idempotency_key": event.idempotency_key,
                "scheduled_offset_seconds": event.scheduled_offset_seconds,
                "payload_sha256": event.payload_sha256,
            }
            for event in events  # type: ignore[union-attr]
        ],
    }


class ReplayPlan(_ReplayModel):
    """A deterministic, fully bounded delivery plan for one verified bundle."""

    schema_version: Literal["1.0"] = REPLAY_SCHEMA_VERSION
    plan_id: str
    policy: ReplayPolicy
    dataset_id: SafeName
    dataset_version: SafeName
    scenario: SafeName
    lock_id: str
    bundle_id: str
    bundle_content_sha256: Sha256Digest
    source_artifact_sha256: Sha256Digest
    source_window_start: UtcDatetime
    source_window_end: UtcDatetime
    replay_window_start: UtcDatetime
    replay_window_end: UtcDatetime
    events: tuple[ReplayEvent, ...] = Field(min_length=1, max_length=HARD_MAX_EVENTS)

    @model_validator(mode="after")
    def _validate_plan(self) -> Self:
        if not _REPLAY_ID.fullmatch(self.plan_id):
            raise ValueError("replay plan identifier is invalid")
        if not _LOCK_ID.fullmatch(self.lock_id):
            raise ValueError("workspace lock identifier is invalid")
        if not _BUNDLE_ID.fullmatch(self.bundle_id):
            raise ValueError("bundle identifier is invalid")
        if not _SAFE_SCENARIO.fullmatch(self.scenario):
            raise ValueError("scenario is invalid")
        if self.source_window_end < self.source_window_start:
            raise ValueError("source window is invalid")
        if self.replay_window_end < self.replay_window_start:
            raise ValueError("replay window is invalid")
        if len(self.events) > self.policy.max_events:
            raise ValueError("replay event count exceeds policy")
        if tuple(event.sequence_number for event in self.events) != tuple(
            range(1, len(self.events) + 1)
        ):
            raise ValueError("replay sequence must be contiguous")

        previous_schedule = -1.0
        resources: set[str] = set()
        total_payload_bytes = 0
        for event in self.events:
            if (
                event.dataset_id != self.dataset_id
                or event.dataset_version != self.dataset_version
                or event.scenario != self.scenario
                or event.lock_id != self.lock_id
                or event.bundle_id != self.bundle_id
            ):
                raise ValueError("event provenance does not match replay plan")
            source_offset = (
                event.source_observed_at - self.source_window_start
            ).total_seconds()
            replay_offset = (
                event.replay_observed_at - self.replay_window_start
            ).total_seconds()
            if source_offset < 0 or replay_offset != source_offset:
                raise ValueError("replay timestamps do not preserve relative time")
            expected_schedule = max(
                source_offset / self.policy.speed,
                (
                    0.0
                    if previous_schedule < 0
                    else previous_schedule + 1.0 / self.policy.max_rate_per_second
                ),
            )
            if not math.isclose(
                event.scheduled_offset_seconds,
                expected_schedule,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError("event delivery schedule is inconsistent")
            previous_schedule = event.scheduled_offset_seconds
            resources.add(event.resource_id)
            payload_bytes = len(canonical_json_bytes(event.sink_payload()))
            if payload_bytes > self.policy.max_payload_bytes:
                raise ValueError("event payload exceeds policy")
            total_payload_bytes += payload_bytes

        if self.events[0].source_observed_at != self.source_window_start:
            raise ValueError("source window start does not match events")
        if self.events[-1].source_observed_at != self.source_window_end:
            raise ValueError("source window end does not match events")
        if self.events[0].replay_observed_at != self.replay_window_start:
            raise ValueError("replay window start does not match events")
        if self.events[-1].replay_observed_at != self.replay_window_end:
            raise ValueError("replay window end does not match events")
        logical_duration = (
            self.replay_window_end - self.replay_window_start
        ).total_seconds()
        if (
            logical_duration > self.policy.max_duration_seconds
            or self.events[-1].scheduled_offset_seconds
            > self.policy.max_duration_seconds
        ):
            raise ValueError("replay duration exceeds policy")
        if len(resources) > self.policy.max_resources:
            raise ValueError("replay resources exceed policy")
        if total_payload_bytes > self.policy.max_total_payload_bytes:
            raise ValueError("replay payload total exceeds policy")

        values = self.model_dump(mode="python")
        values["events"] = self.events
        expected_plan_id = "labreplay-" + _digest(
            "telco-lab:replay-plan:v1", _plan_identity(values)
        )
        if self.plan_id != expected_plan_id:
            raise ValueError("replay plan identifier is inconsistent")
        try:
            assert_model_safe(values)
        except SensitiveDataError:
            raise ValueError("replay plan is not privacy safe") from None
        return self

    def _validated_for_public_use(self) -> "ReplayPlan":
        """Return a fully revalidated copy or reject post-validation tampering."""

        # ``model_copy(update=...)`` deliberately skips validation.  Bound the
        # structure before serialization so a forged container cannot turn the
        # integrity check itself into an unbounded iteration.
        try:
            structure_is_bounded = (
                type(self) is ReplayPlan
                and type(self.policy) is ReplayPolicy
                and type(self.events) is tuple
                and 1 <= len(self.events) <= HARD_MAX_EVENTS
                and all(
                    type(event) is ReplayEvent
                    and type(event.metrics) is _FrozenMapping
                    and type(event.metrics._items) is tuple
                    and 1 <= len(event.metrics._items) <= MAX_METRICS_PER_OBSERVATION
                    and type(event.units) is _FrozenMapping
                    and type(event.units._items) is tuple
                    and 1 <= len(event.units._items) <= MAX_METRICS_PER_OBSERVATION
                    and type(event.quality_flags) is tuple
                    and len(event.quality_flags) <= MAX_QUALITY_FLAGS
                    for event in self.events
                )
            )
        except (AttributeError, MemoryError, TypeError, ValueError):
            raise ReplayError("replay_plan_invalid") from None
        if not structure_is_bounded:
            raise ReplayError("replay_plan_invalid")
        try:
            payload = self.model_dump(mode="python")
            return ReplayPlan.model_validate(payload)
        except ReplayError:
            raise ReplayError("replay_plan_invalid") from None
        except (
            AttributeError,
            MemoryError,
            OverflowError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            raise ReplayError("replay_plan_invalid") from None

    def resume_after(self, sequence_number: int) -> tuple[ReplayEvent, ...]:
        """Return the deterministic suffix following a caller-supplied sequence."""

        validated = self._validated_for_public_use()
        if (
            isinstance(sequence_number, bool)
            or not isinstance(sequence_number, int)
            or not 0 <= sequence_number <= len(validated.events)
        ):
            raise ReplayError("replay_sequence_invalid")
        return validated.events[sequence_number:]

    def delivery_order(
        self, sequence_numbers: Sequence[int]
    ) -> tuple[ReplayEvent, ...]:
        """Select a bounded order for duplicate and out-of-order fault tests."""

        validated = self._validated_for_public_use()
        # Only the two concrete, bounded built-ins are accepted.  An arbitrary
        # ``Sequence`` can lie in ``__len__`` and expose an unbounded iterator,
        # so calling ``tuple(sequence_numbers)`` would be a memory/availability
        # bypass even after an apparently safe length check.
        if type(sequence_numbers) not in {list, tuple}:
            raise ReplayError("replay_sequence_invalid")
        try:
            requested = tuple(sequence_numbers[: validated.policy.max_events + 1])
            if not 1 <= len(requested) <= validated.policy.max_events:
                raise ReplayError("replay_sequence_invalid")
        except (TypeError, MemoryError):
            raise ReplayError("replay_sequence_invalid") from None
        if any(
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 1 <= item <= len(validated.events)
            for item in requested
        ):
            raise ReplayError("replay_sequence_invalid")
        selected = tuple(validated.events[item - 1] for item in requested)
        total_payload_bytes = 0
        for event in selected:
            try:
                total_payload_bytes += len(canonical_json_bytes(event.sink_payload()))
            except ReplayError:
                raise
            except Exception:
                raise ReplayError("replay_payload_limit") from None
            if total_payload_bytes > validated.policy.max_total_payload_bytes:
                raise ReplayError("replay_payload_limit")
        return selected


@runtime_checkable
class ReplaySink(Protocol):
    """Transport boundary implemented by the built-in or a caller-owned sink."""

    @property
    def endpoint(self) -> str: ...

    async def emit(self, event: ReplayEvent) -> object: ...


def validate_replay_environment(
    policy: ReplayPolicy,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Reject cloud, credential, or real Engineer configuration without echoing it."""

    try:
        normalized_policy = ReplayPolicy.model_validate(
            policy.model_dump(mode="python")
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise ReplayError("replay_environment_unsafe") from None
    values = os.environ if environ is None else environ
    explicit_environment = environ is not None
    try:
        normalized: dict[str, str] = {}
        if len(values) > _MAX_ENVIRONMENT_KEYS:
            raise ReplayError("replay_environment_unsafe")
        for index, (key, value) in enumerate(values.items(), start=1):
            if index > _MAX_ENVIRONMENT_KEYS:
                raise ReplayError("replay_environment_unsafe")
            if not isinstance(key, str) or not isinstance(value, str):
                raise ReplayError("replay_environment_unsafe")
            clean_key = key.strip().upper()
            clean_value = value.strip()
            if (
                len(clean_key) > _MAX_ENVIRONMENT_KEY_LENGTH
                or len(clean_value) > _MAX_ENVIRONMENT_VALUE_LENGTH
                or (clean_key in normalized and normalized[clean_key] != clean_value)
            ):
                raise ReplayError("replay_environment_unsafe")
            if clean_key and clean_value:
                normalized[clean_key] = clean_value
    except ReplayError:
        raise
    except Exception:
        raise ReplayError("replay_environment_unsafe") from None

    if any(
        key in normalized and normalized[key].lower() != "local"
        for key in _PROFILE_KEYS
    ):
        raise ReplayError("replay_environment_unsafe")
    action = normalized.get("ACTION_MODE")
    if action is not None and (
        action.lower() not in {"disabled", "simulate"}
        or action.lower() != normalized_policy.action_mode
    ):
        raise ReplayError("replay_environment_unsafe")

    for key in normalized:
        if key in _CLOUD_ENVIRONMENT_KEYS or key.startswith(
            _CLOUD_ENVIRONMENT_PREFIXES
        ):
            raise ReplayError("replay_environment_unsafe")
        if key == "KUBECONFIG" or any(
            marker in key for marker in _CONTROL_PLANE_KEY_MARKERS
        ):
            raise ReplayError("replay_environment_unsafe")
        key_tokens = frozenset(key.split("_"))
        if "AGENT" in key and key_tokens.intersection(_AGENT_LOCATION_MARKERS):
            raise ReplayError("replay_environment_unsafe")
        if (
            key in _PRODUCTION_PROFILE_KEYS
            and normalized[key].lower() in _PRODUCTION_PROFILE_VALUES
        ):
            raise ReplayError("replay_environment_unsafe")
        # A caller-supplied mapping is a security configuration, so it has a
        # deliberately tiny allowlist.  The process environment also contains
        # unrelated OS keys such as PATH; those are ignored only after all
        # dangerous control-plane patterns above have been checked.
        if explicit_environment and key not in _SAFE_EXPLICIT_ENVIRONMENT_KEYS:
            raise ReplayError("replay_environment_unsafe")


def _verified_lock(lab: TelcoLab) -> WorkspaceLock:
    if type(lab) is not TelcoLab:
        raise ReplayError("replay_artifact_unverified")
    try:
        report = TelcoLab.verify(lab)
    except Exception:
        raise ReplayError("replay_artifact_unverified") from None
    if (
        report.schema_version != "1.0"
        or not report.valid
        or not report.artifacts
        or any(item.status != "VERIFIED" for item in report.artifacts)
    ):
        raise ReplayError("replay_artifact_unverified")
    try:
        candidate = TelcoLab.verified_manifest(lab)
        lock = WorkspaceLock.model_validate(candidate.model_dump(mode="python"))
    except Exception:
        raise ReplayError("replay_artifact_unverified") from None
    verified_projection = tuple(
        sorted(
            (item.resource_id, item.filename, item.status) for item in report.artifacts
        )
    )
    lock_projection = tuple(
        sorted((item.resource_id, item.filename, "VERIFIED") for item in lock.artifacts)
    )
    if (
        report.catalog_id != lock.catalog_id
        or report.catalog_version != lock.catalog_version
        or verified_projection != lock_projection
    ):
        raise ReplayError("replay_artifact_unverified")
    return lock


def _canonical_bundle_from_verified_artifact(
    lab: TelcoLab,
    lock: WorkspaceLock,
    supplied: LabBundle,
) -> LabBundle:
    """Rebuild and compare the only bundle permitted to become replay data."""

    manifest = supplied.manifest
    adapter_key = (manifest.adapter_id, manifest.adapter_version)
    adapter_contract = _APPROVED_BUNDLE_ADAPTERS.get(adapter_key)
    if adapter_contract is None:
        raise ReplayError("replay_bundle_unbound")
    dataset_id, dataset_version, source_license, adapter = adapter_contract
    if (
        manifest.dataset_id != dataset_id
        or manifest.dataset_version != dataset_version
        or manifest.source_license != source_license
    ):
        raise ReplayError("replay_bundle_unbound")

    matching = tuple(
        artifact
        for artifact in lock.artifacts
        if artifact.sha256 == manifest.source_artifact_sha256
        and artifact.dataset_id == dataset_id
        and artifact.dataset_version == dataset_version
        and artifact.adapter == manifest.adapter_id
        and artifact.license_id == source_license
    )
    if len(matching) != 1:
        raise ReplayError("replay_bundle_unbound")
    artifact = matching[0]

    try:
        # The public path method performs a fresh digest verification.  Calling
        # the concrete implementation and requiring the concrete facade keeps
        # duck-typed or overridden verification shims outside this boundary.
        artifact_path = TelcoLab.artifact_path(lab, artifact.resource_id)
    except Exception:
        raise ReplayError("replay_artifact_unverified") from None
    try:
        rebuilt = LabBundle.model_validate(
            adapter(artifact_path).model_dump(mode="python")
        )
    except Exception:
        raise ReplayError("replay_bundle_invalid") from None

    # This comparison includes observations, separately held ground truth,
    # quality flags, manifest counters, hashes and IDs.  Lock metadata alone is
    # deliberately insufficient to authorize a caller-created bundle.
    if canonical_json_bytes(rebuilt) != canonical_json_bytes(supplied):
        raise ReplayError("replay_bundle_unbound")
    return rebuilt


def build_replay_plan(
    lab: TelcoLab,
    bundle: LabBundle,
    *,
    scenario: str,
    replay_window_start: datetime,
    policy: ReplayPolicy,
    environ: Mapping[str, str] | None = None,
) -> ReplayPlan:
    """Build a deterministic plan from one lock-bound, fully verified bundle."""

    validate_replay_environment(policy, environ)
    if not isinstance(scenario, str) or not _SAFE_SCENARIO.fullmatch(scenario):
        raise ReplayError("replay_arguments_invalid")
    try:
        start = _utc(replay_window_start)
        normalized_policy = ReplayPolicy.model_validate(
            policy.model_dump(mode="python")
        )
    except (AttributeError, OverflowError, TypeError, ValueError, ValidationError):
        raise ReplayError("replay_arguments_invalid") from None
    try:
        normalized_bundle = LabBundle.model_validate(bundle.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise ReplayError("replay_bundle_invalid") from None

    lock = _verified_lock(lab)
    manifest = normalized_bundle.manifest
    approved_projection = _APPROVED_ADAPTER_PROJECTIONS.get(
        (manifest.adapter_id, manifest.adapter_version)
    )
    approved_flags = _APPROVED_ADAPTER_QUALITY_FLAGS.get(
        (manifest.adapter_id, manifest.adapter_version)
    )
    if approved_projection is None or approved_flags is None:
        raise ReplayError("replay_bundle_unbound")
    if any(
        not set(observation.metrics).issubset(approved_projection)
        or any(
            observation.units[name] != approved_projection[name]
            for name in observation.metrics
        )
        or not set(observation.quality_flags).issubset(approved_flags)
        for observation in normalized_bundle.observations
    ):
        raise ReplayError("replay_bundle_invalid")

    normalized_bundle = _canonical_bundle_from_verified_artifact(
        lab,
        lock,
        normalized_bundle,
    )
    manifest = normalized_bundle.manifest

    if len(normalized_bundle.observations) > normalized_policy.max_events:
        raise ReplayError("replay_event_limit")
    observations = tuple(
        sorted(
            normalized_bundle.observations,
            key=lambda item: (
                item.observed_at,
                item.resource_id,
                item.observation_id,
            ),
        )
    )
    resources = {item.resource_id for item in observations}
    if len(resources) > normalized_policy.max_resources:
        raise ReplayError("replay_resource_limit")
    source_start = manifest.window_start
    source_end = manifest.window_end
    logical_duration = (source_end - source_start).total_seconds()
    if logical_duration > normalized_policy.max_duration_seconds:
        raise ReplayError("replay_duration_limit")
    try:
        replay_end = start + (source_end - source_start)
    except (OverflowError, ValueError):
        raise ReplayError("replay_arguments_invalid") from None

    events: list[ReplayEvent] = []
    total_payload_bytes = 0
    previous_schedule = -1.0
    for sequence_number, observation in enumerate(observations, start=1):
        source_offset = (observation.observed_at - source_start).total_seconds()
        try:
            replay_at = start + (observation.observed_at - source_start)
        except (OverflowError, ValueError):
            raise ReplayError("replay_arguments_invalid") from None
        scheduled_offset = max(
            source_offset / normalized_policy.speed,
            (
                0.0
                if previous_schedule < 0
                else previous_schedule + 1.0 / normalized_policy.max_rate_per_second
            ),
        )
        if scheduled_offset > normalized_policy.max_duration_seconds:
            raise ReplayError("replay_duration_limit")
        values: dict[str, object] = {
            "dataset_id": manifest.dataset_id,
            "dataset_version": manifest.dataset_version,
            "scenario": scenario,
            "lock_id": lock.lock_id,
            "bundle_id": manifest.bundle_id,
            "source_observation_id": observation.observation_id,
            "source_observed_at": observation.observed_at,
            "replay_observed_at": replay_at,
            "resource_id": observation.resource_id,
            "technology": observation.technology,
            "metrics": dict(observation.metrics),
            "units": dict(observation.units),
            "quality_flags": tuple(observation.quality_flags),
        }
        payload_sha256 = _digest(
            "telco-lab:replay-payload:v1",
            _event_payload_identity(values),
        )
        source_event_id = _source_event_id(values, payload_sha256)
        idempotency_key = "labidempotency-" + _digest(
            "telco-lab:replay-idempotency:v1",
            {"source_event_id": source_event_id},
        )
        try:
            event = ReplayEvent(
                sequence_number=sequence_number,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key,
                scheduled_offset_seconds=scheduled_offset,
                payload_sha256=payload_sha256,
                **values,
            )
        except (TypeError, ValueError, ValidationError):
            raise ReplayError("replay_bundle_invalid") from None
        payload_bytes = len(canonical_json_bytes(event.sink_payload()))
        if payload_bytes > normalized_policy.max_payload_bytes:
            raise ReplayError("replay_payload_limit")
        total_payload_bytes += payload_bytes
        if total_payload_bytes > normalized_policy.max_total_payload_bytes:
            raise ReplayError("replay_payload_limit")
        events.append(event)
        previous_schedule = scheduled_offset

    plan_values: dict[str, object] = {
        "policy": normalized_policy,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "scenario": scenario,
        "lock_id": lock.lock_id,
        "bundle_id": manifest.bundle_id,
        "bundle_content_sha256": manifest.content_sha256,
        "source_artifact_sha256": manifest.source_artifact_sha256,
        "source_window_start": source_start,
        "source_window_end": source_end,
        "replay_window_start": start,
        "replay_window_end": replay_end,
        "events": tuple(events),
    }
    plan_id = "labreplay-" + _digest(
        "telco-lab:replay-plan:v1", _plan_identity(plan_values)
    )
    try:
        return ReplayPlan(plan_id=plan_id, **plan_values)
    except (TypeError, ValueError, ValidationError):
        raise ReplayError("replay_arguments_invalid") from None


__all__ = [
    "HARD_MAX_CONCURRENCY",
    "HARD_MAX_DURATION_SECONDS",
    "HARD_MAX_EVENTS",
    "HARD_MAX_PAYLOAD_BYTES",
    "HARD_MAX_RATE_PER_SECOND",
    "HARD_MAX_RESOURCES",
    "HARD_MAX_SPEED",
    "HARD_MAX_TOTAL_PAYLOAD_BYTES",
    "MAX_REPLAY_CONCURRENCY",
    "MAX_REPLAY_DURATION_SECONDS",
    "MAX_REPLAY_EVENTS",
    "MAX_REPLAY_PAYLOAD_BYTES",
    "MAX_REPLAY_RATE_PER_SECOND",
    "MAX_REPLAY_RESOURCES",
    "MAX_REPLAY_SPEED",
    "MAX_REPLAY_TOTAL_PAYLOAD_BYTES",
    "REPLAY_SCHEMA_VERSION",
    "ReplayError",
    "ReplayEvent",
    "ReplayPlan",
    "ReplayPolicy",
    "ReplaySink",
    "ReplayWirePayload",
    "build_replay_plan",
    "replay_wire_payload_from_event",
    "validate_replay_environment",
    "validate_replay_wire_payload",
]
