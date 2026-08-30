from __future__ import annotations

import asyncio
import math
import threading

import pytest

from telco_lab.loopback_sink import (
    LoopbackHttpReplaySink,
    LoopbackHttpRequest,
    LoopbackHttpResponse,
    ReplayDeliveryCheckpoint,
    ReplayDeliveryError,
)
from telco_lab.paced_runner import (
    PacedReplayCancelled,
    PacedReplayResult,
    ReplayPacingClock,
    ReplayRetryPolicy,
    run_paced_replay,
)

from test_replay import _plan, _policy, _source


class _FakeTransport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.requests: list[LoopbackHttpRequest] = []

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse:
        self.requests.append(request)
        response = (
            self.responses.pop(0)
            if self.responses
            else LoopbackHttpResponse(status_code=202)
        )
        if isinstance(response, BaseException):
            raise response
        return response  # type: ignore[return-value]


class _FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _sink(plan, transport: object) -> LoopbackHttpReplaySink:  # noqa: ANN001
    return LoopbackHttpReplaySink(plan.policy, transport=transport, environ={})  # type: ignore[arg-type]


def test_paced_runner_follows_plan_schedule_with_default_zero_retry(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace"))
    transport = _FakeTransport()
    clock = _FakeClock()

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            deadline_seconds=2,
            clock=clock,
        )
    )

    assert isinstance(result, PacedReplayResult)
    assert result.plan_complete
    assert result.selected_count == 3
    assert result.attempted_count == 3
    assert result.delivered_count == 3
    assert result.retry_count == 0
    assert result.error_code is None
    assert result.uncertain_sequence_number is None
    assert result.checkpoint.sequence_number == 3
    assert clock.sleeps == pytest.approx([0.5, 0.5])
    assert [
        dict(request.headers)["Idempotency-Key"] for request in transport.requests
    ] == [event.idempotency_key for event in plan.events]


def test_default_retry_policy_stops_after_one_transient_attempt(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport(OSError("must-not-leak"), LoopbackHttpResponse(202))

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            deadline_seconds=2,
            clock=_FakeClock(),
        )
    )

    assert result.error_code == "replay_delivery_network"
    assert result.attempted_count == 1
    assert result.delivered_count == 0
    assert result.retry_count == 0
    assert result.checkpoint.sequence_number == 0
    assert result.uncertain_sequence_number == 1
    assert len(transport.requests) == 1
    assert "must-not-leak" not in str(result)


def test_frozen_transient_retry_is_bounded_with_fixed_backoff(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport(
        OSError("first"),
        TimeoutError("second"),
        LoopbackHttpResponse(202),
    )
    clock = _FakeClock()

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            retry_policy=ReplayRetryPolicy.TRANSIENT_TWICE,
            deadline_seconds=3,
            clock=clock,
        )
    )

    assert result.plan_complete
    assert result.attempted_count == 3
    assert result.delivered_count == 1
    assert result.retry_count == 2
    assert result.uncertain_sequence_number is None
    assert clock.sleeps == pytest.approx([0.25, 1.0])
    assert len(transport.requests) == 3
    assert (
        len(
            {dict(request.headers)["Idempotency-Key"] for request in transport.requests}
        )
        == 1
    )


def test_late_retry_does_not_catch_up_faster_than_plan_rate(tmp_path) -> None:
    policy = _policy(speed=1_000, max_rate_per_second=4)
    plan = _plan(
        _source(tmp_path / "workspace", offsets=(0, 1)),
        policy=policy,
    )
    transport = _FakeTransport(
        OSError("transient"),
        LoopbackHttpResponse(202),
        LoopbackHttpResponse(202),
    )
    clock = _FakeClock()

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            retry_policy=ReplayRetryPolicy.TRANSIENT_ONCE,
            deadline_seconds=2,
            clock=clock,
        )
    )

    assert result.plan_complete
    assert result.attempted_count == 3
    assert result.retry_count == 1
    # The first 250 ms is retry backoff.  Although event 2 is then overdue,
    # the runner waits another full rate interval after the retry attempt.
    assert clock.sleeps == pytest.approx([0.25, 0.25])


def test_retry_policy_never_retries_nontransient_failure(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport(
        LoopbackHttpResponse(status_code=500),
        LoopbackHttpResponse(status_code=202),
    )

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            retry_policy=ReplayRetryPolicy.TRANSIENT_TWICE,
            deadline_seconds=3,
            clock=_FakeClock(),
        )
    )

    assert result.error_code == "replay_delivery_status"
    assert result.attempted_count == 1
    assert result.retry_count == 0
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "failure",
    (OSError("network response lost"), TimeoutError("response timed out")),
)
def test_exhausted_transient_retry_marks_the_current_event_uncertain(
    tmp_path,
    failure,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport(failure, failure)

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            retry_policy=ReplayRetryPolicy.TRANSIENT_ONCE,
            deadline_seconds=3,
            clock=_FakeClock(),
        )
    )

    assert result.error_code in {
        "replay_delivery_network",
        "replay_delivery_timeout",
    }
    assert result.attempted_count == 2
    assert result.retry_count == 1
    assert result.delivered_count == 0
    assert result.checkpoint.sequence_number == 0
    assert result.failed_sequence_number == 1
    assert result.uncertain_sequence_number == 1


