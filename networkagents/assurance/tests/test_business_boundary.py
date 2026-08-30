from __future__ import annotations

import asyncio
import threading

import pytest

from telco_assurance_agent.business_boundary import (
    LOCAL_BUSINESS_MAX_CONCURRENCY,
    LOCAL_BUSINESS_QUEUE_CAPACITY,
    LOCAL_HTTP_REQUEST_MAX_CONCURRENCY,
    LOCAL_HTTP_REQUEST_QUEUE_CAPACITY,
    LocalBusinessOperationBoundary,
    LocalBusinessOperationBusy,
    LocalBusinessOperationTimedOut,
    LocalHttpRequestAdmission,
    LocalHttpRequestBodyTimedOut,
)


def test_boundary_is_single_flight_zero_queue_and_reusable_across_event_loops() -> None:
    boundary = LocalBusinessOperationBoundary(deadline_seconds=1.0)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    async def blocked() -> str:
        nonlocal calls
        calls += 1
        started.set()
        if not release.wait(timeout=2.0):
            raise RuntimeError("test worker release timed out")
        return "settled"

    async def first_loop() -> None:
        running = asyncio.create_task(boundary.run(blocked))
        assert await asyncio.to_thread(started.wait, 1.0)

        async def contender() -> str:
            with pytest.raises(LocalBusinessOperationBusy):
                await boundary.run(blocked)
            return "busy"

        assert await asyncio.gather(*(contender() for _ in range(10))) == ["busy"] * 10
        assert calls == 1
        assert boundary.max_concurrency == LOCAL_BUSINESS_MAX_CONCURRENCY == 1
        assert boundary.queue_capacity == LOCAL_BUSINESS_QUEUE_CAPACITY == 0
        release.set()
        assert await running == "settled"

    asyncio.run(first_loop())

    async def second_loop() -> None:
        async def immediate() -> str:
            return "reused"

        assert await boundary.run(immediate) == "reused"

    asyncio.run(second_loop())
    assert boundary.close()
    assert not boundary.worker_is_alive


def test_timeout_and_caller_cancellation_never_cancel_or_clear_the_worker() -> None:
    for mode in ("timeout", "cancel"):
        boundary = LocalBusinessOperationBoundary(
            deadline_seconds=0.05 if mode == "timeout" else 1.0
        )
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        async def blocked() -> str:
            started.set()
            try:
                if not release.wait(timeout=2.0):
                    raise RuntimeError("test worker release timed out")
                return "committed"
            finally:
                finished.set()

        async def scenario() -> None:
            running = asyncio.create_task(boundary.run(blocked))
            assert await asyncio.to_thread(started.wait, 1.0)
            if mode == "timeout":
                with pytest.raises(LocalBusinessOperationTimedOut):
                    await running
            else:
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running

            assert boundary.is_busy
            with pytest.raises(LocalBusinessOperationBusy):
                await boundary.run(blocked)
            assert not finished.is_set()

            assert not await asyncio.to_thread(boundary.close, 0.01)
            assert boundary.worker_is_alive
            release.set()
            assert await asyncio.to_thread(boundary.wait_until_idle, 1.0)
            assert finished.is_set()
            assert await asyncio.to_thread(boundary.wait_until_stopped, 1.0)
            assert not boundary.worker_is_alive

        asyncio.run(scenario())


def test_http_admission_is_atomic_zero_queue_and_body_deadline_is_absolute() -> None:
    admission = LocalHttpRequestAdmission(body_deadline_seconds=0.05)
    lease = admission.try_acquire()
    assert admission.is_busy
    assert admission.max_concurrency == LOCAL_HTTP_REQUEST_MAX_CONCURRENCY == 1
    assert admission.queue_capacity == LOCAL_HTTP_REQUEST_QUEUE_CAPACITY == 0
    with pytest.raises(LocalBusinessOperationBusy):
        admission.try_acquire()

    lease.release()
    lease.release()
    assert not admission.is_busy
    replacement = admission.try_acquire()
    replacement.release()

    cancelled = asyncio.Event()

    async def slow_whole_body() -> bytes:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return b"unreachable"

    async def scenario() -> None:
        with pytest.raises(LocalHttpRequestBodyTimedOut):
            await admission.read_body(slow_whole_body)
        assert cancelled.is_set()

        progress = []

        async def slow_drip() -> bytes:
            for index in range(10):
                await asyncio.sleep(0.02)
                progress.append(index)
            return b"unreachable"

        with pytest.raises(LocalHttpRequestBodyTimedOut):
            await admission.read_body(slow_drip)
        assert 1 <= len(progress) < 10

    asyncio.run(scenario())
