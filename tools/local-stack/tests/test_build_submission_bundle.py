from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "local-stack" / "build_submission_bundle.py"
EVIDENCE_PATH = (
    REPOSITORY_ROOT / "docs" / "evidence" / "local-submission-evidence.v1.json"
)
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "telco-submission.yml"

SPEC = importlib.util.spec_from_file_location(
    "networkagent_submission_bundle",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bundle
SPEC.loader.exec_module(bundle)


def _git(root: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_AUTHOR_DATE": "2026-08-31T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-08-31T00:00:00Z",
        }
    )
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def _ledger_is_ready() -> bool:
    try:
        bundle._parse_ledger_bytes(EVIDENCE_PATH.read_bytes())
    except (OSError, bundle.SubmissionBundleError):
        return False
    return True


LEDGER_IS_READY = _ledger_is_ready()


class SubmissionBundleContractTests(unittest.TestCase):
    def _new_repository(
        self,
        *,
        ledger_bytes: bytes | None = None,
        include_ledger: bool = True,
    ) -> tuple[Path, str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        _git(root, "init", "--quiet")
        _git(root, "config", "user.email", "submission-tests@example.invalid")
        _git(root, "config", "user.name", "Submission Tests")
        if include_ledger:
            target = root / bundle.EVIDENCE_LEDGER
            target.parent.mkdir(parents=True)
            target.write_bytes(
                EVIDENCE_PATH.read_bytes() if ledger_bytes is None else ledger_bytes
            )
            _git(root, "add", bundle.EVIDENCE_LEDGER.as_posix())
        else:
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            _git(root, "add", "tracked.txt")
        _git(root, "commit", "--quiet", "-m", "fixture")
        return root, _git(root, "rev-parse", "HEAD")

    def _run_main(
        self,
        root: Path,
        *,
        arguments: list[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        code = bundle.main(
            ["--offline"] if arguments is None else arguments,
            stdout=stdout,
            stderr=stderr,
            repository_root=root,
            environment={} if environment is None else environment,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_committed_ledger_is_frozen_and_builder_valid(self) -> None:
        self.assertTrue(LEDGER_IS_READY)
        raw = EVIDENCE_PATH.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "b93e1ed2fa6f8ae890c9e76d5c5d0fa3fb797d2c9ea94990c2e3c6cc175acbeb",
        )
        self.assertEqual(hashlib.sha256(raw).hexdigest(), bundle.EXPECTED_LEDGER_SHA256)
        ledger = bundle._parse_ledger_bytes(raw)
        self.assertEqual(len(ledger["slices"]), len(bundle.SLICE_IDS))

    def _assert_error(
        self,
        result: tuple[int, str, str],
        expected_code: str,
    ) -> dict[str, object]:
        code, stdout, stderr = result
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        expected = {
            "error": {
                "code": expected_code,
                "message": bundle._MESSAGES[expected_code],
            },
            "ok": False,
            "schema": bundle.ERROR_SCHEMA,
        }
        self.assertEqual(stderr, bundle._canonical_json_bytes(expected).decode("utf-8"))
        return json.loads(stderr)

    def _rendered_files(self) -> dict[str, bytes]:
        raw = EVIDENCE_PATH.read_bytes()
        snapshot = bundle._SourceSnapshot(
            head_sha="a" * 40,
            ledger_bytes=raw,
            ledger_sha256=hashlib.sha256(raw).hexdigest(),
        )
        return bundle._render_bundle(bundle._parse_ledger_bytes(raw), snapshot)

    def _replace_payload_and_rebind_manifest(
        self,
        files: dict[str, bytes],
        name: str,
        payload: bytes,
    ) -> dict[str, bytes]:
        mutated = dict(files)
        mutated[name] = payload
        manifest = json.loads(mutated["manifest.json"])
        for record in manifest["files"]:
            if record["name"] == name:
                record["bytes"] = len(payload)
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                break
        else:
            self.fail(f"manifest did not contain {name}")
        mutated["manifest.json"] = bundle._canonical_json_bytes(
            manifest,
            code="bundle_contract_failed",
        )
        return mutated

    def test_surface_is_one_offline_command_and_five_fixed_outputs(self) -> None:
        self.assertEqual(
            bundle.OUTPUT_DIRECTORY,
            Path(".local/networkagent-submission"),
        )
        self.assertEqual(
            bundle.OUTPUT_FILENAMES,
            (
                "submission-index.json",
                "limitations.json",
                "REPRODUCE.md",
                "index.html",
                "manifest.json",
            ),
        )
        bundle._parse_arguments(("--offline",))
        with self.assertRaisesRegex(
            bundle.SubmissionBundleError,
            "explicit offline mode is required",
        ):
            bundle._parse_arguments(())
        for invalid in (
            ("--help",),
            ("--unknown",),
            ("--off",),
            ("--offline=",),
            ("--offline", "--offline"),
            ("--offline", "--output", "elsewhere"),
            ("--offline", "extra"),
        ):
            with self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "command arguments are invalid",
            ):
                bundle._parse_arguments(invalid)

    def test_sixteen_error_codes_and_messages_are_frozen(self) -> None:
        self.assertEqual(
            bundle._MESSAGES,
            {
                "invalid_arguments": "command arguments are invalid",
                "offline_required": "explicit offline mode is required",
                "source_unavailable": "commit-bound source is unavailable",
                "source_not_clean": "tracked source is not clean",
                "source_mismatch": (
                    "source commit does not match the requested commit"
                ),
                "source_changed": "source changed during bundle construction",
                "ledger_read_failed": (
                    "submission evidence ledger could not be read safely"
                ),
                "ledger_contract_failed": (
                    "submission evidence ledger violated its fixed contract"
                ),
                "privacy_contract_failed": (
                    "submission bundle violated its privacy contract"
                ),
                "workspace_unsafe": "submission workspace is unsafe",
                "workspace_not_owned": ("submission workspace is not marker-owned"),
                "build_in_progress": (
                    "submission bundle construction is already in progress"
                ),
                "bundle_write_failed": (
                    "submission bundle could not be written safely"
                ),
                "bundle_contract_failed": (
                    "submission bundle violated its fixed contract"
                ),
                "cleanup_failed": "submission workspace cleanup failed safely",
                "command_failed": "submission bundle command failed safely",
            },
        )

    def test_workflow_is_dual_python_and_only_312_uploads_bundle(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertEqual(workflow.count('python-version: "3.12"'), 2)
        self.assertEqual(workflow.count('python-version: "3.13"'), 1)
        self.assertEqual(
            workflow.count(
                "python tools/local-stack/build_submission_bundle.py --offline"
            ),
            3,
        )
        self.assertIn("cross-python-equality:", workflow)
        self.assertIn("publish-python-312:", workflow)
        self.assertEqual(workflow.count("uses: actions/upload-artifact@"), 1)
        self.assertIn("path: .local/networkagent-submission", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        for contract_token in (
            '"payload_content_embedded_in_bundle": False',
            '"reference_mode": "HISTORICAL_VERIFIED_REFERENCE"',
            '"remote_availability_asserted": False',
            '"non_closure",',
            "submission HTML CSP is invalid",
            "submission HTML contains an active element",
            "submission HTML contains an active attribute",
        ):
            self.assertIn(contract_token, workflow)
        self.assertNotIn("style-src", workflow)
        for network_token in (
            "curl ",
            "wget ",
            "pip install",
            "Invoke-WebRequest",
        ):
            self.assertNotIn(network_token, workflow)

    def test_failure_json_is_exact_and_never_uses_stdout(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self._assert_error(self._run_main(root, arguments=[]), "offline_required")
        self._assert_error(
            self._run_main(root, arguments=["--unknown"]),
            "invalid_arguments",
        )
        for error_code in tuple(bundle._MESSAGES)[2:]:
            with self.subTest(error_code=error_code), mock.patch.object(
                bundle,
                "_build",
                side_effect=bundle.SubmissionBundleError(error_code),
            ):
                self._assert_error(self._run_main(root), error_code)

    def test_unexpected_base_exception_is_fixed_command_failure(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(bundle, "_build", side_effect=KeyboardInterrupt):
            self._assert_error(
                self._run_main(Path(temporary.name)),
                "command_failed",
            )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_ledger_preserves_fact_state_scope_and_non_closure(self) -> None:
        ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(ledger),
            {
                "evidence_as_of",
                "evidence_cutoff_commit",
                "non_closure",
                "repository",
                "schema_version",
                "slices",
            },
        )
        self.assertEqual(ledger["schema_version"], bundle.LEDGER_SCHEMA)
        self.assertEqual(
            ledger["evidence_cutoff_commit"],
            bundle.EVIDENCE_CUTOFF_COMMIT,
        )
        self.assertEqual(ledger["non_closure"], bundle._EXPECTED_NON_CLOSURE)
        slices = {item["id"]: item for item in ledger["slices"]}
        self.assertEqual(tuple(slices), bundle.SLICE_IDS)
        self.assertEqual(
            {key: item["evidence_classification"] for key, item in slices.items()},
            bundle.EXPECTED_EVIDENCE_CLASSIFICATION,
        )
        self.assertEqual(sum(len(item["runs"]) for item in slices.values()), 24)
        self.assertEqual(
            sum(len(run["jobs"]) for item in slices.values() for run in item["runs"]),
            66,
        )
        artifacts = {
            key: next(
                run["artifact"]
                for run in item["runs"]
                if run["run_id"] == item["primary_run_id"]
            )
            for key, item in slices.items()
        }
        self.assertEqual(len(artifacts), 9)
        for artifact in artifacts.values():
            self.assertFalse(artifact["payload_content_embedded_in_ledger"])
            self.assertFalse(artifact["remote_availability_asserted"])
            self.assertNotIn("evidence_payload_classification", artifact)
            self.assertEqual(
                artifact["primary_summary"]["path"],
                f"release-evidence/{artifact['primary_summary']['name']}",
            )
        self.assertEqual(
            artifacts["S7-01"]["exact_closure"],
            {
                "manifest_record_count": None,
                "member_count": None,
                "state": "UNKNOWN",
            },
        )
        self.assertEqual(
            artifacts["S7-01"]["primary_summary"]["availability"],
            "NOT_EMITTED",
        )
        self.assertEqual(
            artifacts["S4-05"]["primary_summary"]["bytes"]["state"],
            "PRESENT_NOT_RECORDED",
        )
        self.assertEqual(
            slices["S7-03"]["gate_effect"]["stage_updates"],
            bundle.EXPECTED_STAGE_UPDATES["S7-03"],
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_offline_build_is_exact_commit_bound_and_zero_write_on_retry(self) -> None:
        root, head = self._new_repository()
        (root / "untracked.txt").write_text("ignored\n", encoding="utf-8")
        first_code, first_stdout, first_stderr = self._run_main(root)
        self.assertEqual(first_code, 0)
        self.assertEqual(first_stderr, "")
        first = json.loads(first_stdout)
        self.assertEqual(
            first,
            {
                "bundle": {
                    "files": 5,
                    "manifest_bytes": first["bundle"]["manifest_bytes"],
                    "manifest_sha256": first["bundle"]["manifest_sha256"],
                    "relative_directory": ".local/networkagent-submission",
                },
                "changed": True,
                "classification": "COMMIT_BOUND_LOCAL_SUBMISSION_BUNDLE",
                "ok": True,
                "schema": bundle.RESULT_SCHEMA,
                "source": {
                    "commit_bound": True,
                    "commit_sha": head,
                    "tracked_clean": True,
                },
            },
        )
        self.assertEqual(
            first_stdout,
            bundle._canonical_json_bytes(first).decode("utf-8"),
        )

        output = root / bundle.OUTPUT_DIRECTORY
        self.assertEqual(
            tuple(sorted(item.name for item in output.iterdir())),
            tuple(sorted(bundle.OUTPUT_FILENAMES)),
        )
        before = {
            name: (
                (output / name).read_bytes(),
                (output / name).stat().st_mtime_ns,
                (output / name).stat().st_ctime_ns,
            )
            for name in bundle.OUTPUT_FILENAMES
        }

        retry_code, retry_stdout, retry_stderr = self._run_main(root)
        self.assertEqual(retry_code, 0)
        self.assertEqual(retry_stderr, "")
        retry = json.loads(retry_stdout)
        self.assertFalse(retry["changed"])
        self.assertEqual({**first, "changed": False}, retry)
        self.assertEqual(
            before,
            {
                name: (
                    (output / name).read_bytes(),
                    (output / name).stat().st_mtime_ns,
                    (output / name).stat().st_ctime_ns,
                )
                for name in bundle.OUTPUT_FILENAMES
            },
        )

        with tempfile.TemporaryDirectory() as clone_parent:
            clone = Path(clone_parent) / "same-commit-clone"
            shutil.copytree(
                root,
                clone,
                ignore=shutil.ignore_patterns(".local", "untracked.txt"),
            )
            clone_code, _, clone_stderr = self._run_main(clone)
            self.assertEqual((clone_code, clone_stderr), (0, ""))
            self.assertEqual(
                {name: before[name][0] for name in bundle.OUTPUT_FILENAMES},
                {
                    name: (clone / bundle.OUTPUT_DIRECTORY / name).read_bytes()
                    for name in bundle.OUTPUT_FILENAMES
                },
            )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_manifest_closes_four_payloads_and_the_exact_five_file_directory(
        self,
    ) -> None:
        root, head = self._new_repository()
        code, _, stderr = self._run_main(root)
        self.assertEqual((code, stderr), (0, ""))
        output = root / bundle.OUTPUT_DIRECTORY
        manifest_bytes = (output / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest["schema"], bundle.MANIFEST_SCHEMA)
        self.assertEqual(
            manifest["ownership"],
            {
                "ledger_sha256": hashlib.sha256(EVIDENCE_PATH.read_bytes()).hexdigest(),
                "marker": "NETWORKAGENT_LOCAL_SUBMISSION_BUNDLE",
                "repository": bundle.REPOSITORY,
                "source_commit": head,
            },
        )
        self.assertEqual(
            [record["name"] for record in manifest["files"]],
            list(bundle.OUTPUT_FILENAMES[:-1]),
        )
        for record in manifest["files"]:
            payload = (output / record["name"]).read_bytes()
            self.assertEqual(record["bytes"], len(payload))
            self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(
            tuple(sorted(item.name for item in output.iterdir())),
            tuple(sorted(bundle.OUTPUT_FILENAMES)),
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_manifest_is_the_last_published_acceptance_marker(self) -> None:
        root, _ = self._new_repository()
        published: list[str] = []
        original_link = os.link

        def record_link(source: object, target: object, **options: object) -> None:
            published.append(Path(target).name)
            original_link(source, target, **options)

        with mock.patch.object(bundle.os, "link", side_effect=record_link):
            self.assertEqual(self._run_main(root)[0], 0)
        self.assertEqual(tuple(published), bundle.OUTPUT_FILENAMES)
        self.assertEqual(published[-1], "manifest.json")

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_outputs_are_canonical_local_only_and_html_is_passive(self) -> None:
        root, _ = self._new_repository()
        self.assertEqual(self._run_main(root)[0], 0)
        output = root / bundle.OUTPUT_DIRECTORY
        for name in bundle.OUTPUT_FILENAMES:
            payload = (output / name).read_bytes()
            self.assertNotIn(b"\r", payload)
            self.assertNotIn(b"\x00", payload)
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            lowered = payload.lower()
            self.assertNotIn(b"http://", lowered)
            self.assertNotIn(b"https://", lowered)
            self.assertNotIn(b"file://", lowered)
            if name.endswith(".json"):
                self.assertEqual(
                    payload,
                    bundle._canonical_json_bytes(json.loads(payload)),
                )
        page = (output / "index.html").read_text(encoding="utf-8")
        lowered_page = page.lower()
        expected_csp = (
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'\">"
        )
        self.assertEqual(page.count(expected_csp), 1)
        self.assertNotIn("style-src", lowered_page)
        for forbidden in (
            "<base",
            "<embed",
            "<form",
            "<iframe",
            "<img",
            "<link",
            "<object",
            "<script",
            "<style",
            "<svg",
            "javascript:",
            " onload=",
            " src=",
            " style=",
        ):
            self.assertNotIn(forbidden, lowered_page)
        self.assertEqual(
            set(bundle.re.findall(r'<a href="([^"]+)">', page)),
            set(bundle.OUTPUT_FILENAMES) - {"index.html"},
        )
        index = json.loads((output / "submission-index.json").read_bytes())
        self.assertEqual(
            index["artifact_citation_policy"],
            {
                "payload_content_embedded_in_bundle": False,
                "reference_mode": "HISTORICAL_VERIFIED_REFERENCE",
                "remote_availability_asserted": False,
            },
        )
        self.assertEqual(
            index["evidence_cutoff_commit"],
            bundle.EVIDENCE_CUTOFF_COMMIT,
        )
        self.assertEqual(index["non_closure"], bundle._EXPECTED_NON_CLOSURE)
        self.assertEqual(
            hashlib.sha256((output / "submission-index.json").read_bytes()).hexdigest(),
            bundle.EXPECTED_INDEX_SHA256,
        )
        self.assertEqual(
            {item["id"]: item["evidence_classification"] for item in index["slices"]},
            bundle.EXPECTED_EVIDENCE_CLASSIFICATION,
        )
        limitations = json.loads((output / "limitations.json").read_bytes())
        self.assertEqual(limitations["non_closure"], bundle._EXPECTED_NON_CLOSURE)
        self.assertEqual(
            hashlib.sha256((output / "limitations.json").read_bytes()).hexdigest(),
            bundle.EXPECTED_LIMITATIONS_SHA256,
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_html_escapes_ledger_text(self) -> None:
        ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        ledger["slices"][0]["title_zh"] = '<script data-x="&">test</script>'
        page = bundle._render_html(ledger).decode("utf-8")
        self.assertNotIn("<script", page.lower())
        self.assertIn("&lt;script data-x=&quot;&amp;&quot;&gt;", page)

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_public_index_schema_and_key_mutations_are_rejected(self) -> None:
        files = self._rendered_files()
        for mutation in ("schema", "extra-key"):
            index = json.loads(files["submission-index.json"])
            if mutation == "schema":
                index["schema"] = "networkagent-local-submission-index/9.9"
            else:
                index["unexpected"] = False
            mutated = self._replace_payload_and_rebind_manifest(
                files,
                "submission-index.json",
                bundle._canonical_json_bytes(index, code="bundle_contract_failed"),
            )
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission bundle violated its fixed contract",
            ):
                bundle._validate_rendered_outputs(mutated)

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_public_index_private_projection_mutation_is_rejected(self) -> None:
        files = self._rendered_files()
        for private_name in ("body", "raw_payload"):
            index = json.loads(files["submission-index.json"])
            index["slices"][0]["runs"][0][private_name] = "fixture"
            mutated = self._replace_payload_and_rebind_manifest(
                files,
                "submission-index.json",
                bundle._canonical_json_bytes(index, code="bundle_contract_failed"),
            )
            with self.subTest(private_name=private_name), self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission bundle violated its privacy contract",
            ):
                bundle._validate_rendered_outputs(mutated)

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_every_public_nested_object_rejects_safe_extra_and_missing_fields(
        self,
    ) -> None:
        files = self._rendered_files()

        def locate(value: object, path: tuple[object, ...]) -> dict[str, object]:
            current = value
            for part in path:
                if isinstance(part, int):
                    self.assertIsInstance(current, list)
                    current = current[part]
                else:
                    self.assertIsInstance(current, dict)
                    current = current[part]
            self.assertIsInstance(current, dict)
            return current

        object_cases = (
            (
                "artifact-policy",
                "index",
                ("artifact_citation_policy",),
                "reference_mode",
            ),
            ("non-closure-gates", "index", ("non_closure", "gates"), "G2"),
            ("non-closure-stages", "index", ("non_closure", "stages"), "P6"),
            ("slice", "index", ("slices", 0), "title_zh"),
            (
                "evidence-classification",
                "index",
                ("slices", 0, "evidence_classification"),
                "value",
            ),
            (
                "evidence-docs",
                "index",
                ("slices", 0, "evidence_docs"),
                "document_count",
            ),
            (
                "provenance-item",
                "index",
                ("slices", 0, "evidence_docs", "provenance", 0),
                "role",
            ),
            (
                "gate-effect",
                "index",
                ("slices", 0, "gate_effect"),
                "scope",
            ),
            (
                "stage-update-item",
                "index",
                ("slices", 7, "gate_effect", "stage_updates", 0),
                "effect",
            ),
            ("delivered", "index", ("slices", 0, "delivered"), "state"),
            (
                "delivered-boolean-map",
                "index",
                ("slices", 3, "delivered", "value"),
                "checkpointed_full_database_backup",
            ),
            ("run", "index", ("slices", 0, "runs", 0), "trigger"),
            ("job", "index", ("slices", 0, "runs", 0, "jobs", 0), "python"),
            (
                "artifact",
                "index",
                ("slices", 0, "runs", 0, "artifact"),
                "retention_days",
            ),
            (
                "exact-closure",
                "index",
                ("slices", 0, "runs", 0, "artifact", "exact_closure"),
                "state",
            ),
            (
                "verification-label",
                "index",
                ("slices", 0, "runs", 0, "artifact", "verification_label"),
                "origin",
            ),
            (
                "primary-summary",
                "index",
                ("slices", 0, "runs", 0, "artifact", "primary_summary"),
                "schema",
            ),
            (
                "bytes-fact-record",
                "index",
                (
                    "slices",
                    0,
                    "runs",
                    0,
                    "artifact",
                    "primary_summary",
                    "bytes",
                ),
                "value",
            ),
            (
                "sha256-fact-record",
                "index",
                (
                    "slices",
                    0,
                    "runs",
                    0,
                    "artifact",
                    "primary_summary",
                    "sha256",
                ),
                "value",
            ),
            ("limitations-gates", "limitations", ("non_closure", "gates"), "G2"),
            (
                "limitations-stages",
                "limitations",
                ("non_closure", "stages"),
                "P6",
            ),
            ("limitations-slice", "limitations", ("slices", 0), "not_claimed"),
        )
        for label, document, path, missing_key in object_cases:
            for mutation in ("extra", "missing"):
                index = json.loads(files["submission-index.json"])
                limitations = json.loads(files["limitations.json"])
                root = index if document == "index" else limitations
                target = locate(root, path)
                if mutation == "extra":
                    target["diagnostic_status"] = True
                else:
                    del target[missing_key]
                with self.subTest(
                    label=label,
                    mutation=mutation,
                ), self.assertRaisesRegex(
                    bundle.SubmissionBundleError,
                    "submission bundle violated its fixed contract",
                ):
                    bundle._validate_public_documents(index, limitations)

        sequence_cases = (
            ("delivered-token-list", "index", ("slices", 0, "delivered", "value")),
            (
                "limitations-not-claimed",
                "limitations",
                ("slices", 0, "not_claimed"),
            ),
        )
        for label, document, path in sequence_cases:
            for mutation in ("extra", "missing"):
                index = json.loads(files["submission-index.json"])
                limitations = json.loads(files["limitations.json"])
                current = index if document == "index" else limitations
                for part in path:
                    current = current[part]
                self.assertIsInstance(current, list)
                if mutation == "extra":
                    current.append("SAFE_EXTRA_FACT")
                else:
                    current.pop()
                with self.subTest(
                    label=label,
                    mutation=mutation,
                ), self.assertRaisesRegex(
                    bundle.SubmissionBundleError,
                    "submission bundle violated its fixed contract",
                ):
                    bundle._validate_public_documents(index, limitations)

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_html_csp_active_element_and_attribute_mutations_are_rejected(self) -> None:
        files = self._rendered_files()
        page = files["index.html"].decode("utf-8")
        mutations = {
            "csp": page.replace(
                "frame-ancestors 'none'",
                "style-src 'none'; frame-ancestors 'none'",
                1,
            ),
            "active-element": page.replace(
                "</body>",
                "<script>void 0</script>\n</body>",
                1,
            ),
            "active-attribute": page.replace(
                "<h1>",
                '<h1 style="display:block">',
                1,
            ),
        }
        for mutation, mutated_page in mutations.items():
            mutated = self._replace_payload_and_rebind_manifest(
                files,
                "index.html",
                mutated_page.encode("utf-8"),
            )
            with self.subTest(mutation=mutation), self.assertRaises(
                bundle.SubmissionBundleError
            ):
                bundle._validate_rendered_outputs(mutated)

    def test_source_unavailable_and_missing_ledger_are_distinct(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self._assert_error(
            self._run_main(Path(temporary.name)),
            "source_unavailable",
        )
        root, _ = self._new_repository(include_ledger=False)
        self._assert_error(self._run_main(root), "ledger_read_failed")

    def test_git_invocation_disables_replacements_and_strips_every_git_variable(
        self,
    ) -> None:
        completed = SimpleNamespace(returncode=0, stdout=b"ok\n")
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "nested").mkdir()
            supplied_root = repository / "nested" / ".."
            expected_root = Path(os.path.abspath(supplied_root))
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "safe.directory",
                    "GIT_CONFIG_VALUE_0": "*",
                    "GIT_DIR": "attacker-dir",
                    "git_mixed_case_attack": "attacker-value",
                    "NETWORKAGENT_TEST_SENTINEL": "preserved",
                },
                clear=False,
            ), mock.patch.object(
                bundle,
                "_validate_directory_ancestry",
                wraps=bundle._validate_directory_ancestry,
            ) as validate_ancestry, mock.patch.object(
                bundle.subprocess,
                "run",
                return_value=completed,
            ) as run:
                self.assertEqual(
                    bundle._run_git(supplied_root, ("rev-parse", "HEAD")),
                    b"ok\n",
                )
        command = run.call_args.args[0]
        child_environment = run.call_args.kwargs["env"]
        configurations = tuple(
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "-c"
        )
        safe_directories = tuple(
            value for value in configurations if value.startswith("safe.directory=")
        )
        self.assertEqual(
            safe_directories,
            (f"safe.directory={expected_root}",),
        )
        self.assertNotIn("safe.directory=*", configurations)
        self.assertNotIn(f"safe.directory={expected_root.parent}", configurations)
        self.assertNotIn(
            bundle.OUTPUT_DIRECTORY.as_posix(),
            " ".join(command).replace("\\", "/"),
        )
        self.assertEqual(run.call_args.kwargs["cwd"], str(expected_root))
        validate_ancestry.assert_called_once_with(expected_root, "source_unavailable")
        self.assertIn("--no-replace-objects", command)
        self.assertIn("core.useReplaceRefs=false", command)
        self.assertFalse(
            any(key.upper().startswith("GIT_") for key in child_environment)
        )
        self.assertEqual(
            child_environment["NETWORKAGENT_TEST_SENTINEL"],
            "preserved",
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"safe.directory=*"', source)

    def test_git_safe_directory_rejects_a_wildcard_root_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            bundle.subprocess,
            "run",
        ) as run:
            with self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "commit-bound source is unavailable",
            ):
                bundle._run_git(
                    Path(f"{temporary}*"),
                    ("rev-parse", "HEAD"),
                )
        run.assert_not_called()

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_dirty_and_requested_commit_mismatch_fail_before_build(self) -> None:
        root, head = self._new_repository()
        (root / bundle.EVIDENCE_LEDGER).write_bytes(b"dirty\n")
        self._assert_error(self._run_main(root), "source_not_clean")

        _git(root, "checkout", "--quiet", "--", bundle.EVIDENCE_LEDGER.as_posix())
        wrong = "0" * 40 if head != "0" * 40 else "1" * 40
        self._assert_error(
            self._run_main(root, environment={"GITHUB_SHA": wrong}),
            "source_mismatch",
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_git_replace_ref_cannot_substitute_head_or_ledger_blob(self) -> None:
        root, original = self._new_repository()
        ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        ledger["slices"][0]["title_zh"] = "replace-ref attack payload"
        replacement_bytes = bundle._canonical_json_bytes(ledger)
        (root / bundle.EVIDENCE_LEDGER).write_bytes(replacement_bytes)
        _git(root, "add", bundle.EVIDENCE_LEDGER.as_posix())
        _git(root, "commit", "--quiet", "-m", "replacement")
        replacement = _git(root, "rev-parse", "HEAD")
        _git(root, "checkout", "--quiet", "--detach", original)
        _git(root, "replace", original, replacement)

        self.assertEqual(_git(root, "replace", "--list"), original)
        unprotected = subprocess.run(
            (
                "git",
                "show",
                f"{original}:{bundle.EVIDENCE_LEDGER.as_posix()}",
            ),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(unprotected.returncode, 0)
        replaced = json.loads(unprotected.stdout)
        self.assertEqual(
            replaced["slices"][0]["title_zh"], "replace-ref attack payload"
        )

        code, stdout, stderr = self._run_main(root)
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads(stdout)
        self.assertEqual(result["source"]["commit_sha"], original)
        manifest = json.loads(
            (root / bundle.OUTPUT_DIRECTORY / "manifest.json").read_bytes()
        )
        self.assertEqual(
            manifest["ownership"]["ledger_sha256"],
            bundle.EXPECTED_LEDGER_SHA256,
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_git_environment_cannot_redirect_repository_or_object_reads(self) -> None:
        root, original = self._new_repository()
        ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        ledger["slices"][0]["title_zh"] = "environment attack payload"
        evil_root, evil_head = self._new_repository(
            ledger_bytes=bundle._canonical_json_bytes(ledger)
        )
        self.assertNotEqual(original, evil_head)
        attack_environment = {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(root / ".git" / "objects"),
            "GIT_COMMON_DIR": str(evil_root / ".git"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.useReplaceRefs",
            "GIT_CONFIG_VALUE_0": "true",
            "GIT_DIR": str(evil_root / ".git"),
            "GIT_EXEC_PATH": str(evil_root / "missing-git-exec-path"),
            "GIT_INDEX_FILE": str(evil_root / ".git" / "index"),
            "GIT_OBJECT_DIRECTORY": str(evil_root / ".git" / "objects"),
            "GIT_REPLACE_REF_BASE": "refs/attack/",
            "GIT_WORK_TREE": str(evil_root),
        }
        with mock.patch.dict(os.environ, attack_environment, clear=False):
            code, stdout, stderr = self._run_main(root)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["source"]["commit_sha"], original)

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_reparse_point_in_source_ancestry_is_rejected(self) -> None:
        root, _ = self._new_repository()
        real_lstat = os.lstat
        tainted_ancestor = root.parent

        def inject_reparse(path: object) -> object:
            metadata = real_lstat(path)
            if Path(path) != tainted_ancestor:
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_file_attributes=(getattr(metadata, "st_file_attributes", 0) | 0x400),
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
            )

        with mock.patch.object(bundle.os, "lstat", side_effect=inject_reparse):
            self._assert_error(self._run_main(root), "source_unavailable")

    @unittest.skipUnless(
        LEDGER_IS_READY and os.name == "nt",
        "Windows junction coverage requires the final ledger and Windows",
    )
    def test_windows_source_junction_is_rejected_without_following(self) -> None:
        root, _ = self._new_repository()
        with tempfile.TemporaryDirectory() as temporary:
            junction = Path(temporary) / "repository-junction"
            created = subprocess.run(
                ("cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(root)),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest("directory junctions are unavailable")
            try:
                self._assert_error(self._run_main(junction), "source_unavailable")
            finally:
                os.rmdir(junction)

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_source_drift_is_detected_from_head(self) -> None:
        root, _ = self._new_repository()
        snapshot, _ = bundle._load_source_snapshot(root, {})
        (root / "next.txt").write_text("next\n", encoding="utf-8")
        _git(root, "add", "next.txt")
        _git(root, "commit", "--quiet", "-m", "next")
        with self.assertRaisesRegex(
            bundle.SubmissionBundleError,
            "source changed during bundle construction",
        ):
            bundle._assert_source_unchanged(root, snapshot, {})

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_source_drift_before_manifest_cleans_only_owned_state(self) -> None:
        root, _ = self._new_repository()
        drift = bundle.SubmissionBundleError("source_changed")
        with mock.patch.object(
            bundle,
            "_assert_source_unchanged",
            side_effect=(None, drift),
        ):
            self._assert_error(self._run_main(root), "source_changed")
        local = root / bundle.OUTPUT_DIRECTORY.parent
        self.assertFalse((root / bundle.OUTPUT_DIRECTORY).exists())
        self.assertFalse((local / ".networkagent-submission.lock").exists())
        self.assertFalse((local / ".networkagent-submission.staging").exists())

    def test_noncanonical_or_duplicate_ledger_is_contract_failure(self) -> None:
        invalid_ledgers = (
            b'{"a":1, "a":1}\n',
            b'{"schema":"wrong"}\n',
            b"{}",
            b"{}\r\n",
            b"\xef\xbb\xbf{}\n",
            b'{"x":"\\ud800"}\n',
        )
        for payload in invalid_ledgers:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission evidence ledger violated its fixed contract",
            ):
                bundle._parse_ledger_bytes(payload)

        if LEDGER_IS_READY:
            ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
            ledger["slices"][5]["evidence_classification"][
                "scope"
            ] = "PRIMARY_SUMMARY_PAYLOAD"
            with self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission evidence ledger violated its fixed contract",
            ):
                bundle._parse_ledger_bytes(bundle._canonical_json_bytes(ledger))

    def test_privacy_contract_rejects_secrets_urls_and_local_paths(self) -> None:
        forbidden = (
            {"api_key": "redacted"},
            {"value": "https://example.invalid/evidence"},
            {"value": "embedded C:\\Users\\person\\evidence.json path"},
            {"value": "embedded /home/person/evidence.json path"},
            {"value": "embedded (/\u7528\u6237/\u59d3\u540d/\u8bc1\u636e) path"},
            {"value": r"embedded \\server\share\evidence.json path"},
            {"value": r"embedded \\?\C:\private\evidence.json path"},
            {"value": r"embedded \\.\PhysicalDrive0 path"},
            {"value": "-----BEGIN PRIVATE KEY-----"},
            {"value": "Bearer abcdefghijklmnopqrstuvwxyz012345"},
            {"value": "password=hunter2"},
            {"value": "xo" + "xb-1234567890-abcdefghijklmnop"},
            {"value": "gl" + "pat-abcdefghijklmnopqrstuvwxyz"},
            {"path": "release-evidence/summary.json"},
        )
        for value in forbidden:
            with self.subTest(value=value), self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission bundle violated its privacy contract",
            ):
                bundle._validate_privacy_contract(value)

        bundle._validate_privacy_contract(
            {
                "html_fragment": "</table>",
                "runbook": "docs/runbooks/local-submission.md",
            }
        )
        bundle._validate_privacy_contract(
            {
                "slices": [
                    {
                        "runs": [
                            {
                                "artifact": {
                                    "primary_summary": {
                                        "availability": "PRESENT",
                                        "bytes": {},
                                        "name": "summary.json",
                                        "path": "release-evidence/summary.json",
                                        "schema": "fixture/1.0",
                                        "sha256": {},
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        )
        for forbidden_key in (
            "body",
            "event_id",
            "events",
            "ground_truth",
            "incident_id",
            "label",
            "labels",
            "ran_ue_id",
            "raw",
            "raw_payload",
            "row",
            "rows",
            "source_event_id",
            "source_url",
            "trace_id",
            "ue",
            "ue_id",
        ):
            with self.subTest(forbidden_key=forbidden_key), self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission bundle violated its privacy contract",
            ):
                bundle._validate_privacy_contract({forbidden_key: "fixture"})

        if LEDGER_IS_READY:
            ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
            ledger["slices"][0]["title_zh"] = "https://example.invalid/private"
            root, _ = self._new_repository(
                ledger_bytes=bundle._canonical_json_bytes(ledger)
            )
            self._assert_error(self._run_main(root), "privacy_contract_failed")

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_benign_title_mutation_fails_the_frozen_ledger_digest(self) -> None:
        ledger = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        ledger["slices"][0]["title_zh"] = "\u9759\u6001\u5408\u540c\u53d8\u5f02"
        mutated = bundle._canonical_json_bytes(ledger)
        self.assertNotEqual(
            hashlib.sha256(mutated).hexdigest(), bundle.EXPECTED_LEDGER_SHA256
        )
        root, _ = self._new_repository(ledger_bytes=mutated)
        self._assert_error(self._run_main(root), "ledger_contract_failed")

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_unknown_final_workspace_is_preserved(self) -> None:
        root, _ = self._new_repository()
        output = root / bundle.OUTPUT_DIRECTORY
        output.mkdir(parents=True)
        unknown = output / "keep.txt"
        unknown.write_text("preserve\n", encoding="utf-8")
        self._assert_error(self._run_main(root), "workspace_not_owned")
        self.assertEqual(unknown.read_text(encoding="utf-8"), "preserve\n")

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_valid_owned_bundle_with_new_source_is_not_overwritten(self) -> None:
        root, _ = self._new_repository()
        self.assertEqual(self._run_main(root)[0], 0)
        output = root / bundle.OUTPUT_DIRECTORY
        before = {
            name: (output / name).read_bytes() for name in bundle.OUTPUT_FILENAMES
        }
        (root / "next.txt").write_text("next\n", encoding="utf-8")
        _git(root, "add", "next.txt")
        _git(root, "commit", "--quiet", "-m", "new source")
        self._assert_error(self._run_main(root), "bundle_contract_failed")
        self.assertEqual(
            before,
            {name: (output / name).read_bytes() for name in bundle.OUTPUT_FILENAMES},
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_hardlinked_final_payload_is_rejected_and_preserved(self) -> None:
        root, _ = self._new_repository()
        self.assertEqual(self._run_main(root)[0], 0)
        output = root / bundle.OUTPUT_DIRECTORY
        extra_link = root / "external-hardlink"
        try:
            os.link(output / "index.html", extra_link)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        self._assert_error(self._run_main(root), "workspace_not_owned")
        self.assertTrue(extra_link.exists())
        self.assertTrue((output / "index.html").exists())

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_linked_local_workspace_is_rejected_without_following(self) -> None:
        root, _ = self._new_repository()
        outside = root.parent / f"{root.name}-outside"
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        try:
            os.symlink(outside, root / ".local", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        self._assert_error(self._run_main(root), "workspace_unsafe")
        self.assertEqual(tuple(outside.iterdir()), ())

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_lock_and_staging_states_are_distinct_and_preserved(self) -> None:
        root, _ = self._new_repository()
        local = root / bundle.OUTPUT_DIRECTORY.parent
        local.mkdir(parents=True)
        lock = local / ".networkagent-submission.lock"
        lock.write_bytes(b"networkagent-local-submission-lock/1\n")
        self._assert_error(self._run_main(root), "build_in_progress")
        self.assertTrue(lock.exists())

        lock.unlink()
        lock.write_bytes(b"not-owned\n")
        self._assert_error(self._run_main(root), "workspace_unsafe")
        self.assertEqual(lock.read_bytes(), b"not-owned\n")

        lock.unlink()
        stage = local / ".networkagent-submission.staging"
        stage.mkdir()
        marker = stage / "unknown"
        marker.write_text("preserve\n", encoding="utf-8")
        self._assert_error(self._run_main(root), "workspace_unsafe")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse(lock.exists())

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_exact_output_still_requires_lock_and_absent_staging(self) -> None:
        root, _ = self._new_repository()
        self.assertEqual(self._run_main(root)[0], 0)
        output = root / bundle.OUTPUT_DIRECTORY
        local = output.parent
        before = {
            name: (output / name).read_bytes() for name in bundle.OUTPUT_FILENAMES
        }
        lock = local / ".networkagent-submission.lock"
        stage = local / ".networkagent-submission.staging"

        lock.write_bytes(bundle._LOCK_BYTES)
        self._assert_error(self._run_main(root), "build_in_progress")
        self.assertEqual(lock.read_bytes(), bundle._LOCK_BYTES)
        self.assertEqual(
            before,
            {name: (output / name).read_bytes() for name in bundle.OUTPUT_FILENAMES},
        )
        lock.unlink()

        stage.mkdir()
        foreign = stage / "foreign"
        foreign.write_text("preserve\n", encoding="utf-8")
        self._assert_error(self._run_main(root), "workspace_unsafe")
        self.assertEqual(foreign.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse(lock.exists())
        foreign.unlink()
        stage.rmdir()

        stage.mkdir()
        for name, payload in before.items():
            (stage / name).write_bytes(payload)
        self._assert_error(self._run_main(root), "workspace_unsafe")
        self.assertEqual(
            before,
            {name: (stage / name).read_bytes() for name in bundle.OUTPUT_FILENAMES},
        )
        self.assertFalse(lock.exists())
        self.assertEqual(
            before,
            {name: (output / name).read_bytes() for name in bundle.OUTPUT_FILENAMES},
        )

    @unittest.skipUnless(LEDGER_IS_READY, "final evidence ledger is not frozen yet")
    def test_cleanup_failure_has_highest_failure_priority(self) -> None:
        root, _ = self._new_repository()
        stage = (
            root / bundle.OUTPUT_DIRECTORY.parent / (".networkagent-submission.staging")
        )
        stage.mkdir(parents=True)
        with mock.patch.object(bundle, "_cleanup_owned_file", return_value=False):
            self._assert_error(self._run_main(root), "cleanup_failed")

    def test_write_and_render_contract_failures_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            bundle.os,
            "open",
            side_effect=PermissionError,
        ):
            with self.assertRaisesRegex(
                bundle.SubmissionBundleError,
                "submission bundle could not be written safely",
            ):
                bundle._exclusive_write(Path(temporary) / "file", b"payload\n")
        oversized = {
            name: b"x" * (bundle._OUTPUT_FILE_MAX_BYTES + 1)
            for name in bundle.OUTPUT_FILENAMES
        }
        with self.assertRaisesRegex(
            bundle.SubmissionBundleError,
            "submission bundle violated its fixed contract",
        ):
            bundle._validate_rendered_outputs(oversized)


if __name__ == "__main__":
    unittest.main()
