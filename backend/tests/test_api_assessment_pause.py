"""Tests for assessment pause-state behavior."""

from datetime import datetime, timezone

from app.models.assessment import Assessment, AssessmentStatus
from tests.conftest import auth_headers, create_assessment_via_api, create_task_via_api


def _assessment_token_headers(token: str) -> dict:
    return {"X-Assessment-Token": token}


def test_cv_upload_blocked_while_paused(client, db):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Paused CV upload task").json()
    created = create_assessment_via_api(
        client,
        headers,
        task_id=task["id"],
        candidate_email="paused-cv@example.com",
        candidate_name="Paused CV",
    )
    assert created.status_code == 201
    payload = created.json()

    assessment = db.query(Assessment).filter(Assessment.id == payload["id"]).first()
    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = datetime.now(timezone.utc)
    assessment.is_timer_paused = True
    assessment.pause_reason = "claude_outage"
    assessment.paused_at = datetime.now(timezone.utc)
    db.commit()

    files = {"file": ("resume.pdf", b"%PDF-1.4 paused cv", "application/pdf")}
    resp = client.post(
        f"/api/v1/assessments/token/{payload['token']}/upload-cv",
        files=files,
    )
    assert resp.status_code == 423
    detail = resp.json()["detail"]
    assert detail["code"] == "ASSESSMENT_PAUSED"
