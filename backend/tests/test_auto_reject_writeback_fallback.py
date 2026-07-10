"""Auto-reject write-back failure falls back to a Decision Hub card.

When a role runs ``auto_reject=True`` (direct Workable disqualify) but the
disqualify *fails* — e.g. a 403 on a Workable job the configured actor member
can't action (the DeepLight role-53 incident) — the candidate must surface as a
pre-screen-reject *card* rather than strand silently in
``auto_reject_state='failed'``. The card (a pending decision) is also what lets
``pre_screen_reject_sweep``'s ``~has_pending`` guard stop re-attempting the
failing write, turning an endless per-tick 403 retry storm into one attempt.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services import application_automation_service as svc


def _seed_org(db, *, workable=True) -> Organization:
    org = Organization(
        name="O",
        slug=f"o-{uuid.uuid4().hex[:10]}",
        workable_connected=workable,
        workable_access_token="tok" if workable else None,
        workable_subdomain="sub" if workable else None,
    )
    db.add(org)
    db.flush()
    return org


def _seed_role(db, org, *, auto_reject=True, agentic=True, auto_reject_pre_screen=False) -> Role:
    role = Role(
        organization_id=org.id,
        name="Data Engineer",
        source="manual",
        agentic_mode_enabled=agentic,
        auto_reject=auto_reject,
        auto_reject_pre_screen=auto_reject_pre_screen,
        score_threshold=50,
        monthly_usd_budget_cents=0,
        job_spec_text="Requirements\n- Python\n",
    )
    db.add(role)
    db.flush()
    return role


def _seed_app(db, org, role, *, workable_id="wk-1") -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id,
        email=f"c-{uuid.uuid4().hex[:10]}@x.test",
        full_name="C",
    )
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
        workable_candidate_id=workable_id,
        pre_screen_score_100=10,
        pre_screen_recommendation="Below threshold",
        pre_screen_run_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(app)
    db.flush()
    return app


# A below-threshold, should-trigger verdict with a fully-configured Workable
# actor — so run_auto_reject_if_needed reaches the actual disqualify call (the
# branch the fix changes), not an earlier config gate.
_BELOW = {
    "should_trigger": True,
    "state": "eligible",
    "reason": "Below threshold",
    "config": {
        "threshold_100": 50,
        "workable_actor_member_id": "m1",
        "enabled": True,
        "auto_reject_note_template": None,
        "workable_disqualify_reason_id": None,
    },
    "snapshot": {"pre_screen_score": 10, "cv_fit_score": None, "requirements_fit_score": None},
}

_FORBIDDEN = {
    "success": False,
    "message": "Client error '403 Forbidden' for url '.../disqualify'",
    "code": "api_error",
    "action": "disqualify",
}


def _pending_card(db, app):
    return (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == app.id,
            AgentDecision.status == "pending",
        )
        .first()
    )


def test_workable_403_falls_back_to_decision_hub_card(db):
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=True, agentic=True)
    app = _seed_app(db, org, role)

    with patch.object(svc, "evaluate_auto_reject_decision", return_value=dict(_BELOW)), \
         patch.object(svc, "disqualify_candidate_in_workable", return_value=dict(_FORBIDDEN)):
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    # No silent strand: the reject surfaces as a card, not state='failed'.
    assert result["performed"] is False
    assert result["state"] == "awaiting_recruiter_approval"
    assert app.auto_reject_state == "awaiting_recruiter_approval"
    card = _pending_card(db, app)
    assert card is not None
    assert card.decision_type == "skip_assessment_reject"
    # The Workable failure context is still carried for diagnostics.
    assert result["workable_result"]["code"] == "api_error"


def test_workable_403_without_agent_now_cards(db):
    """A pre-screen reject is deterministic and agent-independent, so even on a
    non-agent role a failed Workable disqualify (403) falls back to a Decision
    Hub card instead of stranding in state='failed'."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=True, agentic=False)
    app = _seed_app(db, org, role)

    with patch.object(svc, "evaluate_auto_reject_decision", return_value=dict(_BELOW)), \
         patch.object(svc, "disqualify_candidate_in_workable", return_value=dict(_FORBIDDEN)):
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    assert result["performed"] is False
    assert result["state"] == "awaiting_recruiter_approval"
    assert app.auto_reject_state == "awaiting_recruiter_approval"
    assert _pending_card(db, app) is not None


def test_workable_success_still_disqualifies(db):
    """Regression guard: a successful disqualify rejects the candidate with no
    card and no 'failed' — the fix only changes the failure path."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=True, agentic=True)
    app = _seed_app(db, org, role)

    with patch.object(svc, "evaluate_auto_reject_decision", return_value=dict(_BELOW)), \
         patch.object(
             svc,
             "disqualify_candidate_in_workable",
             return_value={"success": True, "action": "disqualify"},
         ):
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    assert result["performed"] is True
    assert app.application_outcome == "rejected"
    assert _pending_card(db, app) is None


def test_pre_screen_only_toggle_disqualifies_directly(db):
    """auto_reject_pre_screen=True (full auto_reject OFF) still opts this
    pre-screen path into the direct disqualify — it IS the pre-screen stage."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=False, agentic=True, auto_reject_pre_screen=True)
    app = _seed_app(db, org, role)

    with patch.object(svc, "evaluate_auto_reject_decision", return_value=dict(_BELOW)), \
         patch.object(
             svc,
             "disqualify_candidate_in_workable",
             return_value={"success": True, "action": "disqualify"},
         ):
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    assert result["performed"] is True
    assert app.application_outcome == "rejected"
    assert _pending_card(db, app) is None


def test_both_reject_toggles_off_cards_instead(db):
    """Neither auto_reject nor auto_reject_pre_screen → no direct disqualify;
    the below-threshold verdict surfaces as a Decision Hub card."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=False, agentic=True)
    app = _seed_app(db, org, role)

    with patch.object(svc, "evaluate_auto_reject_decision", return_value=dict(_BELOW)), \
         patch.object(svc, "disqualify_candidate_in_workable") as disqualify:
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    disqualify.assert_not_called()
    assert result["performed"] is False
    assert result["state"] == "awaiting_recruiter_approval"
    assert _pending_card(db, app) is not None


def test_post_handover_stage_never_auto_disqualifies(db):
    """HARD RAIL: a below-threshold candidate a recruiter already advanced in
    Workable (e.g. moved to Technical Interview before the application entered
    Taali) must NEVER be auto-disqualified there — even on an
    ``auto_reject=True`` agentic role. The reject surfaces as a HITL card
    instead; the Workable write-back is not attempted."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=True, agentic=True)
    app = _seed_app(db, org, role)
    app.workable_stage = "Technical Interview"
    db.flush()

    verdict = svc.evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict.get("auto_disqualify_eligible") is False

    with patch.object(
        svc, "disqualify_candidate_in_workable"
    ) as disqualify:
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    disqualify.assert_not_called()
    assert result["performed"] is False
    assert app.application_outcome == "open"  # never closed automatically
    card = _pending_card(db, app)
    assert card is not None  # HITL card for the recruiter (warned in the UI)


def test_pre_handover_stage_keeps_auto_disqualify_eligible(db):
    """Control: a neutral pre-handover stage keeps the opt-in write-back path."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org, auto_reject=True, agentic=True)
    app = _seed_app(db, org, role)
    app.workable_stage = "Applied"
    db.flush()

    verdict = svc.evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    assert verdict.get("auto_disqualify_eligible") is True
