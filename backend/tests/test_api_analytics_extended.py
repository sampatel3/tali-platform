"""Extended analytics API tests (filters, buckets, benchmarks)."""

from datetime import datetime, timedelta, timezone

from app.models.assessment import Assessment, AssessmentStatus
from tests.conftest import auth_headers, create_assessment_via_api, create_task_via_api


def _mark_completed(
    db,
    assessment_id: int,
    *,
    score_10: float,
    created_at: datetime | None = None,
    task_completion: float = 7.0,
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    assert assessment is not None
    now = datetime.now(timezone.utc)
    assessment.status = AssessmentStatus.COMPLETED
    assessment.started_at = now - timedelta(minutes=35)
    assessment.completed_at = now
    assessment.created_at = created_at or now
    assessment.score = float(score_10)
    assessment.final_score = float(score_10) * 10.0
    assessment.score_breakdown = {
        "category_scores": {
            "task_completion": float(task_completion),
            "prompt_clarity": 6.5,
            "context_provision": 6.0,
            "independence_efficiency": 7.0,
            "response_utilization": 7.0,
            "debugging_design": 6.5,
            "written_communication": 7.5,
            "role_fit": 6.0,
        }
    }
    db.commit()
    db.refresh(assessment)


def test_analytics_filters_score_buckets_and_dimension_averages(client, db):
    headers, _ = auth_headers(client)
    task_a = create_task_via_api(client, headers, name="Analytics Task A").json()
    task_b = create_task_via_api(client, headers, name="Analytics Task B").json()

    assess_a = create_assessment_via_api(client, headers, task_a["id"]).json()
    assess_b = create_assessment_via_api(client, headers, task_b["id"]).json()

    _mark_completed(db, assess_a["id"], score_10=8.0, task_completion=8.0)
    _mark_completed(db, assess_b["id"], score_10=5.0, task_completion=5.0)

    resp = client.get(f"/api/v1/analytics/?task_id={task_a['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["total_assessments"] == 1
    assert payload["completed_count"] == 1
    assert payload["avg_score"] == 8.0
    assert isinstance(payload["score_buckets"], list)
    eighty_bucket = next(item for item in payload["score_buckets"] if item["range"] == "80-100")
    assert eighty_bucket["count"] == 1
    assert payload["dimension_averages"]["task_completion"] == 8.0


def test_benchmarks_endpoint_returns_percentiles_and_candidate_rank(client, db):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Benchmarks Task").json()

    assessment_ids: list[int] = []
    for idx in range(20):
        assessment = create_assessment_via_api(client, headers, task["id"]).json()
        assessment_ids.append(int(assessment["id"]))
        _mark_completed(
            db,
            assessment["id"],
            score_10=5.0 + idx * 0.2,
            task_completion=5.0 + idx * 0.15,
        )

    target_assessment_id = assessment_ids[-1]
    resp = client.get(
        f"/api/v1/analytics/benchmarks?task_id={task['id']}&assessment_id={target_assessment_id}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["task_id"] == task["id"]
    assert payload["available"] is True
    assert payload["sample_size"] == 20
    assert payload["p25"] <= payload["p50"] <= payload["p75"] <= payload["p90"]
    assert "task_completion" in payload["dimension_averages"]
    assert "candidate_percentiles" in payload
    assert payload["candidate_percentiles"]["overall"] >= 90
