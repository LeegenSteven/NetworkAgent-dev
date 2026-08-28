"""Small lazy Spanner compatibility helpers used by injected adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class _FallbackKeySet:
    keys: tuple[tuple[object, ...], ...] = ()
    all_: bool = False


_FALLBACK_COMMIT_TIMESTAMP = "spanner.commit_timestamp()"


def commit_timestamp() -> Any:
    """Return Spanner's server-side commit timestamp placeholder lazily."""

    try:
        from google.cloud.spanner_v1 import COMMIT_TIMESTAMP
    except ImportError:
        return _FALLBACK_COMMIT_TIMESTAMP
    return COMMIT_TIMESTAMP


def keyset(*keys: tuple[object, ...], all_: bool = False) -> Any:
    """Create a real KeySet when the optional runtime is installed.

    Unit fakes can import the package without importing any Google SDK. The
    package dependency ensures production paths always obtain the real class.
    """

    try:
        from google.cloud.spanner_v1 import KeySet
    except ImportError:
        return _FallbackKeySet(keys=tuple(keys), all_=all_)
    return KeySet(keys=list(keys), all_=all_)


def json_object(value: dict[str, object]) -> Any:
    """Wrap a JSON mutation value for the real Spanner client lazily."""

    try:
        from google.cloud.spanner_v1.data_types import JsonObject
    except ImportError:
        return value
    return JsonObject(value)


def sql_param_types(spec: dict[str, str]) -> dict[str, Any] | None:
    try:
        from google.cloud.spanner_v1 import param_types
    except ImportError:
        return None
    resolved: dict[str, Any] = {}
    for name, kind in spec.items():
        if kind == "STRING":
            resolved[name] = param_types.STRING
        elif kind == "INT64":
            resolved[name] = param_types.INT64
        elif kind == "TIMESTAMP":
            resolved[name] = param_types.TIMESTAMP
        elif kind == "STRING_ARRAY":
            resolved[name] = param_types.Array(param_types.STRING)
        else:  # pragma: no cover - internal programming guard
            raise ValueError(f"unsupported Spanner parameter type {kind!r}")
    return resolved


def execute_sql(
    reader: Any,
    sql: str,
    *,
    params: dict[str, object],
    type_spec: dict[str, str],
):
    kwargs: dict[str, object] = {"params": params}
    types = sql_param_types(type_spec)
    if types is not None:
        kwargs["param_types"] = types
    return reader.execute_sql(sql, **kwargs)


def read_one(
    reader: Any,
    table: str,
    columns: tuple[str, ...],
    key: tuple[object, ...],
) -> tuple[object, ...] | None:
    rows = reader.read(table, columns=columns, keyset=keyset(key), limit=1)
    return next(iter(rows), None)


__all__ = [
    "commit_timestamp",
    "execute_sql",
    "json_object",
    "keyset",
    "read_one",
]
