"""Direct unit tests for the new MCP tool handlers.

These bypass the MCP HTTP transport and call the pure-function handlers
in ``app.mcp.handlers`` directly. The MCP HTTP path is already covered
in ``test_mcp_server.py``; these focus on the v2 tools that wrap the
existing search services.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.candidate_search.schemas import (
    GraphPayload,
    ParsedFilter,
    SearchOutput,
)
from app.mcp import handlers
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _make_user_and_org(db) -> tuple[User, Organization]:
    org = Organization(name="Test Org", slug=f"org-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"u-{id(db)}@example.com",
        hashed_password="x",
        full_name="Test",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.commit()
    return user, org


def _make_app(db, *, org_id, role, candidate_name, email, taali=None, pre_screen=None):
    candidate = Candidate(
        organization_id=org_id, email=email, full_name=candidate_name, position="Engineer"
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=taali,
        pre_screen_score_100=pre_screen,
    )
    db.add(app)
    db.commit()
    return app


# ---------------------------------------------------------------------------
# nl_search_candidates
# ---------------------------------------------------------------------------


def test_nl_search_candidates_passes_through_run_search(db):
    """Handler should call ``run_search`` and hydrate result ids into payloads."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()
    app1 = _make_app(db, org_id=org.id, role=role, candidate_name="Alice",
                     email="alice@x.test", taali=80.0)
    app2 = _make_app(db, org_id=org.id, role=role, candidate_name="Bob",
                     email="bob@x.test", taali=70.0)

    fake_result = SearchOutput(
        application_ids=[app2.id, app1.id],  # rerank changed order
        parsed_filter=ParsedFilter(skills_all=["aws"], free_text="aws engineers"),
        warnings=[],
        rerank_applied=True,
        subgraph=None,
    )

    with patch("app.candidate_search.runner.run_search", return_value=fake_result) as runner:
        out = handlers.nl_search_candidates(
            db, user, query="aws engineers with 5 years", role_id=role.id
        )

    assert runner.called
    kwargs = runner.call_args.kwargs
    assert kwargs["organization_id"] == org.id
    assert kwargs["nl_query"] == "aws engineers with 5 years"
    assert out["total_matched"] == 2
    assert out["rerank_applied"] is True
    # Order from run_search must be preserved.
    assert [a["application_id"] for a in out["applications"]] == [app2.id, app1.id]
    assert out["parsed_filter"]["skills_all"] == ["aws"]


def test_nl_search_candidates_caps_limit(db):
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="X", source="manual")
    db.add(role)
    db.commit()
    apps = [
        _make_app(db, org_id=org.id, role=role, candidate_name=f"C{i}",
                  email=f"c{i}@x.test", taali=float(i))
        for i in range(5)
    ]
    fake = SearchOutput(
        application_ids=[a.id for a in apps],
        parsed_filter=ParsedFilter(),
        warnings=[],
        rerank_applied=False,
    )
    with patch("app.candidate_search.runner.run_search", return_value=fake):
        out = handlers.nl_search_candidates(db, user, query="any", limit=2)
    assert len(out["applications"]) == 2
    assert out["total_matched"] == 5  # raw match count is unaffected


def test_nl_search_candidates_rejects_empty_query(db):
    user, _org = _make_user_and_org(db)
    with pytest.raises(ValueError, match="non-empty"):
        handlers.nl_search_candidates(db, user, query="   ")


# ---------------------------------------------------------------------------
# find_top_candidates — in-the-running pool filter
# ---------------------------------------------------------------------------


