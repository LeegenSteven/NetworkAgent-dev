from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from telco_lab import emit_local_runtime_trace_event


MODULE_PATH = Path(__file__).parents[1] / "run_runtime_trace_demo.py"
SPEC = importlib.util.spec_from_file_location(
    "networkagent_runtime_trace_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
runtime_demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime_demo
SPEC.loader.exec_module(runtime_demo)


TRACE_VALUE = "local-replay-trace-" + "1" * 64
EXPECTED_OPERATIONS = (
    "REPLAY_REQUEST_VALIDATED",
    "INCIDENT_DURABLE_READBACK",
    "REPLAY_RESPONSE_ACCEPTED",
    "REPLAY_DELIVERY_ACKNOWLEDGED",
    "ANALYZE_REQUEST_VALIDATED",
    "ANALYZE_COMPLETED",
)
EXPECTED_COMPONENTS = ("sender", "repository", "receiver", "sender", "a2a", "a2a")


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
        assert cwd.is_absolute()
        assert timeout == 10
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        if arguments == ("git", "rev-parse", "--verify", "HEAD"):
            return subprocess.CompletedProcess(arguments, 0, b"a" * 40 + b"\n", b"")
        assert arguments == (
            "git",
            "status",
            "--porcelain",
            "--untracked-files=no",
        )
        return subprocess.CompletedProcess(
            arguments, 0, b"" if self.clean else b" M tracked.py\n", b""
        )


def _event(operation: str, component: str):
    return runtime_demo.LocalRuntimeTraceEvent(
        schema=runtime_demo.LOCAL_RUNTIME_TRACE_SCHEMA,
        emitted_at="2026-08-31T02:03:04.000000Z",
        trace_id=TRACE_VALUE,
        component=component,
        operation=operation,
        outcome="OK",
        error_code=None,
    )


class FakeScenario:
    def __init__(self) -> None:
        self.completed = False

    def __call__(self, *, collector, **_kwargs):  # noqa: ANN001
        for component, operation in zip(
            EXPECTED_COMPONENTS, EXPECTED_OPERATIONS, strict=True
        ):
            emit_local_runtime_trace_event(
                collector,
                trace_id=TRACE_VALUE,
                component=component,
                operation=operation,
                clock=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
            )
        self.completed = True
        return runtime_demo.ScenarioEvidence.fixed_success()


def _invoke(
    tmp_path: Path,
    *arguments: str,
    scenario=None,  # noqa: ANN001
    collector_factory=None,  # noqa: ANN001
    process_runner=None,  # noqa: ANN001
):
    stdout = StringIO()
    stderr = StringIO()
    code = runtime_demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        repository_root=tmp_path,
        utc_now=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
        random_token=lambda: "1a2b3c4d5e6f",
        process_runner=process_runner or FakeProcessRunner(),
        scenario_runner=scenario or FakeScenario(),
        collector_factory=collector_factory or runtime_demo.RawEventCollector,
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value))
    return set()


