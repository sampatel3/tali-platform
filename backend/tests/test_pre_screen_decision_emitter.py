"""Pre-screen failures surface as Decision Hub cards instead of being
silently parked. Covers the new system-side emitter + the one-shot
backfill that catches up historical stranded apps.
"""
from __future__ import annotations

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.pre_screen_decision_emitter import (
    backfill_existing_below_threshold,
    queue_pre_screen_reject,
    reconcile_pre_screen_reject_decisions,
)


# SQLite BigInteger PK workaround. ``AgentDecision.id`` is BigInteger, and
# SQLite only auto-increments INTEGER PRIMARY KEY columns (not BIGINT).
# Production uses Postgres where this isn't a problem. Mirrors the same
# fix used in ``test_agent_runtime_orchestrator.py``.
_BIG_PK = {"agent_decisions": 0}

def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]

event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _seed(db, *, score: float | None = 35.0, threshold: float | None = 50.0, outcome: str = "open"):
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True)
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        pre_screen_score_100=score,
    )
    db.add(app); db.flush()
    return org, role, app


def test_queue_pre_screen_reject_creates_pending_decision(db):
    org, role, app = _seed(db, score=35.0, threshold=50.0)
    decision = queue_pre_screen_reject(
        db,
        organization_id=int(org.id),
        role=role,
        application=app,
        pre_screen_score=35.0,
        threshold=50.0,
    )
    assert decision is not None
    assert decision.decision_type == "skip_assessment_reject"
    assert decision.status == "pending"
    assert decision.agent_run_id is None  # system-emitted
    assert decision.application_id == app.id
    assert decision.role_id == role.id
    # Reasoning string includes both numbers so the recruiter can see why.
    assert "35" in (decision.reasoning or "")
    assert "50" in (decision.reasoning or "")


def test_queue_pre_screen_reject_skips_agent_off_roles(db):
    """Agent-off roles aren't under agent management. Emitting a Decision
    Hub card for them would surprise the recruiter — they'd see decisions
    appearing for roles they never enabled the agent on. Return None
    without creating a row.
    """
    org, role, app = _seed(db, score=35.0)
    role.agentic_mode_enabled = False
    db.flush()
    result = queue_pre_screen_reject(
        db,
        organization_id=int(org.id),
        role=role,
        application=app,
        pre_screen_score=35.0,
        threshold=50.0,
    )
    assert result is None
    n = db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count()
    assert n == 0


def test_backfill_only_processes_agent_on_roles(db):
    """The backfill must not surface decisions for agent-off roles."""
    # One agent-on role with a below-threshold app — should get a decision.
    org = Organization(name="Mixed", slug=f"mx-{id(db)}")
    db.add(org); db.flush()
    role_on = Role(organization_id=org.id, name="On", source="manual", auto_reject=False, agentic_mode_enabled=True)
    role_off = Role(organization_id=org.id, name="Off", source="manual", auto_reject=False, agentic_mode_enabled=False)
    db.add_all([role_on, role_off]); db.flush()
    for role in (role_on, role_off):
        cand = Candidate(organization_id=org.id, email=f"c{role.id}@x.test", full_name=f"C{role.id}")
        db.add(cand); db.flush()
        db.add(
            CandidateApplication(
                organization_id=org.id,
                candidate_id=cand.id,
                role_id=role.id,
                status="applied",
                pipeline_stage="review",
                pipeline_stage_source="recruiter",
                application_outcome="open",
                source="manual",
                pre_screen_score_100=25.0,
            )
        )
    db.commit()

    summary = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary["created"] == 1  # only the agent-on role's app
    on_rows = db.query(AgentDecision).filter(AgentDecision.role_id == role_on.id).count()
    off_rows = db.query(AgentDecision).filter(AgentDecision.role_id == role_off.id).count()
    assert on_rows == 1
    assert off_rows == 0


