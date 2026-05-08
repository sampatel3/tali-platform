"""Graph-priors sub-agent: cold-start, decay, role-family filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.candidate_search.schemas import GraphPredicate
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.sub_agents.base import SubAgentRequest
from app.sub_agents.graph_priors import GRAPH_PRIORS_SUB_AGENT, clear_cycle_cache

from .conftest import make_full_application


def _setup(db, *, ratio_advanced: float = 0.6, neighbours: int = 8):
    """Create the focal application + N neighbour candidates with
    application outcomes. Patches graph search to return them.
    """
    org, role, _candidate, app = make_full_application(db)
    target_role_name = role.name

    # Build extra candidates + applications in the same role family.
    advanced_count = int(neighbours * ratio_advanced)
    rejected_count = neighbours - advanced_count
    neighbour_ids: list[int] = []
    for i in range(neighbours):
        c = Candidate(
            organization_id=org.id, email=f"n{i}@x.test", full_name=f"N{i}"
        )
        db.add(c)
        db.flush()
        outcome = "hired" if i < advanced_count else "rejected"
        a = CandidateApplication(
            organization_id=org.id,
            candidate_id=c.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome=outcome,
        )
        db.add(a)
        db.flush()
        neighbour_ids.append(c.id)

    return org, role, app, neighbour_ids


def test_cold_start_returns_zero_confidence(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    clear_cycle_cache()
    # Fake graph: no neighbours.
    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
        return_value={"companies": [], "schools": [], "skills": []},
    ):
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.confidence == 0.0
    assert result.output["neighbour_count"] == 0


def test_priors_compute_p_advance_from_neighbours(db):
    org, role, app, neighbour_ids = _setup(db, ratio_advanced=0.75, neighbours=8)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    clear_cycle_cache()
    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
        return_value={
            "companies": [{"name": "ACME", "title": "engineer", "colleagues": []}],
            "schools": [],
            "skills": [],
        },
    ), patch(
        "app.sub_agents.graph_priors.graph_search.candidate_ids_matching_all",
        return_value=neighbour_ids,
    ):
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.output["neighbour_count"] == 8
    # 6 advanced of 8 → ~0.75
    assert 0.7 < result.output["p_advance"] < 0.8
    assert result.confidence > 0.0


def test_priors_filter_to_same_role_family(db):
    # Need ≥ min_neighbours_for_prior (default 5) same-family neighbours
    # for the prior to fire — pick 6 same-family + 2 different.
    org, role, app, neighbour_ids = _setup(db, ratio_advanced=0.5, neighbours=6)
    # Add two more candidates in a DIFFERENT role family — should not pollute.
    other_role = Role(
        organization_id=org.id, name="Sales Rep", source="manual"
    )
    db.add(other_role)
    db.flush()
    extra_ids: list[int] = []
    for i in range(2):
        c = Candidate(
            organization_id=org.id,
            email=f"sales{i}@x.test",
            full_name=f"S{i}",
        )
        db.add(c)
        db.flush()
        a = CandidateApplication(
            organization_id=org.id,
            candidate_id=c.id,
            role_id=other_role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="hired",  # would skew priors if not filtered
        )
        db.add(a)
        db.flush()
        extra_ids.append(c.id)

    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    clear_cycle_cache()
    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
        return_value={
            "companies": [{"name": "ACME", "title": "engineer", "colleagues": []}],
            "schools": [],
            "skills": [],
        },
    ), patch(
        "app.sub_agents.graph_priors.graph_search.candidate_ids_matching_all",
        return_value=neighbour_ids + extra_ids,
    ):
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)
    # Should only count 6 same-family neighbours, not 8.
    assert result.ok is True
    assert result.output["neighbour_count"] == 6


def test_priors_use_only_existing_search_apis(db):
    """Sanity: the sub-agent only references the two functions it's
    documented to compose. If a future PR introduces a new query path
    here, this test fails and forces a code review."""
    import inspect

    from app.sub_agents import graph_priors as mod

    source = inspect.getsource(mod)
    # The only graph_search calls should be these two:
    assert "graph_search.colleague_neighbourhood" in source
    assert "graph_search.candidate_ids_matching_all" in source
    # And no direct Cypher / Graphiti driver poking. Strip docstrings
    # before grepping so the algorithm narrative doesn't false-trigger.
    code_only = "\n".join(
        line for line in source.split("\n") if not line.lstrip().startswith("#")
    )
    # Heuristic: skip the leading module docstring entirely.
    if '"""' in code_only:
        first = code_only.find('"""')
        second = code_only.find('"""', first + 3)
        if second != -1:
            code_only = code_only[second + 3 :]
    assert ".driver" not in code_only
    assert "graphiti.search(" not in code_only
