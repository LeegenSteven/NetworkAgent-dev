from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).parents[1] / "local_stack.py"
SPEC = importlib.util.spec_from_file_location("networkagent_local_stack", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
local_stack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_stack)


class FakeRuntime:
    def __init__(self, workspace: local_stack.Workspace) -> None:
        self.workspace = workspace

    def doctor(self, *, port: int) -> dict[str, object]:
        return {
            "ready": True,
            "python": {"supported": True, "version": "3.12"},
            "dependencies": {"core": True, "server": True},
            "data": {"ready": True},
            "port": {"number": port, "available": True},
        }

    def initialize(self) -> dict[str, object]:
        self.workspace.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.database_path.write_bytes(b"test-db")
        return {
            "schema_version": "1.1",
            "performance_rows": 16,
            "trace_rows": 4,
            "incident_rows": 0,
            "server_schema": True,
        }

    def status(self, *, port: int) -> dict[str, object]:
        return {
            "ready": self.workspace.database_path.is_file(),
            "database": {
                "initialized": self.workspace.database_path.is_file(),
                "schema_version": "1.1",
                "incident_rows": 0,
            },
            "server": {"host": "127.0.0.1", "port": port, "available": True},
        }

    def demo(
        self,
        *,
        action_mode: str,
        confirm_incident: bool,
        approve_action: bool,
        reason: str | None,
        expected_action_hash: str | None,
        expected_revision: int | None,
        verification_outcome: str,
    ) -> dict[str, object]:
        confirmation = self.workspace.state_dir / "confirmed"
        if confirm_incident and approve_action:
            raise local_stack.SafeCliError("approval_requires_prior_preview")
        if approve_action and not confirmation.is_file():
            raise local_stack.SafeCliError("approval_requires_incident")
        if approve_action and action_mode != "simulate":
            raise local_stack.SafeCliError("actions_disabled")
        state = "PREVIEW"
        if confirm_incident:
            confirmation.write_text("confirmed")
            state = "AWAITING_APPROVAL"
        if approve_action:
            if expected_action_hash != "action-sha256" or expected_revision != 2:
                raise local_stack.SafeCliError("approval_binding_mismatch")
            state = "RESOLVED" if verification_outcome == "passed" else "REOPENED"
        return {
            "workflow_id": "local-demo-fixed",
            "state": state,
            "closed_loop": state == "RESOLVED",
            "action_mode": action_mode,
            "candidate_count": 2,
            "action_preview": {
                "action_hash": "action-sha256",
                "expected_revision": 2,
                "resources": ["lte-cell:1/1"],
                "risk": "LOW",
            },
            "artifacts": ["artifacts/demo-result.json"],
        }

    def seed_container_demo(self) -> dict[str, object]:
        return {
            "candidate_count": 15,
            "incident_id": "incident-container-demo",
            "status": "DETECTED",
            "revision": 0,
        }

    def verify_container_demo(self, *, expected_status: str) -> dict[str, object]:
        return {
            "incident_id": "incident-container-demo",
            "status": expected_status,
            "expected_status": expected_status,
            "revision": 7,
            "rca_reports": 1,
            "recommendations": 1,
            "approvals": 2,
            "action_runs": 1,
            "verification_runs": 1,
            "audit_events": 8,
            "action": {
                "action_type": "LOCAL_SIMULATION",
                "status": "SUCCEEDED",
                "side_effects": False,
            },
            "verification": {
                "status": "PASSED" if expected_status == "RESOLVED" else "FAILED"
            },
        }

    def lifecycle_events(self, *, expected_status: str) -> dict[str, object]:
        verification = "PASSED" if expected_status == "RESOLVED" else "FAILED"
        return {
            "schema": "networkagent-local-lifecycle-projection/1.0",
            "classification": "DERIVED_FROM_DURABLE_CANONICAL_RECORDS",
            "read_only": True,
            "distributed_trace": False,
            "ordering": "REVISION_GROUPED_ATOMIC_PROJECTION",
            "scenario": (
                "SUCCESS_BRANCH" if expected_status == "RESOLVED" else "FAILURE_BRANCH"
            ),
            "terminal_status": expected_status,
            "record_counts": {
                "audit_events": 8,
                "domain_records": 6,
                "projected_events": 14,
                "revision_groups": 8,
            },
            "invariants": {
                "bindings_exact": True,
                "expected_verification": verification,
                "single_incident": True,
                "side_effects": False,
            },
            "revision_groups": [
                {
                    "revision": revision,
                    "events": [
                        {
                            "sequence": revision + 1,
                            "occurred_at": "2026-08-31T00:00:00Z",
                            "record_type": "INCIDENT_AUDIT",
                            "component": "INCIDENT_REPOSITORY",
                            "operation": "STATE_TRANSITION",
                            "outcome": "RECORDED",
                        }
                    ],
                }
                for revision in range(8)
            ],
        }

    def serve(self, *, port: int) -> None:  # pragma: no cover - foreground path
        raise AssertionError("serve should not be exercised in unit tests")


def invoke(tmp_path: Path, *args: str) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(tmp_path / "stack"), *args],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FakeRuntime,
    )
    success = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, success, error


def invoke_real(tmp_path: Path, *args: str) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(tmp_path / "stack"), *args],
        stdout=stdout,
        stderr=stderr,
    )
    success = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, success, error


def invoke_real_workspace(
    workspace: Path, *args: str
) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(workspace), *args],
        stdout=stdout,
        stderr=stderr,
    )
    success = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, success, error


def prepare_maintenance_workspace(tmp_path: Path) -> Path:
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "stack"
    workspace = local_stack.Workspace(root)
    workspace_id, created, _ = workspace.prepare_init()
    assert created is True
    connection = duckdb.connect(str(workspace.database_path))
    try:
        connection.execute(
            "CREATE TABLE local_schema_metadata(key VARCHAR PRIMARY KEY, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO local_schema_metadata VALUES ('schema_version', '1.1')"
        )
        connection.execute(
            "CREATE TABLE assurance_schema_metadata("
            "key VARCHAR PRIMARY KEY, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO assurance_schema_metadata VALUES ('schema_version', '1.1')"
        )
        connection.execute("CREATE SEQUENCE record_ids START 1")
        connection.execute(
            "CREATE TABLE records("
            "id INTEGER PRIMARY KEY DEFAULT nextval('record_ids'), "
            "value VARCHAR NOT NULL CHECK (length(value) <= 32))"
        )
        connection.execute("INSERT INTO records(value) VALUES ('one'), ('two')")
        connection.execute("CREATE UNIQUE INDEX records_value_idx ON records(value)")
        connection.execute("CREATE VIEW record_values AS SELECT value FROM records")
        connection.execute("CREATE MACRO local_increment(value) AS value + 1")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    workspace.commit_marker(workspace_id)
    return root


def manifest_sha256(backup: Path) -> str:
    return hashlib.sha256(
        (backup / local_stack.BACKUP_MANIFEST_NAME).read_bytes()
    ).hexdigest()


