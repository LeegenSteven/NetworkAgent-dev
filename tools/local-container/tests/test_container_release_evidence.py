from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "container_release_evidence.py"
WORKFLOW_PATH = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "telco-container.yml"
)
IMAGE_TAG = "networkagent-local:dev"
BASE_DIGEST = "b" * 64
BASE_REFERENCE = f"python:3.12-slim-bookworm@sha256:{BASE_DIGEST}"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "container_release_evidence", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _replace_tar_member(path: Path, member_name: str, replacement: bytes) -> None:
    entries: list[tuple[str, bytes]] = []
    with tarfile.open(path, mode="r:") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            assert handle is not None
            content = replacement if member.name == member_name else handle.read()
            entries.append((member.name, content))
    rewritten = path.with_suffix(".rewritten.tar")
    with tarfile.open(rewritten, mode="w") as archive:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    os.replace(rewritten, path)


def _source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "GITHUB_REPOSITORY": "LeegenSteven/NetworkAgent-dev",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_JOB": "build-inspect-smoke",
        "GITHUB_WORKFLOW": "telco-container",
        "GITHUB_SERVER_URL": "https://github.com",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _docker_archive(path: Path) -> tuple[str, list[str]]:
    layer_payloads: list[bytes] = []
    for member_name, content in (
        ("opt/networkagent/bin/container_entrypoint.py", b"safe\n"),
        ("opt/networkagent/share/input-manifest.json", b"{}\n"),
    ):
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode="w") as nested:
            member = tarfile.TarInfo(member_name)
            member.size = len(content)
            nested.addfile(member, io.BytesIO(content))
        layer_payloads.append(layer.getvalue())
    diff_ids = [
        "sha256:" + hashlib.sha256(layer).hexdigest() for layer in layer_payloads
    ]
    config = json.dumps(
        {
            "architecture": "amd64",
            "config": {"User": "10001:10001"},
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": diff_ids},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        [
            {
                "Config": f"{digest}.json",
                "RepoTags": [IMAGE_TAG],
                "Layers": ["layer/layer.tar", "layer-2/layer.tar"],
            }
        ]
    ).encode("utf-8")
    with tarfile.open(path, mode="w") as archive:
        for name, content in (
            ("manifest.json", manifest),
            (f"{digest}.json", config),
            ("layer/layer.tar", layer_payloads[0]),
            ("layer-2/layer.tar", layer_payloads[1]),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return f"sha256:{digest}", diff_ids


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, dict[str, Path | str]]:
    module = _load_module()
    _source_env(monkeypatch)
    trivy_binary_bytes = b"official-trivy-binary\n"
    module.TRIVY_LINUX_AMD64_BINARY_SHA256 = hashlib.sha256(
        trivy_binary_bytes
    ).hexdigest()
    evidence = tmp_path / "evidence"
    context = evidence / "context.json"
    assert (
        module.main(
            [
                "initialize",
                "--output",
                str(context),
                "--artifact-name",
                "telco-container-release-attempt-2",
                "--artifact-retention-days",
                "14",
            ]
        )
        == 0
    )

    archive = tmp_path / "image.tar"
    image_id, diff_ids = _docker_archive(archive)
    image_inspect = evidence / "image-inspect.json"
    _write_json(
        image_inspect,
        [
            {
                "Id": image_id,
                "Architecture": "amd64",
                "Os": "linux",
                "RepoTags": [IMAGE_TAG],
                "RepoDigests": [],
                "Config": {"User": "10001:10001"},
                "RootFS": {
                    "Type": "layers",
                    "Layers": diff_ids,
                },
            }
        ],
    )
    base_inspect = evidence / "base-image-inspect.json"
    _write_json(
        base_inspect,
        [
            {
                "Id": "sha256:" + "c" * 64,
                "Architecture": "amd64",
                "Os": "linux",
                "RepoTags": ["python:3.12-slim-bookworm"],
                "RepoDigests": [f"python@sha256:{BASE_DIGEST}"],
                "Config": {},
                "RootFS": {"Type": "layers", "Layers": [diff_ids[0]]},
            }
        ],
    )

    now = datetime.now(timezone.utc)
    updated = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    next_update = (now + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    downloaded = now.isoformat().replace("+00:00", "Z")
    version = evidence / "trivy-version.json"
    _write_json(
        version,
        {
            "Version": "0.74.0",
            "VulnerabilityDB": {
                "Version": 2,
                "UpdatedAt": updated,
                "NextUpdate": next_update,
                "DownloadedAt": downloaded,
            },
        },
    )
    db_metadata = evidence / "db-metadata.json"
    _write_json(
        db_metadata,
        {
            "Version": 2,
            "UpdatedAt": updated,
            "NextUpdate": next_update,
            "DownloadedAt": downloaded,
        },
    )
    trivy_binary = tmp_path / "trivy"
    trivy_binary.write_bytes(trivy_binary_bytes)
    trivy_db = tmp_path / "trivy-cache" / "db" / "trivy.db"
    trivy_db.parent.mkdir(parents=True)
    trivy_db.write_bytes(b"trivy-vulnerability-database\n")

    scan = evidence / "trivy-vulnerability.json"
    _write_json(
        scan,
        {
            "SchemaVersion": 2,
            "Trivy": {"Version": "0.74.0"},
            "ReportID": "report-1",
            "CreatedAt": downloaded,
            "ArtifactID": "sha256:" + "9" * 64,
            "ArtifactName": image_id,
            "ArtifactType": "container_image",
            "Metadata": {
                "ImageID": image_id,
                "DiffIDs": diff_ids,
                "RepoTags": [IMAGE_TAG],
                "Reference": image_id,
                "OS": {"Family": "debian", "Name": "12.15"},
                "ImageConfig": {"architecture": "amd64", "os": "linux"},
            },
            "Results": [
                {
                    "Target": f"{IMAGE_TAG} (debian 12.15)",
                    "Class": "os-pkgs",
                    "Type": "debian",
                    "Packages": [
                        {
                            "Name": "adduser",
                            "Version": "3.134",
                            "Identifier": {"PURL": "pkg:deb/debian/adduser@3.134"},
                            "Layer": {"DiffID": diff_ids[0]},
                            "AnalyzedBy": "dpkg",
                        }
                    ],
                    "Vulnerabilities": None,
                },
                {
                    "Target": "Python",
                    "Class": "lang-pkgs",
                    "Type": "python-pkg",
                    "Packages": [
                        {
                            "Name": "pip",
                            "Version": "25.0.1",
                            "Identifier": {"PURL": "pkg:pypi/pip@25.0.1"},
                            "Layer": {"DiffID": diff_ids[1]},
                            "AnalyzedBy": "python-pkg",
                        }
                    ],
                    "Vulnerabilities": None,
                },
            ],
        },
    )
    full_scan = evidence / "trivy-vulnerability-all.json"
    full_scan.write_bytes(scan.read_bytes())
    sbom = evidence / "sbom.cdx.json"
    _write_json(
        sbom,
        {
            "$schema": "http://cyclonedx.org/schema/bom-1.7.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "serialNumber": "urn:uuid:12345678-1234-4234-8234-123456789abc",
            "version": 1,
            "metadata": {
                "timestamp": downloaded,
                "tools": {
                    "components": [
                        {
                            "type": "application",
                            "group": "aquasecurity",
                            "name": "trivy",
                            "version": "0.74.0",
                        }
                    ]
                },
                "component": {
                    "bom-ref": "root",
                    "type": "container",
                    "name": image_id,
                    "properties": [
                        {
                            "name": "aquasecurity:trivy:ImageID",
                            "value": image_id,
                        },
                        {
                            "name": "aquasecurity:trivy:SchemaVersion",
                            "value": "2",
                        },
                        {
                            "name": "aquasecurity:trivy:DiffID",
                            "value": diff_ids[1],
                        },
                        {
                            "name": "aquasecurity:trivy:DiffID",
                            "value": diff_ids[0],
                        },
                    ],
                },
            },
            "components": [
                {
                    "bom-ref": "component-1",
                    "type": "operating-system",
                    "name": "debian",
                    "version": "12.15",
                },
                {
                    "bom-ref": "component-2",
                    "type": "library",
                    "name": "pip",
                    "version": "25.0.1",
                    "purl": "pkg:pypi/pip@25.0.1",
                },
                {
                    "bom-ref": "component-3",
                    "type": "library",
                    "name": "adduser",
                    "version": "3.134",
                    "purl": "pkg:deb/debian/adduser@3.134",
                },
            ],
            "dependencies": [
                {
                    "ref": "root",
                    "dependsOn": ["component-1", "component-2", "component-3"],
                }
            ],
        },
    )
    summary = tmp_path / "summary.md"
    manifest = evidence / "release-manifest.json"
    return module, {
        "evidence": evidence,
        "context": context,
        "archive": archive,
        "image_id": image_id,
        "diff_ids": diff_ids,
        "image_inspect": image_inspect,
        "base_inspect": base_inspect,
        "version": version,
        "db_metadata": db_metadata,
        "trivy_binary": trivy_binary,
        "trivy_db": trivy_db,
        "scan": scan,
        "full_scan": full_scan,
        "sbom": sbom,
        "summary": summary,
        "manifest": manifest,
    }


def _manifest_args(
    paths: dict[str, Path | str], command: str = "manifest"
) -> list[str]:
    args = [
        command,
        "--evidence-root",
        str(paths["evidence"]),
        "--context",
        str(paths["context"]),
        "--image-inspect",
        str(paths["image_inspect"]),
        "--base-image-inspect",
        str(paths["base_inspect"]),
        "--image-archive",
        str(paths["archive"]),
        "--trivy-version",
        str(paths["version"]),
        "--trivy-binary",
        str(paths["trivy_binary"]),
        "--trivy-db",
        str(paths["trivy_db"]),
        "--db-metadata",
        str(paths["db_metadata"]),
        "--scan-report",
        str(paths["scan"]),
        "--scan-exit-code",
        "0",
        "--full-scan-report",
        str(paths["full_scan"]),
        "--full-scan-exit-code",
        "0",
        "--sbom",
        str(paths["sbom"]),
        "--sbom-exit-code",
        "0",
        "--image-reference",
        IMAGE_TAG,
        "--base-image-reference",
        BASE_REFERENCE,
        "--artifact-name",
        "telco-container-release-attempt-2",
        "--artifact-retention-days",
        "14",
    ]
    if command == "manifest":
        args.extend(
            ["--output", str(paths["manifest"]), "--summary", str(paths["summary"])]
        )
    else:
        args.extend(["--manifest", str(paths["manifest"])])
    return args


def _payload(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_initialize_binds_commit_run_job_and_pending_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    context = _payload(paths["context"])
    assert context["schema_version"] == module.CONTEXT_SCHEMA
    assert context["source"] == {
        "commit_sha": "a" * 40,
        "job": "build-inspect-smoke",
        "repository": "LeegenSteven/NetworkAgent-dev",
        "run_attempt": 2,
        "run_id": 123456,
        "run_url": "https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/123456",
        "workflow": "telco-container",
    }
    assert context["artifact"] == {
        "name": "telco-container-release-attempt-2",
        "retention_days": 14,
    }


def test_clean_evidence_is_pending_then_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    assert module.main(_manifest_args(paths)) == 0
    manifest = _payload(paths["manifest"])
    assert manifest["status"] == "PASS"
    assert manifest["classification"] == "PENDING VERIFY-MANIFEST"
    assert manifest["failures"] == []
    assert manifest["image"]["local_image_id"] == paths["image_id"]
    assert manifest["image"]["config_digest"] == paths["image_id"]
    assert manifest["image"]["rootfs_diff_ids"] == paths["diff_ids"]
    assert manifest["image"]["platform"] == {
        "architecture": "amd64",
        "os": "linux",
    }
    assert {
        key: manifest["security"][key]
        for key in (
            "critical_count",
            "gate_severities",
            "high_count",
            "scan_exit_code",
            "scanner",
            "status",
            "vulnerability_count",
            "fixable_vulnerability_count",
            "unfixed_vulnerability_count",
            "ignore_unfixed",
        )
    } == {
        "critical_count": 0,
        "gate_severities": ["CRITICAL", "HIGH"],
        "high_count": 0,
        "scan_exit_code": 0,
        "scanner": "vuln",
        "status": "PASS",
        "vulnerability_count": 0,
        "fixable_vulnerability_count": 0,
        "unfixed_vulnerability_count": 0,
        "ignore_unfixed": True,
    }
    assert manifest["security_diagnostic"]["ignore_unfixed"] is False
    assert manifest["security_diagnostic"]["vulnerability_count"] == 0
    assert manifest["sbom"]["format"] == "CycloneDX"
    assert manifest["sbom"]["spec_version"] == "1.7"
    assert manifest["boundaries"] == {
        "offline_independent_reverification": False,
        "registry_image_published": False,
        "signing_attestation_or_provenance": False,
        "trivy_database_registry_digest_or_signature": False,
    }
    assert module.main(_manifest_args(paths, "verify-manifest")) == 0
    summary = paths["summary"].read_text(encoding="utf-8")
    assert "PENDING VERIFY-MANIFEST" in summary
    assert "VERIFIED RC" not in summary
    assert "Registry image publication: **NOT PERFORMED**" in summary
    assert "Offline independent re-verification: **NOT AVAILABLE**" in summary


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_high_or_critical_vulnerability_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, severity: str
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    scan = _payload(paths["scan"])
    scan["Results"][0]["Vulnerabilities"] = [
        {
            "VulnerabilityID": "CVE-2026-1234",
            "PkgName": "unsafe",
            "InstalledVersion": "1",
            "FixedVersion": "2",
            "Severity": severity,
        }
    ]
    _write_json(paths["scan"], scan)
    full_scan = _payload(paths["full_scan"])
    full_scan["Results"][0]["Vulnerabilities"] = scan["Results"][0]["Vulnerabilities"]
    _write_json(paths["full_scan"], full_scan)
    args = _manifest_args(paths)
    args[args.index("--scan-exit-code") + 1] = "1"
    assert module.main(args) == 1
    manifest = _payload(paths["manifest"])
    assert manifest["status"] == "FAIL"
    assert manifest["classification"] == "DIAGNOSTIC ONLY"
    assert manifest["security"][f"{severity.lower()}_count"] == 1
    assert manifest["security"]["fixable_vulnerability_count"] == 1


def test_unfixed_findings_are_retained_in_full_diagnostic_but_do_not_fail_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    full_scan = _payload(paths["full_scan"])
    full_scan["Results"][0]["Vulnerabilities"] = [
        {
            "VulnerabilityID": "CVE-2026-UNFIXED",
            "PkgName": "base-package",
            "InstalledVersion": "1",
            "Severity": "CRITICAL",
        }
    ]
    _write_json(paths["full_scan"], full_scan)

    assert module.main(_manifest_args(paths)) == 0
    manifest = _payload(paths["manifest"])
    assert manifest["security"]["vulnerability_count"] == 0
    assert manifest["security"]["ignore_unfixed"] is True
    assert manifest["security_diagnostic"]["status"] == "PASS"
    assert manifest["security_diagnostic"]["vulnerability_count"] == 1
    assert manifest["security_diagnostic"]["fixable_vulnerability_count"] == 0
    assert manifest["security_diagnostic"]["unfixed_vulnerability_count"] == 1
    assert manifest["security_diagnostic"]["critical_count"] == 1
    assert module.main(_manifest_args(paths, "verify-manifest")) == 0


def test_fixable_findings_cannot_be_removed_from_gate_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    full_scan = _payload(paths["full_scan"])
    full_scan["Results"][0]["Vulnerabilities"] = [
        {
            "VulnerabilityID": "CVE-2026-FIXABLE",
            "PkgName": "base-package",
            "InstalledVersion": "1",
            "FixedVersion": "2",
            "Severity": "HIGH",
        }
    ]
    _write_json(paths["full_scan"], full_scan)

    assert module.main(_manifest_args(paths)) == 1
    assert (
        "trivy_gate_and_diagnostic_disagree" in _payload(paths["manifest"])["failures"]
    )


def test_zero_vulnerabilities_with_nonzero_scan_exit_is_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    args = _manifest_args(paths)
    args[args.index("--scan-exit-code") + 1] = "2"
    assert module.main(args) == 1
    assert "trivy_scan_command_failed" in _payload(paths["manifest"])["failures"]


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        (lambda value: value.update({"SchemaVersion": 1}), "trivy_scan_invalid"),
        (
            lambda value: value["Metadata"].update({"ImageID": "sha256:" + "f" * 64}),
            "trivy_scan_invalid",
        ),
        (
            lambda value: value.update({"ArtifactType": "filesystem"}),
            "trivy_scan_invalid",
        ),
        (
            lambda value: value.update({"ArtifactName": "unrelated"}),
            "trivy_scan_invalid",
        ),
        (
            lambda value: value["Metadata"].update({"DiffIDs": ["sha256:" + "f" * 64]}),
            "trivy_scan_invalid",
        ),
        (
            lambda value: value.update({"Results": value["Results"][:1]}),
            "trivy_scan_invalid",
        ),
        (
            lambda value: (
                value["Metadata"].update(
                    {"OS": {"Family": "unsupported", "Name": "99"}}
                ),
                value["Results"][0].update({"Type": "unsupported"}),
            ),
            "trivy_scan_invalid",
        ),
    ],
)
def test_scan_identity_or_schema_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    failure: str,
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    scan = _payload(paths["scan"])
    mutation(scan)
    _write_json(paths["scan"], scan)
    assert module.main(_manifest_args(paths)) == 1
    assert failure in _payload(paths["manifest"])["failures"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"bomFormat": "SPDX"}),
        lambda value: value.update({"specVersion": "1.6"}),
        lambda value: value.update({"components": []}),
        lambda value: value["metadata"]["component"]["properties"][0].update(
            {"value": "sha256:" + "f" * 64}
        ),
        lambda value: value["components"][0].update({"type": "not-a-type"}),
        lambda value: value["components"].append(
            {
                "bom-ref": "orphan",
                "type": "library",
                "name": "orphan",
                "version": "1",
            }
        ),
    ],
)
def test_sbom_content_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    sbom = _payload(paths["sbom"])
    mutation(sbom)
    _write_json(paths["sbom"], sbom)
    assert module.main(_manifest_args(paths)) == 1
    assert "cyclonedx_sbom_invalid" in _payload(paths["manifest"])["failures"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda properties: properties.pop(),
        lambda properties: properties[-1].update({"value": "sha256:" + "f" * 64}),
        lambda properties: properties.append(
            {
                "name": "aquasecurity:trivy:ImageID",
                "value": "sha256:" + "a" * 64,
            }
        ),
    ],
)
def test_sbom_diff_ids_and_singleton_properties_bind_exact_image_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    sbom = _payload(paths["sbom"])
    mutation(sbom["metadata"]["component"]["properties"])
    _write_json(paths["sbom"], sbom)
    assert module.main(_manifest_args(paths)) == 1
    assert "cyclonedx_sbom_invalid" in _payload(paths["manifest"])["failures"]


