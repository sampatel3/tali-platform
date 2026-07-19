"""Tests for assessment pause-state behavior."""

from datetime import datetime, timezone

import pytest

from app.domains.assessments_runtime import candidate_runtime_routes
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


def _post_cv(client, payload, route_kind):
    files = {"file": ("resume.pdf", b"%PDF-1.4 lifecycle", "application/pdf")}
    if route_kind == "id":
        return client.post(
            f"/api/v1/assessments/{payload['id']}/upload-cv",
            data={"token": payload["token"]},
            files=files,
        )
    return client.post(
        f"/api/v1/assessments/token/{payload['token']}/upload-cv",
        files=files,
    )


@pytest.mark.parametrize("route_kind", ["id", "token"])
@pytest.mark.parametrize(
    ("assessment_status", "voided"),
    [
        (AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT, False),
        (AssessmentStatus.EXPIRED, False),
        (AssessmentStatus.PENDING, True),
    ],
)
def test_cv_upload_rejects_terminal_or_voided_assessment(
    client, db, monkeypatch, route_kind, assessment_status, voided,
):
    headers, _ = auth_headers(client)
    task = create_task_via_api(
        client,
        headers,
        name=f"CV lifecycle {route_kind} {assessment_status.value} {voided}",
    ).json()
    created = create_assessment_via_api(
        client,
        headers,
        task_id=task["id"],
        candidate_email=(
            f"cv-{route_kind}-{assessment_status.value}-{voided}@example.com"
        ),
    )
    payload = created.json()
    row = db.get(Assessment, payload["id"])
    row.status = assessment_status
    row.is_voided = voided
    db.commit()
    monkeypatch.setattr(
        candidate_runtime_routes,
        "store_cv_upload",
        lambda *_args, **_kwargs: pytest.fail(
            "terminal or voided CV upload must not mutate metadata"
        ),
    )

    response = _post_cv(client, payload, route_kind)

    assert response.status_code == 400, response.text


@pytest.mark.parametrize("route_kind", ["id", "token"])
@pytest.mark.parametrize(
    "assessment_status",
    [AssessmentStatus.PENDING, AssessmentStatus.IN_PROGRESS],
)
def test_cv_upload_preserves_pending_and_in_progress_flow(
    client, db, monkeypatch, route_kind, assessment_status,
):
    headers, _ = auth_headers(client)
    task = create_task_via_api(
        client,
        headers,
        name=f"CV live {route_kind} {assessment_status.value}",
    ).json()
    created = create_assessment_via_api(
        client,
        headers,
        task_id=task["id"],
        candidate_email=f"cv-live-{route_kind}-{assessment_status.value}@example.com",
    )
    payload = created.json()
    row = db.get(Assessment, payload["id"])
    row.status = assessment_status
    row.started_at = (
        datetime.now(timezone.utc)
        if assessment_status == AssessmentStatus.IN_PROGRESS
        else None
    )
    db.commit()
    calls = []
    monkeypatch.setattr(
        candidate_runtime_routes,
        "store_cv_upload",
        lambda assessment, _file, _db: calls.append(int(assessment.id))
        or {"success": True, "assessment_id": int(assessment.id)},
    )

    response = _post_cv(client, payload, route_kind)

    assert response.status_code == 200, response.text
    assert calls == [payload["id"]]
