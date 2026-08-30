"""Offline catalog providers; loading a catalog never performs network I/O."""

from __future__ import annotations

from importlib import resources
from typing import Mapping, Protocol, runtime_checkable

from pydantic import ValidationError

from .errors import LabError
from .models import DatasetCatalog
from .safe_json import StrictJsonError, load_strict_json


_MAX_CATALOG_BYTES = 1024 * 1024


@runtime_checkable
class CatalogProvider(Protocol):
    def load(self) -> DatasetCatalog:
        """Load and validate one immutable catalog without network access."""


class FixtureCatalogProvider:
    """Dependency-injected catalog provider intended for hermetic tests."""

    def __init__(self, catalog: DatasetCatalog | Mapping[str, object]) -> None:
        self._catalog = (
            catalog
            if isinstance(catalog, DatasetCatalog)
            else DatasetCatalog.model_validate(catalog)
        )

    def load(self) -> DatasetCatalog:
        return self._catalog


class PackageCatalogProvider:
    """Load the audited catalog bundled in the installed wheel."""

    def __init__(self, filename: str = "default.json") -> None:
        if filename not in {"default.json"}:
            raise LabError("catalog_unavailable")
        self._filename = filename
        self._catalog: DatasetCatalog | None = None

    def load(self) -> DatasetCatalog:
        if self._catalog is not None:
            return self._catalog
        try:
            catalog_file = resources.files("telco_lab").joinpath(
                "catalogs", self._filename
            )
            with catalog_file.open("rb") as stream:
                raw = stream.read(_MAX_CATALOG_BYTES + 1)
            payload = load_strict_json(
                raw,
                max_bytes=_MAX_CATALOG_BYTES,
                max_depth=16,
            )
            self._catalog = DatasetCatalog.model_validate(payload)
            return self._catalog
        except LabError:
            raise
        except (OSError, UnicodeError, StrictJsonError, ValidationError) as exc:
            raise LabError("catalog_unavailable") from exc


__all__ = ["CatalogProvider", "FixtureCatalogProvider", "PackageCatalogProvider"]
