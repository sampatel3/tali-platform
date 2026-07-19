"""Reconcile a Workable-side advance onto Taali.

A recruiter moving a candidate forward in Workable is reconciled by Taali, but
only a TERMINAL hand-off (Offer / Hired) freezes them as 'advanced'. A
mid-interview stage (Phone Screen / Technical / Final Interview) keeps them
in-funnel and decidable — Taali only discards a stale *reject* card (dangerous
on someone in a live interview), leaving legitimate advance/send cards alone.
Plus: scoring skips Workable-disqualified candidates.
"""
from __future__ import annotations

from app.domains.assessments_runtime.pipeline_service import (
    reconcile_post_handover_advanced,
)
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _seed(db, *, workable_stage, pipeline_stage="applied", outcome="open"):
    org = Organization(name="O", slug=f"o-{id(db)}-{id(workable_stage)}-{workable_stage}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual",
                job_spec_text="hire an engineer", agentic_mode_enabled=True)
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email=f"c{id(db)}@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage=pipeline_stage, pipeline_stage_source="recruiter",
        application_outcome=outcome, source="workable",
        workable_stage=workable_stage, cv_match_score=40.0,
    )
    db.add(app); db.flush()
    return org, role, app


# The reconcile delegates the verdict to bulk_decision_service.decide_post_handover
# (full decision policy). These tests pin that to isolate the reconcile's ROUTING:
# a Taali-advance verdict reflects the hand-off; a Taali-reject is surfaced in the
# reject queue by decide_post_handover, so the reconcile must NOT advance.
def _pin_verdict(monkeypatch, value):
    monkeypatch.setattr(
        "app.services.bulk_decision_service.decide_post_handover",
        lambda db, *, app, role: value,
    )


def _pending_reject(db, *, org, role, app):
    d = AgentDecision(
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type="reject", recommendation="reject", status="pending",
        reasoning="below threshold", confidence=0.9,
        model_version="m", prompt_version="p", idempotency_key=f"t:{app.id}",
    )
    db.add(d); db.commit()
    return d


def _pending(db, *, org, role, app, decision_type):
    d = AgentDecision(
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type=decision_type, recommendation=decision_type, status="pending",
        reasoning="x", confidence=0.9,
        model_version="m", prompt_version="p", idempotency_key=f"t:{app.id}",
    )
    db.add(d); db.commit()
    return d


def _has_pending(db, app):
    return (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == app.id, AgentDecision.status == "pending")
        .first()
        is not None
    )


# --- TERMINAL stages (Offer / Hired): freeze as 'advanced' -------------------

def test_advances_on_terminal_offer(db, monkeypatch):
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Offer")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is True
    db.commit()
    assert app.pipeline_stage == "advanced"


def test_advances_on_hired(db, monkeypatch):
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Hired")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is True
    assert app.pipeline_stage == "advanced"


def test_terminal_discards_all_pending(db, monkeypatch):
    # A terminal hand-off freezes the candidate → every queued decision is moot.
    _pin_verdict(monkeypatch, "advance")
    org, role, app = _seed(db, workable_stage="Offer")
    _pending(db, org=org, role=role, app=app, decision_type="send_assessment")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is True
    db.commit()
    assert app.pipeline_stage == "advanced"
    assert _has_pending(db, app) is False  # advance card discarded on freeze


def test_terminal_noop_when_already_advanced(db, monkeypatch):
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Offer", pipeline_stage="advanced")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False


# --- MID-INTERVIEW stages: stay decidable, never freeze ----------------------

def test_mid_interview_does_not_advance(db, monkeypatch):
    # Technical Interview is post-handover but NOT terminal — keep them in-funnel.
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Technical Interview")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    db.commit()
    assert app.pipeline_stage == "applied"  # unchanged — not frozen


def test_phone_screen_does_not_advance(db, monkeypatch):
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Phone Screen")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    assert app.pipeline_stage == "applied"


