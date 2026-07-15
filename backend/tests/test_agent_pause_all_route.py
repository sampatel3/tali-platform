"""HTTP-layer tests for the agent pause / resume / turn-off contract.

  POST /api/v1/agent/pause-all          apply the workspace pause overlay
  POST /api/v1/agent/resume-all         clear only the workspace pause overlay
  POST /api/v1/roles/{id}/agent/pause   soft-pause one role
  POST /api/v1/roles/{id}/agent/resume  resume one paused role

The defining contract: a pending decision's lifecycle is tied to the
candidate, not the agent's power state. Neither a soft pause NOR turning the
agent fully off (PATCH agentic_mode_enabled=false) empties the review queue —
discarding it is an explicit opt-in via POST /agent-decisions/discard.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role


# SQLite doesn't autoincrement BIGINT PKs — mirror the workaround the other
# agent-decision route tests use.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _seed_org_with_agent_roles(org_name: str, *, role_names: list[str]) -> dict:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = Organization(name=org_name, slug=f"pa-{uuid.uuid4().hex[:12]}")
        sess.add(org)
        sess.flush()
        role_ids = []
        for name in role_names:
            role = Role(
                organization_id=org.id,
                name=name,
                source="manual",
                agentic_mode_enabled=True,
                # Non-zero cap with zero spend → genuinely under budget, so
                # resume-all can clear the pause.
                monthly_usd_budget_cents=5000,
            )
            sess.add(role)
            sess.flush()
            role_ids.append(int(role.id))
        sess.commit()
        return {"org_id": int(org.id), "role_ids": role_ids}
    finally:
        sess.close()


def _seed_pending_decision(org_id: int, role_id: int) -> int:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        cand = Candidate(organization_id=org_id, email=f"c{role_id}@x.test", full_name="Ada Lovelace")
        sess.add(cand)
        sess.flush()
        app_row = CandidateApplication(
            organization_id=org_id,
            candidate_id=cand.id,
            role_id=role_id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
        )
        sess.add(app_row)
        sess.flush()
        decision = AgentDecision(
            organization_id=org_id,
            role_id=role_id,
            application_id=app_row.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="Strong match.",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            idempotency_key=f"pa:{role_id}:advance",
            created_at=datetime.now(timezone.utc),
        )
        sess.add(decision)
        sess.commit()
        return int(decision.id)
    finally:
        sess.close()


def _seed_open_question(org_id: int, role_id: int) -> int:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        question = AgentNeedsInput(
            # SQLite does not auto-increment BIGINT primary keys.
            id=(int(role_id) * 10_000) + 1,
            organization_id=org_id,
            role_id=role_id,
            kind="intent_clarification",
            prompt="Which platform constraint matters most?",
        )
        sess.add(question)
        sess.commit()
        return int(question.id)
    finally:
        sess.close()


def _attach_user_to_org(email: str, organization_id: int) -> None:
    from app.models.user import User as _U
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        user = sess.query(_U).filter(_U.email == email).first()
        assert user is not None
        user.organization_id = organization_id
        sess.commit()
    finally:
        sess.close()


def _role_pause_state(role_id: int) -> tuple:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).first()
        return (role.agent_paused_at, role.agent_paused_reason, role.agentic_mode_enabled)
    finally:
        sess.close()


def _role_version(role_id: int) -> int:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).one()
        return int(role.version)
    finally:
        sess.close()


def _user_id(email: str) -> int:
    from app.models.user import User as _U
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        return int(sess.query(_U.id).filter(_U.email == email).scalar())
    finally:
        sess.close()


def _set_member_role_access(
    email: str,
    *,
    organization_id: int,
    role_id: int,
    team_role: str | None,
) -> None:
    from app.models.user import User as _U
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        user = sess.query(_U).filter(_U.email == email).one()
        user.organization_id = organization_id
        user.role = "member"
        if team_role is not None:
            sess.add(
                JobHiringTeam(
                    organization_id=organization_id,
                    role_id=role_id,
                    user_id=user.id,
                    team_role=team_role,
                )
            )
        sess.commit()
    finally:
        sess.close()


def _workspace_command(client, headers: dict, action: str, *, expected: int | None = None):
    if expected is None:
        status = client.get("/api/v1/agent/org-status", headers=headers)
        assert status.status_code == 200, status.text
        expected = int(status.json()["workspace_control_version"])
    return client.post(
        f"/api/v1/agent/{action}-all",
        json={"expected_control_version": expected},
        headers=headers,
    )


def _workspace_db_state(org_id: int) -> tuple:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = sess.query(Organization).filter(Organization.id == org_id).one()
        return (
            org.agent_workspace_paused_at,
            org.agent_workspace_paused_reason,
            org.agent_workspace_paused_by_user_id,
            int(org.agent_workspace_control_version or 1),
        )
    finally:
        sess.close()


def _decision_status(decision_id: int) -> str:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        return sess.query(AgentDecision).filter(AgentDecision.id == decision_id).first().status
    finally:
        sess.close()


def test_pause_all_soft_pauses_and_keeps_pending_decisions(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Pause Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])
    decision_id = _seed_pending_decision(seeded["org_id"], seeded["role_ids"][0])

    resp = _workspace_command(client, headers, "pause")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 2
    assert body["enabled_count"] == 2
    assert body["workspace_paused"] is True
    assert body["paused_by"]["user_id"] == _user_id(email)
    assert body["paused_by"]["is_current_user"] is True
    assert body["paused_by"]["source"] == "workspace_control"

    for role_id in seeded["role_ids"]:
        paused_at, reason, enabled = _role_pause_state(role_id)
        # Workspace control is an overlay; local role intent is untouched.
        assert paused_at is None
        assert reason is None
        assert enabled is True

    org_paused_at, org_reason, org_actor, org_version = _workspace_db_state(
        seeded["org_id"]
    )
    assert org_paused_at is not None
    assert org_reason == "workspace paused by recruiter"
    assert org_actor == _user_id(email)
    assert org_version == body["workspace_control_version"]

    # The defining contract: the pending decision is untouched.
    assert _decision_status(decision_id) == "pending"


def test_pause_all_is_idempotent_for_already_paused_roles(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Idempotent Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])

    first = _workspace_command(client, headers, "pause")
    assert first.json()["affected"] == 1
    first_body = first.json()
    # An idempotent stale retry remains 200 and cannot replace actor/time.
    second = _workspace_command(client, headers, "pause", expected=1)
    assert second.json()["affected"] == 0
    assert second.json()["enabled_count"] == 1
    assert second.json()["workspace_control_version"] == first_body["workspace_control_version"]
    assert second.json()["paused_at"] == first_body["paused_at"]
    assert second.json()["paused_by"] == first_body["paused_by"]


def test_resume_all_clears_pause_and_wakes_under_budget_roles(client, monkeypatch):
    from tests.conftest import auth_headers

    wakeups: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda role_id, *, activation, **_kwargs: wakeups.append(
            (role_id, activation)
        ),
    )

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Resume Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])

    _workspace_command(client, headers, "pause")
    resp = _workspace_command(client, headers, "resume")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 2
    assert body["skipped"] == 0
    assert body["workspace_paused"] is False
    assert sorted(wakeups) == sorted(
        (role_id, False) for role_id in seeded["role_ids"]
    )

    for role_id in seeded["role_ids"]:
        paused_at, reason, _enabled = _role_pause_state(role_id)
        assert paused_at is None
        assert reason is None


def test_workspace_resume_does_not_rewrite_role_when_worker_rejects_wakeup(
    client, monkeypatch
):
    from tests.conftest import auth_headers

    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles(
        "Resume Dispatch Failure Org", role_names=["A"]
    )
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    _workspace_command(client, headers, "pause")

    resp = _workspace_command(client, headers, "resume")

    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 1
    assert resp.json()["skipped"] == 1
    paused_at, reason, enabled = _role_pause_state(role_id)
    # A transient wake-up failure does not rewrite local desired state. The
    # next scheduler beat can retry this locally-running role.
    assert paused_at is None
    assert reason is None
    assert enabled is True


def test_workspace_resume_clears_overlay_but_skips_unready_dispatch(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Resume Unready Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])
    _workspace_command(client, headers, "pause")

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        resp = _workspace_command(client, headers, "resume")

    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 2
    assert resp.json()["skipped"] == 2
    for role_id in seeded["role_ids"]:
        assert _role_pause_state(role_id)[0] is None


def test_pause_all_is_org_scoped(client):
    """Pausing one org must not touch another org's agents."""
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    mine = _seed_org_with_agent_roles("My Org", role_names=["A"])
    other = _seed_org_with_agent_roles("Other Org", role_names=["X"])
    _attach_user_to_org(email, mine["org_id"])

    _workspace_command(client, headers, "pause")

    # Mine has the workspace overlay, theirs is untouched; neither local role
    # state is rewritten.
    assert _workspace_db_state(mine["org_id"])[0] is not None
    assert _workspace_db_state(other["org_id"])[0] is None
    assert _role_pause_state(mine["role_ids"][0])[0] is None
    assert _role_pause_state(other["role_ids"][0])[0] is None


