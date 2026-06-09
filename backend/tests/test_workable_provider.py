"""Tests for the Workable Assessments-Provider add-on.

Covers the grade mapping (Sam's calls: taali_score; a "maybe" is not a pass),
the provider endpoints (auth + Workable-shaped contract), role auto-provision
per Workable job, and the result sweep → outbox → drain push-back.
"""
from app.domains.workable_provider import outbox, service
from app.models.assessment import Assessment
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.models.workable_webhook_outbox import WorkableWebhookOutbox
from app.platform.config import settings
from tests.conftest import auth_headers, create_task_via_api


def _mint_key(client, headers, scopes, name="workable provider"):
    r = client.post(
        "/api/v1/api-keys", json={"name": name, "scopes": scopes}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["secret"]


def _kh(secret):
    return {"Authorization": f"Bearer {secret}"}


def _org_id_for(db, email):
    user = db.query(User).filter(User.email == email).first()
    return user.organization_id


# ---- Grade mapping --------------------------------------------------------
def test_grade_mapping():
    assert service.grade_for_score(92) == "excelled"
    assert service.grade_for_score(85) == "excelled"
    assert service.grade_for_score(84.9) == "passed"
    assert service.grade_for_score(70) == "passed"
    # A "maybe" (LEAN_NO band, < 70) is NOT a pass.
    assert service.grade_for_score(69.9) == "failed"
    assert service.grade_for_score(40) == "failed"
    assert service.grade_for_score(None) == "failed"


# ---- Endpoints ------------------------------------------------------------
def test_list_tests_workable_shape(client):
    headers, _ = auth_headers(client, organization_name="OrgWkbTests")
    task = create_task_via_api(client, headers)
    assert task.status_code == 201, task.text
    secret = _mint_key(client, headers, ["roles:read"])

    r = client.get(
        "/public/v1/integrations/workable/tests", headers=_kh(secret)
    )
    assert r.status_code == 200
    body = r.json()
    assert "tests" in body
    names = {t["name"] for t in body["tests"]}
    assert task.json()["name"] in names


def test_create_assessment_auto_provisions_role_and_enqueues_pending(client, db):
    headers, email = auth_headers(client, organization_name="OrgWkbCreate")
    task = create_task_via_api(client, headers).json()
    secret = _mint_key(client, headers, ["roles:read", "assessments:write"])

    r = client.post(
        "/public/v1/integrations/workable/assessments",
        headers=_kh(secret),
        json={
            "test_id": str(task["id"]),
            "callback_url": "https://acme.workable.com/assessments/8823119",
            "candidate": {
                "first_name": "Lakita",
                "last_name": "Marrero",
                "email": "lakita@example.com",
            },
            "job_shortcode": "GROOV005",
            "job_title": "AI Engineer",
        },
    )
    assert r.status_code == 201, r.text
    assessment_id = int(r.json()["assessment_id"])

    org_id = _org_id_for(db, email)
    # Role auto-provisioned, keyed on the Workable job shortcode.
    role = (
        db.query(Role)
        .filter(Role.organization_id == org_id, Role.workable_job_id == "GROOV005")
        .first()
    )
    assert role is not None
    assert role.source == "workable_marketplace"

    # The assessment stored the callback + a 'pending' callback was enqueued.
    a = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    assert a is not None
    assert a.workable_callback_url == "https://acme.workable.com/assessments/8823119"
    pending = (
        db.query(WorkableWebhookOutbox)
        .filter(WorkableWebhookOutbox.dedup_key == f"wkb-assessment-{assessment_id}-pending")
        .first()
    )
    assert pending is not None
    assert pending.payload == {"status": "pending"}


def test_create_assessment_unknown_test_id(client):
    headers, _ = auth_headers(client, organization_name="OrgWkbUnknown")
    secret = _mint_key(client, headers, ["roles:read", "assessments:write"])
    r = client.post(
        "/public/v1/integrations/workable/assessments",
        headers=_kh(secret),
        json={
            "test_id": "does-not-exist",
            "callback_url": "https://acme.workable.com/cb/1",
            "candidate": {"email": "x@example.com"},
        },
    )
    assert r.status_code == 422
    assert r.json()["status"] == 422


def test_create_assessment_requires_write_scope(client):
    headers, _ = auth_headers(client, organization_name="OrgWkbScope")
    task = create_task_via_api(client, headers).json()
    # roles:read only — no assessments:write.
    secret = _mint_key(client, headers, ["roles:read"])
    r = client.post(
        "/public/v1/integrations/workable/assessments",
        headers=_kh(secret),
        json={
            "test_id": str(task["id"]),
            "callback_url": "https://acme.workable.com/cb/1",
            "candidate": {"email": "x@example.com"},
        },
    )
    assert r.status_code == 403


def test_shared_link(client):
    headers, _ = auth_headers(client, organization_name="OrgWkbLink")
    task = create_task_via_api(client, headers).json()
    secret = _mint_key(client, headers, ["roles:read", "assessments:write"])
    created = client.post(
        "/public/v1/integrations/workable/assessments",
        headers=_kh(secret),
        json={
            "test_id": str(task["id"]),
            "callback_url": "https://acme.workable.com/cb/1",
            "candidate": {"email": "link@example.com"},
            "job_shortcode": "JOB1",
        },
    ).json()
    aid = created["assessment_id"]
    r = client.get(
        f"/public/v1/integrations/workable/assessments/{aid}/shared-link",
        headers=_kh(secret),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].endswith(f"/assessment/{aid}") or f"/assessment/{aid}?" in body["url"]
    assert body["ttl_units"] == "minutes"


# ---- Result sweep → outbox → drain ---------------------------------------
def test_result_sweep_and_drain(client, db, monkeypatch):
    headers, email = auth_headers(client, organization_name="OrgWkbResult")
    task = create_task_via_api(client, headers).json()
    org_id = _org_id_for(db, email)

    # Provision a provider assessment directly through the service.
    assessment = service.provision_assessment(
        db,
        organization_id=org_id,
        test_id=str(task["id"]),
        callback_url="https://acme.workable.com/assessments/55",
        candidate=service.WorkableCandidate(email="cand@example.com", first_name="Cand"),
        job_shortcode="JOBX",
        job_title="Engineer",
    )
    # Simulate scoring completion.
    assessment.scored_at = service._now()
    assessment.taali_score = 80.0
    db.commit()

    # Sweep enqueues a 'completed' callback with the mapped grade + score.
    swept = service.enqueue_completed_results(db)
    assert swept["enqueued"] == 1
    row = (
        db.query(WorkableWebhookOutbox)
        .filter(WorkableWebhookOutbox.dedup_key == f"wkb-assessment-{assessment.id}-completed")
        .first()
    )
    assert row is not None
    assert row.payload["status"] == "completed"
    assert row.payload["assessment"]["grade"] == "passed"  # 80 → passed
    assert row.payload["assessment"]["score"] == "80"
    assert "results_url" in row.payload

    # Idempotent: a second sweep enqueues nothing (pushed marker set).
    assert service.enqueue_completed_results(db)["enqueued"] == 0

    # Drain PUTs to the callback_url (httpx mocked); flag must be on.
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

    def fake_put(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _Resp()

    monkeypatch.setattr(outbox.httpx, "put", fake_put)
    monkeypatch.setattr(settings, "WORKABLE_PROVIDER_ENABLED", True)

    result = outbox.drain(db)
    assert result["status"] == "ok"
    # Two pending rows drain: the 'pending' enqueued at provisioning + the
    # 'completed' from the sweep — Workable expects both, to the same callback.
    assert result["sent"] == 2
    assert len(calls) == 2
    assert all(c["url"] == "https://acme.workable.com/assessments/55" for c in calls)
    assert {c["json"]["status"] for c in calls} == {"pending", "completed"}

    db.refresh(row)
    assert row.status == "sent"


def test_drain_disabled_is_noop(db):
    # Default: WORKABLE_PROVIDER_ENABLED is off → drain does nothing.
    assert settings.WORKABLE_PROVIDER_ENABLED is False
    assert outbox.drain(db)["status"] == "disabled"
