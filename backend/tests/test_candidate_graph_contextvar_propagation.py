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

import contextvars

from app.candidate_graph import client as graph_client


_test_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "test_var", default="unset"
)


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
