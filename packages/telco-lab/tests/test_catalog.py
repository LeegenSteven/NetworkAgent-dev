from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest
from pydantic import ValidationError

from telco_lab.catalog import FixtureCatalogProvider, PackageCatalogProvider
from telco_lab.errors import LabError
from telco_lab.models import DatasetCatalog
from telco_lab.workspace import TelcoLab


def test_fixture_provider_returns_a_strict_immutable_catalog(catalog_mapping) -> None:
    provider = FixtureCatalogProvider(catalog_mapping)
    catalog = provider.load()

    assert catalog.schema_version == "1.0"
    assert catalog.resources[0].resource_id == "fixture.kpi.v1"
    assert isinstance(catalog.resources, tuple)
    assert provider.load() is catalog


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_url", "http://datasets.example.test/file.csv"),
        ("source_url", "https://user:password@datasets.example.test/file.csv"),
        ("filename", "../escape.csv"),
        ("filename", "subdir/file.csv"),
        ("sha256", "not-a-digest"),
    ],
)
def test_catalog_rejects_unsafe_resource_fields(catalog_mapping, field, value) -> None:
    catalog_mapping["resources"][0][field] = value
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


def test_catalog_rejects_unknown_schema_and_duplicate_targets(catalog_mapping) -> None:
    unknown = json.loads(json.dumps(catalog_mapping))
    unknown["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(unknown)

    duplicate = json.loads(json.dumps(catalog_mapping))
    duplicate["resources"].append(dict(duplicate["resources"][0]))
    duplicate["resources"][1]["resource_id"] = "fixture.second.v1"
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(duplicate)

    missing = json.loads(json.dumps(catalog_mapping))
    del missing["schema_version"]
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(missing)


@pytest.mark.parametrize(
    "version",
    ["01.0.0", "1.0.0-..", "1.0.0-01", "1.0", "v1.0.0"],
)
def test_catalog_rejects_non_semver_catalog_versions(catalog_mapping, version) -> None:
    catalog_mapping["catalog_version"] = version
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


def test_catalog_accepts_full_semver_prerelease_and_build(catalog_mapping) -> None:
    catalog_mapping["catalog_version"] = "1.0.0-rc.1+build.2"
    assert (
        DatasetCatalog.model_validate(catalog_mapping).catalog_version
        == "1.0.0-rc.1+build.2"
    )


@pytest.mark.parametrize("value", ["1", 1.0, True])
def test_catalog_does_not_coerce_resource_size_types(catalog_mapping, value) -> None:
    catalog_mapping["resources"][0]["size_bytes"] = value
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


def test_catalog_does_not_coerce_license_acceptance_type(catalog_mapping) -> None:
    catalog_mapping["resources"][0]["license"]["acceptance_required"] = 1
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


def test_catalog_requires_source_host_to_be_allowlisted(catalog_mapping) -> None:
    catalog_mapping["resources"][0]["allowed_hosts"] = ["other.example.test"]
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


def test_catalog_rejects_query_material_in_persisted_license_evidence(
    catalog_mapping,
) -> None:
    catalog_mapping["resources"][0]["license"]["evidence_url"] = (
        "https://datasets.example.test/LICENSE?token=secret"
    )
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


@pytest.mark.parametrize(
    "host",
    ["127.1", "2130706433", "0x7f000001", "0177.0.0.1"],
)
def test_catalog_rejects_legacy_ipv4_loopback_notation(catalog_mapping, host) -> None:
    catalog_mapping["resources"][0]["source_url"] = f"https://{host}/file.csv"
    catalog_mapping["resources"][0]["allowed_hosts"] = [host]
    with pytest.raises(ValidationError):
        DatasetCatalog.model_validate(catalog_mapping)


def test_package_catalog_pins_audited_bubbleran_resources() -> None:
    catalog = PackageCatalogProvider().load()

    assert catalog.catalog_id == "networkagent-open-data"
    assert len(catalog.resources) == 3
    assert {item.license.id for item in catalog.resources} == {"CC-BY-SA-4.0"}
    assert {item.allowed_hosts for item in catalog.resources} == {
        ("raw.githubusercontent.com",)
    }
    assert all(
        item.dataset_version == "fa4e3333855d64474e710bc5bebf11a9ec075e0b"
        for item in catalog.resources
    )
    for resource in catalog.resources:
        assert resource.license.attribution == (
            "BubbleRAN Open Telco Datasets contributors"
        )
        assert resource.license.evidence_sha256 == (
            "a25b2415e77fbec63d46ddf10c638218cffdcf63875386c59e766f4fba59897a"
        )
        assert resource.license.reviewed_at.isoformat() == "2026-08-30"
        assert resource.license.evidence_url.endswith(
            "/fa4e3333855d64474e710bc5bebf11a9ec075e0b/LICENSE"
        )
    assert {
        item.resource_id: (item.size_bytes, item.sha256)
        for item in catalog.resources
    } == {
        "bubbleran.persistent-interference.clean.v1": (
            2643723,
            "c6ea5a850953af5b7a1557d7cb7a727a9d38b33e3854826373b70defcaad37ba",
        ),
        "bubbleran.persistent-interference.anomalous.v1": (
            1203488,
            "e19871d6b8f38a0091472a66a15047b814df6a9ef61c37bc55cce0a2cbf6757c",
        ),
        "bubbleran.persistent-interference.alerts.v1": (
            74529,
            "5e0970c81ec855f9c837e5757ff3efdb12de54cebf4614111bec38bb752ebdc6",
        ),
    }


def test_provider_catalog_and_lab_construction_have_no_network_side_effect(
    monkeypatch, tmp_path: Path
) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("catalog inspection must stay offline")

    monkeypatch.setattr(urllib.request, "build_opener", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)

    workspace = tmp_path / "not-created-by-construction"
    provider = PackageCatalogProvider()
    lab = TelcoLab(provider, workspace)
    assert len(lab.catalog().resources) == 3
    assert not workspace.exists()


def test_package_catalog_rejects_duplicate_json_keys(monkeypatch) -> None:
    class FakeCatalogFile:
        def joinpath(self, *_parts):
            return self

        def open(self, _mode):
            from io import BytesIO

            return BytesIO(b'{"schema_version":"1.0","schema_version":"9.9"}')

    monkeypatch.setattr("telco_lab.catalog.resources.files", lambda _package: FakeCatalogFile())

    with pytest.raises(LabError) as caught:
        PackageCatalogProvider().load()
    assert caught.value.code == "catalog_unavailable"
