"""Budgeted JSON decoding for security-sensitive catalogs and lock files."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


class StrictJsonError(ValueError):
    """Raised without embedding source text or parsed values."""

    def __init__(self) -> None:
        super().__init__("JSON document is invalid")


def _decode_source(source: str | bytes, *, max_bytes: int) -> str:
    if max_bytes < 1:
        raise StrictJsonError()
    try:
        if isinstance(source, bytes):
            if len(source) > max_bytes:
                raise StrictJsonError()
            return source.decode("utf-8", errors="strict")
        encoded = source.encode("utf-8", errors="strict")
        if len(encoded) > max_bytes:
            raise StrictJsonError()
        return source
    except UnicodeError as exc:
        raise StrictJsonError() from exc


def _check_depth(source: str, *, max_depth: int) -> None:
    if max_depth < 1:
        raise StrictJsonError()
    depth = 0
    in_string = False
    escaped = False
    for character in source:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise StrictJsonError()
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise StrictJsonError()


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise StrictJsonError()


def _finite_float(value: str) -> float:
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise StrictJsonError() from exc
    if not math.isfinite(result):
        raise StrictJsonError()
    return result


def _bounded_int(value: str) -> int:
    digits = value.removeprefix("-")
    if len(digits) > 20:
        raise StrictJsonError()
    try:
        return int(value)
    except ValueError as exc:
        raise StrictJsonError() from exc


def _normalize_string(value: str) -> str:
    try:
        # JSON escapes may decode into UTF-16 surrogate code units. This combines a
        # valid pair and rejects every unpaired high or low surrogate.
        return value.encode("utf-16-le", "surrogatepass").decode(
            "utf-16-le", "strict"
        )
    except UnicodeError as exc:
        raise StrictJsonError() from exc


def _normalize_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, list):
        return [_normalize_strings(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _normalize_string(raw_key)
            if key in normalized:
                raise StrictJsonError()
            normalized[key] = _normalize_strings(raw_value)
        return normalized
    return value


def load_strict_json(
    source: str | bytes,
    *,
    max_bytes: int,
    max_depth: int = 32,
) -> Any:
    """Decode deterministic JSON while rejecting ambiguity and resource abuse."""

    text = _decode_source(source, max_bytes=max_bytes)
    _check_depth(text, max_depth=max_depth)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            parse_int=_bounded_int,
        )
        return _normalize_strings(parsed)
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError, OverflowError, RecursionError) as exc:
        raise StrictJsonError() from exc


__all__ = ["StrictJsonError", "load_strict_json"]
