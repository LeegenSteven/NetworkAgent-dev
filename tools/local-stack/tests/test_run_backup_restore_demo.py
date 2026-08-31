from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).parents[1] / "run_backup_restore_demo.py"
SPEC = importlib.util.spec_from_file_location(
    "networkagent_backup_restore_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
backup_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_demo)

DEFENSE_TEST_PATH = Path(__file__).with_name("test_run_defense_demo.py")
DEFENSE_TEST_SPEC = importlib.util.spec_from_file_location(
    "networkagent_backup_fake_defense", DEFENSE_TEST_PATH
)
assert DEFENSE_TEST_SPEC is not None and DEFENSE_TEST_SPEC.loader is not None
fake_defense = importlib.util.module_from_spec(DEFENSE_TEST_SPEC)
DEFENSE_TEST_SPEC.loader.exec_module(fake_defense)


def _document(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def _run_identity(path: Path) -> tuple[int, int]:
    return backup_demo._plain_directory_identity(path, error_code="report_write_failed")


class FakeBackupRunner(fake_defense.FakeRunner):
    def __init__(
        self,
        *,
        heads: tuple[str, str] = ("a" * 40, "a" * 40),
        statuses: tuple[bytes, bytes] = (b"", b""),
        restore_changed: tuple[bool, bool] = (True, False),
        corrupt_restore_mutates: bool = False,
    ) -> None:
        super().__init__(heads=heads, statuses=statuses)
        self.restore_changed = iter(restore_changed)
        self.corrupt_restore_mutates = corrupt_restore_mutates
        self.valid_restore_calls = 0

    @staticmethod
    def _workspace(arguments: tuple[str, ...]) -> Path:
        return Path(arguments[arguments.index("--workspace") + 1])

    @staticmethod
    def _database(workspace: Path) -> Path:
        return workspace / "state" / "networkagent.duckdb"

    @staticmethod
    def _catalog() -> dict[str, int]:
        return {"schema_count": 2, "table_count": 2, "view_count": 0}

    @staticmethod
    def _tables() -> list[dict[str, object]]:
        return [
            {"name": "incident_audit_events", "rows": 8, "schema": "main"},
            {"name": "incidents", "rows": 1, "schema": "main"},
        ]

    def _backup_payload(self, *, changed: bool) -> dict[str, object]:
        database = b"fixed-success-lifecycle-database\n"
        manifest = b'{"safe":"manifest"}\n'
        return {
            "ok": True,
            "command": "backup",
            "result": {
                "schema": "networkagent-local-cold-backup/1.0",
                "changed": changed,
                "manifest": {
                    "filename": "backup-manifest.json",
                    "bytes": len(manifest),
                    "sha256": hashlib.sha256(manifest).hexdigest(),
                },
                "database": {
                    "filename": "networkagent.duckdb",
                    "bytes": len(database),
                    "sha256": hashlib.sha256(database).hexdigest(),
                },
                "catalog": self._catalog(),
                "tables": self._tables(),
                "row_count": 9,
                "checkpointed": True,
                "local_ownership_sha256": "0" * 64,
                "logical_equivalence": True,
            },
        }

    def _restore_payload(self, *, changed: bool) -> dict[str, object]:
        backup = self._backup_payload(changed=True)["result"]
        assert isinstance(backup, dict)
        manifest = backup["manifest"]
        database = backup["database"]
        assert isinstance(manifest, dict) and isinstance(database, dict)
        return {
            "ok": True,
            "command": "restore",
            "result": {
                "schema": "networkagent-local-cold-backup/1.0",
                "changed": changed,
                "manifest_sha256": manifest["sha256"],
                "database_sha256": database["sha256"],
                "catalog": self._catalog(),
                "tables": self._tables(),
                "row_count": 9,
                "verified": True,
            },
        }

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        if arguments and arguments[0] == sys.executable:
            workspace = self._workspace(arguments)
            database = self._database(workspace)
            if arguments[-1] == "init" and workspace.exists():
                self.calls.append(arguments)
                self.environments.append(dict(env))
                assert cwd.is_absolute() and timeout == 60
                assert not tuple(workspace.iterdir())
                (workspace / ".local-stack.json").write_text("{}", encoding="utf-8")
                database.parent.mkdir()
                (workspace / "artifacts").mkdir()
                database.write_bytes(b"fresh-initialized-database\n")
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    _document(
                        {
                            "ok": True,
                            "command": "init",
                            "database": {
                                "performance_rows": 13_440,
                                "trace_rows": 579,
                                "incident_rows": 0,
                            },
                        }
                    ),
                    b"",
                )
            if "backup" in arguments:
                self.calls.append(arguments)
                self.environments.append(dict(env))
                assert cwd.is_absolute() and timeout == 60
                destination = Path(arguments[arguments.index("--destination") + 1])
                assert not destination.exists()
                destination.mkdir(parents=False)
                payload = self._backup_payload(changed=True)
                result = payload["result"]
                assert isinstance(result, dict)
                manifest = result["manifest"]
                assert isinstance(manifest, dict)
                (destination / "networkagent.duckdb").write_bytes(
                    b"fixed-success-lifecycle-database\n"
                )
                (destination / "backup-manifest.json").write_bytes(
                    b'{"safe":"manifest"}\n'
                )
                identity = backup_demo._capture_backup_tree_identity(
                    destination, error_code="recovery_contract_failed"
                )
                result["local_ownership_sha256"] = backup_demo._local_ownership_sha256(
                    identity
                )
                assert (
                    manifest["sha256"]
                    == hashlib.sha256(
                        (destination / "backup-manifest.json").read_bytes()
                    ).hexdigest()
                )
                return subprocess.CompletedProcess(
                    arguments, 0, _document(payload), b""
                )
            if "restore" in arguments:
                self.calls.append(arguments)
                self.environments.append(dict(env))
                assert cwd.is_absolute() and timeout == 60
                expected = arguments[arguments.index("--expected-manifest-sha256") + 1]
                source = Path(arguments[arguments.index("--source") + 1])
                valid = self._backup_payload(changed=True)["result"]
                assert isinstance(valid, dict)
                manifest = valid["manifest"]
                assert isinstance(manifest, dict)
                if source.name == "corrupt-backup":
                    if self.corrupt_restore_mutates:
                        database.write_bytes(b"unsafe-mutation")
                    return subprocess.CompletedProcess(
                        arguments,
                        2,
                        b"",
                        _document(
                            {
                                "error": {
                                    "code": "backup_invalid",
                                    "message": (
                                        "backup contents failed integrity validation"
                                    ),
                                },
                                "ok": False,
                            }
                        ),
                    )
                assert expected == manifest["sha256"]
                changed = next(self.restore_changed)
                self.valid_restore_calls += 1
                database.write_bytes(b"fixed-success-lifecycle-database\n")
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    _document(self._restore_payload(changed=changed)),
                    b"",
                )
            if arguments[-2:] == ("reset", "--yes"):
                self.calls.append(arguments)
                self.environments.append(dict(env))
                assert timeout == 60
                if database.exists():
                    database.unlink()
                if database.parent.exists():
                    database.parent.rmdir()
                artifacts = workspace / "artifacts"
                if artifacts.exists():
                    artifacts.rmdir()
                marker = workspace / ".local-stack.json"
                if marker.exists():
                    marker.unlink()
                workspace.rmdir()
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    _document(
                        {
                            "command": "reset",
                            "ok": True,
                            "reset": True,
                            "removed": ["state", "artifacts", "marker"],
                            "workspace_removed": True,
                            "preserved_unknown_entries": False,
                        }
                    ),
                    b"",
                )

        completed = super().__call__(arguments, cwd=cwd, env=env, timeout=timeout)
        if arguments and arguments[0] == sys.executable:
            workspace = self._workspace(arguments)
            database = self._database(workspace)
            if arguments[-1] == "init":
                database.parent.mkdir()
                (workspace / "artifacts").mkdir()
                database.write_bytes(b"fresh-initialized-database\n")
            elif "--approve-action" in arguments:
                database.write_bytes(b"fixed-success-lifecycle-database\n")
        return completed


