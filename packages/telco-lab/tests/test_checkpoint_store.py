from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import threading

import pytest

import telco_lab.checkpoint_store as checkpoint_module
from telco_lab.checkpoint_store import (
    MAX_REPLAY_CHECKPOINT_BYTES,
    REPLAY_CHECKPOINT_SCHEMA_VERSION,
    ReplayCheckpointError,
    clear_replay_checkpoint,
    load_replay_checkpoint,
    run_persistent_paced_replay,
    save_replay_checkpoint,
)
from telco_lab.loopback_sink import (
    LoopbackHttpReplaySink,
    LoopbackHttpRequest,
    LoopbackHttpResponse,
    ReplayDeliveryCheckpoint,
)
from telco_lab.paced_runner import PacedReplayCancelled
from telco_lab.replay import build_replay_plan

from test_paced_runner import _BlockingClock, _FakeClock, _FakeTransport, _sink
from test_replay import REPLAY_START, _plan, _policy, _source


def _store_paths(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "checkpoint-workspace"
    workspace.mkdir()
    return workspace, workspace / "checkpoints"


def _checkpoint(plan, sequence_number: int) -> ReplayDeliveryCheckpoint:  # noqa: ANN001
    if sequence_number == 0:
        return ReplayDeliveryCheckpoint(plan.plan_id, 0, None, None)
    event = plan.events[sequence_number - 1]
    return ReplayDeliveryCheckpoint(
        plan.plan_id,
        sequence_number,
        event.source_event_id,
        event.payload_sha256,
    )


def _load(plan, paths: tuple[Path, Path]) -> ReplayDeliveryCheckpoint:  # noqa: ANN001
    return load_replay_checkpoint(
        plan,
        workspace=paths[0],
        checkpoint_directory=paths[1],
    )


def _save(
    plan,  # noqa: ANN001
    checkpoint: ReplayDeliveryCheckpoint,
    paths: tuple[Path, Path],
) -> ReplayDeliveryCheckpoint:
    return save_replay_checkpoint(
        plan,
        checkpoint,
        workspace=paths[0],
        checkpoint_directory=paths[1],
    )


def test_checkpoint_store_restart_round_trip_is_monotonic_and_clearable(
    tmp_path: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    paths = _store_paths(tmp_path)

    assert _load(plan, paths) == _checkpoint(plan, 0)
    first = _checkpoint(plan, 1)
    assert _save(plan, first, paths) == first
    assert _save(plan, first, paths) == first
    assert _load(plan, paths) == first

    with pytest.raises(ReplayCheckpointError) as regression:
        _save(plan, _checkpoint(plan, 0), paths)
    assert regression.value.code == "replay_checkpoint_regression"
    assert _load(plan, paths) == first

    assert clear_replay_checkpoint(
        plan,
        workspace=paths[0],
        checkpoint_directory=paths[1],
    )
    assert not clear_replay_checkpoint(
        plan,
        workspace=paths[0],
        checkpoint_directory=paths[1],
    )
    assert _load(plan, paths) == _checkpoint(plan, 0)


def test_checkpoint_store_rejects_cross_plan_and_old_window_without_deleting(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "lab")
    plan = _plan(source)
    paths = _store_paths(tmp_path)
    saved = _save(plan, _checkpoint(plan, 1), paths)

    other_plan = _plan(
        source,
        policy=_policy(endpoint="http://127.0.0.1:9081/v1/faults/replay"),
    )
    old_window_plan = build_replay_plan(
        source.lab,
        source.bundle,
        scenario="detector-demo",
        replay_window_start=REPLAY_START - timedelta(seconds=1),
        policy=_policy(),
        environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"},
    )
    for candidate in (other_plan, old_window_plan):
        with pytest.raises(ReplayCheckpointError) as mismatch:
            _load(candidate, paths)
        assert mismatch.value.code == "replay_checkpoint_invalid"
        with pytest.raises(ReplayCheckpointError) as unsafe_clear:
            clear_replay_checkpoint(
                candidate,
                workspace=paths[0],
                checkpoint_directory=paths[1],
            )
        assert unsafe_clear.value.code == "replay_checkpoint_invalid"

    assert _load(plan, paths) == saved


@pytest.mark.parametrize(
    "raw",
    (
        b'{"schema_version":"1.0","schema_version":"1.0"}\n',
        b'{"schema_version":"unknown"}\n',
        b"not-json\n",
    ),
)
def test_checkpoint_store_rejects_ambiguous_or_corrupt_json(
    tmp_path: Path,
    raw: bytes,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    paths = _store_paths(tmp_path)
    _load(plan, paths)
    checkpoint_path = paths[1] / checkpoint_module.CHECKPOINT_FILENAME
    checkpoint_path.write_bytes(raw)

    with pytest.raises(ReplayCheckpointError) as caught:
        _load(plan, paths)
    assert caught.value.code == "replay_checkpoint_invalid"
    assert str(paths[0]) not in str(caught.value)


def test_checkpoint_store_rejects_oversize_and_unknown_fields(tmp_path: Path) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    paths = _store_paths(tmp_path)
    saved = _save(plan, _checkpoint(plan, 1), paths)
    checkpoint_path = paths[1] / checkpoint_module.CHECKPOINT_FILENAME

    checkpoint_path.write_bytes(b"{" + b" " * MAX_REPLAY_CHECKPOINT_BYTES + b"}")
    with pytest.raises(ReplayCheckpointError) as oversize:
        _load(plan, paths)
    assert oversize.value.code == "replay_checkpoint_invalid"

    payload = {
        "schema_version": REPLAY_CHECKPOINT_SCHEMA_VERSION,
        "plan_id": saved.plan_id,
        "sequence_number": saved.sequence_number,
        "source_event_id": saved.source_event_id,
        "payload_sha256": saved.payload_sha256,
        "unexpected": True,
    }
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReplayCheckpointError) as unknown:
        _load(plan, paths)
    assert unknown.value.code == "replay_checkpoint_invalid"


def test_atomic_save_failure_preserves_previous_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    paths = _store_paths(tmp_path)
    first = _save(plan, _checkpoint(plan, 1), paths)

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("must not leak")

    monkeypatch.setattr(checkpoint_module.os, "replace", fail_replace)
    with pytest.raises(ReplayCheckpointError) as caught:
        _save(plan, _checkpoint(plan, 2), paths)
    assert caught.value.code == "replay_checkpoint_io"
    assert "must not leak" not in str(caught.value)
    assert _load(plan, paths) == first
    assert not list(paths[1].glob("*.part"))


def test_checkpoint_paths_reject_escape_symlink_and_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    workspace, checkpoint_directory = _store_paths(tmp_path)

    with pytest.raises(ReplayCheckpointError) as escaped:
        load_replay_checkpoint(
            plan,
            workspace=workspace,
            checkpoint_directory=workspace / ".." / "outside",
        )
    assert escaped.value.code == "replay_checkpoint_workspace_unsafe"

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = workspace / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(ReplayCheckpointError) as symlinked:
            load_replay_checkpoint(
                plan,
                workspace=workspace,
                checkpoint_directory=linked,
            )
        assert symlinked.value.code == "replay_checkpoint_workspace_unsafe"

    checkpoint_directory.mkdir(exist_ok=True)
    original = Path.is_junction
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == checkpoint_directory or original(self),
    )
    with pytest.raises(ReplayCheckpointError) as junction:
        _load(plan, (workspace, checkpoint_directory))
    assert junction.value.code == "replay_checkpoint_workspace_unsafe"


@pytest.mark.parametrize(
    "unsafe_workspace",
    (
        Path(r"\\server\share\workspace"),
        Path("//server/share/workspace"),
        Path(r"\\?\C:\workspace"),
        Path(r"\\.\C:\workspace"),
    ),
)
def test_unc_and_device_paths_fail_before_any_filesystem_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_workspace: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    probes = {"abspath": 0, "drive_type": 0, "exists": 0, "resolve": 0}

    def fake_abspath(value: object) -> str:
        probes["abspath"] += 1
        return str(value)

    def fake_drive_type(_root: str) -> int:
        probes["drive_type"] += 1
        return checkpoint_module._WINDOWS_DRIVE_FIXED

    def fake_exists(_path: Path) -> bool:
        probes["exists"] += 1
        return False

    def fake_resolve(
        path: Path,
        *_args: object,
        **_kwargs: object,
    ) -> Path:
        probes["resolve"] += 1
        return path

    caught: ReplayCheckpointError | None = None
    with monkeypatch.context() as scoped:
        scoped.setattr(checkpoint_module.os.path, "abspath", fake_abspath)
        scoped.setattr(checkpoint_module, "_windows_drive_type", fake_drive_type)
        scoped.setattr(Path, "exists", fake_exists)
        scoped.setattr(Path, "resolve", fake_resolve)
        try:
            load_replay_checkpoint(
                plan,
                workspace=unsafe_workspace,
                checkpoint_directory=unsafe_workspace / "checkpoints",
            )
        except ReplayCheckpointError as error:
            caught = error

    assert caught is not None
    assert caught.code == "replay_checkpoint_workspace_unsafe"
    assert probes == {"abspath": 0, "drive_type": 0, "exists": 0, "resolve": 0}


@pytest.mark.skipif(
    checkpoint_module.os.name != "nt",
    reason="GetDriveTypeW applies only on Windows",
)
@pytest.mark.parametrize("drive_mode", ("remote", "api_failure"))
def test_windows_checkpoint_paths_require_a_fixed_drive_before_path_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drive_mode: str,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    workspace, checkpoint_directory = _store_paths(tmp_path)
    probes = {"drive_type": 0, "exists": 0, "resolve": 0}

    def fake_drive_type(_root: str) -> int:
        probes["drive_type"] += 1
        if drive_mode == "api_failure":
            raise OSError("must fail closed")
        return 4  # DRIVE_REMOTE

    def fake_exists(_path: Path) -> bool:
        probes["exists"] += 1
        return False

    def fake_resolve(
        path: Path,
        *_args: object,
        **_kwargs: object,
    ) -> Path:
        probes["resolve"] += 1
        return path

    caught: ReplayCheckpointError | None = None
    with monkeypatch.context() as scoped:
        scoped.setattr(checkpoint_module, "_windows_drive_type", fake_drive_type)
        scoped.setattr(Path, "exists", fake_exists)
        scoped.setattr(Path, "resolve", fake_resolve)
        try:
            load_replay_checkpoint(
                plan,
                workspace=workspace,
                checkpoint_directory=checkpoint_directory,
            )
        except ReplayCheckpointError as error:
            caught = error

    assert caught is not None
    assert caught.code == "replay_checkpoint_workspace_unsafe"
    assert probes == {"drive_type": 1, "exists": 0, "resolve": 0}


class _InspectingTransport:
    def __init__(self, checkpoint_path: Path) -> None:
        self.checkpoint_path = checkpoint_path
        self.requests: list[LoopbackHttpRequest] = []

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse:
        if self.requests:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            assert payload["sequence_number"] == len(self.requests)
        self.requests.append(request)
        return LoopbackHttpResponse(status_code=202)


def test_persistent_runner_commits_each_ack_before_the_next_emit(
    tmp_path: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    paths = _store_paths(tmp_path)
    transport = _InspectingTransport(paths[1] / checkpoint_module.CHECKPOINT_FILENAME)

    result = asyncio.run(
        run_persistent_paced_replay(
            plan,
            _sink(plan, transport),
            workspace=paths[0],
            checkpoint_directory=paths[1],
            deadline_seconds=2,
            clock=_FakeClock(),
        )
    )

    assert result.plan_complete
    assert len(transport.requests) == 3
    assert _load(plan, paths) == result.checkpoint


def test_persistent_runner_restarts_after_deadline_and_cancellation(
    tmp_path: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab"))
    deadline_paths = _store_paths(tmp_path)
    deadline_result = asyncio.run(
        run_persistent_paced_replay(
            plan,
            _sink(plan, _FakeTransport()),
            workspace=deadline_paths[0],
            checkpoint_directory=deadline_paths[1],
            deadline_seconds=0.25,
            clock=_FakeClock(),
        )
    )
    assert deadline_result.deadline_exceeded
    assert _load(plan, deadline_paths).sequence_number == 1
    resumed_transport = _FakeTransport()
    resumed = asyncio.run(
        run_persistent_paced_replay(
            plan,
            _sink(plan, resumed_transport),
            workspace=deadline_paths[0],
            checkpoint_directory=deadline_paths[1],
            deadline_seconds=2,
            clock=_FakeClock(),
        )
    )
    assert resumed.plan_complete
    assert len(resumed_transport.requests) == 2

    cancel_workspace = tmp_path / "cancel-workspace"
    cancel_workspace.mkdir()
    cancel_paths = (cancel_workspace, cancel_workspace / "checkpoints")
    clock = _BlockingClock()

    async def cancel_after_first_ack() -> PacedReplayCancelled:
        task = asyncio.create_task(
            run_persistent_paced_replay(
                plan,
                _sink(plan, _FakeTransport()),
                workspace=cancel_paths[0],
                checkpoint_directory=cancel_paths[1],
                deadline_seconds=2,
                clock=clock,
            )
        )
        await clock.sleep_started.wait()
        task.cancel()
        with pytest.raises(PacedReplayCancelled) as caught:
            await task
        return caught.value

    cancelled = asyncio.run(cancel_after_first_ack())
    assert cancelled.checkpoint.sequence_number == 1
    assert _load(plan, cancel_paths).sequence_number == 1


def test_response_loss_keeps_old_checkpoint_and_reuses_idempotency_key(
    tmp_path: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab", offsets=(0,)))
    paths = _store_paths(tmp_path)
    first_transport = _FakeTransport(TimeoutError("response lost"))
    first = asyncio.run(
        run_persistent_paced_replay(
            plan,
            _sink(plan, first_transport),
            workspace=paths[0],
            checkpoint_directory=paths[1],
            deadline_seconds=1,
            clock=_FakeClock(),
        )
    )
    assert first.error_code == "replay_delivery_timeout"
    assert first.uncertain_sequence_number == 1
    assert _load(plan, paths).sequence_number == 0

    retry_transport = _FakeTransport(LoopbackHttpResponse(status_code=202))
    recovered = asyncio.run(
        run_persistent_paced_replay(
            plan,
            _sink(plan, retry_transport),
            workspace=paths[0],
            checkpoint_directory=paths[1],
            deadline_seconds=1,
            clock=_FakeClock(),
        )
    )
    first_key = dict(first_transport.requests[0].headers)["Idempotency-Key"]
    retry_key = dict(retry_transport.requests[0].headers)["Idempotency-Key"]
    assert first_key == retry_key
    assert recovered.plan_complete
    assert _load(plan, paths).sequence_number == 1


def test_ack_followed_by_checkpoint_write_loss_retries_the_same_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_source(tmp_path / "lab", offsets=(0,)))
    paths = _store_paths(tmp_path)
    first_transport = _FakeTransport(LoopbackHttpResponse(status_code=202))
    original_replace = checkpoint_module.os.replace

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("local commit response lost")

    monkeypatch.setattr(checkpoint_module.os, "replace", fail_replace)
    with pytest.raises(ReplayCheckpointError) as lost:
        asyncio.run(
            run_persistent_paced_replay(
                plan,
                _sink(plan, first_transport),
                workspace=paths[0],
                checkpoint_directory=paths[1],
                deadline_seconds=1,
                clock=_FakeClock(),
            )
        )
    assert lost.value.code == "replay_checkpoint_io"
    assert len(first_transport.requests) == 1
    assert _load(plan, paths).sequence_number == 0

    monkeypatch.setattr(checkpoint_module.os, "replace", original_replace)
    retry_transport = _FakeTransport(LoopbackHttpResponse(status_code=202))
    recovered = asyncio.run(
        run_persistent_paced_replay(
            plan,
            _sink(plan, retry_transport),
            workspace=paths[0],
            checkpoint_directory=paths[1],
            deadline_seconds=1,
            clock=_FakeClock(),
        )
    )
    assert (
        dict(first_transport.requests[0].headers)["Idempotency-Key"]
        == dict(retry_transport.requests[0].headers)["Idempotency-Key"]
    )
    assert recovered.plan_complete


class _BlockingTransport:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.requests: list[LoopbackHttpRequest] = []

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse:
        self.requests.append(request)
        self.started.set()
        self.release.wait(timeout=2)
        return LoopbackHttpResponse(status_code=202)


def test_persistent_runner_rejects_concurrent_writer_before_transport(
    tmp_path: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab", offsets=(0,)))
    paths = _store_paths(tmp_path)
    blocking = _BlockingTransport()
    competing = _FakeTransport()

    async def exercise() -> None:
        first = asyncio.create_task(
            run_persistent_paced_replay(
                plan,
                _sink(plan, blocking),
                workspace=paths[0],
                checkpoint_directory=paths[1],
                deadline_seconds=1,
            )
        )
        assert await asyncio.to_thread(blocking.started.wait, 1)
        with pytest.raises(ReplayCheckpointError) as busy:
            await run_persistent_paced_replay(
                plan,
                _sink(plan, competing),
                workspace=paths[0],
                checkpoint_directory=paths[1],
                deadline_seconds=1,
            )
        assert busy.value.code == "replay_checkpoint_busy"
        blocking.release.set()
        assert (await first).plan_complete

    asyncio.run(exercise())
    assert competing.requests == []


def test_cancel_during_emit_keeps_persistent_checkpoint_before_inflight_event(
    tmp_path: Path,
) -> None:
    plan = _plan(_source(tmp_path / "lab", offsets=(0,)))
    paths = _store_paths(tmp_path)
    blocking = _BlockingTransport()

    async def cancel_inflight() -> PacedReplayCancelled:
        task = asyncio.create_task(
            run_persistent_paced_replay(
                plan,
                _sink(plan, blocking),
                workspace=paths[0],
                checkpoint_directory=paths[1],
                deadline_seconds=1,
            )
        )
        assert await asyncio.to_thread(blocking.started.wait, 1)
        task.cancel()
        with pytest.raises(PacedReplayCancelled) as caught:
            await task
        blocking.release.set()
        return caught.value

    cancelled = asyncio.run(cancel_inflight())
    assert cancelled.uncertain_sequence_number == 1
    assert _load(plan, paths).sequence_number == 0


def test_checkpoint_api_is_public() -> None:
    from telco_lab import (
        ReplayCheckpointError as ExportedError,
        clear_replay_checkpoint as exported_clear,
        load_replay_checkpoint as exported_load,
        run_persistent_paced_replay as exported_run,
        save_replay_checkpoint as exported_save,
    )

    assert ExportedError is ReplayCheckpointError
    assert exported_load is load_replay_checkpoint
    assert exported_save is save_replay_checkpoint
    assert exported_clear is clear_replay_checkpoint
    assert exported_run is run_persistent_paced_replay
