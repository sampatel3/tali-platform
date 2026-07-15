"""Deterministic proactive-helper behavior and anti-nagging rails."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.agent_chat import proactive, service
from app.models.agent_conversation import (
    AUTHOR_ROLE_USER,
    AgentConversationMessage,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_PROACTIVE,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _world(db, *, enabled=True):
    org = Organization(name="Helper Org", slug=f"helper-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"helper-{id(db)}@example.test",
        hashed_password="x",
        full_name="Helper Recruiter",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(
        organization_id=int(org.id),
        name="Platform Engineer",
        description="A complete platform engineering role.",
        source="manual",
        agentic_mode_enabled=enabled,
        score_threshold=70,
    )
    db.add_all([user, role])
    db.flush()
    conversation = service.ensure_conversation(
        db, organization_id=int(org.id), role=role
    )
    db.flush()
    return org, user, role, conversation


def _helper_messages(db, conversation):
    return (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_PROACTIVE,
        )
        .order_by(AgentConversationMessage.id.asc())
        .all()
    )


def test_busy_conversation_is_skipped_without_waiting():
    """Timeline polls must not block behind an agent turn's row lock."""

    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.one_or_none.return_value = None
    db = MagicMock()
    db.query.return_value = query
    conversation = MagicMock(id=17)
    role = MagicMock(id=26, organization_id=4)

    assert proactive.maybe_post_helper_briefing(
        db, conversation=conversation, role=role
    ) is None
    query.with_for_update.assert_called_once_with(skip_locked=True)


def test_fresh_thread_gets_one_grounded_helper_without_model_call(db):
    _org, _user, role, conversation = _world(db)
    with patch(
        "app.agent_chat.proactive.role_health_check",
        return_value={"open_candidates": 0, "top_finding": None},
    ) as health:
        first = proactive.maybe_post_helper_briefing(
            db, conversation=conversation, role=role
        )
        second = proactive.maybe_post_helper_briefing(
            db, conversation=conversation, role=role
        )

    assert first is not None
    assert second is None
    assert first.kind == MESSAGE_KIND_PROACTIVE
    card = first.actions[0]
    assert card["type"] == "helper_prompt"
    assert card["topic"] == "empty_pool"
    assert card["suggestions"]
    assert conversation.last_message_at is not None
    assert len(_helper_messages(db, conversation)) == 1
    health.assert_called_once()


def test_helper_quotes_highest_priority_question_then_advances_to_next(db):
    org, _user, role, conversation = _world(db)
    now = datetime.now(timezone.utc)
    lower = AgentNeedsInput(
        id=9101,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="other",
        prompt="Which timezone overlap do you prefer?",
        created_at=now - timedelta(minutes=2),
    )
    blocker = AgentNeedsInput(
        id=9102,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="monthly_budget_missing",
        prompt="What monthly spending cap should I use?",
        created_at=now - timedelta(minutes=1),
    )
    db.add_all([lower, blocker])
    db.flush()

    first = proactive.maybe_post_helper_briefing(
        db, conversation=conversation, role=role
    )
    assert first.actions[0]["focus_id"] == int(blocker.id)
    assert "What monthly spending cap" in first.text

    blocker.resolved_at = now
    blocker.response = {"value": 50}
    db.flush()
    second = proactive.maybe_post_helper_briefing(
        db, conversation=conversation, role=role
    )

    assert second is not None
    assert second.actions[0]["focus_id"] == int(lower.id)
    assert "timezone overlap" in second.text
    assert len(_helper_messages(db, conversation)) == 2


def test_snoozed_decisions_do_not_trigger_helper_attention(db):
    org, _user, role, conversation = _world(db)
    candidate = Candidate(
        organization_id=int(org.id),
        email="snoozed-helper@example.test",
        full_name="Snoozed Candidate",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        id=9201,
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        decision_type="escalate_low_confidence",
        recommendation="review",
        status="pending",
        reasoning="Sub-agents disagree.",
        confidence=0.4,
        model_version="test",
        prompt_version="test",
        idempotency_key="helper-snoozed",
        snoozed_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(decision)
    db.flush()

    with patch(
        "app.agent_chat.proactive.role_health_check",
        return_value={"open_candidates": 8, "top_finding": None},
    ):
        card = proactive.build_helper_briefing(db, role)

    assert card["topic"] == "all_clear"
    assert card["topic"] != "low_confidence_decision"


def test_proactive_prompt_cannot_close_or_interleave_an_active_turn(db):
    org, user, role, conversation = _world(db)
    db.add(
        AgentConversationMessage(
            conversation_id=int(conversation.id),
            organization_id=int(org.id),
            role_id=int(role.id),
            author_role=AUTHOR_ROLE_USER,
            author_user_id=int(user.id),
            kind=MESSAGE_KIND_CHAT,
            content=[{"type": "text", "text": "Review the pool"}],
            text="Review the pool",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    assert service.conversation_agent_working(db, conversation) is True
    posted = proactive.maybe_post_helper_briefing(
        db, conversation=conversation, role=role
    )

    assert posted is None
    assert service.conversation_agent_working(db, conversation) is True
    assert _helper_messages(db, conversation) == []


def test_proactive_message_is_unread_until_explicit_acknowledgement(db):
    _org, user, role, conversation = _world(db)
    with patch(
        "app.agent_chat.proactive.role_health_check",
        return_value={"open_candidates": 0, "top_finding": None},
    ):
        proactive.maybe_post_helper_briefing(
            db, conversation=conversation, role=role
        )
    db.commit()

    before = service.list_agent_conversations(
        db, organization_id=int(role.organization_id), user=user
    )
    assert before[0]["unread_messages"] == 1

    service.mark_read(db, conversation=conversation, user=user)
    db.commit()
    after = service.list_agent_conversations(
        db, organization_id=int(role.organization_id), user=user
    )
    assert after[0]["unread_messages"] == 0


def test_timeline_fetch_does_not_consume_proactive_unread_before_read_post(client, db):
    headers, email = auth_headers(client, organization_name="Helper Route Org")
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=int(user.organization_id),
        name="Helper Route Role",
        description="A complete helper route role.",
        source="manual",
        agentic_mode_enabled=True,
        score_threshold=70,
    )
    db.add(role)
    db.commit()

    first = client.get(
        f"/api/v1/agent-chat/conversations/{int(role.id)}/timeline",
        headers=headers,
    )
    second = client.get(
        f"/api/v1/agent-chat/conversations/{int(role.id)}/timeline",
        headers=headers,
    )
    assert first.status_code == second.status_code == 200
    helper_messages = [
        item
        for item in second.json()["timeline"]
        if item.get("kind") == "message"
        and any(card.get("type") == "helper_prompt" for card in item.get("actions") or [])
    ]
    assert len(helper_messages) == 1

    listing = client.get("/api/v1/agent-chat/conversations", headers=headers)
    row = next(item for item in listing.json()["agents"] if item["role_id"] == role.id)
    assert row["unread_messages"] == 1

    acknowledged = client.post(
        f"/api/v1/agent-chat/conversations/{int(role.id)}/read",
        headers=headers,
    )
    assert acknowledged.status_code == 200
    listing = client.get("/api/v1/agent-chat/conversations", headers=headers)
    row = next(item for item in listing.json()["agents"] if item["role_id"] == role.id)
    assert row["unread_messages"] == 0