def test_find_top_candidates_pool_is_scored_and_not_below_threshold(db):
    """The 'top candidates' pool must be in-the-running candidates only:
    scored AND not the engine's below-threshold/reject verdict. Unscored and
    'Below threshold' applications are dropped before ranking/grounding, so a
    re-score reject can't surface as a 'top' candidate and the deep grounding
    window isn't wasted on un-evaluated rows."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()

    strong = _make_app(db, org_id=org.id, role=role, candidate_name="Strong",
                       email="strong@x.test", taali=80.0)
    review = _make_app(db, org_id=org.id, role=role, candidate_name="Review",
                       email="review@x.test", taali=55.0)
    review.pre_screen_recommendation = "Manual review recommended"
    below = _make_app(db, org_id=org.id, role=role, candidate_name="Below",
                      email="below@x.test", taali=20.0)
    below.pre_screen_recommendation = "Below threshold"
    # Non-canonical label (case/trailing space) must still be excluded — the
    # reject policy stores it lower/trim-normalised elsewhere.
    below_noncanonical = _make_app(db, org_id=org.id, role=role, candidate_name="BelowMessy",
                                   email="belowmessy@x.test", taali=30.0)
    below_noncanonical.pre_screen_recommendation = "below threshold "
    unscored = _make_app(db, org_id=org.id, role=role, candidate_name="Unscored",
                         email="unscored@x.test", taali=None)
    db.commit()

    captured: dict = {}

    def _fake_engine(*, db, organization_id, query, base_query, limit, rank_by):
        captured["ids"] = sorted(a.id for a in base_query.all())
        captured["limit"] = limit
        captured["rank_by"] = rank_by
        return {"candidates": [], "shown": 0}

    with patch(
        "app.candidate_search.top_candidates.find_top_candidates",
        side_effect=_fake_engine,
    ):
        handlers.find_top_candidates(
            db, user, query="top 5 with salary <= 30000 AED", role_id=role.id, limit=5
        )

    assert captured["ids"] == sorted([strong.id, review.id])
    assert below.id not in captured["ids"]              # below-threshold reject excluded
    assert below_noncanonical.id not in captured["ids"] # case/space variant excluded too
    assert unscored.id not in captured["ids"]           # un-evaluated excluded
    assert captured["limit"] == 5
    assert captured["rank_by"] == "taali"


# ---------------------------------------------------------------------------
# screen_pool_against_requirement (rediscovery)
# ---------------------------------------------------------------------------


def test_screen_pool_handler_scopes_scored_nonhired(db):
    """Rediscovery casts over the scored HISTORY: every candidate with a stored
    CV match EXCEPT those already hired — unlike find_top it does NOT restrict
    to the open pipeline (a candidate scored for another role is fair game)."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()

    scored = _make_app(db, org_id=org.id, role=role, candidate_name="Scored",
                       email="s@x.test", taali=80.0)
    scored.cv_match_details = {"requirements_assessment": []}
    unscored = _make_app(db, org_id=org.id, role=role, candidate_name="Unscored",
                         email="u@x.test")  # cv_match_details stays None
    hired = _make_app(db, org_id=org.id, role=role, candidate_name="Hired",
                      email="h@x.test", taali=90.0)
    hired.cv_match_details = {"requirements_assessment": []}
    hired.application_outcome = "hired"
    db.commit()

    captured = {}

    def _fake_engine(*, db, organization_id, requirement, base_query, limit):
        captured["ids"] = {a.id for a in base_query.all()}
        return {"mode": "rediscovery", "candidates": []}

    with patch(
        "app.candidate_search.top_candidates.screen_pool_against_requirement",
        _fake_engine,
    ):
        handlers.screen_pool_against_requirement(db, user, requirement_text="banking")

    assert scored.id in captured["ids"]
    assert unscored.id not in captured["ids"]  # not scored → excluded
    assert hired.id not in captured["ids"]      # already placed → excluded


