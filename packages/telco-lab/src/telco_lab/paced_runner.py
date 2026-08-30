"""Bounded wall-clock scheduling for the loopback replay transport only.

The runner consumes an already validated replay plan and its loopback-only sink.
It is not a Canonical Fault receiver, checkpoint store, or Cloud delivery path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
import math
from typing import Protocol, runtime_checkable

from .loopback_sink import (
    LoopbackHttpReplaySink,
    ReplayDeliveryCheckpoint,
    ReplayDeliveryError,
    ReplayDeliveryReceipt,
    _checkpoint_for_sequence,
    _validated_checkpoint,
    _validated_plan,
)
from .replay import HARD_MAX_DURATION_SECONDS, ReplayPlan
from .schema import canonical_json_bytes


MIN_PACED_REPLAY_DEADLINE_SECONDS = 0.001
MAX_PACED_REPLAY_DEADLINE_SECONDS = HARD_MAX_DURATION_SECONDS
MAX_PACED_REPLAY_RETRIES = 2

_CLOCK_TOLERANCE_SECONDS = 1e-9
_TRANSIENT_DELIVERY_CODES = frozenset(
    {
        "replay_delivery_network",
        "replay_delivery_timeout",
    }
)


class ReplayRetryPolicy(str, Enum):
    """Frozen retry choices; callers cannot expand the transient allowlist."""

    NONE = "none"
    TRANSIENT_ONCE = "transient-once"
    TRANSIENT_TWICE = "transient-twice"


_RETRY_BACKOFFS: dict[ReplayRetryPolicy, tuple[float, ...]] = {
    ReplayRetryPolicy.NONE: (),
    ReplayRetryPolicy.TRANSIENT_ONCE: (0.25,),
    ReplayRetryPolicy.TRANSIENT_TWICE: (0.25, 1.0),
}


@runtime_checkable
class ReplayPacingClock(Protocol):
    """Injectable monotonic clock used for deterministic pacing tests."""

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class _AsyncioReplayClock:
    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True, slots=True)
class PacedReplayResult:
    """Finite paced-run outcome with a plan-bound continuation claim."""

    checkpoint: ReplayDeliveryCheckpoint
    selected_count: int
    attempted_count: int
    delivered_count: int
    retry_count: int
    failed_sequence_number: int | None
    error_code: str | None
    deadline_exceeded: bool
    uncertain_sequence_number: int | None
    plan_complete: bool
    elapsed_seconds: float


class PacedReplayCancelled(asyncio.CancelledError):
    """Cancellation evidence without advancing an unconfirmed event."""

    code = "replay_pacing_cancelled"

    def __init__(
        self,
        *,
        checkpoint: ReplayDeliveryCheckpoint,
        attempted_count: int,
        delivered_count: int,
        retry_count: int,
        uncertain_sequence_number: int | None,
    ) -> None:
        self.checkpoint = checkpoint
        self.attempted_count = attempted_count
        self.delivered_count = delivered_count
        self.retry_count = retry_count
        self.uncertain_sequence_number = uncertain_sequence_number
        super().__init__("the paced replay run was cancelled")


def _safe_number(value: object, *, code: str) -> float:
    if type(value) not in {int, float}:
        raise ReplayDeliveryError(code)
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        raise ReplayDeliveryError(code) from None
    if not math.isfinite(normalized):
        raise ReplayDeliveryError(code)
    return normalized


class _CheckedClock:
    def __init__(self, value: ReplayPacingClock | None) -> None:
        raw: ReplayPacingClock = _AsyncioReplayClock() if value is None else value
        try:
            monotonic = getattr(raw, "monotonic")
            sleep = getattr(raw, "sleep")
        except Exception:
            raise ReplayDeliveryError("replay_pacing_clock_invalid") from None
        if not callable(monotonic) or not callable(sleep):
            raise ReplayDeliveryError("replay_pacing_clock_invalid")
        self._monotonic: Callable[[], float] = monotonic
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._last: float | None = None

    def now(self) -> float:
        try:
            current = _safe_number(
                self._monotonic(),
                code="replay_pacing_clock_invalid",
            )
        except ReplayDeliveryError:
            raise
        except Exception:
            raise ReplayDeliveryError("replay_pacing_clock_invalid") from None
        if self._last is not None and current + _CLOCK_TOLERANCE_SECONDS < self._last:
            raise ReplayDeliveryError("replay_pacing_clock_invalid")
        self._last = max(current, self._last) if self._last is not None else current
        return self._last

    async def sleep(self, seconds: float, *, real_timeout: float) -> bool:
        before = self.now()
        try:
            await asyncio.wait_for(self._sleep(seconds), timeout=real_timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return False
        except ReplayDeliveryError:
            raise
        except Exception:
            raise ReplayDeliveryError("replay_pacing_clock_invalid") from None
        after = self.now()
        if after + _CLOCK_TOLERANCE_SECONDS < before + seconds:
            raise ReplayDeliveryError("replay_pacing_clock_invalid")
        return True


def _validated_deadline(plan: ReplayPlan, value: float | None) -> float:
    if value is None:
        return float(plan.policy.max_duration_seconds)
    normalized = _safe_number(value, code="replay_pacing_arguments_invalid")
    if not (
        MIN_PACED_REPLAY_DEADLINE_SECONDS
        <= normalized
        <= min(
            float(plan.policy.max_duration_seconds),
            float(MAX_PACED_REPLAY_DEADLINE_SECONDS),
        )
    ):
        raise ReplayDeliveryError("replay_pacing_arguments_invalid")
    return normalized


def _validated_retry_policy(value: object) -> ReplayRetryPolicy:
    if type(value) is not ReplayRetryPolicy:
        raise ReplayDeliveryError("replay_pacing_arguments_invalid")
    return value


def _validated_sink(plan: ReplayPlan, value: object) -> LoopbackHttpReplaySink:
    if type(value) is not LoopbackHttpReplaySink:
        raise ReplayDeliveryError("replay_delivery_plan_invalid")
    try:
        if canonical_json_bytes(value.policy) != canonical_json_bytes(plan.policy):
            raise ReplayDeliveryError("replay_delivery_plan_invalid")
    except ReplayDeliveryError:
        raise
    except Exception:
        raise ReplayDeliveryError("replay_delivery_plan_invalid") from None
    return value


def _canonical_checkpoint(
    plan: ReplayPlan,
    sequence_number: int,
) -> ReplayDeliveryCheckpoint:
    checkpoint = _checkpoint_for_sequence(plan, sequence_number)
    return _validated_checkpoint(plan, checkpoint)


def _elapsed(clock: _CheckedClock, started_at: float) -> float:
    elapsed = clock.now() - started_at
    if not math.isfinite(elapsed) or elapsed < -_CLOCK_TOLERANCE_SECONDS:
        raise ReplayDeliveryError("replay_pacing_clock_invalid")
    return max(0.0, elapsed)


def _result(
    *,
    plan: ReplayPlan,
    clock: _CheckedClock,
    started_at: float,
    checkpoint_sequence: int,
    selected_count: int,
    attempted_count: int,
    delivered_count: int,
    retry_count: int,
    failed_sequence_number: int | None,
    error_code: str | None,
    deadline_exceeded: bool,
    uncertain_sequence_number: int | None,
) -> PacedReplayResult:
    checkpoint = _canonical_checkpoint(plan, checkpoint_sequence)
    return PacedReplayResult(
        checkpoint=checkpoint,
        selected_count=selected_count,
        attempted_count=attempted_count,
        delivered_count=delivered_count,
        retry_count=retry_count,
        failed_sequence_number=failed_sequence_number,
        error_code=error_code,
        deadline_exceeded=deadline_exceeded,
        uncertain_sequence_number=uncertain_sequence_number,
        plan_complete=checkpoint.sequence_number == len(plan.events),
        elapsed_seconds=_elapsed(clock, started_at),
    )


def _cancelled(
    *,
    plan: ReplayPlan,
    checkpoint_sequence: int,
    attempted_count: int,
    delivered_count: int,
    retry_count: int,
    uncertain_sequence_number: int | None,
) -> PacedReplayCancelled:
    return PacedReplayCancelled(
        checkpoint=_canonical_checkpoint(plan, checkpoint_sequence),
        attempted_count=attempted_count,
        delivered_count=delivered_count,
        retry_count=retry_count,
        uncertain_sequence_number=uncertain_sequence_number,
    )


async def run_paced_replay(
    plan: ReplayPlan,
    sink: LoopbackHttpReplaySink,
    *,
    checkpoint: ReplayDeliveryCheckpoint | None = None,
    retry_policy: ReplayRetryPolicy = ReplayRetryPolicy.NONE,
    deadline_seconds: float | None = None,
    clock: ReplayPacingClock | None = None,
) -> PacedReplayResult:
    """Deliver the canonical plan suffix at its bounded wall-clock schedule.

    The default performs no automatic retry.  Frozen transient modes only retry
    network/timeout errors with fixed backoffs.  Cancellation is re-raised as a
    :class:`PacedReplayCancelled` carrying the last confirmed checkpoint.
    """

    normalized = _validated_plan(plan)
    normalized_sink = _validated_sink(normalized, sink)
    normalized_checkpoint = _validated_checkpoint(normalized, checkpoint)
    normalized_retry = _validated_retry_policy(retry_policy)
    deadline = _validated_deadline(normalized, deadline_seconds)
    checked_clock = _CheckedClock(clock)
    started_at = checked_clock.now()
    real_started_at = asyncio.get_running_loop().time()
    deadline_at = started_at + deadline
    real_deadline_at = real_started_at + deadline
    if not math.isfinite(deadline_at) or not math.isfinite(real_deadline_at):
        raise ReplayDeliveryError("replay_pacing_clock_invalid")
    checkpoint_sequence = normalized_checkpoint.sequence_number
    selected = normalized.resume_after(checkpoint_sequence)
    checkpoint_offset = (
        0.0
        if checkpoint_sequence == 0
        else normalized.events[checkpoint_sequence - 1].scheduled_offset_seconds
    )
    schedule_origin = started_at - checkpoint_offset
    backoffs = _RETRY_BACKOFFS[normalized_retry]
    minimum_attempt_interval = 1.0 / normalized.policy.max_rate_per_second
    last_attempt_at: float | None = None
    attempted_count = 0
    delivered_count = 0
    retry_count = 0

    def deadline_result(
        event_sequence: int,
        *,
        uncertain: bool,
    ) -> PacedReplayResult:
        return _result(
            plan=normalized,
            clock=checked_clock,
            started_at=started_at,
            checkpoint_sequence=checkpoint_sequence,
            selected_count=len(selected),
            attempted_count=attempted_count,
            delivered_count=delivered_count,
            retry_count=retry_count,
            failed_sequence_number=event_sequence,
            error_code="replay_pacing_deadline_exceeded",
            deadline_exceeded=True,
            uncertain_sequence_number=event_sequence if uncertain else None,
        )

    for event in selected:
        retry_index = 0
        retry_not_before = -math.inf
        while True:
            plan_due = schedule_origin + event.scheduled_offset_seconds
            rate_due = (
                -math.inf
                if last_attempt_at is None
                else last_attempt_at + minimum_attempt_interval
            )
            due_at = max(plan_due, rate_due, retry_not_before)
            now = checked_clock.now()
            real_now = asyncio.get_running_loop().time()
            logical_remaining = deadline_at - now
            real_remaining = real_deadline_at - real_now
            if (
                logical_remaining <= 0
                or real_remaining <= 0
                or due_at >= deadline_at
                or due_at - now >= real_remaining
            ):
                return deadline_result(event.sequence_number, uncertain=False)
            delay = due_at - now
            if delay > _CLOCK_TOLERANCE_SECONDS:
                try:
                    completed = await checked_clock.sleep(
                        delay,
                        real_timeout=real_remaining,
                    )
                except asyncio.CancelledError:
                    raise _cancelled(
                        plan=normalized,
                        checkpoint_sequence=checkpoint_sequence,
                        attempted_count=attempted_count,
                        delivered_count=delivered_count,
                        retry_count=retry_count,
                        uncertain_sequence_number=None,
                    ) from None
                if not completed:
                    return deadline_result(event.sequence_number, uncertain=False)

            attempt_started_at = checked_clock.now()
            real_remaining = real_deadline_at - asyncio.get_running_loop().time()
            logical_remaining = deadline_at - attempt_started_at
            remaining = min(real_remaining, logical_remaining)
            if remaining <= 0:
                return deadline_result(event.sequence_number, uncertain=False)
            last_attempt_at = attempt_started_at
            attempted_count += 1
            if retry_index > 0:
                retry_count += 1
            try:
                receipt = await asyncio.wait_for(
                    normalized_sink.emit(event),
                    timeout=remaining,
                )
            except asyncio.CancelledError:
                raise _cancelled(
                    plan=normalized,
                    checkpoint_sequence=checkpoint_sequence,
                    attempted_count=attempted_count,
                    delivered_count=delivered_count,
                    retry_count=retry_count,
                    uncertain_sequence_number=event.sequence_number,
                ) from None
            except TimeoutError:
                return deadline_result(event.sequence_number, uncertain=True)
            except ReplayDeliveryError as error:
                if error.code in _TRANSIENT_DELIVERY_CODES and retry_index < len(
                    backoffs
                ):
                    retry_not_before = checked_clock.now() + backoffs[retry_index]
                    if not math.isfinite(retry_not_before):
                        raise ReplayDeliveryError("replay_pacing_clock_invalid")
                    retry_index += 1
                    continue
                return _result(
                    plan=normalized,
                    clock=checked_clock,
                    started_at=started_at,
                    checkpoint_sequence=checkpoint_sequence,
                    selected_count=len(selected),
                    attempted_count=attempted_count,
                    delivered_count=delivered_count,
                    retry_count=retry_count,
                    failed_sequence_number=event.sequence_number,
                    error_code=error.code,
                    deadline_exceeded=False,
                    uncertain_sequence_number=None,
                )

            if (
                type(receipt) is not ReplayDeliveryReceipt
                or receipt.sequence_number != event.sequence_number
                or receipt.source_event_id != event.source_event_id
                or receipt.status_code not in {202, 204}
            ):
                return _result(
                    plan=normalized,
                    clock=checked_clock,
                    started_at=started_at,
                    checkpoint_sequence=checkpoint_sequence,
                    selected_count=len(selected),
                    attempted_count=attempted_count,
                    delivered_count=delivered_count,
                    retry_count=retry_count,
                    failed_sequence_number=event.sequence_number,
                    error_code="replay_delivery_transport_invalid",
                    deadline_exceeded=False,
                    uncertain_sequence_number=None,
                )
            delivered_count += 1
            checkpoint_sequence = event.sequence_number
            _canonical_checkpoint(normalized, checkpoint_sequence)
            break

    return _result(
        plan=normalized,
        clock=checked_clock,
        started_at=started_at,
        checkpoint_sequence=checkpoint_sequence,
        selected_count=len(selected),
        attempted_count=attempted_count,
        delivered_count=delivered_count,
        retry_count=retry_count,
        failed_sequence_number=None,
        error_code=None,
        deadline_exceeded=False,
        uncertain_sequence_number=None,
    )


__all__ = [
    "MAX_PACED_REPLAY_DEADLINE_SECONDS",
    "MAX_PACED_REPLAY_RETRIES",
    "MIN_PACED_REPLAY_DEADLINE_SECONDS",
    "PacedReplayCancelled",
    "PacedReplayResult",
    "ReplayPacingClock",
    "ReplayRetryPolicy",
    "run_paced_replay",
]
