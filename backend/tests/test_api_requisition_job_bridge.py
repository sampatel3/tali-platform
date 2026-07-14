"""Requisition -> Workable job bridge: publish stands up an inactive Taali job.

Publishing a requisition now does three idempotent things beyond minting the
public job page: it mint-once stamps a ``ref_code``, creates an INACTIVE Role
(``job_status=draft``) linked to the brief, and returns an optional
``workable_spec`` (the rendered JD + a ref line) for organizations that also use
Workable. The native role does not depend on that bridge and opens on Turn on.
The brief stays editable.

No Anthropic is needed (publish only touches DB state).
"""
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.role import (
    JOB_STATUS_DRAFT,
    JOB_STATUS_FILLED,
    JOB_STATUS_FILLED_EXTERNAL,
    JOB_STATUS_OPEN,
    Role,
)
from app.models.role_brief import RoleBrief
from app.models.task import Task
from app.services.role_brief_service import find_ref_code
from app.services.task_provisioning_service import (
    MIN_ASSESSMENT_INPUT_CHARS,
    role_assessment_input_text,
)
from tests.conftest import auth_headers


# Publish now enforces the same "all required fields filled" gate the UI does,
# so these bridge tests (which aren't about validation) fill every required
# template field by default. Column-backed fields go at the top level;
# template-only fields (domain / urgency / responsibilities) live in
# custom_fields. Callers override any column field via **fields.
_REQUIRED_COLUMN_FIELDS = {
    "title": "Backend Engineer",
    "seniority": "senior",
    "summary": "Build and own the payments API.",
    "workplace_type": "remote",
    "employment_type": "full_time",
    "openings": 1,
    "must_haves": ["Python", "Postgres"],
    "success_profile": "Ships reliable services end-to-end.",
}
_REQUIRED_CUSTOM_FIELDS = {
    "domain": "Fintech",
    "urgency": "high",
    "responsibilities": ["Design APIs", "On-call rotation"],
}


def _make_requisition(client, headers, **fields):
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    payload = {**_REQUIRED_COLUMN_FIELDS, **fields, "custom_fields": _REQUIRED_CUSTOM_FIELDS}
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}", json=payload, headers=headers
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


def test_publish_persists_enough_structured_context_for_one_click_assessment(
    client, db
):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Structured Role")

    body = _publish(client, headers, brief_id, jd="# Structured Role")
    role = db.query(Role).filter(Role.id == body["role_id"]).one()

    assert len(role_assessment_input_text(role)) >= MIN_ASSESSMENT_INPUT_CHARS


def test_publish_rejects_blank_jd_instead_of_creating_unscoreable_role(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="No Blank JD")
    response = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "   "},
        headers=headers,
    )
    assert response.status_code == 422
    assert "required" in response.text.lower()


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


def test_turning_on_native_requisition_opens_job_without_workable(client):
    headers, _ = auth_headers(client)
    role_id = _publish(
        client,
        headers,
        _make_requisition(client, headers, title="Native Agent Job"),
        jd="# Native Agent Job\n\nBuild reliable systems and own delivery.",
    )["role_id"]

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as kick,
    ):
        response = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["job_status"] == JOB_STATUS_OPEN
    assert response.json()["workable_job_id"] is None
    assert response.json()["auto_promote"] is True
    kick.assert_called_once_with(role_id, activation=True)


def test_native_activation_dispatch_failure_restores_draft_contract(client):
    headers, _ = auth_headers(client)
    role_id = _publish(
        client,
        headers,
        _make_requisition(client, headers, title="Native Broker Failure"),
    )["role_id"]

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch(
            "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
            side_effect=RuntimeError("broker down"),
        ),
    ):
        response = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    assert response.status_code == 503, response.text
    restored = client.get(f"/api/v1/roles/{role_id}", headers=headers).json()
    assert restored["agentic_mode_enabled"] is False
    assert restored["job_status"] == JOB_STATUS_DRAFT
    # The role remains safely OFF, while its pre-activation platform policy is
    # preserved for the next Turn-on retry.
    assert restored["auto_promote"] is True
    assert restored["agent_effective_policy"]["auto_advance"] is True
    assert restored["starred_for_auto_sync"] is False
    assert restored["agent_bootstrap_status"] == "failed"


