"""Fail-closed persistence for caller-owned replay continuation claims.

The checkpoint stored here is deliberately not a receiver-signed
acknowledgement.  It only records the highest contiguous event whose bounded
HTTP receipt was accepted by the sender-side paced runner.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Final

from .loopback_sink import (
    LoopbackHttpReplaySink,
    ReplayDeliveryCheckpoint,
    ReplayDeliveryError,
    _checkpoint_for_sequence,
    _validated_checkpoint,
    _validated_plan,
)
from .paced_runner import (
    PacedReplayResult,
    ReplayPacingClock,
    ReplayRetryPolicy,
    _run_paced_replay,
)
from .replay import ReplayPlan
from .safe_json import StrictJsonError, load_strict_json


REPLAY_CHECKPOINT_SCHEMA_VERSION: Final = "replay-checkpoint/1.0"
MAX_REPLAY_CHECKPOINT_BYTES: Final = 4 * 1024
CHECKPOINT_FILENAME: Final = "replay-checkpoint.json"

_LOCK_FILENAME = ".replay-checkpoint.lock"
_WINDOWS_DRIVE_FIXED = 3
_CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "plan_id",
        "sequence_number",
        "source_event_id",
        "payload_sha256",
    }
)
_ERROR_MESSAGES = {
    "replay_checkpoint_arguments_invalid": "replay checkpoint arguments are invalid",
    "replay_checkpoint_workspace_unsafe": "replay checkpoint workspace is unsafe",
    "replay_checkpoint_busy": "another replay checkpoint writer is active",
    "replay_checkpoint_invalid": "replay checkpoint content is invalid",
    "replay_checkpoint_regression": "replay checkpoint cannot move backwards",
    "replay_checkpoint_io": "replay checkpoint storage failed",
}


class ReplayCheckpointError(RuntimeError):
    """Stable checkpoint error that never reflects a filesystem path or value."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            code = "replay_checkpoint_io"
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


def _reject_unc_or_device_text(value: Path) -> None:
    """Reject remote/device namespaces before any filesystem-facing call."""

    raw = str(value).replace("/", "\\")
    if raw.startswith("\\\\") or raw.startswith("\\??\\"):
        raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")


def _windows_drive_type(root: str) -> int:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = (ctypes.c_wchar_p,)
    get_drive_type.restype = ctypes.c_uint
    return int(get_drive_type(root))


def _require_fixed_windows_drive(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        anchor = path.anchor
        if not anchor or anchor.replace("/", "\\").startswith("\\\\"):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
        if _windows_drive_type(anchor) != _WINDOWS_DRIVE_FIXED:
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
    except ReplayCheckpointError:
        raise
    except Exception:
        raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None


def _is_link_like(path: Path) -> bool:
    """Treat every symlink, junction, or Windows reparse point as unsafe."""

    try:
        junction_check = getattr(path, "is_junction", None)
        if path.is_symlink() or bool(callable(junction_check) and junction_check()):
            return True
        if not path.exists():
            return False
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)
    except OSError:
        return True


def _absolute_without_parent_segments(value: object) -> Path:
    if not isinstance(value, Path):
        raise ReplayCheckpointError("replay_checkpoint_arguments_invalid")
    try:
        _reject_unc_or_device_text(value)
        if ".." in value.parts:
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
        absolute = Path(os.path.abspath(value))
        _require_fixed_windows_drive(absolute)
        return absolute
    except ReplayCheckpointError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None


def _reject_link_like_chain(path: Path) -> None:
    chain: list[Path] = []
    current = path
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for item in reversed(chain):
        if item.exists() and _is_link_like(item):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")


def _ensure_checkpoint_directory(workspace: Path, directory: Path) -> None:
    try:
        if not workspace.exists() or not workspace.is_dir() or _is_link_like(workspace):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
        _reject_link_like_chain(workspace)
        try:
            relative = directory.relative_to(workspace)
        except ValueError:
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None
        if not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")

        workspace_resolved = workspace.resolve(strict=True)
        cursor = workspace
        for part in relative.parts:
            cursor = cursor / part
            if cursor.exists() or cursor.is_symlink():
                if not cursor.is_dir() or _is_link_like(cursor):
                    raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
            else:
                cursor.mkdir(mode=0o700)
            if cursor.resolve(strict=True).parent != cursor.parent.resolve(strict=True):
                raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")

        directory_resolved = directory.resolve(strict=True)
        try:
            directory_resolved.relative_to(workspace_resolved)
        except ValueError:
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None
        if directory_resolved == workspace_resolved:
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
    except ReplayCheckpointError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None


