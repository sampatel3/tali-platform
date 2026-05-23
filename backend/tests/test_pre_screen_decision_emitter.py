"""Pre-screen failures surface as Decision Hub cards instead of being
silently parked. Covers the new system-side emitter + the one-shot
backfill that catches up historical stranded apps.
"""
from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.pre_screen_decision_emitter import (
    backfill_discard_decisions_on_closed_apps,
    backfill_existing_below_threshold,
    backfill_pre_screen_reject_reasoning,
    backfill_recommendations_from_cvmatch,
    backfill_summaries_from_cvmatch,
    discard_pending_decisions_for_app,
    pre_screen_gate_divergence_report,
    queue_pre_screen_reject,
    reconcile_pre_screen_reject_decisions,
    repair_passed_prescreen_contamination,
    supersede_mislabeled_pre_screen_rejects,
    supersede_pre_screen_reject_on_full_score,
)

# The SQLite BigInteger-PK workaround for AgentDecision is registered
# globally in conftest.py (alongside the claude_call_log one), so this file
# no longer needs a local listener and tests work regardless of import order.


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


def _direct_card(db, org, role, app, threshold, resolved_by=None):
    """Create a pending skip_assessment_reject row directly (the emitter now
    refuses to create one for a fully-scored app, so tests build it here)."""
    d = AgentDecision(
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        agent_run_id=None, decision_type="skip_assessment_reject",
        recommendation="skip_assessment_reject", status="pending", reasoning="x",
        evidence={"threshold_100": threshold}, confidence=None,
        model_version="pre_screen_v1", prompt_version="pre_screen_threshold.v1",
        idempotency_key=f"pre_screen_reject:{int(app.id)}",
        active_capabilities={}, token_spend={}, resolved_by_user_id=resolved_by,
    )
    db.add(d); db.flush()
    return d


def test_emitter_defers_when_fully_scored(db):
    """Once a candidate has a cv_match score, the pre-screen gate is moot —
    the emitter must not create a pre-screen reject card (the agent owns it)."""
    org, role, app = _seed(db, score=15.0, threshold=30.0)
    app.cv_match_score = 15.0
    db.flush()
    result = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=15.0, threshold=30.0,
    )
    assert result is None
    assert db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count() == 0


def test_supersede_on_full_score_discards_when_cleared(db):
    """A pre-screen reject card is discarded when the full score clears the bar."""
    org, role, app = _seed(db, score=None, threshold=30.0)
    d = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=None, threshold=30.0,
    )
    assert d is not None and d.status == "pending"
    app.cv_match_score = 80.0
    db.flush()
    n = supersede_pre_screen_reject_on_full_score(db, application=app, threshold=30.0)
    assert n == 1
    db.flush()  # helper defers commit to its caller (the scoring orchestrator)
    db.refresh(d)
    assert d.status == "discarded"


def test_supersede_on_full_score_keeps_when_below(db):
    """A full score that's also below the bar leaves the reject standing."""
    org, role, app = _seed(db, score=None, threshold=30.0)
    d = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=None, threshold=30.0,
    )
    app.cv_match_score = 12.0
    db.flush()
    n = supersede_pre_screen_reject_on_full_score(db, application=app, threshold=30.0)
    assert n == 0
    db.refresh(d)
    assert d.status == "pending"


def test_supersede_mislabeled_discards_A_and_B_keeps_C(db):
    """Bulk backfill: discard A (passed pre-screen) and B (cleared on full
    score), keep C (genuine pre-screen reject), skip human-resolved rows."""
    org = Organization(name="O", slug=f"o-{id(db)}m"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True)
    db.add(role); db.flush()

    def mkapp(email, llm, ps_score, cv):
        c = Candidate(organization_id=org.id, email=email, full_name=email)
        db.add(c); db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
            pre_screen_score_100=ps_score, cv_match_score=cv,
            pre_screen_evidence={"llm_score_100": llm},
        )
        db.add(a); db.flush()
        return a

    thr = 30.0
    a_app = mkapp("a@x.test", llm=72, ps_score=12, cv=12)   # A: passed pre-screen
    b_app = mkapp("b@x.test", llm=28, ps_score=84, cv=84)   # B: cleared on full score
    c_app = mkapp("c@x.test", llm=20, ps_score=15, cv=15)   # C: genuine reject
    da = _direct_card(db, org, role, a_app, thr)
    db_b = _direct_card(db, org, role, b_app, thr)
    dc = _direct_card(db, org, role, c_app, thr)

    res = supersede_mislabeled_pre_screen_rejects(db, organization_id=int(org.id))
    assert res["discarded"] == 2
    assert res["scanned"] == 3
    for d in (da, db_b, dc):
        db.refresh(d)
    assert da.status == "discarded"   # A: passed pre-screen
    assert db_b.status == "discarded" # B: full score cleared the bar
    assert dc.status == "pending"     # C: genuine reject untouched