def test_production_native_activation_fails_when_public_apply_is_disabled(client):
    headers, _ = auth_headers(client)
    role_id = _publish(
        client,
        headers,
        _make_requisition(client, headers, title="Closed Native Ingress"),
    )["role_id"]

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": True, "reason": None},
        ),
        patch("app.services.agent_activation_readiness.settings.ATS_PUBLIC_APPLY_ENABLED", False),
        patch("app.services.agent_activation_readiness.settings.ANTHROPIC_API_KEY", "live-key"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    assert response.status_code == 503
    assert "native_apply_disabled" in response.text
    assert client.get(f"/api/v1/roles/{role_id}", headers=headers).json()[
        "agentic_mode_enabled"
    ] is False


def test_production_activation_requires_task_approval_or_explicit_skip(client):
    headers, _ = auth_headers(client)
    role_id = _publish(
        client,
        headers,
        _make_requisition(client, headers, title="Assessment Choice Required"),
    )["role_id"]

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": True, "reason": None},
        ),
        patch("app.services.agent_activation_readiness.settings.ATS_PUBLIC_APPLY_ENABLED", True),
        patch("app.services.agent_activation_readiness.settings.USAGE_METER_LIVE", True),
        patch("app.services.agent_activation_readiness.settings.ANTHROPIC_API_KEY", "live-key"),
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as kick,
    ):
        blocked = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
        activated = client.patch(
            f"/api/v1/roles/{role_id}",
            json={
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "auto_skip_assessment": True,
            },
            headers=headers,
        )

    assert blocked.status_code == 503
    assert "assessment_task_approval_required" in blocked.text
    assert activated.status_code == 200, activated.text
    assert activated.json()["agentic_mode_enabled"] is True
    assert activated.json()["auto_skip_assessment"] is True
    kick.assert_called_once_with(role_id, activation=True)


def test_publish_materializes_spend_deferred_agent_ready_role(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(
        client,
        headers,
        title="Platform Engineer",
        department="Engineering",
        location_city="Dubai",
        location_country="UAE",
        workplace_type="hybrid",
        employment_type="full_time",
        salary_min=240000,
        salary_max=300000,
        salary_currency="AED",
        salary_period="year",
    )
    jd = "# Platform Engineer\n\nOwn reliability, delivery, and the platform roadmap."
    with patch(
        "app.tasks.automation_tasks.regenerate_role_tech_questions.apply_async"
    ) as tech_generation:
        body = _publish(client, headers, brief_id, jd=jd)

    role = db.query(Role).filter(Role.id == body["role_id"]).one()
    assert role.job_spec_text == jd
    assert role.department == "Engineering"
    assert role.location_city == "Dubai"
    assert role.location_country == "UAE"
    assert role.workplace_type == "hybrid"
    assert role.employment_type == "full_time"
    assert role.salary_min == 240000
    assert role.salary_max == 300000
    assert role.salary_currency == "AED"
    assert role.salary_period == "year"
    assert role.assessment_task_provisioning["status"] == "awaiting_activation"
    assert role.assessment_task_provisioning["reason"] == "requisition_publish"
    tech_generation.assert_not_called()


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


def test_republish_supersedes_stale_generated_draft_and_requests_fresh_one(
    client, db
):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Platform Engineer")
    first = _publish(
        client,
        headers,
        brief_id,
        jd="# Platform Engineer\n\n" + "Own the original platform scope. " * 8,
    )

    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    stale = Task(
        organization_id=role.organization_id,
        name="Old generated draft",
        task_key="old_generated_draft",
        is_template=False,
        is_active=False,
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test_provisioning": {
                "status": "running",
                "claim_token": "old-worker",
            },
        },
    )
    role.tasks.append(stale)
    db.commit()
    stale_id = int(stale.id)

    second = _publish(
        client,
        headers,
        brief_id,
        jd="# Platform Engineer\n\n" + "Own a materially revised platform scope. " * 8,
    )

    assert second["role_id"] == first["role_id"]
    db.expire_all()
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    archived = db.query(Task).filter(Task.id == stale_id).one()
    assert all(task.id != stale_id for task in role.tasks)
    assert archived.is_active is False
    assert archived.extra_data["superseded"] is True
    assert archived.extra_data["needs_review"] is False
    assert role.assessment_task_provisioning["status"] == "awaiting_activation"
    assert role.assessment_task_provisioning["superseded_task_ids"] == [stale_id]