def test_sbom_diff_id_property_order_is_canonicalization_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    sbom = _payload(paths["sbom"])
    properties = sbom["metadata"]["component"]["properties"]
    properties[-2:] = reversed(properties[-2:])
    _write_json(paths["sbom"], sbom)
    assert module.main(_manifest_args(paths)) == 0
    assert module.main(_manifest_args(paths, "verify-manifest")) == 0


def test_scan_and_sbom_package_inventories_must_match_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    sbom = _payload(paths["sbom"])
    sbom["components"][1]["purl"] = "pkg:pypi/fabricated@999"
    sbom["components"][1]["name"] = "fabricated"
    sbom["components"][1]["version"] = "999"
    _write_json(paths["sbom"], sbom)
    assert module.main(_manifest_args(paths)) == 1
    assert "cyclonedx_sbom_invalid" in _payload(paths["manifest"])["failures"]


@pytest.mark.parametrize("extra_kind", ["file", "directory"])
def test_evidence_root_rejects_unbound_entries_before_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_kind: str,
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    extra = paths["evidence"] / "rogue.json"
    if extra_kind == "file":
        _write_json(extra, {"unbound": True})
    else:
        extra.mkdir()
        _write_json(extra / "nested.json", {"unbound": True})
    assert module.main(_manifest_args(paths)) == 2
    assert not paths["manifest"].exists()