def test_fixed_fake_chain_is_private_atomic_and_source_bound(tmp_path: Path) -> None:
    scenario = FakeScenario()
    code, payload, error = _invoke(
        tmp_path,
        "--approve-local-simulation",
        scenario=scenario,
    )

    assert code == 0 and error is None and scenario.completed is True
    assert payload["schema"] == "networkagent-local-runtime-trace-evidence/1.0"
    assert payload["classification"] == ("LOCAL_SINGLE_PROCESS_LOOPBACK_TRACE_EVIDENCE")
    assert payload["ok"] is True
    assert payload["source"] == {
        "binding_stable": True,
        "commit_bound": True,
        "commit_sha": "a" * 40,
        "git_available": True,
        "tracked_clean": True,
    }
    assert payload["scope"] == {
        "action_mode": "DISABLED",
        "analyze_semantics": "TRANSPORT_WRITE_DOMAIN_UNCHANGED",
        "execution": "SINGLE_PROCESS",
        "network": "REAL_LOOPBACK_TCP",
        "scenario": "FIXED_BUBBLERAN_SINGLE_EVENT",
    }
    assert payload["proof"] == {
        "all_outcomes_ok": True,
        "binding_checks": 6,
        "component_count": 4,
        "event_count": 6,
        "expected_order": True,
        "governance_zero_delta": {
            "actions": 0,
            "approvals": 0,
            "executions": 0,
            "verifications": 0,
        },
        "single_correlation": True,
        "successful_run_cleanup": True,
        "write_semantics": {
            "canonical_domain_unchanged": True,
            "changed_table_count": 1,
            "transport_state_changed": True,
            "unchanged_table_count": 9,
            "whole_database_read_only_claimed": False,
        },
    }
    assert payload["privacy"] == {
        "absolute_paths_recorded": False,
        "domain_identifiers_recorded": False,
        "raw_events_in_release_summary": False,
        "raw_payloads_recorded": False,
        "status": "PASS",
    }
    assert (
        "IDENTITY_UNKNOWN_OR_RACED_RESIDUE_AUTO_CLEANUP"
        in payload["coverage"]["not_claimed"]
    )
    assert payload["release"] == {
        "eligible": True,
        "source_state": "COMMIT_BOUND",
    }
    forbidden = {
        "trace_id",
        "source_event_id",
        "incident_id",
        "resource_id",
        "idempotency_key",
        "task_id",
        "context_id",
        "workflow_id",
        "path",
        "body",
        "metrics",
    }
    assert _nested_keys(payload).isdisjoint(forbidden)

    root = tmp_path / ".local" / "networkagent-runtime-trace"
    run_directory = next(root.iterdir())
    assert {child.name for child in run_directory.iterdir()} == {
        "local-runtime-events.jsonl",
        "local-runtime-trace-report.json",
    }
    raw_lines = (
        (run_directory / "local-runtime-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(raw_lines) == 6
    assert [json.loads(line)["operation"] for line in raw_lines] == list(
        EXPECTED_OPERATIONS
    )
    report_bytes = (run_directory / "local-runtime-trace-report.json").read_bytes()
    assert len(report_bytes) == payload["report"]["bytes"]
    assert (
        runtime_demo.hashlib.sha256(report_bytes).hexdigest()
        == payload["report"]["sha256"]
    )
    assert json.loads(report_bytes) == {
        key: value for key, value in payload.items() if key != "report"
    }


def test_sink_failure_does_not_hide_business_success_and_exits_two(
    tmp_path: Path,
) -> None:
    scenario = FakeScenario()

    class BrokenCollector(runtime_demo.RawEventCollector):
        def __call__(self, event):  # noqa: ANN001
            raise OSError("simulated trace sink loss")

    code, payload, error = _invoke(
        tmp_path,
        "--approve-local-simulation",
        scenario=scenario,
        collector_factory=BrokenCollector,
    )

    assert scenario.completed is True
    assert code == 2 and payload is None
    assert error == {
        "error": {
            "code": "trace_contract_failed",
            "message": "local runtime trace evidence violated its fixed contract",
        },
        "ok": False,
        "schema": "networkagent-local-runtime-trace-evidence/1.0",
    }


def test_cli_is_fixed_and_requires_explicit_local_approval(tmp_path: Path) -> None:
    for arguments, code in [
        ((), "confirmation_required"),
        (("--approve-local-simulation", "extra"), "invalid_arguments"),
        (("--unknown",), "invalid_arguments"),
    ]:
        exit_code, payload, error = _invoke(tmp_path, *arguments)
        assert exit_code == 2 and payload is None
        assert error["error"]["code"] == code


def _create_run(tmp_path: Path, token: str = "abcdef123456"):
    return runtime_demo._create_run_directory(
        tmp_path,
        utc_now=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
        random_token=lambda: token,
    )


def test_dirty_source_is_successful_worktree_evidence_but_not_release_eligible(
    tmp_path: Path,
) -> None:
    code, payload, error = _invoke(
        tmp_path,
        "--approve-local-simulation",
        process_runner=FakeProcessRunner(clean=False),
    )

    assert code == 0 and error is None
    assert payload["classification"] == ("LOCAL_SINGLE_PROCESS_LOOPBACK_TRACE_EVIDENCE")
    assert payload["source"]["commit_bound"] is False
    assert payload["source"]["tracked_clean"] is False
    assert payload["release"] == {
        "eligible": False,
        "source_state": "WORKTREE_ONLY",
    }


def test_existing_run_collision_is_preserved(tmp_path: Path) -> None:
    candidate = (
        tmp_path
        / ".local"
        / "networkagent-runtime-trace"
        / "20260831T020304Z-1a2b3c4d5e6f"
    )
    candidate.mkdir(parents=True)
    marker = candidate / "owned-by-someone-else"
    marker.write_bytes(b"preserve")

    code, payload, error = _invoke(tmp_path, "--approve-local-simulation")

    assert code == 2 and payload is None
    assert error["error"]["code"] == "report_write_failed"
    assert marker.read_bytes() == b"preserve"


def test_candidate_symlink_collision_is_preserved(tmp_path: Path) -> None:
    evidence_root = tmp_path / ".local" / "networkagent-runtime-trace"
    evidence_root.mkdir(parents=True)
    target = tmp_path / "outside-run"
    target.mkdir()
    marker = target / "marker"
    marker.write_bytes(b"preserve")
    candidate = evidence_root / "20260831T020304Z-abcdef123456"
    try:
        candidate.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        _create_run(tmp_path)

    assert captured.value.code == "report_write_failed"
    assert candidate.is_symlink()
    assert marker.read_bytes() == b"preserve"


def test_run_directory_first_identity_failure_preserves_unknown_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = runtime_demo._directory_identity
    failed = False

    def fail_candidate(path: Path, *, code: str):
        nonlocal failed
        if path.name == "20260831T020304Z-abcdef123456" and not failed:
            failed = True
            raise runtime_demo.RuntimeTraceEvidenceError(code)
        return original(path, code=code)

    monkeypatch.setattr(runtime_demo, "_directory_identity", fail_candidate)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        _create_run(tmp_path)

    candidate = (
        tmp_path
        / ".local"
        / "networkagent-runtime-trace"
        / "20260831T020304Z-abcdef123456"
    )
    assert captured.value.code == "report_write_failed"
    assert candidate.is_dir()
    assert tuple(candidate.iterdir()) == ()


def test_run_parent_replacement_breaks_chain_without_deleting_either_tree(
    tmp_path: Path,
) -> None:
    run, _token = _create_run(tmp_path)
    local_root = tmp_path / ".local"
    moved = tmp_path / "moved-local"
    local_root.rename(moved)
    local_root.mkdir()

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        run.validate(code="trace_contract_failed")

    assert captured.value.code == "trace_contract_failed"
    assert run.directory.relative_to(tmp_path / ".local")
    assert (moved / "networkagent-runtime-trace" / run.directory.name).is_dir()
    assert local_root.is_dir()


def test_symlink_evidence_root_is_rejected_and_target_preserved(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / ".local"
    local_root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    marker = target / "marker"
    marker.write_bytes(b"preserve")
    evidence_root = local_root / "networkagent-runtime-trace"
    try:
        evidence_root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        _create_run(tmp_path)

    assert captured.value.code == "report_write_failed"
    assert marker.read_bytes() == b"preserve"
    assert evidence_root.is_symlink()


@pytest.mark.parametrize("kind", ["file", "symlink", "hardlink"])
def test_raw_path_collisions_are_rejected_and_preserved(
    tmp_path: Path, kind: str
) -> None:
    raw_path = tmp_path / "events.jsonl"
    original = tmp_path / "original"
    original.write_bytes(b"preserve")
    if kind == "file":
        raw_path.write_bytes(b"preserve")
    elif kind == "symlink":
        try:
            raw_path.symlink_to(original)
        except OSError:
            pytest.skip("file symlinks are unavailable")
    else:
        os.link(original, raw_path)

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo.RawEventCollector(raw_path)

    assert captured.value.code == "trace_contract_failed"
    assert original.read_bytes() == b"preserve"
    assert os.path.lexists(raw_path)


def test_raw_first_fstat_failure_closes_handle_and_preserves_unknown_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_path = tmp_path / "events.jsonl"
    original_fstat = runtime_demo.os.fstat
    failed = False

    def fail_once(descriptor: int):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated first identity failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(runtime_demo.os, "fstat", fail_once)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo.RawEventCollector(raw_path)

    assert captured.value.code == "trace_contract_failed"
    assert raw_path.is_file()
    raw_path.rename(tmp_path / "preserved-unknown")


def test_raw_first_lstat_failure_closes_handle_and_preserves_unknown_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_path = tmp_path / "events.jsonl"
    original_identity = runtime_demo._path_file_identity
    failed = False

    def fail_once(path: Path, *, code: str):
        nonlocal failed
        if path.name == raw_path.name and not failed:
            failed = True
            raise runtime_demo.RuntimeTraceEvidenceError(code)
        return original_identity(path, code=code)

    monkeypatch.setattr(runtime_demo, "_path_file_identity", fail_once)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo.RawEventCollector(raw_path)

    assert captured.value.code == "trace_contract_failed"
    assert raw_path.is_file()
    raw_path.rename(tmp_path / "preserved-unknown")


def test_raw_write_failure_is_best_effort_but_remains_wrapper_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_path = tmp_path / "events.jsonl"
    collector = runtime_demo.RawEventCollector(raw_path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated write durability failure")

    monkeypatch.setattr(runtime_demo.os, "fsync", fail_fsync)
    emitted = emit_local_runtime_trace_event(
        collector,
        trace_id=TRACE_VALUE,
        component=EXPECTED_COMPONENTS[0],
        operation=EXPECTED_OPERATIONS[0],
        clock=lambda: datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC),
    )

    assert emitted is False
    assert collector.events == []
    assert raw_path.is_file()
    collector.close_safely()


def test_raw_hardlink_added_after_write_is_not_adopted_or_deleted(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "events.jsonl"
    alias = tmp_path / "alias.jsonl"
    collector = runtime_demo.RawEventCollector(raw_path)
    collector(_event(EXPECTED_OPERATIONS[0], EXPECTED_COMPONENTS[0]))
    os.link(raw_path, alias)
    try:
        with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
            collector.close_and_validate()
        assert captured.value.code == "trace_contract_failed"
        assert raw_path.read_bytes() == alias.read_bytes()
        assert os.stat(raw_path).st_nlink == 2
    finally:
        collector.close_safely()


def test_raw_replacement_with_same_bytes_is_rejected_and_preserved(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "events.jsonl"
    collector = runtime_demo.RawEventCollector(raw_path)
    collector(_event(EXPECTED_OPERATIONS[0], EXPECTED_COMPONENTS[0]))
    replacement = tmp_path / "replacement"
    replacement.write_bytes(raw_path.read_bytes())
    try:
        os.replace(replacement, raw_path)
    except PermissionError:
        collector.close_safely()
        pytest.skip("the platform does not replace an open file")
    try:
        with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
            collector.close_and_validate()
        assert captured.value.code == "trace_contract_failed"
        assert raw_path.read_bytes().endswith(b"\n")
    finally:
        collector.close_safely()


def test_cleanup_rejects_symlink_and_preserves_external_target(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"preserve")
    link = work / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._remove_owned_tree(
            work,
            expected_root_identity=runtime_demo._directory_identity(
                work, code="cleanup_failed"
            ),
        )

    assert captured.value.code == "cleanup_failed"
    assert link.is_symlink()
    assert target.read_bytes() == b"preserve"


def test_cleanup_rejects_hardlinked_child_and_preserves_both_names(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    child = work / "state"
    os.link(outside, child)

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._remove_owned_tree(
            work,
            expected_root_identity=runtime_demo._directory_identity(
                work, code="cleanup_failed"
            ),
        )

    assert captured.value.code == "cleanup_failed"
    assert child.read_bytes() == outside.read_bytes() == b"preserve"
    assert os.stat(child).st_nlink == 2


def test_cleanup_preserves_file_replaced_after_identity_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    child = work / "state"
    child.write_bytes(b"owned")
    root_identity = runtime_demo._directory_identity(work, code="cleanup_failed")
    original = runtime_demo._path_file_identity
    raced = False

    def replace_before_check(path: Path, *, code: str):
        nonlocal raced
        if path == child and not raced:
            raced = True
            child.unlink()
            child.write_bytes(b"replacement")
        return original(path, code=code)

    monkeypatch.setattr(runtime_demo, "_path_file_identity", replace_before_check)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._remove_owned_tree(work, expected_root_identity=root_identity)

    assert captured.value.code == "cleanup_failed"
    assert child.read_bytes() == b"replacement"
    assert work.is_dir()


def test_report_collisions_and_link_substitution_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, token = _create_run(tmp_path)
    final_path = run.directory / runtime_demo.REPORT_NAME
    final_path.write_bytes(b"existing")
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError):
        runtime_demo._write_report(run, {"ok": True}, token=token)
    assert final_path.read_bytes() == b"existing"

    final_path.unlink()
    original_link = runtime_demo.os.link

    def substitute(_source, target, *, follow_symlinks):  # noqa: ANN001
        assert follow_symlinks is False
        Path(target).write_bytes(b"replacement")

    monkeypatch.setattr(runtime_demo.os, "link", substitute)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._write_report(run, {"ok": True}, token=token)
    monkeypatch.setattr(runtime_demo.os, "link", original_link)

    assert captured.value.code == "report_write_failed"
    assert final_path.read_bytes() == b"replacement"


def test_report_first_fstat_failure_preserves_unknown_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, token = _create_run(tmp_path)
    original_fstat = runtime_demo.os.fstat
    failed = False

    def fail_once(descriptor: int):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated first identity failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(runtime_demo.os, "fstat", fail_once)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._write_report(run, {"ok": True}, token=token)

    temporary = run.directory / f".{runtime_demo.REPORT_NAME}.{token}.tmp"
    assert captured.value.code == "report_write_failed"
    assert temporary.is_file()
    assert json.loads(temporary.read_bytes()) == {"ok": True}


def test_report_first_lstat_failure_preserves_unknown_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, token = _create_run(tmp_path)
    temporary = run.directory / f".{runtime_demo.REPORT_NAME}.{token}.tmp"
    original_identity = runtime_demo._path_file_identity
    failed = False

    def fail_once(path: Path, *, code: str):
        nonlocal failed
        if path.name == temporary.name and not failed:
            failed = True
            raise runtime_demo.RuntimeTraceEvidenceError(code)
        return original_identity(path, code=code)

    monkeypatch.setattr(runtime_demo, "_path_file_identity", fail_once)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._write_report(run, {"ok": True}, token=token)

    assert captured.value.code == "report_write_failed"
    assert temporary.is_file()
    assert json.loads(temporary.read_bytes()) == {"ok": True}


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_report_final_link_collisions_are_preserved(tmp_path: Path, kind: str) -> None:
    run, token = _create_run(tmp_path)
    final_path = run.directory / runtime_demo.REPORT_NAME
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    if kind == "symlink":
        try:
            final_path.symlink_to(outside)
        except OSError:
            pytest.skip("file symlinks are unavailable")
    else:
        os.link(outside, final_path)

    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._write_report(run, {"ok": True}, token=token)

    assert captured.value.code == "report_write_failed"
    assert outside.read_bytes() == b"preserve"
    assert os.path.lexists(final_path)


def test_report_final_bytes_sha_and_identity_reject_same_content_replacement(
    tmp_path: Path,
) -> None:
    run, token = _create_run(tmp_path)
    body = {"ok": True, "schema": "fixed"}
    size, digest, identity = runtime_demo._write_report(run, body, token=token)
    final_path = run.directory / runtime_demo.REPORT_NAME
    expected = runtime_demo._canonical_bytes(body)

    assert size == len(expected)
    assert digest == runtime_demo.hashlib.sha256(expected).hexdigest()
    assert (
        runtime_demo._read_identity_bound_file(
            final_path,
            expected_identity=identity,
            maximum_bytes=runtime_demo.MAX_REPORT_BYTES,
            code="report_write_failed",
        )
        == expected
    )

    replacement = run.directory / "replacement"
    replacement.write_bytes(expected)
    os.replace(replacement, final_path)
    with pytest.raises(runtime_demo.RuntimeTraceEvidenceError) as captured:
        runtime_demo._read_identity_bound_file(
            final_path,
            expected_identity=identity,
            maximum_bytes=runtime_demo.MAX_REPORT_BYTES,
            code="report_write_failed",
        )
    assert captured.value.code == "report_write_failed"
    assert final_path.read_bytes() == expected


def test_all_stderr_errors_are_stable_and_non_reflective() -> None:
    for code in (
        "cleanup_failed",
        "command_failed",
        "confirmation_required",
        "invalid_arguments",
        "report_write_failed",
        "trace_contract_failed",
        "unrecognized-secret-path",
    ):
        payload = runtime_demo._error_payload(code)
        serialized = json.dumps(payload, sort_keys=True)
        assert set(payload) == {"error", "ok", "schema"}
        assert set(payload["error"]) == {"code", "message"}
        assert "unrecognized-secret-path" not in serialized
        assert TRACE_VALUE not in serialized
        assert str(Path.cwd()) not in serialized
