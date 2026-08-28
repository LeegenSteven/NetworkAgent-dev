"""Shared validation and serialization helpers for Cloud Profile adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable

from telco_domain.contracts import MAX_CONTRACT_DEPTH, MAX_CONTRACT_SERIALIZED_BYTES
from telco_domain.ports import UnsafeIncidentWriteError
from telco_domain.privacy import SensitiveDataError, assert_model_safe


Clock = Callable[[], datetime]


def utc_now(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("repository clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def require_non_empty(name: str, value: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters")
    return normalized


def json_safe(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", round_trip=True)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_safe(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_safe(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        json_safe(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def fingerprint(operation: str, payload: Mapping[str, Any]) -> str:
    encoded = canonical_json({"operation": operation, "payload": payload}).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def payload_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        if depth > MAX_CONTRACT_DEPTH:
            return depth
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((nested, depth + 1) for nested in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((nested, depth + 1) for nested in current)
    return maximum


def assert_safe(value: object, *, boundary: str) -> None:
    try:
        plain = json_safe(value)
        if payload_depth(plain) > MAX_CONTRACT_DEPTH:
            raise ValueError("payload depth exceeded")
        assert_model_safe(plain)
        encoded = canonical_json(plain).encode("utf-8")
    except (SensitiveDataError, TypeError, ValueError, RecursionError):
        raise UnsafeIncidentWriteError(boundary, "privacy or JSON policy violation") from None
    if len(encoded) > MAX_CONTRACT_SERIALIZED_BYTES:
        raise UnsafeIncidentWriteError(
            boundary,
            f"serialized payload exceeds {MAX_CONTRACT_SERIALIZED_BYTES} bytes",
        )


def require_write_metadata(
    *, idempotency_key: str, actor: str, reason: str, trace_id: str
) -> None:
    require_non_empty("idempotency_key", idempotency_key, max_length=256)
    require_non_empty("actor", actor, max_length=256)
    require_non_empty("reason", reason, max_length=4_096)
    require_non_empty("trace_id", trace_id, max_length=256)
    assert_safe(
        {
            "idempotency_key": idempotency_key,
            "actor": actor,
            "reason": reason,
            "trace_id": trace_id,
        },
        boundary="write-metadata",
    )


def parse_json_model(model_type: Any, payload: object):
    if isinstance(payload, str):
        return model_type.model_validate_json(payload)
    if isinstance(payload, Mapping):
        return model_type.model_validate(dict(payload))
    return model_type.model_validate(payload)


__all__ = [
    "Clock",
    "assert_safe",
    "canonical_json",
    "fingerprint",
    "json_safe",
    "parse_json_model",
    "require_non_empty",
    "require_write_metadata",
    "utc_now",
]