def _fake_backup_tree(
    root: Path,
) -> tuple[Path, dict[str, object], object]:
    runner = FakeBackupRunner()
    payload = runner._backup_payload(changed=True)
    result = payload["result"]
    assert isinstance(result, dict)
    backup_directory = root / "backup"
    backup_directory.mkdir()
    (backup_directory / "networkagent.duckdb").write_bytes(
        b"fixed-success-lifecycle-database\n"
    )
    (backup_directory / "backup-manifest.json").write_bytes(b'{"safe":"manifest"}\n')
    result["local_ownership_sha256"] = backup_demo._local_ownership_sha256(
        backup_demo._capture_backup_tree_identity(
            backup_directory, error_code="recovery_contract_failed"
        )
    )
    identity = backup_demo._validate_backup_files(backup_directory, result)
    return backup_directory, result, identity


def _invoke(
    tmp_path: Path,
    runner: FakeBackupRunner,
    *arguments: str,
) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = backup_demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        process_runner=runner,
        repository_root=tmp_path,
        utc_now=lambda: datetime(2026, 8, 31, 4, 5, 6, tzinfo=UTC),
        random_token=lambda: "bacc0ffee123",
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def _replace_workspace_with_marker_owned_racer(
    workspace: Path, parked: Path
) -> tuple[bytes, bytes]:
    marker_bytes = (workspace / ".local-stack.json").read_bytes()
    database_bytes = b"unowned-racer-database\n"
    workspace.replace(parked)
    workspace.mkdir()
    (workspace / "state").mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "state" / "networkagent.duckdb").write_bytes(database_bytes)
    (workspace / ".local-stack.json").write_bytes(marker_bytes)
    return marker_bytes, database_bytes


