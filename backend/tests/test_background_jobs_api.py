from app.models.background_job_run import BackgroundJobRun
from app.models.organization import Organization
from app.models.user import User
from tests.conftest import auth_headers


def test_background_job_run_detail_is_tracked_and_org_scoped(client, db):
    headers, email = auth_headers(
        client,
        email="background-run@example.com",
        organization_name="Background Run Org",
    )
    user = db.query(User).filter(User.email == email).one()
    own_run = BackgroundJobRun(
        kind="workable_op",
        scope_kind="org",
        scope_id=user.organization_id,
        organization_id=user.organization_id,
        status="queued",
        counters={
            "op_type": "move_stage",
            "recovery_payload": "encrypted-internal-payload",
        },
    )
    other_org = Organization(name="Other Background Run Org")
    db.add_all([own_run, other_org])
    db.flush()
    sensitive_run = BackgroundJobRun(
        kind="scoring_batch",
        scope_kind="org",
        scope_id=user.organization_id,
        organization_id=user.organization_id,
        status="failed",
        counters={
            "errors": ["provider token=secret", "postgresql://private-host"],
            "error_message": "sdk key=private",
            "traceback": "private stack",
        },
        error="redis://user:password@internal:6379 and provider token",
    )
    other_run = BackgroundJobRun(
        kind="workable_op",
        scope_kind="org",
        scope_id=other_org.id,
        organization_id=other_org.id,
        status="failed",
        counters={},
        error="private failure",
    )
    db.add_all([sensitive_run, other_run])
    db.commit()

    response = client.get(
        f"/api/v1/background-jobs/runs/{own_run.id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["counters"] == {"op_type": "move_stage"}
    listed = client.get("/api/v1/background-jobs/runs", headers=headers)
    assert listed.status_code == 200, listed.text
    listed_by_id = {run["id"]: run for run in listed.json()["runs"]}
    assert listed_by_id[own_run.id]["counters"] == {"op_type": "move_stage"}
    public_sensitive = listed_by_id[sensitive_run.id]
    assert public_sensitive["error_code"] == "scoring_batch_failed"
    assert public_sensitive["error"] == (
        "The scoring batch could not complete. Retry the failed candidates."
    )
    assert public_sensitive["counters"] == {"errors": 2}
    assert "encrypted-internal-payload" not in listed.text
    assert "private-host" not in listed.text
    assert "provider token" not in listed.text
    assert "private stack" not in listed.text
    sensitive_detail = client.get(
        f"/api/v1/background-jobs/runs/{sensitive_run.id}", headers=headers
    )
    assert sensitive_detail.status_code == 200
    assert sensitive_detail.json()["error_code"] == "scoring_batch_failed"
    assert "internal" not in sensitive_detail.text

    hidden = client.get(
        f"/api/v1/background-jobs/runs/{other_run.id}",
        headers=headers,
    )
    assert hidden.status_code == 404
    assert "private failure" not in hidden.text
