"""Safety guard on the agent's autonomous assessment sends (auto_promote).

When auto_promote is on the agent sends assessments with no human click — but
turning it on must be safe: it can't blast a whole cleared batch at candidates
or run past the role's budget. These tests pin the guard:

- within budget + under the daily cap → auto-send fires (no human)
- over the per-day volume cap → HITL card queued, nothing sent
- over the monthly budget cap → HITL card queued, nothing sent, role auto-paused
- toggle off → unchanged (no auto-send, no guard tag)
- _queue defense-in-depth: a guarded send never auto-executes when held
"""

from __future__ import annotations

from unittest.mock import patch

from app.agent_runtime.tool_registry import _queue, _tool_send_assessment
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services import assessment_autosend_guard as guard_mod
from app.services.assessment_autosend_guard import check_auto_send

from .conftest import make_world


def _make_run(db, role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    return run


def _seed_assessment_today(db, org, role, candidate) -> Assessment:
    a = Assessment(
        organization_id=org.id,
        role_id=role.id,
        candidate_id=candidate.id,
        task_id=role.tasks[0].id,
        token=f"tok-{id(object())}",
    )
    db.add(a)
    db.flush()
    return a


def _seed_over_budget(db, org, role) -> None:
    # role cap is $50 (5000c). 60_000_000 micro-credits = 6000c = $60 > cap.
    db.add(
        UsageEvent(
            organization_id=org.id,
            role_id=role.id,
            feature="assessment",
            model="m",
            markup_multiplier=1,
            credits_charged=60_000_000,
        )
    )
    db.flush()


# --- guard unit --------------------------------------------------------------


def test_guard_ok_within_budget_and_cap(db):
    _, role, _, _ = make_world(db, send_requires_approval=False, with_task=True)
    assert check_auto_send(db, role=role).ok


def test_guard_blocks_over_volume(db, monkeypatch):
    org, role, cand, _ = make_world(db, send_requires_approval=False, with_task=True)
    monkeypatch.setattr(guard_mod, "daily_cap", lambda: 1)
    _seed_assessment_today(db, org, role, cand)
    g = check_auto_send(db, role=role)
    assert not g.ok
    assert g.hold_kind == "volume"


def test_guard_blocks_over_budget(db):
    org, role, _, _ = make_world(db, send_requires_approval=False, with_task=True)
    _seed_over_budget(db, org, role)
    g = check_auto_send(db, role=role)
    assert not g.ok
    assert g.hold_kind == "budget"


# --- integration via _tool_send_assessment -----------------------------------


def test_autosend_within_limits_sends(db):
    _, role, _, app = make_world(db, send_requires_approval=False, with_task=True)
    run = _make_run(db, role)

    class _FakeResult:
        def as_dict(self):
            return {"status": "sent", "assessment_id": 7}

    with patch(
        "app.agent_runtime.tool_registry.send_assessment.run",
        return_value=_FakeResult(),
    ) as send:
        result = _tool_send_assessment(
            db, agent_run=run, role=role, args={"application_id": int(app.id)}
        )
    send.assert_called_once()
    assert result == {"status": "sent", "assessment_id": 7}


def test_over_volume_holds_card_not_send(db, monkeypatch):
    org, role, cand, app = make_world(db, send_requires_approval=False, with_task=True)
    monkeypatch.setattr(guard_mod, "daily_cap", lambda: 1)
    _seed_assessment_today(db, org, role, cand)
    run = _make_run(db, role)
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(
            db, agent_run=run, role=role, args={"application_id": int(app.id)}
        )
    send.assert_not_called()
    assert result["status"] == "awaiting_recruiter_approval"
    assert result.get("auto_send_hold")
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.decision_type == "send_assessment",
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert (rows[0].evidence or {}).get("auto_send_hold")


def test_over_budget_holds_card_not_send_and_pauses(db):
    org, role, _, app = make_world(db, send_requires_approval=False, with_task=True)
    _seed_over_budget(db, org, role)
    run = _make_run(db, role)
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(
            db, agent_run=run, role=role, args={"application_id": int(app.id)}
        )
    send.assert_not_called()
    assert result["status"] == "awaiting_recruiter_approval"
    assert result.get("auto_send_hold")
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.decision_type == "send_assessment",
        )
        .all()
    )
    assert len(rows) == 1 and rows[0].status == "pending"
    # Over-budget also auto-pauses the role (existing budget-gate behavior).
    assert role.agent_paused_at is not None


def test_toggle_off_queues_without_guard_tag(db):
    _, role, _, app = make_world(db, send_requires_approval=True, with_task=True)
    run = _make_run(db, role)
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(
            db, agent_run=run, role=role, args={"application_id": int(app.id)}
        )
    send.assert_not_called()
    assert result["status"] == "awaiting_recruiter_approval"
    # Guard is only consulted for auto_promote=True; a plain HITL send carries
    # no hold tag.
    assert "auto_send_hold" not in result


def test_wrong_role_application_refused_not_sent(db):
    """An application belonging to a *different* role in the same org must be
    refused before any guard runs — otherwise role B's budget/daily cap could
    be bypassed by sending its candidate while running role A (under its own
    limits)."""
    org, role_a, _, _ = make_world(db, send_requires_approval=False, with_task=True)
    # Role B in the same org, with its own application. Its auto_promote is off
    # and it would have its own caps — none of which should be consulted here.
    role_b = Role(
        organization_id=org.id,
        name="Frontend",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_promote=False,
    )
    db.add(role_b)
    db.flush()
    cand_b = Candidate(organization_id=org.id, email=f"b{id(db)}@x.test", full_name="B")
    db.add(cand_b)
    db.flush()
    app_b = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand_b.id,
        role_id=role_b.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
    )
    db.add(app_b)
    db.flush()

    run = _make_run(db, role_a)
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(
            db, agent_run=run, role=role_a, args={"application_id": int(app_b.id)}
        )
    send.assert_not_called()
    assert result["status"] == "wrong_role"
    # Nothing was queued against role A for role B's candidate either.
    assert (
        db.query(AgentDecision).filter(AgentDecision.role_id == role_a.id).all() == []
    )


def test_queue_defense_in_depth_holds_guarded_send(db):
    """_queue must not auto-execute a send_assessment when the guard trips,
    even though role.auto_promote is True."""
    org, role, _, app = make_world(db, send_requires_approval=False, with_task=True)
    _seed_over_budget(db, org, role)
    run = _make_run(db, role)
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _queue(
            db,
            agent_run=run,
            role=role,
            args={
                "application_id": int(app.id),
                "reasoning": "strong",
                "evidence": None,
                "confidence": 0.9,
            },
            decision_type="send_assessment",
        )
    send.assert_not_called()
    assert result["status"] == "pending"
    assert result["auto_send_held"] is True
    row = (
        db.query(AgentDecision)
        .filter(AgentDecision.id == result["decision_id"])
        .first()
    )
    assert (row.evidence or {}).get("auto_send_hold")