def test_fixed_success_recovery_is_private_equivalent_idempotent_and_clean(
    tmp_path: Path,
) -> None:
    runner = FakeBackupRunner()

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 0 and error is None
    assert payload["schema"] == "networkagent-local-backup-recovery/1.0"
    assert payload["ok"] is True
    assert payload["classification"] == "LOCAL_COLD_BACKUP_RECOVERY_EVIDENCE"
    assert payload["source"] == {
        "binding_stable": True,
        "commit_bound": True,
        "commit_sha": "a" * 40,
        "git_available": True,
        "tracked_clean": True,
    }
    assert payload["scope"] == {
        "backup_mode": "COLD_OFFLINE",
        "database_engine": "DUCKDB",
        "execution_mode": "LOCAL_SINGLE_PROCESS",
        "restore_target": "RESET_FRESH_INITIALIZATION",
        "writer_stopped": True,
    }
    assert payload["coverage"] == {
        "delivered": {
            "checkpointed_full_database_backup": True,
            "corrupt_backup_rejection": True,
            "lifecycle_equivalence": True,
            "manifest_hash_binding": True,
            "reset_fresh_init_restore": True,
            "restore_idempotency": True,
            "successful_run_workspace_backup_cleanup": True,
        },
        "not_claimed": backup_demo._NOT_CLAIMED,
    }
    assert payload["proof"] == {
        "backup_changed": True,
        "backup_file_count": 2,
        "catalog_equivalent": True,
        "corrupt_backup_rejected": True,
        "fresh_database_unchanged_after_rejection": True,
        "lifecycle_projection_equivalent": True,
        "restore_changed": True,
        "restore_retry_changed": False,
        "restore_retry_equivalent": True,
        "row_count_equivalent": True,
    }
    assert payload["privacy"] == {
        "absolute_paths_recorded": False,
        "backup_identifiers_recorded": False,
        "child_stderr_recorded": False,
        "child_stdout_recorded": False,
        "database_bytes_recorded": False,
        "database_digests_recorded": False,
        "database_filenames_recorded": False,
        "domain_identifiers_recorded": False,
        "environment_recorded": False,
        "manifest_content_recorded": False,
        "manifest_digests_recorded": False,
        "raw_arguments_recorded": False,
        "status": "PASS",
        "workspace_identifiers_recorded": False,
    }

    forbidden = {
        "absolute_path",
        "action_hash",
        "action_id",
        "approval_id",
        "backup_id",
        "database",
        "database_sha256",
        "destination",
        "event_id",
        "incident_id",
        "manifest",
        "manifest_sha256",
        "relative_path",
        "request_id",
        "source_path",
        "trace_id",
        "workspace",
        "workspace_id",
    }
    persisted = {key: value for key, value in payload.items() if key != "report"}
    assert _nested_keys(persisted).isdisjoint(forbidden)
    assert str(tmp_path) not in json.dumps(payload)
    assert "11111111-1111-4111-8111-111111111111" not in json.dumps(payload)
    assert "fixed-success-lifecycle-database" not in json.dumps(payload)
    assert "local_ownership_sha256" not in json.dumps(payload)
    assert 'safe":"manifest' not in json.dumps(payload)

    assert set(payload["report"]) == {"bytes", "filename", "sha256"}
    assert payload["report"]["filename"] == "local-backup-recovery-report.json"
    reports = list(
        (tmp_path / ".local" / "networkagent-defense").glob(
            "*/local-backup-recovery-report.json"
        )
    )
    assert len(reports) == 1
    report_path = reports[0]
    assert not report_path.is_symlink()
    assert stat.S_ISREG(os.lstat(report_path).st_mode)
    report_bytes = report_path.read_bytes()
    assert len(report_bytes) == payload["report"]["bytes"]
    assert hashlib.sha256(report_bytes).hexdigest() == payload["report"]["sha256"]
    assert json.loads(report_bytes) == persisted

    run_directory = report_path.parent
    assert {child.name for child in run_directory.iterdir()} == {
        "local-backup-recovery-report.json"
    }
    assert runner.valid_restore_calls == 2
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert sum("backup" in call for call in local_calls) == 1
    assert sum("restore" in call for call in local_calls) == 3
    assert sum(call[-1] == "init" for call in local_calls) == 2
    assert sum(call[-2:] == ("reset", "--yes") for call in local_calls) == 2
    assert all(
        environment["PYTHONDONTWRITEBYTECODE"] == "1"
        for environment in runner.environments
    )


def test_corrupt_backup_must_not_mutate_the_fresh_database(tmp_path: Path) -> None:
    runner = FakeBackupRunner(corrupt_restore_mutates=True)

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error == {
        "error": {
            "code": "recovery_contract_failed",
            "message": "local backup recovery evidence violated its fixed contract",
        },
        "ok": False,
        "schema": "networkagent-local-backup-recovery/1.0",
    }
    assert str(tmp_path) not in json.dumps(error)
    assert not list(tmp_path.rglob("networkagent.duckdb"))
    assert not list(tmp_path.rglob("backup-manifest.json"))