def test_supersede_mislabeled_dry_run_does_not_write(db):
    org, role, app = _seed(db, score=12.0, threshold=30.0)
    app.cv_match_score = 12.0
    app.pre_screen_evidence = {"llm_score_100": 72}  # A
    db.flush()
    card = _direct_card(db, org, role, app, 30.0)
    res = supersede_mislabeled_pre_screen_rejects(db, organization_id=int(org.id), dry_run=True)
    assert res["discarded"] == 1
    db.refresh(card)
    assert card.status == "pending"


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
    # Reasoning is a qualitative reason only — the numeric score/threshold
    # are internal and must NOT be surfaced on the card.
    assert "35" not in (decision.reasoning or "")
    assert "50" not in (decision.reasoning or "")
    # No stored evidence summary on this app → generic role-requirements reason.
    assert "Does not meet the role's requirements." in (decision.reasoning or "")


def test_reasoning_uses_pre_screen_summary_when_present(db):
    """A stored pre-screen ``summary`` (the LLM's one-sentence rationale) is
    surfaced verbatim as the candidate-specific reason."""
    org, role, app = _seed(db, score=20.0, threshold=50.0)
    app.pre_screen_evidence = {
        "summary": "Missing must-have Kubernetes and CI/CD experience the role requires"
    }
    db.flush()
    decision = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    assert "Missing must-have Kubernetes and CI/CD experience the role requires." in (
        decision.reasoning or ""
    )
    assert "20" not in (decision.reasoning or "")


def test_reasoning_flags_fraud_capped(db):
    """A fraud-capped candidate gets the fraud reason, not the LLM summary."""
    org, role, app = _seed(db, score=10.0, threshold=50.0)
    app.pre_screen_evidence = {"fraud_capped": True, "summary": "some skills summary"}
    db.flush()
    decision = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=10.0, threshold=50.0,
    )
    assert "fraud" in (decision.reasoning or "").lower()
    assert "some skills summary" not in (decision.reasoning or "")


def test_backfill_rewrites_stale_numeric_reasoning(db):
    """Existing pending cards carrying the old numeric template are rewritten
    to the qualitative reason; already-clean cards are left untouched."""
    org, role, app = _seed(db, score=30.0, threshold=50.0)
    app.pre_screen_evidence = {"summary": "Lacks the required cloud security background"}
    db.flush()
    decision = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=30.0, threshold=50.0,
    )
    # Simulate a legacy stored card with the old numeric template.
    decision.reasoning = "Below pre-screen threshold (score: 30.0, threshold: 50.0). Surfaced for recruiter review."
    db.flush()

    result = backfill_pre_screen_reject_reasoning(db, organization_id=int(org.id))
    assert result["updated"] == 1
    assert result["scanned"] == 1
    db.refresh(decision)
    assert "Lacks the required cloud security background." in (decision.reasoning or "")
    assert "30.0" not in (decision.reasoning or "")

    # Re-running is a no-op now that the text is already correct.
    result2 = backfill_pre_screen_reject_reasoning(db, organization_id=int(org.id))
    assert result2 == {"updated": 0, "scanned": 1}


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
        # Not yet fully scored (cv_match_score is None): the pre-screen score
        # lives in cv_match_details where the snapshot reads it. A *fully*
        # scored candidate would defer to the agent (see the deferral test).
        cv_match_score=None,
        cv_match_details={"pre_screen_score_100": 20.0},
        pre_screen_recommendation="Below threshold",
    )
    db.add(app); db.commit()

    verdict = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict["should_trigger"] is True, verdict
    assert verdict["state"] == "eligible"


