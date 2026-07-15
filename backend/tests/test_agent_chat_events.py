"""Durable background-event publication into role Agent Chat."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import event

from app.agent_chat import engine, events, service, timeline
from app.agent_runtime.budget_guard import BudgetCheck
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.agent_conversation import (
    AgentConversationMessage,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_EVENT,
)
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


_AGENT_RUN_PK = 0


def _assign_agent_run_pk(_mapper, _connection, target):  # pragma: no cover
    global _AGENT_RUN_PK
    if target.id is None:
        _AGENT_RUN_PK += 1
        target.id = _AGENT_RUN_PK


event.listen(AgentRun, "before_insert", _assign_agent_run_pk)


def _world(db):
    org = Organization(name="Event Org", slug=f"event-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"event-{id(db)}@example.test",
        hashed_password="x",
        full_name="Event Recruiter",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(
        organization_id=int(org.id),
        name="Event Engineer",
        description="A complete role for event tests.",
        source="manual",
        agentic_mode_enabled=True,
        score_threshold=70,
    )
    db.add_all([user, role])
    db.flush()
    conversation = service.ensure_conversation(
        db, organization_id=int(org.id), role=role
    )
    db.flush()
    return org, user, role, conversation


def _event_messages(db, conversation):
    return (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_EVENT,
        )
        .all()
    )


def test_event_is_durable_visible_unread_and_idempotent(db):
    org, user, role, conversation = _world(db)
    first = events.post_agent_event(
        db,
        role=role,
        event_type="test_completion",
        event_key="job:41:done",
        severity="success",
        title="Background work finished",
        summary="Three records were updated.",
        details=[{"label": "Updated", "value": 3}],
        source={
            "type": "background_job",
            "id": 41,
            "label": "Job #41",
            "href": "https://attacker.example/private",
        },
        suggestions=[{"label": "Review", "prompt": "Review the updated records."}],
    )
    duplicate = events.post_agent_event(
        db,
        role=role,
        event_type="test_completion",
        event_key="job:41:done",
        severity="error",
        title="A retry must not overwrite this",
        summary="Duplicate delivery.",
    )
    db.commit()

    assert first is not None
    assert duplicate is None
    assert first.kind == MESSAGE_KIND_EVENT
    assert first.source_key and first.source_key.startswith(events.EVENT_STOP_PREFIX)
    assert len(_event_messages(db, conversation)) == 1
    card = first.actions[0]
    assert card["type"] == events.EVENT_CARD_TYPE
    assert card["details"] == [{"label": "Updated", "value": "3"}]
    assert card["source"] == {
        "type": "background_job",
        "id": 41,
        "label": "Job #41",
    }

    merged = timeline.build_timeline(db, conversation=conversation, role=role)
    item = next(row for row in merged if row.get("message_id") == int(first.id))
    assert item["message_kind"] == MESSAGE_KIND_EVENT

    listing = service.list_agent_conversations(
        db, organization_id=int(org.id), user=user
    )
    assert listing[0]["unread_messages"] == 1
    service.mark_read(db, conversation=conversation, user=user)
    db.commit()
    listing = service.list_agent_conversations(
        db, organization_id=int(org.id), user=user
    )
    assert listing[0]["unread_messages"] == 0


def test_event_does_not_close_active_turn_or_enter_model_history(db):
    _org, user, role, conversation = _world(db)
    user_row = engine.persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Review the candidate pool.",
    )
    events.post_agent_event(
        db,
        role=role,
        event_type="test_failure",
        event_key="job:42:failed",
        severity="error",
        title="A background task failed",
        summary="The separate task can be retried.",
    )
    db.flush()

    assert user_row.kind == MESSAGE_KIND_CHAT
    assert service.conversation_agent_working(db, conversation) is True
    history = engine._load_history(db, conversation)
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert "Review the candidate pool" in history[0]["content"][0]["text"]


def test_failed_run_event_is_safe_and_actionable(db):
    org, _user, role, conversation = _world(db)
    run = AgentRun(
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="failed",
        error="provider leaked SECRET-API-KEY in exception",
        model_version="test-model",
        prompt_version="test-prompt",
        rounds_executed=2,
        decisions_emitted=1,
        total_cost_micro_usd=12345,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    message = events.post_agent_run_event(db, role=role, run=run)
    duplicate = events.post_agent_run_event(db, role=role, run=run)
    db.flush()

    assert message is not None
    assert duplicate is None
    assert len(_event_messages(db, conversation)) == 1
    serialized = f"{message.text} {message.actions}"
    assert "SECRET-API-KEY" not in serialized
    card = message.actions[0]
    assert card["severity"] == "error"
    assert card["source"] == {
        "type": "agent_run",
        "id": int(run.id),
        "label": f"Agent run #{int(run.id)}",
    }
    assert card["suggestions"][0]["prompt"]
    assert {item["label"] for item in card["details"]} >= {
        "Trigger",
        "Status",
        "Rounds",
        "Decisions created",
        "Model cost",
    }


def test_run_events_skip_success_and_failures_with_existing_question_card(db):
    org, _user, role, conversation = _world(db)
    succeeded = AgentRun(
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="succeeded",
        model_version="m",
        prompt_version="p",
    )
    missing_spec = AgentRun(
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="aborted",
        error="missing_job_spec",
        model_version="m",
        prompt_version="p",
    )
    db.add_all([succeeded, missing_spec])
    db.flush()

    assert events.post_agent_run_event(db, role=role, run=succeeded) is None
    assert events.post_agent_run_event(db, role=role, run=missing_spec) is None
    assert _event_messages(db, conversation) == []


def test_repeated_run_failure_is_throttled_by_category(db):
    org, _user, role, conversation = _world(db)
    base = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    runs = [
        AgentRun(
            organization_id=int(org.id),
            role_id=int(role.id),
            trigger="cron",
            status="failed",
            error="anthropic call failed: provider detail changed",
            model_version="m",
            prompt_version="p",
            finished_at=base + offset,
        )
        for offset in (timedelta(0), timedelta(hours=1), timedelta(hours=7))
    ]
    db.add_all(runs)
    db.flush()

    assert events.post_agent_run_event(db, role=role, run=runs[0]) is not None
    assert events.post_agent_run_event(db, role=role, run=runs[1]) is None
    assert events.post_agent_run_event(db, role=role, run=runs[2]) is not None
    assert len(_event_messages(db, conversation)) == 2


def test_unknown_event_severity_falls_back_to_info(db):
    _org, _user, role, _conversation = _world(db)
    message = events.post_agent_event(
        db,
        role=role,
        event_type="test",
        event_key="severity",
        severity="critical-ish",
        title="Unknown severity",
        summary="It should render safely.",
    )
    assert message.actions[0]["severity"] == "info"


def test_universal_role_budget_gate_publishes_only_on_pause_transition(db):
    from app.services.role_budget_gate import can_spend_on_role

    _org, _user, role, conversation = _world(db)
    role.monthly_usd_budget_cents = 2_500
    db.flush()
    with patch(
        "app.services.role_budget_gate.check_monthly_usd",
        return_value=BudgetCheck(ok=False, reason="monthly USD cap reached"),
    ), patch(
        "app.agent_runtime.budget_guard.month_to_date_spend_cents",
        return_value=2_500,
    ):
        assert can_spend_on_role(db, role=role) is False
        assert can_spend_on_role(db, role=role) is False
    db.flush()

    rows = _event_messages(db, conversation)
    assert len(rows) == 1
    card = rows[0].actions[0]
    assert card["event_type"] == "agent_budget_guard"
    assert card["details"] == [
        {"label": "Monthly cap", "value": "$25.00"},
        {"label": "Month-to-date spend", "value": "$25.00"},
    ]


def test_org_credit_warning_repeats_only_after_a_new_credit_grant(db):
    org, _user, role, conversation = _world(db)
    base = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)

    def exhausted_run(*, hour: int) -> AgentRun:
        run = AgentRun(
            organization_id=int(org.id),
            role_id=int(role.id),
            trigger="cron",
            status="budget_paused",
            error="insufficient organization credits: private balance details",
            model_version="m",
            prompt_version="p",
            finished_at=base + timedelta(hours=hour),
        )
        db.add(run)
        db.flush()
        return run

    assert events.post_agent_run_event(
        db, role=role, run=exhausted_run(hour=0)
    ) is not None
    # Scheduled retries remain silent while the same empty balance persists.
    assert events.post_agent_run_event(
        db, role=role, run=exhausted_run(hour=12)
    ) is None

    db.add(
        BillingCreditLedger(
            organization_id=int(org.id),
            delta=10_000,
            balance_after=10_000,
            reason="manual_top_up",
            external_ref=f"event-credit-{int(org.id)}",
        )
    )
    db.flush()

    # If the newly granted balance is later exhausted, that is a new episode.
    assert events.post_agent_run_event(
        db, role=role, run=exhausted_run(hour=24)
    ) is not None
    rows = _event_messages(db, conversation)
    assert len(rows) == 2
    assert "private balance details" not in str([row.actions for row in rows])