def test_transient_response_loss_remains_uncertain_after_a_later_http_nack(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport(
        TimeoutError("response lost"),
        LoopbackHttpResponse(status_code=500),
    )

    result = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, transport),
            retry_policy=ReplayRetryPolicy.TRANSIENT_ONCE,
            deadline_seconds=3,
            clock=_FakeClock(),
        )
    )

    assert result.error_code == "replay_delivery_status"
    assert result.attempted_count == 2
    assert result.retry_count == 1
    assert result.checkpoint.sequence_number == 0
    assert result.failed_sequence_number == 1
    assert result.uncertain_sequence_number == 1


def test_deadline_before_next_schedule_returns_checkpoint_and_can_resume(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace"))
    first_transport = _FakeTransport()
    first = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, first_transport),
            deadline_seconds=0.25,
            clock=_FakeClock(),
        )
    )

    assert first.deadline_exceeded
    assert first.error_code == "replay_pacing_deadline_exceeded"
    assert first.checkpoint.sequence_number == 1
    assert first.attempted_count == 1
    assert first.delivered_count == 1
    assert first.uncertain_sequence_number is None
    assert len(first_transport.requests) == 1

    resumed_transport = _FakeTransport()
    resumed_clock = _FakeClock(200)
    resumed = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, resumed_transport),
            checkpoint=first.checkpoint,
            deadline_seconds=2,
            clock=resumed_clock,
        )
    )
    assert resumed.plan_complete
    assert resumed.checkpoint.sequence_number == 3
    assert resumed.selected_count == 2
    assert resumed_clock.sleeps == pytest.approx([0.5, 0.5])
    assert len(resumed_transport.requests) == 2


class _BlockingClock(_FakeClock):
    def __init__(self) -> None:
        super().__init__()
        self.sleep_started = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.sleep_started.set()
        await asyncio.Future()


