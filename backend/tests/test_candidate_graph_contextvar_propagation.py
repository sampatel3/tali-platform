"""``candidate_graph.client.run_async`` propagates contextvars across
the thread/loop hop.

The Graphiti integration runs ``add_episode`` on a dedicated background
event loop in a separate thread. Python's contextvars are thread-local
plus task-local, so a value set in the caller (e.g.
``graph_metering_ctx.set(...)`` inside ``episodes.dispatch``) is
invisible to code on the target loop unless we explicitly carry it
across.

Pre-fix symptom (2026-05-27 worker logs):
    metered_async_anthropic: graph_metering_ctx unset for model=...

→ claude_call_log rows landed with organization_id=NULL → reconciliation
excluded them (its ``organization_id IN (...)`` filter doesn't match
NULL) → those calls were invisible to drift math.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading

import pytest

from app.candidate_graph import client as graph_client


_test_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "test_var", default="unset"
)


@pytest.fixture(autouse=True)
def _close_graph_loop_after_test():
    graph_client.close()
    yield
    graph_client.close()


def test_close_joins_thread_closes_selector_and_allows_clean_restart():
    first_loop = graph_client._start_background_loop()
    first_thread = graph_client._loop_thread
    assert first_thread is not None

    graph_client.close()

    assert not first_thread.is_alive()
    assert first_loop.is_closed()
    assert graph_client._loop is None
    assert graph_client._loop_thread is None

    second_loop = graph_client._start_background_loop()
    assert second_loop is not first_loop
    graph_client.close()
    assert second_loop.is_closed()


def test_repeated_close_during_slow_executor_shutdown_does_not_restop_loop():
    loop = graph_client._start_background_loop()
    loop_thread = graph_client._loop_thread
    assert loop_thread is not None
    executor_started = threading.Event()
    release_executor = threading.Event()

    async def _start_slow_executor_job() -> None:
        running_loop = asyncio.get_running_loop()

        def _wait() -> None:
            executor_started.set()
            release_executor.wait(timeout=5.0)

        running_loop.run_in_executor(None, _wait)

    graph_client.run_async(_start_slow_executor_job(), timeout=2.0)
    assert executor_started.wait(timeout=2.0)
    try:
        graph_client._stop_background_loop(join_timeout=0.01)
        assert loop_thread.is_alive()
        assert graph_client._loop_stopping is True

        graph_client._stop_background_loop(join_timeout=0.01)
        assert loop_thread.is_alive()
        assert not loop.is_closed()
    finally:
        release_executor.set()
        loop_thread.join(timeout=2.0)

    assert not loop_thread.is_alive()
    assert loop.is_closed()
    assert graph_client._loop is None
    assert graph_client._loop_thread is None
    assert graph_client._loop_stopping is False


def test_thread_start_failure_closes_unpublished_loop(monkeypatch):
    created_loops = []
    real_new_event_loop = asyncio.new_event_loop

    def _new_event_loop():
        loop = real_new_event_loop()
        created_loops.append(loop)
        return loop

    def _fail_start(_thread):
        raise RuntimeError("thread start failed")

    monkeypatch.setattr(asyncio, "new_event_loop", _new_event_loop)
    monkeypatch.setattr(threading.Thread, "start", _fail_start)

    with pytest.raises(RuntimeError, match="thread start failed"):
        graph_client._start_background_loop()

    assert len(created_loops) == 1
    assert created_loops[0].is_closed()
    assert graph_client._loop is None
    assert graph_client._loop_thread is None
    assert graph_client._loop_stopping is False


def test_shutdown_closes_original_awaitable_when_wrapper_never_starts(
    monkeypatch,
):
    loop = graph_client._start_background_loop()
    loop_thread = graph_client._loop_thread
    assert loop_thread is not None
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    submitted = threading.Event()
    owned_closed = threading.Event()
    worker_errors = []

    def _block_loop() -> None:
        blocker_started.set()
        release_blocker.wait(timeout=5.0)

    class _OwnedAwaitable:
        def __await__(self):
            yield
            return None

        def close(self) -> None:
            owned_closed.set()

    real_submit = asyncio.run_coroutine_threadsafe

    def _tracked_submit(coro, target_loop):
        future = real_submit(coro, target_loop)
        submitted.set()
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _tracked_submit)
    loop.call_soon_threadsafe(_block_loop)
    assert blocker_started.wait(timeout=2.0)

    def _submit() -> None:
        try:
            graph_client.run_async(_OwnedAwaitable(), timeout=2.0)
        except BaseException as exc:
            worker_errors.append(exc)

    worker = threading.Thread(target=_submit)
    worker.start()
    assert submitted.wait(timeout=2.0)
    try:
        graph_client._stop_background_loop(join_timeout=0.01)
    finally:
        release_blocker.set()
        worker.join(timeout=2.0)
        loop_thread.join(timeout=2.0)

    assert not worker.is_alive()
    assert not loop_thread.is_alive()
    assert loop.is_closed()
    assert owned_closed.is_set()
    assert len(worker_errors) == 1
    assert isinstance(worker_errors[0], concurrent.futures.CancelledError)


def test_run_async_timeout_cancels_background_work_immediately():
    cancelled = threading.Event()

    async def _slow_work() -> None:
        try:
            await asyncio.sleep(5.0)
        finally:
            cancelled.set()

    with pytest.raises(TimeoutError):
        graph_client.run_async(_slow_work(), timeout=0.01)

    assert cancelled.wait(timeout=2.0)


def test_run_async_propagates_contextvar_to_target_loop():
    """A contextvar set in the calling thread is visible inside the
    coroutine running on the Graphiti background loop."""
    token = _test_var.set("set-by-caller")
    try:
        async def _read():
            return _test_var.get()

        observed = graph_client.run_async(_read(), timeout=5.0)
    finally:
        _test_var.reset(token)

    assert observed == "set-by-caller", (
        f"expected the wrapper to propagate _test_var across the thread "
        f"hop, observed {observed!r} (contextvar isolation broken)"
    )


def test_run_async_default_when_contextvar_unset():
    """When no caller has set the var, the coroutine sees the default."""
    async def _read():
        return _test_var.get()

    observed = graph_client.run_async(_read(), timeout=5.0)
    assert observed == "unset"


def test_run_async_isolates_concurrent_callers():
    """Two callers setting the same contextvar to different values must
    not see each other's value — each task's set/reset is scoped to its
    own context tree."""
    import threading

    results: dict[str, str] = {}

    def _caller(value: str) -> None:
        token = _test_var.set(value)
        try:
            async def _read():
                return _test_var.get()

            results[value] = graph_client.run_async(_read(), timeout=5.0)
        finally:
            _test_var.reset(token)

    t1 = threading.Thread(target=_caller, args=("alpha",))
    t2 = threading.Thread(target=_caller, args=("beta",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert results["alpha"] == "alpha"
    assert results["beta"] == "beta"
