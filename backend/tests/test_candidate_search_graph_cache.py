"""Cost and isolation guarantees for production graph retrieval caching.

Every loader in this module is an in-memory fake. Provider credentials are
blanked by ``tests/conftest.py`` before application imports.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.candidate_graph.search import GraphEvidenceSearchResult
from app.candidate_search import hybrid
from app.candidate_search.graph_retrieval_cache import (
    GraphRetrievalCache,
    GraphRetrievalCacheKey,
)
from app.candidate_search.retrieval import BackendStatus


@pytest.fixture(autouse=True)
def _empty_process_cache():
    hybrid.clear_graph_retrieval_cache()
    yield
    hybrid.clear_graph_retrieval_cache()


@pytest.mark.parametrize(
    ("raw", "expected_status", "expected_capped"),
    [
        (
            GraphEvidenceSearchResult(status="unavailable"),
            BackendStatus.UNAVAILABLE,
            False,
        ),
        (
            GraphEvidenceSearchResult(
                status="error", capped=True, errors=("backend timeout",)
            ),
            BackendStatus.ERROR,
            True,
        ),
        (
            GraphEvidenceSearchResult(status="ok", capped=True, exhaustive=False),
            BackendStatus.OK,
            True,
        ),
    ],
)
def test_default_graph_search_caches_raw_typed_failure_state(
    monkeypatch, raw, expected_status, expected_capped
):
    calls: list[dict] = []

    def fake_default(**kwargs):
        calls.append(kwargs)
        return raw

    monkeypatch.setattr(hybrid, "_default_graph_search", fake_default)

    first = hybrid.retrieve_graph_backend(
        query="  payment incident expertise  ",
        organization_id=7,
        role_id=11,
        graph_limit=9,
    )
    second = hybrid.retrieve_graph_backend(
        query="PAYMENT   incident expertise",
        organization_id=7,
        role_id=11,
        graph_limit=9,
    )

    assert len(calls) == 1
    assert calls[0] == {
        "organization_id": 7,
        "role_id": 11,
        "queries": ("payment incident expertise",),
        "limit_per_query": 9,
    }
    assert first.status is second.status is expected_status
    assert first.capped is second.capped is expected_capped
    assert first.exhaustive is second.exhaustive is False


def test_default_cache_key_isolated_by_tenant_role_query_and_limit(monkeypatch):
    calls: list[dict] = []

    def fake_default(**kwargs):
        calls.append(kwargs)
        return GraphEvidenceSearchResult(status="ok", exhaustive=True)

    monkeypatch.setattr(hybrid, "_default_graph_search", fake_default)
    base = {
        "query": "payments",
        "organization_id": 1,
        "role_id": 2,
        "graph_limit": 8,
    }
    variants = (
        base,
        {**base, "organization_id": 3},
        {**base, "role_id": None},
        {**base, "query": "treasury"},
        {**base, "graph_limit": 9},
    )

    for kwargs in variants:
        hybrid.retrieve_graph_backend(**kwargs)
    hybrid.retrieve_graph_backend(**base)

    assert len(calls) == len(variants)


def test_injected_graph_search_is_never_cached():
    calls: list[dict] = []

    def injected(**kwargs):
        calls.append(kwargs)
        return GraphEvidenceSearchResult(status="unavailable")

    for _ in range(2):
        result = hybrid.retrieve_graph_backend(
            query="payments",
            organization_id=1,
            graph_search_fn=injected,
        )
        assert result.status is BackendStatus.UNAVAILABLE

    assert len(calls) == 2


def test_cache_is_lru_bounded_and_entries_expire():
    now = [100.0]
    cache = GraphRetrievalCache(
        max_entries=2,
        ttl_seconds=10.0,
        clock=lambda: now[0],
    )
    loads: list[str] = []

    def load(label: str):
        loads.append(label)
        return GraphEvidenceSearchResult(status="ok", errors=(label,))

    first = GraphRetrievalCacheKey(1, None, "first", 5)
    second = GraphRetrievalCacheKey(1, None, "second", 5)
    third = GraphRetrievalCacheKey(1, None, "third", 5)
    cache.get_or_load(first, lambda: load("first"))
    cache.get_or_load(second, lambda: load("second"))
    cache.get_or_load(first, lambda: load("first-again"))  # first is most recent
    cache.get_or_load(third, lambda: load("third"))

    assert cache.size == 2
    cache.get_or_load(second, lambda: load("second-evicted"))
    assert loads == ["first", "second", "third", "second-evicted"]

    now[0] += 11.0
    cache.get_or_load(second, lambda: load("second-expired"))
    assert loads[-1] == "second-expired"


def test_hybrid_service_rejects_oversized_query_before_backend_work():
    with pytest.raises(ValueError, match="at most 500 characters"):
        hybrid.retrieve_graph_backend(
            query="x" * 501,
            organization_id=1,
        )


def test_mcp_candidate_search_entrypoints_reject_oversized_input_before_work():
    from app.mcp import handlers

    db = MagicMock()
    user = MagicMock(organization_id=1)
    oversized = "x" * 501
    calls = (
        lambda: handlers.nl_search_candidates(db, user, query=oversized),
        lambda: handlers.find_top_candidates(db, user, query=oversized),
        lambda: handlers.screen_pool_against_requirement(
            db, user, requirement_text=oversized
        ),
        lambda: handlers.graph_search_candidates(db, user, query=oversized),
    )

    for call in calls:
        with pytest.raises(ValueError, match="at most 500 characters"):
            call()

    db.query.assert_not_called()


def test_mcp_candidate_search_surfaces_exact_empty_and_separate_match_counts():
    from app.candidate_search.schemas import ParsedFilter, SearchOutput
    from app.mcp import handlers

    result = SearchOutput(
        application_ids=[],
        parsed_filter=ParsedFilter(free_text="no matches"),
        database_matches=0,
        retrieval_matches=0,
        is_exact_empty=True,
    )
    db = MagicMock()
    user = MagicMock(organization_id=1)

    with patch("app.candidate_search.runner.run_search", return_value=result):
        payload = handlers.nl_search_candidates(db, user, query="no matches")

    assert payload["database_matches"] == 0
    assert payload["retrieval_matches"] == 0
    assert payload["total_matched"] == 0
    assert payload["is_exact_empty"] is True
