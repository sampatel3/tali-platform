"""Requisition -> Workable job bridge: publish stands up an inactive Taali job.

Publishing a requisition now does three idempotent things beyond minting the
public job page: it mint-once stamps a ``ref_code``, creates an INACTIVE Role
(``job_status=draft``) linked to the brief, and returns a ``workable_spec`` (the
rendered JD + a ref line) for the recruiter to paste into Workable. The brief
stays editable. (Stage 2 adds the import-side match that flips the draft to
``open``.)

No Anthropic is needed (publish only touches DB state).
"""
from app.models.role import (
    JOB_STATUS_DRAFT,
    JOB_STATUS_FILLED,
    JOB_STATUS_FILLED_EXTERNAL,
    Role,
)
from app.models.role_brief import RoleBrief
from app.services.role_brief_service import find_ref_code
from tests.conftest import auth_headers


def _make_requisition(client, headers, **fields):
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    if fields:
        resp = client.patch(
            f"/api/v1/requisitions/{brief_id}", json=fields, headers=headers
        )
        assert resp.status_code == 200, resp.text
    return brief_id


def _publish(client, headers, brief_id, jd="# Eng\n\nBuild things."):
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": jd},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_publish_returns_ref_code_role_and_workable_spec(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Backend Engineer")
    body = _publish(client, headers, brief_id, jd="# Backend Engineer\n\nBuild APIs.")

    assert body["ref_code"].startswith("TAL-")
    assert isinstance(body["role_id"], int)
    assert body["job_status"] == JOB_STATUS_DRAFT
    # The spec the recruiter pastes into Workable carries the JD + the ref line.
    spec = body["workable_spec"]
    assert "Build APIs." in spec
    assert body["ref_code"] in spec
    # Round-trips: the import-side scanner can recover the code from the spec.
    assert find_ref_code(spec) == body["ref_code"]