def local_ownership_sha256(backup: Path) -> str:
    directory = os.lstat(backup)
    database = os.lstat(backup / local_stack.BACKUP_DATABASE_NAME)
    manifest = os.lstat(backup / local_stack.BACKUP_MANIFEST_NAME)
    entries = [
        ["directory", directory.st_dev, directory.st_ino],
        [
            local_stack.BACKUP_DATABASE_NAME,
            database.st_dev,
            database.st_ino,
            database.st_size,
            database.st_mtime_ns,
            database.st_ctime_ns,
            database.st_nlink,
        ],
        [
            local_stack.BACKUP_MANIFEST_NAME,
            manifest.st_dev,
            manifest.st_ino,
            manifest.st_size,
            manifest.st_mtime_ns,
            manifest.st_ctime_ns,
            manifest.st_nlink,
        ],
    ]
    encoded = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(
        local_stack._LOCAL_BACKUP_OWNERSHIP_DOMAIN + encoded
    ).hexdigest()


def test_real_cold_backup_restore_is_exact_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    workspace = prepare_maintenance_workspace(tmp_path)
    backup = tmp_path / "cold'backup"

    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(backup)
    )
    assert (code, error) == (0, None)
    assert set(payload) == {"command", "ok", "result"}
    result = payload["result"]
    assert set(result) == {
        "catalog",
        "changed",
        "checkpointed",
        "database",
        "local_ownership_sha256",
        "logical_equivalence",
        "manifest",
        "row_count",
        "schema",
        "tables",
    }
    assert result["changed"] is True
    assert result["schema"] == local_stack.BACKUP_SCHEMA_VERSION
    assert {entry.name for entry in backup.iterdir()} == {
        local_stack.BACKUP_DATABASE_NAME,
        local_stack.BACKUP_MANIFEST_NAME,
    }
    manifest_raw = (backup / local_stack.BACKUP_MANIFEST_NAME).read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest_raw == local_stack._canonical_json_bytes(manifest)
    assert result["manifest"]["sha256"] == hashlib.sha256(manifest_raw).hexdigest()
    assert result["local_ownership_sha256"] == local_ownership_sha256(backup)
    assert local_stack._is_sha256(result["local_ownership_sha256"])
    assert result["checkpointed"] is True
    assert result["logical_equivalence"] is True
    assert result["tables"] == sorted(
        result["tables"], key=lambda item: (item["schema"], item["name"])
    )
    assert str(tmp_path) not in json.dumps(payload)

    connection = duckdb.connect(str(workspace / "state" / "networkagent.duckdb"))
    try:
        connection.execute("INSERT INTO records(value) VALUES ('lost-after-backup')")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()

    digest = result["manifest"]["sha256"]
    first_code, first, first_error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        digest,
        "--yes",
    )
    retry_code, retry, retry_error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        digest,
        "--yes",
    )
    assert (first_code, first_error, first["result"]["changed"]) == (0, None, True)
    assert (retry_code, retry_error, retry["result"]["changed"]) == (0, None, False)
    assert {
        key: value for key, value in first["result"].items() if key != "changed"
    } == {key: value for key, value in retry["result"].items() if key != "changed"}
    connection = duckdb.connect(
        str(workspace / "state" / "networkagent.duckdb"), read_only=True
    )
    try:
        assert connection.execute(
            "SELECT value FROM records ORDER BY id"
        ).fetchall() == [
            ("one",),
            ("two",),
        ]
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "database_flip",
        "database_truncate",
        "database_append",
        "manifest_duplicate_key",
        "manifest_unknown_field",
        "manifest_wrong_bytes",
        "manifest_wrong_hash",
        "manifest_wrong_schema",
        "manifest_wrong_version",
    ),
)
def test_restore_rejects_corrupt_backup_without_replacing_workspace(
    tmp_path: Path, mutation: str
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    valid = tmp_path / "valid"
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(valid)
    )
    assert (code, error) == (0, None)
    corrupt = tmp_path / f"corrupt-{mutation}"
    shutil.copytree(valid, corrupt)
    database_path = corrupt / local_stack.BACKUP_DATABASE_NAME
    manifest_path = corrupt / local_stack.BACKUP_MANIFEST_NAME
    expected = manifest_sha256(corrupt)

    if mutation.startswith("database_"):
        data = database_path.read_bytes()
        if mutation == "database_flip":
            changed = bytearray(data)
            changed[len(changed) // 2] ^= 0x01
            database_path.write_bytes(changed)
        elif mutation == "database_truncate":
            database_path.write_bytes(data[:-1])
        else:
            database_path.write_bytes(data + b"x")
    elif mutation == "manifest_duplicate_key":
        raw = manifest_path.read_bytes()
        manifest_path.write_bytes(b'{"schema":"duplicate",' + raw[1:])
        expected = manifest_sha256(corrupt)
    else:
        manifest = json.loads(manifest_path.read_bytes())
        if mutation == "manifest_unknown_field":
            manifest["unknown"] = True
        elif mutation == "manifest_wrong_bytes":
            manifest["database"]["bytes"] += 1
        elif mutation == "manifest_wrong_hash":
            manifest["database"]["sha256"] = "0" * 64
        elif mutation == "manifest_wrong_schema":
            manifest["schema"] = "networkagent-local-cold-backup/2.0"
        else:
            manifest["duckdb"]["library_version"] = "v0.0.0"
        manifest_path.write_bytes(local_stack._canonical_json_bytes(manifest))
        expected = manifest_sha256(corrupt)

    current = workspace / "state" / "networkagent.duckdb"
    before = current.read_bytes()
    rejected_code, rejected, rejected_error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(corrupt),
        "--expected-manifest-sha256",
        expected,
        "--yes",
    )
    assert (rejected_code, rejected) == (2, None)
    assert rejected_error["error"]["code"] == "backup_invalid"
    assert current.read_bytes() == before
    assert str(tmp_path) not in json.dumps(rejected_error)


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_file",
        "missing_database",
        "missing_manifest",
        "database_hardlink",
        "manifest_hardlink",
        "database_symlink",
        "manifest_symlink",
        "oversized_database",
    ),
)
def test_restore_requires_exact_two_single_link_bounded_files(
    tmp_path: Path, mutation: str
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    valid = tmp_path / "valid"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(valid))[0] == 0
    )
    candidate = tmp_path / f"candidate-{mutation}"
    shutil.copytree(valid, candidate)
    expected = manifest_sha256(candidate)
    if mutation == "extra_file":
        (candidate / "extra.txt").write_text("must reject", encoding="utf-8")
    elif mutation == "missing_database":
        (candidate / local_stack.BACKUP_DATABASE_NAME).unlink()
    elif mutation == "missing_manifest":
        (candidate / local_stack.BACKUP_MANIFEST_NAME).unlink()
    elif mutation in {"database_hardlink", "manifest_hardlink"}:
        name = (
            local_stack.BACKUP_DATABASE_NAME
            if mutation == "database_hardlink"
            else local_stack.BACKUP_MANIFEST_NAME
        )
        target = candidate / name
        target.unlink()
        try:
            os.link(valid / name, target)
        except OSError:
            pytest.skip("hard links are unavailable")
    elif mutation in {"database_symlink", "manifest_symlink"}:
        name = (
            local_stack.BACKUP_DATABASE_NAME
            if mutation == "database_symlink"
            else local_stack.BACKUP_MANIFEST_NAME
        )
        target = candidate / name
        target.unlink()
        try:
            target.symlink_to(valid / name)
        except OSError:
            pytest.skip("file symlink creation is unavailable")
    else:
        with (candidate / local_stack.BACKUP_DATABASE_NAME).open("r+b") as handle:
            handle.truncate(local_stack._BACKUP_MAX_DATABASE_BYTES + 1)

    current = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    before = current.read_bytes()
    code, payload, error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(candidate),
        "--expected-manifest-sha256",
        expected,
        "--yes",
    )
    assert (code, payload) == (2, None)
    expected_code = (
        "backup_too_large" if mutation == "oversized_database" else "backup_invalid"
    )
    assert error["error"]["code"] == expected_code
    assert current.read_bytes() == before


