from __future__ import annotations

import json
from io import StringIO

from telco_lab.catalog import FixtureCatalogProvider
from telco_lab.cli import main
from telco_lab.downloader import SecureDownloader

from test_workspace import Opener


def _invoke(args, *, provider, downloader=None):
    stdout = StringIO()
    stderr = StringIO()
    code = main(
        args,
        stdout=stdout,
        stderr=stderr,
        provider=provider,
        downloader=downloader,
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def test_catalog_is_offline_safe_json_without_download_url(
    tmp_path, catalog_mapping
) -> None:
    provider = FixtureCatalogProvider(catalog_mapping)
    code, payload, error = _invoke(
        ["--workspace", str(tmp_path), "catalog"], provider=provider
    )

    assert (code, error) == (0, None)
    assert payload["ok"] is True
    resource = payload["catalog"]["resources"][0]
    assert resource["resource_id"] == "fixture.kpi.v1"
    assert "source_url" not in resource
    assert "secret" not in json.dumps(payload)


def test_fetch_then_verify_are_stable_json_commands(
    tmp_path, catalog_mapping, fixture_bytes
) -> None:
    provider = FixtureCatalogProvider(catalog_mapping)
    downloader = SecureDownloader(opener=Opener(fixture_bytes))
    code, fetched, error = _invoke(
        [
            "--workspace",
            str(tmp_path),
            "fetch",
            "fixture.kpi.v1",
            "--accept-license",
            "CC-BY-4.0",
        ],
        provider=provider,
        downloader=downloader,
    )
    assert (code, error) == (0, None)
    assert fetched == {
        "ok": True,
        "artifact": {
            "cached": False,
            "filename": "fixture-kpi.csv",
            "resource_id": "fixture.kpi.v1",
            "sha256": catalog_mapping["resources"][0]["sha256"],
            "size_bytes": len(fixture_bytes),
        },
    }

    code, report, error = _invoke(
        ["--workspace", str(tmp_path), "verify"], provider=provider
    )
    assert (code, error) == (0, None)
    assert report["ok"] is True
    assert report["verification"]["valid"] is True


def test_cli_errors_have_codes_and_hide_paths_queries_and_attacker_values(
    tmp_path, catalog_mapping
) -> None:
    provider = FixtureCatalogProvider(catalog_mapping)
    attacker = "https://evil.test/?token=super-secret"
    code, payload, error = _invoke(
        ["--workspace", str(tmp_path), "fetch", attacker, "--accept-license", "MIT"],
        provider=provider,
    )

    assert (code, payload) == (2, None)
    encoded = json.dumps(error)
    assert error["error"]["code"] == "resource_not_found"
    assert str(tmp_path) not in encoded
    assert "super-secret" not in encoded


def test_help_uses_injected_stream_and_is_json(tmp_path, catalog_mapping, capsys) -> None:
    provider = FixtureCatalogProvider(catalog_mapping)
    code, payload, error = _invoke(["--help"], provider=provider)

    assert (code, error) == (0, None)
    assert payload["ok"] is True
    assert payload["help"]["program"] == "telco-lab"
    assert "catalog" in payload["help"]["text"]
    assert "evaluate" in payload["help"]["text"]
    assert "run" in payload["help"]["text"]
    assert capsys.readouterr() == ("", "")


def test_subcommand_help_is_also_json(catalog_mapping) -> None:
    provider = FixtureCatalogProvider(catalog_mapping)
    code, payload, error = _invoke(["--workspace", "unused", "fetch", "--help"], provider=provider)
    assert (code, error) == (0, None)
    assert payload["help"]["program"] == "telco-lab"
    assert "--accept-license" in payload["help"]["text"]
