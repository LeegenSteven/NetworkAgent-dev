"""Privacy guards for payloads sent to models or written to audit logs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence, Set
from typing import Any


REDACTED = "[REDACTED]"

_SENSITIVE_KEY_NAMES = frozenset(
    {
        "imsi",
        "msisdn",
        "imei",
        "imeisv",
        "supi",
        "suci",
        "subscriberid",
        "subscriberidentity",
        "subscriberpermanentidentifier",
        "subscriptionconcealedidentifier",
    }
)

_LABELED_IDENTIFIER = re.compile(
    r"(?i)\b(?P<label>imsi|msisdn|imei|imeisv|supi|suci)\b"
    r"(?P<separator>\s*[-:=]\s*|\s+)"
    r"(?P<value>(?:imsi-)?[+a-z0-9_-]{6,})"
)


class SensitiveDataError(ValueError):
    """Raised when a model-bound payload contains a raw subscriber identifier."""


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: object) -> bool:
    return _normalized_key(key) in _SENSITIVE_KEY_NAMES


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _plain_value(value: Any) -> Any:
    """Convert Pydantic-like objects without importing a framework type."""

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python")
    return value


def find_sensitive_paths(value: Any, *, path: str = "$") -> tuple[str, ...]:
    """Return paths containing raw subscriber identifiers without exposing values."""

    value = _plain_value(value)
    matches: list[str] = []

    if isinstance(value, Mapping):
        for key, nested in value.items():
            # Mapping keys are payload content too. Never echo a matching key
            # in the error path, because the key itself contains the value we
            # are preventing from crossing the boundary.
            if isinstance(key, str) and _LABELED_IDENTIFIER.search(key):
                matches.append(f"{path}.<sensitive-key>")
                continue
            nested_path = f"{path}.{key}"
            if _is_sensitive_key(key) and not _is_empty(nested):
                matches.append(nested_path)
                continue
            matches.extend(find_sensitive_paths(nested, path=nested_path))
        return tuple(matches)

    if isinstance(value, (Sequence, Set)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, nested in enumerate(value):
            matches.extend(find_sensitive_paths(nested, path=f"{path}[{index}]"))
        return tuple(matches)

    if isinstance(value, str) and _LABELED_IDENTIFIER.search(value):
        matches.append(path)

    return tuple(matches)


def assert_model_safe(value: Any) -> None:
    """Reject payloads that would disclose a subscriber identifier to a model."""

    paths = find_sensitive_paths(value)
    if paths:
        joined_paths = ", ".join(paths)
        raise SensitiveDataError(
            f"Raw subscriber identifiers are not allowed at: {joined_paths}"
        )


def redact_sensitive_data(value: Any) -> Any:
    """Return a recursively redacted copy suitable for model and log boundaries."""

    value = _plain_value(value)

    if isinstance(value, Mapping):
        return {
            REDACTED if isinstance(key, str) and _LABELED_IDENTIFIER.search(key)
            else key: (
                REDACTED
                if _is_sensitive_key(key) and not _is_empty(nested)
                else redact_sensitive_data(nested)
            )
            for key, nested in value.items()
        }

    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)

    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]

    if isinstance(value, Set):
        return {redact_sensitive_data(item) for item in value}

    if isinstance(value, str):
        return _LABELED_IDENTIFIER.sub(
            lambda match: (
                f"{match.group('label')}{match.group('separator')}{REDACTED}"
            ),
            value,
        )

    return value
