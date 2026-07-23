"""Direct unit tests for the agent runtime's tool registry + reject flow.

These bypass the Anthropic loop and call ``tool_registry.dispatch``
directly with a synthetic AgentRun. They cover:
- the new read tools (search_applications, compare_applications,
  nl_search_candidates, find_top_candidates, get_candidate)
- the new queue tools (queue_reject_decision,
  queue_skip_assessment_reject_decision)
- the reject side-effect wired into approve_decision
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.actions import approve_decision, post_workable_note, queue_decision
from app.actions.types import Actor
from app.agent_runtime import tool_registry
from app.candidate_search.schemas import ParsedFilter, SearchOutput
from app.components.scoring.freshness import (
    capture_score_generation,
    capture_score_generations,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from app.services.provider_usage_admission import AutomaticProviderAuthorityError
from app.services.metered_async_anthropic_client import GraphProviderAdmissionError


# SQLite doesn't autoincrement BigInteger PKs (only INTEGER PKs are special-cased).
# AgentRun, AgentDecision, AgentNeedsInput all use BigInteger to match prod's
# Postgres sequences, so we hand-roll an in-memory counter for the test session.
_BIG_PK_COUNTERS: dict[str, int] = {
    "agent_runs": 0,
    "agent_decisions": 0,
    "agent_needs_input": 0,
}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — fired by SQLA
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentNeedsInput, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_org(db) -> Organization:
    org = Organization(name="Agent Test Org", slug=f"agent-org-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization) -> Role:
    role = Role(
        organization_id=org.id,
        name="Senior Backend Engineer",
        source="manual",
        job_spec_text="Build distributed systems.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        # Phase 7: keep send_assessment auto-execute for these existing
        # tests; HITL behaviour is covered by tests/cohort_planner/.
        auto_promote=True,
        # Direct-dispatch tests exercise the full legacy catalogue. Production
        # roles with no explicit allowlist receive the narrower prompt-aligned
        # default; dedicated governance tests cover that fail-closed behavior.
        agent_action_allowlist=sorted(tool_registry.GOVERNED_ACTION_TOOL_NAMES),
    )
    db.add(role)
    db.flush()
    return role


def _attach_task(db, org: Organization, role: Role) -> Task:
    """Link an assessment task to the role. Required for send_assessment:
    with no task the tool now advances directly instead of sending."""
    task = Task(organization_id=org.id, name=f"Assessment for {role.name}")
    db.add(task)
    db.flush()
    role.tasks.append(task)
    db.flush()
    return task


def _make_application(
    db,
    *,
    org: Organization,
    role: Role,
    name: str,
    email: str,
    taali: float | None = None,
    pipeline_stage: str = "review",
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id, email=email, full_name=name, position="Engineer"
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=taali,
    )
    db.add(app)
    db.flush()
    # These tests exercise queue/action mechanics rather than cold scoring.
    # Give the fixture a canonical completed attempt without injecting numeric
    # values that would alter search/policy expectations. The deliberately old
    # timestamp ensures explicit newer stale attempts in freshness tests win.
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
            queued_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.flush()
    return app


def _make_agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="manual",
        status="running",
        model_version="claude-3-5-haiku-latest",
        prompt_version="agent.v2.test",
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


def _score_generation(db, role: Role, app: CandidateApplication):
    return capture_score_generation(db, role=role, application_id=int(app.id))


def _make_assessment(
    db,
    *,
    org: Organization,
    role: Role,
    app: CandidateApplication,
    token: str = "tok",
) -> Assessment:
    task = Task(
        organization_id=org.id,
        name=f"Task for {role.name}",
        description="x",
        task_type="python",
        difficulty="medium",
        duration_minutes=60,
        starter_code="",
        test_code="",
        is_active=True,
        is_template=False,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=app.candidate_id,
        task_id=task.id,
        role_id=role.id,
        application_id=app.id,
        token=token,
        duration_minutes=60,
    )
    db.add(assessment)
    db.flush()
    return assessment


def _make_recruiter(db, org: Organization) -> User:
    user = User(
        email=f"recruiter-{id(db)}@example.com",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Tool catalogue / schema sanity
# ---------------------------------------------------------------------------


def test_agent_tools_catalogue_contains_expected_names():
    names = {t["name"] for t in tool_registry.AGENT_TOOLS}
    assert {
        # reads
        "get_application",
        "get_candidate",
        "get_candidate_cv",
        "search_applications",
        "compare_applications",
        "nl_search_candidates",
        "find_top_candidates",
        "graph_search_candidates",
        "get_cohort_signals",
        # execute
        "score_cv",
        "send_assessment",
        "resend_assessment_invite",
        "create_application",
        # queue
        "queue_advance_decision",
        "queue_reject_decision",
        "queue_skip_assessment_reject_decision",
        "queue_escalate_decision",
        # terminal
        "agent_run_complete",
    }.issubset(names)
    assert "post_workable_note" not in names

    search_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "search_applications"
    )
    properties = search_tool["input_schema"]["properties"]
    assert "role_id" not in properties
    assert set(properties["score_type"]["enum"]) == {
        "taali",
        "pre_screen",
        "rank",
        "cv_match",
        "workable",
        "assessment",
        "role_fit",
    }
    assert "sourced" in properties["pipeline_stage"]["enum"]
    assert properties["offset"]["minimum"] == 0
    nl_search_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "nl_search_candidates"
    )
    assert nl_search_tool["input_schema"]["properties"]["rerank"]["default"] is False
    grounded_search_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "find_top_candidates"
    )
    assert grounded_search_tool["input_schema"]["required"] == ["query"]
    assert set(grounded_search_tool["input_schema"]["properties"]) == {
        "query",
        "limit",
        "rank_by",
    }
    graph_search_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "graph_search_candidates"
    )
    graph_description = graph_search_tool["description"].lower()
    assert "not citations" in graph_description
    assert "original-source evidence references" in graph_description
    assert "cite specifics" not in graph_description
    compare_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "compare_applications"
    )
    assert compare_tool["input_schema"]["properties"]["application_ids"]["minItems"] == 2
    advance_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "queue_advance_decision"
    )
    escalation_tool = next(
        t for t in tool_registry.AGENT_TOOLS if t["name"] == "queue_escalate_decision"
    )
    assert escalation_tool["input_schema"] == advance_tool["input_schema"]


def test_default_role_tools_hide_legacy_mutations(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_action_allowlist = None
    names = {tool["name"] for tool in tool_registry.tools_for_role(role)}
    assert "create_application" not in names
    assert "post_workable_note" not in names
    assert "refresh_candidate_graph" not in names
    assert "get_application" in names
    assert "find_top_candidates" in names
    assert "find_top_candidates" in tool_registry.DEFAULT_AGENT_ACTION_ALLOWLIST
    assert "agent_run_complete" in names


def test_dispatch_enforces_explicit_empty_action_allowlist(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_action_allowlist = []
    run = _make_agent_run(db, role)
    result = tool_registry.dispatch(
        "score_cv", {"application_id": 123}, db=db, agent_run=run, role=role
    )
    assert result["status"] == "blocked_by_governance"


def test_dispatch_enforces_configured_decision_budget(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_decision_budget_per_cycle = 1
    app1 = _make_application(db, org=org, role=role, name="A1", email="a1@x.test")
    app2 = _make_application(db, org=org, role=role, name="A2", email="a2@x.test")
    run = _make_agent_run(db, role)
    args = {"reasoning": "Below bar", "evidence": {}, "confidence": 0.9}
    first = tool_registry.dispatch(
        "queue_reject_decision", {**args, "application_id": app1.id}, db=db, agent_run=run, role=role
    )
    second = tool_registry.dispatch(
        "queue_reject_decision", {**args, "application_id": app2.id}, db=db, agent_run=run, role=role
    )
    assert first["decision_id"]
    assert second["status"] == "blocked_by_governance"
    assert "decision budget" in second["reason"]


def test_resend_assessment_invite_dispatch_invokes_action(db):
    """Agent's resend tool goes through the shared action when auto_promote=True."""
    org = _make_org(db)
    role = _make_role(db, org)  # auto_promote=True by default in the helper
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    assessment = _make_assessment(db, org=org, role=role, app=app)
    run = _make_agent_run(db, role)

    fake_result = type(
        "_R",
        (),
        {"as_dict": lambda self: {"assessment_id": int(assessment.id), "status": "resent", "detail": None}},
    )()
    with patch(
        "app.actions.resend_assessment_invite.run", return_value=fake_result
    ) as mock_run:
        result = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["organization_id"] == org.id
    assert kwargs["assessment_id"] == int(assessment.id)
    assert result["status"] == "resent"