def test_workspace_status_reports_verified_actor_and_effective_counts(client):
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client, full_name="Workspace Pause Owner")
    seeded = _seed_org_with_agent_roles("Workspace Status Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])
    sess = TestingSessionLocal()
    try:
        locally_paused = sess.query(Role).filter(Role.id == seeded["role_ids"][0]).one()
        locally_paused.agent_paused_at = datetime.now(timezone.utc)
        locally_paused.agent_paused_reason = "paused by recruiter"
        sess.commit()
    finally:
        sess.close()

    paused = _workspace_command(client, headers, "pause")
    assert paused.status_code == 200, paused.text

    status = client.get("/api/v1/agent/org-status", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["workspace_paused"] is True
    assert body["workspace_paused_reason"] == "workspace paused by recruiter"
    assert body["workspace_paused_at"] == paused.json()["paused_at"]
    assert body["workspace_control_version"] == paused.json()["workspace_control_version"]
    assert body["workspace_paused_by"] == paused.json()["paused_by"]
    assert body["workspace_paused_by"]["name"] == "Workspace Pause Owner"
    assert body["workspace_paused_by"]["attribution"] == "verified"
    assert body["workspace_last_change"]["action"] == "paused"
    assert body["workspace_last_change"]["name"] == "Workspace Pause Owner"
    assert body["active_role_count"] == 0
    assert body["paused_role_count"] == 2
    assert body["local_paused_role_count"] == 1


def test_workspace_resume_preserves_preexisting_role_pause(client, monkeypatch):
    from tests.conftest import TestingSessionLocal, auth_headers

    wakeups: list[int] = []
    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda role_id, **_kwargs: wakeups.append(int(role_id)),
    )
    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Workspace Overlay Restore Org", role_names=["Local", "Run"])
    _attach_user_to_org(email, seeded["org_id"])
    local_role_id, running_role_id = seeded["role_ids"]
    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == local_role_id).one()
        role.agent_paused_at = datetime.now(timezone.utc)
        role.agent_paused_reason = "paused by recruiter"
        sess.commit()
    finally:
        sess.close()

    _workspace_command(client, headers, "pause")
    resumed = _workspace_command(client, headers, "resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["enabled_count"] == 2
    assert _role_pause_state(local_role_id)[0] is not None
    assert _role_pause_state(running_role_id)[0] is None
    assert wakeups == [running_role_id]


def test_stale_workspace_resume_conflicts_with_current_pause_actor(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client, full_name="Current Pause Owner")
    seeded = _seed_org_with_agent_roles("Workspace Conflict Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])

    paused = _workspace_command(client, headers, "pause", expected=1)
    assert paused.status_code == 200, paused.text
    conflict = _workspace_command(client, headers, "resume", expected=1)
    assert conflict.status_code == 409, conflict.text
    current = conflict.json()["detail"]["current"]
    assert current["workspace_paused"] is True
    assert current["workspace_control_version"] == 2
    assert current["paused_by"]["name"] == "Current Pause Owner"
    assert current["paused_by"]["is_current_user"] is True
    assert current["changed_by"]["action"] == "paused"
    assert current["changed_by"]["name"] == "Current Pause Owner"


def test_stale_workspace_pause_after_resume_reports_resumer(client, monkeypatch):
    from tests.conftest import auth_headers

    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *_args, **_kwargs: None,
    )
    headers, email = auth_headers(client, full_name="Workspace Resumer")
    seeded = _seed_org_with_agent_roles("Workspace Resume Conflict Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    _workspace_command(client, headers, "pause", expected=1)
    resumed = _workspace_command(client, headers, "resume", expected=2)
    assert resumed.status_code == 200, resumed.text

    conflict = _workspace_command(client, headers, "pause", expected=2)
    assert conflict.status_code == 409, conflict.text
    current = conflict.json()["detail"]["current"]
    assert current["workspace_paused"] is False
    assert current["paused_by"] is None
    assert current["changed_by"]["action"] == "resumed"
    assert current["changed_by"]["name"] == "Workspace Resumer"


def test_stale_same_target_workspace_resume_is_idempotent(client, monkeypatch):
    from tests.conftest import auth_headers

    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *_args, **_kwargs: None,
    )
    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Workspace Resume Retry Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])

    _workspace_command(client, headers, "pause", expected=1)
    first = _workspace_command(client, headers, "resume", expected=2)
    assert first.status_code == 200, first.text
    retry = _workspace_command(client, headers, "resume", expected=2)
    assert retry.status_code == 200, retry.text
    assert retry.json()["affected"] == 0
    assert retry.json()["workspace_control_version"] == 3