def test_restore_changed_sequence_is_exact_and_failure_still_cleans(
    tmp_path: Path,
) -> None:
    runner = FakeBackupRunner(restore_changed=(False, False))

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "recovery_contract_failed"
    assert not list(tmp_path.rglob("networkagent.duckdb"))
    assert not list(tmp_path.rglob("backup-manifest.json"))


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ((), "confirmation_required"),
        (("--unexpected",), "invalid_arguments"),
        (("--approve-local-simulation", "extra"), "invalid_arguments"),
    ],
)
def test_confirmation_and_arguments_fail_closed_without_writes(
    tmp_path: Path,
    arguments: tuple[str, ...],
    code: str,
) -> None:
    runner = FakeBackupRunner()

    exit_code, payload, error = _invoke(tmp_path, runner, *arguments)

    assert (exit_code, payload) == (2, None)
    assert error["error"]["code"] == code
    assert not runner.calls
    assert not (tmp_path / ".local").exists()


def test_source_drift_downgrades_without_false_commit_binding(tmp_path: Path) -> None:
    runner = FakeBackupRunner(heads=("a" * 40, "b" * 40))

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 0 and error is None
    assert payload["classification"] == ("LOCAL_WORKTREE_COLD_BACKUP_RECOVERY_EVIDENCE")
    assert payload["source"]["commit_bound"] is False
    assert payload["source"]["binding_stable"] is False


def test_canonical_report_size_is_bounded() -> None:
    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._canonical_bytes({"value": "x" * (64 * 1024)})
    assert caught.value.code == "report_write_failed"


def test_evidence_reader_rejects_oversized_and_hardlinked_files(
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (backup_demo.MAX_BACKUP_MANIFEST_BYTES + 1))
    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._file_digest(
            oversized,
            maximum_bytes=backup_demo.MAX_BACKUP_MANIFEST_BYTES,
        )
    assert caught.value.code == "recovery_contract_failed"

    original = tmp_path / "original.bin"
    linked = tmp_path / "linked.bin"
    original.write_bytes(b"bounded")
    try:
        os.link(original, linked)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")
    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._file_digest(linked, maximum_bytes=16)
    assert caught.value.code == "recovery_contract_failed"


def test_failed_report_publication_preserves_link_after_identity_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    monkeypatch.setattr(os.path, "samefile", lambda _left, _right: False)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    assert {child.name for child in run_directory.iterdir()} == {
        backup_demo.REPORT_NAME
    }