def test_queue_pre_screen_reject_is_idempotent(db):
    org, role, app = _seed(db)
    a = queue_pre_screen_reject(db, organization_id=org.id, role=role, application=app, pre_screen_score=35.0, threshold=50.0)
    b = queue_pre_screen_reject(db, organization_id=org.id, role=role, application=app, pre_screen_score=35.0, threshold=50.0)
    assert a is not None and b is not None
    assert a.id == b.id  # same row returned both times
    n = db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count()
    assert n == 1


def test_backfill_creates_decisions_for_existing_below_threshold(db):
    """Simulates the prod scenario: 3 apps below threshold, all stranded
    (no decision rows yet). Backfill should create one decision per app."""
    org = Organization(name="Backfill Org", slug=f"bf-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True)
    db.add(role); db.flush()
    for i in range(3):
        cand = Candidate(organization_id=org.id, email=f"c{i}@x.test", full_name=f"C{i}")
        db.add(cand); db.flush()
        db.add(
            CandidateApplication(
                organization_id=org.id,
                candidate_id=cand.id,
                role_id=role.id,
                status="applied",
                pipeline_stage="review",
                pipeline_stage_source="recruiter",
                application_outcome="open",
                source="manual",
                pre_screen_score_100=20.0 + i,  # all < 50
            )
        )
    # One control: score above threshold should NOT get a decision.
    cand = Candidate(organization_id=org.id, email="ok@x.test", full_name="OK")
    db.add(cand); db.flush()
    db.add(
        CandidateApplication(
            organization_id=org.id,
            candidate_id=cand.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
            pre_screen_score_100=85.0,
        )
    )
    db.commit()

    summary = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary["created"] == 3
    assert summary["skipped_existing"] == 0
    assert summary["failed"] == 0

    # Re-running is a no-op.
    summary2 = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary2["created"] == 0
    assert summary2["skipped_existing"] == 3


def test_backfill_picks_up_null_score_below_threshold_recommendation(db):
    """Cache invalidation (#209) nulled ``pre_screen_score_100`` for some
    apps but left ``pre_screen_recommendation='Below threshold'`` set.
    The backfill must surface these — that's the bug that left 250
    candidates stuck in the DeepLight AI / role 31 incident.
    """
    org = Organization(name="NullScore Org", slug=f"ns-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True)
    db.add(role); db.flush()
    # Two apps: one with NULL score + "Below threshold" rec (should be
    # caught), one with NULL score + None rec (should be skipped — could
    # be a candidate that hasn't been pre-screened at all yet).
    for idx, (rec, expected_caught) in enumerate(
        [("Below threshold", True), (None, False)]
    ):
        cand = Candidate(organization_id=org.id, email=f"n{idx}@x.test", full_name=f"N{idx}")
        db.add(cand); db.flush()
        db.add(
            CandidateApplication(
                organization_id=org.id,
                candidate_id=cand.id,
                role_id=role.id,
                status="applied",
                pipeline_stage="review",
                pipeline_stage_source="recruiter",
                application_outcome="open",
                source="manual",
                pre_screen_score_100=None,
                pre_screen_recommendation=rec,
            )
        )
    db.commit()

    summary = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary["created"] == 1  # only the "Below threshold" rec row
    assert summary["failed"] == 0


def test_evaluate_auto_reject_triggers_on_agentic_mode_without_org_workable_flag(db):
    """The legacy org-level ``workable_config.auto_reject_enabled`` flag
    used to be the only enabling gate, so orgs that never wired up
    Workable auto-disqualify got *zero* reject decisions even with
    ``role.agentic_mode_enabled=True``. The HITL queue path must work
    independently of the legacy flag.
    """
    from app.decision_policy.auto_reject import evaluate_auto_reject_decision

    org = Organization(name="No Workable", slug=f"nw-{id(db)}")
    org.workable_config = {}  # auto_reject_enabled absent / falsy
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        auto_reject=False,
        agentic_mode_enabled=True,
        score_threshold=50,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="x@x.test", full_name="X", workable_candidate_id="wid-1")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=20.0,
        cv_match_score=20.0,
        pre_screen_recommendation="Below threshold",
    )
    db.add(app); db.commit()

    verdict = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict["should_trigger"] is True, verdict
    assert verdict["state"] == "eligible"