def test_workspace_controls_append_actor_snapshotted_audit_events(client, monkeypatch):
    from app.models.workspace_agent_control_event import WorkspaceAgentControlEvent
    from tests.conftest import TestingSessionLocal, auth_headers

    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *_args, **_kwargs: None,
    )
    headers, email = auth_headers(client, full_name="Audit Owner")
    seeded = _seed_org_with_agent_roles("Workspace Audit Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    _workspace_command(client, headers, "pause", expected=1)
    _workspace_command(client, headers, "resume", expected=2)

    sess = TestingSessionLocal()
    try:
        events = (
            sess.query(WorkspaceAgentControlEvent)
            .filter(WorkspaceAgentControlEvent.organization_id == seeded["org_id"])
            .order_by(WorkspaceAgentControlEvent.id)
            .all()
        )
        assert [event.action for event in events] == ["paused", "resumed"]
        assert [(event.from_version, event.to_version) for event in events] == [
            (1, 2),
            (2, 3),
        ]
        assert all(event.actor_user_id == _user_id(email) for event in events)
        assert all(event.actor_name == "Audit Owner" for event in events)
        assert all(event.request_id for event in events)
    finally:
        sess.close()


def test_role_status_workspace_pause_precedes_local_pause(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client, full_name="Workspace Owner")
    seeded = _seed_org_with_agent_roles("Workspace Role Status Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    local = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert local.status_code == 200, local.text
    workspace = _workspace_command(client, headers, "pause")
    assert workspace.status_code == 200, workspace.text

    status = client.get(f"/api/v1/roles/{role_id}/agent/status", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["paused"] is True
    assert body["pause_scope"] == "workspace"
    assert body["paused_at"] == body["workspace_paused_at"]
    assert body["paused_by"]["source"] == "workspace_control"
    assert body["role_paused_at"] is not None
    assert body["role_paused_reason"] == "paused by recruiter"
    assert body["role_paused_by"]["source"] == "role_change_event"


def test_role_resume_changes_local_intent_but_stays_effectively_workspace_paused(
    client, monkeypatch
):
    from tests.conftest import auth_headers

    wakeups: list[int] = []
    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda role_id, **_kwargs: wakeups.append(int(role_id)),
    )
    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Workspace Held Role Resume Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    local_pause = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert local_pause.status_code == 200, local_pause.text
    _workspace_command(client, headers, "pause")

    resumed = client.post(
        f"/api/v1/roles/{role_id}/agent/resume",
        json={"expected_version": local_pause.json()["version"]},
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["resumed"] is True
    assert resumed.json()["paused"] is True
    assert resumed.json()["pause_scope"] == "workspace"
    assert resumed.json()["reason"] == "workspace paused by recruiter"
    assert _role_pause_state(role_id)[0] is None
    assert wakeups == []


def test_workspace_overlay_does_not_bypass_role_resume_readiness(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Workspace Held Unready Resume Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    local_pause = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert local_pause.status_code == 200, local_pause.text
    _workspace_command(client, headers, "pause")

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        resumed = client.post(
            f"/api/v1/roles/{role_id}/agent/resume",
            json={"expected_version": local_pause.json()["version"]},
            headers=headers,
        )

    assert resumed.status_code == 503, resumed.text
    assert "Agent runtime is not ready" in resumed.json()["detail"]
    # The workspace overlay is not used as permission to clear a local hold,
    # and the explicit control reports the readiness problem instead of
    # optimistically returning a misleading successful resume.
    assert _role_pause_state(role_id)[0] is not None


# ---------------------------------------------------------------------------
# Per-role pause / resume (the per-role twins of pause-all / resume-all) and
# the turn-off decoupling: neither pause NOR off discards the review queue.
#   POST /api/v1/roles/{id}/agent/pause
#   POST /api/v1/roles/{id}/agent/resume
# ---------------------------------------------------------------------------


def test_pause_one_role_keeps_pending_decisions(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Pause Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    decision_id = _seed_pending_decision(seeded["org_id"], role_id)

    resp = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paused"] is True
    assert body["reason"] == "paused by recruiter"

    paused_at, reason, enabled = _role_pause_state(role_id)
    assert paused_at is not None
    assert reason == "paused by recruiter"
    assert enabled is True  # still enabled — that's what keeps the queue alive
    assert _decision_status(decision_id) == "pending"


def test_agent_status_exposes_canonical_viewer_control_capability(client):
    from tests.conftest import auth_headers

    owner_headers, owner_email = auth_headers(
        client,
        full_name="Capability Owner",
        organization_name="Capability Source Org",
    )
    seeded = _seed_org_with_agent_roles(
        "Agent Status Capability Org", role_names=["A"]
    )
    _attach_user_to_org(owner_email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    owner_status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=owner_headers
    )
    assert owner_status.status_code == 200, owner_status.text
    assert owner_status.json()["can_control_agent"] is True

    viewer_headers, viewer_email = auth_headers(
        client,
        full_name="Role Viewer",
        organization_name="Capability Viewer Org",
    )
    _set_member_role_access(
        viewer_email,
        organization_id=seeded["org_id"],
        role_id=role_id,
        team_role=None,
    )
    viewer_status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=viewer_headers
    )
    assert viewer_status.status_code == 200, viewer_status.text
    assert viewer_status.json()["can_control_agent"] is False

    recruiter_headers, recruiter_email = auth_headers(
        client,
        full_name="Assigned Recruiter",
        organization_name="Capability Recruiter Org",
    )
    _set_member_role_access(
        recruiter_email,
        organization_id=seeded["org_id"],
        role_id=role_id,
        team_role="recruiter",
    )
    recruiter_status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=recruiter_headers
    )
    assert recruiter_status.status_code == 200, recruiter_status.text
    assert recruiter_status.json()["can_control_agent"] is True


def test_agent_status_attributes_current_human_pause_not_latest_editor(client):
    from tests.conftest import auth_headers

    owner_headers, owner_email = auth_headers(
        client,
        full_name="Pause Owner",
        organization_name="Pause Attribution Source Org",
    )
    seeded = _seed_org_with_agent_roles(
        "Pause Attribution Org", role_names=["A"]
    )
    _attach_user_to_org(owner_email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    paused = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=owner_headers,
    )
    assert paused.status_code == 200, paused.text

    mine = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=owner_headers
    )
    assert mine.status_code == 200, mine.text
    paused_by = mine.json()["paused_by"]
    assert set(paused_by) == {
        "user_id",
        "name",
        "is_current_user",
        "changed_at",
        "attribution",
        "source",
    }
    assert paused_by["user_id"] == _user_id(owner_email)
    assert paused_by["name"] == "Pause Owner"
    assert paused_by["is_current_user"] is True
    assert paused_by["changed_at"] is not None
    assert paused_by["attribution"] == "verified"
    assert paused_by["source"] == "role_change_event"

    viewer_headers, viewer_email = auth_headers(
        client,
        full_name="Status Viewer",
        organization_name="Pause Attribution Viewer Org",
    )
    _attach_user_to_org(viewer_email, seeded["org_id"])
    # A later unrelated edit by another user must not replace the actor who
    # authored the still-current pause.
    edited = client.patch(
        f"/api/v1/roles/{role_id}",
        json={"expected_version": paused.json()["version"], "score_threshold": 77},
        headers=viewer_headers,
    )
    assert edited.status_code == 200, edited.text

    viewed = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=viewer_headers
    )
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["paused_by"]["user_id"] == _user_id(owner_email)
    assert viewed.json()["paused_by"]["name"] == "Pause Owner"
    assert viewed.json()["paused_by"]["is_current_user"] is False


def test_agent_status_labels_unique_member_legacy_pause_as_inferred(client):
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(
        client,
        full_name="Legacy Pause Owner",
        organization_name="Legacy Pause Source Org",
    )
    seeded = _seed_org_with_agent_roles(
        "Legacy Unique Pause Attribution Org", role_names=["A"]
    )
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).one()
        # A future instant avoids SQLite timestamp precision making the user
        # appear to have been created after this synthetic legacy pause.
        role.agent_paused_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        role.agent_paused_reason = "paused by recruiter"
        sess.commit()
    finally:
        sess.close()

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    )
    assert status.status_code == 200, status.text
    paused_by = status.json()["paused_by"]
    assert paused_by["user_id"] == _user_id(email)
    assert paused_by["name"] == "Legacy Pause Owner"
    assert paused_by["is_current_user"] is True
    assert paused_by["changed_at"] == status.json()["paused_at"]
    assert paused_by["attribution"] == "inferred"
    assert paused_by["source"] == "legacy_unique_member"


