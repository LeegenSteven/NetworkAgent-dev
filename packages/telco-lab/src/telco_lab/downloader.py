"""A narrowly scoped HTTPS downloader with verified, atomic cache writes."""

from __future__ import annotations

import hashlib
import os
import string
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .errors import LabError
from .models import MAX_DOWNLOAD_BYTES, CatalogResource, validate_https_url


DEFAULT_CHUNK_SIZE = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0


class _Response(Protocol):
    status: int
    headers: object

    def read(self, size: int = -1) -> bytes: ...

    def geturl(self) -> str: ...

    def __enter__(self) -> "_Response": ...

    def __exit__(self, *args: object) -> object: ...


class _Opener(Protocol):
    def open(self, request: urllib.request.Request, *, timeout: float) -> _Response: ...


@dataclass(frozen=True, slots=True)
class DownloadReceipt:
    resource_id: str
    filename: str
    sha256: str
    size_bytes: int
    cached: bool


def _is_link_like(path: Path) -> bool:
    try:
        junction_check = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(
            callable(junction_check) and junction_check()
        )
    except OSError:
        return True


def _hash_stream(stream: BinaryIO, *, chunk_size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(chunk_size):
        size += len(block)
        digest.update(block)
    return digest.hexdigest(), size


def verify_file(path: Path, resource: CatalogResource) -> bool:
    """Re-read a cache file and compare both its size and SHA-256 digest."""

    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != resource.size_bytes:
            return False
        with path.open("rb") as stream:
            digest, size = _hash_stream(stream, chunk_size=DEFAULT_CHUNK_SIZE)
        return size == resource.size_bytes and digest == resource.sha256
    except OSError:
        return False


def _require_allowed_url(url: str, resource: CatalogResource, *, redirect: bool) -> None:
    try:
        validate_https_url(url, allowed_hosts=resource.allowed_hosts)
    except ValueError as exc:
        raise LabError("unsafe_redirect" if redirect else "unsafe_source") from exc


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def __init__(self, resource: CatalogResource) -> None:
        super().__init__()
        self._resource = resource

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _require_allowed_url(newurl, self._resource, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        """Follow a safe redirect without urllib's unbounded ``fp.read()``."""

        try:
            if "location" in headers:
                new_url = headers["location"]
            elif "uri" in headers:
                new_url = headers["uri"]
            else:
                raise LabError("unexpected_response")
            parsed = urllib.parse.urlparse(new_url)
            if not parsed.path and parsed.netloc:
                parts = list(parsed)
                parts[2] = "/"
                new_url = urllib.parse.urlunparse(parts)
            new_url = urllib.parse.quote(
                new_url,
                encoding="iso-8859-1",
                safe=string.punctuation,
            )
            new_url = urllib.parse.urljoin(req.full_url, new_url)
            _require_allowed_url(new_url, self._resource, redirect=True)
            new_request = self.redirect_request(
                req, fp, code, msg, headers, new_url
            )
            if new_request is None:
                raise LabError("unsafe_redirect")

            if hasattr(req, "redirect_dict"):
                visited = new_request.redirect_dict = req.redirect_dict
                if (
                    visited.get(new_url, 0) >= self.max_repeats
                    or len(visited) >= self.max_redirections
                ):
                    raise LabError("unsafe_redirect")
            else:
                visited = new_request.redirect_dict = req.redirect_dict = {}
            visited[new_url] = visited.get(new_url, 0) + 1
        except Exception:
            fp.close()
            raise

        # The stdlib implementation calls fp.read() here with no limit. A 3xx
        # body is irrelevant to this workflow, so close it without consuming it.
        fp.close()
        return self.parent.open(new_request, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_302
    http_error_308 = http_error_302


def _content_length(response: _Response) -> int | None:
    try:
        headers = response.headers
        getter = getattr(headers, "get", None)
        raw = getter("Content-Length") if callable(getter) else None
        if raw is None:
            return None
        value = int(raw)
        if value < 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise LabError("unexpected_response") from None


class SecureDownloader:
    """Download only an explicitly selected, catalog-pinned resource."""

    def __init__(
        self,
        *,
        opener: _Opener | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not 1 <= chunk_size <= 4 * 1024 * 1024:
            raise ValueError("chunk_size is outside the supported range")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds is outside the supported range")
        self._opener = opener
        self._chunk_size = chunk_size
        self._timeout_seconds = timeout_seconds

    def _network_opener(self, resource: CatalogResource) -> _Opener:
        if self._opener is not None:
            return self._opener
        return urllib.request.build_opener(_AllowlistedRedirectHandler(resource))

    def download(self, resource: CatalogResource, target: Path) -> DownloadReceipt:
        """Fetch and atomically commit one resource, or revalidate a cache hit."""

        target = Path(target)
        _require_allowed_url(resource.source_url, resource, redirect=False)
        if resource.size_bytes > MAX_DOWNLOAD_BYTES:
            raise LabError("download_too_large")
        if target.name != resource.filename or _is_link_like(target):
            raise LabError("workspace_unsafe")
        if verify_file(target, resource):
            return DownloadReceipt(
                resource_id=resource.resource_id,
                filename=resource.filename,
                sha256=resource.sha256,
                size_bytes=resource.size_bytes,
                cached=True,
            )

        temporary: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if _is_link_like(target.parent):
                raise LabError("workspace_unsafe")
            request = urllib.request.Request(
                resource.source_url,
                headers={
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                    "User-Agent": "NetworkAgent-telco-lab/0.1",
                },
                method="GET",
            )
            with self._network_opener(resource).open(
                request, timeout=self._timeout_seconds
            ) as response:
                if int(getattr(response, "status", 0)) != 200:
                    raise LabError("unexpected_response")
                _require_allowed_url(response.geturl(), resource, redirect=True)
                content_length = _content_length(response)
                if content_length is not None:
                    if content_length > resource.size_bytes:
                        raise LabError("download_too_large")
                    if content_length != resource.size_bytes:
                        raise LabError("size_mismatch")

                handle = tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{resource.filename}.",
                    suffix=".part",
                    dir=target.parent,
                    delete=False,
                )
                temporary = Path(handle.name)
                digest = hashlib.sha256()
                size = 0
                with handle:
                    while block := response.read(self._chunk_size):
                        size += len(block)
                        if size > resource.size_bytes or size > MAX_DOWNLOAD_BYTES:
                            raise LabError("download_too_large")
                        digest.update(block)
                        handle.write(block)
                    handle.flush()
                    os.fsync(handle.fileno())

            if size != resource.size_bytes:
                raise LabError("size_mismatch")
            if digest.hexdigest() != resource.sha256:
                raise LabError("digest_mismatch")
            os.replace(temporary, target)
            temporary = None
            return DownloadReceipt(
                resource_id=resource.resource_id,
                filename=resource.filename,
                sha256=resource.sha256,
                size_bytes=resource.size_bytes,
                cached=False,
            )
        except LabError:
            raise
        except Exception as exc:
            raise LabError("download_failed") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = ["DownloadReceipt", "SecureDownloader", "verify_file"]