def test_evaluate_auto_reject_triggers_on_null_score_with_below_threshold_rec(db):
    """Cache-invalidated rows (NULL score, but recommendation says 'Below
    threshold') must still trigger so the recruiter gets the card.
    """
    from app.decision_policy.auto_reject import evaluate_auto_reject_decision

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        auto_reject=False,
        agentic_mode_enabled=True,
        score_threshold=50,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="x@x.test", full_name="X", workable_candidate_id="wid-1")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=None,
        pre_screen_recommendation="Below threshold",
    )
    db.add(app); db.commit()

    verdict = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict["should_trigger"] is True, verdict
    assert verdict["state"] == "eligible"


def test_evaluate_auto_reject_does_not_trigger_when_score_above_threshold(db):
    """Sanity: a strong match (score above threshold) must NOT trigger
    even though ``agentic_mode_enabled`` is on.
    """
    from app.decision_policy.auto_reject import evaluate_auto_reject_decision

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        auto_reject=False,
        agentic_mode_enabled=True,
        score_threshold=50,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="x@x.test", full_name="X", workable_candidate_id="wid-1")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=75.0,
        # ``pre_screen_snapshot`` reads ``cv_match_score`` first; mirror
        # the score there so the evaluator sees a numeric value.
        cv_match_score=75.0,
        pre_screen_recommendation="Proceed to screening",
    )
    db.add(app); db.commit()

    verdict = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict["should_trigger"] is False
    assert verdict["state"] == "not_triggered"


# ---------------------------------------------------------------------------
# reconcile_pre_screen_reject_decisions — keep the deterministic reject queue
# in sync with the role's threshold when it changes (no re-scoring).
# ---------------------------------------------------------------------------


def _add_app(db, org, role, *, score, email, rec=None, outcome="open"):
    cand = Candidate(organization_id=org.id, email=email, full_name=email[:1].upper())
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        pre_screen_score_100=score,
        pre_screen_recommendation=rec,
    )
    db.add(app); db.flush()
    return app


def _latest_status(db, app):
    row = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == app.id)
        .order_by(AgentDecision.id.desc())
        .first()
    )
    return row.status if row is not None else None


def test_reconcile_lowered_threshold_is_score_authoritative(db):
    """Lowering the threshold (50 → 30) retires cards for candidates now
    at/above 30 — the numeric score wins, even when the candidate carries a
    'Below threshold' recommendation (that label is a hard-coded ``< 50``,
    not a role-threshold verdict). The recommendation only keeps a card
    alive when there's NO numeric score (a genuine must-have miss).
    """
    org, role, app_above = _seed(db, score=40.0, threshold=50.0)  # 40 >= 30 → discard
    app_below = _add_app(db, org, role, score=20.0, email="b@x.test")  # 20 < 30 → keep
    # Numeric 40 + 'Below threshold' rec → still discarded (score authoritative).
    app_numeric_rec = _add_app(db, org, role, score=40.0, rec="Below threshold", email="c@x.test")
    # NULL score + 'Below threshold' rec → kept (must-have miss).
    app_null_rec = _add_app(db, org, role, score=None, rec="Below threshold", email="d@x.test")
    for app in (app_above, app_below, app_numeric_rec, app_null_rec):
        queue_pre_screen_reject(
            db, organization_id=org.id, role=role, application=app,
            pre_screen_score=app.pre_screen_score_100, threshold=50.0,
        )
    db.commit()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=30.0
    )
    assert summary["discarded"] == 2  # app_above + app_numeric_rec
    assert summary["created"] == 0
    assert _latest_status(db, app_above) == "discarded"
    assert _latest_status(db, app_numeric_rec) == "discarded"
    assert _latest_status(db, app_below) == "pending"
    assert _latest_status(db, app_null_rec) == "pending"