def test_mid_interview_keeps_reject_card(db, monkeypatch):
    # A reject card on someone in a live interview is Taali's honest HITL
    # second opinion — it stays live (approve surfaces warn the recruiter;
    # nothing auto-executes it). Verdict-flip staleness is the cohort tick's
    # ``_reconcile_stale_pending`` to manage, not the sync reflection's.
    _pin_verdict(monkeypatch, "advance")
    org, role, app = _seed(db, workable_stage="Final Interview")
    _pending_reject(db, org=org, role=role, app=app)
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    db.commit()
    assert app.pipeline_stage != "advanced"
    assert _has_pending(db, app) is True  # reject card preserved


def test_mid_interview_keeps_legitimate_pending(db, monkeypatch):
    # A non-reject card (advance / send_assessment) is the agent legitimately
    # acting on a still-live candidate — must NOT be discarded.
    _pin_verdict(monkeypatch, "advance")
    org, role, app = _seed(db, workable_stage="Technical Interview")
    _pending(db, org=org, role=role, app=app, decision_type="send_assessment")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    db.commit()
    assert _has_pending(db, app) is True  # send_assessment card preserved


def test_mid_interview_heals_agent_stranded_review(db, monkeypatch):
    # A candidate an earlier agent reject second-opinion pulled advanced→review
    # (source='agent'), whose reject card was then discarded because they're in a
    # live interview, is STRANDED in 'review' looking decision-pending. The
    # reconcile heals them back to 'advanced' — honest: being interviewed = handed
    # off — rather than leaving them parked in review with no card.
    _pin_verdict(monkeypatch, None)
    _org, role, app = _seed(db, workable_stage="Final Interview", pipeline_stage="review")
    app.pipeline_stage_source = "agent"
    db.commit()
    assert reconcile_post_handover_advanced(db, app=app, role=role) is True
    db.commit()
    assert app.pipeline_stage == "advanced"


def test_mid_interview_never_heals_under_live_reject_card(db, monkeypatch):
    # While a reject card is still PENDING, the heal must not advance the
    # candidate — advancing under a live reject card would contradict it.
    _pin_verdict(monkeypatch, None)
    org, role, app = _seed(db, workable_stage="Final Interview", pipeline_stage="review")
    app.pipeline_stage_source = "agent"
    db.commit()
    _pending_reject(db, org=org, role=role, app=app)
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    db.commit()
    assert app.pipeline_stage == "review"  # untouched while the card is live
    assert _has_pending(db, app) is True


def test_mid_interview_does_not_heal_legit_system_review(db, monkeypatch):
    # A genuine assessment-completion review (source='system') on a candidate also
    # being interviewed in Workable is a REAL pending decision — never auto-heal it.
    _pin_verdict(monkeypatch, None)
    _org, role, app = _seed(db, workable_stage="Final Interview", pipeline_stage="review")
    app.pipeline_stage_source = "system"
    db.commit()
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    db.commit()
    assert app.pipeline_stage == "review"  # untouched


# --- Verdict / guard cases (unchanged behaviour) -----------------------------

def test_reject_verdict_is_not_advanced(db, monkeypatch):
    # decide_post_handover owns the un-advance + reject-queue; the reconcile must
    # NOT advance a candidate Taali would reject.
    _pin_verdict(monkeypatch, "reject")
    _org, role, app = _seed(db, workable_stage="Offer")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    assert app.pipeline_stage != "advanced"


def test_noop_when_not_post_handover(db, monkeypatch):
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Applied")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False
    assert app.pipeline_stage == "applied"


def test_noop_when_resolved(db, monkeypatch):
    # A6: a rejected/hired candidate is frozen — don't re-advance.
    _pin_verdict(monkeypatch, "advance")
    _org, role, app = _seed(db, workable_stage="Offer", outcome="rejected")
    assert reconcile_post_handover_advanced(db, app=app, role=role) is False


def test_enqueue_score_skips_disqualified(db, monkeypatch):
    from app.services import cv_score_orchestrator as O

    _org, _role, app = _seed(db, workable_stage="Applied")
    app.cv_text = "some real cv text"
    app.workable_disqualified = True
    db.add(app); db.commit()
    # Pass the API-key guard so we reach the disqualified guard specifically.
    monkeypatch.setattr(O.settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    assert O.enqueue_score(db, app) is None  # skipped: workable-disqualified
