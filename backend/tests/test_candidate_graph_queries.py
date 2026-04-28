"""Static checks for candidate_graph Cypher templates.

These tests do NOT spin up Neo4j (the test env is SQLite-only). They
verify the query templates as text:
- Every Cypher template embeds an ``organization_id`` filter so cross-org
  traversal is structurally impossible.
- Predicate dispatch returns ``set()`` for unknown predicate types.

Live Neo4j integration tests live separately and run against a Railway-
backed test instance — out of scope for the unit suite.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from app.candidate_graph import queries as graph_queries
from app.candidate_graph import sync as graph_sync
from app.candidate_search.schemas import GraphPredicate


# Regex helper: every cypher block in this module has at least one
# ``organization_id`` reference. We flag any template that doesn't.
_CYPHER_TEMPLATE_NAMES = [
    "_UPSERT_PERSON_CYPHER",
    "_REPLACE_RELS_CYPHER",
    "_UPSERT_WORKED_AT_CYPHER",
    "_UPSERT_STUDIED_AT_CYPHER",
    "_UPSERT_HAS_SKILL_CYPHER",
    "_UPSERT_LOCATED_IN_CYPHER",
]


def test_every_sync_cypher_template_filters_organization_id():
    for name in _CYPHER_TEMPLATE_NAMES:
        template = getattr(graph_sync, name)
        assert "organization_id" in template, (
            f"Sync template {name} is missing an organization_id filter — "
            "cross-org leakage risk."
        )


def test_unknown_predicate_returns_empty_set():
    bogus = GraphPredicate.model_construct(type="ESCAPE_HATCH", value="x")
    # Force-disable Neo4j so the function path never opens a session.
    with patch.object(graph_queries.graph_client, "is_configured", return_value=True), \
         patch.object(graph_queries.graph_client, "session") as mock_session:
        mock_session.return_value.__enter__.return_value.run.return_value = []
        result = graph_queries.candidate_ids_for_predicate(
            organization_id=1, predicate=bogus
        )
    assert result == set()


def test_colleague_of_with_non_int_value_returns_empty():
    pred = GraphPredicate(type="colleague_of", value="not-a-number")
    with patch.object(graph_queries.graph_client, "is_configured", return_value=True), \
         patch.object(graph_queries.graph_client, "session") as mock_session:
        mock_session.return_value.__enter__.return_value.run.return_value = []
        result = graph_queries.candidate_ids_for_predicate(
            organization_id=1, predicate=pred
        )
    assert result == set()


def test_n_hop_clamps_hops_to_safe_range():
    """Synthesise the cypher and confirm n_hops ends up in [1,4]."""
    pred = GraphPredicate(type="n_hop_from", value="42", n_hops=4)
    captured = {}

    class _FakeRun:
        def __init__(self, *args, **kwargs):
            captured["cypher"] = args[0] if args else kwargs.get("cypher")
            captured["params"] = kwargs

        def __iter__(self):
            return iter([])

    class _FakeSession:
        def run(self, cypher, **params):
            captured["cypher"] = cypher
            captured["params"] = params
            return iter([])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    with patch.object(graph_queries.graph_client, "is_configured", return_value=True), \
         patch.object(graph_queries.graph_client, "session", return_value=_FakeSession()):
        graph_queries.candidate_ids_for_predicate(organization_id=1, predicate=pred)

    cypher = captured.get("cypher", "")
    # The interpolated *1..N range must be present, with N == 4.
    match = re.search(r"\*1\.\.(\d+)", cypher)
    assert match, "n_hop_from cypher should embed *1..N path range"
    assert int(match.group(1)) == 4
    # And every relationship in the path is constrained to the org.
    assert "ALL(r IN relationships(path) WHERE r.organization_id = $org_id)" in cypher


def test_intersection_short_circuits_on_first_empty():
    """If predicate #1 matches nothing, we don't call predicate #2."""
    p1 = GraphPredicate(type="worked_at", value="Acme")
    p2 = GraphPredicate(type="worked_at", value="Globex")
    calls = []

    def fake_for_predicate(*, organization_id, predicate):
        calls.append(predicate.value)
        if predicate.value == "Acme":
            return set()
        return {1, 2}

    with patch.object(graph_queries, "candidate_ids_for_predicate", side_effect=fake_for_predicate):
        out = graph_queries.candidate_ids_matching_all(
            organization_id=1, predicates=[p1, p2]
        )
    assert out == []
    assert calls == ["Acme"]


def test_intersection_yields_set_intersection():
    p1 = GraphPredicate(type="worked_at", value="A")
    p2 = GraphPredicate(type="worked_at", value="B")

    def fake_for_predicate(*, organization_id, predicate):
        return {1, 2, 3} if predicate.value == "A" else {2, 3, 4}

    with patch.object(graph_queries, "candidate_ids_for_predicate", side_effect=fake_for_predicate):
        out = graph_queries.candidate_ids_matching_all(
            organization_id=1, predicates=[p1, p2]
        )
    assert out == [2, 3]