def test_reconcile_raised_threshold_emits_new_cards(db):
    """Raising the threshold (30 → 50) should surface candidates who are
    now below the cutoff but had no card before.
    """
    org, role, app = _seed(db, score=40.0, threshold=30.0)  # 40 was above 30, no card
    db.commit()
    assert _latest_status(db, app) is None

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=50.0
    )
    assert summary["created"] == 1
    assert summary["discarded"] == 0
    card = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == app.id, AgentDecision.status == "pending")
        .one()
    )
    assert card.decision_type == "skip_assessment_reject"


def test_reconcile_no_op_for_agent_off_role(db):
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    card = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=40.0, threshold=50.0,
    )
    db.commit()
    role.agentic_mode_enabled = False
    db.flush()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=30.0
    )
    assert summary == {"discarded": 0, "created": 0, "skipped_existing": 0}
    assert db.query(AgentDecision).filter(AgentDecision.id == card.id).one().status == "pending"


def test_reconcile_no_op_when_auto_reject_on(db):
    """auto_reject=on disqualifies in Workable directly rather than carding;
    the reconcile must not touch the queue for those roles.
    """
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    card = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=40.0, threshold=50.0,
    )
    db.commit()
    role.auto_reject = True
    db.flush()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=30.0
    )
    assert summary == {"discarded": 0, "created": 0, "skipped_existing": 0}
    assert db.query(AgentDecision).filter(AgentDecision.id == card.id).one().status == "pending"


def test_reconcile_leaves_non_pre_screen_decisions_untouched(db):
    """Only ``skip_assessment_reject`` cards are threshold-driven. A pending
    full-pipeline ``reject`` on the same app must survive a reconcile.
    """
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    other = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type="reject",
        recommendation="reject",
        status="pending",
        reasoning="full-pipeline reject",
        evidence={},
        confidence=None,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"reject:{app.id}",
        active_capabilities={},
        token_spend={},
    )
    db.add(other); db.commit()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=30.0
    )
    assert summary["discarded"] == 0
    assert db.query(AgentDecision).filter(AgentDecision.id == other.id).one().status == "pending"


# ---------------------------------------------------------------------------
# rederive_pre_screen_recommendations — fix stale "Below threshold" labels
# left by the old hard-coded <50 rule (relax-only, display only).
# ---------------------------------------------------------------------------


def test_rederive_relabels_above_cutoff_below_threshold_rows(db):
    """A 40-scorer on a role that rejects at 30 was branded 'Below
    threshold' by the old <50 label. Re-derive moves it off the reject
    label; genuinely-below rows and fraud-capped rows are left alone.
    """
    from app.services.pre_screen_decision_emitter import rederive_pre_screen_recommendations

    org = Organization(name="Relabel", slug=f"rl-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        auto_reject=False, agentic_mode_enabled=True, score_threshold=30,
    )
    db.add(role); db.flush()

    # Above cutoff but stale-labelled — should be relabelled.
    above = _add_app(db, org, role, score=40.0, rec="Below threshold", email="a@x.test")
    # Genuinely below cutoff — keep.
    below = _add_app(db, org, role, score=20.0, rec="Below threshold", email="b@x.test")
    # Above cutoff but fraud-capped — keep the fraud verdict.
    fraud = _add_app(db, org, role, score=40.0, rec="Below threshold", email="f@x.test")
    fraud.pre_screen_evidence = {"fraud_capped": True}
    # NULL score (must-have miss) — not score-derivable, keep.
    null_rec = _add_app(db, org, role, score=None, rec="Below threshold", email="n@x.test")
    db.commit()

    summary = rederive_pre_screen_recommendations(db, role_id=int(role.id))
    assert summary["updated"] == 1
    db.refresh(above); db.refresh(below); db.refresh(fraud); db.refresh(null_rec)
    assert above.pre_screen_recommendation == "Manual review recommended"
    assert below.pre_screen_recommendation == "Below threshold"
    assert fraud.pre_screen_recommendation == "Below threshold"
    assert null_rec.pre_screen_recommendation == "Below threshold"