def test_agent_status_leaves_ambiguous_legacy_pause_actor_unavailable(client):
    from tests.conftest import TestingSessionLocal, auth_headers

    first_headers, first_email = auth_headers(
        client,
        full_name="First Legacy Member",
        organization_name="First Legacy Member Org",
    )
    _, second_email = auth_headers(
        client,
        full_name="Second Legacy Member",
        organization_name="Second Legacy Member Org",
    )
    seeded = _seed_org_with_agent_roles(
        "Ambiguous Legacy Pause Attribution Org", role_names=["A"]
    )
    _attach_user_to_org(first_email, seeded["org_id"])
    _attach_user_to_org(second_email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).one()
        role.agent_paused_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        role.agent_paused_reason = "paused by recruiter"
        sess.commit()
    finally:
        sess.close()

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=first_headers
    )
    assert status.status_code == 200, status.text
    paused_by = status.json()["paused_by"]
    assert paused_by["user_id"] is None
    assert paused_by["name"] is None
    assert paused_by["is_current_user"] is False
    assert paused_by["changed_at"] == status.json()["paused_at"]
    assert paused_by["attribution"] == "unavailable"
    assert paused_by["source"] == "legacy_history"


def test_agent_status_preserves_audit_time_when_actor_is_unavailable(client):
    from app.services.role_change_audit import (
        ROLE_CHANGE_ACTION_AGENT_PAUSED,
        add_role_change_event,
        capture_role_change_snapshot,
    )
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles(
        "Unavailable Audited Pause Attribution Org", role_names=["A"]
    )
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).one()
        before = capture_role_change_snapshot(role)
        role.agent_paused_at = datetime.now(timezone.utc)
        role.agent_paused_reason = "paused by recruiter"
        role.version = 2
        add_role_change_event(
            sess,
            role=role,
            before=before,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
            # Mirrors a preserved append-only event after its actor account is
            # removed and the FK anonymizes the actor reference.
            actor_user_id=None,
            from_version=1,
            to_version=2,
            reason="paused by recruiter",
        )
        sess.commit()
    finally:
        sess.close()

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    )
    assert status.status_code == 200, status.text
    paused_by = status.json()["paused_by"]
    assert paused_by["user_id"] is None
    assert paused_by["name"] is None
    assert paused_by["is_current_user"] is False
    assert paused_by["changed_at"] is not None
    assert paused_by["attribution"] == "unavailable"
    assert paused_by["source"] == "role_change_event"


