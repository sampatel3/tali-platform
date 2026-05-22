"""Activation hook: turning the agent ON for a role fires a one-shot
backfill that queues ``skip_assessment_reject`` decisions for any
below-threshold pre-screens that landed during the off-window.

The whole point: ``queue_pre_screen_reject`` is gated on
``agentic_mode_enabled``. While the agent is off, every below-threshold
pre-screen is silently dropped — there's no decision row, the candidate
isn't in any cohort (cohort SQL only surfaces scores >= 50), and
nothing in the normal post-toggle daily-review cycle would catch them.
Without this hook a recruiter who toggles agent off → applicant
arrives → re-toggles on would lose that candidate to the void.

Resume (pause -> not-paused with mode still on) does NOT trigger the
backfill: the emitter's gate is on ``agentic_mode_enabled`` not on
``agent_paused_at``, so pre-screens kept queueing rejects through the
pause. Re-running the backfill would be wasted work.
"""
from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.tasks.agent_tasks import agent_backfill_pre_screen_rejects
from tests.conftest import auth_headers


# Same BigInteger PK shim used elsewhere — SQLite only autoincrements
# plain INTEGER PKs, not BigInteger.
_BIG_PK = {"agent_decisions": 0, "agent_runs": 0}

def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]

event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentRun, "before_insert", _assign_big_pk)


def _create_role(client, headers, name="Backfill Role") -> dict:
    resp = client.post("/api/v1/roles", json={"name": name}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Toggle-handler hook: enqueue backfill on agent_activated_now ONLY
# ---------------------------------------------------------------------------


def test_activating_agent_enqueues_pre_screen_backfill(client):
    headers, _ = auth_headers(client)
    role = _create_role(client, headers, name="Activation Backfill Target")

    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ), patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ) as mock_backfill, patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay"
    ):
        resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    mock_backfill.assert_called_once_with(int(role["id"]))


def test_disabling_agent_does_not_enqueue_backfill(client):
    headers, _ = auth_headers(client)
    role = _create_role(client, headers, name="Toggle-Off Target")

    # Turn on first.
    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ), patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ), patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay"
    ):
        on = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert on.status_code == 200

    # Now turn off — must NOT enqueue backfill.
    with patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ) as mock_backfill, patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay"
    ):
        off = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": False},
            headers=headers,
        )
    assert off.status_code == 200, off.text
    mock_backfill.assert_not_called()


def test_no_op_patch_with_agent_on_does_not_enqueue_backfill(client):
    """PATCH that doesn't touch ``agentic_mode_enabled`` must be a no-op
    for the backfill hook. Without this guard, every unrelated role edit
    would re-scan the org's pre-screen rejects."""
    headers, _ = auth_headers(client)
    role = _create_role(client, headers, name="Unrelated PATCH Target")

    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ), patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ), patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay"
    ):
        client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    # Now an unrelated PATCH (e.g. renaming the role).
    with patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ) as mock_backfill:
        resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"name": "Renamed Role"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    mock_backfill.assert_not_called()


def test_already_enabled_patch_does_not_re_enqueue_backfill(client):
    """Sending ``agentic_mode_enabled: True`` when the role is already
    enabled is also a no-op for the backfill — we only fire on the
    false→true transition."""
    headers, _ = auth_headers(client)
    role = _create_role(client, headers, name="Idempotent Activation Target")

    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ), patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ), patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay"
    ):
        client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    with patch(
        "app.tasks.agent_tasks.agent_backfill_pre_screen_rejects.delay"
    ) as mock_backfill, patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay"
    ):
        resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True},
            headers=headers,
        )
    assert resp.status_code == 200
    mock_backfill.assert_not_called()


# ---------------------------------------------------------------------------
# Celery task itself: does the right thing when invoked
# ---------------------------------------------------------------------------


def _seed_stranded_below_threshold(db):
    """Build an agent-on role with one open below-threshold app, no
    pending decision. Returns (role, app)."""
    org = Organization(name="Stranded Org", slug=f"st-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        auto_reject=False,
        agentic_mode_enabled=True,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email=f"s-{id(db)}@x.test", full_name="S")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=30.0,
    )
    db.add(app); db.flush()
    db.commit()
    return org, role, app


def test_backfill_task_queues_stranded_pre_screen_rejects(db):
    org, role, app = _seed_stranded_below_threshold(db)
    # Sanity: no pending decision exists yet.
    assert db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count() == 0

    # ``task_always_eager=True`` in conftest makes .run synchronous.
    result = agent_backfill_pre_screen_rejects.run(int(role.id))
    assert result["status"] == "ok"
    assert result["created"] == 1
    assert result["role_id"] == int(role.id)

    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == app.id,
            AgentDecision.status == "pending",
        )
        .all()
    )
    assert len(pending) == 1
    assert pending[0].decision_type == "skip_assessment_reject"


def test_backfill_task_skips_when_role_toggled_off_between_patch_and_pickup(db):
    """The PATCH commits before the Celery worker picks up the task.
    If the recruiter toggles the role back off in that window, the task
    must re-check the flag and skip rather than emit phantom decisions
    on a now-off role."""
    org, role, app = _seed_stranded_below_threshold(db)
    role.agentic_mode_enabled = False
    db.flush()
    db.commit()

    result = agent_backfill_pre_screen_rejects.run(int(role.id))
    assert result["status"] == "skipped"
    assert result["reason"] == "agentic_mode_disabled"
    assert db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count() == 0


def test_backfill_task_missing_role_returns_skipped(db):
    result = agent_backfill_pre_screen_rejects.run(999_999_999)
    assert result["status"] == "skipped"
    assert result["reason"] == "role_not_found"
