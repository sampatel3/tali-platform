"""Regression tests for Claude outage pause/retry behavior."""

from datetime import datetime, timezone

from app.models.assessment import Assessment, AssessmentStatus
from tests.conftest import auth_headers, create_assessment_via_api, create_task_via_api


def _assessment_token_headers(token: str) -> dict:
    return {"X-Assessment-Token": token}


def test_claude_failure_pauses_timer_and_locks_actions(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Pause flow task").json()
    created = create_assessment_via_api(
        client,
        headers,
        task_id=task["id"],
        candidate_email="pause-flow@example.com",
        candidate_name="Pause Flow",
    )
    assert created.status_code == 201, created.text
    assessment_payload = created.json()

    assessment = db.query(Assessment).filter(Assessment.id == assessment_payload["id"]).first()
    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = datetime.now(timezone.utc)
    db.commit()

    def _failed_chat(self, messages, system=None):
        return {
            "success": False,
            "content": "",
            "tokens_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    monkeypatch.setattr("app.api.v1.assessments.ClaudeService.chat", _failed_chat)

    claude_resp = client.post(
        f"/api/v1/assessments/{assessment.id}/claude",
        json={"message": "help please", "conversation_history": []},
        headers=_assessment_token_headers(assessment_payload["token"]),
    )
    assert claude_resp.status_code == 200, claude_resp.text
    paused_payload = claude_resp.json()
    assert paused_payload["success"] is False
    assert paused_payload["is_timer_paused"] is True
    assert paused_payload["pause_reason"] == "claude_outage"

    locked_execute = client.post(
        f"/api/v1/assessments/{assessment.id}/execute",
        json={"code": "print('hello')"},
        headers=_assessment_token_headers(assessment_payload["token"]),
    )
    assert locked_execute.status_code == 423
    execute_detail = locked_execute.json()["detail"]
    assert execute_detail["code"] == "ASSESSMENT_PAUSED"

    locked_submit = client.post(
        f"/api/v1/assessments/{assessment.id}/submit",
        json={"final_code": "print('hello')", "tab_switch_count": 0},
        headers=_assessment_token_headers(assessment_payload["token"]),
    )
    assert locked_submit.status_code == 423
    submit_detail = locked_submit.json()["detail"]
    assert submit_detail["code"] == "ASSESSMENT_PAUSED"


def test_claude_retry_resumes_timer(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Retry flow task").json()
    created = create_assessment_via_api(
        client,
        headers,
        task_id=task["id"],
        candidate_email="retry-flow@example.com",
        candidate_name="Retry Flow",
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

    def _healthy_chat(self, messages, system=None):
        return {
            "success": True,
            "content": "OK",
            "tokens_used": 3,
            "input_tokens": 1,
            "output_tokens": 2,
        }

    monkeypatch.setattr("app.api.v1.assessments.ClaudeService.chat", _healthy_chat)

    retry_resp = client.post(
        f"/api/v1/assessments/{assessment.id}/claude/retry",
        headers=_assessment_token_headers(payload["token"]),
    )
    assert retry_resp.status_code == 200, retry_resp.text
    retry_payload = retry_resp.json()
    assert retry_payload["success"] is True
    assert retry_payload["is_timer_paused"] is False

    db.refresh(assessment)
    assert assessment.is_timer_paused is False
    assert assessment.pause_reason is None