def test_agent_status_does_not_attribute_system_pause_to_older_human(client):
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles(
        "System Pause Attribution Org", role_names=["A"]
    )
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    paused = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    assert client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    ).json()["paused_by"] is not None

    # Simulate a later automatic hold while retaining the older human pause
    # audit row. Status must describe the system reason without borrowing that
    # older actor.
    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).one()
        role.agent_paused_at = datetime.now(timezone.utc)
        role.agent_paused_reason = "monthly USD cap reached: 5400c >= 5000c"
        role.version = int(role.version or 1) + 1
        sess.commit()
    finally:
        sess.close()

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["paused_reason"].startswith("monthly USD cap reached")
    assert status.json()["paused_by"] is None


def test_agent_status_breaks_pending_total_into_decisions_and_questions(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles(
        "Pending Breakdown Org", role_names=["A"]
    )
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    _seed_pending_decision(seeded["org_id"], role_id)
    _seed_open_question(seeded["org_id"], role_id)

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["pending_decisions"] == 2
    assert status.json()["pending_breakdown"] == {
        "total": 2,
        "decisions": 1,
        "questions": 1,
    }


def test_pause_one_role_is_idempotent(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Idem Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    first = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_paused_at = _role_pause_state(role_id)[0]
    second = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": first.json()["version"]},
        headers=headers,
    )
    assert second.status_code == 200
    # The pause timestamp doesn't move on a repeat pause.
    assert _role_pause_state(role_id)[0] == first_paused_at


def test_pause_one_role_requires_enabled_agent(client):
    """Pausing a role whose agent is off is a 409 — nothing to pause."""
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Off Role Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).first()
        role.agentic_mode_enabled = False
        sess.commit()
    finally:
        sess.close()

    resp = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert resp.status_code == 409


