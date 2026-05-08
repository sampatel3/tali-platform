"""Tests for role-scoped Taali Chat.

Covers:
- Creating a conversation with role_id persists role_id
- role_id from a different org's role is silently ignored (defense in depth)
- _build_system_blocks emits the role-context block when role_id is set
- The role-context block includes the role name + pending decision count
- New chat tools (list_recent_agent_decisions, list_recent_agent_runs,
  explain_agent_decision) return correct payloads
- Tools are org-scoped (no cross-org leakage)
- explain_agent_decision joins decision + agent_run
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

from app.mcp import handlers
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.user import User
from app.taali_chat.service import _ensure_conversation
from app.taali_chat.system_prompt import build_system_blocks as _build_system_blocks


# Shared SQLite BigInteger PK workaround.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _make_org(db, *, name: str = "Org A") -> Organization:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_user(db, org: Organization) -> User:
    user = User(
        email=f"user-{id(db)}@x.test",
        hashed_password="x",
        full_name="Test User",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


def _make_role(db, org: Organization, *, name: str = "Backend") -> Role:
    role = Role(organization_id=org.id, name=name, source="manual")
    db.add(role)
    db.flush()
    return role


# ---------------------------------------------------------------------------
# _ensure_conversation persists role_id (and rejects cross-org spoofing)
# ---------------------------------------------------------------------------


def test_ensure_conversation_persists_role_id_when_set(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)

    convo = _ensure_conversation(
        db,
        user=user,
        conversation_id=None,
        first_message="Hi",
        role_id=int(role.id),
    )
    db.commit()
    db.refresh(convo)
    assert convo.role_id == role.id


def test_ensure_conversation_ignores_cross_org_role_id(db):
    """Defense in depth: a recruiter passing a role_id that belongs to
    another org should not result in a conversation scoped to that role."""
    org_a = _make_org(db, name="Org A")
    org_b = _make_org(db, name="Org B")
    user_a = _make_user(db, org_a)
    role_b = _make_role(db, org_b, name="Stranger Role")

    convo = _ensure_conversation(
        db,
        user=user_a,
        conversation_id=None,
        first_message="Hi",
        role_id=int(role_b.id),
    )
    db.commit()
    db.refresh(convo)
    assert convo.role_id is None


def test_ensure_conversation_unscoped_when_role_id_is_none(db):
    """Backwards-compat: existing chat creation paths with no role_id
    keep producing org-wide conversations."""
    org = _make_org(db)
    user = _make_user(db, org)

    convo = _ensure_conversation(
        db, user=user, conversation_id=None, first_message="Hi", role_id=None
    )
    db.commit()
    db.refresh(convo)
    assert convo.role_id is None


# ---------------------------------------------------------------------------
# _build_system_blocks injects role context
# ---------------------------------------------------------------------------


def test_build_system_blocks_returns_only_base_when_no_role_id(db):
    org = _make_org(db)
    user = _make_user(db, org)
    convo = TaaliChatConversation(
        organization_id=org.id, user_id=user.id, role_id=None, title="Test"
    )
    db.add(convo)
    db.flush()

    blocks = _build_system_blocks(db, conversation=convo)
    assert len(blocks) == 1
    assert "Taali" in blocks[0]["text"]


def test_build_system_blocks_appends_role_context_when_role_id_set(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org, name="Senior Backend")
    convo = TaaliChatConversation(
        organization_id=org.id, user_id=user.id, role_id=role.id, title="Test"
    )
    db.add(convo)
    db.flush()

    blocks = _build_system_blocks(db, conversation=convo)
    assert len(blocks) == 2
    role_context = blocks[1]["text"]
    assert "Senior Backend" in role_context
    assert f"role_id={role.id}" in role_context
    assert "default to this role" in role_context.lower()


def test_role_context_surfaces_pending_decision_count(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    convo = TaaliChatConversation(
        organization_id=org.id, user_id=user.id, role_id=role.id, title="Test"
    )
    db.add(convo)

    # Create one role with 2 pending + 1 approved decision.
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review",
        pipeline_stage_source="recruiter", application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()

    for status, key in [("pending", "p1"), ("pending", "p2"), ("approved", "a1")]:
        d = AgentDecision(
            organization_id=org.id, role_id=role.id, application_id=app.id,
            decision_type="advance_to_interview", recommendation="advance",
            status=status, reasoning="r", model_version="m", prompt_version="p",
            idempotency_key=key,
        )
        db.add(d)
    db.flush()

    blocks = _build_system_blocks(db, conversation=convo)
    role_context = blocks[1]["text"]
    assert "2 pending agent decision" in role_context


def test_role_context_skipped_when_role_soft_deleted(db):
    """If the role was soft-deleted after the conversation was created,
    the system block degrades to the base prompt rather than leaking
    role state from a deleted row."""
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    convo = TaaliChatConversation(
        organization_id=org.id, user_id=user.id, role_id=role.id, title="Test"
    )
    db.add(convo)
    db.flush()

    # Soft-delete the role.
    role.deleted_at = datetime.now(timezone.utc)
    db.add(role)
    db.commit()

    blocks = _build_system_blocks(db, conversation=convo)
    # Base-only: role context skipped because the soft-delete filter
    # excludes the role.
    assert len(blocks) == 1


# ---------------------------------------------------------------------------
# Agent-aware MCP handlers
# ---------------------------------------------------------------------------


def _make_decision(db, *, org, role, application_id, status="pending", decision_type="advance_to_interview", key=None):
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=application_id,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning=f"reasoning for {decision_type}",
        confidence=0.85,
        model_version="claude-3-5-haiku",
        prompt_version="agent.v5.test",
        idempotency_key=key or f"k:{application_id}:{decision_type}:{status}",
    )
    db.add(decision)
    db.flush()
    return decision


def _make_application(db, *, org, role):
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    cand = Candidate(organization_id=org.id, email=f"a-{id(db)}-{role.id}@x.test", full_name="A")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review",
        pipeline_stage_source="recruiter", application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def test_list_recent_agent_decisions_returns_decisions_for_org(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role)
    _make_decision(db, org=org, role=role, application_id=app.id, status="pending", key="k1")
    _make_decision(
        db, org=org, role=role, application_id=app.id,
        decision_type="reject", status="approved", key="k2",
    )
    db.commit()

    out = handlers.list_recent_agent_decisions(db, user)
    assert len(out) == 2
    types = {d["decision_type"] for d in out}
    assert types == {"advance_to_interview", "reject"}


def test_list_recent_agent_decisions_filters_by_role_id_and_status(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role_a = _make_role(db, org, name="A")
    role_b = _make_role(db, org, name="B")
    app_a = _make_application(db, org=org, role=role_a)
    app_b = _make_application(db, org=org, role=role_b)
    _make_decision(db, org=org, role=role_a, application_id=app_a.id, status="pending", key="ra-pending")
    _make_decision(db, org=org, role=role_a, application_id=app_a.id, status="approved", key="ra-approved")
    _make_decision(db, org=org, role=role_b, application_id=app_b.id, status="pending", key="rb-pending")
    db.commit()

    pending_role_a = handlers.list_recent_agent_decisions(
        db, user, role_id=role_a.id, status="pending"
    )
    assert len(pending_role_a) == 1
    assert pending_role_a[0]["role_id"] == role_a.id
    assert pending_role_a[0]["status"] == "pending"


def test_list_recent_agent_decisions_is_org_scoped(db):
    """Cross-org leakage check: org A's user must not see org B's decisions."""
    org_a = _make_org(db, name="A")
    org_b = _make_org(db, name="B")
    user_a = _make_user(db, org_a)
    role_b = _make_role(db, org_b)
    app_b = _make_application(db, org=org_b, role=role_b)
    _make_decision(db, org=org_b, role=role_b, application_id=app_b.id, status="pending", key="kb")
    db.commit()

    out = handlers.list_recent_agent_decisions(db, user_a)
    assert out == []


def test_list_recent_agent_decisions_validates_status_enum(db):
    org = _make_org(db)
    user = _make_user(db, org)
    with pytest.raises(ValueError):
        handlers.list_recent_agent_decisions(db, user, status="bogus")


def _make_run(db, *, org, role, trigger="manual", status="succeeded"):
    run = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger=trigger,
        status=status,
        model_version="claude-3-5-haiku",
        prompt_version="agent.v5.test",
        decisions_emitted=1,
    )
    db.add(run)
    db.flush()
    return run


def test_list_recent_agent_runs_returns_org_runs(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    _make_run(db, org=org, role=role, trigger="manual")
    _make_run(db, org=org, role=role, trigger="event")
    db.commit()

    out = handlers.list_recent_agent_runs(db, user)
    assert len(out) == 2


def test_list_recent_agent_runs_filters_by_trigger(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    _make_run(db, org=org, role=role, trigger="manual")
    _make_run(db, org=org, role=role, trigger="event")
    _make_run(db, org=org, role=role, trigger="cron")
    db.commit()

    cron_only = handlers.list_recent_agent_runs(db, user, trigger="cron")
    assert len(cron_only) == 1
    assert cron_only[0]["trigger"] == "cron"


def test_list_recent_agent_runs_validates_trigger_enum(db):
    org = _make_org(db)
    user = _make_user(db, org)
    with pytest.raises(ValueError):
        handlers.list_recent_agent_runs(db, user, trigger="bogus")


def test_explain_agent_decision_joins_decision_and_run(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role)
    run = _make_run(db, org=org, role=role)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=run.id,
        decision_type="advance_to_interview",
        recommendation="advance",
        status="approved",
        reasoning="strong CV match + cohort signals positive",
        confidence=0.85,
        model_version="claude-3-5-haiku",
        prompt_version="agent.v5.test",
        idempotency_key="explain-test",
    )
    db.add(decision)
    db.flush()
    db.commit()

    out = handlers.explain_agent_decision(db, user, decision_id=decision.id)
    assert out["decision"]["id"] == decision.id
    assert out["decision"]["reasoning"] == "strong CV match + cohort signals positive"
    assert out["decision"]["confidence"] == 0.85
    assert out["agent_run"] is not None
    assert out["agent_run"]["id"] == run.id


def test_explain_agent_decision_handles_missing_run(db):
    org = _make_org(db)
    user = _make_user(db, org)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role)
    # No agent_run linked.
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type="reject",
        recommendation="reject",
        status="pending",
        reasoning="below threshold",
        model_version="m",
        prompt_version="p",
        idempotency_key="orphan-decision",
    )
    db.add(decision)
    db.flush()
    db.commit()

    out = handlers.explain_agent_decision(db, user, decision_id=decision.id)
    assert out["decision"]["id"] == decision.id
    assert out["agent_run"] is None


def test_explain_agent_decision_returns_404_style_for_unknown_id(db):
    org = _make_org(db)
    user = _make_user(db, org)
    with pytest.raises(ValueError):
        handlers.explain_agent_decision(db, user, decision_id=999_999)


def test_explain_agent_decision_is_org_scoped(db):
    """Decision id from another org must not be readable."""
    org_a = _make_org(db, name="A")
    org_b = _make_org(db, name="B")
    user_a = _make_user(db, org_a)
    role_b = _make_role(db, org_b)
    app_b = _make_application(db, org=org_b, role=role_b)
    decision = _make_decision(
        db, org=org_b, role=role_b, application_id=app_b.id, key="cross-org"
    )
    db.commit()

    with pytest.raises(ValueError):
        handlers.explain_agent_decision(db, user_a, decision_id=decision.id)


# ---------------------------------------------------------------------------
# Tool registry exposes the new tools
# ---------------------------------------------------------------------------


def test_taali_chat_tool_registry_includes_new_agent_tools():
    from app.taali_chat.tool_registry import TAALI_CHAT_TOOLS

    names = {t["name"] for t in TAALI_CHAT_TOOLS}
    assert "list_recent_agent_decisions" in names
    assert "list_recent_agent_runs" in names
    assert "explain_agent_decision" in names