def test_direct_candidate_contact_is_hard_capped_per_cycle(db):
    """Auto-promote cannot use the direct path to bypass the one-contact cap."""
    org = _make_org(db)
    role = _make_role(db, org)
    app1 = _make_application(db, org=org, role=role, name="A1", email="a1@x.test")
    app2 = _make_application(db, org=org, role=role, name="A2", email="a2@x.test")
    assessment1 = _make_assessment(db, org=org, role=role, app=app1, token="one")
    assessment2 = _make_assessment(db, org=org, role=role, app=app2, token="two")
    run = _make_agent_run(db, role)

    def _result(assessment_id):
        return type(
            "_R",
            (),
            {"as_dict": lambda self: {"assessment_id": assessment_id, "status": "resent", "detail": None}},
        )()

    with patch(
        "app.actions.resend_assessment_invite.run",
        side_effect=[_result(int(assessment1.id)), _result(int(assessment2.id))],
    ) as mock_run:
        first = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment1.id)},
            db=db,
            agent_run=run,
            role=role,
        )
        second = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment2.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert first["status"] == "resent"
    assert second["status"] == "blocked_by_governance"
    assert "high-risk action cap" in second["reason"]
    assert mock_run.call_count == 1


def test_resend_assessment_invite_dispatch_hitl_gate_queues_decision(db):
    """When auto_promote=False the tool queues an AgentDecision (decision_type
    'resend_assessment_invite') instead of calling the action; the recruiter
    approves on the Home page and the approve path dispatches."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    assessment = _make_assessment(db, org=org, role=role, app=app)
    run = _make_agent_run(db, role)

    with patch("app.actions.resend_assessment_invite.run") as mock_action:
        result = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_action.called
    assert result["status"] == "awaiting_recruiter_approval"
    assert "decision_id" in result
    assert result["assessment_id"] == int(assessment.id)
    decision = db.query(AgentDecision).filter(AgentDecision.id == result["decision_id"]).one()
    assert decision.decision_type == "resend_assessment_invite"
    assert decision.status == "pending"
    assert int(decision.application_id) == int(app.id)
    assert (decision.evidence or {}).get("assessment_id") == int(assessment.id)


def test_resend_assessment_invite_dispatch_refuses_cross_role(db):
    """Regression: an agent running for role A must not resend an invite
    for an assessment that belongs to role B in the same org. The
    running role's auto_promote toggle says nothing about role B's
    HITL policy, so we refuse the resend entirely. (Codex P1 on #141.)
    """
    org = _make_org(db)
    role_a = _make_role(db, org)  # auto_promote=True via _make_role default
    role_b = Role(
        organization_id=org.id,
        name="Other Role",
        source="manual",
        agentic_mode_enabled=True,
        auto_promote=False,  # role B requires recruiter approval
    )
    db.add(role_b)
    db.flush()
    app_for_b = _make_application(db, org=org, role=role_b, name="B", email="b@x.test")
    assessment_for_b = _make_assessment(
        db, org=org, role=role_b, app=app_for_b, token="tok-b"
    )
    run_a = _make_agent_run(db, role_a)

    with patch(
        "app.actions.resend_assessment_invite.run"
    ) as mock_action, patch(
        "app.actions.ask_recruiter.open"
    ) as mock_ask:
        result = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment_for_b.id)},
            db=db,
            agent_run=run_a,
            role=role_a,
        )

    assert result["status"] == "wrong_role"
    assert result["assessment_id"] == int(assessment_for_b.id)
    assert not mock_action.called
    assert not mock_ask.called


def test_resend_assessment_invite_dispatch_returns_not_found_for_unknown_id(db):
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)

    with patch(
        "app.actions.resend_assessment_invite.run"
    ) as mock_action:
        result = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": 999999},
            db=db,
            agent_run=run,
            role=role,
        )

    assert result["status"] == "not_found"
    assert not mock_action.called


def test_send_assessment_dispatch_hitl_queues_decision(db):
    """auto_promote=False on the agent's send_assessment tool queues an
    AgentDecision(decision_type='send_assessment') and does NOT touch the
    underlying send_assessment action. Approval routes through
    approve_decision.run (covered in test_approve_decision_dispatches_*)."""
    org = _make_org(db)
    role = _make_role(db, org)
    _attach_task(db, org, role)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    run = _make_agent_run(db, role)

    with patch("app.actions.send_assessment.run") as mock_run:
        result = tool_registry.dispatch(
            "send_assessment",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_run.called
    assert result["status"] == "awaiting_recruiter_approval"
    assert "decision_id" in result
    decision = db.query(AgentDecision).filter(AgentDecision.id == result["decision_id"]).one()
    assert decision.decision_type == "send_assessment"
    assert decision.status == "pending"
    assert int(decision.application_id) == int(app.id)


def test_send_assessment_dispatch_dedups_existing_pending_decision(db):
    """Repeated agent calls for the same candidate return the existing
    pending decision instead of piling up duplicate cards in the
    recruiter's queue."""
    org = _make_org(db)
    role = _make_role(db, org)
    _attach_task(db, org, role)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    run = _make_agent_run(db, role)

    with patch("app.actions.send_assessment.run"):
        first = tool_registry.dispatch(
            "send_assessment",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )
        second = tool_registry.dispatch(
            "send_assessment",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert first["decision_id"] == second["decision_id"]
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.application_id == app.id,
            AgentDecision.decision_type == "send_assessment",
        )
        .all()
    )
    assert len(rows) == 1


def test_approve_decision_dispatches_send_assessment(db):
    """Recruiter approving a send_assessment decision invokes the
    underlying send_assessment action with the agent's chosen
    task_id / duration carried via evidence."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    user = _make_recruiter(db, org)

    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="strong candidate",
        evidence={"task_id": 7, "duration_minutes": 120},
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"test:{app.id}:send_assessment",
    )
    db.add(decision)
    db.flush()

    # The dispatch must actually succeed ("sent") for the decision to close as
    # approved — a non-sent status (e.g. misconfigured) raises instead.
    sent_result = type("_R", (), {"status": "sent", "detail": None})()
    with patch(
        "app.actions.approve_decision.send_assessment.run", return_value=sent_result
    ) as mock_run:
        approve_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=org.id,
            decision_id=int(decision.id),
        )
        db.flush()

    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["application_id"] == int(app.id)
    assert kwargs["task_id"] == 7
    assert kwargs["duration_minutes"] == 120
    db.refresh(decision)
    assert decision.status == "approved"


def test_approve_decision_dispatches_resend_assessment_invite(db):
    """Recruiter approving a resend decision invokes the resend action
    with the assessment_id stored in evidence."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    assessment = _make_assessment(db, org=org, role=role, app=app)
    user = _make_recruiter(db, org)

    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type="resend_assessment_invite",
        recommendation="resend_assessment_invite",
        status="pending",
        reasoning="invite expired",
        evidence={"assessment_id": int(assessment.id)},
        confidence=0.8,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"test:{app.id}:resend_assessment_invite",
    )
    db.add(decision)
    db.flush()

    with patch("app.actions.approve_decision.resend_assessment_invite.run") as mock_run:
        approve_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=org.id,
            decision_id=int(decision.id),
        )
        db.flush()

    assert mock_run.called
    assert mock_run.call_args.kwargs["assessment_id"] == int(assessment.id)
    db.refresh(decision)
    assert decision.status == "approved"


def test_create_application_dispatch_invokes_action(db):
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)

    fake_result = type(
        "_R",
        (),
        {
            "as_dict": lambda self: {
                "application_id": 42,
                "candidate_id": 7,
                "status": "created",
            }
        },
    )()
    with patch(
        "app.actions.create_application.run", return_value=fake_result
    ) as mock_run:
        result = tool_registry.dispatch(
            "create_application",
            {
                "role_id": role.id,
                "candidate_email": "new@example.com",
                "candidate_name": "New Candidate",
            },
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["organization_id"] == org.id
    assert kwargs["role_id"] == role.id
    assert kwargs["candidate_email"] == "new@example.com"
    # Actor passed positionally as the 2nd arg.
    actor = mock_run.call_args.args[1]
    assert actor.type == "agent"
    assert result["status"] == "created"


def test_retired_post_workable_note_dispatch_fails_closed_for_stale_runs(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="X", email="x@x.test")
    run = _make_agent_run(db, role)

    with patch(
        "app.actions.post_workable_note.run", wraps=post_workable_note.run
    ) as action, patch(
        "app.services.workable_op_runner.enqueue_workable_op"
    ) as enqueue:
        result = tool_registry.dispatch(
            "post_workable_note",
            {"application_id": app.id, "body": "Agent flagged this candidate."},
            db=db,
            agent_run=run,
            role=role,
        )

    assert result["status"] == "blocked_by_policy"
    assert result["tool"] == "post_workable_note"
    assert "internal Taali note" in result["detail"]
    action.assert_called_once()
    assert action.call_args.kwargs == {
        "organization_id": org.id,
        "application_id": 0,
        "body": "",
    }
    actor = action.call_args.args[1]
    assert actor.type == "agent"
    assert actor.agent_run_id == run.id
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    ({}, {"application_id": "not-an-integer"}, {"body": None}),
)
def test_retired_post_workable_note_fails_closed_for_malformed_stale_args(
    db, arguments
):
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)

    with patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue:
        result = tool_registry.dispatch(
            "post_workable_note",
            arguments,
            db=db,
            agent_run=run,
            role=role,
        )

    assert result["status"] == "blocked_by_policy"
    assert result["tool"] == "post_workable_note"
    enqueue.assert_not_called()


