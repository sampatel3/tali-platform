"""Phase 2 — Graph-centric sub-agents + GraphRAG synthesis.

These tests cover the pieces of Phase 2 we can exercise without a
running Graphiti / Neo4j: the synthesis function (pure Python), the
SubAgentResult v2 fields (uncertainty + citations + exemplars), the
agent episode builders, and the registration of graph_priors.

Cypher execution against a live graph is covered by the integration
smoke under ``tests/integration_smoke/`` when the env is configured.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.candidate_graph import agent_episodes
from app.candidate_graph import graphrag_queries
from app.candidate_graph import schema as graph_schema
from app.sub_agents import all_sub_agents, get_sub_agent
from app.sub_agents.base import SubAgentResult


# ---------------------------------------------------------------------------
# synthesise_prior — pure Python, no graph required
# ---------------------------------------------------------------------------


def test_cypher_failure_never_logs_query_or_provider_detail(monkeypatch, caplog):
    query_secret = "MATCH (candidate {email: 'private@example.test'}) RETURN candidate"
    provider_secret = "neo4j://user:password@private-host tenant-token"

    class _Driver:
        async def execute_query(self, *_args, **_kwargs):
            raise RuntimeError(provider_secret)

    monkeypatch.setattr(graphrag_queries.graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        graphrag_queries.graph_client,
        "get_graphiti",
        lambda: SimpleNamespace(driver=_Driver()),
    )
    monkeypatch.setattr(
        graphrag_queries.graph_client,
        "run_async",
        lambda awaitable, **_kwargs: asyncio.run(awaitable),
    )

    assert graphrag_queries._execute(query_secret) == []
    assert query_secret not in caplog.text
    assert provider_secret not in caplog.text
    assert "graphrag_cypher:RuntimeError" in caplog.text


def test_synthesise_prior_empty_inputs_returns_none():
    out = graphrag_queries.synthesise_prior(
        referrer=None,
        overlap_rows=[],
        similar_rows=[],
        skill_outcome_rows=[],
    )
    assert out["p_advance"] is None
    assert out["confidence"] == 0.0


def test_synthesise_prior_only_referrer_signal():
    referrer = {
        "total_referrals": 10,
        "hires": 6,
        "avg_quality_signal": 0.72,
        "top_performers": 3,
    }
    out = graphrag_queries.synthesise_prior(
        referrer=referrer,
        overlap_rows=[],
        similar_rows=[],
        skill_outcome_rows=[],
    )
    assert out["p_advance"] is not None
    assert 0.0 < out["p_advance"] <= 1.0
    names = [c["name"] for c in out["components"]]
    assert names == ["referrer"]
    # Single-component confidence is the >=0.05 floor.
    assert out["confidence"] >= 0.05


def test_synthesise_prior_multiple_components_increases_confidence():
    referrer = {"total_referrals": 5, "hires": 3, "top_performers": 2}
    overlap = [
        {"company": "Stripe", "overlap_top_performers": 4, "avg_quality": 0.8},
        {"company": "Square", "overlap_top_performers": 2, "avg_quality": 0.7},
    ]
    similar = [
        {"candidate_id": "1", "outcome": "hired", "quality_signal": 0.8,
         "shared_skills": 4, "shared_companies": 1, "similarity_score": 6.0},
        {"candidate_id": "2", "outcome": "hired", "quality_signal": 0.7,
         "shared_skills": 3, "shared_companies": 0, "similarity_score": 3.0},
    ]
    paths = [
        {"skill": "python", "candidates_with_skill": 50, "hire_rate": 0.6, "avg_quality_signal": 0.7},
        {"skill": "kubernetes", "candidates_with_skill": 30, "hire_rate": 0.4, "avg_quality_signal": 0.6},
    ]
    out = graphrag_queries.synthesise_prior(
        referrer=referrer,
        overlap_rows=overlap,
        similar_rows=similar,
        skill_outcome_rows=paths,
    )
    assert out["p_advance"] is not None
    assert out["confidence"] >= 0.5  # 4-of-4 sources → confidence saturates
    names = [c["name"] for c in out["components"]]
    assert "referrer" in names
    assert "company_overlap" in names
    assert "similar_candidates" in names
    assert "skill_outcome_paths" in names


def test_synthesise_prior_strong_signals_pull_p_advance_up():
    # All four sources screaming "yes".
    out = graphrag_queries.synthesise_prior(
        referrer={"total_referrals": 5, "hires": 5, "top_performers": 5},
        overlap_rows=[{"company": "X", "overlap_top_performers": 10}],
        similar_rows=[{"outcome": "hired"} for _ in range(5)],
        skill_outcome_rows=[
            {"skill": s, "candidates_with_skill": 100, "hire_rate": 0.9}
            for s in ("a", "b", "c")
        ],
    )
    assert out["p_advance"] is not None
    assert out["p_advance"] > 0.7


# ---------------------------------------------------------------------------
# SubAgentResult v2 fields
# ---------------------------------------------------------------------------


def test_sub_agent_result_has_v2_fields_defaults():
    r = SubAgentResult(sub_agent="pre_screen", ok=True)
    assert r.uncertainty == 0.0
    assert r.citations == []
    assert r.exemplars_used == []


def test_sub_agent_result_carries_uncertainty_and_citations():
    r = SubAgentResult(
        sub_agent="cv_scoring",
        ok=True,
        confidence=0.8,
        uncertainty=0.2,
        citations=[{"node_ids": ["n1"], "edge_ids": [], "summary": "x"}],
        exemplars_used=[{"exemplar_id": 12, "similarity": 0.9}],
    )
    assert r.uncertainty == 0.2
    assert r.citations[0]["summary"] == "x"
    assert r.exemplars_used[0]["exemplar_id"] == 12


# ---------------------------------------------------------------------------
# Agent episode builders
# ---------------------------------------------------------------------------


def test_build_agent_score_episode_contains_canonical_strings():
    ep = agent_episodes.build_agent_score_episode(
        organization_id=42,
        candidate_full_name="Maya Chen",
        candidate_taali_id=7,
        application_id=33,
        role_id=12,
        agent_name="cv_scoring",
        score=0.81,
        uncertainty=0.12,
        structured_evidence_summary="strong on python, weak on react",
        model_version="claude-opus-4-7",
        scored_at=datetime.now(timezone.utc),
    )
    assert ep is not None
    assert ep.source_description == graph_schema.EPISODE_SOURCE_AGENT_SCORE
    assert graph_schema.NODE_AGENT_SCORE_EVENT in ep.body
    assert graph_schema.EDGE_SCORED_BY in ep.body
    assert "Maya Chen" in ep.body


def test_build_decision_episode_links_to_score_events():
    ep = agent_episodes.build_decision_episode(
        organization_id=42,
        candidate_full_name="Maya Chen",
        candidate_taali_id=7,
        application_id=33,
        role_id=12,
        decision_id=99,
        recommended_action="advance_stage",
        confidence=0.88,
        policy_revision_id=17,
        reasoning="all four sub-agents agree above the floor",
        created_at=datetime.now(timezone.utc),
    )
    assert ep is not None
    assert "D-99" in ep.body
    assert graph_schema.EDGE_FED_INTO in ep.body
    assert graph_schema.NODE_DECISION in ep.body


def test_build_recruiter_action_episode():
    ep = agent_episodes.build_recruiter_action_episode(
        organization_id=42,
        decision_id=99,
        recruiter_id=5,
        action="teach",
        reason="cv_scoring under by ~10 — leadership signal missed",
        happened_at=datetime.now(timezone.utc),
    )
    assert ep is not None
    assert graph_schema.EDGE_REVIEWED_BY in ep.body
    assert "teach" in ep.source_description


def test_build_hiring_outcome_episode():
    ep = agent_episodes.build_hiring_outcome_episode(
        organization_id=42,
        candidate_full_name="Maya Chen",
        candidate_taali_id=7,
        decision_id=99,
        outcome_type="hired",
        quality_signal=0.82,
        observed_at=datetime.now(timezone.utc),
    )
    assert ep is not None
    assert graph_schema.EDGE_RESULTED_IN in ep.body
    assert "quality signal 0.82" in ep.body


def test_org_zero_returns_no_episode():
    ep = agent_episodes.build_agent_score_episode(
        organization_id=0,
        candidate_full_name="x",
        candidate_taali_id=1,
        application_id=1,
        role_id=1,
        agent_name="cv_scoring",
        score=0.5,
        uncertainty=0.5,
        structured_evidence_summary="",
        model_version="m",
        scored_at=datetime.now(timezone.utc),
    )
    assert ep is None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_graph_priors_is_auto_registered():
    names = {a.name for a in all_sub_agents()}
    assert "graph_priors" in names
    assert get_sub_agent("graph_priors") is not None