def test_evidence_root_rejects_entries_added_after_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    assert module.main(_manifest_args(paths)) == 0
    _write_json(paths["evidence"] / "rogue.json", {"unbound": True})
    assert module.main(_manifest_args(paths, "verify-manifest")) == 1


def test_tool_version_and_database_metadata_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    version = _payload(paths["version"])
    version["Version"] = "0.74.1"
    _write_json(paths["version"], version)
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_tool_metadata_invalid" in _payload(paths["manifest"])["failures"]

    module, paths = _fixture(tmp_path / "second", monkeypatch)
    metadata = _payload(paths["db_metadata"])
    metadata["UpdatedAt"] = "2026-08-30T09:00:00Z"
    _write_json(paths["db_metadata"], metadata)
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_database_metadata_invalid" in _payload(paths["manifest"])["failures"]


def test_tool_binary_and_database_freshness_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    paths["trivy_binary"].write_bytes(b"not-the-pinned-binary\n")
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_tool_metadata_invalid" in _payload(paths["manifest"])["failures"]

    module, paths = _fixture(tmp_path / "stale", monkeypatch)
    stale_updated = datetime.now(timezone.utc) - timedelta(days=5)
    stale = {
        "Version": 2,
        "UpdatedAt": stale_updated.isoformat().replace("+00:00", "Z"),
        "NextUpdate": (stale_updated + timedelta(hours=6))
        .isoformat()
        .replace("+00:00", "Z"),
        "DownloadedAt": (stale_updated + timedelta(hours=2))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    version = _payload(paths["version"])
    version["VulnerabilityDB"] = stale
    _write_json(paths["version"], version)
    _write_json(paths["db_metadata"], stale)
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_database_metadata_invalid" in _payload(paths["manifest"])["failures"]


def test_archive_config_digest_must_match_local_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    inspect = json.loads(paths["image_inspect"].read_text(encoding="utf-8"))
    inspect[0]["Id"] = "sha256:" + "f" * 64
    _write_json(paths["image_inspect"], inspect)
    assert module.main(_manifest_args(paths)) == 1
    assert "container_image_identity_invalid" in _payload(paths["manifest"])["failures"]


def test_archive_layers_platform_and_base_prefix_bind_exact_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    _replace_tar_member(paths["archive"], "layer/layer.tar", b"tampered-layer")
    assert module.main(_manifest_args(paths)) == 1
    assert "container_image_identity_invalid" in _payload(paths["manifest"])["failures"]

    module, paths = _fixture(tmp_path / "platform", monkeypatch)
    inspect = json.loads(paths["image_inspect"].read_text(encoding="utf-8"))
    inspect[0]["Architecture"] = "arm64"
    _write_json(paths["image_inspect"], inspect)
    assert module.main(_manifest_args(paths)) == 1
    assert "container_image_identity_invalid" in _payload(paths["manifest"])["failures"]

    module, paths = _fixture(tmp_path / "base-prefix", monkeypatch)
    base = json.loads(paths["base_inspect"].read_text(encoding="utf-8"))
    base[0]["RootFS"]["Layers"] = ["sha256:" + "f" * 64]
    _write_json(paths["base_inspect"], base)
    assert module.main(_manifest_args(paths)) == 1
    assert "base_image_identity_invalid" in _payload(paths["manifest"])["failures"]


def test_base_manifest_digest_must_match_pinned_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    inspect = json.loads(paths["base_inspect"].read_text(encoding="utf-8"))
    inspect[0]["RepoDigests"] = ["python@sha256:" + "f" * 64]
    _write_json(paths["base_inspect"], inspect)
    assert module.main(_manifest_args(paths)) == 1
    assert "base_image_identity_invalid" in _payload(paths["manifest"])["failures"]


def test_duplicate_keys_and_nonfinite_json_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    paths["scan"].write_text('{"SchemaVersion":2,"SchemaVersion":2}', encoding="utf-8")
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_scan_invalid" in _payload(paths["manifest"])["failures"]

    module, paths = _fixture(tmp_path / "nan", monkeypatch)
    paths["scan"].write_text('{"value":NaN}', encoding="utf-8")
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_scan_invalid" in _payload(paths["manifest"])["failures"]

    module, paths = _fixture(tmp_path / "infinity", monkeypatch)
    paths["scan"].write_text('{"value":1e999}', encoding="utf-8")
    assert module.main(_manifest_args(paths)) == 1
    assert "trivy_scan_invalid" in _payload(paths["manifest"])["failures"]


def test_tampering_after_manifest_breaks_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    assert module.main(_manifest_args(paths)) == 0
    scan = _payload(paths["scan"])
    scan["ArtifactName"] = "tampered"
    _write_json(paths["scan"], scan)
    assert module.main(_manifest_args(paths, "verify-manifest")) == 1


def test_manifest_timestamp_cannot_be_rewritten_to_stale_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    assert module.main(_manifest_args(paths)) == 0
    manifest = _payload(paths["manifest"])
    manifest["generated_at_utc"] = "2000-01-01T00:00:00Z"
    _write_json(paths["manifest"], manifest)
    assert module.main(_manifest_args(paths, "verify-manifest")) == 1

    module, paths = _fixture(tmp_path / "non-monotonic", monkeypatch)
    assert module.main(_manifest_args(paths)) == 0
    manifest = _payload(paths["manifest"])
    manifest["generated_at_utc"] = (
        (datetime.now(timezone.utc) - timedelta(minutes=90))
        .isoformat()
        .replace("+00:00", "Z")
    )
    _write_json(paths["manifest"], manifest)
    assert module.main(_manifest_args(paths, "verify-manifest")) == 1


def test_manifest_source_context_cannot_be_replayed_for_another_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    context = _payload(paths["context"])
    context["source"]["job"] = "compose-policy"
    _write_json(paths["context"], context)
    assert module.main(_manifest_args(paths)) == 1
    assert "source_context_invalid" in _payload(paths["manifest"])["failures"]


def test_symlinked_evidence_input_is_rejected_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, paths = _fixture(tmp_path, monkeypatch)
    target = tmp_path / "real-scan.json"
    target.write_bytes(paths["scan"].read_bytes())
    paths["scan"].unlink()
    try:
        os.symlink(target, paths["scan"])
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    assert module.main(_manifest_args(paths)) == 2
    assert not paths["manifest"].exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "container release evidence error: "
        "evidence root entries must be regular files\n"
    )