def test_report_collision_never_deletes_an_unowned_file(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    existing = run_directory / backup_demo.REPORT_NAME
    existing.write_bytes(b"user-owned-collision")

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    assert existing.read_bytes() == b"user-owned-collision"


def test_backup_directory_replacement_is_preserved_on_cleanup(tmp_path: Path) -> None:
    backup_directory, _backup, identity = _fake_backup_tree(tmp_path)
    parked = tmp_path / "parked-backup"
    backup_directory.replace(parked)
    backup_directory.mkdir()
    (backup_directory / "backup-manifest.json").write_bytes(b"unowned-racer")
    (backup_directory / "networkagent.duckdb").write_bytes(b"unowned-racer")

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._remove_backup_directory(backup_directory, identity)

    assert caught.value.code == "cleanup_failed"
    assert (backup_directory / "backup-manifest.json").read_bytes() == (
        b"unowned-racer"
    )
    assert (backup_directory / "networkagent.duckdb").read_bytes() == (b"unowned-racer")
    assert parked.is_dir()


def test_backup_child_replacement_is_preserved_on_cleanup(tmp_path: Path) -> None:
    backup_directory, _backup, identity = _fake_backup_tree(tmp_path)
    database = backup_directory / "networkagent.duckdb"
    parked = tmp_path / "parked-database"
    database.replace(parked)
    database.write_bytes(b"unowned-racer")

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._remove_backup_directory(backup_directory, identity)

    assert caught.value.code == "cleanup_failed"
    assert database.read_bytes() == b"unowned-racer"
    assert (backup_directory / "backup-manifest.json").is_file()
    assert parked.is_file()


def test_same_inode_with_different_ctime_is_never_unlinked(tmp_path: Path) -> None:
    target = tmp_path / "owned-report.tmp"
    target.write_bytes(b"unowned-racer")
    current = backup_demo._file_identity(
        os.lstat(target), error_code="report_write_failed"
    )
    expected = (*current[:4], current[4] + 1, current[5])

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._unlink_owned_file(
            target, expected, error_code="report_write_failed"
        )

    assert caught.value.code == "report_write_failed"
    assert target.read_bytes() == b"unowned-racer"


def test_backup_same_inode_with_different_ctime_is_not_owned(
    tmp_path: Path,
) -> None:
    backup_directory, _backup, identity = _fake_backup_tree(tmp_path)
    directory_identity, file_identities = identity
    changed_files = []
    for filename, file_identity in file_identities:
        changed_files.append(
            (
                filename,
                (
                    *file_identity[:4],
                    file_identity[4] + (1 if filename == "networkagent.duckdb" else 0),
                    file_identity[5],
                ),
            )
        )
    raced_identity = (directory_identity, tuple(changed_files))

    assert backup_demo._local_ownership_sha256(identity) != (
        backup_demo._local_ownership_sha256(raced_identity)
    )
    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._remove_backup_directory(backup_directory, raced_identity)

    assert caught.value.code == "cleanup_failed"
    assert (backup_directory / "backup-manifest.json").is_file()
    assert (backup_directory / "networkagent.duckdb").is_file()


def test_backup_response_window_exact_copy_replacement_is_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeBackupRunner()
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    backup_directory = run_directory / "backup"
    parked = tmp_path / "parked-original-backup"
    real_call = backup_demo._call_stack
    injected = False

    def replace_after_backup(
        process_runner: object,
        arguments: tuple[str, ...],
        *,
        repository_root: Path,
        environment: dict[str, str],
    ) -> object:
        nonlocal injected
        result = real_call(
            process_runner,
            arguments,
            repository_root=repository_root,
            environment=environment,
        )
        if "backup" in arguments and not injected:
            injected = True
            backup_directory.replace(parked)
            backup_directory.mkdir()
            for filename in ("backup-manifest.json", "networkagent.duckdb"):
                (backup_directory / filename).write_bytes(
                    (parked / filename).read_bytes()
                )
        return result

    monkeypatch.setattr(backup_demo, "_call_stack", replace_after_backup)

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "cleanup_failed"
    assert injected is True
    for filename in ("backup-manifest.json", "networkagent.duckdb"):
        assert (backup_directory / filename).read_bytes() == (
            parked / filename
        ).read_bytes()
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert sum("restore" in call for call in local_calls) == 0
    assert sum(call[-2:] == ("reset", "--yes") for call in local_calls) == 1


@pytest.mark.parametrize(
    "ownership",
    [True, 1, "A" * 64, "0" * 63, "0" * 65],
)
def test_backup_local_ownership_binding_is_strict(ownership: object) -> None:
    payload = FakeBackupRunner()._backup_payload(changed=True)
    result = payload["result"]
    assert isinstance(result, dict)
    result["local_ownership_sha256"] = ownership

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._validate_backup(payload)

    assert caught.value.code == "recovery_contract_failed"


def test_corrupt_copy_directory_replacement_is_preserved_and_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_directory, _backup, source_identity = _fake_backup_tree(tmp_path)
    corrupt_directory = tmp_path / "corrupt-backup"
    parked = tmp_path / "parked-corrupt-backup"
    real_open = os.open
    injected = False

    def replace_before_mutation(
        path: str | os.PathLike[str], flags: int, mode: int = 0o777
    ) -> int:
        nonlocal injected
        candidate = Path(path)
        if (
            candidate == corrupt_directory / "networkagent.duckdb"
            and flags & os.O_RDWR
            and not injected
        ):
            injected = True
            corrupt_directory.replace(parked)
            corrupt_directory.mkdir()
            (corrupt_directory / "backup-manifest.json").write_bytes(b"unowned-racer")
            (corrupt_directory / "networkagent.duckdb").write_bytes(b"unowned-racer")
        return real_open(path, flags, mode)

    monkeypatch.setattr(backup_demo.os, "open", replace_before_mutation)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._copy_corrupt_backup(
            backup_directory,
            corrupt_directory,
            source_identity=source_identity,
        )

    assert caught.value.code == "recovery_contract_failed"
    assert injected is True
    assert (corrupt_directory / "networkagent.duckdb").read_bytes() == (
        b"unowned-racer"
    )
    assert (corrupt_directory / "backup-manifest.json").read_bytes() == (
        b"unowned-racer"
    )
    assert parked.is_dir()


@pytest.mark.parametrize("failure", ["initial_identity", "write"])
def test_early_report_failure_cleanup_requires_full_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    if failure == "initial_identity":
        real_fstat = os.fstat

        def invalid_links(descriptor: int) -> SimpleNamespace:
            details = real_fstat(descriptor)
            return SimpleNamespace(
                st_dev=details.st_dev,
                st_ino=details.st_ino,
                st_mode=details.st_mode,
                st_nlink=2,
            )

        monkeypatch.setattr(backup_demo.os, "fstat", invalid_links)
    else:

        def failed_fsync(_descriptor: int) -> None:
            raise OSError("injected write failure")

        monkeypatch.setattr(backup_demo.os, "fsync", failed_fsync)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    remaining = {child.name for child in run_directory.iterdir()}
    if failure == "initial_identity":
        assert remaining == {".local-backup-recovery-report.json.abc123def456.tmp"}
    else:
        assert not remaining


def test_transient_first_fstat_failure_recovers_identity_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_fstat = os.fstat
    calls = 0

    def failed_once(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected transient fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(backup_demo.os, "fstat", failed_once)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    assert calls == 2
    assert not tuple(run_directory.iterdir())


def test_persistent_first_fstat_failure_preserves_unknown_diagnostic_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    def failed_fstat(_descriptor: int) -> os.stat_result:
        raise OSError("injected persistent fstat failure")

    monkeypatch.setattr(backup_demo.os, "fstat", failed_fstat)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    assert {child.name for child in run_directory.iterdir()} == {
        ".local-backup-recovery-report.json.abc123def456.tmp"
    }


def test_first_fstat_replacement_attack_preserves_unowned_racer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    temporary = run_directory / ".local-backup-recovery-report.json.abc123def456.tmp"
    parked = run_directory / "parked-original.tmp"
    real_fstat = os.fstat
    injected = False

    def replace_then_fail(descriptor: int) -> os.stat_result:
        nonlocal injected
        if not injected:
            injected = True
            os.close(descriptor)
            temporary.replace(parked)
            temporary.write_bytes(b"unowned-racer")
            raise OSError("injected replacement race")
        return real_fstat(descriptor)

    monkeypatch.setattr(backup_demo.os, "fstat", replace_then_fail)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    assert injected is True
    assert temporary.read_bytes() == b"unowned-racer"
    assert parked.is_file()


def test_report_publish_run_replacement_preserves_unowned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    run_identity = _run_identity(run_directory)
    parked = tmp_path / "parked-run"
    replacement_identity: tuple[int, int] | None = None

    def replace_before_link(
        _source: str | os.PathLike[str],
        _target: str | os.PathLike[str],
        *,
        follow_symlinks: bool,
    ) -> None:
        nonlocal replacement_identity
        assert follow_symlinks is False
        run_directory.replace(parked)
        run_directory.mkdir()
        replacement = os.lstat(run_directory)
        replacement_identity = (replacement.st_dev, replacement.st_ino)
        raise OSError("injected run replacement")

    monkeypatch.setattr(backup_demo.os, "link", replace_before_link)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=run_identity,
        )

    assert caught.value.code == "report_write_failed"
    assert replacement_identity is not None
    current = os.lstat(run_directory)
    assert (current.st_dev, current.st_ino) == replacement_identity
    assert run_directory.is_dir() and not tuple(run_directory.iterdir())
    assert parked.is_dir()


def test_report_final_replacement_after_digest_is_rejected_and_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    final_path = run_directory / backup_demo.REPORT_NAME
    parked = run_directory / "parked-owned-report.json"
    real_digest = backup_demo._file_digest
    injected = False

    def replace_after_digest(
        path: Path, *, maximum_bytes: int, error_code: str = "recovery_contract_failed"
    ) -> tuple[int, str]:
        nonlocal injected
        result = real_digest(path, maximum_bytes=maximum_bytes, error_code=error_code)
        if path == final_path and not injected:
            injected = True
            final_path.replace(parked)
            final_path.write_bytes(b"unowned-racer")
        return result

    monkeypatch.setattr(backup_demo, "_file_digest", replace_after_digest)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._write_report(
            run_directory,
            {"ok": True, "schema": backup_demo.SCHEMA},
            token="abc123def456",
            run_identity=_run_identity(run_directory),
        )

    assert caught.value.code == "report_write_failed"
    assert injected is True
    assert final_path.read_bytes() == b"unowned-racer"
    assert parked.is_file()


def test_run_directory_reparse_validation_failure_preserves_diagnostic_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    real_is_link_like = backup_demo._is_link_like

    def injected_reparse_failure(path: Path) -> bool:
        if path == expected and path.exists():
            return True
        return real_is_link_like(path)

    monkeypatch.setattr(backup_demo, "_is_link_like", injected_reparse_failure)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._create_run_directory(
            tmp_path,
            utc_now=lambda: datetime(2026, 8, 31, 4, 5, 6, tzinfo=UTC),
            random_token=lambda: "bacc0ffee123",
        )

    assert caught.value.code == "report_write_failed"
    assert expected.is_dir()
    assert not tuple(expected.iterdir())


def test_first_run_directory_lstat_failure_preserves_unknown_diagnostic_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    real_lstat = os.lstat
    injected = False

    def failed_first_target_lstat(path: str | os.PathLike[str]) -> os.stat_result:
        nonlocal injected
        if Path(path) == expected and not injected:
            injected = True
            raise OSError("injected first lstat failure")
        return real_lstat(path)

    monkeypatch.setattr(backup_demo.os, "lstat", failed_first_target_lstat)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._create_run_directory(
            tmp_path,
            utc_now=lambda: datetime(2026, 8, 31, 4, 5, 6, tzinfo=UTC),
            random_token=lambda: "bacc0ffee123",
        )

    assert caught.value.code == "report_write_failed"
    assert injected is True
    assert expected.is_dir()
    assert not tuple(expected.iterdir())


def test_first_run_lstat_replacement_attack_preserves_unowned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    parked = expected.parent / "parked-original"
    real_lstat = os.lstat
    replacement_identity: tuple[int, int] | None = None
    injected = False

    def replace_then_fail(path: str | os.PathLike[str]) -> os.stat_result:
        nonlocal injected, replacement_identity
        if Path(path) == expected and not injected:
            injected = True
            expected.replace(parked)
            expected.mkdir()
            replacement = real_lstat(expected)
            replacement_identity = (replacement.st_dev, replacement.st_ino)
            raise OSError("injected directory replacement race")
        return real_lstat(path)

    monkeypatch.setattr(backup_demo.os, "lstat", replace_then_fail)

    with pytest.raises(backup_demo.BackupRecoveryError) as caught:
        backup_demo._create_run_directory(
            tmp_path,
            utc_now=lambda: datetime(2026, 8, 31, 4, 5, 6, tzinfo=UTC),
            random_token=lambda: "bacc0ffee123",
        )

    assert caught.value.code == "report_write_failed"
    assert injected is True and replacement_identity is not None
    current = real_lstat(expected)
    assert (current.st_dev, current.st_ino) == replacement_identity
    assert expected.is_dir() and not tuple(expected.iterdir())
    assert parked.is_dir()


def test_operation_error_cleanup_preserves_replaced_empty_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    parked = expected.parent / "parked-operation-run"
    replacement_identity: tuple[int, int] | None = None

    def replace_then_fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal replacement_identity
        expected.replace(parked)
        expected.mkdir()
        replacement = os.lstat(expected)
        replacement_identity = (replacement.st_dev, replacement.st_ino)
        raise backup_demo.BackupRecoveryError("command_failed")

    monkeypatch.setattr(backup_demo, "_call_stack", replace_then_fail)

    code, payload, error = _invoke(
        tmp_path, FakeBackupRunner(), "--approve-local-simulation"
    )

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "cleanup_failed"
    assert replacement_identity is not None
    current = os.lstat(expected)
    assert (current.st_dev, current.st_ino) == replacement_identity
    assert expected.is_dir() and not tuple(expected.iterdir())
    assert parked.is_dir()


def test_workspace_replacement_before_restore_prevents_restore_and_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeBackupRunner()
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    workspace = run_directory / "success"
    parked = tmp_path / "parked-before-restore-workspace"
    real_copy = backup_demo._copy_corrupt_backup
    racer: tuple[bytes, bytes] | None = None

    def replace_after_copy(
        source: Path, destination: Path, *, source_identity: object
    ) -> object:
        nonlocal racer
        result = real_copy(source, destination, source_identity=source_identity)
        racer = _replace_workspace_with_marker_owned_racer(workspace, parked)
        return result

    monkeypatch.setattr(backup_demo, "_copy_corrupt_backup", replace_after_copy)

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "cleanup_failed"
    assert racer is not None
    marker_bytes, database_bytes = racer
    assert (workspace / ".local-stack.json").read_bytes() == marker_bytes
    assert (workspace / "state" / "networkagent.duckdb").read_bytes() == (
        database_bytes
    )
    assert parked.is_dir()
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert sum("restore" in call for call in local_calls) == 0
    assert sum(call[-2:] == ("reset", "--yes") for call in local_calls) == 1


def test_workspace_replacement_before_branch_return_is_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeBackupRunner()
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    workspace = run_directory / "success"
    parked = tmp_path / "parked-before-branch-return-workspace"
    real_branch = backup_demo.defense_demo._run_branch
    racer: tuple[bytes, bytes] | None = None

    def replace_before_return(*args: object, **kwargs: object) -> object:
        nonlocal racer
        result = real_branch(*args, **kwargs)
        racer = _replace_workspace_with_marker_owned_racer(workspace, parked)
        return result

    monkeypatch.setattr(backup_demo.defense_demo, "_run_branch", replace_before_return)

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "cleanup_failed"
    assert racer is not None
    marker_bytes, database_bytes = racer
    assert (workspace / ".local-stack.json").read_bytes() == marker_bytes
    assert (workspace / "state" / "networkagent.duckdb").read_bytes() == (
        database_bytes
    )
    assert parked.is_dir()
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert sum("restore" in call for call in local_calls) == 0
    assert sum(call[-2:] == ("reset", "--yes") for call in local_calls) == 0


def test_workspace_replacement_before_fresh_init_return_is_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeBackupRunner()
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    workspace = run_directory / "success"
    parked = tmp_path / "parked-before-fresh-init-return-workspace"
    real_call = backup_demo._call_stack
    init_calls = 0
    racer: tuple[bytes, bytes] | None = None

    def replace_before_return(
        process_runner: object,
        arguments: tuple[str, ...],
        *,
        repository_root: Path,
        environment: dict[str, str],
    ) -> object:
        nonlocal init_calls, racer
        result = real_call(
            process_runner,
            arguments,
            repository_root=repository_root,
            environment=environment,
        )
        if arguments[-1] == "init":
            init_calls += 1
            if init_calls == 1:
                racer = _replace_workspace_with_marker_owned_racer(workspace, parked)
        return result

    monkeypatch.setattr(backup_demo, "_call_stack", replace_before_return)

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "cleanup_failed"
    assert init_calls == 1 and racer is not None
    marker_bytes, database_bytes = racer
    assert (workspace / ".local-stack.json").read_bytes() == marker_bytes
    assert (workspace / "state" / "networkagent.duckdb").read_bytes() == (
        database_bytes
    )
    assert parked.is_dir()
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert sum("restore" in call for call in local_calls) == 0
    assert sum(call[-2:] == ("reset", "--yes") for call in local_calls) == 1


def test_workspace_replacement_before_finally_reset_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeBackupRunner()
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    workspace = run_directory / "success"
    parked = tmp_path / "parked-before-finally-workspace"
    racer: tuple[bytes, bytes] | None = None

    def replace_then_fail(*_args: object, **_kwargs: object) -> None:
        nonlocal racer
        racer = _replace_workspace_with_marker_owned_racer(workspace, parked)
        raise backup_demo.BackupRecoveryError("command_failed")

    monkeypatch.setattr(
        backup_demo, "_call_expected_restore_rejection", replace_then_fail
    )

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "cleanup_failed"
    assert racer is not None
    marker_bytes, database_bytes = racer
    assert (workspace / ".local-stack.json").read_bytes() == marker_bytes
    assert (workspace / "state" / "networkagent.duckdb").read_bytes() == (
        database_bytes
    )
    assert parked.is_dir()
    local_calls = [call for call in runner.calls if call[0] == sys.executable]
    assert sum("restore" in call for call in local_calls) == 0
    assert sum(call[-2:] == ("reset", "--yes") for call in local_calls) == 1


def test_report_error_cleanup_preserves_replaced_empty_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T040506Z-bacc0ffee123"
    )
    parked = expected.parent / "parked-report-run"
    replacement_identity: tuple[int, int] | None = None

    def replace_then_fail(
        run_directory: Path,
        _report: object,
        *,
        token: str,
        run_identity: tuple[int, int],
    ) -> tuple[int, str]:
        nonlocal replacement_identity
        assert run_directory == expected
        assert token == "bacc0ffee123"
        assert run_identity == _run_identity(run_directory)
        run_directory.replace(parked)
        run_directory.mkdir()
        replacement = os.lstat(run_directory)
        replacement_identity = (replacement.st_dev, replacement.st_ino)
        raise backup_demo.BackupRecoveryError("report_write_failed")

    monkeypatch.setattr(backup_demo, "_write_report", replace_then_fail)

    code, payload, error = _invoke(
        tmp_path, FakeBackupRunner(), "--approve-local-simulation"
    )

    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "report_write_failed"
    assert replacement_identity is not None
    current = os.lstat(expected)
    assert (current.st_dev, current.st_ino) == replacement_identity
    assert expected.is_dir() and not tuple(expected.iterdir())
    assert parked.is_dir()


