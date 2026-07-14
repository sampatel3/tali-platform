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
    other_run = BackgroundJobRun(
        kind="workable_op",
        scope_kind="org",
        scope_id=other_org.id,
        organization_id=other_org.id,
        status="failed",
        counters={},
        error="private failure",
    )
    db.add(other_run)
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
    assert listed.json()["runs"][0]["counters"] == {"op_type": "move_stage"}
    assert "encrypted-internal-payload" not in listed.text

    hidden = client.get(
        f"/api/v1/background-jobs/runs/{other_run.id}",
        headers=headers,
    )
    assert hidden.status_code == 404
    assert "private failure" not in hidden.text
