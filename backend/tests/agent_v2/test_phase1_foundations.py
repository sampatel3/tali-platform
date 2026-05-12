"""Phase 1 of the multi-agent upgrade — foundations.

Covers:
- The new ``attributed_to`` / ``direction`` / ``graph_write_hints``
  columns round-trip on ``decision_feedback``.
- ``teach_decision.run`` rejects unknown attribution values.
- v2 Pydantic contracts (``SubAgentScore``, ``TeachFeedback``,
  ``GraphWriteHint``) validate.
- Graph vocabulary constants are importable + non-empty (cheap guard
  against typos splitting writers and readers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import event

from app.actions import teach_decision
from app.actions.types import Actor
from app.agent_runtime import contracts as v2
from app.candidate_graph import schema as graph_schema
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import (
    ATTRIBUTED_TO_VALUES,
    FEEDBACK_DIRECTIONS,
    DecisionFeedback,
)
from app.models.organization import Organization
from app.models.role import Role


_BIG_PK_COUNTERS: dict[str, int] = {"agent_decisions": 0, "decision_feedback": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(DecisionFeedback, "before_insert", _assign_big_pk)


def _seed(db):
    org = Organization(name="V2 Org", slug=f"v2-org-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="ML Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email="v2@x.test", full_name="Alex Doe")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="Strong all-rounder.",
        confidence=0.91,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"v2:{app.id}:advance",
    )
    db.add(decision)
    db.flush()
    return SimpleNamespace(org=org, role=role, decision=decision)


def _user(db, org):
    from app.models.user import User
    user = User(
        email=f"v2-user-{id(db)}@x.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="V2 User",
        organization_id=org.id,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Column round-trip
# ---------------------------------------------------------------------------


def test_attributed_fields_persist_on_teach(db):
    s = _seed(db)
    user = _user(db, s.org)
    hints = [
        {
            "action": "assert_edge",
            "from_node_id": "candidate:42",
            "edge_type": "SIMILAR_TO",
            "to_node_id": "candidate:99",
            "confidence": 0.7,
            "rationale": "Same career path",
        }
    ]
    feedback, _decision = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="missing_signal",
        correction_text="cv scoring missed the leadership signal",
        scope="role",
        attributed_to="cv_scoring",
        direction="under",
        graph_write_hints=hints,
    )
    db.commit()
    db.refresh(feedback)
    assert feedback.attributed_to == "cv_scoring"
    assert feedback.direction == "under"
    assert feedback.graph_write_hints == hints


def test_teach_rejects_unknown_attribution(db):
    s = _seed(db)
    user = _user(db, s.org)
    try:
        teach_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=s.org.id,
            decision_id=int(s.decision.id),
            failure_mode="rubric_mismatch",
            correction_text="x",
            scope="role",
            attributed_to="not_a_real_agent",
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        return
    raise AssertionError("expected 422")


def test_teach_rejects_unknown_direction(db):
    s = _seed(db)
    user = _user(db, s.org)
    try:
        teach_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=s.org.id,
            decision_id=int(s.decision.id),
            failure_mode="rubric_mismatch",
            correction_text="x",
            scope="role",
            direction="sideways",
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        return
    raise AssertionError("expected 422")


def test_legacy_teach_without_attribution_still_works(db):
    # back-compat: existing UI flows that don't send the v2 fields
    # must keep working — both columns simply stay NULL.
    s = _seed(db)
    user = _user(db, s.org)
    feedback, _ = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="rubric_mismatch",
        correction_text="legacy-shaped teach",
        scope="role",
    )
    db.commit()
    assert feedback.attributed_to is None
    assert feedback.direction is None
    assert feedback.graph_write_hints is None


# ---------------------------------------------------------------------------
# Enum surface
# ---------------------------------------------------------------------------


def test_attributed_to_values_match_spec():
    assert set(ATTRIBUTED_TO_VALUES) == {
        "pre_screen",
        "cv_scoring",
        "assessment_scoring",
        "graph_priors",
        "policy_combination",
    }


def test_feedback_directions_match_spec():
    assert set(FEEDBACK_DIRECTIONS) == {"over", "under"}


# ---------------------------------------------------------------------------
# v2 contracts
# ---------------------------------------------------------------------------


def test_sub_agent_score_contract_validates():
    score = v2.SubAgentScore(
        agent_name="cv_scoring",
        score=0.72,
        uncertainty=0.18,
        structured_evidence={"per_criterion": []},
        citations=[
            v2.GraphCitation(
                node_ids=["n1", "n2"],
                edge_ids=["e1"],
                summary="Worked at Stripe overlap",
            )
        ],
        exemplars_used=[v2.ExemplarRef(exemplar_id=7, similarity=0.84)],
        model_version="claude-opus-4-7",
        scored_at=datetime.now(timezone.utc),
    )
    assert score.score == 0.72
    assert score.uncertainty == 0.18


def test_teach_feedback_contract_round_trips_hints():
    teach = v2.TeachFeedback(
        decision_id=42,
        failure_mode="missing_signal",
        attributed_to="cv_scoring",
        direction="under",
        scope="this_role",
        free_text_reason="Missed leadership signal",
        graph_write_hints=[
            v2.GraphWriteHint(
                action="assert_edge",
                from_node_id="candidate:abc",
                edge_type="HAS_SKILL",
                to_node_id="skill:python",
                confidence=0.9,
                rationale="confirmed in interview",
            )
        ],
        recruiter_id=1,
        submitted_at=datetime.now(timezone.utc),
    )
    dumped = teach.model_dump()
    assert dumped["graph_write_hints"][0]["edge_type"] == "HAS_SKILL"
    assert v2.scope_to_wire(teach.scope) == "role"


def test_scope_aliases_round_trip():
    for spec_name in ("this_candidate", "this_role", "all_similar"):
        wire = v2.scope_to_wire(spec_name)
        assert v2.wire_to_scope(wire) == spec_name


# ---------------------------------------------------------------------------
# Graph vocabulary
# ---------------------------------------------------------------------------


def test_graph_vocabulary_has_required_labels():
    # Cheap guard: writers and readers (Phase 2+) must use the same strings.
    assert graph_schema.NODE_AGENT_SCORE_EVENT == "AgentScoreEvent"
    assert graph_schema.NODE_HIRING_OUTCOME == "HiringOutcome"
    assert graph_schema.EDGE_SCORED_BY in graph_schema.ALL_EDGE_TYPES
    assert graph_schema.EDGE_FED_INTO in graph_schema.ALL_EDGE_TYPES
    assert graph_schema.EDGE_RESULTED_IN in graph_schema.ALL_EDGE_TYPES


def test_sensitivity_buckets_partition_edge_types():
    low = graph_schema.LOW_RISK_EDGE_TYPES
    medium = graph_schema.MEDIUM_RISK_EDGE_TYPES
    # No overlap.
    assert not (low & medium)
    # All medium edges are reachable through ALL_EDGE_TYPES (no typos).
    assert medium.issubset(set(graph_schema.ALL_EDGE_TYPES))
