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
