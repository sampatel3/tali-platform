"""Adapter tests for candidate_graph.search.

These do NOT require a running Graphiti / Neo4j. We mock
``graph_client.is_configured`` and ``graph_client.run_async`` and verify:

- candidate_ids_for_predicate routes the right NL query and group_id.
- candidate_ids_matching_all intersects, short-circuits on empty.
- subgraph_for_candidates merges Graphiti-shaped facts into a
  GraphPayload with stable ``person:<taali_id>`` ids.
- colleague_neighbourhood collapses results into the rerank shape.
- _extract_taali_ids tolerates both attribute-shaped and text-shaped
  candidate id markers.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.candidate_graph import search as graph_search
from app.candidate_search.schemas import GraphPredicate


def _fact(
    *,
    source_uuid: str,
    target_uuid: str,
    edge_label: str,
    source_name: str = "?",
    target_name: str = "?",
    source_attrs: dict | None = None,
    target_attrs: dict | None = None,
    fact: str = "",
) -> dict:
    return {
        "uuid": f"edge-{source_uuid}-{target_uuid}",
        "name": edge_label,
        "fact": fact,
        "edge_label": edge_label,
        "valid_at": None,
        "invalid_at": None,
        "source_uuid": source_uuid,
        "source_name": source_name,
        "source_labels": ["Person"] if source_uuid.startswith("p") else ["Entity"],
        "source_attributes": source_attrs or {},
        "target_uuid": target_uuid,
        "target_name": target_name,
        "target_labels": ["Person"] if target_uuid.startswith("p") else ["Entity"],
        "target_attributes": target_attrs or {},
        "attributes": {},
    }


def test_predicate_query_phrasing_for_each_type():
    pred = GraphPredicate(type="worked_at", value="Acme")
    assert "Acme" in graph_search._query_for_predicate(pred)
    assert "worked at" in graph_search._query_for_predicate(pred).lower()

    pred = GraphPredicate(type="studied_at", value="MIT")
    assert "studied at" in graph_search._query_for_predicate(pred).lower()

    pred = GraphPredicate(type="colleague_of", value="42")
    assert "shared" in graph_search._query_for_predicate(pred).lower()

    pred = GraphPredicate(type="n_hop_from", value="42", n_hops=2)
    assert "connected" in graph_search._query_for_predicate(pred).lower()


def test_candidate_ids_matching_all_intersects():
    pred1 = GraphPredicate(type="worked_at", value="Acme")
    pred2 = GraphPredicate(type="worked_at", value="Globex")

    def fake_for_predicate(*, organization_id, predicate):
        if predicate.value == "Acme":
            return {1, 2, 3}
        return {2, 3, 4}

    with patch.object(graph_search, "candidate_ids_for_predicate", side_effect=fake_for_predicate):
        out = graph_search.candidate_ids_matching_all(
            organization_id=1, predicates=[pred1, pred2]
        )
    assert out == [2, 3]


def test_candidate_ids_matching_all_short_circuits_on_empty():
    pred1 = GraphPredicate(type="worked_at", value="A")
    pred2 = GraphPredicate(type="worked_at", value="B")
    calls = []

    def fake(*, organization_id, predicate):
        calls.append(predicate.value)
        return set() if predicate.value == "A" else {1, 2}

    with patch.object(graph_search, "candidate_ids_for_predicate", side_effect=fake):
        out = graph_search.candidate_ids_matching_all(
            organization_id=1, predicates=[pred1, pred2]
        )
    assert out == []
    assert calls == ["A"]


def test_extract_taali_ids_from_attributes_and_text():
    facts = [
        _fact(
            source_uuid="p1",
            target_uuid="company-1",
            edge_label="WORKED_AT",
            source_attrs={"taali_id": 7},
        ),
        _fact(
            source_uuid="p2",
            target_uuid="company-2",
            edge_label="WORKED_AT",
            fact="Subject candidate Bob (taali_id=12) worked at Globex",
        ),
        _fact(
            source_uuid="p3",
            target_uuid="company-3",
            edge_label="WORKED_AT",
        ),
    ]
    ids = graph_search._extract_taali_ids(facts)
    assert ids == {7, 12}


def test_subgraph_assembles_with_person_id_format():
    # subgraph_for_candidates now drops to a direct Cypher query against
    # graphiti.driver, returning a Neo4j EagerResult-shaped object with
    # .records (each record is dict-like). Match that shape so the
    # _merge_neo4j_records helper can build GraphNode/GraphEdge objects.
    record = {
        "s_uuid": "p-uuid-aaa",
        "s_name": "Alice",
        "s_props": {"taali_id": 42, "headline": "Senior Engineer"},
        "t_uuid": "c-uuid-acme",
        "t_name": "Acme Corp",
        "t_props": {"kind": "Company"},
        "e_name": None,
        "e_fact": "Alice worked at Acme Corp",
        "e_valid_at": None,
        "e_invalid_at": None,
    }
    fake_result = SimpleNamespace(records=[record])

    def fake_run_async(coro, **kwargs):
        return fake_result

    fake_graphiti = SimpleNamespace(
        search=lambda **kw: None,
        driver=SimpleNamespace(execute_query=lambda *a, **kw: None),
    )
    with patch.object(graph_search.graph_client, "is_configured", return_value=True), \
         patch.object(graph_search.graph_client, "run_async", side_effect=fake_run_async), \
         patch.object(graph_search.graph_client, "get_graphiti", return_value=fake_graphiti):
        payload = graph_search.subgraph_for_candidates(
            organization_id=1, candidate_ids=[42]
        )

    assert any(n.id == "person:42" for n in payload.nodes)
    company_nodes = [n for n in payload.nodes if n.label == "Company"]
    assert company_nodes and company_nodes[0].name == "Acme Corp"
    assert payload.edges and payload.edges[0].label == "WORKED_AT"
    assert payload.edges[0].source == "person:42"


def test_subgraph_dedupes_edges_seen_via_multiple_episodes():
    # Same (s, t, fact) reachable from a profile episode AND an interview
    # episode would otherwise produce duplicate GraphEdge entries.
    record = {
        "s_uuid": "p-uuid-aaa",
        "s_name": "Alice",
        "s_props": {"taali_id": 42},
        "t_uuid": "c-uuid-acme",
        "t_name": "Acme Corp",
        "t_props": {"kind": "Company"},
        "e_uuid": "edge-uuid-1",
        "e_name": None,
        "e_fact": "Alice worked at Acme Corp",
        "e_valid_at": None,
        "e_invalid_at": None,
    }
    fake_result = SimpleNamespace(records=[record, record, record])
    fake_graphiti = SimpleNamespace(
        search=lambda **kw: None,
        driver=SimpleNamespace(execute_query=lambda *a, **kw: None),
    )
    with patch.object(graph_search.graph_client, "is_configured", return_value=True), \
         patch.object(graph_search.graph_client, "run_async", return_value=fake_result), \
         patch.object(graph_search.graph_client, "get_graphiti", return_value=fake_graphiti):
        payload = graph_search.subgraph_for_candidates(
            organization_id=1, candidate_ids=[42]
        )
    assert len(payload.edges) == 1


def test_episode_prefixes_includes_interview_and_event_when_db_present():
    # When a Session is supplied we expand the prefix list with one entry
    # per interview and per pipeline event for the candidate.
    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows
        def join(self, *a, **kw):
            return self
        def filter(self, *a, **kw):
            return self
        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self):
            self._calls = 0
        def query(self, col):
            self._calls += 1
            # First call: interviews, second: events
            if self._calls == 1:
                return FakeQuery([(101,), (102,)])
            return FakeQuery([(201,), (202,), (203,)])

    prefixes = graph_search._episode_prefixes_for_candidates(
        FakeSession(), [42]
    )
    assert "candidate-42-" in prefixes
    assert "interview-101-" in prefixes
    assert "interview-102-" in prefixes
    assert "event-201" in prefixes
    assert "event-202" in prefixes
    assert "event-203" in prefixes


def test_episode_prefixes_falls_back_to_candidate_only_without_db():
    prefixes = graph_search._episode_prefixes_for_candidates(None, [7, 8])
    assert prefixes == ["candidate-7-", "candidate-8-"]


def test_colleague_neighbourhood_groups_by_company():
    facts = [
        _fact(
            source_uuid="p1",
            target_uuid="c1",
            edge_label="WORKED_AT",
            target_name="Acme",
        ),
        _fact(
            source_uuid="p1",
            target_uuid="c1",
            edge_label="WORKED_AT",
            target_name="Acme",
        ),
        _fact(
            source_uuid="p1",
            target_uuid="s1",
            edge_label="STUDIED_AT",
            target_name="MIT",
        ),
        _fact(
            source_uuid="p1",
            target_uuid="sk1",
            edge_label="HAS_SKILL",
            target_name="Python",
        ),
    ]
    with patch.object(graph_search.graph_client, "is_configured", return_value=True), \
         patch.object(graph_search.graph_client, "run_async", return_value=facts), \
         patch.object(graph_search.graph_client, "get_graphiti", return_value=SimpleNamespace(search=lambda **kw: None)):
        out = graph_search.colleague_neighbourhood(organization_id=1, candidate_id=99)

    assert any(c["name"] == "Acme" for c in out["companies"])
    assert "MIT" in out["schools"]
    assert "Python" in out["skills"]


def test_label_for_classifies_job_titles_as_skill_not_company():
    # The bug from production: "Senior Software Engineer" was rendered
    # as a Company (black) because the heuristic substring-matched
    # " software" against the company-suffix list before checking
    # whether the name looked like a job title.
    cases = [
        ("Senior Software Engineer", "Skill"),
        ("Software Engineer", "Skill"),
        ("Solutions Architect", "Skill"),  # was matching " solutions"
        ("Data Scientist", "Skill"),       # was matching " data"
        ("AI Engineer", "Skill"),          # was matching " ai"
        ("Cloud Architect", "Skill"),      # was matching " cloud"
        ("Lead Software Developer", "Skill"),
        ("Chief Technology Officer", "Skill"),
        ("Product Manager", "Skill"),
        ("Senior Recruiter", "Skill"),
    ]
    for name, expected in cases:
        got = graph_search._label_for({}, [], name, edge_context="HAS_SKILL")
        assert got == expected, f"{name!r}: expected {expected}, got {got}"


def test_label_for_still_recognises_real_companies():
    # The job-title check must not regress real company classifications.
    cases = [
        ("Acme Inc", "Company"),                 # definitive suffix
        ("Acme Holdings", "Company"),            # definitive suffix
        ("Microsoft Software", "Company"),       # soft suffix, no job title
        ("AD Ports Group", "Company"),           # soft " group"
        ("Fusemachines", "Company"),             # falls through to edge_context
        ("AWS Cloud Services", "Company"),       # soft " cloud" / " services"
        ("Stripe", "Company"),                   # falls through to edge_context
    ]
    for name, expected in cases:
        # Pass WORKED_AT context because real company nodes are usually
        # the target of WORKED_AT edges.
        got = graph_search._label_for({}, [], name, edge_context="WORKED_AT")
        assert got == expected, f"{name!r}: expected {expected}, got {got}"


def test_label_for_handles_ambiguous_company_with_job_title_word():
    # If a name has BOTH a definitive company suffix AND a job-title
    # word, the company suffix wins because it's a more reliable
    # signal — "Engineering Solutions Inc" is a company, not a role.
    assert (
        graph_search._label_for({}, [], "Engineering Solutions Inc")
        == "Company"
    )
    # But without the definitive suffix, the job-title word wins:
    assert graph_search._label_for({}, [], "Engineering Manager") == "Skill"


def test_search_unconfigured_returns_empty():
    with patch.object(graph_search.graph_client, "is_configured", return_value=False):
        assert (
            graph_search.candidate_ids_for_predicate(
                organization_id=1,
                predicate=GraphPredicate(type="worked_at", value="X"),
            )
            == set()
        )
        assert graph_search.subgraph_for_candidates(organization_id=1, candidate_ids=[1]).nodes == []
        assert graph_search.colleague_neighbourhood(organization_id=1, candidate_id=1) == {
            "companies": [],
            "schools": [],
            "skills": [],
        }