def test_retired_post_workable_note_action_never_calls_provider(db):
    from app.actions import post_workable_note
    from app.actions.types import Actor

    with patch(
        "app.domains.integrations_notifications.adapters.build_workable_adapter"
    ) as adapter:
        result = post_workable_note.run(
            db,
            Actor.agent(1),
            organization_id=123,
            application_id=456,
            body="Do not send this externally.",
        )

    assert result.status == "skipped"
    assert result.application_id == 456
    assert "internal Taali note" in str(result.detail)
    adapter.assert_not_called()


def test_create_application_dispatch_refuses_cross_role(db):
    """An agent running for role A must not create an application under
    role B in the same org. This matches the single-role execution boundary
    enforced by resend_assessment_invite.
    """
    org = _make_org(db)
    role_a = _make_role(db, org)
    role_b = Role(
        organization_id=org.id,
        name="Other Role",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role_b)
    db.flush()
    run_a = _make_agent_run(db, role_a)

    with patch("app.actions.create_application.run") as mock_action:
        result = tool_registry.dispatch(
            "create_application",
            {
                "role_id": int(role_b.id),
                "candidate_email": "leaked@example.com",
                "candidate_name": "Leaked",
            },
            db=db,
            agent_run=run_a,
            role=role_a,
        )

    assert result["status"] == "wrong_role"
    assert result["role_id"] == int(role_b.id)
    assert not mock_action.called


def test_send_assessment_dispatch_invokes_action(db):
    """Agent tool wires through to the action and returns the action's payload shape."""
    org = _make_org(db)
    role = _make_role(db, org)
    _attach_task(db, org, role)
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test", taali=80.0)
    run = _make_agent_run(db, role)

    fake_result = type(
        "_R", (), {"as_dict": lambda self: {"assessment_id": 99, "status": "sent", "detail": None}}
    )()
    with patch(
        "app.actions.send_assessment.run", return_value=fake_result
    ) as mock_run:
        result = tool_registry.dispatch(
            "send_assessment",
            {"application_id": app.id, "duration_minutes": 60},
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["organization_id"] == org.id
    assert kwargs["application_id"] == app.id
    assert kwargs["duration_minutes"] == 60
    # Actor passed positionally (second arg).
    actor = mock_run.call_args.args[1]
    assert actor.type == "agent"
    assert actor.agent_run_id == run.id

    assert result == {"assessment_id": 99, "status": "sent", "detail": None}


def test_queue_decision_tool_names_covers_all_queue_tools():
    """Decision-budget gating covers every queue tool, not just advance."""
    assert "queue_advance_decision" in tool_registry.QUEUE_DECISION_TOOL_NAMES
    assert "queue_reject_decision" in tool_registry.QUEUE_DECISION_TOOL_NAMES
    assert "queue_skip_assessment_reject_decision" in tool_registry.QUEUE_DECISION_TOOL_NAMES
    assert "queue_escalate_decision" in tool_registry.QUEUE_DECISION_TOOL_NAMES


def test_dispatch_unknown_tool_raises():
    with pytest.raises(KeyError):
        tool_registry.dispatch("bogus_tool", {}, db=None, agent_run=None, role=None)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def test_search_applications_dispatch_ignores_spoofed_role_id(db):
    org = _make_org(db)
    role = _make_role(db, org)
    other_role = Role(organization_id=org.id, name="Other", source="manual")
    db.add(other_role)
    db.flush()
    a1 = _make_application(db, org=org, role=role, name="High", email="h@x.test", taali=85.0)
    a2 = _make_application(db, org=org, role=role, name="Mid", email="m@x.test", taali=60.0)
    other_app = _make_application(
        db,
        org=org,
        role=other_role,
        name="Other Role",
        email="o@x.test",
        taali=99.0,
    )
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "search_applications",
        {"role_id": other_role.id, "min_score": 70.0, "limit": 10},
        db=db,
        agent_run=run,
        role=role,
    )

    ids = [r["application_id"] for r in result]
    assert a1.id in ids
    assert a2.id not in ids  # below threshold
    assert other_app.id not in ids  # spoofed role scope is ignored


def test_search_applications_defaults_to_agents_role(db):
    """Caller doesn't have to pass role_id — the agent uses its own role."""
    org = _make_org(db)
    role = _make_role(db, org)
    a1 = _make_application(db, org=org, role=role, name="A", email="a@x.test", taali=80.0)
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "search_applications", {}, db=db, agent_run=run, role=role
    )
    ids = [r["application_id"] for r in result]
    assert a1.id in ids


def test_compare_applications_dispatch(db):
    org = _make_org(db)
    role = _make_role(db, org)
    a1 = _make_application(db, org=org, role=role, name="A", email="a@x.test", taali=80.0)
    a2 = _make_application(db, org=org, role=role, name="B", email="b@x.test", taali=70.0)
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "compare_applications",
        {"application_ids": [a1.id, a2.id]},
        db=db,
        agent_run=run,
        role=role,
    )

    assert "applications" in result
    assert [r["application_id"] for r in result["applications"]] == [a1.id, a2.id]


def test_nl_search_candidates_dispatch_pins_agent_role_id(db):
    """Agent search cannot spoof another role's scope or billing identity."""
    org = _make_org(db)
    role = _make_role(db, org)
    other_role = _make_role(db, org)
    a1 = _make_application(db, org=org, role=role, name="A", email="a@x.test", taali=80.0)
    run = _make_agent_run(db, role)

    fake = SearchOutput(
        application_ids=[a1.id],
        parsed_filter=ParsedFilter(skills_all=["python"], free_text="python engineer"),
        warnings=[],
        rerank_applied=False,
        subgraph=None,
    )
    with patch(
        "app.candidate_search.runner.run_search", return_value=fake
    ) as mock_runner:
        result = tool_registry.dispatch(
            "nl_search_candidates",
            {"query": "python engineer", "role_id": other_role.id},
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_runner.called
    assert mock_runner.call_args.kwargs["role_id"] == role.id
    assert mock_runner.call_args.kwargs["rerank_enabled"] is False
    assert mock_runner.call_args.kwargs["require_role_authority"] is True
    assert result["total_matched"] == 1
    assert result["applications"][0]["application_id"] == a1.id


def test_find_top_candidates_dispatch_pins_agent_role_id(db):
    """Grounded discovery cannot search or bill against another role."""
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)
    payload = {
        "candidates": [],
        "criteria_unchecked": [],
        "deep_checked": 0,
        "qualified_in_checked": 0,
    }

    with patch(
        "app.mcp.handlers.find_top_candidates", return_value=payload
    ) as mock_find:
        result = tool_registry.dispatch(
            "find_top_candidates",
            {
                "query": "banking experience",
                "limit": 7,
                "rank_by": "role_fit",
                "role_id": 999_999,
            },
            db=db,
            agent_run=run,
            role=role,
        )

    assert result == payload
    assert mock_find.call_args.args[1].require_role_authority is True
    assert mock_find.call_args.kwargs == {
        "query": "banking experience",
        "limit": 7,
        "rank_by": "role_fit",
        "role_id": role.id,
    }


def test_get_candidate_dispatch(db):
    org = _make_org(db)
    role = _make_role(db, org)
    a = _make_application(db, org=org, role=role, name="Cand", email="c@x.test", taali=80.0)
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "get_candidate",
        {"candidate_id": a.candidate_id},
        db=db,
        agent_run=run,
        role=role,
    )
    assert result["candidate_id"] == a.candidate_id
    assert result["full_name"] == "Cand"


def test_graph_search_candidates_dispatch_pins_agent_role_id(db):
    """Graph-shaped search cannot escape the autonomous agent's role."""
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)
    payload = {"applications": [], "graph_facts": [], "warnings": []}

    with patch(
        "app.mcp.handlers.graph_search_candidates", return_value=payload
    ) as mock_graph_search:
        result = tool_registry.dispatch(
            "graph_search_candidates",
            {"query": "stripe", "limit": 7, "role_id": 999_999},
            db=db,
            agent_run=run,
            role=role,
        )

    assert result == payload
    assert mock_graph_search.call_args.args[1].require_role_authority is True
    assert mock_graph_search.call_args.kwargs == {
        "query": "stripe",
        "limit": 7,
        "role_id": role.id,
    }


