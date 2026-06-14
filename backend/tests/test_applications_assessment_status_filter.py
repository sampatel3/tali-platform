"""GET /applications?assessment_status= filter (Home 'Assessment pending' tracker).

Verifies the latest-non-voided-assessment-status filter selects the right
applications and that score_summary carries invite_tracking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from tests.conftest import auth_headers


def _seed(db, org_id, *, email, status, email_status=None):
    role = db.query(Role).filter(Role.organization_id == org_id).first()
    if role is None:
        role = Role(organization_id=org_id, name="Backend", source="manual")
        db.add(role)
        db.flush()
    task = db.query(Task).filter(Task.organization_id == org_id).first()
    if task is None:
        task = Task(name="T", task_key=f"t-{org_id}", organization_id=org_id, is_active=True)
        db.add(task)
        db.flush()
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app_row = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role.id,
        pipeline_stage="invited",
        application_outcome="open",
    )
    db.add(app_row)
    db.flush()
    asmt = Assessment(
        organization_id=org_id,
        candidate_id=cand.id,
        task_id=task.id,
        role_id=role.id,
        application_id=app_row.id,
        token=f"tok-{app_row.id}",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        status=status,
        invite_sent_at=datetime.now(timezone.utc),
        invite_email_status=email_status,
    )
    db.add(asmt)
    db.flush()
    return app_row.id


def test_assessment_status_filter_selects_latest_status(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id

    pending_app = _seed(db, org_id, email="pend@x.test", status=AssessmentStatus.PENDING, email_status="delivered")
    completed_app = _seed(db, org_id, email="done@x.test", status=AssessmentStatus.COMPLETED)
    db.commit()

    # pending filter → only the pending application
    resp = client.get("/api/v1/applications?assessment_status=pending", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert pending_app in ids
    assert completed_app not in ids

    # completed filter → only the completed application
    resp2 = client.get("/api/v1/applications?assessment_status=completed", headers=headers)
    assert resp2.status_code == 200, resp2.text
    ids2 = {item["id"] for item in resp2.json()["items"]}
    assert completed_app in ids2
    assert pending_app not in ids2


def test_invite_tracking_in_payload(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    app_id = _seed(db, org_id, email="track@x.test", status=AssessmentStatus.PENDING, email_status="delivered")
    db.commit()

    resp = client.get("/api/v1/applications?assessment_status=pending", headers=headers)
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["id"] == app_id)
    tracking = item["score_summary"]["invite_tracking"]
    assert tracking is not None
    assert tracking["email_status"] == "delivered"
    assert tracking["invite_sent_at"] is not None
    assert item["score_summary"]["assessment_status"] == "pending"