def test_restore_confirmation_and_manifest_binding_fail_without_writes(
    tmp_path: Path,
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    backup = tmp_path / "backup"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(backup))[0] == 0
    )
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    before = database.read_bytes()
    digest = manifest_sha256(backup)

    missing_code, missing, missing_error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        digest,
    )
    mismatch_code, mismatch, mismatch_error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        "0" * 64,
        "--yes",
    )
    assert (missing_code, missing) == (1, None)
    assert missing_error["error"]["code"] == "restore_confirmation_required"
    assert (mismatch_code, mismatch) == (2, None)
    assert mismatch_error["error"]["code"] == "manifest_mismatch"
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "failure_stage",
    ("early_stat", "post_mkdir_validation", "catalog", "manifest", "publish"),
)
def test_backup_interruption_never_publishes_or_leaves_owned_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    destination = tmp_path / f"backup-{failure_stage}"
    if failure_stage == "early_stat":
        original_stat = local_stack.Path.stat

        def fail_first_staging_stat(path: Path, *args, **kwargs):
            if path.name.startswith(".networkagent-backup-"):
                raise OSError("injected early stat failure")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(local_stack.Path, "stat", fail_first_staging_stat)
    elif failure_stage == "post_mkdir_validation":
        original_require_directory_identity = local_stack._require_directory_identity
        identity_calls = 0

        def fail_first_identity_check(*args, **kwargs):
            nonlocal identity_calls
            identity_calls += 1
            if identity_calls == 1:
                raise local_stack.SafeCliError("unsafe_workspace")
            return original_require_directory_identity(*args, **kwargs)

        monkeypatch.setattr(
            local_stack,
            "_require_directory_identity",
            fail_first_identity_check,
        )
    elif failure_stage == "catalog":
        original = local_stack._database_metadata
        calls = 0

        def fail_after_copy(connection):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise local_stack.SafeCliError("backup_failed")
            return original(connection)

        monkeypatch.setattr(local_stack, "_database_metadata", fail_after_copy)
    elif failure_stage == "manifest":
        monkeypatch.setattr(
            local_stack,
            "_write_new_file",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                local_stack.SafeCliError("backup_failed")
            ),
        )
    else:
        monkeypatch.setattr(
            local_stack,
            "_publish_directory_no_replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                local_stack.SafeCliError("backup_failed")
            ),
        )
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(destination)
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] in {"backup_failed", "unsafe_workspace"}
    assert not destination.exists()
    residual = list(tmp_path.glob(".networkagent-backup-*.partial"))
    if failure_stage == "early_stat":
        # Identity acquisition failed; preserve the unknown empty directory.
        assert len(residual) == 1
        assert not list(residual[0].iterdir())
    else:
        assert not residual


def test_backup_refuses_existing_and_racing_destination_without_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("user-owned", encoding="utf-8")
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(existing)
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_exists"
    assert sentinel.read_text(encoding="utf-8") == "user-owned"

    racing = tmp_path / "racing"
    original_publish = local_stack._publish_directory_no_replace

    def create_race(source: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "keep.txt").write_text("racer", encoding="utf-8")
        original_publish(source, destination)

    monkeypatch.setattr(local_stack, "_publish_directory_no_replace", create_race)
    race_code, race_payload, race_error = invoke_real_workspace(
        workspace, "backup", "--destination", str(racing)
    )
    assert (race_code, race_payload) == (2, None)
    assert race_error["error"]["code"] == "backup_exists"
    assert (racing / "keep.txt").read_text(encoding="utf-8") == "racer"
    assert not list(tmp_path.glob(".networkagent-backup-*.partial"))


def test_backup_never_deletes_a_preexisting_staging_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    fixed = uuid.UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr(local_stack.uuid, "uuid4", lambda: fixed)
    staging = tmp_path / f".networkagent-backup-{fixed.hex}.partial"
    staging.mkdir()
    sentinel = staging / "keep.txt"
    sentinel.write_text("user-owned", encoding="utf-8")
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(tmp_path / "backup")
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_failed"
    assert sentinel.read_text(encoding="utf-8") == "user-owned"


def test_backup_cleanup_anomaly_fails_closed_and_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    destination = tmp_path / "backup"

    def inject_unknown_staging_entry(path: Path, *_args, **_kwargs) -> None:
        (path.parent / "unexpected-entry").write_text("injected", encoding="utf-8")
        raise local_stack.SafeCliError("backup_failed")

    monkeypatch.setattr(local_stack, "_write_new_file", inject_unknown_staging_entry)
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(destination)
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_failed"
    assert not destination.exists()
    residual = list(tmp_path.glob(".networkagent-backup-*.partial"))
    assert len(residual) == 1
    assert {entry.name for entry in residual[0].iterdir()} == {
        local_stack.BACKUP_DATABASE_NAME,
        "unexpected-entry",
    }


def test_backup_cleanup_preserves_replaced_known_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    destination = tmp_path / "backup"
    replacement = b"same-name replacement"

    def replace_database_before_failure(path: Path, *_args, **_kwargs) -> None:
        database = path.parent / local_stack.BACKUP_DATABASE_NAME
        database.unlink()
        database.write_bytes(replacement)
        raise local_stack.SafeCliError("backup_failed")

    monkeypatch.setattr(local_stack, "_write_new_file", replace_database_before_failure)
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(destination)
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_failed"
    assert not destination.exists()
    residual = list(tmp_path.glob(".networkagent-backup-*.partial"))
    assert len(residual) == 1
    assert (residual[0] / local_stack.BACKUP_DATABASE_NAME).read_bytes() == replacement


def test_backup_post_publish_validation_failure_preserves_durable_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    destination = tmp_path / "backup"
    original = local_stack._require_directory_identity

    def fail_only_after_publish(path: Path, expected, *, invalid_code: str) -> None:
        if path == destination and destination.exists():
            raise local_stack.SafeCliError("backup_failed")
        original(path, expected, invalid_code=invalid_code)

    monkeypatch.setattr(
        local_stack, "_require_directory_identity", fail_only_after_publish
    )
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(destination)
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_failed"
    assert {entry.name for entry in destination.iterdir()} == {
        local_stack.BACKUP_DATABASE_NAME,
        local_stack.BACKUP_MANIFEST_NAME,
    }
    assert not list(tmp_path.glob(".networkagent-backup-*.partial"))
    retry_code, retry_payload, retry_error = invoke_real_workspace(
        workspace, "backup", "--destination", str(destination)
    )
    assert (retry_code, retry_payload) == (2, None)
    assert retry_error["error"]["code"] == "backup_exists"


def test_local_ownership_identity_rejects_non_strict_or_invalid_integers() -> None:
    for identity in ((False, 1), (0, True), (-1, 1), (0, 0)):
        with pytest.raises(local_stack.SafeCliError) as captured:
            local_stack._strict_directory_identity(
                identity, invalid_code="backup_failed"
            )
        assert captured.value.code == "backup_failed"
    invalid_files = (
        (False, 1, 0, 1, 1, 1),
        (0, True, 0, 1, 1, 1),
        (0, 1, False, 1, 1, 1),
        (0, 1, 0, False, 1, 1),
        (0, 1, 0, 1, False, 1),
        (0, 1, 0, 1, 1, True),
        (-1, 1, 0, 1, 1, 1),
        (0, 0, 0, 1, 1, 1),
        (0, 1, -1, 1, 1, 1),
        (0, 1, 0, 0, 1, 1),
        (0, 1, 0, 1, 0, 1),
        (0, 1, 0, 1, 1, 2),
    )
    for identity in invalid_files:
        with pytest.raises(local_stack.SafeCliError) as captured:
            local_stack._strict_file_identity(identity, invalid_code="backup_failed")
        assert captured.value.code == "backup_failed"