def test_refresh_candidate_graph_returns_unconfigured_when_graph_off(db):
    """Mirror of the search test: tool no-ops when Graphiti isn't configured."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="Cand", email="c@x.test", taali=70.0)
    run = _make_agent_run(db, role)

    with patch("app.candidate_graph.client.is_configured", return_value=False):
        result = tool_registry.dispatch(
            "refresh_candidate_graph",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert result["status"] == "unconfigured"
    assert result["episodes_sent"] == 0
    assert result["application_id"] == int(app.id)


def test_refresh_candidate_graph_calls_sync_when_configured(db):
    """When configured, the tool delegates to graph_sync.sync_candidate."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="Cand", email="c@x.test", taali=70.0)
    run = _make_agent_run(db, role)

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.sync.sync_candidate", return_value=4
    ) as sync_mock:
        result = tool_registry.dispatch(
            "refresh_candidate_graph",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    sync_mock.assert_called_once()
    _, kwargs = sync_mock.call_args
    assert kwargs["bill_organization_id"] == int(role.organization_id)
    assert kwargs["bill_role_id"] == int(role.id)
    assert kwargs["include_cv_text"] is True
    assert kwargs["require_role_admission"] is True
    assert kwargs["raise_on_error"] is True
    assert result["status"] == "ok"
    assert result["episodes_sent"] == 4
    assert result["application_id"] == int(app.id)


def test_refresh_candidate_graph_refuses_application_from_another_role(db):
    org = _make_org(db)
    running_role = _make_role(db, org)
    other_role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=other_role,
        name="Other role candidate",
        email="other-role-graph@x.test",
        taali=70.0,
    )
    run = _make_agent_run(db, running_role)

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.sync.sync_candidate"
    ) as sync_mock:
        result = tool_registry.dispatch(
            "refresh_candidate_graph",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=running_role,
        )

    assert result["status"] == "not_found"
    sync_mock.assert_not_called()


def test_refresh_candidate_graph_propagates_provider_authority_denial(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Paused graph candidate",
        email="paused-graph@x.test",
        taali=70.0,
    )
    run = _make_agent_run(db, role)
    denied = GraphProviderAdmissionError("role agent is paused")

    with (
        patch("app.candidate_graph.client.is_configured", return_value=True),
        patch(
            "app.candidate_graph.sync.sync_candidate",
            side_effect=denied,
        ) as sync_mock,
        pytest.raises(AutomaticProviderAuthorityError, match="paused"),
    ):
        tool_registry.dispatch(
            "refresh_candidate_graph",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert sync_mock.call_args.kwargs["raise_on_error"] is True


# ---------------------------------------------------------------------------
# Queue tools
# ---------------------------------------------------------------------------


def test_queue_reject_decision_creates_pending_row(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="Low", email="l@x.test", taali=40.0)
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "queue_reject_decision",
        {
            "application_id": app.id,
            "reasoning": "TAALI 40 < threshold; missing required Kubernetes experience.",
            "evidence": {"taali_score": 40, "criteria_misses": ["kubernetes"]},
            "confidence": 0.85,
        },
        db=db,
        agent_run=run,
        role=role,
    )

    assert result["status"] == "pending"
    assert result["decision_type"] == "reject"

    decision = db.query(AgentDecision).filter(AgentDecision.id == result["decision_id"]).one()
    assert decision.decision_type == "reject"
    assert decision.application_id == app.id
    assert decision.idempotency_key == f"{run.id}:{app.id}:reject"


def test_queue_skip_assessment_reject_decision_creates_pending_row(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="VeryLow", email="vl@x.test", taali=20.0,
        pipeline_stage="applied",
    )
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "queue_skip_assessment_reject_decision",
        {
            "application_id": app.id,
            "reasoning": "CV-match 20, no relevant experience; not worth the assessment cost.",
            "evidence": {"cv_match_score": 20},
            "confidence": 0.9,
        },
        db=db,
        agent_run=run,
        role=role,
    )

    assert result["decision_type"] == "skip_assessment_reject"
    decision = db.query(AgentDecision).filter(AgentDecision.id == result["decision_id"]).one()
    assert decision.decision_type == "skip_assessment_reject"


def test_evaluate_policy_then_queue_escalation_creates_pending_human_decision(db):
    """An abstention is a real HITL decision, not a silently dropped verdict."""
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_decision_budget_per_cycle = 1
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Uncertain",
        email="uncertain@x.test",
        taali=61.0,
        pipeline_stage="applied",
    )
    run = _make_agent_run(db, role)
    verdict = PolicyDecision(
        decision_type="escalate_low_confidence",
        confidence=0.42,
        reasoning="The CV and assessment signals disagree sharply.",
        rule_path=["abstention_overlay:sharp_disagreement"],
        decision_point="advance_to_interview",
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(verdict, {}),
    ) as evaluate:
        evaluated = tool_registry.dispatch(
            "evaluate_policy",
            {"application_id": app.id},
            db=db,
            agent_run=run,
            role=role,
        )

    assert evaluated["decision_type"] == "escalate_low_confidence"
    assert (
        evaluate.call_args.kwargs["metering_context"]["require_role_authority"]
        is True
    )
    assert run.__engine_verdicts__[int(app.id)] == "escalate_low_confidence"

    result = tool_registry.dispatch(
        "queue_escalate_decision",
        {
            "application_id": app.id,
            "reasoning": evaluated["reasoning"],
            "evidence": {"rule_path": evaluated["rule_path"]},
            "confidence": evaluated["confidence"],
        },
        db=db,
        agent_run=run,
        role=role,
    )

    assert result["status"] == "pending"
    assert result["decision_type"] == "escalate_low_confidence"
    assert run.decisions_emitted == 1
    decision = db.query(AgentDecision).filter(AgentDecision.id == result["decision_id"]).one()
    assert decision.decision_type == "escalate_low_confidence"
    assert decision.reasoning == verdict.reasoning
    assert float(decision.confidence) == pytest.approx(0.42)
    assert app.pipeline_stage == "applied"
    assert app.application_outcome == "open"

    second_app = _make_application(
        db,
        org=org,
        role=role,
        name="Second",
        email="second-uncertain@x.test",
    )
    blocked = tool_registry.dispatch(
        "queue_escalate_decision",
        {
            "application_id": second_app.id,
            "reasoning": "Another uncertain verdict.",
            "evidence": {},
            "confidence": 0.4,
        },
        db=db,
        agent_run=run,
        role=role,
    )
    assert blocked["status"] == "blocked_by_governance"
    assert "decision budget" in blocked["reason"]