def test_evaluate_auto_reject_defers_when_fully_scored(db):
    """Once cv_match scoring has run, the pre-screen gate must defer to the
    agent's cv_match decision rather than firing on the (overwritten) score."""
    from app.decision_policy.auto_reject import evaluate_auto_reject_decision

    org = Organization(name="O", slug=f"o-{id(db)}fs")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        auto_reject=False, agentic_mode_enabled=True, score_threshold=50,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="fs@x.test", full_name="FS", workable_candidate_id="wid-fs")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        pre_screen_score_100=12.0, cv_match_score=12.0,  # fully scored, below bar
        pre_screen_recommendation="Below threshold",
    )
    db.add(app); db.commit()

    verdict = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict["should_trigger"] is False, verdict
    assert verdict["state"] == "deferred_to_full_scoring"


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
        # Not yet fully scored; the pre-screen score is read from
        # cv_match_details. (A fully-scored above-bar candidate would also
        # not trigger, but via the deferral path.)
        cv_match_score=None,
        cv_match_details={"pre_screen_score_100": 75.0},
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


def test_rederive_corrects_non_canonical_below_threshold_label(db):
    """Stale labels stored non-canonically ('below threshold ' lowercase +
    space) must still be scanned and corrected, or the self-heal never
    converges on dirty data.
    """
    from app.services.pre_screen_decision_emitter import rederive_pre_screen_recommendations

    org = Organization(name="Norm", slug=f"nm-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        auto_reject=False, agentic_mode_enabled=True, score_threshold=30,
    )
    db.add(role); db.flush()
    app = _add_app(db, org, role, score=40.0, rec="below threshold ", email="nc@x.test")
    db.commit()

    summary = rederive_pre_screen_recommendations(db, role_id=int(role.id))
    assert summary["updated"] == 1
    db.refresh(app)
    assert app.pre_screen_recommendation == "Manual review recommended"


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


def test_queue_pre_screen_reject_does_not_revive_recruiter_discard(db):
    """A recruiter *discard* (toggle-off bulk discard) also sets
    status='discarded' but stamps resolved_by_user_id. That must NOT be
    revived — only system supersede (no human resolver) is revivable.
    """
    from app.models.user import User

    org, role, app = _seed(db, score=20.0, threshold=50.0)
    user = User(email=f"r{id(db)}@x.test", hashed_password="x", organization_id=org.id)
    db.add(user); db.flush()
    first = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    db.commit()
    first.status = "discarded"
    first.resolved_by_user_id = user.id  # recruiter discarded it
    db.commit()

    result = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    assert result is not None
    assert result.id == first.id
    assert result.status == "discarded"  # NOT revived — human discard respected
    assert result.resolved_by_user_id == user.id


def test_queue_pre_screen_reject_does_not_revive_threshold_cleared_card(db):
    """A card system-discarded because the threshold was *cleared*
    (threshold=None ⇒ no score-based reject for a scored candidate) must NOT
    be revived on a later re-queue — otherwise it churns pending↔discarded
    each cohort tick. Revival is gated on current below-threshold eligibility.
    """
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    app.pre_screen_recommendation = "Below threshold"  # stale <50 label
    first = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=40.0, threshold=50.0,
    )
    db.commit()
    first.status = "discarded"  # system supersede (no resolver) — threshold cleared
    db.commit()

    # Re-queue with threshold=None (cleared): a scored candidate is no longer
    # a score-based reject, so the discarded card must stay discarded.
    result = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=40.0, threshold=None,
    )
    assert result is not None
    assert result.id == first.id
    assert result.status == "discarded"  # NOT revived


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


def test_pending_decision_map_resolves_per_app(db):
    """The candidate-list AGENT column reads its decision from a per-app
    batch map so it isn't capped by the /agent-decisions fetch limit.
    """
    from app.domains.assessments_runtime.applications_routes import _pending_decision_map

    org, role, app = _seed(db, score=20.0, threshold=50.0)
    decision = queue_pre_screen_reject(
        db, organization_id=org.id, role=role, application=app,
        pre_screen_score=20.0, threshold=50.0,
    )
    db.commit()

    m = _pending_decision_map(db, [app.id])
    assert app.id in m
    assert m[app.id]["id"] == decision.id
    assert m[app.id]["decision_type"] == "skip_assessment_reject"
    assert m[app.id]["recommendation"] == "skip_assessment_reject"
    assert m[app.id]["status"] == "pending"

    # Resolved decisions drop out of the map.
    decision.status = "discarded"
    db.commit()
    assert _pending_decision_map(db, [app.id]) == {}

    # Empty input is a no-op (no query).
    assert _pending_decision_map(db, []) == {}


