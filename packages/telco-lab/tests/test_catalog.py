from __future__ import annotations

import json
import urllib.request
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from telco_lab.catalog import FixtureCatalogProvider, PackageCatalogProvider
from telco_lab.cli import main as cli_main
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
    catalog_mapping["resources"][0]["license"][
        "evidence_url"
    ] = "https://datasets.example.test/LICENSE?token=secret"
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
    resources = tuple(
        item
        for item in catalog.resources
        if item.dataset_id == "bubbleran-persistent-interference"
    )

    assert catalog.catalog_id == "networkagent-open-data"
    assert len(resources) == 3
    assert {item.license.id for item in resources} == {"CC-BY-SA-4.0"}
    assert {item.allowed_hosts for item in resources} == {
        ("raw.githubusercontent.com",)
    }
    assert all(
        item.dataset_version == "fa4e3333855d64474e710bc5bebf11a9ec075e0b"
        for item in resources
    )
    for resource in resources:
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
    assert {item.resource_id: (item.size_bytes, item.sha256) for item in resources} == {
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


def test_package_catalog_pins_audited_rcaeval_opaque_slice() -> None:
    revision = "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e"
    expected = {
        "cases.parquet": (
            29500,
            "c49a288920dbba2e8e724679a14636d5c7eb2b45426bba14007ef79a6c0ab1bb",
        ),
        "re2ob_checkoutservice_cpu_1/metrics.parquet": (
            157212,
            "f46b3d354b234e37c424f54a005329b61aac83d15dcdd46b5642e55f390ab25a",
        ),
        "re2ob_checkoutservice_cpu_1/logs.parquet": (
            336876,
            "e1a25e50c8b0beae4b2886df54d86d2f28877d0b611fed98411e9c0b0c77dad3",
        ),
        "re2ob_checkoutservice_cpu_1/traces.parquet": (
            10146857,
            "56ca8f6dbeb76fab2d33faeca54bce8f40e9c5491ebd76404ebb2dfd83409367",
        ),
        "re2ob_currencyservice_cpu_1/metrics.parquet": (
            159637,
            "ed8471d2f53c0c3f4095b25eff69d5c45762425afaa973d4dbdc04d7bc32c0f3",
        ),
        "re2ob_currencyservice_cpu_1/logs.parquet": (
            342888,
            "4fd881f7620902c141a7cfa2cd4c6c8115ab2d85c6c0a7fd1914a8dcf9a0c1ba",
        ),
        "re2ob_currencyservice_cpu_1/traces.parquet": (
            10214448,
            "04d1512a44ece85f370caeaa09d24e441adc7f67bbebc0de689faea0d5563a63",
        ),
        "re2ob_emailservice_cpu_1/metrics.parquet": (
            156391,
            "52752452d9f2fdb69cec2b7575b617f84419131338fcb1c2e1829ef5c242e586",
        ),
        "re2ob_emailservice_cpu_1/logs.parquet": (
            337106,
            "5b0eff42c36df03f4b618f79fea5e0e6f95d54e9afe0e857c86b68696bf72f05",
        ),
        "re2ob_emailservice_cpu_1/traces.parquet": (
            10155267,
            "c98764ff45836b7514480c515a4e0bfe685fb0ce15de8484eae012451b5aea6e",
        ),
        "re2ob_productcatalogservice_cpu_1/metrics.parquet": (
            143586,
            "40a32cdc027251907770a69cfff1a1f7f0b93d9188148b837a3afa1ed425f2de",
        ),
        "re2ob_productcatalogservice_cpu_1/logs.parquet": (
            340440,
            "3d9a771b2325aee6bce7b1536d7baae0fca7ba8cea8c043921fc64a123d34442",
        ),
        "re2ob_productcatalogservice_cpu_1/traces.parquet": (
            10244076,
            "fcc49515cf5f50a84000a1dfddf0d75ab5ca06e9712a7f4775bfd2c364283135",
        ),
        "re2ob_recommendationservice_cpu_1/metrics.parquet": (
            154244,
            "6695d4e58c7383a5a32f7a936f3700aa332501d4431c49003d111d3ef282b12f",
        ),
        "re2ob_recommendationservice_cpu_1/logs.parquet": (
            343682,
            "b2622c7e637a9d4b11d618cf17fde180a76cd177f1234d495de6e325f415e021",
        ),
        "re2ob_recommendationservice_cpu_1/traces.parquet": (
            10171322,
            "59a3f6b063779d3088d8eea40bbfcf24c79cbde1fe6a6bb3af326ad0bff793b0",
        ),
    }

    catalog = PackageCatalogProvider().load()
    resources = tuple(
        item
        for item in catalog.resources
        if item.dataset_id == "rcaeval-re2ob-evaluation-slice"
    )
    prefix = (
        "https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/" f"{revision}/"
    )

    assert catalog.catalog_version == "1.1.0"
    assert len(resources) == 16
    assert sum(item.size_bytes for item in resources) == 53_433_532
    assert sum(item.source_url.endswith("/cases.parquet") for item in resources) == 1
    assert (
        sum(not item.source_url.endswith("/cases.parquet") for item in resources) == 15
    )
    assert {
        item.source_url.removeprefix(prefix): (item.size_bytes, item.sha256)
        for item in resources
    } == expected
    assert len({item.filename for item in resources}) == 16
    assert all(item.dataset_version == revision for item in resources)
    assert all(item.source_url.startswith(prefix) for item in resources)
    assert all(
        item.media_type == "application/vnd.apache.parquet" for item in resources
    )
    assert resources[0].adapter == "rcaeval_case_index_v1"
    assert (
        tuple(item.adapter for item in resources[1:])
        == (
            "rcaeval_metrics_v1",
            "rcaeval_logs_v1",
            "rcaeval_traces_v1",
        )
        * 5
    )
    assert all(
        item.allowed_hosts == ("huggingface.co", "us.aws.cdn.hf.co")
        for item in resources
    )
    for resource in resources:
        assert resource.license.id == "MIT"
        assert resource.license.attribution == "RCAEval dataset contributors"
        assert resource.license.evidence_url == f"{prefix}README.md"
        assert resource.license.evidence_sha256 == (
            "c2990bbe2e040a8d2f55fdd47c4f47f02223d8ea098e5d6e8851585a64956a0f"
        )
        assert resource.license.reviewed_at.isoformat() == "2026-08-31"


def test_rcaeval_public_catalog_uses_fixed_opaque_slots_in_source_order(
    tmp_path: Path,
) -> None:
    catalog = PackageCatalogProvider().load()
    resources = tuple(
        item
        for item in catalog.resources
        if item.dataset_id == "rcaeval-re2ob-evaluation-slice"
    )
    expected = (
        (
            "rcaeval.re2ob.index.v1",
            "rcaeval-re2ob-index.parquet",
            "rcaeval_case_index_v1",
            "cases.parquet",
        ),
        *tuple(
            (
                f"rcaeval.re2ob.slot-{slot:02d}.{kind}.v1",
                f"rcaeval-re2ob-slot-{slot:02d}-{kind}.parquet",
                f"rcaeval_{kind}_v1",
                f"re2ob_{service}_cpu_1/{kind}.parquet",
            )
            for slot, service in enumerate(
                (
                    "checkoutservice",
                    "currencyservice",
                    "emailservice",
                    "productcatalogservice",
                    "recommendationservice",
                ),
                start=1,
            )
            for kind in ("metrics", "logs", "traces")
        ),
    )
    prefix = (
        "https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/"
        "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e/"
    )

    assert (
        tuple(
            (
                item.resource_id,
                item.filename,
                item.adapter,
                item.source_url.removeprefix(prefix),
            )
            for item in resources
        )
        == expected
    )

    stdout = StringIO()
    stderr = StringIO()
    assert (
        cli_main(
            ["--workspace", str(tmp_path / "unused"), "catalog"],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stderr.getvalue() == ""
    payload = json.loads(stdout.getvalue())["catalog"]
    public_resources = tuple(
        item
        for item in payload["resources"]
        if item["dataset_id"] == "rcaeval-re2ob-evaluation-slice"
    )
    public_text = json.dumps(public_resources, sort_keys=True).casefold()
    for prohibited in (
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "productcatalogservice",
        "recommendationservice",
        "_cpu_",
        ".cpu.",
    ):
        assert prohibited not in public_text


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
    assert len(lab.catalog().resources) == 19
    assert not workspace.exists()


def test_package_catalog_rejects_duplicate_json_keys(monkeypatch) -> None:
    class FakeCatalogFile:
        def joinpath(self, *_parts):
            return self

        def open(self, _mode):
            from io import BytesIO

            return BytesIO(b'{"schema_version":"1.0","schema_version":"9.9"}')

    monkeypatch.setattr(
        "telco_lab.catalog.resources.files", lambda _package: FakeCatalogFile()
    )

    with pytest.raises(LabError) as caught:
        PackageCatalogProvider().load()
    assert caught.value.code == "catalog_unavailable"