def test_queue_reject_idempotent_within_run(db):
    """Double-call from same run on same app+type should reuse the existing decision."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="X", email="x@x.test", taali=40.0)
    run = _make_agent_run(db, role)

    args = {
        "application_id": app.id,
        "reasoning": "Below threshold.",
        "evidence": {"taali_score": 40},
        "confidence": 0.7,
    }
    first = tool_registry.dispatch(
        "queue_reject_decision", args, db=db, agent_run=run, role=role
    )
    # Commit the first insert so the second call's IntegrityError-driven
    # rollback (in queue_decision.run) doesn't unwind our setup rows. In
    # prod the first call lands in its own transaction.
    db.commit()
    second = tool_registry.dispatch(
        "queue_reject_decision", args, db=db, agent_run=run, role=role
    )
    assert first["decision_id"] == second["decision_id"]


# ---------------------------------------------------------------------------
# Reject approval — the previously-501 path is now wired
# ---------------------------------------------------------------------------


def test_approve_reject_decision_sets_outcome_rejected(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="X", email="x@x.test", taali=40.0)
    run = _make_agent_run(db, role)
    recruiter = _make_recruiter(db, org)

    # Agent queues
    queued = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="reject",
        reasoning="Below threshold.",
        confidence=0.7,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    # Recruiter approves — should now succeed (previously raised 501). The org
    # isn't Workable-connected, so the reject resolves locally with no external
    # calls and Taali sends the candidate no email (job comms belong to the ATS).
    approve_decision.run(
        db,
        Actor.recruiter(recruiter),
        organization_id=int(org.id),
        decision_id=int(queued.id),
    )
    db.flush()

    db.refresh(app)
    assert app.application_outcome == "rejected"

    db.refresh(queued)
    assert queued.status == "approved"
    assert queued.resolved_by_user_id == recruiter.id


def test_approve_skip_assessment_reject_sets_outcome_rejected(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Y", email="y@x.test", taali=20.0,
        pipeline_stage="applied",
    )
    run = _make_agent_run(db, role)
    recruiter = _make_recruiter(db, org)

    queued = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="skip_assessment_reject",
        reasoning="Pre-screen well below cutoff.",
        confidence=0.85,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    approve_decision.run(
        db,
        Actor.recruiter(recruiter),
        organization_id=int(org.id),
        decision_id=int(queued.id),
    )
    db.flush()

    db.refresh(app)
    assert app.application_outcome == "rejected"


# ---------------------------------------------------------------------------
# Human-confirm rail for irreversible auto-reject (TAA-11 / P1-TALI-03)
# ---------------------------------------------------------------------------


def test_auto_reject_role_does_not_disqualify_without_human_confirm(db):
    """With ``role.auto_reject=True`` the agent's queued reject must NOT
    fire the irreversible Workable disqualify. The recommendation is
    recorded and left ``pending`` for a recruiter's one-click confirmation;
    no ``reject_application.run`` (→ disqualify_candidate_in_workable) is
    invoked. (TAA-11 / AUDIT_01 P1-TALI-03.)"""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_reject = True  # opt-in still cannot auto-fire an irreversible reject
    db.flush()
    app = _make_application(db, org=org, role=role, name="Low", email="l@x.test", taali=40.0)
    run = _make_agent_run(db, role)

    # Patch at the tool_registry binding so any auto-execute attempt is caught.
    with patch.object(tool_registry.reject_application, "run") as mock_reject:
        result = tool_registry.dispatch(
            "queue_reject_decision",
            {
                "application_id": app.id,
                "reasoning": "TAALI 40 < threshold; missing Kubernetes.",
                "evidence": {"taali_score": 40},
                "confidence": 0.9,
            },
            db=db,
            agent_run=run,
            role=role,
        )

    # No Workable disqualify side effect.
    assert not mock_reject.called, (
        "auto_reject=True must NOT auto-execute the irreversible Workable "
        "disqualify; it requires explicit human confirmation"
    )
    # The decision is queued, pending, and flagged for human confirmation.
    assert result["status"] == "pending"
    assert result["decision_type"] == "reject"
    assert result["human_confirm_required"] is True

    decision = db.query(AgentDecision).filter(AgentDecision.id == result["decision_id"]).one()
    assert decision.status == "pending"
    assert decision.resolved_at is None

    # The application is untouched — still open, not disqualified.
    db.refresh(app)
    assert app.application_outcome == "open"


def test_auto_reject_role_does_not_disqualify_skip_assessment_reject(db):
    """Same rail for the more impactful skip-assessment reject: a candidate
    cut at the CV/pre-screen stage is just as irreversible, so an
    ``auto_reject`` role must still leave it pending for human confirm."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_reject = True
    db.flush()
    app = _make_application(
        db, org=org, role=role, name="VeryLow", email="vl@x.test", taali=20.0,
        pipeline_stage="applied",
    )
    run = _make_agent_run(db, role)

    with patch.object(tool_registry.reject_application, "run") as mock_reject:
        result = tool_registry.dispatch(
            "queue_skip_assessment_reject_decision",
            {
                "application_id": app.id,
                "reasoning": "CV-match 20; not worth the assessment cost.",
                "evidence": {"cv_match_score": 20},
                "confidence": 0.92,
            },
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_reject.called
    assert result["status"] == "pending"
    assert result["human_confirm_required"] is True
    db.refresh(app)
    assert app.application_outcome == "open"


def test_auto_promote_role_still_auto_executes_advance(db):
    """Control: the rail is reject-specific. A reversible advance under
    ``auto_promote=True`` must still auto-execute (stage move), proving the
    human-confirm rail didn't break the reversible auto path."""
    org = _make_org(db)
    role = _make_role(db, org)  # auto_promote=True via _make_role default
    db.flush()
    app = _make_application(db, org=org, role=role, name="Strong", email="s@x.test", taali=85.0)
    run = _make_agent_run(db, role)
    # The agent evaluated policy first (the normal flow), so the engine verdict
    # is captured and the advance is on-policy — TAA-22's guard lets it auto-execute.
    run.__engine_verdicts__ = {int(app.id): "advance_to_interview"}

    with patch.object(tool_registry, "advance_stage") as mock_advance:
        result = tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": app.id,
                "reasoning": "TAALI 85; strong on all must-haves.",
                "evidence": {"taali_score": 85},
                "confidence": 0.9,
            },
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_advance.run.called, "reversible advance must still auto-execute under auto_promote"
    # advance is not on the human-confirm rail.
    assert result.get("human_confirm_required") is False


def test_auto_execute_decision_refuses_irreversible_reject(db):
    """Defense-in-depth: even if a future caller routes a reject into
    ``_auto_execute_decision`` directly, it refuses rather than firing the
    disqualify, so the rail holds at the side-effect boundary."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="Low", email="l2@x.test", taali=40.0)
    run = _make_agent_run(db, role)

    queued = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="reject",
        reasoning="Below threshold.",
        confidence=0.8,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    with patch.object(tool_registry.reject_application, "run") as mock_reject:
        with pytest.raises(ValueError):
            tool_registry._auto_execute_decision(
                db, role=role, decision=queued, decision_type="reject"
            )
    assert not mock_reject.called


def test_auto_execute_allows_deterministic_full_scoring_reject(db):
    """auto_reject grants only the deterministic post-score policy path."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_reject = True
    app = _make_application(
        db, org=org, role=role, name="Scored low", email="scored-low@x.test",
        taali=25.0,
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="reject",
        reasoning="Deterministic role-fit score is below threshold.",
        evidence={
            "decision_source": "policy",
            "decision_stage": "full_scoring",
            "source": "score_time_decision",
            "effective_threshold": 50.0,
            "has_assessment_task": False,
        },
        confidence=1.0,
        model_version="bulk-deterministic",
        prompt_version="single_threshold_v1",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    def _reject(*_args, **_kwargs):
        app.application_outcome = "rejected"
        return app

    with patch.object(
        tool_registry.reject_application, "run", side_effect=_reject
    ) as mock_reject, patch.object(
        tool_registry, "apply_decision_side_effects"
    ) as apply_side_effects:
        executed = tool_registry._auto_execute_decision(
            db, role=role, decision=decision, decision_type="reject"
        )

    assert executed is True
    assert mock_reject.called
    assert mock_reject.call_args.kwargs["defer_notify"] is True
    assert apply_side_effects.call_args.kwargs["reject_notify"] is True
    assert decision.status == "approved"


@pytest.mark.parametrize("decision_role", ["owner", "related"])
def test_related_ats_link_does_not_force_deterministic_reject_to_hitl(
    db, decision_role
):
    org = _make_org(db)
    owner = _make_role(db, org)
    owner.auto_reject = True
    related = Role(
        organization_id=org.id,
        name="Related backend role",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner.id,
        job_spec_text="Build distributed systems for the related role.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_reject=True,
    )
    db.add(related)
    db.flush()
    role = owner if decision_role == "owner" else related
    related_contract = decision_role == "related"
    decision = MagicMock(
        id=991,
        role_id=int(role.id),
        status="pending",
        evidence={
            "decision_source": "policy",
            "decision_stage": "full_scoring",
            "source": (
                "related_role_runtime" if related_contract else "score_time_decision"
            ),
            "role_state_is_independent": related_contract,
            "related_role_id": int(role.id) if related_contract else None,
            "sister_evaluation_id": 771 if related_contract else None,
        },
        model_version=(
            "related-role-deterministic"
            if related_contract
            else "bulk-deterministic"
        ),
        _just_created=True,
    )

    with patch.object(
        tool_registry, "_auto_execute_decision", return_value=True
    ) as execute:
        outcome = tool_registry.maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="reject",
            on_policy=True,
        )

    assert outcome["executed"] is True
    assert outcome["human_confirm_required"] is False
    assert decision.status == "pending"
    execute.assert_called_once_with(
        db,
        role=role,
        decision=decision,
        decision_type="reject",
        _locked_live_role=role,
        expected_score_generation=None,
    )


def test_auto_reject_does_not_execute_deterministic_assessment_reject(db):
    """The scored toggle stops at the assessment boundary."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_reject = True
    _attach_task(db, org, role)
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Assessment low",
        email="assessment-low@x.test",
        taali=25.0,
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="reject",
        reasoning="Deterministic assessment outcome is below threshold.",
        evidence={
            "decision_source": "policy",
            "decision_stage": "assessment",
            "source": "score_time_decision",
            "effective_threshold": 50.0,
            "has_assessment_task": True,
        },
        confidence=1.0,
        model_version="bulk-deterministic",
        prompt_version="single_threshold_v1",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    with patch.object(tool_registry.reject_application, "run") as mock_reject:
        result = tool_registry.maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="reject",
            on_policy=True,
        )

    assert result["executed"] is False
    assert result["human_confirm_required"] is True
    assert decision.status == "pending"
    mock_reject.assert_not_called()


def test_auto_execute_reloads_live_role_and_holds_after_turn_off(db):
    """A stale in-memory ON role cannot authorize a post-Turn-off side effect."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Concurrent", email="concurrent@x.test"
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong fit",
        confidence=0.95,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    # Bypass identity-map synchronization so ``role`` still says ON while the
    # database row already says OFF, matching a queued worker's stale object.
    db.query(Role).filter(Role.id == role.id).update(
        {"agentic_mode_enabled": False}, synchronize_session=False
    )
    assert role.agentic_mode_enabled is True

    with patch.object(tool_registry.advance_stage, "run") as advance:
        executed = tool_registry._auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="advance_to_interview",
        )

    assert executed is False
    advance.assert_not_called()
    assert decision.status == "pending"
    assert decision.evidence["auto_execute_hold"]["status"] == "role_not_runnable"
    assert "disabled" in decision.evidence["auto_execute_hold"]["detail"]