# ---------------------------------------------------------------------------
# Score/decision consistency repairs (P1–P4)
# ---------------------------------------------------------------------------


def test_discard_pending_decisions_for_app_skips_human_resolved(db):
    org, role, app = _seed(db, score=15.0, threshold=30.0)
    d = _direct_card(db, org, role, app, 30.0)
    n = discard_pending_decisions_for_app(db, application_id=int(app.id), reason="closed")
    assert n == 1
    db.flush(); db.refresh(d)
    assert d.status == "discarded"


def test_backfill_discard_decisions_on_closed_apps(db):
    org = Organization(name="O", slug=f"o-{id(db)}cl"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True)
    db.add(role); db.flush()

    def mkapp(email, outcome):
        c = Candidate(organization_id=org.id, email=email, full_name=email); db.add(c); db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome=outcome, source="manual", pre_screen_score_100=15.0,
        )
        db.add(a); db.flush(); return a

    open_app = mkapp("open@x.test", "open")
    closed_app = mkapp("closed@x.test", "rejected")
    d_open = _direct_card(db, org, role, open_app, 30.0)
    d_closed = _direct_card(db, org, role, closed_app, 30.0)

    res = backfill_discard_decisions_on_closed_apps(db, organization_id=int(org.id))
    assert res["discarded"] == 1
    assert res["scanned"] == 1
    db.refresh(d_open); db.refresh(d_closed)
    assert d_open.status == "pending"      # open app untouched
    assert d_closed.status == "discarded"  # closed app's card discarded


def test_backfill_recommendations_from_cvmatch_both_directions(db):
    org, role, _ = _seed(db, score=None, threshold=50.0)
    role.score_threshold = 50
    db.flush()

    def mkapp(email, score, rec, fraud=False):
        c = Candidate(organization_id=org.id, email=email, full_name=email); db.add(c); db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
            pre_screen_score_100=score, cv_match_score=score,
            pre_screen_recommendation=rec,
            pre_screen_evidence={"fraud_capped": True} if fraud else {},
        )
        db.add(a); db.flush(); return a

    low = mkapp("low@x.test", 12.0, "Strong match")     # stale high → should drop
    high = mkapp("high@x.test", 85.0, "Below threshold")  # stale low → should raise
    fraud = mkapp("fraud@x.test", 10.0, "Below threshold", fraud=True)  # keep

    res = backfill_recommendations_from_cvmatch(db, organization_id=int(org.id))
    assert res["updated"] == 2
    db.refresh(low); db.refresh(high); db.refresh(fraud)
    assert low.pre_screen_recommendation == "Below threshold"
    assert high.pre_screen_recommendation == "Strong match"
    assert fraud.pre_screen_recommendation == "Below threshold"  # untouched


def test_backfill_summaries_from_cvmatch(db):
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    app.pre_screen_evidence = {}  # no summary
    app.cv_match_details = {"summary": "Weak fit — missing must-have Kubernetes."}
    db.flush()
    res = backfill_summaries_from_cvmatch(db, organization_id=int(org.id))
    assert res["updated"] == 1
    db.refresh(app)
    assert "Weak fit" in (app.pre_screen_evidence or {}).get("summary", "")


def test_backfill_summaries_skips_when_present(db):
    org, role, app = _seed(db, score=40.0, threshold=50.0)
    app.pre_screen_evidence = {"summary": "existing"}
    app.cv_match_details = {"summary": "from cv"}
    db.flush()
    res = backfill_summaries_from_cvmatch(db, organization_id=int(org.id))
    assert res["updated"] == 0


def test_gate_divergence_report(db):
    org, role, _ = _seed(db, score=None, threshold=30.0)

    def mkapp(email, llm, cv):
        c = Candidate(organization_id=org.id, email=email, full_name=email); db.add(c); db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
            cv_match_score=cv, pre_screen_evidence={"llm_score_100": llm},
        )
        db.add(a); db.flush(); return a

    mkapp("fn@x.test", llm=20, cv=80)   # false negative + diverge
    mkapp("fp@x.test", llm=70, cv=15)   # false positive + diverge
    mkapp("agree@x.test", llm=60, cv=62)  # agree

    rep = pre_screen_gate_divergence_report(db, organization_id=int(org.id))
    assert rep["both_scored"] == 3
    assert rep["diverge_gt20"] == 2
    assert rep["gate_false_negatives"] == 1
    assert rep["gate_false_positives"] == 1


