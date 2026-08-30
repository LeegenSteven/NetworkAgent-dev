"""Bounded isolation for Local HTTP business operations.

The Local Profile repository exposes async methods whose DuckDB work is
synchronous.  Running those coroutines on the ASGI event loop would therefore
make an asyncio-only timeout ineffective.  This boundary owns one daemon
worker thread with one persistent event loop, admits exactly one operation,
and never queues another operation behind an outcome that may be unknown.
"""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future as ConcurrentFuture
from typing import Any, TypeVar


LOCAL_BUSINESS_OPERATION_DEADLINE_SECONDS = 5.0
LOCAL_BUSINESS_MAX_CONCURRENCY = 1
LOCAL_BUSINESS_QUEUE_CAPACITY = 0
LOCAL_BUSINESS_WORKER_START_TIMEOUT_SECONDS = 1.0
LOCAL_BUSINESS_WORKER_STOP_TIMEOUT_SECONDS = 1.0
LOCAL_HTTP_REQUEST_BODY_DEADLINE_SECONDS = 2.0
LOCAL_HTTP_REQUEST_MAX_CONCURRENCY = 1
LOCAL_HTTP_REQUEST_QUEUE_CAPACITY = 0

_ResultT = TypeVar("_ResultT")


class LocalBusinessOperationBusy(RuntimeError):
    """The sole isolated worker is still handling an earlier operation."""


class LocalBusinessOperationTimedOut(RuntimeError):
    """The caller deadline elapsed while the isolated operation continues."""


class LocalHttpRequestBodyTimedOut(RuntimeError):
    """The absolute request-body deadline elapsed before validation finished."""


class _LocalHttpRequestLease:
    """Identity-bound, idempotent release handle for one HTTP admission."""

    def __init__(self, admission: "LocalHttpRequestAdmission", token: object) -> None:
        self._admission = admission
        self._token: object | None = token

    def release(self) -> None:
        token = self._token
        if token is None:
            return
        self._token = None
        self._admission._release(token)