def test_file_cleanup_rejects_simulated_reused_inode_with_new_ctime(
    tmp_path: Path,
) -> None:
    target = tmp_path / "same-inode"
    target.write_bytes(b"replacement")
    actual = local_stack._file_identity(os.lstat(target), invalid_code="backup_failed")
    simulated_old = (*actual[:4], actual[4] - 1, actual[5])
    with pytest.raises(local_stack.SafeCliError) as captured:
        local_stack._unlink_file_identity(
            target,
            simulated_old,
            invalid_code="backup_failed",
            failure_code="backup_failed",
        )
    assert captured.value.code == "backup_failed"
    assert target.read_bytes() == b"replacement"


def test_backup_ownership_binding_rejects_exact_content_child_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    destination = tmp_path / "backup"
    original = local_stack._local_backup_ownership_sha256
    replaced_manifest_identity: local_stack.FileIdentity | None = None
    original_manifest_digest: str | None = None

    def replace_manifest_before_binding(directory: Path, **kwargs) -> str:
        nonlocal original_manifest_digest, replaced_manifest_identity
        manifest = directory / local_stack.BACKUP_MANIFEST_NAME
        original_payload = manifest.read_bytes()
        original_manifest_digest = hashlib.sha256(original_payload).hexdigest()
        manifest.unlink()
        manifest.write_bytes(original_payload)
        metadata = os.lstat(manifest)
        replaced_manifest_identity = local_stack._file_identity(
            metadata, invalid_code="backup_failed"
        )
        return original(directory, **kwargs)

    monkeypatch.setattr(
        local_stack,
        "_local_backup_ownership_sha256",
        replace_manifest_before_binding,
    )
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(destination)
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_failed"
    assert replaced_manifest_identity is not None
    assert {entry.name for entry in destination.iterdir()} == {
        local_stack.BACKUP_DATABASE_NAME,
        local_stack.BACKUP_MANIFEST_NAME,
    }
    assert manifest_sha256(destination) == original_manifest_digest
    assert not list(tmp_path.glob(".networkagent-backup-*.partial"))


def test_restore_rejects_linked_backup_directory_without_following_it(
    tmp_path: Path,
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    valid = tmp_path / "valid"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(valid))[0] == 0
    )
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(valid, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    before = database.read_bytes()
    code, payload, error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(linked),
        "--expected-manifest-sha256",
        manifest_sha256(valid),
        "--yes",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "backup_invalid"
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "failure_stage", ("copy", "stage_verify", "pre_replace", "response_loss")
)
def test_restore_interruption_is_atomic_and_response_loss_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    workspace = prepare_maintenance_workspace(tmp_path)
    backup = tmp_path / "backup"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(backup))[0] == 0
    )
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    connection = duckdb.connect(str(database))
    try:
        connection.execute("INSERT INTO records(value) VALUES ('current-only')")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    before = database.read_bytes()
    arguments = (
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        manifest_sha256(backup),
        "--yes",
    )

    with monkeypatch.context() as scoped:
        if failure_stage == "copy":
            scoped.setattr(
                local_stack,
                "_copy_database_to_restore_temp",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    local_stack.SafeCliError("restore_failed")
                ),
            )
        elif failure_stage == "stage_verify":
            original_metadata = local_stack._database_metadata
            calls = 0

            def fail_temp_verify(connection):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise local_stack.SafeCliError("backup_invalid")
                return original_metadata(connection)

            scoped.setattr(local_stack, "_database_metadata", fail_temp_verify)
        elif failure_stage == "pre_replace":
            original_replace = local_stack.os.replace

            def fail_restore_replace(source: object, destination: object) -> None:
                if Path(source).name == local_stack._RESTORE_TEMP_NAME:
                    raise OSError("injected replacement failure")
                original_replace(source, destination)

            scoped.setattr(local_stack.os, "replace", fail_restore_replace)
        else:
            scoped.setattr(
                local_stack,
                "_restore_public_summary",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("injected response loss")
                ),
            )
        code, payload, error = invoke_real_workspace(workspace, *arguments)

    assert (code, payload) == (2, None)
    if failure_stage == "response_loss":
        assert error["error"]["code"] == "runtime_failed"
        assert database.read_bytes() != before
        retry_code, retry, retry_error = invoke_real_workspace(workspace, *arguments)
        assert (retry_code, retry_error) == (0, None)
        assert retry["result"]["changed"] is False
    else:
        assert error["error"]["code"] in {"backup_invalid", "restore_failed"}
        assert database.read_bytes() == before
    assert not (workspace / "state" / local_stack._RESTORE_TEMP_NAME).exists()


def test_restore_cleanup_preserves_replaced_temp_and_current_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    duckdb = pytest.importorskip("duckdb")
    workspace = prepare_maintenance_workspace(tmp_path)
    backup = tmp_path / "backup"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(backup))[0] == 0
    )
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    connection = duckdb.connect(str(database))
    try:
        connection.execute("INSERT INTO records(value) VALUES ('current-only')")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    before = database.read_bytes()
    restore_temp = workspace / "state" / local_stack._RESTORE_TEMP_NAME
    replacement = b"unknown same-name restore temp"
    original_replace = local_stack.os.replace

    def replace_temp_then_fail(source: object, destination: object) -> None:
        if Path(source).name == local_stack._RESTORE_TEMP_NAME:
            Path(source).unlink()
            Path(source).write_bytes(replacement)
            raise OSError("injected replacement race")
        original_replace(source, destination)

    monkeypatch.setattr(local_stack.os, "replace", replace_temp_then_fail)
    code, payload, error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        manifest_sha256(backup),
        "--yes",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "unsafe_workspace"
    assert database.read_bytes() == before
    assert restore_temp.read_bytes() == replacement


def test_restore_preserves_unknown_preexisting_fixed_temp(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    workspace = prepare_maintenance_workspace(tmp_path)
    backup = tmp_path / "backup"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(backup))[0] == 0
    )
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    connection = duckdb.connect(str(database))
    try:
        connection.execute("INSERT INTO records(value) VALUES ('current-only')")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    before = database.read_bytes()
    restore_temp = workspace / "state" / local_stack._RESTORE_TEMP_NAME
    restore_temp.write_bytes(b"unknown prior invocation")
    code, payload, error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        manifest_sha256(backup),
        "--yes",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "restore_failed"
    assert database.read_bytes() == before
    assert restore_temp.read_bytes() == b"unknown prior invocation"


