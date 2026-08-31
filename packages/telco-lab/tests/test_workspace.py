from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

import pytest

from telco_lab import workspace as workspace_module
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


def _two_resource_lab(
    tmp_path, catalog_mapping, fixture_bytes, *, fetch_second: bool = True
):
    mapping = json.loads(json.dumps(catalog_mapping))
    second = json.loads(json.dumps(mapping["resources"][0]))
    second.update(
        {
            "resource_id": "fixture.telemetry.v1",
            "filename": "fixture-telemetry.csv",
            "source_url": "https://datasets.example.test/releases/telemetry.csv",
        }
    )
    mapping["resources"].append(second)
    lab = _lab(tmp_path, mapping, fixture_bytes)
    lab.fetch("fixture.kpi.v1", accepted_license="CC-BY-4.0")
    if fetch_second:
        lab.fetch("fixture.telemetry.v1", accepted_license="CC-BY-4.0")
    return lab


def _assert_artifact_unverified(caught: pytest.ExceptionInfo[LabError]) -> None:
    assert caught.value.code == "artifact_unverified"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


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


def test_open_verified_artifacts_holds_safe_streams_and_operation_lock(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _two_resource_lab(tmp_path, catalog_mapping, fixture_bytes)
    resource_ids = ("fixture.kpi.v1", "fixture.telemetry.v1")

    with lab.open_verified_artifacts(resource_ids) as artifacts:
        assert tuple(item.resource_id for item in artifacts) == resource_ids
        assert all(
            isinstance(item, workspace_module.VerifiedArtifactStream)
            for item in artifacts
        )
        assert all(item.read() == fixture_bytes for item in artifacts)
        assert all(item.seek(0) == 0 for item in artifacts)
        assert (tmp_path / ".telco-lab.operation.lock").is_file()

        public_repr = repr(artifacts[0])
        for prohibited in (
            str(tmp_path),
            "fixture-kpi.csv",
            "datasets.example.test",
            "filename",
            "local_path",
            "source_url",
        ):
            assert prohibited not in public_repr
        assert not hasattr(artifacts[0], "filename")
        assert not hasattr(artifacts[0], "local_path")
        assert not hasattr(artifacts[0], "source_url")

        with pytest.raises(LabError) as busy:
            lab.verify()
        assert busy.value.code == "workspace_busy"

    assert all(item.closed for item in artifacts)
    with pytest.raises(LabError) as closed:
        artifacts[0].read()
    _assert_artifact_unverified(closed)


@pytest.mark.parametrize(
    ("method_name", "arguments"),
    [
        ("read", (1,)),
        ("readinto", (bytearray(1),)),
        ("seek", (0,)),
        ("tell", ()),
        ("closed", ()),
        ("readable", ()),
        ("seekable", ()),
    ],
)
def test_verified_artifact_stream_operations_detach_private_failures(
    method_name: str,
    arguments: tuple[object, ...],
) -> None:
    class _BombStream:
        @property
        def closed(self):
            raise RuntimeError("PRIVATE-STREAM-CANARY")

        def read(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-STREAM-CANARY")

        def readinto(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-STREAM-CANARY")

        def seek(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-STREAM-CANARY")

        def tell(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-STREAM-CANARY")

    stream = workspace_module.VerifiedArtifactStream(
        resource_id="fixture.kpi.v1",
        dataset_id="fixture-network-kpi",
        dataset_version="1.0.0",
        sha256="a" * 64,
        size_bytes=1,
        media_type="text/csv",
        adapter="fixture_adapter_v1",
        _stream=_BombStream(),
    )

    with pytest.raises(LabError) as error:
        operation = getattr(stream, method_name)
        if callable(operation):
            operation(*arguments)

    _assert_artifact_unverified(error)
    assert "PRIVATE-STREAM-CANARY" not in str(error.value)
    assert "PRIVATE-STREAM-CANARY" not in repr(error.value)


def test_open_verified_artifacts_rejects_duplicate_missing_and_extra_closures(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _two_resource_lab(tmp_path, catalog_mapping, fixture_bytes)

    invalid_closures = (
        ("fixture.kpi.v1", "fixture.kpi.v1"),
        ("fixture.kpi.v1",),
        ("fixture.kpi.v1", "fixture.telemetry.v1", "fixture.extra.v1"),
    )
    for resource_ids in invalid_closures:
        with pytest.raises(LabError) as caught:
            with lab.open_verified_artifacts(resource_ids):
                raise AssertionError("an invalid closure must not be yielded")
        _assert_artifact_unverified(caught)

    missing_lab = _two_resource_lab(
        tmp_path / "missing", catalog_mapping, fixture_bytes, fetch_second=False
    )
    with pytest.raises(LabError) as missing:
        with missing_lab.open_verified_artifacts(
            ("fixture.kpi.v1", "fixture.telemetry.v1")
        ):
            raise AssertionError("a partial lock must not be yielded")
    _assert_artifact_unverified(missing)


def test_open_verified_artifacts_bounds_resource_iterables_before_workspace_io(
    tmp_path,
    catalog_mapping,
) -> None:
    class ResourceId(str):
        pass

    def endless_ids():
        for index in range(10_000):
            yield f"fixture.resource-{index}.v1"

    workspace = tmp_path / "missing"
    lab = TelcoLab(FixtureCatalogProvider(catalog_mapping), workspace)
    with pytest.raises(LabError) as excessive:
        with lab.open_verified_artifacts(endless_ids()):
            raise AssertionError("an excessive iterable must not be yielded")
    _assert_artifact_unverified(excessive)
    assert not workspace.exists()

    with pytest.raises(LabError) as subclassed:
        with lab.open_verified_artifacts((ResourceId("fixture.kpi.v1"),)):
            raise AssertionError("a string subclass must not be yielded")
    _assert_artifact_unverified(subclassed)
    assert not workspace.exists()

    def broken_ids():
        yield "fixture.kpi.v1"
        raise RuntimeError("private iterable failure")

    with pytest.raises(LabError) as broken:
        with lab.open_verified_artifacts(broken_ids()):
            raise AssertionError("a broken iterable must not be yielded")
    _assert_artifact_unverified(broken)
    assert not workspace.exists()


def test_open_verified_artifacts_rejects_catalog_identity_drift(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    lab = _two_resource_lab(tmp_path, catalog_mapping, fixture_bytes)
    changed = lab.catalog().model_dump(mode="json")
    changed["resources"][0]["adapter"] = "different_adapter_v1"
    drifted = TelcoLab(FixtureCatalogProvider(changed), tmp_path)

    with pytest.raises(LabError) as caught:
        with drifted.open_verified_artifacts(
            ("fixture.kpi.v1", "fixture.telemetry.v1")
        ):
            raise AssertionError("catalog drift must not be yielded")
    _assert_artifact_unverified(caught)


def test_open_verified_artifacts_rejects_links_and_hardlinks(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    symlink_lab = _two_resource_lab(
        tmp_path / "symlink", catalog_mapping, fixture_bytes
    )
    symlink_artifact = tmp_path / "symlink" / "artifacts" / "fixture-kpi.csv"
    symlink_target = tmp_path / "symlink-target.csv"
    symlink_target.write_bytes(fixture_bytes)
    symlink_artifact.unlink()
    try:
        symlink_artifact.symlink_to(symlink_target)
    except OSError:
        pass
    else:
        with pytest.raises(LabError) as linked:
            with symlink_lab.open_verified_artifacts(
                ("fixture.kpi.v1", "fixture.telemetry.v1")
            ):
                raise AssertionError("a symlink must not be yielded")
        _assert_artifact_unverified(linked)

    hardlink_lab = _two_resource_lab(
        tmp_path / "hardlink", catalog_mapping, fixture_bytes
    )
    hardlink_artifact = tmp_path / "hardlink" / "artifacts" / "fixture-kpi.csv"
    hardlink_copy = tmp_path / "hardlink-copy.csv"
    try:
        os.link(hardlink_artifact, hardlink_copy)
    except OSError:
        pass
    else:
        with pytest.raises(LabError) as hardlinked:
            with hardlink_lab.open_verified_artifacts(
                ("fixture.kpi.v1", "fixture.telemetry.v1")
            ):
                raise AssertionError("a multiply linked file must not be yielded")
        _assert_artifact_unverified(hardlinked)


def test_open_verified_artifacts_detects_replacement_and_in_place_mutation(
    tmp_path, catalog_mapping, fixture_bytes, monkeypatch
) -> None:
    replaced_lab = _two_resource_lab(
        tmp_path / "replaced", catalog_mapping, fixture_bytes
    )
    replaced_path = tmp_path / "replaced" / "artifacts" / "fixture-kpi.csv"
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(fixture_bytes)

    with pytest.raises(LabError) as replaced:
        with replaced_lab.open_verified_artifacts(
            ("fixture.kpi.v1", "fixture.telemetry.v1")
        ):
            try:
                os.replace(replacement, replaced_path)
            except PermissionError:
                original_lstat = workspace_module.os.lstat

                def changed_identity(path):
                    current = original_lstat(path)
                    if Path(path) != replaced_path:
                        return current

                    class ChangedIdentity:
                        def __init__(self, metadata):
                            self._metadata = metadata
                            self.st_ino = metadata.st_ino + 1

                        def __getattr__(self, name):
                            return getattr(self._metadata, name)

                    return ChangedIdentity(current)

                monkeypatch.setattr(workspace_module.os, "lstat", changed_identity)
    _assert_artifact_unverified(replaced)

    mutated_lab = _two_resource_lab(
        tmp_path / "mutated", catalog_mapping, fixture_bytes
    )
    mutated_path = tmp_path / "mutated" / "artifacts" / "fixture-kpi.csv"
    with pytest.raises(LabError) as mutated:
        with mutated_lab.open_verified_artifacts(
            ("fixture.kpi.v1", "fixture.telemetry.v1")
        ):
            mutated_path.write_bytes(b"x" * len(fixture_bytes))
    _assert_artifact_unverified(mutated)


def test_open_verified_artifacts_closes_every_handle_when_one_close_fails(
    tmp_path, catalog_mapping, fixture_bytes, monkeypatch
) -> None:
    lab = _two_resource_lab(tmp_path, catalog_mapping, fixture_bytes)
    original_close = workspace_module.VerifiedArtifactStream._close
    close_calls: list[str] = []

    def close_with_first_failure(stream):
        close_calls.append(stream.resource_id)
        original_close(stream)
        if len(close_calls) == 1:
            raise OSError("private close detail")

    monkeypatch.setattr(
        workspace_module.VerifiedArtifactStream,
        "_close",
        close_with_first_failure,
    )

    with pytest.raises(LabError) as caught:
        with lab.open_verified_artifacts(
            ("fixture.kpi.v1", "fixture.telemetry.v1")
        ) as artifacts:
            assert all(not item.closed for item in artifacts)
    _assert_artifact_unverified(caught)
    assert close_calls == ["fixture.kpi.v1", "fixture.telemetry.v1"]
    assert all(item.closed for item in artifacts)