def test_auto_execute_reloads_decision_and_stops_after_recruiter_claim(db):
    """A stale pending snapshot cannot execute after recruiter processing wins."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Claimed", email="claimed@x.test"
    )
    run = _make_agent_run(db, role)
    generation = _score_generation(db, role, app)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong fit",
        confidence=0.95,
        model_version="bulk-deterministic",
        prompt_version="single_threshold_v1",
        expected_score_generation=generation,
    )
    db.flush()

    # Simulate another committed transaction claiming the row while this
    # worker retains its old pending instance in the identity map.
    db.query(AgentDecision).filter(AgentDecision.id == decision.id).update(
        {"status": "processing"}, synchronize_session=False
    )
    assert decision.status == "pending"

    with patch.object(tool_registry.advance_stage, "run") as advance:
        outcome = tool_registry.maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="advance_to_interview",
            expected_score_generation=generation,
        )

    assert outcome["executed"] is False
    assert outcome["action_held"] is True
    advance.assert_not_called()
    db.refresh(decision)
    assert decision.status == "processing"


def test_auto_execute_reloads_fresh_decision_and_still_executes(db):
    """The live-row fence preserves the normal just-created auto path."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Fresh", email="fresh@x.test"
    )
    run = _make_agent_run(db, role)
    generation = _score_generation(db, role, app)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong fit",
        confidence=0.95,
        model_version="bulk-deterministic",
        prompt_version="single_threshold_v1",
        expected_score_generation=generation,
    )

    with patch.object(tool_registry.advance_stage, "run") as advance, patch.object(
        tool_registry, "apply_decision_side_effects"
    ):
        outcome = tool_registry.maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="advance_to_interview",
            expected_score_generation=generation,
        )

    assert outcome["executed"] is True
    advance.assert_called_once()
    assert decision.status == "approved"


def test_auto_execute_holds_when_newer_score_attempt_supersedes_older_done(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Stale score", email="stale-score@x.test"
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Older score cleared the bar.",
        confidence=0.95,
        model_version="bulk-deterministic",
        prompt_version="single_threshold_v1",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.add_all(
        [
            CvScoreJob(
                application_id=int(app.id),
                role_id=int(role.id),
                status="done",
                queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            CvScoreJob(
                application_id=int(app.id),
                role_id=int(role.id),
                status="stale",
                queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            ),
        ]
    )
    db.flush()

    with patch.object(tool_registry.advance_stage, "run") as advance:
        executed = tool_registry._auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="advance_to_interview",
        )

    assert executed is False
    advance.assert_not_called()
    assert decision.status == "pending"
    assert decision.evidence["auto_execute_hold"]["status"] == "score_refresh_required"


def test_auto_execute_discards_decision_after_application_resolves(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Resolved", email="resolved@x.test"
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong fit",
        confidence=0.95,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    app.application_outcome = "hired"
    db.flush()

    with patch.object(tool_registry.advance_stage, "run") as advance:
        executed = tool_registry._auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="advance_to_interview",
        )

    assert executed is False
    advance.assert_not_called()
    assert decision.status == "discarded"
    assert "resolved" in (decision.resolution_note or "")


def test_superseded_assessment_task_result_cannot_auto_advance(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Old Task", email="old-task@x.test"
    )
    old_task = Task(
        organization_id=org.id,
        name="Old generated task",
        is_active=True,
        extra_data={"generated": True},
    )
    replacement = Task(
        organization_id=org.id,
        name="Replacement task",
        is_active=True,
        extra_data={"generated": True},
    )
    db.add_all([old_task, replacement])
    db.flush()
    role.tasks.append(old_task)
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=app.candidate_id,
        application_id=app.id,
        role_id=role.id,
        task_id=old_task.id,
        token="superseded-result",
        status="completed",
        assessment_score=92.0,
    )
    db.add(assessment)
    db.flush()
    old_task.is_active = False
    role.tasks.remove(old_task)
    role.tasks.append(replacement)
    db.flush()
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Old assessment scored highly",
        confidence=0.95,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    with patch.object(tool_registry.advance_stage, "run") as advance:
        executed = tool_registry._auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="advance_to_interview",
        )

    assert executed is False
    advance.assert_not_called()
    hold = decision.evidence["auto_execute_hold"]
    assert hold["status"] == "superseded_assessment_task"
    assert hold["assessment_id"] == assessment.id
    assert hold["task_id"] == old_task.id


def test_auto_send_noop_stays_pending_instead_of_false_approval(db):
    """A misconfigured/credit-blocked send result is not an approval: no
    candidate invite exists, so the deterministic card must remain recoverable."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Strong", email="held@x.test", taali=90.0
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="send_assessment",
        reasoning="Strong fit",
        confidence=0.95,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    blocked = MagicMock(status="misconfigured", detail="no active task")
    with patch.object(tool_registry.send_assessment, "run", return_value=blocked):
        outcome = tool_registry.maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="send_assessment",
        )

    assert outcome["executed"] is False
    assert outcome["action_held"] is True
    assert decision.status == "pending"
    assert decision.human_disposition is None
    assert decision.evidence["auto_execute_hold"]["status"] == "misconfigured"


def test_auto_action_exception_rolls_back_partial_candidate_mutations(db):
    """A caller may continue/commit the cohort after one action fails.  The
    shared auto-execute boundary must therefore roll back action-local writes
    while preserving the queued decision as a retryable hold."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Strong", email="rollback@x.test"
    )
    run = _make_agent_run(db, role)
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="send_assessment",
        reasoning="Strong fit",
        confidence=0.95,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    db.flush()

    def _partial_send(*_args, **_kwargs):
        app.pipeline_stage = "invited"
        db.add(app)
        db.flush()
        raise RuntimeError("invite broker unavailable")

    with patch.object(tool_registry.send_assessment, "run", side_effect=_partial_send):
        outcome = tool_registry.maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type="send_assessment",
        )

    # Mirror the outer cohort's eventual commit boundary; the hold is a normal
    # pending ORM update while the partial action itself was rolled back.
    db.flush()
    db.refresh(app)
    db.refresh(decision)
    assert outcome["executed"] is False
    assert outcome["action_held"] is True
    assert app.pipeline_stage == "review"
    assert decision.status == "pending"
    assert decision.evidence["auto_execute_hold"] == {
        "status": "action_error",
        "detail": "invite broker unavailable",
    }


# ---------------------------------------------------------------------------
# Off-policy auto-execution guard (TAA-22 / AUDIT_02 P2-TALI-01)
# ---------------------------------------------------------------------------


def test_is_on_policy_helper():
    """The pure guard: hire-relevant types must match the captured engine
    verdict; operational types are exempt; missing/mismatched fails safe."""
    run = MagicMock()
    run.__engine_verdicts__ = {7: "advance_to_interview"}
    # advance matches the captured engine verdict -> on-policy
    assert tool_registry._is_on_policy(run, 7, "advance_to_interview") == (True, "advance_to_interview")
    # advance with NO captured verdict -> fail safe (off-policy)
    assert tool_registry._is_on_policy(run, 99, "advance_to_interview") == (False, None)
    # advance vs a captured reject verdict -> off-policy
    run.__engine_verdicts__ = {8: "reject"}
    assert tool_registry._is_on_policy(run, 8, "advance_to_interview")[0] is False
    # reject / send / resend are exempt (human-confirm or operational) -> on-policy
    assert tool_registry._is_on_policy(run, 8, "reject") == (True, None)
    assert tool_registry._is_on_policy(run, 99, "resend_assessment_invite") == (True, None)


def test_off_policy_advance_is_withheld_from_auto_execute(db):
    """With ``auto_promote=True`` but the deterministic engine verdict for the
    application being ``reject``, an agent that queues ``advance_to_interview``
    must NOT auto-execute the stage move — it is off-policy and routes to human
    review. (TAA-22 / AUDIT_02 P2-TALI-01.)"""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = True
    db.flush()
    app = _make_application(db, org=org, role=role, name="Mid", email="m@x.test", taali=55.0)
    run = _make_agent_run(db, role)
    # The engine said reject this cycle; the LLM is trying to advance.
    run.__engine_verdicts__ = {int(app.id): "reject"}

    with patch.object(tool_registry.advance_stage, "run") as mock_advance:
        result = tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": app.id,
                "reasoning": "looks strong",
                "evidence": {"taali_score": 55},
                "confidence": 0.8,
            },
            db=db, agent_run=run, role=role,
        )

    assert not mock_advance.called, "off-policy advance must not auto-execute"
    assert result["off_policy_withheld"] is True
    assert result["status"] == "pending"


