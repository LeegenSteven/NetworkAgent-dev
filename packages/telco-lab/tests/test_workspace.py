from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from telco_lab.catalog import FixtureCatalogProvider
from telco_lab.downloader import SecureDownloader
from telco_lab.errors import LabError
from telco_lab.workspace import TelcoLab


class Response(BytesIO):
    status = 200

    def __init__(self, payload: bytes, url: str) -> None:
        super().__init__(payload)
        self._url = url
        self.headers = {"Content-Length": str(len(payload))}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class Opener:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def open(self, request, *, timeout: float):
        return Response(self.payload, request.full_url)


def _lab(tmp_path, catalog_mapping, fixture_bytes) -> TelcoLab:
    return TelcoLab(
        FixtureCatalogProvider(catalog_mapping),
        tmp_path,
        downloader=SecureDownloader(opener=Opener(fixture_bytes), chunk_size=8),
    )


def test_fetch_requires_exact_explicit_license_acceptance(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)

    with pytest.raises(LabError) as missing:
        lab.fetch("fixture.kpi.v1", accepted_license="")
    assert missing.value.code == "license_not_accepted"

    with pytest.raises(LabError) as wrong:
        lab.fetch("fixture.kpi.v1", accepted_license="MIT")
    assert wrong.value.code == "license_not_accepted"


def test_fetch_persists_a_versioned_lock_and_verify_detects_tampering(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    artifact = lab.fetch("fixture.kpi.v1", accepted_license="CC-BY-4.0")

    assert artifact.local_path.read_bytes() == fixture_bytes
    lock_payload = json.loads((tmp_path / "telco-lab.lock.json").read_text("utf-8"))
    assert lock_payload["schema_version"] == "1.0"
    assert lock_payload["lock_id"].startswith("lablock-")
    assert len(lock_payload["lock_id"]) == 72
    assert lock_payload["catalog_version"] == "1.0.0"
    locked = lock_payload["artifacts"][0]
    assert locked["sha256"] == artifact.sha256
    assert locked["catalog_resource_sha256"]
    assert locked["source_url_sha256"]
    assert locked["allowed_hosts"] == ["datasets.example.test"]
    assert locked["license_attribution"] == "Fixture dataset authors"
    assert locked["license_evidence_sha256"] == "a" * 64
    assert locked["license_reviewed_at"] == "2026-08-30"
    assert "source_url" not in locked
    assert "token=secret" not in json.dumps(lock_payload)

    verified = lab.verify()
    assert verified.valid is True
    assert verified.artifacts[0].status == "VERIFIED"
    assert lab.artifact_path("fixture.kpi.v1") == artifact.local_path

    artifact.local_path.write_bytes(b"x" * len(fixture_bytes))
    failed = lab.verify("fixture.kpi.v1")
    assert failed.valid is False
    assert failed.artifacts[0].status == "DIGEST_MISMATCH"
    with pytest.raises(LabError) as caught:
        lab.artifact_path("fixture.kpi.v1")
    assert caught.value.code == "artifact_unverified"


def test_workspace_lock_identity_binds_license_provenance(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    lab.fetch("fixture.kpi.v1", accepted_license="CC-BY-4.0")
    lock_path = tmp_path / "telco-lab.lock.json"
    payload = json.loads(lock_path.read_text("utf-8"))
    payload["artifacts"][0]["license_attribution"] = "Different authors"
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LabError) as caught:
        lab.verify()
    assert caught.value.code == "lock_invalid"


def test_unknown_resource_errors_do_not_echo_attacker_input(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    attacker_value = "https://evil.test/?token=secret"
    with pytest.raises(LabError) as caught:
        lab.fetch(attacker_value, accepted_license="CC-BY-4.0")

    assert caught.value.code == "resource_not_found"
    assert "secret" not in str(caught.value)


def test_verify_rejects_an_untrusted_lock_without_echoing_its_contents(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    tmp_path.mkdir(exist_ok=True)
    secret = "https://evil.test/?token=do-not-echo"
    (tmp_path / "telco-lab.lock.json").write_text(
        json.dumps({"schema_version": "1.0", "unexpected": secret}),
        encoding="utf-8",
    )

    with pytest.raises(LabError) as caught:
        lab.verify()

    assert caught.value.code == "lock_invalid"
    assert "do-not-echo" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":"1.0","schema_version":"9.9"}',
        b'{"schema_version":"1.0","value":NaN}',
        b'{"schema_version":"1.0","value":"\\ud800"}',
    ],
)
def test_workspace_lock_uses_strict_json_loader(
    tmp_path, catalog_mapping, fixture_bytes, payload
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "telco-lab.lock.json").write_bytes(payload)

    with pytest.raises(LabError) as caught:
        lab.verify()
    assert caught.value.code == "lock_invalid"


def test_workspace_lock_requires_explicit_schema_version(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    artifact = lab.fetch("fixture.kpi.v1", accepted_license="CC-BY-4.0")
    lock_path = tmp_path / "telco-lab.lock.json"
    payload = json.loads(lock_path.read_text("utf-8"))
    del payload["schema_version"]
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LabError) as caught:
        lab.verify()
    assert caught.value.code == "lock_invalid"
    assert artifact.local_path.exists()


def test_verify_refuses_to_race_an_active_workspace_operation(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    lab.fetch("fixture.kpi.v1", accepted_license="CC-BY-4.0")
    (tmp_path / ".telco-lab.operation.lock").write_bytes(b"locked\n")

    with pytest.raises(LabError) as caught:
        lab.verify()
    assert caught.value.code == "workspace_busy"


@pytest.mark.parametrize("generated_at", [0, 1.5, True])
def test_workspace_lock_does_not_coerce_timestamp_types(
    tmp_path, catalog_mapping, fixture_bytes, generated_at
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    lab.fetch("fixture.kpi.v1", accepted_license="CC-BY-4.0")
    lock_path = tmp_path / "telco-lab.lock.json"
    payload = json.loads(lock_path.read_text("utf-8"))
    payload["generated_at"] = generated_at
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LabError) as caught:
        lab.verify()
    assert caught.value.code == "lock_invalid"


def test_verify_rejects_a_junction_artifact_directory(
    tmp_path, catalog_mapping, fixture_bytes, monkeypatch
) -> None:
    lab = _lab(tmp_path, catalog_mapping, fixture_bytes)
    (tmp_path / "artifacts").mkdir()
    original = Path.is_junction
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path.name == "artifacts" or original(path),
    )

    with pytest.raises(LabError) as caught:
        lab.verify()
    assert caught.value.code == "workspace_unsafe"
