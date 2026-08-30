"""Reproducible dataset workspace orchestration and lock verification."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Literal

from pydantic import ValidationError

from .catalog import CatalogProvider
from .downloader import SecureDownloader
from .errors import LabError
from .models import (
    LOCK_SCHEMA_VERSION,
    CatalogResource,
    LockedArtifact,
    WorkspaceLock,
    catalog_resource_sha256,
    source_url_sha256,
    workspace_lock_id,
)
from .safe_json import StrictJsonError, load_strict_json


LOCK_FILENAME = "telco-lab.lock.json"
_OPERATION_LOCK_FILENAME = ".telco-lab.operation.lock"
_ARTIFACT_DIRECTORY = "artifacts"
_MAX_LOCK_BYTES = 1024 * 1024


def _is_link_like(path: Path) -> bool:
    try:
        junction_check = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(
            callable(junction_check) and junction_check()
        )
    except OSError:
        return True


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    resource_id: str
    filename: str
    sha256: str
    size_bytes: int
    local_path: Path
    cached: bool


VerificationStatus = Literal[
    "VERIFIED",
    "NOT_FETCHED",
    "MISSING",
    "SIZE_MISMATCH",
    "DIGEST_MISMATCH",
    "CATALOG_MISMATCH",
]


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    resource_id: str
    filename: str
    status: VerificationStatus


@dataclass(frozen=True, slots=True)
class VerificationReport:
    schema_version: str
    catalog_id: str
    catalog_version: str
    valid: bool
    artifacts: tuple[ArtifactVerification, ...]


class TelcoLab:
    """Local-only facade; construction and catalog inspection never use network I/O."""

    def __init__(
        self,
        provider: CatalogProvider,
        workspace: Path,
        *,
        downloader: SecureDownloader | None = None,
    ) -> None:
        self._provider = provider
        self._workspace = Path(workspace)
        self._downloader = downloader if downloader is not None else SecureDownloader()

    def catalog(self):
        return self._provider.load()

    @property
    def _artifact_directory(self) -> Path:
        return self._workspace / _ARTIFACT_DIRECTORY

    @property
    def _lock_path(self) -> Path:
        return self._workspace / LOCK_FILENAME

    def _resource(self, resource_id: str) -> CatalogResource:
        resource = self.catalog().resource(resource_id)
        if resource is None:
            raise LabError("resource_not_found")
        return resource

    def _artifact_path(self, resource: CatalogResource) -> Path:
        directory = self._artifact_directory
        candidate = directory / resource.filename
        if candidate.parent != directory or candidate.name != resource.filename:
            raise LabError("workspace_unsafe")
        return candidate

    def _ensure_workspace(self) -> None:
        try:
            self._workspace.mkdir(parents=True, exist_ok=True)
            if not self._workspace.is_dir() or _is_link_like(self._workspace):
                raise LabError("workspace_unsafe")
            self._artifact_directory.mkdir(exist_ok=True)
            if (
                not self._artifact_directory.is_dir()
                or _is_link_like(self._artifact_directory)
                or _is_link_like(self._lock_path)
                or self._artifact_directory.resolve().parent
                != self._workspace.resolve()
            ):
                raise LabError("workspace_unsafe")
        except LabError:
            raise
        except OSError as exc:
            raise LabError("workspace_unsafe") from exc

    def _validate_existing_workspace(self) -> None:
        try:
            if (
                not self._workspace.is_dir()
                or _is_link_like(self._workspace)
                or _is_link_like(self._lock_path)
            ):
                raise LabError("workspace_unsafe")
            if self._artifact_directory.exists() and (
                not self._artifact_directory.is_dir()
                or _is_link_like(self._artifact_directory)
                or self._artifact_directory.resolve().parent
                != self._workspace.resolve()
            ):
                raise LabError("workspace_unsafe")
        except LabError:
            raise
        except OSError as exc:
            raise LabError("workspace_unsafe") from exc

    @contextmanager
    def _operation(self, *, create_workspace: bool = True) -> Iterator[None]:
        if create_workspace:
            self._ensure_workspace()
        else:
            self._validate_existing_workspace()
        marker = self._workspace / _OPERATION_LOCK_FILENAME
        descriptor: int | None = None
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LabError("workspace_busy") from exc
        except OSError as exc:
            raise LabError("workspace_unsafe") from exc
        try:
            os.write(descriptor, b"locked\n")
            os.fsync(descriptor)
            yield
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_lock(self, *, required: bool) -> WorkspaceLock | None:
        try:
            if not self._lock_path.exists():
                if required:
                    raise LabError("lock_invalid")
                return None
            if self._lock_path.is_symlink() or not self._lock_path.is_file():
                raise LabError("lock_invalid")
            with self._lock_path.open("rb") as stream:
                raw = stream.read(_MAX_LOCK_BYTES + 1)
            payload = load_strict_json(
                raw,
                max_bytes=_MAX_LOCK_BYTES,
                max_depth=16,
            )
            return WorkspaceLock.model_validate(payload)
        except LabError:
            raise
        except (OSError, UnicodeError, StrictJsonError, ValidationError) as exc:
            raise LabError("lock_invalid") from exc

    def _write_lock(self, lock: WorkspaceLock) -> None:
        temporary: Path | None = None
        try:
            serialized = json.dumps(
                lock.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8") + b"\n"
            if len(serialized) > _MAX_LOCK_BYTES:
                raise LabError("lock_invalid")
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".telco-lab.lock.",
                suffix=".part",
                dir=self._workspace,
                delete=False,
            )
            temporary = Path(handle.name)
            with handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._lock_path)
            temporary = None
        except LabError:
            raise
        except OSError as exc:
            raise LabError("lock_invalid") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def fetch(self, resource_id: str, *, accepted_license: str) -> ArtifactRecord:
        resource = self._resource(resource_id)
        if accepted_license != resource.license.id:
            raise LabError("license_not_accepted")
        with self._operation():
            existing = self._read_lock(required=False)
            catalog = self.catalog()
            if existing is not None and (
                existing.catalog_id != catalog.catalog_id
                or existing.catalog_version != catalog.catalog_version
            ):
                raise LabError("lock_invalid")
            target = self._artifact_path(resource)
            receipt = self._downloader.download(resource, target)

            old_artifacts = list(existing.artifacts) if existing is not None else []
            old = next(
                (item for item in old_artifacts if item.resource_id == resource.resource_id),
                None,
            )
            if receipt.cached and old is not None and self._locked_matches_resource(old, resource):
                locked = old
            else:
                locked = LockedArtifact(
                    resource_id=resource.resource_id,
                    dataset_id=resource.dataset_id,
                    dataset_version=resource.dataset_version,
                    filename=resource.filename,
                    sha256=resource.sha256,
                    size_bytes=resource.size_bytes,
                    media_type=resource.media_type,
                    adapter=resource.adapter,
                    catalog_resource_sha256=catalog_resource_sha256(resource),
                    source_url_sha256=source_url_sha256(resource.source_url),
                    allowed_hosts=resource.allowed_hosts,
                    license_id=resource.license.id,
                    license_name=resource.license.name,
                    license_url=resource.license.url,
                    license_evidence_url=resource.license.evidence_url,
                    license_evidence_sha256=resource.license.evidence_sha256,
                    license_attribution=resource.license.attribution,
                    license_reviewed_at=resource.license.reviewed_at,
                    fetched_at=datetime.now(UTC),
                )
            artifacts = [
                item for item in old_artifacts if item.resource_id != resource.resource_id
            ]
            artifacts.append(locked)
            locked_artifacts = tuple(
                sorted(artifacts, key=lambda item: item.resource_id)
            )
            workspace_lock = WorkspaceLock(
                schema_version=LOCK_SCHEMA_VERSION,
                lock_id=workspace_lock_id(
                    catalog.catalog_id,
                    catalog.catalog_version,
                    locked_artifacts,
                ),
                catalog_id=catalog.catalog_id,
                catalog_version=catalog.catalog_version,
                generated_at=datetime.now(UTC),
                artifacts=locked_artifacts,
            )
            self._write_lock(workspace_lock)
            return ArtifactRecord(
                resource_id=receipt.resource_id,
                filename=receipt.filename,
                sha256=receipt.sha256,
                size_bytes=receipt.size_bytes,
                local_path=target,
                cached=receipt.cached,
            )

    @staticmethod
    def _locked_matches_resource(
        locked: LockedArtifact, resource: CatalogResource
    ) -> bool:
        return (
            locked.dataset_id == resource.dataset_id
            and locked.dataset_version == resource.dataset_version
            and locked.filename == resource.filename
            and locked.sha256 == resource.sha256
            and locked.size_bytes == resource.size_bytes
            and locked.media_type == resource.media_type
            and locked.adapter == resource.adapter
            and locked.catalog_resource_sha256
            == catalog_resource_sha256(resource)
            and locked.source_url_sha256 == source_url_sha256(resource.source_url)
            and locked.allowed_hosts == resource.allowed_hosts
            and locked.license_id == resource.license.id
            and locked.license_name == resource.license.name
            and locked.license_url == resource.license.url
            and locked.license_evidence_url == resource.license.evidence_url
            and locked.license_evidence_sha256
            == resource.license.evidence_sha256
            and locked.license_attribution == resource.license.attribution
            and locked.license_reviewed_at == resource.license.reviewed_at
        )

    def verify(self, resource_id: str | None = None) -> VerificationReport:
        catalog = self.catalog()
        selected = (self._resource(resource_id),) if resource_id is not None else None
        if not self._workspace.exists():
            return self._verify_snapshot(catalog, selected, resource_id)
        with self._operation(create_workspace=False):
            return self._verify_snapshot(catalog, selected, resource_id)

    def _verify_snapshot(
        self,
        catalog,
        selected: tuple[CatalogResource, ...] | None,
        resource_id: str | None,
    ) -> VerificationReport:
        lock = self._read_lock(required=False)
        if lock is None:
            resources = selected or catalog.resources
            artifacts = tuple(
                ArtifactVerification(item.resource_id, item.filename, "NOT_FETCHED")
                for item in resources
            )
            return VerificationReport(
                schema_version="1.0",
                catalog_id=catalog.catalog_id,
                catalog_version=catalog.catalog_version,
                valid=False,
                artifacts=artifacts,
            )

        catalog_matches = (
            lock.catalog_id == catalog.catalog_id
            and lock.catalog_version == catalog.catalog_version
        )
        locked_items = lock.artifacts
        if selected is not None:
            locked_items = tuple(
                item for item in locked_items if item.resource_id == resource_id
            )
            if not locked_items:
                resource = selected[0]
                return VerificationReport(
                    schema_version="1.0",
                    catalog_id=catalog.catalog_id,
                    catalog_version=catalog.catalog_version,
                    valid=False,
                    artifacts=(
                        ArtifactVerification(
                            resource.resource_id, resource.filename, "NOT_FETCHED"
                        ),
                    ),
                )

        results: list[ArtifactVerification] = []
        for locked in locked_items:
            resource = catalog.resource(locked.resource_id)
            if (
                not catalog_matches
                or resource is None
                or not self._locked_matches_resource(locked, resource)
            ):
                status: VerificationStatus = "CATALOG_MISMATCH"
            else:
                path = self._artifact_path(resource)
                try:
                    if path.is_symlink() or not path.is_file():
                        status = "MISSING"
                    elif path.stat().st_size != locked.size_bytes:
                        status = "SIZE_MISMATCH"
                    else:
                        digest = hashlib.sha256()
                        with path.open("rb") as stream:
                            while block := stream.read(256 * 1024):
                                digest.update(block)
                        status = (
                            "VERIFIED"
                            if digest.hexdigest() == locked.sha256
                            else "DIGEST_MISMATCH"
                        )
                except OSError:
                    status = "MISSING"
            results.append(
                ArtifactVerification(locked.resource_id, locked.filename, status)
            )

        valid = bool(results) and all(item.status == "VERIFIED" for item in results)
        return VerificationReport(
            schema_version="1.0",
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.catalog_version,
            valid=valid,
            artifacts=tuple(results),
        )

    def artifact_path(self, resource_id: str) -> Path:
        report = self.verify(resource_id)
        if not report.valid:
            raise LabError("artifact_unverified")
        return self._artifact_path(self._resource(resource_id))

    def verified_manifest(self) -> WorkspaceLock:
        """Return the fully verified lock without exposing artifact paths."""

        catalog = self.catalog()
        if not self._workspace.exists():
            raise LabError("artifact_unverified")
        with self._operation(create_workspace=False):
            report = self._verify_snapshot(catalog, None, None)
            if not report.valid:
                raise LabError("artifact_unverified")
            lock = self._read_lock(required=True)
            if lock is None:  # pragma: no cover - required=True is fail-closed
                raise LabError("lock_invalid")
            return lock


__all__ = [
    "ArtifactRecord",
    "ArtifactVerification",
    "TelcoLab",
    "VerificationReport",
]