def test_transition_outcome_discards_pending_decisions(db):
    from app.domains.assessments_runtime.pipeline_service import transition_outcome

    org, role, app = _seed(db, score=15.0, threshold=30.0)
    d = _direct_card(db, org, role, app, 30.0)
    transition_outcome(db, app=app, to_outcome="rejected", actor_type="system")
    db.flush(); db.refresh(d)
    assert d.status == "discarded"


# ---------------------------------------------------------------------------
# Respect the pre-screen decision (don't card/revive passed candidates)
# ---------------------------------------------------------------------------


def test_emitter_skips_when_pre_screen_decision_yes(db):
    """A candidate the gate passed (decision='yes') must not get a card, even
    when the numeric score is below threshold (cv contamination)."""
    org, role, app = _seed(db, score=16.7, threshold=30.0)
    app.cv_match_score = None  # not live-scored; column holds a stale low value
    app.pre_screen_evidence = {"decision": "yes", "llm_score_100": 75.0}
    db.flush()
    result = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=16.7, threshold=30.0,
    )
    assert result is None
    assert db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count() == 0


def test_emitter_does_not_revive_passed_candidate(db):
    """A discarded card for a passed candidate stays discarded (no revival)."""
    org, role, app = _seed(db, score=16.7, threshold=30.0)
    app.pre_screen_evidence = {"decision": "yes", "llm_score_100": 75.0}
    db.flush()
    d = _direct_card(db, org, role, app, 30.0)
    d.status = "discarded"
    db.flush()
    result = queue_pre_screen_reject(
        db, organization_id=int(org.id), role=role, application=app,
        pre_screen_score=16.7, threshold=30.0,
    )
    assert result is None
    db.refresh(d)
    assert d.status == "discarded"  # not revived


def test_gate_defers_when_pre_screen_decision_yes(db):
    from app.decision_policy.auto_reject import evaluate_auto_reject_decision

    org = Organization(name="O", slug=f"o-{id(db)}psp"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True, score_threshold=30)
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="psp@x.test", full_name="P", workable_candidate_id="wid-psp")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        pre_screen_score_100=16.7, cv_match_score=None,
        cv_match_details={"pre_screen_score_100": 16.7},
        pre_screen_recommendation="Below threshold",
        pre_screen_evidence={"decision": "yes", "llm_score_100": 75.0},
    )
    db.add(app); db.commit()
    verdict = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict["should_trigger"] is False, verdict
    assert verdict["state"] == "pre_screen_passed"


def test_repair_passed_prescreen_contamination(db):
    org = Organization(name="O", slug=f"o-{id(db)}rp"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False, agentic_mode_enabled=True, score_threshold=30)
    db.add(role); db.flush()

    def mkapp(email, decision, rec, llm):
        c = Candidate(organization_id=org.id, email=email, full_name=email); db.add(c); db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
            pre_screen_score_100=16.7, pre_screen_recommendation=rec,
            pre_screen_evidence={"decision": decision, "llm_score_100": llm},
        )
        db.add(a); db.flush(); return a

    passed = mkapp("passed@x.test", "yes", "Below threshold", 75.0)   # card + rec fixed
    failed = mkapp("failed@x.test", "no", "Below threshold", 20.0)    # genuine reject — untouched
    d_passed = _direct_card(db, org, role, passed, 30.0)
    d_failed = _direct_card(db, org, role, failed, 30.0)

    res = repair_passed_prescreen_contamination(db, organization_id=int(org.id))
    assert res["cards_discarded"] == 1
    assert res["recs_fixed"] == 1
    db.refresh(d_passed); db.refresh(d_failed); db.refresh(passed); db.refresh(failed)
    assert d_passed.status == "discarded"
    assert d_failed.status == "pending"   # genuine reject untouched
    assert passed.pre_screen_recommendation != "Below threshold"  # relabelled from llm 75
    assert failed.pre_screen_recommendation == "Below threshold"  # unchanged