def test_resume_one_role_clears_pause(client, monkeypatch):
    from tests.conftest import auth_headers

    # Resume kicks an immediate cycle; stub it so this stays a fast HTTP-layer
    # check (celery runs eagerly in the suite).
    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *a, **k: None,
    )

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Resume Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    paused = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    assert _role_pause_state(role_id)[0] is not None

    resp = client.post(
        f"/api/v1/roles/{role_id}/agent/resume",
        json={"expected_version": paused.json()["version"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resumed"] is True
    assert body["paused"] is False

    paused_at, reason, _enabled = _role_pause_state(role_id)
    assert paused_at is None
    assert reason is None

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["bootstrap_status"] == "starting"
    assert status.json()["bootstrap_started_at"] is not None


def test_resume_one_role_returns_503_and_stays_paused_when_runtime_unready(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Unready Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    paused = client.post(
        f"/api/v1/roles/{role_id}/agent/pause",
        json={"expected_version": _role_version(role_id)},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        resp = client.post(
            f"/api/v1/roles/{role_id}/agent/resume",
            json={"expected_version": paused.json()["version"]},
            headers=headers,
        )

    assert resp.status_code == 503, resp.text
    assert "heartbeat_stale" in resp.text
    assert _role_pause_state(role_id)[0] is not None


def test_turn_off_keeps_pending_decisions_by_default(client):
    """Regression guard for the pause/off decoupling: turning the agent OFF
    (PATCH agentic_mode_enabled=false) must NOT discard the role's pending
    decisions — they stay actionable. Discarding is now an explicit opt-in.
    """
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Turn-off Keep Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    decision_id = _seed_pending_decision(seeded["org_id"], role_id)

    resp = client.patch(
        f"/api/v1/roles/{role_id}",
        json={
            "agentic_mode_enabled": False,
            "expected_version": _role_version(role_id),
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agentic_mode_enabled"] is False
    # The defining change: the queue survives turn-off.
    assert _decision_status(decision_id) == "pending"


def test_explicit_discard_after_turn_off_clears_queue(client):
    """The opt-in path: POST /agent-decisions/discard wipes the queue when the
    recruiter ticks 'also discard' on the Turn-off dialog.
    """
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Turn-off Discard Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    decision_id = _seed_pending_decision(seeded["org_id"], role_id)

    turned_off = client.patch(
        f"/api/v1/roles/{role_id}",
        json={
            "agentic_mode_enabled": False,
            "expected_version": _role_version(role_id),
        },
        headers=headers,
    )
    resp = client.post(
        "/api/v1/agent-decisions/discard",
        json={
            "role_id": role_id,
            "expected_version": turned_off.json()["version"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["discarded"] == 1
    assert _decision_status(decision_id) == "discarded"
