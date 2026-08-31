from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPOSITORY_ROOT / "tools" / "local-stack" / "run_bubbleran_defense_demo.py"
)
SPEC = importlib.util.spec_from_file_location(
    "networkagent_bubbleran_defense_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo
SPEC.loader.exec_module(demo)


class FakeProcessRunner:
    def __init__(self, *, clean: bool = True) -> None:
        self.clean = clean

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        self.assertions(arguments, cwd, env, timeout)
        if arguments == ("git", "rev-parse", "--verify", "HEAD"):
            return subprocess.CompletedProcess(arguments, 0, b"a" * 40 + b"\n", b"")
        return subprocess.CompletedProcess(
            arguments,
            0,
            b"" if self.clean else b" M tracked.py\n",
            b"",
        )

    @staticmethod
    def assertions(
        arguments: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> None:
        assert cwd.is_absolute()
        assert timeout == 10
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert arguments in {
            ("git", "rev-parse", "--verify", "HEAD"),
            ("git", "status", "--porcelain", "--untracked-files=no"),
        }


class FakeScenario:
    def __init__(self) -> None:
        self.completed = False

    def __call__(self, *, work_directory: Path, **_kwargs):  # noqa: ANN001
        fixture = work_directory / "fixture.csv"
        fixture.write_text("generated\n", encoding="utf-8")
        state = work_directory / "state"
        state.mkdir()
        (state / "checkpoint.json").write_text("{}", encoding="utf-8")
        self.completed = True
        return demo.ScenarioEvidence.fixed_success()


def invoke(
    root: Path,
    *arguments: str,
    scenario=None,  # noqa: ANN001
    clean: bool = True,
):
    stdout = StringIO()
    stderr = StringIO()
    code = demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        repository_root=root,
        asset_root=root,
        utc_now=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
        random_token=lambda: "1a2b3c4d5e6f",
        process_runner=FakeProcessRunner(clean=clean),
        scenario_runner=scenario or FakeScenario(),
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


class BubbleRanDefenseUnitTests(unittest.TestCase):
    def test_fake_success_keeps_only_identity_bound_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = FakeScenario()
            code, payload, error = invoke(
                root,
                "--offline",
                "--approve-local-simulation",
                scenario=scenario,
            )

            self.assertEqual(code, 0)
            self.assertIsNone(error)
            self.assertTrue(scenario.completed)
            self.assertEqual(
                payload["schema"],
                "networkagent-local-bubbleran-defense-evidence/1.0",
            )
            self.assertEqual(
                payload["classification"],
                "LOCAL_BUBBLERAN_VERTICAL_DEFENSE_EVIDENCE",
            )
            self.assertEqual(
                payload["fixture"],
                {
                    "origin": "CODE_GENERATED_SCHEMA_FIXTURE",
                    "record_count": 4,
                },
            )
            self.assertEqual(payload["proof"], demo.expected_proof())
            self.assertEqual(
                payload["release"],
                {
                    "eligible": True,
                    "source_state": "COMMIT_BOUND",
                },
            )
            run_root = root / ".local" / "networkagent-bubbleran-defense"
            run_directory = next(run_root.iterdir())
            self.assertEqual(
                {item.name for item in run_directory.iterdir()},
                {demo.REPORT_NAME},
            )
            report = (run_directory / demo.REPORT_NAME).read_bytes()
            self.assertEqual(len(report), payload["report"]["bytes"])
            self.assertEqual(
                demo.hashlib.sha256(report).hexdigest(),
                payload["report"]["sha256"],
            )
            self.assertEqual(
                json.loads(report),
                {key: value for key, value in payload.items() if key != "report"},
            )

    def test_cli_is_exact_and_requires_both_safety_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                ((), "offline_required"),
                (("--offline",), "confirmation_required"),
                (("--approve-local-simulation",), "offline_required"),
                (
                    ("--offline", "--approve-local-simulation", "extra"),
                    "invalid_arguments",
                ),
                (("--unknown",), "invalid_arguments"),
            )
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    code, payload, error = invoke(root, *arguments)
                    self.assertEqual(code, 2)
                    self.assertIsNone(payload)
                    self.assertEqual(error["error"]["code"], expected)

    def test_dirty_source_is_worktree_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code, payload, error = invoke(
                Path(temporary),
                "--offline",
                "--approve-local-simulation",
                clean=False,
            )
            self.assertEqual(code, 0)
            self.assertIsNone(error)
            self.assertFalse(payload["source"]["commit_bound"])
            self.assertEqual(
                payload["release"],
                {
                    "eligible": False,
                    "source_state": "WORKTREE_ONLY",
                },
            )

    def test_existing_or_symlink_run_collision_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = (
                root
                / ".local"
                / "networkagent-bubbleran-defense"
                / "20260831T020304Z-1a2b3c4d5e6f"
            )
            candidate.mkdir(parents=True)
            marker = candidate / "marker"
            marker.write_bytes(b"preserve")
            code, payload, error = invoke(
                root, "--offline", "--approve-local-simulation"
            )
            self.assertEqual(code, 2)
            self.assertIsNone(payload)
            self.assertEqual(error["error"]["code"], "report_write_failed")
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_unknown_first_run_identity_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = demo._directory_identity
            failed = False

            def fail_once(path: Path, *, code: str):
                nonlocal failed
                if path.name.endswith("1a2b3c4d5e6f") and not failed:
                    failed = True
                    raise demo.BubbleRanDefenseError(code)
                return original(path, code=code)

            with mock.patch.object(demo, "_directory_identity", fail_once):
                code, payload, error = invoke(
                    root, "--offline", "--approve-local-simulation"
                )
            candidate = (
                root
                / ".local"
                / "networkagent-bubbleran-defense"
                / "20260831T020304Z-1a2b3c4d5e6f"
            )
            self.assertEqual(code, 2)
            self.assertIsNone(payload)
            self.assertEqual(error["error"]["code"], "report_write_failed")
            self.assertTrue(candidate.is_dir())

    def test_cleanup_rejects_symlink_and_hardlink_without_deleting_targets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.write_bytes(b"preserve")
            for kind in ("symlink", "hardlink"):
                work = root / f"work-{kind}"
                work.mkdir()
                child = work / "child"
                try:
                    if kind == "symlink":
                        child.symlink_to(outside)
                    else:
                        os.link(outside, child)
                except OSError:
                    continue
                with self.assertRaises(demo.BubbleRanDefenseError) as captured:
                    demo._capture_owned_tree(
                        work,
                        expected_root_identity=demo._directory_identity(
                            work, code="cleanup_failed"
                        ),
                    )
                self.assertEqual(captured.exception.code, "cleanup_failed")
                self.assertEqual(outside.read_bytes(), b"preserve")
                self.assertTrue(os.path.lexists(child))

    def test_cleanup_replacement_after_capture_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            child = work / "state"
            child.write_bytes(b"owned")
            manifest = demo._capture_owned_tree(
                work,
                expected_root_identity=demo._directory_identity(
                    work, code="cleanup_failed"
                ),
            )
            child.unlink()
            child.write_bytes(b"replacement")
            with self.assertRaises(demo.BubbleRanDefenseError) as captured:
                demo._remove_captured_tree(manifest)
            self.assertEqual(captured.exception.code, "cleanup_failed")
            self.assertEqual(child.read_bytes(), b"replacement")
            self.assertTrue(work.is_dir())

    def test_scenario_failure_preserves_unmanifested_unknown_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fail_after_write(*, work_directory: Path, **_kwargs):  # noqa: ANN001
                (work_directory / "unknown-state").write_bytes(b"preserve")
                raise demo.BubbleRanDefenseError("contract_failed")

            code, payload, error = invoke(
                root,
                "--offline",
                "--approve-local-simulation",
                scenario=fail_after_write,
            )
            self.assertEqual(code, 2)
            self.assertIsNone(payload)
            self.assertEqual(error["error"]["code"], "contract_failed")
            run_root = root / ".local" / "networkagent-bubbleran-defense"
            work = next(run_root.iterdir()) / "work"
            self.assertEqual((work / "unknown-state").read_bytes(), b"preserve")

    def test_report_collision_and_unknown_temporary_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, token = demo._create_run_directory(
                root,
                utc_now=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
                random_token=lambda: "abcdef123456",
            )
            final = run.directory / demo.REPORT_NAME
            final.write_bytes(b"existing")
            with self.assertRaises(demo.BubbleRanDefenseError):
                demo._write_report(run, {"ok": True}, token=token)
            self.assertEqual(final.read_bytes(), b"existing")

            final.unlink()
            original = demo._path_file_identity
            failed = False

            def fail_once(path: Path, *, code: str):
                nonlocal failed
                if path.name.startswith(f".{demo.REPORT_NAME}") and not failed:
                    failed = True
                    raise demo.BubbleRanDefenseError(code)
                return original(path, code=code)

            with mock.patch.object(demo, "_path_file_identity", fail_once):
                with self.assertRaises(demo.BubbleRanDefenseError) as captured:
                    demo._write_report(run, {"ok": True}, token=token)
            self.assertEqual(captured.exception.code, "report_write_failed")
            temporary_path = run.directory / f".{demo.REPORT_NAME}.{token}.tmp"
            self.assertTrue(temporary_path.is_file())
            self.assertEqual(temporary_path.read_bytes(), b"")

    def test_report_links_and_same_content_replacement_are_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for kind in ("symlink", "hardlink"):
                run, token = demo._create_run_directory(
                    root,
                    utc_now=lambda: datetime.now(UTC),
                    random_token=lambda: (
                        "111111111111" if kind == "symlink" else "222222222222"
                    ),
                )
                outside = root / f"outside-{kind}"
                outside.write_bytes(b"preserve")
                final = run.directory / demo.REPORT_NAME
                try:
                    if kind == "symlink":
                        final.symlink_to(outside)
                    else:
                        os.link(outside, final)
                except OSError:
                    continue
                with self.assertRaises(demo.BubbleRanDefenseError):
                    demo._write_report(run, {"ok": True}, token=token)
                self.assertEqual(outside.read_bytes(), b"preserve")
                self.assertTrue(os.path.lexists(final))

            run, token = demo._create_run_directory(
                root,
                utc_now=lambda: datetime.now(UTC),
                random_token=lambda: "333333333333",
            )
            size, _digest, identity = demo._write_report(run, {"ok": True}, token=token)
            final = run.directory / demo.REPORT_NAME
            content = final.read_bytes()
            replacement = run.directory / "replacement"
            replacement.write_bytes(content)
            os.replace(replacement, final)
            with self.assertRaises(demo.BubbleRanDefenseError) as captured:
                demo._read_identity_bound_file(
                    final,
                    expected_identity=identity,
                    maximum_bytes=size,
                    code="report_write_failed",
                )
            self.assertEqual(captured.exception.code, "report_write_failed")
            self.assertEqual(final.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
