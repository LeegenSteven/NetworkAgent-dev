"""Explicit Local Profile configuration without environment side effects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class LocalProfileConfig:
    """Every filesystem and timestamp assumption required by Local Profile.

    LTE demo timestamps do not carry offsets.  Importing them under a machine's
    local timezone would make correlation and evidence windows non-deterministic,
    so the only supported interpretation is explicitly configured UTC.
    """

    database_path: Path
    performance_csv_path: Path
    safe_trace_csv_path: Path
    rules_dir: Path
    source_timezone: str
    documents_dir: Path | None = None

    def __post_init__(self) -> None:
        normalized_timezone = str(self.source_timezone).strip().upper()
        if normalized_timezone != "UTC":
            raise ValueError("Local Profile source_timezone must explicitly be UTC")

        object.__setattr__(self, "source_timezone", "UTC")
        for field_name in (
            "database_path",
            "performance_csv_path",
            "safe_trace_csv_path",
            "rules_dir",
        ):
            object.__setattr__(self, field_name, _resolved(getattr(self, field_name)))
        if self.documents_dir is not None:
            object.__setattr__(self, "documents_dir", _resolved(self.documents_dir))

        source_paths = {
            self.performance_csv_path,
            self.safe_trace_csv_path,
        }
        if self.database_path in source_paths:
            raise ValueError("database_path must not overwrite a source CSV")
        if self.performance_csv_path == self.safe_trace_csv_path:
            raise ValueError("performance and safe trace inputs must be different files")

    def validate_inputs(self) -> None:
        """Fail before opening DuckDB when a configured source is unavailable."""

        for path in (self.performance_csv_path, self.safe_trace_csv_path):
            if not path.is_file():
                raise FileNotFoundError(path)
        if not self.rules_dir.is_dir():
            raise FileNotFoundError(self.rules_dir)
        if self.documents_dir is not None and not self.documents_dir.is_dir():
            raise FileNotFoundError(self.documents_dir)


__all__ = ["LocalProfileConfig"]