def test_real_other_process_duckdb_lock_fails_closed_without_path_leak(
    tmp_path: Path,
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    valid_backup = tmp_path / "valid-backup"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(valid_backup))[
            0
        ]
        == 0
    )
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    before = database.read_bytes()
    script = (
        "import duckdb,sys,time;"
        "c=duckdb.connect(sys.argv[1]);"
        "c.execute('BEGIN TRANSACTION');"
        "c.execute(\"INSERT INTO records(value) VALUES ('locked')\");"
        "print('READY',flush=True);time.sleep(20)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(database)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        invocations = (
            ("backup", "--destination", str(tmp_path / "blocked-backup")),
            (
                "restore",
                "--source",
                str(valid_backup),
                "--expected-manifest-sha256",
                manifest_sha256(valid_backup),
                "--yes",
            ),
        )
        for arguments in invocations:
            code, payload, error = invoke_real_workspace(workspace, *arguments)
            assert (code, payload) == (2, None)
            assert error["error"]["code"] == "workspace_busy"
            assert str(tmp_path) not in json.dumps(error)
    finally:
        process.terminate()
        process.communicate(timeout=10)
    assert database.read_bytes() == before


def test_backup_rejects_unsafe_database_sidecars_without_following_them(
    tmp_path: Path,
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    wal = Path(f"{database}.wal")
    try:
        wal.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(tmp_path / "backup")
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "unsafe_workspace"
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_backup_rejects_hardlinked_live_database(tmp_path: Path) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    alias = tmp_path / "database-alias"
    try:
        os.link(database, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    code, payload, error = invoke_real_workspace(
        workspace, "backup", "--destination", str(tmp_path / "backup")
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "unsafe_workspace"
    assert alias.is_file()


def test_restore_rejects_hardlinked_workspace_marker_without_writes(
    tmp_path: Path,
) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    backup = tmp_path / "backup"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(backup))[0] == 0
    )
    marker = workspace / local_stack.MARKER_NAME
    alias = tmp_path / "marker-alias"
    try:
        os.link(marker, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    database = workspace / "state" / local_stack.BACKUP_DATABASE_NAME
    before = database.read_bytes()
    code, payload, error = invoke_real_workspace(
        workspace,
        "restore",
        "--source",
        str(backup),
        "--expected-manifest-sha256",
        manifest_sha256(backup),
        "--yes",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "workspace_not_owned"
    assert database.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_restore_rejects_backup_directory_junction(tmp_path: Path) -> None:
    workspace = prepare_maintenance_workspace(tmp_path)
    valid = tmp_path / "valid"
    assert (
        invoke_real_workspace(workspace, "backup", "--destination", str(valid))[0] == 0
    )
    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(valid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        code, payload, error = invoke_real_workspace(
            workspace,
            "restore",
            "--source",
            str(junction),
            "--expected-manifest-sha256",
            manifest_sha256(valid),
            "--yes",
        )
        assert (code, payload) == (2, None)
        assert error["error"]["code"] == "backup_invalid"
    finally:
        if getattr(junction, "is_junction", lambda: False)():
            os.rmdir(junction)


def test_workspace_is_required_and_errors_do_not_echo_paths(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()
    secret_path = tmp_path / "private-user-name" / "stack"
    code = local_stack.main(
        ["--workspace", str(secret_path), "status"],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FakeRuntime,
    )
    assert code == 1
    payload = json.loads(stderr.getvalue())
    assert payload["error"]["code"] == "workspace_not_initialized"
    assert str(secret_path) not in stderr.getvalue()


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC regression")
def test_unc_workspace_is_rejected_before_any_filesystem_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_probe(_path: Path) -> bool:
        raise AssertionError("UNC rejection must happen before a filesystem probe")

    monkeypatch.setattr(local_stack, "_is_link_like", unexpected_probe)
    with pytest.raises(local_stack.SafeCliError) as caught:
        local_stack.Workspace(Path(r"\\example.invalid\share\stack"))
    assert caught.value.code == "unsafe_workspace"


def test_doctor_is_read_only_and_json_contains_no_workspace_path(
    tmp_path: Path,
) -> None:
    code, payload, error = invoke(tmp_path, "doctor")
    assert (code, error) == (0, None)
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert not (tmp_path / "stack").exists()
    assert str(tmp_path) not in json.dumps(payload)


def test_init_is_idempotent_for_owned_workspace(tmp_path: Path) -> None:
    first_code, first, first_error = invoke(tmp_path, "init")
    second_code, second, second_error = invoke(tmp_path, "init")
    assert (first_code, first_error) == (0, None)
    assert (second_code, second_error) == (0, None)
    assert first["workspace"]["initialized"] is True
    assert second["workspace"]["initialized"] is True
    assert first["workspace"]["workspace_id"] == second["workspace"]["workspace_id"]
    marker = json.loads((tmp_path / "stack" / ".local-stack.json").read_text())
    assert marker["kind"] == "networkagent-local-stack"


def test_container_demo_commands_are_strict_disabled_mode_json_contracts(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")

    seed_code, seed, seed_error = invoke(tmp_path, "demo-seed")
    assert (seed_code, seed_error) == (0, None)
    assert seed == {
        "action_mode": "disabled",
        "command": "demo-seed",
        "ok": True,
        "result": {
            "candidate_count": 15,
            "incident_id": "incident-container-demo",
            "revision": 0,
            "status": "DETECTED",
        },
        "workspace": seed["workspace"],
    }
    assert seed["workspace"]["initialized"] is True

    verify_code, verified, verify_error = invoke(
        tmp_path,
        "demo-verify",
        "--expected-status",
        "REOPENED",
    )
    assert (verify_code, verify_error) == (0, None)
    assert verified["action_mode"] == "disabled"
    assert verified["result"]["status"] == "REOPENED"
    assert verified["result"]["expected_status"] == "REOPENED"
    assert verified["result"]["rca_reports"] == 1
    assert verified["result"]["recommendations"] == 1
    assert verified["result"]["approvals"] == 2
    assert verified["result"]["action_runs"] == 1
    assert verified["result"]["verification_runs"] == 1
    assert verified["result"]["audit_events"] == 8
    assert verified["result"]["action"] == {
        "action_type": "LOCAL_SIMULATION",
        "status": "SUCCEEDED",
        "side_effects": False,
    }
    assert verified["result"]["verification"] == {"status": "FAILED"}

    events_code, events, events_error = invoke(
        tmp_path,
        "demo-events",
        "--expected-status",
        "REOPENED",
    )
    assert (events_code, events_error) == (0, None)
    assert events["action_mode"] == "disabled"
    assert events["command"] == "demo-events"
    assert events["result"]["schema"] == ("networkagent-local-lifecycle-projection/1.0")
    assert events["result"]["terminal_status"] == "REOPENED"
    assert events["result"]["distributed_trace"] is False


@pytest.mark.parametrize(
    "arguments",
    (
        ("--action-mode", "simulate", "demo-seed"),
        ("--action-mode", "simulate", "demo-verify", "--expected-status", "RESOLVED"),
        ("demo-verify",),
        ("demo-verify", "--expected-status", "resolved"),
        ("--action-mode", "simulate", "demo-events", "--expected-status", "RESOLVED"),
        ("demo-events",),
        ("demo-events", "--expected-status", "resolved"),
        ("demo-seed", "--incident-id", "untrusted"),
    ),
)
def test_container_demo_commands_reject_mode_relaxation_or_extra_arguments(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    invoke(tmp_path, "init")
    code, payload, error = invoke(tmp_path, *arguments)
    assert (code, payload) == (2, None)
    assert error["error"]["code"] in {"actions_disabled", "invalid_arguments"}


def test_real_container_demo_seed_creates_one_detected_incident_only(
    tmp_path: Path,
) -> None:
    init_code, initialized, init_error = invoke_real(tmp_path, "init")
    assert (init_code, init_error) == (0, None)
    assert initialized["database"]["incident_rows"] == 0

    seed_code, seeded, seed_error = invoke_real(tmp_path, "demo-seed")
    assert (seed_code, seed_error) == (0, None)
    assert seeded["result"]["candidate_count"] == 15
    assert seeded["result"]["status"] == "DETECTED"
    assert seeded["result"]["revision"] == 0
    assert str(tmp_path) not in json.dumps(seeded)

    from telco_local import LocalProfile

    runtime = local_stack.LocalStackRuntime(local_stack.Workspace(tmp_path / "stack"))
    profile = LocalProfile.open_existing(runtime._config())

    async def inspect_seed() -> None:
        incidents = tuple(await profile.incident_repository.list(limit=2, offset=0))
        assert len(incidents) == 1
        incident = incidents[0]
        assert incident.incident_id == seeded["result"]["incident_id"]
        assert incident.status.value == "DETECTED"
        assert incident.revision == 0
        assert incident.rca_reports == ()
        assert incident.recommendations == ()
        assert incident.approvals == ()
        assert incident.action_runs == ()
        assert incident.verification_runs == ()
        history = tuple(
            await profile.incident_repository.history(
                incident.incident_id,
                limit=2,
                offset=0,
            )
        )
        assert len(history) == 1
        assert history[0].revision == 0
        assert history[0].to_status.value == "DETECTED"

    asyncio.run(inspect_seed())

    database_before_verify = runtime.workspace.database_path.read_bytes()
    verify_code, verify_payload, verify_error = invoke_real(
        tmp_path,
        "demo-verify",
        "--expected-status",
        "RESOLVED",
    )
    assert (verify_code, verify_payload) == (2, None)
    assert verify_error["error"]["code"] == "demo_verification_failed"
    assert runtime.workspace.database_path.read_bytes() == database_before_verify

    replay_code, replay_payload, replay_error = invoke_real(tmp_path, "demo-seed")
    assert (replay_code, replay_payload) == (2, None)
    assert replay_error["error"]["code"] == "demo_seed_requires_fresh_workspace"


@pytest.mark.parametrize(
    ("expected_status", "verification_passed", "verification_status"),
    (("RESOLVED", True, "PASSED"), ("REOPENED", False, "FAILED")),
)
def test_real_container_demo_verify_requires_exact_non_amplified_terminal_state(
    tmp_path: Path,
    expected_status: str,
    verification_passed: bool,
    verification_status: str,
) -> None:
    assert invoke_real(tmp_path, "init")[0] == 0
    seed_code, seeded, seed_error = invoke_real(tmp_path, "demo-seed")
    assert (seed_code, seed_error) == (0, None)

    from telco_local import LocalGovernanceEngine, LocalProfile

    runtime = local_stack.LocalStackRuntime(local_stack.Workspace(tmp_path / "stack"))
    profile = LocalProfile.open_existing(runtime._config())

    async def complete() -> None:
        incident_id = seeded["result"]["incident_id"]
        engine = LocalGovernanceEngine(
            profile.incident_repository,
            profile.rca_gateway,
            clock=lambda: local_stack.datetime.now(local_stack.UTC),
        )
        prepared = await engine.prepare(
            incident_id,
            idempotency_key="container-demo-prepare-v1",
            actor="container-demo-governance",
        )
        assert prepared.action is not None
        decided = await engine.decide(
            incident_id,
            approve=True,
            expected_action_hash=prepared.action.action_hash,
            expected_revision=prepared.incident.revision,
            actor="container-demo-operator",
            reason="Approve the exact side-effect-free container simulation",
            idempotency_key="container-demo-decide-v1",
        )
        assert decided.incident.status.value == "REMEDIATING"
        completed = await engine.execute(
            incident_id,
            idempotency_key="container-demo-execute-v1",
            actor="container-demo-simulator",
            verification_passed=verification_passed,
        )
        assert completed.incident.status.value == expected_status

    asyncio.run(complete())
    database_before_verify = runtime.workspace.database_path.read_bytes()

    code, verified, error = invoke_real(
        tmp_path,
        "demo-verify",
        "--expected-status",
        expected_status,
    )
    assert (code, error) == (0, None)
    assert verified["result"] == {
        "action": {
            "action_type": "LOCAL_SIMULATION",
            "side_effects": False,
            "status": "SUCCEEDED",
        },
        "action_runs": 1,
        "approvals": 2,
        "audit_events": 8,
        "expected_status": expected_status,
        "incident_id": seeded["result"]["incident_id"],
        "rca_reports": 1,
        "recommendations": 1,
        "revision": 7,
        "status": expected_status,
        "verification": {"status": verification_status},
        "verification_runs": 1,
    }
    assert str(tmp_path) not in json.dumps(verified)
    assert runtime.workspace.database_path.read_bytes() == database_before_verify

    wrong = "REOPENED" if expected_status == "RESOLVED" else "RESOLVED"
    wrong_code, wrong_payload, wrong_error = invoke_real(
        tmp_path,
        "demo-verify",
        "--expected-status",
        wrong,
    )
    assert (wrong_code, wrong_payload) == (2, None)
    assert wrong_error["error"]["code"] == "demo_verification_failed"


@pytest.mark.parametrize(
    ("expected_status", "verification_outcome"),
    (("RESOLVED", "passed"), ("REOPENED", "failed")),
)
def test_real_demo_events_is_repeatable_read_only_public_flow(
    tmp_path: Path,
    expected_status: str,
    verification_outcome: str,
) -> None:
    assert invoke_real(tmp_path, "init")[0] == 0
    preview_code, preview, preview_error = invoke_real(
        tmp_path,
        "demo",
        "--confirm-incident",
    )
    assert (preview_code, preview_error) == (0, None)
    action = preview["result"]["action_preview"]
    approval = (
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "approved fixed isolated local simulation",
        "--expected-action-hash",
        action["action_hash"],
        "--expected-revision",
        str(action["expected_revision"]),
        *(
            ("--verification-outcome", "failed")
            if verification_outcome == "failed"
            else ()
        ),
    )
    terminal_code, terminal, terminal_error = invoke_real(tmp_path, *approval)
    retry_code, retry, retry_error = invoke_real(tmp_path, *approval)
    assert (terminal_code, terminal_error) == (0, None)
    assert (retry_code, retry_error) == (0, None)
    assert terminal["result"]["state"] == expected_status
    assert retry["result"]["state"] == expected_status

    runtime = local_stack.LocalStackRuntime(local_stack.Workspace(tmp_path / "stack"))
    database_before_projection = runtime.workspace.database_path.read_bytes()
    projection_code, projection, projection_error = invoke_real(
        tmp_path,
        "demo-events",
        "--expected-status",
        expected_status,
    )
    repeat_code, repeat_projection, repeat_error = invoke_real(
        tmp_path,
        "demo-events",
        "--expected-status",
        expected_status,
    )
    assert (projection_code, projection_error) == (0, None)
    assert (repeat_code, repeat_error) == (0, None)
    assert projection["result"] == repeat_projection["result"]
    assert runtime.workspace.database_path.read_bytes() == database_before_projection
    lifecycle = projection["result"]
    assert set(lifecycle) == {
        "classification",
        "distributed_trace",
        "invariants",
        "ordering",
        "read_only",
        "record_counts",
        "revision_groups",
        "scenario",
        "schema",
        "terminal_status",
    }
    assert lifecycle["schema"] == "networkagent-local-lifecycle-projection/1.0"
    assert lifecycle["classification"] == "DERIVED_FROM_DURABLE_CANONICAL_RECORDS"
    assert lifecycle["read_only"] is True
    assert lifecycle["distributed_trace"] is False
    assert lifecycle["ordering"] == "REVISION_GROUPED_ATOMIC_PROJECTION"
    assert lifecycle["terminal_status"] == expected_status
    assert lifecycle["invariants"] == {
        "bindings_exact": True,
        "revision_contiguous": True,
        "side_effects": False,
        "single_execution_attempt": True,
        "single_incident": True,
    }
    assert [group["revision"] for group in lifecycle["revision_groups"]] == list(
        range(8)
    )
    assert sum(len(group["events"]) for group in lifecycle["revision_groups"]) == 14
    forbidden_keys = {
        "action_hash",
        "action_id",
        "approval_id",
        "correlation_id",
        "event_id",
        "incident_id",
        "idempotency_key",
        "report_id",
        "request_id",
        "resource_id",
        "trace_id",
        "verification_id",
        "workspace_id",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    assert keys(lifecycle).isdisjoint(forbidden_keys)
    wrong_status = "REOPENED" if expected_status == "RESOLVED" else "RESOLVED"
    wrong_code, wrong_payload, wrong_error = invoke_real(
        tmp_path,
        "demo-events",
        "--expected-status",
        wrong_status,
    )
    assert (wrong_code, wrong_payload) == (2, None)
    assert wrong_error["error"]["code"] == "lifecycle_projection_failed"
    assert runtime.workspace.database_path.read_bytes() == database_before_projection


def test_init_rejects_nonempty_unowned_directory_without_deleting(
    tmp_path: Path,
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    sentinel = stack / "keep-me.txt"
    sentinel.write_text("owned by user")
    code, payload, error = invoke(tmp_path, "init")
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "workspace_not_owned"
    assert sentinel.read_text() == "owned by user"


def test_failed_first_init_rolls_back_only_new_owned_entries_and_can_retry(
    tmp_path: Path,
) -> None:
    class FailingRuntime(FakeRuntime):
        def initialize(self) -> dict[str, object]:
            self.workspace.state_dir.mkdir(parents=True, exist_ok=True)
            self.workspace.database_path.write_bytes(b"partial")
            raise local_stack.SafeCliError("runtime_failed")

    stack = tmp_path / "stack"
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(stack), "init"],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FailingRuntime,
    )
    assert code == 2
    assert not stack.exists()

    retry_code, retry, retry_error = invoke(tmp_path, "init")
    assert (retry_code, retry_error) == (0, None)
    assert retry["workspace"]["initialized"] is True


def test_reset_requires_yes_and_only_removes_owned_content(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    stack = tmp_path / "stack"
    user_file = stack / "user-notes.txt"
    user_file.write_text("preserve")

    code, payload, error = invoke(tmp_path, "reset")
    assert (code, error) == (1, None)
    assert payload["confirmation_required"] is True
    assert (stack / ".local-stack.json").is_file()

    code, payload, error = invoke(tmp_path, "reset", "--yes")
    assert (code, error) == (0, None)
    assert payload["reset"] is True
    assert payload["workspace_removed"] is False
    assert user_file.read_text() == "preserve"
    assert not (stack / ".local-stack.json").exists()
    assert not (stack / "state").exists()


@pytest.mark.parametrize("mode", ["disabled", "simulate"])
def test_demo_never_approves_without_explicit_flag(tmp_path: Path, mode: str) -> None:
    invoke(tmp_path, "init")
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        mode,
        "demo",
        "--confirm-incident",
    )
    assert (code, error) == (0, None)
    assert payload["result"]["state"] == "AWAITING_APPROVAL"
    assert payload["result"]["closed_loop"] is False


def test_simulated_closed_loop_requires_prior_preview_and_bound_confirmation(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")
    preview_code, preview, preview_error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    assert (preview_code, preview_error) == (0, None)
    binding = preview["result"]["action_preview"]

    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "approved local simulation",
        "--expected-action-hash",
        binding["action_hash"],
        "--expected-revision",
        str(binding["expected_revision"]),
    )
    assert (code, error) == (0, None)
    assert payload["result"]["state"] == "RESOLVED"
    assert payload["result"]["closed_loop"] is True
    assert str(tmp_path) not in json.dumps(payload)


def test_disabled_mode_rejects_action_approval(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    invoke(tmp_path, "demo", "--confirm-incident")
    code, payload, error = invoke(
        tmp_path,
        "demo",
        "--approve-action",
        "--reason",
        "must remain disabled",
        "--expected-action-hash",
        "action-sha256",
        "--expected-revision",
        "2",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "actions_disabled"


def test_action_approval_cannot_be_combined_with_first_confirmation(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
        "--approve-action",
        "--reason",
        "not reviewed yet",
        "--expected-action-hash",
        "guessed",
        "--expected-revision",
        "0",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "approval_requires_prior_preview"


def test_action_approval_requires_exact_preview_binding(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "reviewed",
        "--expected-action-hash",
        "wrong-hash",
        "--expected-revision",
        "2",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "approval_binding_mismatch"


def test_action_approval_requires_both_preview_binding_values(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "reviewed",
        "--expected-action-hash",
        "action-sha256",
    )
    assert (code, payload) == (2, None)
    assert error["error"]["code"] == "approval_binding_required"


def test_failed_simulated_verification_reopens_instead_of_claiming_closed_loop(
    tmp_path: Path,
) -> None:
    invoke(tmp_path, "init")
    _, preview, _ = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--confirm-incident",
    )
    binding = preview["result"]["action_preview"]
    code, payload, error = invoke(
        tmp_path,
        "--action-mode",
        "simulate",
        "demo",
        "--approve-action",
        "--reason",
        "reviewed failure path",
        "--expected-action-hash",
        binding["action_hash"],
        "--expected-revision",
        str(binding["expected_revision"]),
        "--verification-outcome",
        "failed",
    )
    assert (code, error) == (0, None)
    assert payload["result"]["state"] == "REOPENED"
    assert payload["result"]["closed_loop"] is False


def test_action_preview_whitelists_resource_scope_fields() -> None:
    action = SimpleNamespace(
        action_hash="a" * 64,
        action_type="LOCAL_SIMULATION",
        risk_level=SimpleNamespace(value="LOW"),
        target_resources=(
            SimpleNamespace(
                resource_id="cell-1",
                resource_type=SimpleNamespace(value="CELL"),
                technology=SimpleNamespace(value="LTE"),
                attributes={"local_path": "must-not-leak"},
            ),
        ),
    )
    preview = local_stack._safe_action_preview(action)
    assert preview["resources"] == [
        {"resource_id": "cell-1", "resource_type": "CELL", "technology": "LTE"}
    ]
    assert preview["risk"] == "LOW"
    assert "must-not-leak" not in json.dumps(preview)


@pytest.mark.parametrize("resume_state", ("REMEDIATING", "VERIFYING"))
def test_real_runtime_resumes_after_approval_or_action_commit_response_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume_state: str,
) -> None:
    action = SimpleNamespace(
        action_hash="a" * 64,
        action_type="LOCAL_SIMULATION",
        target_resources=(),
        risk_level=SimpleNamespace(value="LOW"),
    )
    incident = SimpleNamespace(
        incident_id="incident-resume",
        revision=5 if resume_state == "REMEDIATING" else 6,
        status=SimpleNamespace(value=resume_state),
    )
    trigger = SimpleNamespace(
        incident_id=incident.incident_id,
        incident=SimpleNamespace(
            severity=SimpleNamespace(value="UNKNOWN"),
            technology=SimpleNamespace(value="LTE"),
            affected_resources=(),
        ),
    )

    class Repository:
        async def get(self, _incident_id: str):
            return incident

    class Detector:
        async def scan(self, _trace_id: str, *, workflow_id: str):
            assert workflow_id == "local-stack-detect-workflow-v1"
            return (trigger,)

    profile = SimpleNamespace(
        detector=Detector(),
        incident_repository=Repository(),
        rca_gateway=object(),
    )

    class LocalProfile:
        @staticmethod
        def open_existing(_config):
            return profile

    class Engine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(
                incident=incident,
                action=action,
                awaiting_approval=False,
            )

        async def decide(self, *_args, **kwargs):
            assert kwargs["expected_revision"] == 4
            return SimpleNamespace(incident=incident)

        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(
                incident=SimpleNamespace(status=SimpleNamespace(value="RESOLVED"))
            )

    local_module = SimpleNamespace(
        LocalProfile=LocalProfile,
        LocalProfileConfig=lambda **values: values,
    )
    governance_module = SimpleNamespace(LocalGovernanceEngine=Engine)
    monkeypatch.setitem(sys.modules, "telco_local", local_module)
    monkeypatch.setitem(sys.modules, "telco_local.governance", governance_module)

    workspace = local_stack.Workspace(tmp_path / "stack")
    runtime = local_stack.LocalStackRuntime(workspace)
    result = asyncio.run(
        runtime._run_demo(
            action_mode="simulate",
            confirm_incident=False,
            approve_action=True,
            reason="resume the exact reviewed simulation",
            expected_action_hash="a" * 64,
            expected_revision=4,
            verification_outcome="passed",
        )
    )
    assert result["state"] == "RESOLVED"
    assert result["closed_loop"] is True


def test_runtime_reports_expired_execution_grant_as_failed_without_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = SimpleNamespace(
        action_hash="b" * 64,
        action_type="LOCAL_SIMULATION",
        target_resources=(),
        risk_level=SimpleNamespace(value="LOW"),
    )
    failed_incident = SimpleNamespace(
        incident_id="incident-expired-execution",
        revision=6,
        status=SimpleNamespace(value="FAILED"),
    )
    trigger = SimpleNamespace(
        incident_id=failed_incident.incident_id,
        incident=SimpleNamespace(
            severity=SimpleNamespace(value="UNKNOWN"),
            technology=SimpleNamespace(value="LTE"),
            affected_resources=(),
        ),
    )

    class Repository:
        async def get(self, _incident_id: str):
            return failed_incident

    class Detector:
        async def scan(self, _trace_id: str, *, workflow_id: str):
            assert workflow_id == "local-stack-detect-workflow-v1"
            return (trigger,)

    profile = SimpleNamespace(
        detector=Detector(),
        incident_repository=Repository(),
        rca_gateway=object(),
    )

    class LocalProfile:
        @staticmethod
        def open_existing(_config):
            return profile

    class Engine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(
                incident=failed_incident,
                action=action,
                awaiting_approval=False,
            )

        async def decide(self, *_args, **_kwargs):
            return SimpleNamespace(incident=failed_incident)

        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(incident=failed_incident)

    monkeypatch.setitem(
        sys.modules,
        "telco_local",
        SimpleNamespace(
            LocalProfile=LocalProfile,
            LocalProfileConfig=lambda **values: values,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "telco_local.governance",
        SimpleNamespace(LocalGovernanceEngine=Engine),
    )

    result = asyncio.run(
        local_stack.LocalStackRuntime(
            local_stack.Workspace(tmp_path / "stack")
        )._run_demo(
            action_mode="simulate",
            confirm_incident=False,
            approve_action=True,
            reason="resume an expired local approval safely",
            expected_action_hash="b" * 64,
            expected_revision=4,
            verification_outcome="passed",
        )
    )
    assert result["state"] == "FAILED"
    assert result["closed_loop"] is False
    assert result["outcome"] == "APPROVAL_NOT_EFFECTIVE"
    assert result["approval"] == {
        "incident_confirmed": True,
        "action_approved": False,
        "decision_state": "FAILED",
    }


def test_reset_rejects_repository_root_even_with_forged_marker(tmp_path: Path) -> None:
    workspace = local_stack.REPOSITORY_ROOT
    stdout = StringIO()
    stderr = StringIO()
    code = local_stack.main(
        ["--workspace", str(workspace), "reset", "--yes"],
        stdout=stdout,
        stderr=stderr,
        runtime_factory=FakeRuntime,
    )
    assert code == 2
    assert json.loads(stderr.getvalue())["error"]["code"] == "unsafe_workspace"


def test_real_serve_uses_fixed_hardened_uvicorn_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = local_stack.Workspace(tmp_path / "stack")
    runtime = local_stack.LocalStackRuntime(workspace)
    application = object()
    captured = {}
    assurance_package = ModuleType("telco_assurance_agent")
    assurance_package.__path__ = []
    assurance_app = ModuleType("telco_assurance_agent.app")
    assurance_app.create_app = lambda _config: application
    assurance_transport = ModuleType("telco_assurance_agent.transport_http")

    class BoundedH11Protocol:
        pass

    assurance_transport.BoundedH11Protocol = BoundedH11Protocol
    uvicorn = ModuleType("uvicorn")
    uvicorn.run = lambda app, **kwargs: captured.update(app=app, **kwargs)
    monkeypatch.setitem(sys.modules, "telco_assurance_agent", assurance_package)
    monkeypatch.setitem(sys.modules, "telco_assurance_agent.app", assurance_app)
    monkeypatch.setitem(
        sys.modules,
        "telco_assurance_agent.transport_http",
        assurance_transport,
    )
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setattr(local_stack, "_port_available", lambda _port: True)
    monkeypatch.setattr(
        local_stack.LocalStackRuntime,
        "status",
        lambda self, *, port: {"database": {"initialized": True}},
    )
    monkeypatch.setattr(
        local_stack.LocalStackRuntime,
        "_assurance_config",
        lambda self, port: SimpleNamespace(port=port),
    )
    runtime.serve(port=8085)
    assert captured == {
        "app": application,
        "host": "127.0.0.1",
        "port": 8085,
        "workers": 1,
        "reload": False,
        "interface": "asgi3",
        "lifespan": "on",
        "http": BoundedH11Protocol,
        "ws": "none",
        "proxy_headers": False,
        "forwarded_allow_ips": "",
        "access_log": False,
        "server_header": False,
        "date_header": False,
        "limit_concurrency": None,
        "backlog": 16,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 10,
        "h11_max_incomplete_event_size": 16_384,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_junction_cannot_escape_artifact_write_or_reset(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    stack = tmp_path / "stack"
    artifacts = stack / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    artifacts.rmdir()
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(artifacts), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        workspace = local_stack.Workspace(stack)
        with pytest.raises(local_stack.SafeCliError) as caught:
            workspace.write_artifact("demo-result.json", {"safe": True})
        assert caught.value.code == "unsafe_workspace"
        assert not (outside / "demo-result.json").exists()

        code, payload, error = invoke(tmp_path, "reset", "--yes")
        assert (code, payload) == (2, None)
        assert error["error"]["code"] == "unsafe_workspace"
        assert outside.is_dir()
    finally:
        if getattr(artifacts, "is_junction", lambda: False)():
            os.rmdir(artifacts)