def test_publish_creates_inactive_role_linked_to_brief(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Data Engineer", summary="ETL")
    body = _publish(client, headers, brief_id)

    role = db.query(Role).filter(Role.id == body["role_id"]).first()
    assert role is not None
    assert role.name == "Data Engineer"
    assert role.source == "requisition"
    assert role.job_status == JOB_STATUS_DRAFT
    assert role.workable_job_id is None  # not yet linked to Workable

    brief = db.query(RoleBrief).filter(RoleBrief.id == brief_id).first()
    assert brief.role_id == role.id
    assert brief.ref_code == body["ref_code"]
    assert brief.status != "applied"  # stays editable


def test_republish_reuses_ref_code_and_role_no_duplicate(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    first = _publish(client, headers, brief_id)
    second = _publish(client, headers, brief_id)

    assert second["ref_code"] == first["ref_code"]
    assert second["role_id"] == first["role_id"]
    # exactly one requisition role for this brief
    roles = db.query(Role).filter(Role.id == first["role_id"]).all()
    assert len(roles) == 1


def test_serializer_job_block_null_before_then_set_after_publish(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")

    before = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert before["job"] is None
    assert before["ref_code"] is None

    pub = _publish(client, headers, brief_id)
    after = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert after["job"]["role_id"] == pub["role_id"]
    assert after["job"]["job_status"] == JOB_STATUS_DRAFT
    assert after["job"]["workable_job_id"] is None
    assert after["ref_code"] == pub["ref_code"]


def test_publish_keeps_brief_editable(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    _publish(client, headers, brief_id)
    edit = client.patch(
        f"/api/v1/requisitions/{brief_id}", json={"title": "Eng II"}, headers=headers
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["title"] == "Eng II"


# --------------------------------------------------------------------------- #
# Stage 3: the role's Job Spec tab is fed the linked requisition's structured spec
# --------------------------------------------------------------------------- #
def test_role_detail_exposes_requisition_spec_and_job_status(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(
        client, headers,
        title="Platform Engineer",
        summary="Own the platform",
        must_haves=["Kubernetes", "Go"],
        preferred=["Terraform"],
        dealbreakers=["No remote"],
        success_profile="Ships reliably, mentors the team.",
    )
    pub = _publish(client, headers, brief_id)

    resp = client.get(f"/api/v1/roles/{pub['role_id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_status"] == JOB_STATUS_DRAFT
    req = body["requisition"]
    assert req is not None
    assert req["ref_code"] == pub["ref_code"]
    assert req["title"] == "Platform Engineer"
    assert req["summary"] == "Own the platform"
    assert "Kubernetes" in [str(x) for x in req["must_haves"]]
    assert "Terraform" in [str(x) for x in req["preferred"]]
    assert "No remote" in [str(x) for x in req["dealbreakers"]]
    assert req["success_profile"] == "Ships reliably, mentors the team."


def test_role_detail_requisition_null_for_plain_role(client):
    headers, _ = auth_headers(client)
    created = client.post("/api/v1/roles", json={"name": "Manual Role"}, headers=headers)
    assert created.status_code in (200, 201), created.text
    role_id = created.json()["id"]

    body = client.get(f"/api/v1/roles/{role_id}", headers=headers).json()
    assert body["requisition"] is None
    assert body["job_status"] is None  # legacy/manual roles have no lifecycle status


# --------------------------------------------------------------------------- #
# Stage 4: job status + fill tracking
# --------------------------------------------------------------------------- #
def test_set_job_status_marks_filled_external(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    pub = _publish(client, headers, brief_id)
    assert pub["job_status"] == JOB_STATUS_DRAFT

    resp = client.post(
        f"/api/v1/roles/{pub['role_id']}/job-status",
        json={"status": JOB_STATUS_FILLED_EXTERNAL, "reason": "placed by an outside agency"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["job_status"] == JOB_STATUS_FILLED_EXTERNAL
    # persisted across a fresh read
    again = client.get(f"/api/v1/roles/{pub['role_id']}", headers=headers).json()
    assert again["job_status"] == JOB_STATUS_FILLED_EXTERNAL


def test_set_job_status_reopen_then_fill(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    role_id = _publish(client, headers, brief_id)["role_id"]
    for status in ("open", JOB_STATUS_FILLED, "open", JOB_STATUS_FILLED):
        r = client.post(
            f"/api/v1/roles/{role_id}/job-status", json={"status": status}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["job_status"] == status


def test_set_job_status_rejects_unknown_status(client):
    headers, _ = auth_headers(client)
    role_id = _publish(client, headers, _make_requisition(client, headers, title="Eng"))["role_id"]
    resp = client.post(
        f"/api/v1/roles/{role_id}/job-status", json={"status": "bogus"}, headers=headers
    )
    assert resp.status_code == 422


def test_set_job_status_unknown_role_404(client):
    headers, _ = auth_headers(client)
    resp = client.post("/api/v1/roles/999999/job-status", json={"status": "open"}, headers=headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Stage 5: client surfaces on the Jobs list + the per-client rollup
# --------------------------------------------------------------------------- #
def _publish_for_client(client, headers, client_id, title):
    bid = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{bid}",
        json={"title": title, "client_id": client_id},
        headers=headers,
    )
    return client.post(
        f"/api/v1/requisitions/{bid}/publish", json={"jd_markdown": "JD"}, headers=headers
    ).json()["role_id"]


def test_roles_list_exposes_client_and_status(client):
    headers, _ = auth_headers(client)
    cid = client.post("/api/v1/clients", json={"name": "Globex"}, headers=headers).json()["id"]
    role_id = _publish_for_client(client, headers, cid, "Eng")

    roles = client.get("/api/v1/roles", headers=headers).json()
    row = next(r for r in roles if r["id"] == role_id)
    assert row["client_id"] == cid
    assert row["client_name"] == "Globex"
    assert row["job_status"] == JOB_STATUS_DRAFT


def test_client_rollup_reflects_role_statuses(client):
    headers, _ = auth_headers(client)
    cid = client.post("/api/v1/clients", json={"name": "Acme"}, headers=headers).json()["id"]
    role_ids = [_publish_for_client(client, headers, cid, f"Role {i}") for i in range(3)]

    # all three start as draft -> active
    roll = client.get(f"/api/v1/clients/{cid}", headers=headers).json()["job_rollup"]
    assert roll["draft"] == 3 and roll["active"] == 3 and roll["total"] == 3

    client.post(f"/api/v1/roles/{role_ids[0]}/job-status", json={"status": JOB_STATUS_FILLED}, headers=headers)
    client.post(
        f"/api/v1/roles/{role_ids[1]}/job-status",
        json={"status": JOB_STATUS_FILLED_EXTERNAL},
        headers=headers,
    )

    roll = client.get(f"/api/v1/clients/{cid}", headers=headers).json()["job_rollup"]
    assert roll["filled"] == 1
    assert roll["filled_external"] == 1
    assert roll["draft"] == 1
    assert roll["active"] == 1  # only the remaining draft
    assert roll["total"] == 3

    # the clients LIST carries the same rollup
    listed = next(
        c for c in client.get("/api/v1/clients", headers=headers).json() if c["id"] == cid
    )
    assert listed["job_rollup"]["filled"] == 1
    assert listed["job_rollup"]["total"] == 3


def test_client_rollup_empty_for_client_with_no_roles(client):
    headers, _ = auth_headers(client)
    cid = client.post("/api/v1/clients", json={"name": "Empty Co"}, headers=headers).json()["id"]
    roll = client.get(f"/api/v1/clients/{cid}", headers=headers).json()["job_rollup"]
    assert roll["total"] == 0 and roll["active"] == 0