def _make_running_generated_requisition(db, *, role: Role) -> Task:
    task = Task(
        organization_id=role.organization_id,
        name="Prior generated assessment",
        task_key=f"prior_generated_{role.id}",
        is_template=False,
        is_active=True,
        repo_structure={"name": "prior", "files": {"README.md": "Prior JD"}},
        extra_data={
            "generated": True,
            "needs_review": False,
            "approved_by_user_id": 1,
            "battle_test": {"verdict": "pass"},
            "battle_test_provisioning": {"status": "succeeded"},
        },
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    role.agentic_mode_enabled = True
    role.auto_promote = True
    role.starred_for_auto_sync = True
    role.monthly_usd_budget_cents = 7500
    role.job_status = JOB_STATUS_OPEN
    role.assessment_task_provisioning = {
        "status": "succeeded",
        "task_id": int(task.id),
        "activation_intent": {
            "command": "approve_when_ready",
            "status": "succeeded",
            "request_id": "prior-activation",
            "task_id": int(task.id),
            "monthly_usd_budget_cents": 7500,
            "auto_promote": True,
            "requested_by_user_id": 1,
        },
    }
    db.commit()
    return task


def test_changed_republish_of_running_generated_role_reconfigures_and_reactivates(
    client, db
):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Durable Reconfigure")
    original_jd = "# Durable Reconfigure\n\n" + "Own the original scope. " * 10
    first = _publish(client, headers, brief_id, jd=original_jd)
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    old_task = _make_running_generated_requisition(db, role=role)
    old_task_id = int(old_task.id)

    with patch(
        "app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"
    ) as generation:
        second = _publish(
            client,
            headers,
            brief_id,
            jd="# Durable Reconfigure\n\n" + "Own the revised scope. " * 10,
        )

    assert second["role_id"] == first["role_id"]
    assert second["job_status"] == JOB_STATUS_DRAFT
    db.expire_all()
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    archived = db.query(Task).filter(Task.id == old_task_id).one()
    assert role.agentic_mode_enabled is False
    assert role.starred_for_auto_sync is False
    assert role.auto_promote is True
    assert role.monthly_usd_budget_cents == 7500
    assert all(task.id != old_task_id for task in role.tasks)
    assert archived.is_active is False
    assert archived.extra_data["superseded"] is True
    state = role.assessment_task_provisioning
    assert state["status"] == "pending"
    assert state["superseded_task_ids"] == [old_task_id]
    assert state["activation_intent"]["status"] == "pending"
    assert state["activation_intent"]["request_id"] != "prior-activation"
    assert state["activation_intent"]["monthly_usd_budget_cents"] == 7500
    assert state["activation_intent"]["auto_promote"] is True
    assert state["reconfiguration"]["status"] == "pending"
    assert state["reconfiguration"]["superseded_task_id"] == old_task_id
    generation.assert_called_once_with(int(role.id), int(role.organization_id))


def test_identical_republish_keeps_running_agent_and_generated_task(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Idempotent Running")
    jd = "# Idempotent Running\n\n" + "Keep this material scope. " * 10
    first = _publish(client, headers, brief_id, jd=jd)
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    task = _make_running_generated_requisition(db, role=role)

    with patch(
        "app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"
    ) as generation:
        second = _publish(client, headers, brief_id, jd=jd)

    assert second["job_status"] == JOB_STATUS_OPEN
    db.expire_all()
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    persisted_task = db.query(Task).filter(Task.id == task.id).one()
    assert role.agentic_mode_enabled is True
    assert role.starred_for_auto_sync is True
    assert persisted_task.is_active is True
    assert any(linked.id == task.id for linked in role.tasks)
    assert role.assessment_task_provisioning["activation_intent"]["status"] == "succeeded"
    generation.assert_not_called()


def test_changed_republish_preserves_manual_task_but_blocks_for_hitl(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Manual Task Role")
    first = _publish(
        client,
        headers,
        brief_id,
        jd="# Manual Task Role\n\n" + "Original manual assessment scope. " * 8,
    )
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    manual = Task(
        organization_id=role.organization_id,
        name="Recruiter-authored exercise",
        task_key=f"manual_{role.id}",
        is_active=True,
        extra_data={"generated": False},
    )
    role.tasks.append(manual)
    role.agentic_mode_enabled = True
    role.auto_promote = True
    role.starred_for_auto_sync = True
    role.monthly_usd_budget_cents = 6000
    role.job_status = JOB_STATUS_OPEN
    provisioning = dict(role.assessment_task_provisioning or {})
    provisioning.update(
        {"status": "running", "claim_token": "old-generation-claim"}
    )
    role.assessment_task_provisioning = provisioning
    db.commit()
    manual_id = int(manual.id)

    with patch(
        "app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"
    ) as generation:
        _publish(
            client,
            headers,
            brief_id,
            jd="# Manual Task Role\n\n" + "Materially revised manual scope. " * 8,
        )

    db.expire_all()
    role = db.query(Role).filter(Role.id == first["role_id"]).one()
    preserved = db.query(Task).filter(Task.id == manual_id).one()
    assert role.agentic_mode_enabled is False
    assert role.job_status == JOB_STATUS_DRAFT
    assert role.starred_for_auto_sync is False
    assert preserved.is_active is True
    assert any(task.id == manual_id for task in role.tasks)
    intent = role.assessment_task_provisioning["activation_intent"]
    assert intent["status"] == "blocked"
    assert intent["command"] == "review_republished_task"
    assert "manual or their automatic provenance is ambiguous" in intent["last_error"]
    assert role.assessment_task_provisioning["reconfiguration"]["status"] == "blocked"
    assert role.assessment_task_provisioning["claim_token"] is None
    generation.assert_not_called()

    # The preserved choice is resolvable: a subsequent explicit Turn on is the
    # necessary HITL confirmation and hands the active manual task to the
    # durable activation worker (no regeneration and no endless wait).
    with patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as activation:
        confirmed = client.patch(
            f"/api/v1/roles/{role.id}",
            json={
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 6000,
                "auto_promote": True,
                "activation_assessment_action": "approve_when_ready",
            },
            headers=headers,
        )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_state = confirmed.json()["assessment_task_provisioning"]
    assert confirmed_state["activation_intent"]["status"] == "pending"
    assert confirmed_state["activation_intent"]["task_id"] == manual_id
    assert confirmed_state["reconfiguration"]["status"] == "pending"
    assert confirmed_state["reconfiguration"]["resolution"] == (
        "preserved_task_confirmed_by_user"
    )
    activation.assert_called_once()


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


def test_set_job_status_open_cannot_bypass_agent_activation(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    role_id = _publish(client, headers, brief_id)["role_id"]

    blocked = client.post(
        f"/api/v1/roles/{role_id}/job-status",
        json={"status": "open"},
        headers=headers,
    )

    assert blocked.status_code == 409
    assert "turn on" in blocked.text.lower()
    db.expire_all()
    assert db.query(Role).filter(Role.id == role_id).one().job_status == JOB_STATUS_DRAFT


def test_set_job_status_open_keeps_legacy_manual_role_compatibility(client):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles",
        json={"name": "Legacy Manual Role"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]

    closed = client.post(
        f"/api/v1/roles/{role_id}/job-status",
        json={"status": JOB_STATUS_FILLED},
        headers=headers,
    )
    reopened = client.post(
        f"/api/v1/roles/{role_id}/job-status",
        json={"status": JOB_STATUS_OPEN},
        headers=headers,
    )

    assert closed.status_code == 200, closed.text
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["job_status"] == JOB_STATUS_OPEN


def test_set_job_status_reopen_then_fill_for_ready_enabled_agent(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    role_id = _publish(client, headers, brief_id)["role_id"]
    role = db.query(Role).filter(Role.id == role_id).one()
    role.agentic_mode_enabled = True
    db.commit()

    for status in ("open", JOB_STATUS_FILLED, "open", JOB_STATUS_FILLED):
        r = client.post(
            f"/api/v1/roles/{role_id}/job-status", json={"status": status}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["job_status"] == status


def test_set_job_status_open_refuses_paused_agent(client, db):
    headers, _ = auth_headers(client)
    role_id = _publish(
        client, headers, _make_requisition(client, headers, title="Paused Eng")
    )["role_id"]
    role = db.query(Role).filter(Role.id == role_id).one()
    role.agentic_mode_enabled = True
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "monthly cap reached"
    db.commit()

    response = client.post(
        f"/api/v1/roles/{role_id}/job-status",
        json={"status": "open"},
        headers=headers,
    )

    assert response.status_code == 409
    assert "paused" in response.text.lower()


def test_set_job_status_open_rechecks_runtime_readiness(client, db):
    headers, _ = auth_headers(client)
    role_id = _publish(
        client, headers, _make_requisition(client, headers, title="Unready Eng")
    )["role_id"]
    role = db.query(Role).filter(Role.id == role_id).one()
    role.agentic_mode_enabled = True
    db.commit()

    with patch(
        "app.services.agent_activation_readiness.activation_readiness",
        return_value={
            "ready": False,
            "production": True,
            "reasons": [{"code": "worker_unready", "detail": "queue unavailable"}],
        },
    ):
        response = client.post(
            f"/api/v1/roles/{role_id}/job-status",
            json={"status": "open"},
            headers=headers,
        )

    assert response.status_code == 503
    assert "applications remain closed" in response.text.lower()
    db.expire_all()
    assert db.query(Role).filter(Role.id == role_id).one().job_status == JOB_STATUS_DRAFT


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
        json={
            **_REQUIRED_COLUMN_FIELDS,
            "title": title,
            "client_id": client_id,
            "custom_fields": _REQUIRED_CUSTOM_FIELDS,
        },
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
