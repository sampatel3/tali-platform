"""HITL gate on send_assessment when role.auto_promote=False."""

from __future__ import annotations

from unittest.mock import patch

from app.agent_runtime.tool_registry import _tool_send_assessment
from app.models.agent_needs_input import AgentNeedsInput
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


def test_hitl_gate_opens_needs_input_instead_of_sending(db):
    org, role, _, app = make_world(db, send_requires_approval=True)
    run = _make_run(db, role)
    args = {"application_id": int(app.id)}
    with patch("app.agent_runtime.tool_registry.send_assessment.run") as send:
        result = _tool_send_assessment(db, agent_run=run, role=role, args=args)
    send.assert_not_called()
    assert result["status"] == "awaiting_recruiter_approval"
    assert "needs_input_id" in result
    rows = db.query(AgentNeedsInput).filter(
        AgentNeedsInput.role_id == role.id,
        AgentNeedsInput.kind == "send_assessment_approval",
    ).all()
    assert len(rows) == 1
    assert rows[0].is_open


def test_no_gate_auto_executes_send(db):
    org, role, _, app = make_world(db, send_requires_approval=False)
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