class _CheckpointPaths:
    def __init__(self, workspace: object, checkpoint_directory: object) -> None:
        self.workspace = _absolute_without_parent_segments(workspace)
        self.directory = _absolute_without_parent_segments(checkpoint_directory)
        _ensure_checkpoint_directory(self.workspace, self.directory)
        self.checkpoint = self.directory / CHECKPOINT_FILENAME
        self.lock = self.directory / _LOCK_FILENAME
        self._validate_files()

    def _validate_files(self) -> None:
        try:
            for candidate in (self.checkpoint, self.lock):
                if candidate.exists() or candidate.is_symlink():
                    if _is_link_like(candidate) or not candidate.is_file():
                        raise ReplayCheckpointError(
                            "replay_checkpoint_workspace_unsafe"
                        )
                    if candidate.resolve(strict=True).parent != self.directory.resolve(
                        strict=True
                    ):
                        raise ReplayCheckpointError(
                            "replay_checkpoint_workspace_unsafe"
                        )
        except ReplayCheckpointError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None

    def revalidate(self) -> None:
        _ensure_checkpoint_directory(self.workspace, self.directory)
        self._validate_files()


def _lock_descriptor(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    flags |= no_follow
    try:
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        path_metadata = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
        if metadata.st_size == 0:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except ReplayCheckpointError:
        try:
            os.close(descriptor)  # type: ignore[possibly-undefined]
        except (OSError, UnboundLocalError):
            pass
        raise
    except OSError:
        try:
            os.close(descriptor)  # type: ignore[possibly-undefined]
        except (OSError, UnboundLocalError):
            pass
        raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe") from None


def _acquire_lock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        busy_errors = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
        if getattr(exc, "errno", None) in busy_errors:
            raise ReplayCheckpointError("replay_checkpoint_busy") from None
        raise ReplayCheckpointError("replay_checkpoint_io") from None


def _release_lock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def _exclusive(paths: _CheckpointPaths) -> Iterator[None]:
    paths.revalidate()
    descriptor = _lock_descriptor(paths.lock)
    try:
        _acquire_lock(descriptor)
        paths.revalidate()
        yield
    finally:
        _release_lock(descriptor)
        try:
            os.close(descriptor)
        except OSError:
            pass


def _validated_replay_plan(value: object) -> ReplayPlan:
    try:
        return _validated_plan(value)
    except ReplayDeliveryError:
        raise ReplayCheckpointError("replay_checkpoint_arguments_invalid") from None


def _canonical_checkpoint(
    plan: ReplayPlan,
    checkpoint: object,
) -> ReplayDeliveryCheckpoint:
    try:
        return _validated_checkpoint(plan, checkpoint)
    except ReplayDeliveryError:
        raise ReplayCheckpointError("replay_checkpoint_invalid") from None


def _read_locked(
    plan: ReplayPlan,
    paths: _CheckpointPaths,
) -> ReplayDeliveryCheckpoint:
    try:
        paths.revalidate()
        if not paths.checkpoint.exists():
            return _checkpoint_for_sequence(plan, 0)
        if _is_link_like(paths.checkpoint) or not paths.checkpoint.is_file():
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
        with paths.checkpoint.open("rb") as stream:
            raw = stream.read(MAX_REPLAY_CHECKPOINT_BYTES + 1)
        payload = load_strict_json(
            raw,
            max_bytes=MAX_REPLAY_CHECKPOINT_BYTES,
            max_depth=4,
        )
        if type(payload) is not dict or frozenset(payload) != _CHECKPOINT_KEYS:
            raise ReplayCheckpointError("replay_checkpoint_invalid")
        if payload.get("schema_version") != REPLAY_CHECKPOINT_SCHEMA_VERSION:
            raise ReplayCheckpointError("replay_checkpoint_invalid")
        candidate = ReplayDeliveryCheckpoint(
            plan_id=payload.get("plan_id"),
            sequence_number=payload.get("sequence_number"),
            source_event_id=payload.get("source_event_id"),
            payload_sha256=payload.get("payload_sha256"),
        )
        return _canonical_checkpoint(plan, candidate)
    except ReplayCheckpointError:
        raise
    except (OSError, UnicodeError, StrictJsonError, TypeError, ValueError):
        raise ReplayCheckpointError("replay_checkpoint_invalid") from None


def _serialized(checkpoint: ReplayDeliveryCheckpoint) -> bytes:
    try:
        payload = {
            "schema_version": REPLAY_CHECKPOINT_SCHEMA_VERSION,
            "plan_id": checkpoint.plan_id,
            "sequence_number": checkpoint.sequence_number,
            "source_event_id": checkpoint.source_event_id,
            "payload_sha256": checkpoint.payload_sha256,
        }
        raw = (
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
        if len(raw) > MAX_REPLAY_CHECKPOINT_BYTES:
            raise ReplayCheckpointError("replay_checkpoint_invalid")
        return raw
    except ReplayCheckpointError:
        raise
    except (TypeError, ValueError, UnicodeError):
        raise ReplayCheckpointError("replay_checkpoint_invalid") from None


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        raise ReplayCheckpointError("replay_checkpoint_io") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_locked(
    paths: _CheckpointPaths,
    checkpoint: ReplayDeliveryCheckpoint,
) -> None:
    temporary: Path | None = None
    try:
        paths.revalidate()
        raw = _serialized(checkpoint)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".replay-checkpoint.",
            suffix=".part",
            dir=paths.directory,
            delete=False,
        )
        temporary = Path(handle.name)
        with handle:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if paths.checkpoint.exists() and _is_link_like(paths.checkpoint):
            raise ReplayCheckpointError("replay_checkpoint_workspace_unsafe")
        os.replace(temporary, paths.checkpoint)
        temporary = None
        _sync_directory(paths.directory)
    except ReplayCheckpointError:
        raise
    except OSError:
        raise ReplayCheckpointError("replay_checkpoint_io") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _save_locked(
    plan: ReplayPlan,
    checkpoint: object,
    paths: _CheckpointPaths,
) -> ReplayDeliveryCheckpoint:
    candidate = _canonical_checkpoint(plan, checkpoint)
    current = _read_locked(plan, paths)
    if candidate.sequence_number < current.sequence_number:
        raise ReplayCheckpointError("replay_checkpoint_regression")
    if candidate == current:
        return current
    _write_locked(paths, candidate)
    return candidate


def load_replay_checkpoint(
    plan: ReplayPlan,
    *,
    workspace: Path,
    checkpoint_directory: Path,
) -> ReplayDeliveryCheckpoint:
    """Load one exact plan checkpoint, or its canonical zero when absent."""

    normalized = _validated_replay_plan(plan)
    paths = _CheckpointPaths(workspace, checkpoint_directory)
    with _exclusive(paths):
        return _read_locked(normalized, paths)


def save_replay_checkpoint(
    plan: ReplayPlan,
    checkpoint: ReplayDeliveryCheckpoint,
    *,
    workspace: Path,
    checkpoint_directory: Path,
) -> ReplayDeliveryCheckpoint:
    """Atomically persist a canonical checkpoint without allowing regression."""

    normalized = _validated_replay_plan(plan)
    paths = _CheckpointPaths(workspace, checkpoint_directory)
    with _exclusive(paths):
        return _save_locked(normalized, checkpoint, paths)


def clear_replay_checkpoint(
    plan: ReplayPlan,
    *,
    workspace: Path,
    checkpoint_directory: Path,
) -> bool:
    """Clear only a valid checkpoint that belongs to the supplied plan."""

    normalized = _validated_replay_plan(plan)
    paths = _CheckpointPaths(workspace, checkpoint_directory)
    with _exclusive(paths):
        if not paths.checkpoint.exists():
            return False
        _read_locked(normalized, paths)
        try:
            paths.checkpoint.unlink()
            _sync_directory(paths.directory)
            return True
        except OSError:
            raise ReplayCheckpointError("replay_checkpoint_io") from None


async def run_persistent_paced_replay(
    plan: ReplayPlan,
    sink: LoopbackHttpReplaySink,
    *,
    workspace: Path,
    checkpoint_directory: Path,
    retry_policy: ReplayRetryPolicy = ReplayRetryPolicy.NONE,
    deadline_seconds: float | None = None,
    clock: ReplayPacingClock | None = None,
) -> PacedReplayResult:
    """Resume a paced run and commit each accepted receipt before advancing.

    If the receiver commits but the response or local checkpoint write is lost,
    the older checkpoint is intentionally retained.  A later call sends the
    same event with the same idempotency key and relies on receiver-side exact
    replay.  The local file never becomes an authenticated acknowledgement.
    """

    normalized = _validated_replay_plan(plan)
    paths = _CheckpointPaths(workspace, checkpoint_directory)
    with _exclusive(paths):
        checkpoint = _read_locked(normalized, paths)

        def commit(candidate: ReplayDeliveryCheckpoint) -> None:
            _save_locked(normalized, candidate, paths)

        return await _run_paced_replay(
            normalized,
            sink,
            checkpoint=checkpoint,
            retry_policy=retry_policy,
            deadline_seconds=deadline_seconds,
            clock=clock,
            checkpoint_committer=commit,
        )


__all__ = [
    "CHECKPOINT_FILENAME",
    "MAX_REPLAY_CHECKPOINT_BYTES",
    "REPLAY_CHECKPOINT_SCHEMA_VERSION",
    "ReplayCheckpointError",
    "clear_replay_checkpoint",
    "load_replay_checkpoint",
    "run_persistent_paced_replay",
    "save_replay_checkpoint",
]