@pytest.mark.skipif(
    importlib.util.find_spec("duckdb") is None,
    reason="real Local Profile dependencies are not installed",
)
def test_real_runtime_recovers_one_success_lifecycle_and_removes_backup() -> None:
    repository = MODULE_PATH.parents[2]
    defense_root = repository / ".local" / "networkagent-defense"
    moment = datetime.now(UTC).replace(microsecond=0)
    token = os.urandom(6).hex()
    run_directory = defense_root / f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{token}"
    stdout = StringIO()
    stderr = StringIO()
    try:
        code = backup_demo.main(
            ["--approve-local-simulation"],
            stdout=stdout,
            stderr=stderr,
            repository_root=repository,
            utc_now=lambda: moment,
            random_token=lambda: token,
        )
        assert code == 0, stderr.getvalue()
        assert stderr.getvalue() == ""
        payload = json.loads(stdout.getvalue())
        assert payload["proof"]["lifecycle_projection_equivalent"] is True
        assert payload["proof"]["restore_retry_changed"] is False
        assert (
            payload["coverage"]["delivered"]["successful_run_workspace_backup_cleanup"]
            is True
        )
        assert {child.name for child in run_directory.iterdir()} == {
            "local-backup-recovery-report.json"
        }
    finally:
        report = run_directory / "local-backup-recovery-report.json"
        if report.is_file() and not report.is_symlink():
            report.unlink()
        try:
            run_directory.rmdir()
        except OSError:
            pass
    assert not os.path.lexists(run_directory)