def test_queue_pre_screen_reject_revives_discarded_card(db):
    """The per-app idempotency key blocks a 2nd insert, so re-queueing after
    a discard must REVIVE the existing row to pending — not leave the
    candidate with no pending card.
    """
    org, role, app = _seed(db, score=20.0, threshold=50.0)
    first = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    db.commit()
    # Simulate a prior reconcile discard.
    first.status = "discarded"
    db.commit()

    revived = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    assert revived is not None
    assert revived.id == first.id  # same row, no duplicate
    assert revived.status == "pending"
    assert revived.resolved_at is None
    n = db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count()
    assert n == 1


def test_queue_pre_screen_reject_does_not_revive_recruiter_resolution(db):
    """A recruiter-resolved (overridden) card must NOT be reopened by a
    re-queue — the cohort tick re-runs reconcile each cycle, so reviving it
    would undo the human decision repeatedly.
    """
    org, role, app = _seed(db, score=20.0, threshold=50.0)
    first = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    db.commit()
    first.status = "overridden"  # recruiter kept the candidate in pipeline
    db.commit()

    result = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    assert result is not None
    assert result.id == first.id
    assert result.status == "overridden"  # left as-is, NOT revived to pending
    n = db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count()
    assert n == 1


def test_reconcile_threshold_replay_revives_after_discard(db):
    """Full replay: 50→30 discards a 40-scorer; 30→50 must put it back as a
    pending card (the silent-miss Codex flagged).
    """
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=40.0, threshold=50.0,
    )
    db.commit()

    lowered = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=30.0
    )
    assert lowered["discarded"] == 1
    assert _latest_status(db, app) == "discarded"

    raised = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=50.0
    )
    assert raised["created"] == 1
    assert _latest_status(db, app) == "pending"
    # Exactly one row — revived, not duplicated.
    assert db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count() == 1


def test_reconcile_emit_matches_recommendation_case_insensitively(db):
    """A non-canonical 'below threshold' (lowercase) null-score row must
    still get a card — the decider normalizes, so the emit query must too.
    """
    org, role, app = _seed(db, score=None, threshold=30.0)
    app.pre_screen_recommendation = "below threshold "  # lowercase + trailing space
    db.commit()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=30.0
    )
    assert summary["created"] == 1
    assert _latest_status(db, app) == "pending"


def test_reconcile_emits_rec_only_rejects_when_threshold_none(db):
    """With threshold cleared to None, numeric rejects are dropped but
    recommendation-only (must-have miss) rejects must still be surfaced.
    """
    org, role, app = _seed(db, score=None, threshold=None)
    app.pre_screen_recommendation = "Below threshold"
    db.commit()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=None
    )
    assert summary["created"] == 1
    assert _latest_status(db, app) == "pending"


def test_reconcile_threshold_cleared_discards_scored_reject(db):
    """Clearing the threshold to None removes score-based rejects: a scored
    candidate with a stale 'Below threshold' label is discarded (no cutoff),
    while a null-score must-have-miss is kept.
    """
    org, role, scored = _seed(db, score=40.0, threshold=50.0)
    scored.pre_screen_recommendation = "Below threshold"  # stale <50 label
    null_rec = _add_app(db, org, role, score=None, rec="Below threshold", email="z@x.test")
    for a in (scored, null_rec):
        queue_pre_screen_reject(
            db, organization_id=org.id, role=role, application=a,
            pre_screen_score=a.pre_screen_score_100, threshold=50.0,
        )
    db.commit()

    summary = reconcile_pre_screen_reject_decisions(
        db, role=role, organization_id=int(org.id), threshold=None
    )
    assert summary["discarded"] == 1  # the scored row — no cutoff to be below
    assert _latest_status(db, scored) == "discarded"
    assert _latest_status(db, null_rec) == "pending"  # must-have miss survives