def test_screen_pool_handler_excludes_candidate_hired_elsewhere(db):
    """A person hired via ONE application must not resurface through a DIFFERENT,
    still-open scored application: rediscovery excludes placed *people*, not just
    the row whose own outcome is 'hired'."""
    user, org = _make_user_and_org(db)
    role_a = Role(organization_id=org.id, name="Backend", source="manual")
    role_b = Role(organization_id=org.id, name="Data", source="manual")
    db.add_all([role_a, role_b])
    db.commit()

    # ONE candidate, TWO applications: hired on role_a, scored + still-open on role_b.
    cand = Candidate(organization_id=org.id, email="dup@x.test", full_name="Dup",
                     position="Engineer")
    db.add(cand)
    db.flush()
    hired_app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role_a.id,
        status="hired", pipeline_stage="hired", pipeline_stage_source="recruiter",
        application_outcome="hired", source="manual", taali_score_cache_100=90.0,
    )
    open_app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role_b.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual", taali_score_cache_100=80.0,
    )
    db.add_all([hired_app, open_app])
    db.commit()
    open_app.cv_match_details = {"requirements_assessment": []}  # scored → eligible but for the hire
    db.commit()

    captured = {}

    def _fake_engine(*, db, organization_id, requirement, base_query, limit):
        captured["ids"] = {a.id for a in base_query.all()}
        return {"mode": "rediscovery", "candidates": []}

    with patch(
        "app.candidate_search.top_candidates.screen_pool_against_requirement",
        _fake_engine,
    ):
        handlers.screen_pool_against_requirement(db, user, requirement_text="banking")

    assert open_app.id not in captured["ids"]   # candidate already placed elsewhere
    assert hired_app.id not in captured["ids"]


def test_screen_pool_handler_rejects_empty_requirement(db):
    user, _org = _make_user_and_org(db)
    with pytest.raises(ValueError, match="non-empty"):
        handlers.screen_pool_against_requirement(db, user, requirement_text="  ")


# ---------------------------------------------------------------------------
# _graph_topology — referential-integrity guard
# ---------------------------------------------------------------------------


def _node(node_id: str, *, label: str = "Person", name: str | None = None) -> dict:
    return {
        "id": node_id,
        "label": label,
        "name": name or node_id,
        "extra": {},
    }


def _edge(source: str, target: str, *, label: str = "WORKED_AT", fact: str = "") -> dict:
    return {
        "source": source,
        "target": target,
        "label": label,
        "extra": {"fact": fact} if fact else {},
    }


def test_graph_topology_drops_edges_with_unknown_endpoints():
    # Production crash: when payload had >60 nodes, the previous slicing
    # let through edges referencing dropped nodes — cytoscape throws
    # synchronously on dangling endpoints and the React error boundary
    # caught it as "Something went wrong".
    payload = GraphPayload(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[
            _edge("a", "b"),                 # both endpoints kept → keep
            _edge("a", "ghost"),             # target not in nodes → drop
            _edge("ghost-2", "c"),           # source not in nodes → drop
        ],
    )
    out = handlers._graph_topology(payload)
    edge_pairs = {(e["source"], e["target"]) for e in out["edges"]}
    assert edge_pairs == {("a", "b")}
    # The kept node ids must cover every kept edge endpoint.
    kept_node_ids = {n["id"] for n in out["nodes"]}
    for edge in out["edges"]:
        assert edge["source"] in kept_node_ids
        assert edge["target"] in kept_node_ids


def test_graph_topology_caps_at_60_nodes_but_preserves_edge_endpoints():
    # Build 80 nodes + 100 edges. Edges reference nodes scattered across
    # the full 80, including some past index 60. The kept nodes must
    # cover every kept edge endpoint, AND the cap of 60 nodes /
    # 100 edges must hold.
    nodes = [_node(f"n-{i}") for i in range(80)]
    # Edges 0..49 reference low-index nodes; edges 50..99 reference
    # high-index nodes (which would be dropped by naive slicing).
    edges = (
        [_edge(f"n-{i}", f"n-{(i + 1) % 50}") for i in range(50)]
        + [_edge(f"n-{60 + (i % 20)}", f"n-{60 + ((i + 1) % 20)}") for i in range(50)]
    )
    payload = GraphPayload(nodes=nodes, edges=edges)
    out = handlers._graph_topology(payload)
    assert len(out["nodes"]) <= 60
    assert len(out["edges"]) <= 100
    kept_ids = {n["id"] for n in out["nodes"]}
    for edge in out["edges"]:
        assert edge["source"] in kept_ids and edge["target"] in kept_ids, (
            f"edge {edge} references a node not in the kept set"
        )


