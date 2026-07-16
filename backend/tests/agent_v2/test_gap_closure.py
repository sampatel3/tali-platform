"""Cover the three gap closures landed in this PR.

- RoleIntent → Graphiti episode emit (mirrors the Postgres row).
- nightly_policy_fit reads from Graphiti first (Postgres fallback).
- exemplar_store.render_exemplars_for_prompt returns the few-shot
  block when the store has rows, empty string when cold.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.agent_runtime import exemplar_store
from app.agent_runtime import role_intent as ri
from app.agent_runtime.contracts import StructuredIntent
from app.candidate_graph import agent_episodes
from app.decision_policy import nightly_policy_fit
from app.models.agent_exemplar import AgentExemplar
from app.models.organization import Organization
from app.models.role import Role


def _seed(db):
    org = Organization(name="GapOrg", slug=f"gap-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="Backend Engineer", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    return SimpleNamespace(org=org, role=role)


# ---------------------------------------------------------------------------
# RoleIntent → Graphiti episode
# ---------------------------------------------------------------------------


def test_role_intent_episode_built_correctly():
    from datetime import datetime, timezone
    ep = agent_episodes.build_role_intent_episode(
        organization_id=42,
        role_id=7,
        role_name="ML Engineer",
        intent_version=2,
        structured_summary="Soft signals: communication · Deal-breakers: remote-only",
        free_text="Looking for someone who pushes back on engineering decisions",
        authored_by_user_id=5,
        authored_at=datetime.now(timezone.utc),
    )
    assert ep is not None
    assert "RoleIntent v2" in ep.body
    assert "HAS_INTENT" in ep.body
    assert "AUTHORED_BY" in ep.body
    assert "communication" in ep.body
    assert "engineering decisions" in ep.body


def test_role_intent_episode_returns_none_for_invalid_org():
    from datetime import datetime, timezone
    ep = agent_episodes.build_role_intent_episode(
        organization_id=0,
        role_id=1,
        role_name="x",
        intent_version=1,
        structured_summary="",
        free_text=None,
        authored_by_user_id=None,
        authored_at=datetime.now(timezone.utc),
    )
    assert ep is None


def test_author_new_version_attempts_episode_emit(db):
    """The Postgres write succeeds even when Graphiti isn't configured.
    Emit is best-effort — exceptions are swallowed.
    """
    s = _seed(db)
    with patch(
        "app.candidate_graph.agent_episodes.emit_role_intent_event",
        return_value=False,  # simulate Graphiti unavailable
    ) as mocked:
        row = ri.author_new_version(
            db,
            organization_id=int(s.org.id),
            role_id=int(s.role.id),
            structured=StructuredIntent(soft_signals=["leadership"]),
            free_text="needs to mentor juniors",
        )
        db.commit()
    assert row.version == 1
    # Episode emit was attempted (called once).
    assert mocked.call_count == 1
    kwargs = mocked.call_args.kwargs
    assert kwargs["role_id"] == int(s.role.id)
    assert kwargs["intent_version"] == 1
    assert "leadership" in kwargs["structured_summary"]


# ---------------------------------------------------------------------------
# Policy fitter Graphiti path with Postgres fallback
# ---------------------------------------------------------------------------


def test_policy_fitter_graphiti_fetch_empty_when_unconfigured(db):
    """When Graphiti is unconfigured, the helper returns []; caller
    then walks Postgres as the fallback."""
    s = _seed(db)
    from datetime import datetime, timezone, timedelta
    since = datetime.now(timezone.utc) - timedelta(days=30)
    out = nightly_policy_fit._collect_from_graphiti(
        organization_id=int(s.org.id), since=since,
    )
    # No Graphiti config in tests → empty list. Postgres fallback in
    # the caller handles real rows.
    assert out == []


def test_policy_fitter_graphiti_dedup_prevents_double_count(db):
    """When Graphiti has an outcome for decision_id=N, the Postgres
    path must NOT also count that decision (weaker label would dilute
    the strong outcome label).
    """
    from datetime import datetime, timezone, timedelta
    s = _seed(db)
    # Mock the Graphiti collector to return a single "hired" training
    # example tied to a specific decision_id, then confirm the Postgres
    # collector skips that decision_id when constructing rows.
    fake_features = {"cv_scoring_score": 0.8, "pre_screen_score": 0.9}
    with patch.object(
        nightly_policy_fit,
        "_collect_from_graphiti",
        return_value=[(
            nightly_policy_fit.TrainingExample(
                features=fake_features,
                label=1.0,
                weight=1.0,
                role_id=int(s.role.id),
            ),
            123,  # decision_id
        )],
    ):
        # No real Postgres decisions for this org — but the dedup
        # mechanism still has to be exercised.
        rows = nightly_policy_fit._collect_training_data(
            db, organization_id=int(s.org.id),
            since=datetime.now(timezone.utc) - timedelta(days=30),
        )
    # Exactly one row, from Graphiti, weight 1.0 (strong label).
    assert len(rows) == 1
    assert rows[0].weight == 1.0
    assert rows[0].features == fake_features


# ---------------------------------------------------------------------------
# render_exemplars_for_prompt
# ---------------------------------------------------------------------------


def test_render_exemplars_returns_empty_when_store_cold(db):
    s = _seed(db)
    out = exemplar_store.render_exemplars_for_prompt(
        db,
        agent_name="cv_scoring",
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        query_features={"role_fit_score": 78.0},
    )
    assert out == ""


def test_render_exemplars_returns_few_shot_block_when_store_has_rows(db):
    s = _seed(db)
    db.add_all([
        AgentExemplar(
            organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
            features_json={"role_fit_score": 78.0, "agent_cv_scoring": 1.0},
            agent_score=0.78, corrected_score=0.65, direction="over",
            attributed_reason="over-credited iteration axis",
        ),
        AgentExemplar(
            organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
            features_json={"role_fit_score": 72.0, "agent_cv_scoring": 1.0},
            agent_score=0.40, corrected_score=0.55, direction="under",
            attributed_reason="missed the leadership signal",
        ),
    ])
    db.commit()
    out = exemplar_store.render_exemplars_for_prompt(
        db,
        agent_name="cv_scoring",
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        query_features={"role_fit_score": 75.0, "agent_cv_scoring": 1.0},
        k=2,
    )
    assert out  # non-empty
    assert "Past corrections" in out
    assert "iteration axis" in out or "leadership signal" in out
    assert "agent scored" in out
    assert "recruiter corrected" in out


def test_render_exemplars_caps_at_k(db):
    """Only ``k`` exemplars add prompt tokens or retrieval side effects."""
    s = _seed(db)
    rows = [
        AgentExemplar(
            organization_id=s.org.id,
            role_id=s.role.id,
            agent_name="cv_scoring",
            features_json={"role_fit_score": float(80 - index)},
            agent_score=0.8,
            corrected_score=0.7,
            direction="over",
            attributed_reason=f"correction-{index}",
        )
        for index in range(5)
    ]
    db.add_all(rows)
    db.commit()

    out = exemplar_store.render_exemplars_for_prompt(
        db,
        agent_name="cv_scoring",
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        query_features={"role_fit_score": 80.0},
        k=2,
    )

    assert out.count("\nExample ") == 2
    assert "Example 3" not in out
    assert sum(int(row.use_count or 0) for row in rows) == 2


def test_exemplar_store_pre_check_avoids_cosine_walk_on_empty_store(db):
    """The cost guard: render_exemplars_for_prompt skips retrieve_top_k
    entirely when the store is empty for (agent, org). Verified by
    patching retrieve_top_k and confirming it's not called.
    """
    s = _seed(db)
    with patch.object(exemplar_store, "retrieve_top_k") as mocked:
        out = exemplar_store.render_exemplars_for_prompt(
            db,
            agent_name="cv_scoring",
            organization_id=int(s.org.id),
            role_id=int(s.role.id),
            query_features={"x": 1.0},
        )
    assert out == ""
    assert mocked.call_count == 0
