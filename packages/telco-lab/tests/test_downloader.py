from __future__ import annotations

from io import BytesIO
from pathlib import Path
import urllib.request

import pytest

from telco_lab.catalog import FixtureCatalogProvider
from telco_lab.downloader import SecureDownloader, _AllowlistedRedirectHandler
from telco_lab.errors import LabError


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        final_url: str,
        content_length: int | None = None,
        status: int = 200,
    ) -> None:
        self._body = BytesIO(payload)
        self._url = final_url
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self._body.close()


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = 0

    def open(self, _request, *, timeout: float):
        assert timeout > 0
        self.calls += 1
        return self.response


class FailingOpener:
    def open(self, _request, *, timeout: float):
        raise AssertionError("network must not be used for a valid cache hit")


def _resource(catalog_mapping):
    return FixtureCatalogProvider(catalog_mapping).load().resources[0]


def test_streams_verified_bytes_to_an_atomic_target(
    tmp_path: Path, catalog_mapping, fixture_bytes
) -> None:
    resource = _resource(catalog_mapping)
    opener = FakeOpener(
        FakeResponse(
            fixture_bytes,
            final_url=str(resource.source_url),
            content_length=len(fixture_bytes),
        )
    )
    target = tmp_path / resource.filename

    receipt = SecureDownloader(opener=opener, chunk_size=7).download(resource, target)

    assert target.read_bytes() == fixture_bytes
    assert receipt.cached is False
    assert receipt.sha256 == resource.sha256
    assert list(tmp_path.glob("*.part")) == []


def test_valid_cache_is_reverified_without_network(
    tmp_path: Path, catalog_mapping, fixture_bytes
) -> None:
    resource = _resource(catalog_mapping)
    target = tmp_path / resource.filename
    target.write_bytes(fixture_bytes)

    receipt = SecureDownloader(opener=FailingOpener()).download(resource, target)

    assert receipt.cached is True
    assert receipt.size_bytes == len(fixture_bytes)


@pytest.mark.parametrize(
    "final_url",
    [
        "http://datasets.example.test/releases/fixture.csv",
        "https://evil.example.test/releases/fixture.csv",
    ],
)
def test_rejects_an_unsafe_redirect_without_committing_target(
    tmp_path: Path, catalog_mapping, fixture_bytes, final_url: str
) -> None:
    resource = _resource(catalog_mapping)
    target = tmp_path / resource.filename
    opener = FakeOpener(FakeResponse(fixture_bytes, final_url=final_url))

    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=opener).download(resource, target)

    assert caught.value.code == "unsafe_redirect"
    assert "evil.example" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert not target.exists()


def test_rejects_oversize_body_and_cleans_temporary_files(
    tmp_path: Path, catalog_mapping, fixture_bytes
) -> None:
    resource = _resource(catalog_mapping)
    target = tmp_path / resource.filename
    opener = FakeOpener(
        FakeResponse(
            fixture_bytes + b"too much",
            final_url=str(resource.source_url),
        )
    )

    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=opener, chunk_size=3).download(resource, target)

    assert caught.value.code == "download_too_large"
    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_rejects_same_size_digest_mismatch(tmp_path, catalog_mapping, fixture_bytes) -> None:
    resource = _resource(catalog_mapping)
    payload = b"x" * len(fixture_bytes)
    opener = FakeOpener(FakeResponse(payload, final_url=str(resource.source_url)))

    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=opener).download(
            resource, tmp_path / resource.filename
        )
    assert caught.value.code == "digest_mismatch"


@pytest.mark.parametrize(
    ("content_length", "expected_code"),
    [(1, "size_mismatch"), (10_000, "download_too_large")],
)
def test_rejects_content_length_mismatch(
    tmp_path, catalog_mapping, fixture_bytes, content_length, expected_code
) -> None:
    resource = _resource(catalog_mapping)
    opener = FakeOpener(
        FakeResponse(
            fixture_bytes,
            final_url=str(resource.source_url),
            content_length=content_length,
        )
    )
    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=opener).download(
            resource, tmp_path / resource.filename
        )
    assert caught.value.code == expected_code


def test_rejects_non_success_status(tmp_path, catalog_mapping, fixture_bytes) -> None:
    resource = _resource(catalog_mapping)
    opener = FakeOpener(
        FakeResponse(
            fixture_bytes,
            final_url=str(resource.source_url),
            status=503,
        )
    )
    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=opener).download(
            resource, tmp_path / resource.filename
        )
    assert caught.value.code == "unexpected_response"


def test_network_errors_are_sanitized(tmp_path: Path, catalog_mapping) -> None:
    class SecretFailure:
        def open(self, request, *, timeout: float):
            raise OSError(f"failed {request.full_url} at {tmp_path}")

    resource = _resource(catalog_mapping)
    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=SecretFailure()).download(
            resource, tmp_path / resource.filename
        )

    assert caught.value.code == "download_failed"
    assert "secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_rejects_a_junction_cache_directory_before_network(
    tmp_path: Path, catalog_mapping, monkeypatch
) -> None:
    resource = _resource(catalog_mapping)
    target = tmp_path / resource.filename
    original = Path.is_junction
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == tmp_path or original(path),
    )

    with pytest.raises(LabError) as caught:
        SecureDownloader(opener=FailingOpener()).download(resource, target)
    assert caught.value.code == "workspace_unsafe"


def test_production_redirect_handler_never_reads_redirect_body(catalog_mapping) -> None:
    resource = _resource(catalog_mapping)

    class RedirectBody:
        closed = False

        def read(self, _size=-1):
            raise AssertionError("redirect response bodies must never be read")

        def close(self):
            self.closed = True

    class Parent:
        request = None

        def open(self, request, *, timeout):
            self.request = request
            return "followed"

    body = RedirectBody()
    parent = Parent()
    handler = _AllowlistedRedirectHandler(resource)
    handler.parent = parent
    request = urllib.request.Request(str(resource.source_url))
    request.timeout = 3

    result = handler.http_error_302(
        request,
        body,
        302,
        "Found",
        {"location": "/next.csv"},
    )

    assert result == "followed"
    assert body.closed is True
    assert parent.request.full_url == "https://datasets.example.test/next.csv"


def test_production_redirect_handler_closes_unsafe_redirect_body(catalog_mapping) -> None:
    resource = _resource(catalog_mapping)

    class RedirectBody:
        closed = False

        def read(self, _size=-1):
            raise AssertionError("redirect response bodies must never be read")

        def close(self):
            self.closed = True

    body = RedirectBody()
    handler = _AllowlistedRedirectHandler(resource)
    request = urllib.request.Request(str(resource.source_url))
    request.timeout = 3

    with pytest.raises(LabError) as caught:
        handler.http_error_302(
            request,
            body,
            302,
            "Found",
            {"location": "http://evil.example.test/next.csv"},
        )
    assert caught.value.code == "unsafe_redirect"
    assert body.closed is True
