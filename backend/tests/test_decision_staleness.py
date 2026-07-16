"""Decision integrity: A1 fingerprint, A2 staleness, A6 terminal-state freeze.

These lock in the recruiter-trust invariants:
- A queued decision snapshots the inputs it cited (A1).
- The Hub flags a pending decision as stale when those inputs shift (A2).
- Resolved candidates (rejected / hired / advanced) are frozen — their
  decision is never re-evaluated and never flagged stale (A6).
- queue_decision refuses to act on resolved candidates (A6) and dedups a
  recently-discarded re-emit (C3).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import event

import pytest
from fastapi import HTTPException

from app.actions import queue_decision
from app.actions.types import Actor
from app.domains.assessments_runtime.role_support import is_resolved
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.services import decision_staleness


def _seed(db, *, outcome="open", stage="review", cv="some cv text"):
    org = Organization(name="O", slug=f"o-{uuid4().hex}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire an engineer",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_reject=False,
    )
    db.add(role); db.flush()
    crit = RoleCriterion(role_id=role.id, text="5y Python", bucket="must_have", weight=2.0)
    db.add(crit); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        cv_text=cv,
        pre_screen_score_100=72.0,
        cv_match_score=80.0,
    )
    db.add(app); db.flush()
    return org, role, crit, app


def _agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run); db.flush()
    return run


def _queue(db, org, role, app):
    run = _agent_run(db, role)
    db.commit()
    decision = queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong CV.", confidence=0.9, model_version="m", prompt_version="p",
    )
    db.commit()
    return decision


# ---------------------------------------------------------------------------
# A6: is_resolved helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "outcome,stage,expected",
    [
        ("open", "review", False),
        ("open", "applied", False),
        ("rejected", "review", True),
        ("hired", "review", True),
        ("open", "advanced", True),
    ],
)
def test_is_resolved(db, outcome, stage, expected):
    _, _, _, app = _seed(db, outcome=outcome, stage=stage)
    assert is_resolved(app) is expected


# ---------------------------------------------------------------------------
# A1: fingerprint capture at queue time
# ---------------------------------------------------------------------------

def test_queue_captures_input_fingerprint(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    fp = decision.input_fingerprint
    assert isinstance(fp, dict) and fp  # non-empty
    assert decision.criteria_fingerprint  # scalar shortcut populated
    assert fp["pre_screen_score_at_emit"] == 72.0
    assert decision.decision_dedup_key  # C4 key populated


# ---------------------------------------------------------------------------
# A2: staleness detection
# ---------------------------------------------------------------------------

def test_fresh_decision_not_stale(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False
    assert report.reasons == []


def test_criteria_edit_marks_stale(db):
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    # Recruiter edits the must-have criterion text after the decision queued.
    crit.text = "8y Python + Go"
    db.add(crit); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert "criteria_changed" in report.reasons
    assert report.summary  # human label present


# ---------------------------------------------------------------------------
# A2: engine-version staleness (the "old model" dimension)
# ---------------------------------------------------------------------------

def _force_holistic(monkeypatch, enabled: bool = True):
    """Pin the org-gate so engine-staleness is exercised independently of the
    ambient HOLISTIC_SCORING_* settings."""
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._holistic_enabled_for",
        lambda application: enabled,
    )


def test_old_engine_score_marks_stale(db, monkeypatch):
    _force_holistic(monkeypatch)
    org, role, _, app = _seed(db)
    app.cv_match_details = {"prompt_version": "cv_match_v16"}  # → engine v1.16.0
    db.add(app); db.commit()
    decision = _queue(db, org, role, app)
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert "engine_outdated" in report.reasons
    assert report.details["engine_outdated"]["engine_version"] == "1.16.0"
    assert report.details["engine_outdated"]["current"]  # current engine version
    assert "older model" in (report.summary or "").lower()


def test_current_engine_score_not_stale(db, monkeypatch):
    _force_holistic(monkeypatch)
    org, role, _, app = _seed(db)
    app.cv_match_details = {"prompt_version": "holistic_v2", "engine_version": "2.1.0"}
    db.add(app); db.commit()
    decision = _queue(db, org, role, app)
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False
    assert "engine_outdated" not in report.reasons


def test_old_engine_not_flagged_when_org_off_holistic(db, monkeypatch):
    # An org NOT on the holistic engine has no newer engine to move to —
    # flagging its v1.x scores would loop forever. score_is_outdated gates it.
    _force_holistic(monkeypatch, enabled=False)
    org, role, _, app = _seed(db)
    app.cv_match_details = {"prompt_version": "cv_match_v16"}
    db.add(app); db.commit()
    decision = _queue(db, org, role, app)
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False


def test_resolved_app_never_stale_even_on_old_engine(db, monkeypatch):
    _force_holistic(monkeypatch)
    org, role, _, app = _seed(db)
    app.cv_match_details = {"prompt_version": "cv_match_v16"}
    db.add(app); db.commit()
    decision = _queue(db, org, role, app)
    # Candidate later rejected → frozen audit snapshot, never re-flagged (A6).
    app.application_outcome = "rejected"
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False


def test_old_engine_flags_even_without_fingerprint(db, monkeypatch):
    # Engine staleness is known from the stored blob alone, so it flags even a
    # pre-A1 (fingerprint-less) decision — unlike the input-drift dimensions.
    _force_holistic(monkeypatch)
    org, role, _, app = _seed(db)
    app.cv_match_details = {"prompt_version": "cv_match_v16"}
    db.add(app); db.commit()
    decision = _queue(db, org, role, app)
    # Simulate a pre-A1 row: wipe the captured fingerprint baseline.
    decision.input_fingerprint = {}
    decision.criteria_fingerprint = None
    db.add(decision); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert report.reasons == ["engine_outdated"]


def _rederive_criteria(db, role, specs):
    """Simulate Workable ``_replace_derived_criteria``: hard-delete every
    active criterion and re-insert ``specs`` with genuinely FRESH row ids.

    Postgres assigns new serial ids on re-insert; SQLite would otherwise reuse
    the just-freed id, which would mask an id-based-hash regression. We force
    ids strictly above the prior max so the "content-only" guarantee is
    actually exercised.
    """
    from sqlalchemy import func

    prev_max = db.query(func.max(RoleCriterion.id)).scalar() or 0
    db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).delete()
    db.flush()
    for offset, (text, bucket, weight) in enumerate(specs, start=1):
        db.add(
            RoleCriterion(
                id=prev_max + offset,
                role_id=role.id,
                text=text,
                bucket=bucket,
                weight=weight,
            )
        )
    db.flush()


def test_criteria_fingerprint_is_content_only_stable_across_id_churn(db):
    """The fingerprint must hash criteria CONTENT, not row ids.

    Regression for the prod incident: Workable sync hard-deletes + re-inserts
    derived criteria with new ids on every tick. An id-based hash churned each
    sync and spuriously marked every pending decision stale. Content-only means
    re-deriving identical criteria is a no-op.
    """
    _, role, _, _ = _seed(db)
    fp_before = decision_staleness.criteria_content_fingerprint(db, int(role.id))
    assert fp_before  # baseline present

    # Re-derive the SAME criterion (different row id, identical content).
    _rederive_criteria(db, role, [("5y Python", "must_have", 2.0)])
    db.commit()
    fp_after = decision_staleness.criteria_content_fingerprint(db, int(role.id))

    # New row id but identical content → identical fingerprint.
    assert fp_after == fp_before


def test_criteria_rederive_identical_keeps_decision_fresh(db):
    """End-to-end: a sync re-derive of unchanged criteria must NOT flip a
    pending decision to stale (the bug that 409'd advances in prod)."""
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    assert decision_staleness.evaluate(db, decision).is_stale is False

    _rederive_criteria(db, role, [("5y Python", "must_have", 2.0)])
    db.commit()

    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False
    assert "criteria_changed" not in report.reasons


def test_criteria_rederive_with_changed_content_marks_stale(db):
    """The flip-side guard: a re-derive that actually changes criterion
    content (new requirement) must still mark the decision stale."""
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)

    _rederive_criteria(
        db, role,
        [("5y Python", "must_have", 2.0), ("Kubernetes in prod", "nice_to_have", 1.0)],
    )
    db.commit()

    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert "criteria_changed" in report.reasons


