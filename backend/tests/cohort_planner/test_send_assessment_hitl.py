"""HITL gate on send_assessment when role.auto_promote=False.

The gate now queues an AgentDecision(decision_type='send_assessment')
instead of an AgentNeedsInput row, so the per-candidate verdict lands
in the recruiter's Review queue alongside advance/reject decisions.
"""

from __future__ import annotations

from unittest.mock import patch

from app.agent_runtime.tool_registry import _tool_send_assessment
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun

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


def test_hitl_gate_queues_decision_instead_of_sending(db):
    org, role, _, app = make_world(db, send_requires_approval=True, with_task=True)
    run = _make_run(db, role)
    args = {"application_id": int(app.id)}
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(db, agent_run=run, role=role, args=args)
    send.assert_not_called()
    assert result["status"] == "awaiting_recruiter_approval"
    assert "decision_id" in result
    rows = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.decision_type == "send_assessment",
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert int(rows[0].application_id) == int(app.id)


def test_no_gate_auto_executes_send(db):
    org, role, _, app = make_world(db, send_requires_approval=False, with_task=True)
    run = _make_run(db, role)
    args = {"application_id": int(app.id)}

    class _FakeResult:
        def as_dict(self):
            return {"status": "sent", "assessment_id": 7}

    with patch(
        "app.agent_runtime.tool_registry.send_assessment.run",
        return_value=_FakeResult(),
    ) as send:
        result = _tool_send_assessment(db, agent_run=run, role=role, args=args)
    send.assert_called_once()
    assert result == {"status": "sent", "assessment_id": 7}


def test_skip_toggle_redirects_send_to_advance_despite_task(db):
    """auto_skip_assessment=True bypasses the assessment stage even when a
    task exists — the send tool queues an advance_to_interview decision
    instead of an assessment invite."""
    org, role, _, app = make_world(db, send_requires_approval=True, with_task=True)
    role.auto_skip_assessment = True
    db.flush()
    run = _make_run(db, role)
    args = {"application_id": int(app.id)}
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(db, agent_run=run, role=role, args=args)
    send.assert_not_called()
    assert result["redirected_to"] == "advance_to_interview"
    assert result["status"] == "awaiting_recruiter_approval"
    rows = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.decision_type == "advance_to_interview",
    ).all()
    assert len(rows) == 1
    assert (rows[0].evidence or {}).get("reason") == "auto_skip_assessment"


def test_hitl_gate_returns_existing_decision_instead_of_duplicating(db):
    """Repeated agent calls for the same candidate hit the dedup branch."""
    org, role, _, app = make_world(db, send_requires_approval=True, with_task=True)
    run = _make_run(db, role)
    args = {"application_id": int(app.id)}
    with patch("app.agent_runtime.tool_registry.send_assessment.run"):
        first = _tool_send_assessment(db, agent_run=run, role=role, args=args)
        second = _tool_send_assessment(db, agent_run=run, role=role, args=args)
    assert first["decision_id"] == second["decision_id"]
    rows = db.query(AgentDecision).filter(
        AgentDecision.role_id == role.id,
        AgentDecision.decision_type == "send_assessment",
    ).all()
    assert len(rows) == 1