class LocalHttpRequestAdmission:
    """Atomically admit one Local HTTP request without an awaitable queue.

    The lease is acquired only after cheap route/header checks but before any
    request-stream receive or JSON parsing.  It remains held through business
    submission.  If an earlier business result is still unknown, the worker
    state also blocks admission so a retry is rejected without reading its
    body.
    """

    def __init__(
        self,
        *,
        body_deadline_seconds: float = LOCAL_HTTP_REQUEST_BODY_DEADLINE_SECONDS,
    ) -> None:
        if (
            isinstance(body_deadline_seconds, bool)
            or not isinstance(body_deadline_seconds, (int, float))
            or not math.isfinite(float(body_deadline_seconds))
            or float(body_deadline_seconds) <= 0
        ):
            raise ValueError("body_deadline_seconds must be a positive finite number")
        self.body_deadline_seconds = float(body_deadline_seconds)
        self.max_concurrency = LOCAL_HTTP_REQUEST_MAX_CONCURRENCY
        self.queue_capacity = LOCAL_HTTP_REQUEST_QUEUE_CAPACITY
        self._state_lock = threading.Lock()
        self._active_token: object | None = None

    @property
    def is_busy(self) -> bool:
        with self._state_lock:
            return self._active_token is not None

    def try_acquire(
        self,
        *,
        operation_boundary: "LocalBusinessOperationBoundary | None" = None,
    ) -> _LocalHttpRequestLease:
        """Acquire immediately or fail; this method never waits or queues."""

        with self._state_lock:
            if self._active_token is not None or (
                operation_boundary is not None
                and not operation_boundary.is_accepting_work
            ):
                raise LocalBusinessOperationBusy(
                    "the Local HTTP request boundary is occupied"
                )
            token = object()
            self._active_token = token
        return _LocalHttpRequestLease(self, token)

    def _release(self, token: object) -> None:
        with self._state_lock:
            if self._active_token is token:
                self._active_token = None

    async def read_body(
        self,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        """Apply one absolute deadline to stream receive and body validation."""

        try:
            async with asyncio.timeout(self.body_deadline_seconds):
                return await operation()
        except TimeoutError:
            raise LocalHttpRequestBodyTimedOut(
                "the Local HTTP request body exceeded its absolute deadline"
            ) from None


class LocalBusinessOperationBoundary:
    """Run one coroutine at a time outside the ASGI event loop.

    A timed-out or caller-cancelled operation is deliberately not cancelled:
    synchronous DuckDB work cannot be interrupted safely and may already have
    committed.  The worker remains occupied until that operation finishes, so
    later requests receive a fixed busy response and can perform an exact
    idempotent retry only after the unknown outcome has settled.
    """

    def __init__(
        self,
        *,
        deadline_seconds: float = LOCAL_BUSINESS_OPERATION_DEADLINE_SECONDS,
    ) -> None:
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(float(deadline_seconds))
            or float(deadline_seconds) <= 0
        ):
            raise ValueError("deadline_seconds must be a positive finite number")
        self.deadline_seconds = float(deadline_seconds)
        self.max_concurrency = LOCAL_BUSINESS_MAX_CONCURRENCY
        self.queue_capacity = LOCAL_BUSINESS_QUEUE_CAPACITY
        self._state_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stopped = threading.Event()
        self._stopped.set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._inflight: ConcurrentFuture[Any] | None = None
        self._closed = False

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._state_lock:
            self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()
            with self._state_lock:
                if self._loop is loop:
                    self._loop = None
                self._ready.clear()
                self._stopped.set()

    def _ensure_worker(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            with self._state_lock:
                if self._closed:
                    raise LocalBusinessOperationBusy(
                        "the isolated business worker is closed"
                    )
                loop = self._loop
                thread = self._thread
            if loop is None or thread is None or not thread.is_alive():
                self._ready.clear()
                self._stopped.clear()
                thread = threading.Thread(
                    target=self._worker_main,
                    name="local-assurance-business-worker",
                    daemon=True,
                )
                with self._state_lock:
                    self._thread = thread
                    self._loop = None
                thread.start()
            if not self._ready.wait(
                timeout=LOCAL_BUSINESS_WORKER_START_TIMEOUT_SECONDS
            ):
                raise LocalBusinessOperationBusy(
                    "the isolated business worker did not start"
                )
            with self._state_lock:
                loop = self._loop
                thread = self._thread
            if loop is None or thread is None or not thread.is_alive():
                raise LocalBusinessOperationBusy(
                    "the isolated business worker is unavailable"
                )
            return loop

    @staticmethod
    async def _invoke(
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        return await operation()

    def _clear_inflight(self, completed: ConcurrentFuture[Any]) -> None:
        stop_loop: asyncio.AbstractEventLoop | None = None
        with self._state_lock:
            if self._inflight is completed:
                self._inflight = None
                self._idle.set()
                if self._closed:
                    stop_loop = self._loop
        if stop_loop is not None:
            stop_loop.call_soon_threadsafe(stop_loop.stop)

    @property
    def is_busy(self) -> bool:
        """Return a lock-protected snapshot of the single-flight state."""

        with self._state_lock:
            return self._inflight is not None and not self._inflight.done()

    @property
    def is_accepting_work(self) -> bool:
        """Whether a request may be parsed for immediate worker submission."""

        with self._state_lock:
            return not self._closed and (
                self._inflight is None or self._inflight.done()
            )

    def wait_until_idle(self, timeout: float) -> bool:
        """Bounded test/lifecycle seam; never wait on the ASGI event loop."""

        return self._idle.wait(timeout=timeout)

    def wait_until_stopped(self, timeout: float) -> bool:
        """Boundedly wait for the daemon worker thread to exit."""

        return self._stopped.wait(timeout=timeout)

    @property
    def worker_is_alive(self) -> bool:
        """Expose bounded lifecycle state for health tests and shutdown QA."""

        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def close(
        self,
        timeout: float = LOCAL_BUSINESS_WORKER_STOP_TIMEOUT_SECONDS,
    ) -> bool:
        """Request shutdown without cancelling a possibly committed operation.

        Returns ``True`` when the worker has stopped within ``timeout``.  A
        stuck operation keeps its daemon thread only until it really finishes;
        its completion callback then stops the loop.  No new work is admitted
        once closing starts.
        """

        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) < 0
        ):
            raise ValueError("timeout must be a non-negative finite number")
        with self._state_lock:
            self._closed = True
            loop = self._loop
            thread = self._thread
            inflight = self._inflight
            can_stop = inflight is None or inflight.done()
        if loop is not None and can_stop:
            loop.call_soon_threadsafe(loop.stop)
        if thread is None or thread is threading.current_thread():
            return thread is None or not thread.is_alive()
        thread.join(timeout=float(timeout))
        return not thread.is_alive()

    async def aclose(self) -> bool:
        """Asynchronously perform the bounded worker shutdown."""

        return await asyncio.to_thread(self.close)

    def _submit(
        self,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> ConcurrentFuture[_ResultT]:
        loop = self._ensure_worker()
        coroutine = self._invoke(operation)
        with self._state_lock:
            if self._closed:
                coroutine.close()
                raise LocalBusinessOperationBusy(
                    "the isolated business worker is closed"
                )
            inflight = self._inflight
            if inflight is not None and not inflight.done():
                coroutine.close()
                raise LocalBusinessOperationBusy(
                    "the isolated business worker is occupied"
                )
            if inflight is not None:
                self._inflight = None
            try:
                self._idle.clear()
                submitted = asyncio.run_coroutine_threadsafe(coroutine, loop)
            except BaseException:
                self._idle.set()
                coroutine.close()
                raise
            self._inflight = submitted
        submitted.add_done_callback(self._clear_inflight)
        return submitted

    @staticmethod
    def _consume_async_result(completed: asyncio.Future[Any]) -> None:
        if completed.cancelled():
            return
        try:
            completed.exception()
        except BaseException:
            # Retrieving the result prevents an unobserved-future warning after
            # an HTTP deadline or client cancellation.  The original waiter,
            # when present, still receives the same exception.
            return

    async def run(
        self,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        submitted = self._submit(operation)
        wrapped = asyncio.wrap_future(submitted)
        wrapped.add_done_callback(self._consume_async_result)
        done, _ = await asyncio.wait(
            (wrapped,),
            timeout=self.deadline_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise LocalBusinessOperationTimedOut(
                "the isolated business operation exceeded its caller deadline"
            )
        return await wrapped


__all__ = [
    "LOCAL_BUSINESS_MAX_CONCURRENCY",
    "LOCAL_BUSINESS_OPERATION_DEADLINE_SECONDS",
    "LOCAL_BUSINESS_QUEUE_CAPACITY",
    "LOCAL_BUSINESS_WORKER_STOP_TIMEOUT_SECONDS",
    "LOCAL_HTTP_REQUEST_BODY_DEADLINE_SECONDS",
    "LOCAL_HTTP_REQUEST_MAX_CONCURRENCY",
    "LOCAL_HTTP_REQUEST_QUEUE_CAPACITY",
    "LocalBusinessOperationBoundary",
    "LocalBusinessOperationBusy",
    "LocalBusinessOperationTimedOut",
    "LocalHttpRequestAdmission",
    "LocalHttpRequestBodyTimedOut",
]