# ---------------------------------------------------------------------------
# graph_search_candidates
# ---------------------------------------------------------------------------


def test_graph_search_unconfigured_returns_warning(db):
    user, _org = _make_user_and_org(db)
    with patch("app.candidate_graph.client.is_configured", return_value=False):
        out = handlers.graph_search_candidates(db, user, query="worked at stripe")
    assert out["applications"] == []
    assert out["graph_facts"] == []
    assert out["warnings"][0]["code"] == "neo4j_unavailable"


def test_graph_search_returns_candidates_from_graph(db):
    """When graph nodes carry a ``taali_id``, hydrate to applications."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="X", source="manual")
    db.add(role)
    db.commit()
    target_app = _make_app(
        db, org_id=org.id, role=role, candidate_name="Sam",
        email="sam@x.test", taali=85.0,
    )
    other_org = Organization(name="Other", slug="other")
    db.add(other_org)
    db.commit()

    payload = GraphPayload(
        nodes=[
            {
                "id": "person-1",
                "label": "Person",
                "name": "Sam",
                "extra": {"taali_id": str(target_app.candidate_id)},
            },
            # A leaked Person from another org — guarded against by
            # CandidateApplication.organization_id filter inside the handler.
            {
                "id": "person-2",
                "label": "Person",
                "name": "External",
                "extra": {"taali_id": "999999"},
            },
            # Edge endpoint must exist in the node list — _graph_topology
            # drops dangling edges to keep cytoscape from crashing.
            {
                "id": "company-1",
                "label": "Company",
                "name": "Stripe",
                "extra": {},
            },
        ],
        edges=[
            {
                "source": "person-1",
                "target": "company-1",
                "label": "WORKED_AT",
                "extra": {"fact": "Senior Engineer at Stripe"},
            }
        ],
    )

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.search.subgraph_for_query", return_value=payload
    ):
        out = handlers.graph_search_candidates(db, user, query="stripe")

    ids = {a["application_id"] for a in out["applications"]}
    assert target_app.id in ids
    # The graph topology (nodes + edges) is also surfaced for inline
    # visualisation. Source-of-truth shape so the React side can call
    # cytoscape.layout against it.
    assert "graph" in out
    assert {n["id"] for n in out["graph"]["nodes"]} >= {"person-1", "person-2"}
    assert any(e["source"] == "person-1" and e["target"] == "company-1" for e in out["graph"]["edges"])
    # Cross-org candidate id (999999) must not surface — even via graph hits.
    assert all(a["candidate_id"] == target_app.candidate_id for a in out["applications"])
    assert any("Stripe" in f["fact"] for f in out["graph_facts"])


# ---------------------------------------------------------------------------
# get_candidate_cv
# ---------------------------------------------------------------------------


def test_get_candidate_cv_returns_sections(db):
    user, org = _make_user_and_org(db)
    candidate = Candidate(
        organization_id=org.id,
        email="cara@x.test",
        full_name="Cara",
        position="Eng",
        cv_text="A long CV with many things",
        cv_filename="cara.pdf",
        cv_sections={"summary": "Senior engineer", "skills": ["aws", "python"]},
        skills=["aws", "python"],
    )
    db.add(candidate)
    db.commit()
    out = handlers.get_candidate_cv(db, user, candidate_id=candidate.id)
    assert out["candidate_id"] == candidate.id
    assert out["cv_text"].startswith("A long CV")
    assert out["cv_sections"]["skills"] == ["aws", "python"]
    assert out["cv_filename"] == "cara.pdf"


def test_get_candidate_cv_cross_org_raises(db):
    user, _org = _make_user_and_org(db)
    other_org = Organization(name="Other", slug="other2")
    db.add(other_org)
    db.flush()
    foreign = Candidate(
        organization_id=other_org.id, email="x@y.test", full_name="Hidden", position="X"
    )
    db.add(foreign)
    db.commit()
    with pytest.raises(ValueError, match="not found"):
        handlers.get_candidate_cv(db, user, candidate_id=foreign.id)
