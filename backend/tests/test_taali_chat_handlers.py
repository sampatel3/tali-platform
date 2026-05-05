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
