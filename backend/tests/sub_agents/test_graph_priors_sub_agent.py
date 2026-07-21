"""Graph-priors sub-agent: cold-start, decay, role-family filter."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.services.metered_async_anthropic_client import GraphProviderAdmissionError
from app.sub_agents.base import SubAgentRequest, SubAgentResult
from app.sub_agents.graph_priors import GRAPH_PRIORS_SUB_AGENT, clear_cycle_cache

from .conftest import make_full_application


def _setup(db, *, ratio_advanced: float = 0.6, neighbours: int = 8):
    """Create the focal application + N neighbour candidates with
    application outcomes. Patches graph search to return them.
    """
    org, role, _candidate, app = make_full_application(db)
    # Build extra candidates + applications in the same role family.
    advanced_count = int(neighbours * ratio_advanced)
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
    ) as neighbourhood, patch(
        "app.sub_agents.graph_priors.graph_search.candidate_ids_matching_all",
        return_value=neighbour_ids,
    ) as matching_all:
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.output["neighbour_count"] == 8
    # 6 advanced of 8 → ~0.75
    assert 0.7 < result.output["p_advance"] < 0.8
    assert result.confidence > 0.0
    assert neighbourhood.call_args.kwargs["role_id"] == role.id
    assert neighbourhood.call_args.kwargs["require_role_authority"] is True
    assert matching_all.call_args.kwargs["role_id"] == role.id
    assert matching_all.call_args.kwargs["require_role_authority"] is True


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


def test_graph_priors_does_not_cache_between_calls(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    first = SubAgentResult(
        sub_agent="graph_priors",
        ok=True,
        output={"p_advance": 0.2},
        confidence=0.2,
    )
    second = SubAgentResult(
        sub_agent="graph_priors",
        ok=True,
        output={"p_advance": 0.8},
        confidence=0.8,
    )

    clear_cycle_cache()
    with patch.object(
        GRAPH_PRIORS_SUB_AGENT,
        "_run",
        side_effect=[first, second],
    ) as run:
        first_result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)
        second_result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)

    assert run.call_count == 2
    assert first_result.output["p_advance"] == 0.2
    assert second_result.output["p_advance"] == 0.8
    assert second_result.cache_hit is False


def test_graphrag_priors_run_inside_role_authority_context(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    captured = {}

    @contextmanager
    def provider_context(
        organization_id,
        label,
        *,
        role_id=None,
        require_role_authority=False,
    ):
        captured.update(
            organization_id=organization_id,
            label=label,
            role_id=role_id,
            require_role_authority=require_role_authority,
        )
        yield

    backend = SimpleNamespace(
        get_priors=lambda **_kwargs: SimpleNamespace(
            examples=[{"summary": "grounded prior"}],
            p_advance=0.7,
            confidence=0.8,
            neighbour_count=4,
        )
    )
    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch(
        "app.sub_agents.graph_priors.graph_search.graph_provider_context",
        provider_context,
    ), patch.object(
        GRAPH_PRIORS_SUB_AGENT,
        "_graph_backend",
        return_value=backend,
    ):
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)

    assert result.ok is True
    assert result.output["source"] == "graphrag"
    assert captured == {
        "organization_id": int(org.id),
        "label": "graph_priors",
        "role_id": int(role.id),
        "require_role_authority": True,
    }


@pytest.mark.parametrize("provider_edge", ("neighbourhood", "intersection"))
def test_graph_priors_reraises_authority_error_without_caching(db, provider_edge):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )

    authority_error = GraphProviderAdmissionError("role agent is paused")
    neighbourhood_result = {
        "companies": [
            {"name": "ACME", "title": "engineer", "colleagues": []}
        ],
        "schools": [],
        "skills": [],
    }
    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch.object(
        GRAPH_PRIORS_SUB_AGENT,
        "_try_graphrag",
        return_value=None,
    ), patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
        side_effect=(authority_error if provider_edge == "neighbourhood" else None),
        return_value=neighbourhood_result,
    ) as neighbourhood, patch(
        "app.sub_agents.graph_priors.graph_search.candidate_ids_matching_all",
        side_effect=(authority_error if provider_edge == "intersection" else None),
        return_value=[],
    ) as matching_all:
        for _ in range(2):
            with pytest.raises(GraphProviderAdmissionError, match="paused"):
                GRAPH_PRIORS_SUB_AGENT.run(req, db=db)

    assert neighbourhood.call_count == 2
    assert all(
        call.kwargs["require_role_authority"] is True
        for call in neighbourhood.call_args_list
    )
    if provider_edge == "intersection":
        assert matching_all.call_count == 2
        assert all(
            call.kwargs["require_role_authority"] is True
            for call in matching_all.call_args_list
        )


def test_graph_priors_rejects_application_owned_by_another_standard_role(db):
    org, owner_role, _, app = make_full_application(db)
    other_role = Role(
        organization_id=int(org.id),
        name="Other role",
        source="manual",
    )
    db.add(other_role)
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(other_role.id),
    )

    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch.object(
        GRAPH_PRIORS_SUB_AGENT,
        "_try_graphrag",
        return_value=None,
    ) as graphrag, patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
    ) as neighbourhood:
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)

    assert result.ok is False
    assert "application" in (result.error or "")
    graphrag.assert_not_called()
    neighbourhood.assert_not_called()


def test_graph_priors_allows_sister_role_to_use_owner_application(db):
    org, owner_role, _, app = make_full_application(db)
    sister_role = Role(
        organization_id=int(org.id),
        name="Related backend role",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner_role.id),
    )
    db.add(sister_role)
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(sister_role.id),
    )

    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch.object(
        GRAPH_PRIORS_SUB_AGENT,
        "_try_graphrag",
        return_value=None,
    ), patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
        return_value={
            "companies": [
                {"name": "ACME", "title": "engineer", "colleagues": []}
            ],
            "schools": [],
            "skills": [],
        },
    ) as neighbourhood, patch(
        "app.sub_agents.graph_priors.graph_search.candidate_ids_matching_all",
        return_value=[],
    ) as matching_all:
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)

    assert result.ok is True
    assert neighbourhood.call_args.kwargs["role_id"] == sister_role.id
    assert neighbourhood.call_args.kwargs["require_role_authority"] is True
    assert matching_all.call_args.kwargs["role_id"] == sister_role.id
    assert matching_all.call_args.kwargs["require_role_authority"] is True


@pytest.mark.parametrize("deleted_entity", ("application", "candidate", "role"))
def test_graph_priors_rejects_soft_deleted_request_scope(db, deleted_entity):
    org, role, candidate, app = make_full_application(db)
    deleted_at = datetime.now(timezone.utc)
    if deleted_entity == "application":
        app.deleted_at = deleted_at
    elif deleted_entity == "candidate":
        candidate.deleted_at = deleted_at
    else:
        role.deleted_at = deleted_at
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )

    with patch(
        "app.sub_agents.graph_priors.graph_client.is_configured",
        return_value=True,
    ), patch.object(
        GRAPH_PRIORS_SUB_AGENT,
        "_try_graphrag",
        return_value=None,
    ) as graphrag, patch(
        "app.sub_agents.graph_priors.graph_search.colleague_neighbourhood",
    ) as neighbourhood:
        result = GRAPH_PRIORS_SUB_AGENT.run(req, db=db)

    assert result.ok is False
    graphrag.assert_not_called()
    neighbourhood.assert_not_called()
