"""Phase 3 — Fitted policy + per-agent exemplar stores.

Covers the pure-Python logistic regression + isotonic calibration +
hierarchical pooling, the exemplar store write/retrieve/evict cycle,
and the integration of the exemplar write into the teach action.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.actions import teach_decision
from app.actions.types import Actor
from app.agent_runtime import exemplar_store
from app.decision_policy.fitted_policy import (
    FittedModel,
    TrainingExample,
    apply_calibration,
    fit_model,
    fit_pooled,
    isotonic_calibration,
    predict_proba_with_model,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_exemplar import AgentExemplar
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


# ---------------------------------------------------------------------------
# Pure-Python ML primitives
# ---------------------------------------------------------------------------


def test_logistic_fits_separable_data():
    # Two clusters cleanly separated on x; logistic must produce a
    # large positive coef.
    examples = [
        TrainingExample(features={"x": 0.0}, label=0.0) for _ in range(20)
    ] + [
        TrainingExample(features={"x": 1.0}, label=1.0) for _ in range(20)
    ]
    model = fit_pooled(examples, role_id=None)
    # Coef should be substantially positive.
    assert model.coefs["x"] > 1.0
    # And predictions reflect the separation.
    p0 = predict_proba_with_model(model, {"x": 0.0}, calibrated=False)
    p1 = predict_proba_with_model(model, {"x": 1.0}, calibrated=False)
    assert p0 < 0.4
    assert p1 > 0.6


def test_isotonic_calibration_is_monotone():
    raw = [0.1, 0.4, 0.2, 0.6, 0.5, 0.9, 0.3, 0.8]
    labels = [0, 0, 1, 1, 0, 1, 0, 1]
    bps = isotonic_calibration(raw, labels)
    # Monotone non-decreasing in y.
    ys = [y for _, y in bps]
    assert all(b >= a for a, b in zip(ys, ys[1:]))


def test_apply_calibration_passthrough_when_empty():
    assert apply_calibration(0.42, []) == 0.42


def test_fit_model_returns_metrics_when_gold_provided():
    rng = random.Random(7)
    examples = []
    for _ in range(80):
        x = rng.random()
        label = 1.0 if rng.random() < x else 0.0
        examples.append(TrainingExample(features={"x": x}, label=label))
    gold = examples[-16:]
    train = examples[:-16]
    model, metrics = fit_model(train, role_id=None, gold_set=gold)
    assert "holdout_log_loss" in metrics
    assert "holdout_ece" in metrics
    # ECE for a reasonable fit on 16 examples should at least be bounded.
    assert 0.0 <= metrics["holdout_ece"] < 1.0


def test_hierarchical_pooling_falls_back_to_org_when_role_thin():
    # Org examples say x=1 is overwhelmingly NEGATIVE; the role
    # examples disagree (only 2, label=1). With only 2 role examples
    # (under floor=10), the fitted model should equal the org-level
    # model verbatim — the role evidence is ignored.
    examples = (
        [TrainingExample(features={"x": 1.0}, label=1.0, role_id=1) for _ in range(2)]
        + [TrainingExample(features={"x": 0.0}, label=0.0, role_id=2) for _ in range(40)]
        + [TrainingExample(features={"x": 1.0}, label=0.0, role_id=2) for _ in range(40)]
    )
    model = fit_pooled(examples, role_id=1)
    assert model.role_sample_count == 2
    # The org-level dominates: the two role-1 positive examples should
    # not flip the prediction at x=1.0 toward "advance".
    p = predict_proba_with_model(model, {"x": 1.0}, calibrated=False)
    assert p < 0.5
    # And the model's coefs should match the org coefs exactly when
    # below the pooling floor.
    assert model.coefs == model.org_coefs
    assert model.intercept == model.org_intercept


def test_fitted_model_round_trips_through_dict():
    m = FittedModel(
        coefs={"a": 0.5, "b": -0.3},
        intercept=0.1,
        org_coefs={"a": 0.3},
        org_intercept=0.0,
        role_sample_count=42,
        calibration=[(0.0, 0.05), (1.0, 0.95)],
    )
    m2 = FittedModel.from_dict(m.to_dict())
    assert m2.coefs == m.coefs
    assert m2.intercept == m.intercept
    assert m2.calibration == m.calibration


# ---------------------------------------------------------------------------
# Exemplar store: cosine retrieval + eviction + write
# ---------------------------------------------------------------------------


def _seed(db):
    org = Organization(name="Phase3 Org", slug=f"phase3-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email="p3@x.test", full_name="P3 Tester")
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
        reasoning="phase 3 fixture",
        confidence=0.7,
        evidence={
            "scores": {
                "cv_scoring": {"score": 0.71, "uncertainty": 0.15, "skills_match_pct": 78.0}
            }
        },
        model_version="m",
        prompt_version="p",
        idempotency_key=f"p3:{app.id}:advance",
    )
    db.add(decision)
    db.flush()
    return SimpleNamespace(org=org, role=role, decision=decision)


def _user(db, org, idx=1):
    from app.models.user import User
    u = User(
        email=f"p3-user-{idx}-{id(db)}@x.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="P3 User",
        organization_id=org.id,
    )
    db.add(u)
    db.flush()
    return u


def test_teach_writes_exemplar_when_attributed(db):
    s = _seed(db)
    user = _user(db, s.org)
    feedback, _ = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="missing_signal",
        correction_text="Missed the leadership signal in the CV",
        scope="role",
        attributed_to="cv_scoring",
        direction="under",
    )
    db.commit()
    rows = db.query(AgentExemplar).filter(AgentExemplar.source_feedback_id == feedback.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.agent_name == "cv_scoring"
    assert row.direction == "under"
    # corrected_score should have moved upward (under = score was too low).
    assert row.corrected_score is not None
    assert row.corrected_score > row.agent_score


def test_teach_with_policy_combination_does_not_write_exemplar(db):
    s = _seed(db)
    user = _user(db, s.org)
    feedback, _ = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="rubric_mismatch",
        correction_text="Sub-agents were fine, policy weights need adjusting",
        scope="role",
        attributed_to="policy_combination",
        direction="over",
    )
    db.commit()
    rows = db.query(AgentExemplar).filter(AgentExemplar.source_feedback_id == feedback.id).all()
    assert len(rows) == 0


def test_retrieve_top_k_returns_most_similar(db):
    s = _seed(db)
    # Three synthetic exemplars: two close-ish, one far.
    e1 = AgentExemplar(
        organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
        features_json={"skills_match_pct": 80.0, "agent_cv_scoring": 1.0},
        agent_score=0.8,
    )
    e2 = AgentExemplar(
        organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
        features_json={"skills_match_pct": 75.0, "agent_cv_scoring": 1.0},
        agent_score=0.75,
    )
    e3 = AgentExemplar(
        organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
        features_json={"skills_match_pct": 10.0, "agent_cv_scoring": 1.0},
        agent_score=0.1,
    )
    db.add_all([e1, e2, e3])
    db.flush()

    hits = exemplar_store.retrieve_top_k(
        db,
        agent_name="cv_scoring",
        organization_id=s.org.id,
        role_id=s.role.id,
        query_features={"skills_match_pct": 78.0, "agent_cv_scoring": 1.0},
        k=2,
    )
    assert len(hits) == 2
    returned_ids = {h[0].id for h in hits}
    # e3 is far and should not be in the top-2.
    assert e3.id not in returned_ids
    # Both retrieved exemplars should have non-zero similarity.
    assert all(score > 0 for _, score in hits)
    # use_count should have incremented.
    db.refresh(e1)
    assert e1.use_count == 1


def test_evict_overflow_keeps_high_value_rows(db):
    s = _seed(db)
    # Set the cap low so we can exercise eviction cheaply.
    now = datetime.now(timezone.utc)
    # Old row, low value (no use_count, no big correction).
    old = AgentExemplar(
        organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
        features_json={"x": 1.0}, agent_score=0.5, corrected_score=0.5, use_count=0,
        created_at=now - timedelta(days=200),
    )
    # Recent row with large correction.
    recent_big = AgentExemplar(
        organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
        features_json={"x": 1.0}, agent_score=0.2, corrected_score=0.9, use_count=10,
        created_at=now - timedelta(days=2),
    )
    # Middle-aged, light correction.
    middle = AgentExemplar(
        organization_id=s.org.id, role_id=s.role.id, agent_name="cv_scoring",
        features_json={"x": 1.0}, agent_score=0.5, corrected_score=0.55, use_count=1,
        created_at=now - timedelta(days=30),
    )
    db.add_all([old, recent_big, middle])
    db.flush()
    dropped = exemplar_store.evict_overflow(
        db, agent_name="cv_scoring", organization_id=s.org.id, role_id=s.role.id, cap=2
    )
    assert dropped == 1
    survivors = (
        db.query(AgentExemplar)
        .filter(AgentExemplar.organization_id == s.org.id)
        .all()
    )
    surv_ids = {r.id for r in survivors}
    # The old row should be the one dropped; recent_big definitely survives.
    assert recent_big.id in surv_ids
    assert old.id not in surv_ids


# ---------------------------------------------------------------------------
# Feature projection
# ---------------------------------------------------------------------------


def test_features_from_sub_agent_output_handles_nested():
    out = {
        "score": 0.8,
        "uncertainty": 0.1,
        "structured_evidence": {
            "per_criterion": [{"name": "python"}, {"name": "kubernetes"}],
        },
        "has_python": True,
    }
    feats = exemplar_store.features_from_sub_agent_output(out, agent_name="cv_scoring")
    assert feats["score"] == 0.8
    assert feats["has_python"] == 1.0
    # Nested list summarised by length.
    assert feats["structured_evidence_per_criterion__n"] == 2.0
    assert feats["agent_cv_scoring"] == 1.0
