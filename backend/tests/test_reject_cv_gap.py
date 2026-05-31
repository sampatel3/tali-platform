"""Reject — CV gap.

Covers the single-candidate reject helper (Workable-gated, mirrors
auto-reject) and the bulk route that rejects a role's CV-gap cohort. Both
the ``missing_cv`` (no file) and ``cv_unreadable`` (file present, no text)
cards can reject — each its own cohort, with a cause-specific reason.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

from app.agent_runtime import data_readiness
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.services import application_automation_service as svc
from tests.conftest import auth_headers

REJECT_URL = "/api/v1/agent-needs-input/{id}/reject-cv-gap"


def _seed_org(db, *, workable=False) -> Organization:
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


def _seed_role(db, org, *, name="Backend") -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
        job_spec_text="Requirements\n- Python\n",
    )
    db.add(role)
    db.flush()
    return role


def _seed_app(
    db,
    org,
    role,
    *,
    cv_text=None,
    cv_file_url=None,
    workable_id=None,
) -> CandidateApplication:
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
        cv_text=cv_text,
        cv_file_url=cv_file_url,
        workable_candidate_id=workable_id,
    )
    db.add(app)
    db.flush()
    return app


def _reget(db, app_id) -> CandidateApplication:
    return db.query(CandidateApplication).filter(CandidateApplication.id == app_id).first()


# ---------------------------------------------------------------------------
# reject_for_cv_gap helper
# ---------------------------------------------------------------------------

def test_reject_for_cv_gap_writes_to_workable_then_rejects(db):
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id="wk-1")

    with patch.object(
        svc,
        "disqualify_candidate_in_workable",
        return_value={"success": True, "action": "disqualify"},
    ) as mock_dq:
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter", actor_id=1
        )

    assert result["performed"] is True
    assert result["workable_written"] is True
    mock_dq.assert_called_once()
    assert app.application_outcome == "rejected"


def test_reject_for_cv_gap_passes_reason_to_workable(db):
    """The caller's reason flows into the Workable disqualify call + result,
    so the unreadable card records 'CV could not be read', not 'no CV'."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id="wk-r")

    with patch.object(
        svc,
        "disqualify_candidate_in_workable",
        return_value={"success": True, "action": "disqualify"},
    ) as mock_dq:
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter",
            actor_id=1, reason="CV could not be read",
        )

    assert result["reason"] == "CV could not be read"
    assert mock_dq.call_args.kwargs["reason"] == "CV could not be read"


def test_reject_for_cv_gap_workable_failure_leaves_candidate_open(db):
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id="wk-2")

    with patch.object(
        svc,
        "disqualify_candidate_in_workable",
        return_value={"success": False, "message": "boom", "code": "api_error"},
    ):
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter", actor_id=1
        )

    # Workable write failed → local outcome stays open (no silent divergence).
    assert result["performed"] is False
    assert "boom" in result["reason"]
    assert app.application_outcome == "open"


def test_reject_for_cv_gap_unlinked_rejects_locally_only(db):
    org = _seed_org(db, workable=False)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id=None)

    with patch.object(svc, "disqualify_candidate_in_workable") as mock_dq:
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter", actor_id=1
        )

    assert result["performed"] is True
    assert result["workable_written"] is False
    mock_dq.assert_not_called()
    assert app.application_outcome == "rejected"


# ---------------------------------------------------------------------------
# POST /agent-needs-input/{id}/reject-cv-gap
# ---------------------------------------------------------------------------

def _org_for_user(db, email) -> Organization:
    user = db.query(User).filter(User.email == email).first()
    return db.query(Organization).filter(Organization.id == user.organization_id).first()


def _open_card(db, role, kind) -> AgentNeedsInput:
    return (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == kind,
            AgentNeedsInput.resolved_at.is_(None),
        )
        .one()
    )


def test_reject_missing_cv_card_rejects_file_less_only(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    a1 = _seed_app(db, org, role)                       # file-less → rejected
    a2 = _seed_app(db, org, role)                       # file-less → rejected
    unreadable = _seed_app(db, org, role, cv_file_url="s3://b/scan.png")  # file present
    has_cv = _seed_app(db, org, role, cv_text="real cv")                 # control
    db.commit()

    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")

    resp = client.post(REJECT_URL.format(id=row.id), headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"] == 2
    assert body["remaining"] == 0
    assert body["failed"] == []

    db.expire_all()
    assert _reget(db, a1.id).application_outcome == "rejected"
    assert _reget(db, a2.id).application_outcome == "rejected"
    # The unreadable + has-CV candidates are untouched by the missing_cv card.
    assert _reget(db, unreadable.id).application_outcome == "open"
    assert _reget(db, has_cv.id).application_outcome == "open"
    # Nothing file-less left → the card auto-resolves.
    assert db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).first().resolved_at is not None


def test_reject_unreadable_card_rejects_unreadable_cohort_only(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    u1 = _seed_app(db, org, role, cv_file_url="s3://b/scan1.png")  # unreadable → rejected
    u2 = _seed_app(db, org, role, cv_file_url="s3://b/scan2.png")  # unreadable → rejected
    file_less = _seed_app(db, org, role)                          # missing_cv cohort
    has_cv = _seed_app(db, org, role, cv_text="real cv")          # control
    db.commit()

    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "cv_unreadable")

    resp = client.post(REJECT_URL.format(id=row.id), headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"] == 2
    assert body["remaining"] == 0

    db.expire_all()
    assert _reget(db, u1.id).application_outcome == "rejected"
    assert _reget(db, u2.id).application_outcome == "rejected"
    # The file-less + has-CV candidates are untouched by the unreadable card.
    assert _reget(db, file_less.id).application_outcome == "open"
    assert _reget(db, has_cv.id).application_outcome == "open"
    # The unreadable card auto-resolves, but the missing_cv card stays open
    # because the file-less candidate is still there.
    assert db.query(AgentNeedsInput).filter(
        AgentNeedsInput.id == row.id
    ).first().resolved_at is not None
    assert data_readiness.missing_cv_count(db, role=role) == 1


def test_reject_cv_gap_rejects_only_cv_gap_kinds(client, db):
    """A non-CV-gap card (e.g. missing_job_spec) can't be rejected here."""
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org, name="No Spec Role")
    db.commit()
    # Raise a missing_job_spec card by clearing the spec.
    role.job_spec_text = ""
    db.add(role)
    db.commit()
    data_readiness.raise_missing_job_spec(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_job_spec")

    resp = client.post(REJECT_URL.format(id=row.id), headers=headers)
    assert resp.status_code == 422, resp.text


def test_reject_cv_gap_404_for_other_org(client, db):
    headers_a, email_a = auth_headers(client, organization_name="OrgA")
    headers_b, email_b = auth_headers(client, organization_name="OrgB")
    org_a = _org_for_user(db, email_a)
    role = _seed_role(db, org_a)
    _seed_app(db, org_a, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")

    # Org B can't see or act on Org A's card.
    resp = client.post(REJECT_URL.format(id=row.id), headers=headers_b)
    assert resp.status_code == 404
