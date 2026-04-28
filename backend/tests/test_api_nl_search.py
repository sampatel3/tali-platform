"""Endpoint integration tests for ?nl_query= on /applications.

Mocks ``run_search`` so we don't depend on Postgres-only JSONB or a
running Anthropic key. Asserts:
- nl_query routes through the runner with the correct args.
- response shape includes parsed_filter, nl_warnings, nl_rerank_applied.
- view=graph attaches subgraph; view=list omits it.
- legacy ``search`` param is suppressed when nl_query is present.
- per-org rate limit returns 429 after 60 NL queries / minute.
- /healthz/neo4j returns "unconfigured" when NEO4J_URI is empty.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.candidate_search import rate_limit as nl_rate_limit
from app.candidate_search.schemas import (
    GraphPayload,
    GraphNode,
    GraphEdge,
    ParsedFilter,
    SearchOutput,
    SearchWarning,
)
from tests.conftest import auth_headers


def _stub_search_output(*, with_subgraph: bool = False) -> SearchOutput:
    parsed = ParsedFilter(
        skills_all=["AWS Glue"],
        free_text="candidates with AWS Glue experience",
    )
    subgraph = None
    if with_subgraph:
        subgraph = GraphPayload(
            nodes=[
                GraphNode(id="person:1", label="Person", name="Alice"),
                GraphNode(id="company:acme", label="Company", name="Acme"),
            ],
            edges=[GraphEdge(source="person:1", target="company:acme", label="WORKED_AT")],
        )
    return SearchOutput(
        application_ids=[],
        parsed_filter=parsed,
        warnings=[SearchWarning(code="neo4j_unavailable", message="not configured")],
        rerank_applied=False,
        subgraph=subgraph,
    )


@pytest.fixture(autouse=True)
def _reset_nl_rate_limit():
    nl_rate_limit.reset()
    yield
    nl_rate_limit.reset()


def test_nl_query_routes_through_runner_and_echoes_parsed_filter(client):
    headers, _ = auth_headers(client)
    # No applications exist for this user → run_search returns empty ids,
    # the endpoint applies WHERE id IN ([-1]) and SQLite handles it cleanly.
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=candidates with AWS Glue experience",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"] == []
    assert data["parsed_filter"]["skills_all"] == ["AWS Glue"]
    assert data["nl_rerank_applied"] is False
    assert data["nl_warnings"][0]["code"] == "neo4j_unavailable"
    # subgraph absent in list view.
    assert "subgraph" not in data
    # Runner was called with our query and rerank=True default.
    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["nl_query"] == "candidates with AWS Glue experience"
    assert kwargs["rerank_enabled"] is True
    assert kwargs["include_subgraph"] is False


def test_view_graph_attaches_subgraph(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(with_subgraph=True),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=ignored&view=graph",
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "subgraph" in data
    assert data["subgraph"]["nodes"][0]["id"] == "person:1"
    kwargs = mocked.call_args.kwargs
    assert kwargs["include_subgraph"] is True


def test_rerank_false_propagates_to_runner(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=ignored&rerank=false",
            headers=headers,
        )
    assert resp.status_code == 200
    assert mocked.call_args.kwargs["rerank_enabled"] is False


def test_legacy_search_is_ignored_when_nl_query_set(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ):
        # Even with `search=Alice`, the response uses parsed_filter only.
        resp = client.get(
            "/api/v1/applications?nl_query=AWS&search=Alice",
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "parsed_filter" in data
    # No items exist regardless; the lack of a 500 here is the regression
    # we're guarding (legacy search would otherwise apply a Candidate join
    # that conflicts with the nl_query path).


def test_per_org_rate_limit_returns_429(client, monkeypatch):
    headers, _ = auth_headers(client)
    monkeypatch.setattr(nl_rate_limit, "MAX_PER_WINDOW", 2)
    nl_rate_limit.reset()
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ):
        for _ in range(2):
            assert (
                client.get("/api/v1/applications?nl_query=foo", headers=headers).status_code
                == 200
            )
        third = client.get("/api/v1/applications?nl_query=foo", headers=headers)
        assert third.status_code == 429
        assert "natural-language" in third.json()["detail"].lower()


def test_no_nl_query_keeps_legacy_path_unchanged(client):
    headers, _ = auth_headers(client)
    # Without nl_query, parsed_filter should not appear and run_search must
    # NOT be invoked.
    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=AssertionError("run_search must not be called"),
    ):
        resp = client.get("/api/v1/applications", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "parsed_filter" not in data


def test_neo4j_healthcheck_unconfigured(client):
    resp = client.get("/healthz/neo4j")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("unconfigured", "ok", "error")
    # In the test environment NEO4J_URI is unset, so:
    assert body["status"] == "unconfigured"
