"""HITL gate on send_assessment when role.auto_promote=False.

The gate now queues an AgentDecision(decision_type='send_assessment')
instead of an AgentNeedsInput row, so the per-candidate verdict lands
in the recruiter's Review queue alongside advance/reject decisions.
"""

from __future__ import annotations

from unittest.mock import patch

from app.agent_runtime.tool_registry import _tool_send_assessment
from app.components.scoring.freshness import capture_score_generations
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob

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
    application_ids = [
        int(row[0])
        for row in db.query(CandidateApplication.id)
        .filter(CandidateApplication.role_id == int(role.id))
        .all()
    ]
    generations = capture_score_generations(
        db, role=role, application_ids=application_ids
    )
    run.__engine_policy_snapshots__ = {  # type: ignore[attr-defined]
        application_id: {
            "_score_generation": generation,
            "_persisted_decision_type": "send_assessment",
        }
        for application_id, generation in generations.items()
    }
    return run


def test_hitl_gate_queues_decision_instead_of_sending(db):
    org, role, _, app = make_world(
        db,
        send_requires_approval=True,
        with_task=True,
        pre_screen=80.0,
        cv_match=80.0,
    )
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
    org, role, _, app = make_world(
        db,
        send_requires_approval=False,
        with_task=True,
        pre_screen=80.0,
        cv_match=80.0,
    )
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


def test_no_gate_auto_send_requires_matching_policy_decision_context(db):
    _org, role, _, app = make_world(
        db,
        send_requires_approval=False,
        with_task=True,
        pre_screen=80.0,
        cv_match=80.0,
    )
    run = _make_run(db, role)
    run.__engine_policy_snapshots__[int(app.id)][  # type: ignore[attr-defined]
        "_persisted_decision_type"
    ] = "reject"

    with patch(
        "app.actions.send_assessment.freeze_assessment_task",
        side_effect=AssertionError("off-policy send must stop before provisioning"),
    ):
        result = _tool_send_assessment(
            db,
            agent_run=run,
            role=role,
            args={"application_id": int(app.id)},
        )

    assert result["status"] == "blocked"
    assert "decision context" in str(result["detail"])


def test_no_gate_auto_send_refuses_cold_no_job_application(db):
    _org, role, _, app = make_world(
        db, send_requires_approval=False, with_task=True
    )
    run = _make_run(db, role)

    with patch(
        "app.actions.send_assessment.freeze_assessment_task",
        side_effect=AssertionError("cold app must stop before provisioning"),
    ):
        result = _tool_send_assessment(
            db,
            agent_run=run,
            role=role,
            args={"application_id": int(app.id)},
        )

    assert result["status"] == "blocked"
    assert "score refresh" in str(result["detail"])


def test_no_gate_auto_send_refuses_latest_stale_owner_score(db):
    _org, role, _, app = make_world(
        db, send_requires_approval=False, with_task=True
    )
    run = _make_run(db, role)
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="stale",
        )
    )
    db.flush()

    with patch(
        "app.actions.send_assessment.freeze_assessment_task",
        side_effect=AssertionError("stale score must stop before provisioning"),
    ):
        result = _tool_send_assessment(
            db,
            agent_run=run,
            role=role,
            args={"application_id": int(app.id)},
        )

    assert result["status"] == "blocked"
    assert "score refresh" in str(result["detail"])


def test_skip_toggle_redirects_send_to_advance_despite_task(db):
    """auto_skip_assessment=True bypasses the assessment stage even when a
    task exists — the send tool queues an advance_to_interview decision
    instead of an assessment invite."""
    org, role, _, app = make_world(
        db,
        send_requires_approval=True,
        with_task=True,
        pre_screen=80.0,
        cv_match=80.0,
    )
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
    org, role, _, app = make_world(
        db,
        send_requires_approval=True,
        with_task=True,
        pre_screen=80.0,
        cv_match=80.0,
    )
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