def test_rebaseline_pending_criteria_fingerprint_unstales(db):
    """rebaseline_pending_criteria_fingerprint re-points pending decisions at
    the current criteria fingerprint without re-running the agent — used for
    immaterial spec edits + the one-time backfill."""
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    assert decision_staleness.evaluate(db, decision).is_stale is False

    # Criteria content changes (would normally mark the decision stale).
    crit.text = "8y Python + Go"
    db.add(crit); db.commit()
    assert decision_staleness.evaluate(db, decision).is_stale is True

    updated = decision_staleness.rebaseline_pending_criteria_fingerprint(
        db, role_id=int(role.id)
    )
    db.commit()
    assert updated == 1
    # No longer stale on the criteria dimension.
    report = decision_staleness.evaluate(db, decision)
    assert "criteria_changed" not in report.reasons
    assert report.is_stale is False


def test_pre_screen_score_swing_marks_stale(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 50.0  # was 72 → 22pt drop, well over the 5pt band
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert "pre_screen_score_shifted" in report.reasons


def test_sub_band_score_noise_not_stale(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 74.0  # 2pt jitter, under the 5pt band
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert "pre_screen_score_shifted" not in report.reasons


def test_resolved_decision_never_stale(db):
    """A6: once the candidate is resolved, the decision is frozen — even
    if criteria change, it's the immutable audit record, not stale."""
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    crit.text = "totally different"
    db.add(crit)
    app.application_outcome = "rejected"  # candidate resolved after queue
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False


def test_pre_a1_decision_not_stale(db):
    """Decisions queued before A1 (empty fingerprint) have no baseline —
    we must not flag them as stale."""
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    decision.input_fingerprint = {}
    decision.criteria_fingerprint = None
    db.add(decision); db.commit()
    crit.text = "changed"
    db.add(crit); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False


def test_staleness_cache_collapses_per_role_queries(db):
    """N+1 guard: evaluating a batch of decisions that share a role must
    look up role_criteria / role_feedback_notes once per distinct role,
    not once per decision, when a shared StalenessCache is passed.

    Without the cache the Decision Hub list endpoint issued 2 queries per
    pending row; this locks in the collapse so a future refactor that
    drops the ``cache`` arg can't silently reintroduce the N+1.
    """
    org, role, _, app1 = _seed(db)
    decision1 = _queue(db, org, role, app1)

    # Second candidate + application on the SAME role → both decisions
    # share its criteria and feedback notes.
    cand2 = Candidate(organization_id=org.id, email="c2@x.test", full_name="C2")
    db.add(cand2); db.flush()
    app2 = CandidateApplication(
        organization_id=org.id, candidate_id=cand2.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual", cv_text="some cv text",
        pre_screen_score_100=72.0, cv_match_score=80.0,
    )
    db.add(app2); db.flush()
    decision2 = _queue(db, org, role, app2)

    bind = db.get_bind()
    counts = {"role_criteria": 0, "role_feedback_notes": 0}

    def _on_exec(conn, cursor, statement, params, context, executemany):
        for table in counts:
            if f"FROM {table}" in statement:
                counts[table] += 1

    event.listen(bind, "after_cursor_execute", _on_exec)
    try:
        cache = decision_staleness.StalenessCache()
        decision_staleness.evaluate(db, decision1, cache=cache)
        decision_staleness.evaluate(db, decision2, cache=cache)
    finally:
        event.remove(bind, "after_cursor_execute", _on_exec)

    assert counts["role_criteria"] == 1
    assert counts["role_feedback_notes"] == 1


# ---------------------------------------------------------------------------
# A6: queue_decision refuses resolved candidates
# ---------------------------------------------------------------------------

def test_queue_decision_refuses_resolved_app(db):
    org, role, _, app = _seed(db, outcome="rejected")
    run = _agent_run(db, role)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        queue_decision.run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
            decision_type="advance_to_interview",
            reasoning="x", confidence=0.9, model_version="m", prompt_version="p",
        )
    assert exc.value.status_code == 422
    assert "resolved" in str(exc.value.detail).lower()


# ---------------------------------------------------------------------------
# C3: recently-discarded suppression
# ---------------------------------------------------------------------------

def test_list_agent_decisions_route_returns_pending_with_staleness(db):
    """Regression: list_agent_decisions must execute end-to-end with a
    pending decision in the queue. A function-local re-import of
    CandidateApplication once shadowed the module-level name and raised
    UnboundLocalError at runtime (prod queue went dark). This exercises
    the exact path — the join + the staleness batch-load — so it can't
    regress silently again.
    """
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes

    org, role, _, app = _seed(db)
    app.cv_match_details = {
        "summary": (
            "Strong production Python and CI ownership. "
            "The full report contains a longer evidence walkthrough."
        )
    }
    _queue(db, org, role, app)

    current_user = SimpleNamespace(organization_id=int(org.id), id=1)
    # Pass every param explicitly — calling the route fn directly bypasses
    # FastAPI's Query(...) default resolution.
    payloads = agentic_routes.list_agent_decisions(
        role_id=int(role.id),
        application_id=None,
        status="pending",
        decision_type=None,
        q=None,
        since=None,
        limit=50,
        db=db,
        current_user=current_user,
    )
    assert len(payloads) == 1
    p = payloads[0]
    assert p.status == "pending"
    # Trust-signal fields the Hub renders are populated.
    assert p.confidence_band in {"high", "medium", "low", None}
    assert p.age_seconds >= 0
    assert p.is_stale is False  # fresh decision
    # Decision cause and candidate synthesis are separate API concepts.
    assert p.decision_explanation["source"] == "agent"
    assert p.decision_explanation["summary"] == "Strong CV."
    assert p.candidate_summary == (
        "Strong production Python and CI ownership. "
        "The full report contains a longer evidence walkthrough."
    )


def test_approve_route_409s_on_stale_decision(db):
    """A4: approving a stale decision returns 409 (unless force=true).
    Exercises the route end-to-end — the class of bug (runtime error in
    a route my isolation tests skipped) that took the queue down."""
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes

    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    crit.text = "changed materially"
    db.add(crit); db.commit()

    user = SimpleNamespace(organization_id=int(org.id), id=1)
    with pytest.raises(HTTPException) as exc:
        agentic_routes.approve(
            decision_id=int(decision.id),
            body=agentic_routes.ApproveBody(),
            force=False,
            db=db,
            current_user=user,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail.get("code") == "decision_stale"


def test_re_evaluate_route_discards_and_requeues(db, monkeypatch):
    """A4: re-evaluate discards the pending decision and enqueues a fresh
    cycle. Mock the Celery dispatch so we don't run a real cycle."""
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes
    from app.tasks import agent_tasks

    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)

    monkeypatch.setattr(
        agent_tasks.agent_manual_run, "delay",
        lambda **kw: SimpleNamespace(id="fake-task-id"),
    )
    user = SimpleNamespace(organization_id=int(org.id), id=1)
    result = agentic_routes.re_evaluate(
        decision_id=int(decision.id), db=db, current_user=user,
    )
    assert result.superseded >= 1
    assert result.queued is True
    db.refresh(decision)
    assert decision.status == "discarded"


def test_re_evaluate_route_409s_on_resolved_app(db):
    """A6: a resolved candidate's decision is frozen — re-evaluate 409s."""
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes

    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.application_outcome = "hired"  # resolved after queue
    db.add(app); db.commit()

    user = SimpleNamespace(organization_id=int(org.id), id=1)
    with pytest.raises(HTTPException) as exc:
        agentic_routes.re_evaluate(
            decision_id=int(decision.id), db=db, current_user=user,
        )
    assert exc.value.status_code == 409


def test_recently_discarded_decision_suppresses_reemit(db):
    from app.models.user import User

    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    recruiter = User(
        email=f"rec-{id(db)}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(recruiter); db.flush()
    # Recruiter discards it. resolved_by_user_id marks it as an explicit
    # human "no" — system discards (NULL) deliberately don't suppress.
    decision.status = "discarded"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = recruiter.id
    db.add(decision); db.commit()

    # Agent re-emits the same type within the 10-min window.
    run2 = _agent_run(db, role)
    second = queue_decision.run(
        db, Actor.agent(int(run2.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="again", confidence=0.9, model_version="m", prompt_version="p",
    )
    assert second.id == decision.id  # returned the discarded row, no new pending
    pending = db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id,
        AgentDecision.status == "pending",
    ).count()
    assert pending == 0


# ---------------------------------------------------------------------------
# Verdict-aware staleness: a score re-score that doesn't flip the deterministic
# verdict is a "hold" — its banner clears itself; only genuine flips stay.
# ---------------------------------------------------------------------------


def test_score_drift_suppressed_when_verdict_holds(db, monkeypatch):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)  # advance_to_interview, pre_screen@emit=72
    app.pre_screen_score_100 = 60.0  # 12pt drift → would normally banner
    db.flush()
    import app.services.bulk_decision_service as bds
    # The rule still says the same thing → a hold.
    monkeypatch.setattr(bds, "recompute_persisted_verdict",
                        lambda db, *, role, app: "advance_to_interview")
    report = decision_staleness.evaluate(db, decision, application=app, role=role)
    assert report.is_stale is False
    assert "pre_screen_score_shifted" not in report.reasons


def test_score_drift_kept_when_verdict_flips(db, monkeypatch):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 60.0
    db.flush()
    import app.services.bulk_decision_service as bds
    # The rule now says reject — a genuine flip; the recruiter must see it.
    monkeypatch.setattr(bds, "recompute_persisted_verdict",
                        lambda db, *, role, app: "reject")
    report = decision_staleness.evaluate(db, decision, application=app, role=role)
    assert report.is_stale is True
    assert "pre_screen_score_shifted" in report.reasons


def test_score_drift_kept_when_recompute_unavailable(db, monkeypatch):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 60.0
    db.flush()
    import app.services.bulk_decision_service as bds
    # Can't recompute (escalate / unscorable / error) → fail safe, keep banner.
    monkeypatch.setattr(bds, "recompute_persisted_verdict",
                        lambda db, *, role, app: None)
    report = decision_staleness.evaluate(db, decision, application=app, role=role)
    assert report.is_stale is True
    assert "pre_screen_score_shifted" in report.reasons


def test_verdict_hold_does_not_suppress_cv_replaced(db, monkeypatch):
    # A holding verdict clears the score noise but NOT genuine new info (a new CV).
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 60.0
    app.cv_text = "an entirely different resume body now"
    db.flush()
    import app.services.bulk_decision_service as bds
    monkeypatch.setattr(bds, "recompute_persisted_verdict",
                        lambda db, *, role, app: "advance_to_interview")
    report = decision_staleness.evaluate(db, decision, application=app, role=role)
    assert report.is_stale is True
    assert "cv_replaced" in report.reasons
    assert "pre_screen_score_shifted" not in report.reasons  # score noise still cleared
