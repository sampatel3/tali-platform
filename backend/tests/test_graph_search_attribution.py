"""graph SEARCH query embeds must be attributed to the searcher's org.

The graph_sync (indexing) path sets graph_metering_ctx, but the SEARCH path
(graphiti.search → Voyage query-embed) ran outside any dispatch, so its embed
spend landed in call_log with organization_id=NULL — reconcilable against the
provider but never attributed to an org. ``_attribute_search`` closes that gap.
"""
from __future__ import annotations

from app.candidate_graph.search import _attribute_search
from app.services.metered_async_anthropic_client import graph_metering_ctx


def test_attribute_search_sets_org_then_resets():
    assert graph_metering_ctx.get() is None
    with _attribute_search(42, "predicate"):
        ctx = graph_metering_ctx.get()
        assert ctx is not None
        assert ctx.organization_id == 42
        assert ctx.episode_name == "graph_search:predicate"
        assert ctx.require_hard_admission is True
        assert ctx.require_role_admission is False
        assert ctx.role_id is None  # explicit workspace spend, not invented role spend
    assert graph_metering_ctx.get() is None  # reset on exit


def test_attribute_search_resets_on_exception():
    assert graph_metering_ctx.get() is None
    try:
        with _attribute_search(7, "neighbourhood"):
            assert graph_metering_ctx.get().organization_id == 7
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert graph_metering_ctx.get() is None  # reset even when the body raises


def test_attribute_search_enforces_role_admission_when_role_scoped():
    assert graph_metering_ctx.get() is None
    with _attribute_search(42, "predicate", role_id=9):
        ctx = graph_metering_ctx.get()
        assert ctx is not None
        assert ctx.organization_id == 42
        assert ctx.role_id == 9
        assert ctx.require_hard_admission is True
        assert ctx.require_role_admission is True
        assert ctx.trace_id == "graph-search:42:role:9:predicate"
    assert graph_metering_ctx.get() is None
