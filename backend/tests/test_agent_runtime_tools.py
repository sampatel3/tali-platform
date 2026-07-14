"""Direct unit tests for the agent runtime's tool registry + reject flow.

These bypass the Anthropic loop and call ``tool_registry.dispatch``
directly with a synthetic AgentRun. They cover:
- the new read tools (search_applications, compare_applications,
  nl_search_candidates, get_candidate)
- the new queue tools (queue_reject_decision,
  queue_skip_assessment_reject_decision)
- the reject side-effect wired into approve_decision
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.actions import approve_decision, queue_decision
from app.actions.types import Actor
from app.agent_runtime import tool_registry
from app.candidate_search.schemas import ParsedFilter, SearchOutput
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User


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
    return run


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
        "graph_search_candidates",
        "get_cohort_signals",
        # execute
        "score_cv",
        "send_assessment",
        "resend_assessment_invite",
        "create_application",
        "post_workable_note",
        # queue
        "queue_advance_decision",
        "queue_reject_decision",
        "queue_skip_assessment_reject_decision",
        # terminal
        "agent_run_complete",
    }.issubset(names)


def test_default_role_tools_hide_legacy_mutations(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_action_allowlist = None
    names = {tool["name"] for tool in tool_registry.tools_for_role(role)}
    assert "create_application" not in names
    assert "post_workable_note" not in names
    assert "refresh_candidate_graph" not in names
    assert "get_application" in names
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


def test_post_workable_note_dispatch_invokes_action(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, name="X", email="x@x.test")
    run = _make_agent_run(db, role)

    fake_result = type(
        "_R",
        (),
        {
            "as_dict": lambda self: {
                "application_id": app.id,
                "status": "posted",
                "detail": None,
            }
        },
    )()
    with patch(
        "app.actions.post_workable_note.run", return_value=fake_result
    ) as mock_run:
        result = tool_registry.dispatch(
            "post_workable_note",
            {"application_id": app.id, "body": "Agent flagged this candidate."},
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["application_id"] == app.id
    assert kwargs["body"] == "Agent flagged this candidate."
    actor = mock_run.call_args.args[1]
    assert actor.type == "agent"
    assert result["status"] == "posted"


def test_post_workable_note_dispatch_refuses_cross_role(db):
    """Regression: an agent running for role A must not post a Workable
    note on an application that belongs to role B in the same org.
    (Codex P2 follow-up on #141.)
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
    app_for_b = _make_application(db, org=org, role=role_b, name="B", email="b@x.test")
    run_a = _make_agent_run(db, role_a)

    with patch("app.actions.post_workable_note.run") as mock_action:
        result = tool_registry.dispatch(
            "post_workable_note",
            {"application_id": int(app_for_b.id), "body": "leaked note"},
            db=db,
            agent_run=run_a,
            role=role_a,
        )

    assert result["status"] == "wrong_role"
    assert result["application_id"] == int(app_for_b.id)
    assert not mock_action.called


def test_post_workable_note_dispatch_returns_not_found_for_unknown_id(db):
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)

    with patch("app.actions.post_workable_note.run") as mock_action:
        result = tool_registry.dispatch(
            "post_workable_note",
            {"application_id": 999999, "body": "x"},
            db=db,
            agent_run=run,
            role=role,
        )

    assert result["status"] == "not_found"
    assert not mock_action.called


def test_create_application_dispatch_refuses_cross_role(db):
    """An agent running for role A must not create an application under
    role B in the same org. Same single-role-execution-boundary that
    resend_assessment_invite and post_workable_note enforce.
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


def test_dispatch_unknown_tool_raises():
    with pytest.raises(KeyError):
        tool_registry.dispatch("bogus_tool", {}, db=None, agent_run=None, role=None)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def test_search_applications_dispatch_returns_role_scoped_results(db):
    org = _make_org(db)
    role = _make_role(db, org)
    other_role = Role(organization_id=org.id, name="Other", source="manual")
    db.add(other_role)
    db.flush()
    a1 = _make_application(db, org=org, role=role, name="High", email="h@x.test", taali=85.0)
    a2 = _make_application(db, org=org, role=role, name="Mid", email="m@x.test", taali=60.0)
    _make_application(db, org=org, role=other_role, name="Other Role", email="o@x.test", taali=99.0)
    run = _make_agent_run(db, role)

    result = tool_registry.dispatch(
        "search_applications",
        {"min_score": 70.0, "limit": 10},
        db=db,
        agent_run=run,
        role=role,
    )

    ids = [r["application_id"] for r in result]
    assert a1.id in ids
    assert a2.id not in ids  # below threshold


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
    assert result["total_matched"] == 1
    assert result["applications"][0]["application_id"] == a1.id


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


def test_graph_search_candidates_returns_warning_when_unconfigured(db):
    """Without NEO4J creds the tool should degrade gracefully, not crash."""
    org = _make_org(db)
    role = _make_role(db, org)
    run = _make_agent_run(db, role)

    with patch(
        "app.candidate_graph.client.is_configured", return_value=False
    ):
        result = tool_registry.dispatch(
            "graph_search_candidates",
            {"query": "stripe"},
            db=db,
            agent_run=run,
            role=role,
        )

    assert result["applications"] == []
    assert any(w["code"] == "neo4j_unavailable" for w in result["warnings"])


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
    assert result["status"] == "ok"
    assert result["episodes_sent"] == 4
    assert result["application_id"] == int(app.id)


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
    )
    db.flush()

    with patch.object(tool_registry.reject_application, "run") as mock_reject:
        with pytest.raises(ValueError):
            tool_registry._auto_execute_decision(
                db, role=role, decision=queued, decision_type="reject"
            )
    assert not mock_reject.called


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


def test_evaluate_policy_escalated_verdict_withholds_advance(db):
    """When the engine escalates (low confidence), capture stores None (the
    verdict is not a queueable noun), so a subsequent auto_promote advance is
    withheld and routed to human review. Locks the fail-safe through the real
    capture path — an escalated verdict must never auto-advance."""
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

    assert run.__engine_verdicts__[int(app.id)] is None, (
        "a non-queueable/escalated verdict must capture as None (no advance blessing)"
    )

    with patch.object(tool_registry.advance_stage, "run") as mock_advance:
        result = tool_registry.dispatch(
            "queue_advance_decision",
            {
                "application_id": app.id,
                "reasoning": "try advance",
                "evidence": {"taali_score": 60},
                "confidence": 0.6,
            },
            db=db, agent_run=run, role=role,
        )

    assert not mock_advance.called, "escalated verdict must not auto-execute an advance"
    assert result["off_policy_withheld"] is True


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