def test_on_policy_advance_auto_executes(db):
    """When the queued type matches the engine verdict, auto_promote still
    auto-executes (the guard only blocks off-policy)."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = True
    db.flush()
    app = _make_application(db, org=org, role=role, name="Strong", email="s@x.test", taali=88.0)
    run = _make_agent_run(db, role)
    run.__engine_verdicts__ = {int(app.id): "advance_to_interview"}

    with patch.object(tool_registry.advance_stage, "run") as mock_advance:
        result = tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": app.id,
                "reasoning": "clear advance",
                "evidence": {"taali_score": 88},
                "confidence": 0.95,
            },
            db=db, agent_run=run, role=role,
        )

    assert mock_advance.called, "on-policy advance with auto_promote should auto-execute"
    assert result["off_policy_withheld"] is False


def test_evaluate_policy_then_advance_auto_executes_end_to_end(db):
    """End-to-end regression for the TAA-22 vocabulary bug. Drives the REAL
    capture path (evaluate_policy populating __engine_verdicts__) instead of
    hand-seeding the dict, then queues the advance. The deterministic engine
    emits the VERB 'queue_advance_decision'; capture MUST translate it to the
    persisted NOUN 'advance_to_interview' (the value the queue tool carries) or
    _is_on_policy never matches and every on-policy advance is wrongly withheld.
    Before the fix the raw verb was stored and this test would fail at both the
    capture assertion and the auto-execute assertion."""
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = True
    db.flush()
    app = _make_application(db, org=org, role=role, name="Strong", email="s2@x.test", taali=88.0)
    app.cv_match_details = {"summary": "Strong production backend fit. Longer supporting analysis."}
    run = _make_agent_run(db, role)

    # The deterministic engine's advance verdict is the VERB (engine.py emits
    # decision_type = rule.then = 'queue_advance_decision').
    engine_verdict = PolicyDecision(
        decision_type="queue_advance_decision",
        confidence=0.95,
        reasoning="clears advance threshold",
        rule_path=["advance_rule"],
        decision_point="advance_to_interview",
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(engine_verdict, {}),
    ):
        tool_registry.dispatch(
            "evaluate_policy", {"application_id": app.id},
            db=db, agent_run=run, role=role,
        )

    # Capture must hold the persisted NOUN, not the engine verb.
    assert run.__engine_verdicts__[int(app.id)] == "advance_to_interview", (
        "evaluate_policy must translate the engine verb 'queue_advance_decision' "
        "to the persisted noun 'advance_to_interview' (TAA-22 vocab bug)"
    )

    with patch.object(tool_registry.advance_stage, "run") as mock_advance:
        result = tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": app.id,
                "reasoning": "clear advance",
                "evidence": {"taali_score": 88},
                "confidence": 0.95,
            },
            db=db, agent_run=run, role=role,
        )

    assert mock_advance.called, (
        "on-policy advance captured via the real evaluate_policy path must auto-execute"
    )
    assert result["off_policy_withheld"] is False
    decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(app.id))
        .order_by(AgentDecision.id.desc())
        .first()
    )
    assert decision.evidence["decision_source"] == "policy"
    assert decision.evidence["source"] == "agent_runtime_policy"
    assert decision.evidence["engine_verdict"] == "queue_advance_decision"
    assert decision.evidence["policy_reasoning"] == "clears advance threshold"
    assert decision.evidence["rule_path"] == ["advance_rule"]
    assert decision.evidence["candidate_summary"] == (
        "Strong production backend fit. Longer supporting analysis."
    )


@pytest.mark.parametrize(
    ("assessment_output", "expected_stage"),
    [
        (
            {
                "assessment_completed": True,
                "assessment_score": None,
                "taali_score": 81.0,
            },
            "assessment",
        ),
        (
            {
                "assessment_completed": False,
                "assessment_score": 72.0,
                "taali_score": 81.0,
            },
            "assessment",
        ),
        (
            {
                "assessment_completed": False,
                "assessment_score": None,
                "taali_score": None,
            },
            "full_scoring",
        ),
    ],
)
def test_policy_snapshot_owns_decision_stage_from_assessment_output(
    db, assessment_output, expected_stage
):
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Stage provenance",
        email=f"stage-{expected_stage}-{id(assessment_output)}@x.test",
        taali=81.0,
    )
    result = MagicMock(ok=True, output=assessment_output)
    verdict = PolicyDecision(
        decision_type="queue_advance_decision",
        confidence=0.9,
        reasoning="policy result",
        rule_path=["advance_rule"],
        decision_point="advance_to_interview",
    )

    snapshot = tool_registry._policy_snapshot_for_evaluation(
        db,
        role=role,
        application_id=int(app.id),
        verdict=verdict,
        sub_outputs={"assessment_scoring": result},
        persisted_decision_type="advance_to_interview",
    )

    assert snapshot["decision_stage"] == expected_stage


def test_queue_evidence_reserves_decision_stage_from_model_input(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Reserved stage",
        email="reserved-stage@x.test",
        taali=81.0,
    )
    run = _make_agent_run(db, role)
    run.__engine_policy_snapshots__ = {
        int(app.id): {
            "_persisted_decision_type": "advance_to_interview",
            "decision_stage": "full_scoring",
        }
    }

    evidence = tool_registry._queue_evidence(
        db,
        agent_run=run,
        role=role,
        application_id=int(app.id),
        decision_type="advance_to_interview",
        supplied={"decision_stage": "assessment", "role_fit_score": 81.0},
    )

    assert evidence["decision_stage"] == "full_scoring"


def test_evaluate_policy_generation_cannot_queue_after_newer_done_score(db):
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="Generation A", email="generation-a@x.test",
        taali=88.0,
    )
    run = _make_agent_run(db, role)
    verdict = PolicyDecision(
        decision_type="queue_advance_decision",
        confidence=0.95,
        reasoning="generation A clears the threshold",
        rule_path=["advance_rule"],
        decision_point="advance_to_interview",
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(verdict, {}),
    ):
        tool_registry.dispatch(
            "evaluate_policy", {"application_id": int(app.id)},
            db=db, agent_run=run, role=role,
        )

    captured = run.__engine_policy_snapshots__[int(app.id)]["_score_generation"]
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
            queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    db.flush()

    with patch.object(tool_registry.advance_stage, "run") as advance:
        with pytest.raises(HTTPException) as exc:
            tool_registry.dispatch(
                "queue_advance_decision",
                {
                    "application_id": int(app.id),
                    "reasoning": "generation A verdict",
                    "evidence": {"taali_score": 88},
                    "confidence": 0.95,
                },
                db=db, agent_run=run, role=role,
            )

    assert exc.value.status_code == 409
    assert captured.job_id is not None
    advance.assert_not_called()
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(app.id))
        .count()
        == 0
    )


def test_evaluate_policy_forced_refresh_queues_durable_score_without_subagents(
    db, monkeypatch
):
    from app.services import cv_score_orchestrator

    monkeypatch.setattr(
        cv_score_orchestrator.settings, "ANTHROPIC_API_KEY", "test-api-key"
    )
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Durable refresh",
        email="durable-refresh@x.test",
        taali=88.0,
    )
    app.cv_text = "Senior backend engineer with production Python experience."
    app.genuine_pre_screen_score_100 = 80.0
    app.pre_screen_score_100 = 80.0
    app.pre_screen_run_at = datetime.now(timezone.utc)
    db.flush()
    run = _make_agent_run(db, role)
    run.__engine_verdicts__ = {int(app.id): "send_assessment"}

    broker_receipt = MagicMock(id="durable-score-task")
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        side_effect=AssertionError("forced refresh must not run ephemeral subagents"),
    ) as evaluator, patch(
        "app.tasks.scoring_tasks.score_application_job.delay",
        return_value=broker_receipt,
    ) as dispatch_score:
        first = tool_registry.dispatch(
            "evaluate_policy",
            {"application_id": int(app.id), "skip_cache": True},
            db=db,
            agent_run=run,
            role=role,
        )

        assert first["decision_type"] == "score_refresh_queued"
        assert first["status"] == "pending"
        assert int(app.id) not in run.__engine_policy_snapshots__
        assert int(app.id) not in run.__engine_verdicts__
        evaluator.assert_not_called()
        dispatch_score.assert_called_once()

        with pytest.raises(HTTPException) as exc:
            tool_registry.dispatch(
                "queue_advance_decision",
                {
                    "application_id": int(app.id),
                    "reasoning": "must not use the pre-refresh verdict",
                    "confidence": 0.95,
                },
                db=db,
                agent_run=run,
                role=role,
            )
        assert exc.value.status_code == 409

        second = tool_registry.dispatch(
            "evaluate_policy",
            {"application_id": int(app.id), "skip_cache": True},
            db=db,
            agent_run=run,
            role=role,
        )

    assert second["decision_type"] == "score_refresh_pending"
    assert second["score_job_id"] == first["score_job_id"]
    assert (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.application_id == int(app.id),
            CvScoreJob.status == "pending",
        )
        .count()
        == 1
    )
    dispatch_score.assert_called_once()


def test_evaluate_policy_forced_refresh_pause_preserves_existing_scores(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=role,
        name="Paused refresh",
        email="paused-refresh@x.test",
        taali=88.0,
    )
    app.genuine_pre_screen_score_100 = 80.0
    app.pre_screen_score_100 = 80.0
    app.pre_screen_run_at = datetime.now(timezone.utc)
    run = _make_agent_run(db, role)
    db.commit()

    ConcurrentSession = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.get_bind(),
    )
    with ConcurrentSession() as concurrent_db:
        concurrent_role = concurrent_db.get(Role, int(role.id))
        assert concurrent_role is not None
        concurrent_role.agent_paused_at = datetime.now(timezone.utc)
        concurrent_db.commit()

    db.expire_all()
    with (
        patch("app.tasks.scoring_tasks.score_application_job.delay") as dispatch_score,
        pytest.raises(AutomaticProviderAuthorityError, match="paused"),
    ):
        tool_registry.dispatch(
            "evaluate_policy",
            {"application_id": int(app.id), "skip_cache": True},
            db=db,
            agent_run=run,
            role=role,
        )

    db.rollback()
    preserved = db.get(CandidateApplication, int(app.id))
    assert preserved is not None
    assert preserved.pre_screen_score_100 == 80.0
    assert preserved.genuine_pre_screen_score_100 == 80.0
    assert (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.application_id == int(app.id),
            CvScoreJob.status.in_(("pending", "running")),
        )
        .count()
        == 0
    )
    dispatch_score.assert_not_called()


def test_standard_queue_tool_refuses_missing_server_generation_snapshot(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role, name="No evaluation", email="no-eval@x.test",
        taali=88.0,
    )
    run = _make_agent_run(db, role)
    run.__engine_policy_snapshots__ = {}

    with pytest.raises(HTTPException) as exc:
        tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": int(app.id),
                "reasoning": "attempted without evaluation",
                "evidence": {"taali_score": 88},
                "confidence": 0.95,
            },
            db=db, agent_run=run, role=role,
        )

    assert exc.value.status_code == 409
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(app.id))
        .count()
        == 0
    )


def test_evaluate_policy_escalated_verdict_withholds_advance(db):
    """The escalation noun is captured, but never blesses an auto-advance."""
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = True
    db.flush()
    app = _make_application(db, org=org, role=role, name="Borderline", email="b2@x.test", taali=60.0)
    run = _make_agent_run(db, role)

    escalated = PolicyDecision(
        decision_type="escalate_low_confidence",
        confidence=0.4,
        reasoning="sub-agents disagree",
        rule_path=["abstention_overlay:disagreement"],
        decision_point="advance_to_interview",
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(escalated, {}),
    ):
        tool_registry.dispatch(
            "evaluate_policy", {"application_id": app.id},
            db=db, agent_run=run, role=role,
        )

    assert run.__engine_verdicts__[int(app.id)] == "escalate_low_confidence", (
        "an escalation must remain queueable while withholding an advance blessing"
    )

    with patch.object(tool_registry.advance_stage, "run") as mock_advance:
        result = tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": app.id,
                "reasoning": "try advance",
                "evidence": {
                    "taali_score": 60,
                    "decision_source": "policy",
                    "decision_trigger": "must_have_blocked",
                    "decision_factors": [{"label": "Invented blocker", "status": "missing"}],
                },
                "confidence": 0.6,
            },
            db=db, agent_run=run, role=role,
        )

    assert not mock_advance.called, "escalated verdict must not auto-execute an advance"
    assert result["off_policy_withheld"] is True
    decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(app.id))
        .order_by(AgentDecision.id.desc())
        .first()
    )
    assert "decision_source" not in decision.evidence
    assert "decision_trigger" not in decision.evidence
    assert "decision_factors" not in decision.evidence


def test_evaluate_policy_must_have_reject_freezes_authoritative_factors(db):
    """A matching hard-rule reject carries server-derived factors; the model
    cannot replace them with its own policy story."""
    from app.decision_policy.engine import PolicyDecision
    from app.models.decision_policy import DecisionPolicy
    from app.models.rubric_revision import RubricRevision

    org = _make_org(db)
    role = _make_role(db, org)
    db.add_all(
        [
            RubricRevision(
                id=42,
                organization_id=int(org.id),
                role_id=None,
                cause="human_edit",
                feedback_ids=[],
            ),
            DecisionPolicy(
                id=42,
                organization_id=int(org.id),
                role_id=None,
                revision_id=42,
                policy_json={},
                activated_at=datetime.now(timezone.utc),
            ),
        ]
    )
    db.flush()
    app = _make_application(
        db, org=org, role=role, name="Blocked", email="blocked@x.test", taali=72.0
    )
    app.cv_match_score = 72.0
    app.cv_match_details = {
        "summary": "Strong dimensional modelling background. Longer analysis.",
        "requirements_assessment": [
            {
                "requirement_id": "criterion_12",
                "criterion_text": "Production knowledge graph development",
                "priority": "must_have",
                "status": "missing",
            }
        ],
    }
    run = _make_agent_run(db, role)
    engine_verdict = PolicyDecision(
        decision_type="queue_reject_decision",
        confidence=1.0,
        reasoning="A configured must-have requirement is blocked.",
        rule_path=["point:send_assessment", "rule:fired:must_have_blocked"],
        decision_point="send_assessment",
        policy_revision_id=42,
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(engine_verdict, {}),
    ):
        tool_registry.dispatch(
            "evaluate_policy", {"application_id": app.id},
            db=db, agent_run=run, role=role,
        )

    tool_registry.dispatch(
        "queue_reject_decision",
        {
            "application_id": app.id,
            "reasoning": "model copy",
            "evidence": {
                "decision_source": "agent",
                "decision_factors": [{"label": "Invented", "status": "unknown"}],
            },
            "confidence": 1.0,
        },
        db=db, agent_run=run, role=role,
    )
    decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(app.id))
        .order_by(AgentDecision.id.desc())
        .first()
    )
    assert decision.evidence["decision_source"] == "policy"
    assert decision.evidence["decision_trigger"] == "must_have_blocked"
    assert decision.evidence["policy_revision_id"] == 42
    assert decision.evidence["decision_factors"] == [
        {
            "label": "Production knowledge graph development",
            "status": "missing",
            "priority": "must_have",
        }
    ]


def test_evaluate_policy_no_task_send_verdict_captures_as_advance(db):
    """The no-assessment-task switch must hold on the CAPTURE side too. When the
    engine emits 'queue_send_assessment' for a role with NO task, the persisted
    decision is 'advance_to_interview' (nothing to send -> straight to interview),
    so the captured engine verdict must also be 'advance_to_interview' — otherwise
    a legitimate no-task advance reads as off-policy. This is precisely the case a
    static verb->noun map would mis-handle, which is why capture goes through
    resolve_persisted_decision_type (context-aware on has_assessment_task)."""
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    db.flush()
    assert not getattr(role, "tasks", None), "precondition: role has no assessment task"
    app = _make_application(db, org=org, role=role, name="NoTask", email="nt@x.test", taali=85.0)
    run = _make_agent_run(db, role)

    send_verdict = PolicyDecision(
        decision_type="queue_send_assessment",
        confidence=0.9,
        reasoning="strong candidate; role has no task to send",
        rule_path=["send_rule"],
        decision_point="send_assessment",
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(send_verdict, {}),
    ):
        tool_registry.dispatch(
            "evaluate_policy", {"application_id": app.id},
            db=db, agent_run=run, role=role,
        )

    assert run.__engine_verdicts__[int(app.id)] == "advance_to_interview", (
        "no-task send_assessment verdict must capture as advance_to_interview"
    )
    assert tool_registry._is_on_policy(run, int(app.id), "advance_to_interview")[0] is True


def test_evaluate_policy_skip_toggle_send_verdict_captures_as_advance(db):
    """Same capture-side switch for auto_skip_assessment: the role HAS a task,
    but the recruiter toggled the assessment stage off, so a
    'queue_send_assessment' engine verdict must capture as
    'advance_to_interview' — otherwise the skip-toggled advance reads as
    off-policy and is wrongly withheld."""
    from app.decision_policy.engine import PolicyDecision

    org = _make_org(db)
    role = _make_role(db, org)
    task = Task(organization_id=org.id, name="Take-home")
    db.add(task)
    db.flush()
    role.tasks.append(task)
    role.auto_skip_assessment = True
    db.flush()
    app = _make_application(db, org=org, role=role, name="SkipTask", email="st@x.test", taali=85.0)
    run = _make_agent_run(db, role)

    send_verdict = PolicyDecision(
        decision_type="queue_send_assessment",
        confidence=0.9,
        reasoning="strong candidate; assessments skipped on this role",
        rule_path=["send_rule"],
        decision_point="send_assessment",
    )
    with patch.object(
        tool_registry.policy_evaluator,
        "evaluate_for_application",
        return_value=(send_verdict, {}),
    ):
        tool_registry.dispatch(
            "evaluate_policy", {"application_id": app.id},
            db=db, agent_run=run, role=role,
        )

    assert run.__engine_verdicts__[int(app.id)] == "advance_to_interview", (
        "skip-toggled send_assessment verdict must capture as advance_to_interview"
    )
    assert tool_registry._is_on_policy(run, int(app.id), "advance_to_interview")[0] is True