def test_cancel_during_pacing_preserves_checkpoint_for_explicit_resume(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace"))
    transport = _FakeTransport()
    clock = _BlockingClock()

    async def cancel_during_sleep() -> PacedReplayCancelled:
        task = asyncio.create_task(
            run_paced_replay(
                plan,
                _sink(plan, transport),
                deadline_seconds=2,
                clock=clock,
            )
        )
        await clock.sleep_started.wait()
        task.cancel()
        with pytest.raises(PacedReplayCancelled) as caught:
            await task
        return caught.value

    cancelled = asyncio.run(cancel_during_sleep())
    assert isinstance(cancelled, asyncio.CancelledError)
    assert cancelled.code == "replay_pacing_cancelled"
    assert cancelled.checkpoint.sequence_number == 1
    assert cancelled.attempted_count == 1
    assert cancelled.delivered_count == 1
    assert cancelled.retry_count == 0
    assert cancelled.uncertain_sequence_number is None
    assert len(transport.requests) == 1

    resume_transport = _FakeTransport()
    resumed = asyncio.run(
        run_paced_replay(
            plan,
            _sink(plan, resume_transport),
            checkpoint=cancelled.checkpoint,
            deadline_seconds=2,
            clock=_FakeClock(),
        )
    )
    assert resumed.plan_complete
    assert len(resume_transport.requests) == 2


class _BlockingTransport:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.requests: list[LoopbackHttpRequest] = []

    def send(self, request: LoopbackHttpRequest) -> LoopbackHttpResponse:
        self.requests.append(request)
        self.started.set()
        self.release.wait(timeout=2)
        self.finished.set()
        return LoopbackHttpResponse(status_code=202)


def test_total_deadline_does_not_advance_inflight_checkpoint(tmp_path) -> None:
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _BlockingTransport()

    async def expire_inflight() -> PacedReplayResult:
        result = await run_paced_replay(
            plan,
            _sink(plan, transport),
            deadline_seconds=0.02,
        )
        transport.release.set()
        assert await asyncio.to_thread(transport.finished.wait, 1)
        return result

    result = asyncio.run(expire_inflight())
    assert result.deadline_exceeded
    assert result.error_code == "replay_pacing_deadline_exceeded"
    assert result.checkpoint == ReplayDeliveryCheckpoint(
        plan_id=plan.plan_id,
        sequence_number=0,
        source_event_id=None,
        payload_sha256=None,
    )
    assert result.attempted_count == 1
    assert result.delivered_count == 0
    assert result.uncertain_sequence_number == 1
    assert len(transport.requests) == 1


def test_cancel_during_emit_marks_event_uncertain_without_advancing_checkpoint(
    tmp_path,
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _BlockingTransport()

    async def cancel_inflight() -> PacedReplayCancelled:
        task = asyncio.create_task(
            run_paced_replay(
                plan,
                _sink(plan, transport),
                deadline_seconds=2,
            )
        )
        assert await asyncio.to_thread(transport.started.wait, 1)
        task.cancel()
        with pytest.raises(PacedReplayCancelled) as caught:
            await task
        transport.release.set()
        assert await asyncio.to_thread(transport.finished.wait, 1)
        return caught.value

    cancelled = asyncio.run(cancel_inflight())
    assert cancelled.checkpoint.sequence_number == 0
    assert cancelled.attempted_count == 1
    assert cancelled.delivered_count == 0
    assert cancelled.uncertain_sequence_number == 1


@pytest.mark.parametrize(
    "retry_policy",
    ("none", 0, True, object()),
)
def test_invalid_retry_policy_fails_before_transport(
    tmp_path, retry_policy
) -> None:  # noqa: ANN001
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport()

    with pytest.raises(ReplayDeliveryError) as caught:
        asyncio.run(
            run_paced_replay(
                plan,
                _sink(plan, transport),
                retry_policy=retry_policy,
                deadline_seconds=1,
                clock=_FakeClock(),
            )
        )
    assert caught.value.code == "replay_pacing_arguments_invalid"
    assert transport.requests == []


@pytest.mark.parametrize(
    "deadline",
    (0, -1, True, math.inf, math.nan, 61),
)
def test_invalid_deadline_fails_before_transport(tmp_path, deadline: object) -> None:
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport()

    with pytest.raises(ReplayDeliveryError) as caught:
        asyncio.run(
            run_paced_replay(
                plan,
                _sink(plan, transport),
                deadline_seconds=deadline,  # type: ignore[arg-type]
                clock=_FakeClock(),
            )
        )
    assert caught.value.code == "replay_pacing_arguments_invalid"
    assert transport.requests == []


def test_overflowing_integer_deadline_has_fixed_error_before_transport(
    tmp_path,
) -> None:
    plan = _plan(_source(tmp_path / "workspace", offsets=(0,)))
    transport = _FakeTransport()

    with pytest.raises(ReplayDeliveryError) as caught:
        asyncio.run(
            run_paced_replay(
                plan,
                _sink(plan, transport),
                deadline_seconds=10**10_000,
                clock=_FakeClock(),
            )
        )
    assert caught.value.code == "replay_pacing_arguments_invalid"
    assert transport.requests == []


class _BrokenClock:
    def monotonic(self) -> float:
        return math.nan

    async def sleep(self, _seconds: float) -> None:
        return


def test_invalid_clock_and_cross_plan_checkpoint_fail_before_transport(
    tmp_path,
) -> None:
    source = _source(tmp_path / "workspace", offsets=(0,))
    plan = _plan(source)
    transport = _FakeTransport()
    with pytest.raises(ReplayDeliveryError) as broken_clock:
        asyncio.run(
            run_paced_replay(
                plan,
                _sink(plan, transport),
                deadline_seconds=1,
                clock=_BrokenClock(),
            )
        )
    assert broken_clock.value.code == "replay_pacing_clock_invalid"
    assert transport.requests == []

    other_policy = _policy(endpoint="http://127.0.0.1:9081/replay")
    other_plan = _plan(source, policy=other_policy)
    checkpoint = ReplayDeliveryCheckpoint(
        plan_id=plan.plan_id,
        sequence_number=1,
        source_event_id=plan.events[0].source_event_id,
        payload_sha256=plan.events[0].payload_sha256,
    )
    with pytest.raises(ReplayDeliveryError) as cross_plan:
        asyncio.run(
            run_paced_replay(
                other_plan,
                _sink(other_plan, transport),
                checkpoint=checkpoint,
                deadline_seconds=1,
                clock=_FakeClock(),
            )
        )
    assert cross_plan.value.code == "replay_delivery_checkpoint_invalid"
    assert transport.requests == []


def test_paced_runner_public_exports() -> None:
    from telco_lab import (
        PacedReplayCancelled as ExportedCancelled,
        PacedReplayResult as ExportedResult,
        ReplayPacingClock as ExportedClock,
        ReplayRetryPolicy as ExportedRetry,
        run_paced_replay as exported_run,
    )

    assert ExportedCancelled is PacedReplayCancelled
    assert ExportedResult is PacedReplayResult
    assert ExportedClock is ReplayPacingClock
    assert ExportedRetry is ReplayRetryPolicy
    assert exported_run is run_paced_replay
