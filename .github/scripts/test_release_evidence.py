#!/usr/bin/env python3
"""Fail-closed self-tests for the release-evidence boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

import release_evidence


_TOOLS = {
    "build": "1.3.0",
    "pip": "26.0.1",
    "setuptools": "82.0.1",
    "wheel": "0.46.3",
    "pip-audit": "2.10.1",
}


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self._original_cwd = Path.cwd()
        os.chdir(self.root)
        self._environment = patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": "example/networkagent",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_SERVER_URL": "https://github.example",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_REF": "refs/heads/test",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_WORKFLOW": "release-evidence-self-test",
                "GITHUB_JOB": "test",
                "GITHUB_RUN_ATTEMPT": "1",
                "RUNNER_OS": "self-test",
                "RUNNER_ARCH": "self-test",
                "SOURCE_DATE_EPOCH": "946684800",
            },
            clear=False,
        )
        self._environment.start()
        self._tools = patch.object(
            release_evidence, "_installed_tool_versions", return_value=_TOOLS
        )
        self._tools.start()

    def tearDown(self) -> None:
        self._tools.stop()
        self._environment.stop()
        os.chdir(self._original_cwd)
        self._temporary.cleanup()

    def _wheel(
        self,
        name: str = "demo",
        version: str = "1.0.0",
        *,
        extra_members: dict[str, bytes] | None = None,
        root: Path | None = None,
    ) -> Path:
        wheel_root = (root or self.root) / "dist"
        wheel_root.mkdir(parents=True, exist_ok=True)
        normalized = name.replace("-", "_")
        path = wheel_root / f"{normalized}-{version}-py3-none-any.whl"
        members = {
            f"{normalized}/__init__.py": b"__version__ = '1.0.0'\n",
            f"{normalized}-{version}.dist-info/METADATA": (
                f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"
            ).encode(),
            f"{normalized}-{version}.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\nGenerator: self-test\n"
                b"Root-Is-Purelib: true\nTag: py3-none-any\n"
            ),
            f"{normalized}-{version}.dist-info/RECORD": b"",
        }
        members.update(extra_members or {})
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def _fixture(self, label: str | None = None) -> tuple[Path, argparse.Namespace]:
        root = self.root if label is None else self.root / label
        root.mkdir(parents=True, exist_ok=True)
        wheel = self._wheel(root=root)
        evidence = root / "release-evidence"
        evidence.mkdir()
        release_evidence._write_json(
            evidence / "pip-audit.json",
            {
                "dependencies": [
                    {"name": "third-party", "version": "2.0.0", "vulns": []}
                ],
                "fixes": [],
            },
        )
        first_party = release_evidence._wheel_component(wheel)
        release_evidence._write_json(
            evidence / "sbom.cdx.json",
            {
                "bomFormat": "CycloneDX",
                "specVersion": release_evidence.CYCLONEDX_SPEC_VERSION,
                "version": 1,
                "metadata": {
                    "component": release_evidence._release_metadata_component(
                        release_evidence._source_metadata()
                    )
                },
                "components": [
                    {
                        "bom-ref": "pkg:pypi/third-party@2.0.0",
                        "type": "library",
                        "name": "third-party",
                        "version": "2.0.0",
                        "purl": "pkg:pypi/third-party@2.0.0",
                    },
                    first_party,
                ],
                "dependencies": [{"ref": first_party["bom-ref"]}],
            },
        )
        release_evidence._write_json(
            evidence / "pip-audit-sbom.cdx.json",
            {
                "bomFormat": "CycloneDX",
                "specVersion": release_evidence.CYCLONEDX_SPEC_VERSION,
                "version": 1,
                "components": [],
                "dependencies": [],
            },
        )
        requirements = evidence / "runtime-requirements.txt"
        requirements.write_text("third-party==2.0.0\n", encoding="utf-8", newline="\n")
        release_evidence._write_json(
            evidence / "runtime-inventory.json",
            {
                "schema_version": release_evidence.RUNTIME_INVENTORY_SCHEMA,
                "status": "PASS",
                "first_party": ["demo"],
                "package_count": 2,
                "runtime_dependency_count": 1,
                "requirements": release_evidence._file_record(
                    requirements, base=evidence.resolve()
                ),
                "packages": [
                    {
                        "name": "demo",
                        "normalized_name": "demo",
                        "version": "1.0.0",
                        "scope": "first-party",
                        "license_expression": None,
                        "license": "Apache-2.0",
                    },
                    {
                        "name": "third-party",
                        "normalized_name": "third-party",
                        "version": "2.0.0",
                        "scope": "runtime",
                        "license_expression": None,
                        "license": "Apache-2.0",
                    },
                ],
            },
        )
        scan_status = release_evidence.scan_wheels(
            argparse.Namespace(
                wheel_root=root / "dist",
                output=evidence / "wheel-content-scan.json",
                max_wheel_bytes=release_evidence.DEFAULT_MAX_WHEEL_BYTES,
                max_uncompressed_bytes=(
                    release_evidence.DEFAULT_MAX_UNCOMPRESSED_BYTES
                ),
            )
        )
        self.assertEqual(scan_status, 0)
        args = argparse.Namespace(
            wheel_root=root / "dist",
            evidence_root=evidence,
            output=evidence / "release-manifest.json",
            audit_report=evidence / "pip-audit.json",
            audit_exit_code=0,
            expected_pip_audit_version="2.10.1",
            sbom=evidence / "sbom.cdx.json",
            sbom_exit_code=0,
            wheel_scan=evidence / "wheel-content-scan.json",
            expected_wheel=["demo-*.whl"],
            expected_component=["demo"],
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
            artifact_name="demo-release",
            artifact_retention_days=14,
            summary=root / "summary.md",
        )
        return wheel, args

    def _verify_args(self, args: argparse.Namespace) -> argparse.Namespace:
        return argparse.Namespace(
            manifest=args.output,
            wheel_root=args.wheel_root,
            evidence_root=args.evidence_root,
            expected_wheel=args.expected_wheel,
            expected_component=args.expected_component,
            audit_exit_code=args.audit_exit_code,
            sbom_exit_code=args.sbom_exit_code,
            expected_pip_audit_version=args.expected_pip_audit_version,
            python_version=args.python_version,
            artifact_name=args.artifact_name,
            artifact_retention_days=args.artifact_retention_days,
            supplemental_evidence=getattr(args, "supplemental_evidence", []),
        )

    def test_positive_manifest_and_verification_pass(self) -> None:
        _, args = self._fixture()
        self.assertEqual(release_evidence.build_manifest(args), 0)
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        self.assertNotIn("supplemental_evidence", payload)
        self.assertIn(
            "Artifact classification: **PENDING VERIFY-MANIFEST**",
            args.summary.read_text(encoding="utf-8"),
        )
        self.assertEqual(release_evidence.verify_manifest(self._verify_args(args)), 0)

    def test_supplemental_evidence_is_hashed_and_reverified(self) -> None:
        _, args = self._fixture("supplemental")
        supplemental = args.evidence_root / "defense-demo-summary.json"
        release_evidence._write_json(
            supplemental,
            {"schema": "networkagent-native-defense-demo/1.0", "ok": True},
        )
        args.supplemental_evidence = [supplemental]

        self.assertEqual(release_evidence.build_manifest(args), 0)
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        matching = [
            item
            for item in payload["files"]
            if item["path"].endswith("defense-demo-summary.json")
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0],
            release_evidence._file_record(supplemental, base=self.root),
        )
        verify_args = self._verify_args(args)
        self.assertEqual(release_evidence.verify_manifest(verify_args), 0)

        without_supplemental = self._verify_args(args)
        without_supplemental.supplemental_evidence = []
        self.assertEqual(release_evidence.verify_manifest(without_supplemental), 1)

        payload["supplemental_evidence"] = []
        release_evidence._write_json(args.output, payload)
        self.assertEqual(release_evidence.verify_manifest(verify_args), 1)
        self.assertEqual(release_evidence.build_manifest(args), 0)

        supplemental.write_text('{"ok":false}\n', encoding="utf-8", newline="\n")
        self.assertEqual(release_evidence.verify_manifest(verify_args), 1)

        supplemental.unlink()
        self.assertEqual(release_evidence.verify_manifest(verify_args), 1)

    def test_supplemental_evidence_boundary_rejects_unsafe_inputs(self) -> None:
        _, args = self._fixture("supplemental-boundary")
        valid = args.evidence_root / "defense-demo-summary.json"
        valid.write_text("{}\n", encoding="utf-8", newline="\n")
        outside = args.evidence_root.parent / "outside.json"
        outside.write_text("{}\n", encoding="utf-8", newline="\n")

        invalid_sets = (
            [outside],
            [args.evidence_root / ".." / "outside.json"],
            [args.evidence_root / "pip-audit.json"],
            [args.output],
            [valid, valid],
            [args.evidence_root / ".hidden.json"],
            [args.evidence_root / "nested" / "evidence.json"],
        )
        for supplementals in invalid_sets:
            with self.subTest(supplementals=supplementals):
                args.supplemental_evidence = supplementals
                self.assertEqual(release_evidence.build_manifest(args), 2)

    def test_supplemental_evidence_rejects_links_missing_and_unlisted_files(
        self,
    ) -> None:
        _, args = self._fixture("supplemental-entries")
        supplemental = args.evidence_root / "defense-demo-summary.json"
        args.supplemental_evidence = [supplemental]

        self.assertEqual(release_evidence.build_manifest(args), 2)
        supplemental.write_text("{}\n", encoding="utf-8", newline="\n")
        (args.evidence_root / "unlisted.json").write_text(
            "{}\n", encoding="utf-8", newline="\n"
        )
        self.assertEqual(release_evidence.build_manifest(args), 2)

        (args.evidence_root / "unlisted.json").unlink()
        target = args.evidence_root.parent / "target.json"
        target.write_text("{}\n", encoding="utf-8", newline="\n")
        supplemental.unlink()
        try:
            supplemental.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation is unavailable on this platform")
        self.assertEqual(release_evidence.build_manifest(args), 2)

    def test_inventory_and_sbom_finalization_pass(self) -> None:
        wheel = self._wheel()
        runtime = self.root / "runtime"
        for name, version in (("demo", "1.0.0"), ("third-party", "2.0.0")):
            metadata = runtime / f"{name.replace('-', '_')}-{version}.dist-info"
            metadata.mkdir(parents=True)
            (metadata / "METADATA").write_text(
                f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n",
                encoding="utf-8",
                newline="\n",
            )
        evidence = self.root / "release-evidence"
        inventory = evidence / "runtime-inventory.json"
        requirements = evidence / "runtime-requirements.txt"
        self.assertEqual(
            release_evidence.build_runtime_inventory(
                argparse.Namespace(
                    environment_path=runtime.relative_to(self.root),
                    first_party=["demo"],
                    output=inventory.relative_to(self.root),
                    requirements_output=requirements.relative_to(self.root),
                )
            ),
            0,
        )
        self.assertEqual(
            requirements.read_text(encoding="utf-8"), "third-party==2.0.0\n"
        )

        raw_sbom = evidence / "pip-audit-sbom.cdx.json"
        final_sbom = evidence / "sbom.cdx.json"
        release_evidence._write_json(
            raw_sbom,
            {
                "bomFormat": "CycloneDX",
                "specVersion": release_evidence.CYCLONEDX_SPEC_VERSION,
                "version": 1,
                "components": [
                    {
                        "bom-ref": "BomRef.runtime-third-party",
                        "type": "library",
                        "name": "third-party",
                        "version": "2.0.0",
                    }
                ],
                "dependencies": [
                    {"ref": "BomRef.runtime-third-party", "dependsOn": []}
                ],
            },
        )
        self.assertEqual(
            release_evidence.finalize_sbom(
                argparse.Namespace(
                    input=raw_sbom,
                    output=final_sbom,
                    wheel_root=self.root / "dist",
                    expected_wheel=["demo-*.whl"],
                    runtime_inventory=inventory,
                    runtime_requirements=requirements,
                )
            ),
            0,
        )
        payload = json.loads(final_sbom.read_text(encoding="utf-8"))
        component = next(
            item for item in payload["components"] if item["name"] == "demo"
        )
        self.assertEqual(
            component["hashes"][0]["content"], release_evidence._sha256(wheel)
        )
        runtime_component = next(
            item for item in payload["components"] if item["name"] == "third-party"
        )
        self.assertEqual(runtime_component["bom-ref"], "pkg:pypi/third-party@2.0.0")
        self.assertEqual(runtime_component["purl"], "pkg:pypi/third-party@2.0.0")
        runtime_dependency = next(
            item
            for item in payload["dependencies"]
            if item["ref"] == "pkg:pypi/third-party@2.0.0"
        )
        self.assertEqual(runtime_dependency["dependsOn"], [])

    def test_missing_multiple_and_unexpected_wheels_fail(self) -> None:
        _, args = self._fixture()
        args.expected_wheel = ["missing-*.whl"]
        self.assertEqual(release_evidence.build_manifest(args), 2)

        self._wheel(name="demo-extra")
        args.expected_wheel = ["demo*.whl"]
        self.assertEqual(release_evidence.build_manifest(args), 2)

        args.expected_wheel = ["demo-*.whl"]
        self.assertEqual(release_evidence.build_manifest(args), 2)

    def test_unuploaded_or_missing_evidence_file_fails(self) -> None:
        _, extra_args = self._fixture("extra-evidence")
        (extra_args.evidence_root / "not-uploaded.pem").write_text(
            "not a release artifact\n", encoding="utf-8"
        )
        self.assertEqual(release_evidence.build_manifest(extra_args), 2)

        _, missing_args = self._fixture("missing-evidence")
        (missing_args.evidence_root / "pip-audit-sbom.cdx.json").unlink()
        self.assertEqual(release_evidence.build_manifest(missing_args), 2)

    def test_tampered_audit_exit_writes_failing_manifest(self) -> None:
        _, args = self._fixture()
        args.audit_exit_code = 9
        self.assertEqual(release_evidence.build_manifest(args), 1)
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "FAIL")
        self.assertIn("pip_audit_failed", payload["failures"])

    def test_vulnerable_sbom_generation_exit_writes_failing_manifest(self) -> None:
        _, args = self._fixture()
        args.sbom_exit_code = 1
        self.assertEqual(release_evidence.build_manifest(args), 1)
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["sbom"]["generation_exit_code"], 1)
        self.assertIn("sbom_generation_failed", payload["failures"])
        summary = args.summary.read_text(encoding="utf-8")
        self.assertIn("Release Gate: **FAIL**", summary)
        self.assertIn("Artifact classification: **DIAGNOSTIC ONLY**", summary)

    def test_nested_failed_security_evidence_cannot_verify_as_pass(self) -> None:
        for evidence_kind in ("audit", "sbom"):
            with self.subTest(evidence_kind=evidence_kind):
                _, args = self._fixture(f"nested-{evidence_kind}")
                self.assertEqual(release_evidence.build_manifest(args), 0)
                payload = json.loads(args.output.read_text(encoding="utf-8"))
                if evidence_kind == "audit":
                    payload["security"]["pip_audit"] = release_evidence._audit_summary(
                        args.audit_report,
                        9,
                        "2.10.1",
                        "2.10.1",
                        {"third-party": "2.0.0"},
                    )
                else:
                    payload["sbom"] = release_evidence._sbom_summary(
                        args.sbom,
                        1,
                        args.expected_component,
                        (next(args.wheel_root.glob("*.whl")),),
                        {"third-party": "2.0.0"},
                    )
                release_evidence._write_json(args.output, payload)
                self.assertEqual(
                    release_evidence.verify_manifest(self._verify_args(args)),
                    1,
                )

    def test_inventory_missing_package_fake_count_and_requirements_drift_fail(
        self,
    ) -> None:
        for mutation in (
            "duplicate-package",
            "missing-package",
            "fake-count",
            "first-party-version",
            "requirements-drift",
        ):
            with self.subTest(mutation=mutation):
                _, args = self._fixture(mutation)
                inventory_path = args.evidence_root / "runtime-inventory.json"
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
                if mutation == "duplicate-package":
                    inventory["packages"].append(dict(inventory["packages"][1]))
                    inventory["package_count"] += 1
                    inventory["runtime_dependency_count"] += 1
                elif mutation == "missing-package":
                    inventory["packages"] = inventory["packages"][:1]
                elif mutation == "fake-count":
                    inventory["package_count"] = 99
                    inventory["runtime_dependency_count"] = 99
                elif mutation == "first-party-version":
                    inventory["packages"][0]["version"] = "9.9.9"
                else:
                    (args.evidence_root / "runtime-requirements.txt").write_text(
                        "third-party==9.9.9\n", encoding="utf-8", newline="\n"
                    )
                release_evidence._write_json(inventory_path, inventory)
                self.assertEqual(release_evidence.build_manifest(args), 1)
                manifest = json.loads(args.output.read_text(encoding="utf-8"))
                self.assertIn("runtime_inventory_failed", manifest["failures"])

    def test_audit_and_sbom_name_version_drift_fail(self) -> None:
        for artifact in (
            "audit-missing",
            "audit-version",
            "audit-fixes",
            "sbom-extra",
            "sbom-version",
        ):
            with self.subTest(artifact=artifact):
                _, args = self._fixture(artifact)
                if artifact.startswith("audit"):
                    report = json.loads(args.audit_report.read_text(encoding="utf-8"))
                    if artifact == "audit-missing":
                        report["dependencies"] = []
                    elif artifact == "audit-fixes":
                        report["fixes"] = [{"name": "third-party"}]
                    else:
                        report["dependencies"][0]["version"] = "9.9.9"
                    release_evidence._write_json(args.audit_report, report)
                    expected_failure = "pip_audit_failed"
                else:
                    sbom = json.loads(args.sbom.read_text(encoding="utf-8"))
                    if artifact == "sbom-extra":
                        sbom["components"].append(
                            {
                                "bom-ref": "pkg:pypi/unexpected@1.0.0",
                                "type": "library",
                                "name": "unexpected",
                                "version": "1.0.0",
                            }
                        )
                    else:
                        third_party = next(
                            component
                            for component in sbom["components"]
                            if component["name"] == "third-party"
                        )
                        third_party["version"] = "9.9.9"
                    release_evidence._write_json(args.sbom, sbom)
                    expected_failure = "sbom_generation_failed"
                self.assertEqual(release_evidence.build_manifest(args), 1)
                manifest = json.loads(args.output.read_text(encoding="utf-8"))
                self.assertIn(expected_failure, manifest["failures"])

    def test_sbom_schema_provenance_and_first_party_drift_fail(self) -> None:
        for mutation in (
            "spec-version",
            "bom-version",
            "dependencies",
            "depends-on",
            "metadata",
            "vulnerabilities",
            "runtime-type",
            "runtime-purl",
            "first-party-fields",
            "first-party-duplicate",
        ):
            with self.subTest(mutation=mutation):
                _, args = self._fixture(f"sbom-{mutation}")
                sbom = json.loads(args.sbom.read_text(encoding="utf-8"))
                if mutation == "spec-version":
                    sbom.pop("specVersion")
                elif mutation == "bom-version":
                    sbom["version"] = 0
                elif mutation == "dependencies":
                    sbom["dependencies"] = "not-a-list"
                elif mutation == "depends-on":
                    sbom["dependencies"].append(
                        {
                            "ref": sbom["components"][0]["bom-ref"],
                            "dependsOn": ["urn:missing:component"],
                        }
                    )
                elif mutation == "metadata":
                    sbom["metadata"]["component"]["version"] = "b" * 40
                elif mutation == "vulnerabilities":
                    sbom["vulnerabilities"] = [{"id": "CVE-2099-0001"}]
                elif mutation in {"runtime-type", "runtime-purl"}:
                    runtime = next(
                        component
                        for component in sbom["components"]
                        if component["name"] == "third-party"
                    )
                    if mutation == "runtime-type":
                        runtime["type"] = "application"
                    else:
                        runtime["purl"] = "pkg:pypi/completely-wrong@999"
                elif mutation == "first-party-fields":
                    first_party = next(
                        component
                        for component in sbom["components"]
                        if component["name"] == "demo"
                    )
                    first_party["properties"][1]["value"] = "other.whl"
                else:
                    sbom["components"].append(
                        {
                            "bom-ref": "pkg:generic/networkagent/demo@9.9.9",
                            "type": "library",
                            "name": "demo",
                            "version": "9.9.9",
                        }
                    )
                release_evidence._write_json(args.sbom, sbom)
                self.assertEqual(release_evidence.build_manifest(args), 1)
                manifest = json.loads(args.output.read_text(encoding="utf-8"))
                self.assertIn("sbom_generation_failed", manifest["failures"])

    def test_empty_and_wrong_sbom_fail(self) -> None:
        _, args = self._fixture()
        for components in (
            [],
            [
                {
                    "bom-ref": "pkg:pypi/wrong@1.0.0",
                    "type": "library",
                    "name": "wrong",
                    "version": "1.0.0",
                }
            ],
        ):
            with self.subTest(components=components):
                release_evidence._write_json(
                    args.sbom,
                    {
                        "bomFormat": "CycloneDX",
                        "specVersion": release_evidence.CYCLONEDX_SPEC_VERSION,
                        "version": 1,
                        "components": components,
                        "dependencies": [],
                    },
                )
                self.assertEqual(release_evidence.build_manifest(args), 1)
                payload = json.loads(args.output.read_text(encoding="utf-8"))
                self.assertIn("sbom_generation_failed", payload["failures"])

    def test_prohibited_wheel_content_fails(self) -> None:
        bad_root = self.root / "bad-dist"
        bad_root.mkdir()
        bad = self._wheel(extra_members={"demo/raw.csv": b"secret,data\n"})
        bad.replace(bad_root / bad.name)
        output = self.root / "bad-scan.json"
        status = release_evidence.scan_wheels(
            argparse.Namespace(
                wheel_root=bad_root,
                output=output,
                max_wheel_bytes=release_evidence.DEFAULT_MAX_WHEEL_BYTES,
                max_uncompressed_bytes=(
                    release_evidence.DEFAULT_MAX_UNCOMPRESSED_BYTES
                ),
            )
        )
        self.assertEqual(status, 1)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "FAIL")
        rules = {
            violation["rule"]
            for record in payload["wheels"]
            for violation in record["violations"]
        }
        self.assertIn("forbidden_file_type", rules)

    def test_large_unscanned_member_and_forged_scan_report_fail(self) -> None:
        large_root = self.root / "large-dist"
        large_root.mkdir()
        large = self._wheel(
            extra_members={
                "demo/large_secret.py": b"-----BEGIN PRIVATE KEY-----\n"
                + b"x" * release_evidence.MAX_SECRET_SCAN_MEMBER_BYTES
            }
        )
        large.replace(large_root / large.name)
        output = self.root / "large-scan.json"
        status = release_evidence.scan_wheels(
            argparse.Namespace(
                wheel_root=large_root,
                output=output,
                max_wheel_bytes=release_evidence.DEFAULT_MAX_WHEEL_BYTES,
                max_uncompressed_bytes=(
                    release_evidence.DEFAULT_MAX_UNCOMPRESSED_BYTES
                ),
            )
        )
        self.assertEqual(status, 1)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn(
            "secret_scan_size_limit_exceeded",
            {
                violation["rule"]
                for record in payload["wheels"]
                for violation in record["violations"]
            },
        )

        _, args = self._fixture("forged-wheel-scan")
        wheel = next(args.wheel_root.glob("*.whl"))
        with ZipFile(wheel, "a", compression=ZIP_DEFLATED) as archive:
            archive.writestr("demo/raw.csv", b"secret,data\n")
        forged_record = release_evidence._scan_wheel(
            wheel,
            base=Path.cwd().resolve(),
            max_wheel_bytes=release_evidence.DEFAULT_MAX_WHEEL_BYTES,
            max_uncompressed_bytes=(release_evidence.DEFAULT_MAX_UNCOMPRESSED_BYTES),
        )
        forged_record["status"] = "PASS"
        forged_record["violations"] = []
        release_evidence._write_json(
            args.wheel_scan,
            {
                "schema_version": release_evidence.WHEEL_SCAN_SCHEMA,
                "generated_at_utc": "2026-08-30T00:00:00Z",
                "status": "PASS",
                "limits": {
                    "max_wheel_bytes": release_evidence.DEFAULT_MAX_WHEEL_BYTES,
                    "max_uncompressed_bytes": (
                        release_evidence.DEFAULT_MAX_UNCOMPRESSED_BYTES
                    ),
                },
                "errors": [],
                "wheels": [forged_record],
            },
        )
        self.assertEqual(release_evidence.build_manifest(args), 1)
        manifest = json.loads(args.output.read_text(encoding="utf-8"))
        self.assertIn("wheel_content_scan_failed", manifest["failures"])

    def test_stale_wheel_sha_fails_manifest(self) -> None:
        wheel, args = self._fixture()
        with ZipFile(wheel, "a", compression=ZIP_DEFLATED) as archive:
            archive.writestr("demo/new_module.py", b"VALUE = 1\n")
        self.assertEqual(release_evidence.build_manifest(args), 1)
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        self.assertIn("wheel_content_scan_failed", payload["failures"])

    def test_manifest_digest_and_field_drift_fail_verification(self) -> None:
        _, args = self._fixture()
        self.assertEqual(release_evidence.build_manifest(args), 0)
        verify_args = self._verify_args(args)

        payload = json.loads(args.output.read_text(encoding="utf-8"))
        payload["files"][0]["sha256"] = "0" * 64
        release_evidence._write_json(args.output, payload)
        self.assertEqual(release_evidence.verify_manifest(verify_args), 1)

        self.assertEqual(release_evidence.build_manifest(args), 0)
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        payload["wheel_count"] = 99
        release_evidence._write_json(args.output, payload)
        self.assertEqual(release_evidence.verify_manifest(verify_args), 1)

        for field, value in (
            ("generated_at_utc", "2999-01-01T00:00:00Z"),
            ("python_implementation", "OtherPython"),
            ("runner_os", "other-os"),
            ("runner_arch", "other-arch"),
        ):
            with self.subTest(field=field):
                self.assertEqual(release_evidence.build_manifest(args), 0)
                payload = json.loads(args.output.read_text(encoding="utf-8"))
                if field == "generated_at_utc":
                    payload[field] = value
                else:
                    payload["build"][field] = value
                release_evidence._write_json(args.output, payload)
                self.assertEqual(
                    release_evidence.verify_manifest(verify_args),
                    1,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
