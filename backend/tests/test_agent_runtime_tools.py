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
from unittest.mock import patch

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
    )
    db.add(role)
    db.flush()
    return role


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


def test_resend_assessment_invite_dispatch_hitl_gate_opens_needs_input(db):
    """When auto_promote=False the tool opens an ask_recruiter card instead
    of calling the action."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    assessment = _make_assessment(db, org=org, role=role, app=app)
    run = _make_agent_run(db, role)

    with patch(
        "app.actions.resend_assessment_invite.run"
    ) as mock_action, patch(
        "app.actions.ask_recruiter.open",
        return_value=type("_R", (), {"id": 777})(),
    ) as mock_ask:
        result = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_action.called
    assert mock_ask.called
    assert mock_ask.call_args.kwargs["kind"] == "resend_assessment_invite_approval"
    assert result["status"] == "awaiting_recruiter_approval"
    assert result["needs_input_id"] == 777
    assert result["assessment_id"] == int(assessment.id)


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


def test_resend_assessment_invite_dispatch_consumes_prior_approval(db):
    """HITL approval loop fix (Codex P1 follow-up on #141).

    Sequence: ``auto_promote=False`` role, recruiter previously
    approved a resend card targeting this assessment_id via the
    ``subject_id`` column. Next dispatch must run the action (not
    open a new card) and mark the approval consumed.
    """
    from datetime import datetime, timezone

    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    assessment = _make_assessment(db, org=org, role=role, app=app)
    run = _make_agent_run(db, role)

    approved = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="resend_assessment_invite_approval",
        subject_id=int(assessment.id),
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "approve"},
    )
    db.add(approved)
    db.flush()

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
    assert result["status"] == "resent"
    db.refresh(approved)
    assert approved.dismissed_at is not None


def test_resend_assessment_invite_dispatch_recruiter_declined_when_prior_card_skipped(db):
    from datetime import datetime, timezone

    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    assessment = _make_assessment(db, org=org, role=role, app=app)
    run = _make_agent_run(db, role)

    declined = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="resend_assessment_invite_approval",
        subject_id=int(assessment.id),
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "skip"},
    )
    db.add(declined)
    db.flush()

    with patch(
        "app.actions.resend_assessment_invite.run"
    ) as mock_run, patch(
        "app.actions.ask_recruiter.open"
    ) as mock_ask_open:
        result = tool_registry.dispatch(
            "resend_assessment_invite",
            {"assessment_id": int(assessment.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_run.called
    assert not mock_ask_open.called
    assert result["status"] == "recruiter_declined"
    assert result["assessment_id"] == int(assessment.id)


def test_send_assessment_dispatch_consumes_prior_approval(db):
    """Same fix applied symmetrically to send_assessment. Codex didn't
    flag this one because it was latent (no test exercised the HITL
    path), but the bug shape was identical."""
    from datetime import datetime, timezone

    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test", taali=80.0)
    run = _make_agent_run(db, role)

    approved = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="send_assessment_approval",
        subject_id=int(app.id),
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "approve"},
    )
    db.add(approved)
    db.flush()

    fake_result = type(
        "_R",
        (),
        {"as_dict": lambda self: {"assessment_id": 99, "status": "sent", "detail": None}},
    )()
    with patch(
        "app.actions.send_assessment.run", return_value=fake_result
    ) as mock_run:
        result = tool_registry.dispatch(
            "send_assessment",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_run.called
    assert result["status"] == "sent"
    db.refresh(approved)
    assert approved.dismissed_at is not None


def test_send_assessment_dispatch_hitl_opens_new_card_when_no_prior_approval(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    app = _make_application(db, org=org, role=role, name="A", email="a@x.test")
    run = _make_agent_run(db, role)

    with patch(
        "app.actions.send_assessment.run"
    ) as mock_run, patch(
        "app.actions.ask_recruiter.open",
        return_value=type("_R", (), {"id": 555})(),
    ) as mock_ask:
        result = tool_registry.dispatch(
            "send_assessment",
            {"application_id": int(app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_run.called
    assert mock_ask.called
    # subject_id is passed through so per-candidate dedupe works.
    assert mock_ask.call_args.kwargs["subject_id"] == int(app.id)
    assert result["status"] == "awaiting_recruiter_approval"
    assert result["needs_input_id"] == 555


def test_consume_resolved_skips_approval_for_different_subject(db):
    """Cross-target safety via subject_id (NOT prompt substring): a
    prior approval for application 12346 must not be consumed when the
    agent is processing application 1, even if application 1's id is a
    substring of '12346'. Codex's second P1 on this PR was that the
    earlier ``target_marker`` approach filtered the chosen row in
    Python after ``first()``, so a later approval for a different
    subject masked an earlier valid one. ``subject_id`` lifts the
    discriminator into the SQL query so this can't recur.
    """
    from datetime import datetime, timezone

    org = _make_org(db)
    role = _make_role(db, org)
    role.auto_promote = False
    db.flush()
    target_app = _make_application(db, org=org, role=role, name="T", email="t@x.test")
    run = _make_agent_run(db, role)

    other_id = int(target_app.id) + 12345
    stale = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="send_assessment_approval",
        subject_id=other_id,
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "approve"},
    )
    db.add(stale)
    db.flush()

    with patch(
        "app.actions.send_assessment.run"
    ) as mock_run, patch(
        "app.actions.ask_recruiter.open",
        return_value=type("_R", (), {"id": 777})(),
    ) as mock_ask:
        result = tool_registry.dispatch(
            "send_assessment",
            {"application_id": int(target_app.id)},
            db=db,
            agent_run=run,
            role=role,
        )

    assert not mock_run.called
    assert mock_ask.called
    assert result["status"] == "awaiting_recruiter_approval"
    db.refresh(stale)
    assert stale.dismissed_at is None


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


def test_nl_search_candidates_dispatch_passes_role_id(db):
    """Agent's nl_search wrapper should default role_id to the agent's role."""
    org = _make_org(db)
    role = _make_role(db, org)
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
            {"query": "python engineer"},
            db=db,
            agent_run=run,
            role=role,
        )

    assert mock_runner.called
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

    # Recruiter approves — should now succeed (previously raised 501).
    # Patch the email dispatch so the test stays hermetic.
    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
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

    # Email dispatch happened with the candidate's address.
    assert mock_email.called
    kwargs = mock_email.call_args.kwargs
    assert kwargs["candidate_email"] == "x@x.test"


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

    with patch("app.actions.reject_application._dispatch_rejection_email"):
        approve_decision.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            decision_id=int(queued.id),
        )
        db.flush()

    db.refresh(app)
    assert app.application_outcome == "rejected"
