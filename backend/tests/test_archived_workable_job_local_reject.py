"""Archived/closed Workable jobs: reject locally, skip the Workable sync.

Workable refuses candidate disqualifies on archived/closed reqs (403). For a
role whose linked Workable job isn't live, a reject must still resolve the
candidate to 'rejected' in Taali — just without the Workable write-back — so
candidates don't pile up waiting forever (the DeepLight role-53 case). Live
(published) jobs are unaffected: they still disqualify in Workable.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

from app.actions import reject_application
from app.actions.types import Actor
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services import application_automation_service as svc


def _seed_org(db) -> Organization:
    org = Organization(
        name="O",
        slug=f"o-{uuid.uuid4().hex[:10]}",
        workable_connected=True,
        workable_access_token="tok",
        workable_subdomain="sub",
    )
    db.add(org)
    db.flush()
    return org


def _seed_role(db, org, *, job_state, auto_reject=False) -> Role:
    role = Role(
        organization_id=org.id,
        name="Data Engineer",
        source="workable",
        agentic_mode_enabled=True,
        auto_reject=auto_reject,
        score_threshold=50,
        monthly_usd_budget_cents=0,
        job_spec_text="Requirements\n- Python\n",
        workable_job_id="JOB123",
        workable_job_data={"state": job_state},
    )
    db.add(role)
    db.flush()
    return role


def _seed_app(db, org, role) -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id,
        email=f"c-{uuid.uuid4().hex[:8]}@x.test",
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
        workable_candidate_id="wk-1",
        pre_screen_score_100=10,
        pre_screen_recommendation="Below threshold",
    )
    db.add(app)
    db.flush()
    return app


_BELOW = {
    "should_trigger": True,
    "state": "eligible",
    "reason": "Below threshold",
    "config": {"threshold_100": 50, "workable_actor_member_id": "m1", "enabled": True},
    "snapshot": {"pre_screen_score": 10, "cv_fit_score": None, "requirements_fit_score": None},
}


# --- recruiter / card-approval reject path (reject_application.run) -----------

def test_reject_on_archived_job_rejects_locally_without_workable(db):
    org = _seed_org(db)
    role = _seed_role(db, org, job_state="archived")
    app = _seed_app(db, org, role)

    # send_email=True so notify_rejection actually runs (and hits the guard).
    # Patch disqualify at the source module — it's imported inline at call time.
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_dq, patch.object(
        reject_application, "_dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.system(),
            organization_id=org.id,
            application_id=app.id,
            reason="below threshold",
            send_email=True,
        )

    assert app.application_outcome == "rejected"   # local reject stands
    mock_dq.assert_not_called()                     # Workable sync skipped (archived)
    mock_email.assert_called_once()                 # candidate still notified via Taali


def test_reject_on_published_job_still_disqualifies_in_workable(db):
    org = _seed_org(db)
    role = _seed_role(db, org, job_state="published")
    app = _seed_app(db, org, role)

    with patch.object(reject_application.settings, "MVP_DISABLE_WORKABLE", False), \
         patch(
             "app.services.workable_actions_service.disqualify_candidate_in_workable",
             return_value={"success": True, "action": "disqualify", "config": {}},
         ) as mock_dq:
        reject_application.run(
            db,
            Actor.system(),
            organization_id=org.id,
            application_id=app.id,
            reason="below threshold",
            send_email=True,
        )

    assert app.application_outcome == "rejected"
    mock_dq.assert_called_once()                     # live job → Workable attempted


# --- pre-screen auto-reject path (run_auto_reject_if_needed) ------------------

def test_auto_reject_on_archived_job_rejects_locally(db):
    org = _seed_org(db)
    role = _seed_role(db, org, job_state="archived", auto_reject=True)
    app = _seed_app(db, org, role)

    with patch.object(svc, "evaluate_auto_reject_decision", return_value=dict(_BELOW)), \
         patch.object(svc, "disqualify_candidate_in_workable") as mock_dq:
        result = svc.run_auto_reject_if_needed(
            db=db, org=org, app=app, role=role, actor_type="system"
        )

    assert result["performed"] is True
    assert result["state"] == "rejected"
    assert result.get("workable_synced") is False
    assert app.application_outcome == "rejected"
    mock_dq.assert_not_called()                      # archived → no Workable disqualify