def test_workflow_pins_actions_trivy_database_and_always_uploads() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    for reference in __import__("re").findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow):
        assert __import__("re").fullmatch(r"[0-9a-f]{40}", reference)
    assert (
        "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567" in workflow
    )
    assert 'version: "v0.74.0"' in workflow
    assert "ghcr.io/aquasecurity/trivy-db:2" in workflow
    assert "trivy-db:latest" not in workflow
    assert "version: latest" not in workflow
    assert "retention-days: 14" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "container-release-evidence/*.json" not in workflow
    assert workflow.count("--ignore-unfixed") == 1
    assert workflow.count("--full-scan-report") == 3
    assert workflow.count("--full-scan-exit-code") == 3
    assert '--output "$evidence_root/trivy-vulnerability-all.json"' in workflow
    for name in (
        "context.json",
        "image-inspect.json",
        "base-image-inspect.json",
        "trivy-version.json",
        "db-metadata.json",
        "trivy-vulnerability.json",
        "trivy-vulnerability-all.json",
        "sbom.cdx.json",
        "release-manifest.json",
    ):
        assert f"container-release-evidence/{name}" in workflow
    assert 'classification="VERIFIED RUNNER-LOCAL EVIDENCE"' in workflow
    assert 'classification="VERIFIED RC"' not in workflow
    assert "Offline independent re-verification: **NOT AVAILABLE**" in workflow
    assert '[[ "$ARTIFACT_ID" =~ ^[1-9][0-9]*$ ]]' in workflow
    assert '[[ "$ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]' in workflow
    assert 'artifact_metadata="PASS"' in workflow
    assert "docker push" not in workflow
    assert "cosign" not in workflow.lower()
