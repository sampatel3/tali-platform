"""Route + engine tests for the role-agent chat.

Drives the synchronous tool-use loop end-to-end with the Anthropic client
mocked (a scripted tool_use → text sequence), through the FastAPI
TestClient. Covers the two headline flows the recruiter asked for:

  * "what happens if I drop the threshold to 60?" → simulate card in the reply
  * "cap salary at 25k on this role" → constraint chip + re-screen kicked off

plus the sidebar list, the merged timeline (chat + decision card), and that
opening the thread clears the unread badge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event

from app.models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


# SQLite BigInteger PK workaround.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_decisions": 0, "agent_needs_input": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentNeedsInput, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Scripted fake Anthropic client
# ---------------------------------------------------------------------------


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool(tid, name, inp):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def _resp(blocks, stop):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=6,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class _FakeClient:
    """Returns a scripted response per messages.create() call."""

    def __init__(self, scripted):
        self._scripted = list(scripted)

        class _Messages:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                return self._outer._scripted.pop(0)

        self.messages = _Messages(self)


# ---------------------------------------------------------------------------
# Setup helpers (org/user from auth, role + apps in shared DB)
# ---------------------------------------------------------------------------


def _org_id(db, email: str) -> int:
    return int(db.query(User).filter(User.email == email).first().organization_id)


def _role(db, org_id, *, name="Backend", threshold=70) -> Role:
    role = Role(
        organization_id=org_id,
        name=name,
        source="manual",
        score_threshold=threshold,
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    return role


def _scored_app(db, org_id, role, *, score, name) -> CandidateApplication:
    cand = Candidate(organization_id=org_id, email=f"{name}@x.test", full_name=name)
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=score,
    )
    db.add(app)
    db.flush()
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _timeline_json(client, role_id, headers) -> dict:
    r = client.get(f"/api/v1/agent-chat/conversations/{role_id}/timeline", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _last_agent_message(client, role_id, headers) -> dict:
    """The agent's reply now lands asynchronously (run_agent_chat_turn); under
    eager Celery the worker has already run by the time the POST returns, so we
    read the reply from the timeline rather than the POST body."""
    timeline = _timeline_json(client, role_id, headers)["timeline"]
    agent_msgs = [
        it for it in timeline if it.get("kind") == "message" and it.get("author") == "agent"
    ]
    assert agent_msgs, "expected an agent reply in the timeline"
    return agent_msgs[-1]


def test_timeline_survives_optional_helper_failure(client, db):
    """A deterministic-helper bug cannot take down the core chat timeline.

    The conversation is created lazily by this request, outside the helper's
    savepoint, and must still be committed even when helper generation raises.
    """

    headers, email = auth_headers(client, organization_name="HelperFailureOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    role_id = int(role.id)
    db.commit()

    with patch(
        "app.domains.agent_chat.routes.maybe_post_helper_briefing",
        side_effect=RuntimeError("helper generation failed"),
    ):
        response = client.get(
            f"/api/v1/agent-chat/conversations/{role_id}/timeline",
            headers=headers,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["role_id"] == role_id
    assert payload["role_name"] == role.name
    assert payload["timeline"] == []
    assert payload["agent_working"] is False

    db.expire_all()
    conversation = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.organization_id == org_id,
            AgentConversation.role_id == role_id,
        )
        .one()
    )
    assert int(conversation.id) == int(payload["conversation_id"])


def test_send_persists_user_message_and_reads_as_working(client, db):
    """The recruiter's message is durable the instant they send (it survives
    navigation / an agent switch / a failed turn), and the conversation reads as
    'agent working' until the worker posts the reply."""
    headers, email = auth_headers(client, organization_name="DurableOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    db.commit()

    # Don't run the worker — observe the durable pending state right after send.
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay") as delay:
        resp = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "who is in the pool?"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["agent_working"] is True
    assert data["agent_progress"] == "Understanding your request…"
    assert data["messages"][-1]["author"] == "recruiter"
    delay.assert_called_once()
    assert delay.call_args.kwargs["accepted_role_version"] == int(role.version or 1)

    # The message is already persisted and the turn reads as "working" on a fresh
    # load, even though no reply exists yet — this is what survives navigation.
    tl = _timeline_json(client, role.id, headers)
    msgs = [it for it in tl["timeline"] if it["kind"] == "message"]
    assert [m["author"] for m in msgs] == ["recruiter"]
    assert msgs[0]["text"] == "who is in the pool?"
    assert msgs[0]["created_at"]  # timestamp present for the UI
    assert tl["agent_working"] is True
    assert tl["agent_progress"] == "Understanding your request…"


def test_timeline_reports_persisted_agent_tool_progress(client, db):
    headers, email = auth_headers(client, organization_name="ProgressOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    db.commit()

    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "rank the strongest candidates"},
        )
    assert response.status_code == 200, response.text

    conversation = (
        db.query(AgentConversation)
        .filter(AgentConversation.role_id == int(role.id))
        .one()
    )
    db.add(
        AgentConversationMessage(
            conversation_id=int(conversation.id),
            organization_id=org_id,
            role_id=int(role.id),
            author_role=AUTHOR_ROLE_ASSISTANT,
            kind=MESSAGE_KIND_TOOL,
            content=[
                {
                    "type": "tool_use",
                    "id": "candidates",
                    "name": "find_top_candidates",
                    "input": {},
                }
            ],
        )
    )
    db.commit()
    assert _timeline_json(client, role.id, headers)["agent_progress"] == (
        "Searching and ranking candidates…"
    )

    db.add(
        AgentConversationMessage(
            conversation_id=int(conversation.id),
            organization_id=org_id,
            role_id=int(role.id),
            author_role=AUTHOR_ROLE_USER,
            kind=MESSAGE_KIND_TOOL,
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "candidates",
                    "content": "{}",
                    "is_error": False,
                }
            ],
        )
    )
    db.commit()
    assert _timeline_json(client, role.id, headers)["agent_progress"] == (
        "Preparing your answer…"
    )


def test_second_message_to_same_agent_while_working_is_rejected(client, db):
    """One turn at a time per agent: a second send while the first is still
    running is rejected with 409 (no interleaved double-turn). The worker is
    held (delay patched) so the turn stays 'working'."""
    headers, email = auth_headers(client, organization_name="LockOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    db.commit()

    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        first = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "first message"},
        )
        assert first.status_code == 200, first.text

        second = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "second message"},
        )
    assert second.status_code == 409, second.text
    # The second message was NOT persisted — only the first is in the thread.
    msgs = [it for it in _timeline_json(client, role.id, headers)["timeline"] if it["kind"] == "message"]
    assert [m["text"] for m in msgs] == ["first message"]


def test_can_message_other_agents_while_one_is_working(client, db):
    """The working lock is per-agent: while one agent is mid-turn you can still
    send to a DIFFERENT agent (independent thread)."""
    headers, email = auth_headers(client, organization_name="MultiOrg")
    org_id = _org_id(db, email)
    role_a = _role(db, org_id, name="Role A")
    role_b = _role(db, org_id, name="Role B")
    db.commit()

    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        a = client.post(
            f"/api/v1/agent-chat/conversations/{role_a.id}/messages",
            headers=headers,
            json={"message": "hello A"},
        )
        assert a.status_code == 200, a.text
        # A is now "working" — B must still accept a message.
        b = client.post(
            f"/api/v1/agent-chat/conversations/{role_b.id}/messages",
            headers=headers,
            json={"message": "hello B"},
        )
    assert b.status_code == 200, b.text
    assert b.json()["agent_working"] is True


def test_send_message_runs_simulate_tool_and_returns_card(client, db):
    headers, email = auth_headers(client, organization_name="ChatOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id, threshold=70)
    _scored_app(db, org_id, role, score=80, name="Ada")
    _scored_app(db, org_id, role, score=65, name="Bo")
    _scored_app(db, org_id, role, score=50, name="Cy")
    db.commit()

    scripted = [
        _resp([_tool("t1", "simulate_threshold", {"threshold": 60})], "tool_use"),
        _resp([_text("Dropping the cut-off from 70 to 60 brings Bo back through.")], "end_turn"),
    ]
    with patch(
        "app.agent_chat.engine.get_client_for_org", return_value=_FakeClient(scripted)
    ):
        resp = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "what happens if I drop the threshold to 60?"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The POST accepts the turn and echoes the user message; the reply follows.
    assert data["status"] == "accepted"
    assert data["agent_working"] is True
    assert data["messages"][-1]["author"] == "recruiter"

    agent_msg = _last_agent_message(client, role.id, headers)
    assert "Bo" in agent_msg["text"]
    cards = agent_msg["actions"]
    assert any(c["type"] == "threshold_simulation" and c["simulated_threshold"] == 60 for c in cards)
    # The reply closes the turn → no longer "working".
    assert _timeline_json(client, role.id, headers)["agent_working"] is False


def test_send_message_truncated_reply_gets_continue_note(client, db):
    """A reply cut off at the length ceiling (stop_reason='max_tokens') keeps its
    partial text + a graceful "say continue" note — never a bare mid-word stop."""
    headers, email = auth_headers(client, organization_name="TruncOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    db.commit()

    scripted = [
        _resp(
            [_text("1. Jojo — Final Interview ✅\n2. Praveena — NLP-based call schedulers (")],
            "max_tokens",
        ),
    ]
    with patch(
        "app.agent_chat.engine.get_client_for_org", return_value=_FakeClient(scripted)
    ):
        resp = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "rank the final-interview candidates in full detail"},
        )

    assert resp.status_code == 200, resp.text
    text = _last_agent_message(client, role.id, headers)["text"]
    assert "Praveena" in text  # partial answer preserved
    assert "continue" in text.lower() and "length limit" in text.lower()


def test_constraint_change_is_opt_in_then_rescreens(client, db):
    """P0: a constraint edit applies immediately but does NOT auto-re-screen —
    it returns a would_rescreen estimate; the re-screen runs only on a
    confirmed rescreen_role call."""
    headers, email = auth_headers(client, organization_name="SalaryOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    _scored_app(db, org_id, role, score=72, name="Dana")
    db.commit()

    scripted = [
        # turn 1 — apply the constraint, report the estimate, ask (no spend)
        _resp(
            [_tool("t1", "add_or_update_constraint", {"text": "Salary expectation <= 25,000", "bucket": "constraint"})],
            "tool_use",
        ),
        _resp([_text("Capped salary at 25k. This would re-screen ~1 candidate (~$0.05) — run it?")], "end_turn"),
        # turn 2 — recruiter confirms → re-screen
        _resp([_tool("t2", "rescreen_role", {})], "tool_use"),
        _resp([_text("Re-screening now.")], "end_turn"),
        # turn 3 — a consumed receipt cannot be replayed to spend again
        _resp([_tool("t3", "rescreen_role", {})], "tool_use"),
        _resp([_text("That confirmation was already used; I need a new preview.")], "end_turn"),
    ]
    with patch(
        "app.agent_chat.engine.get_client_for_org", return_value=_FakeClient(scripted)
    ), patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=3
    ) as stale, patch("app.tasks.scoring_tasks.sweep_stale_scores"):
        r1 = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers, json={"message": "cap salary at 25k"},
        )
        assert r1.status_code == 200, r1.text
        agent1 = _last_agent_message(client, role.id, headers)
        card = next(c for c in agent1["actions"] if c["type"] == "constraint_change")
        assert card["action"] == "added"
        assert card["rescreening_count"] == 0                     # P0: not auto-re-screened
        assert card["would_rescreen"]["count"] >= 1               # estimate surfaced
        assert stale.call_count == 0                              # nothing spent yet

        r2 = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers, json={"message": "yes, re-screen"},
        )
        assert r2.status_code == 200, r2.text
        assert stale.call_count == 1                              # opt-in → re-screen ran

        r3 = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers, json={"message": "yes again"},
        )
        assert r3.status_code == 200, r3.text
        assert stale.call_count == 1                              # receipt is single-use


def test_same_turn_model_confirmation_cannot_start_paid_rescreen(client, db):
    headers, email = auth_headers(client, organization_name="SameTurnGuardOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    _scored_app(db, org_id, role, score=72, name="Dana")
    db.commit()

    scripted = [
        _resp(
            [_tool("t1", "add_or_update_constraint", {"text": "Must know Rust", "bucket": "must"})],
            "tool_use",
        ),
        # The model tries to manufacture confirmation in the same user turn.
        _resp([_tool("t2", "rescreen_role", {})], "tool_use"),
        _resp([_text("I need your confirmation in a new message before I spend.")], "end_turn"),
    ]
    with patch(
        "app.agent_chat.engine.get_client_for_org", return_value=_FakeClient(scripted)
    ), patch("app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=1) as stale:
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "add Rust and rescreen"},
        )
    assert response.status_code == 200, response.text
    stale.assert_not_called()

    from app.models.role_criterion import RoleCriterion

    chip = (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == role.id, RoleCriterion.deleted_at.is_(None))
        .one()
    )
    assert chip.text == "Must know Rust"


def test_negative_later_message_cannot_be_treated_as_paid_confirmation(client, db):
    headers, email = auth_headers(client, organization_name="NegativeConfirmOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    _scored_app(db, org_id, role, score=72, name="Dana")
    db.commit()

    scripted = [
        _resp(
            [_tool("t1", "add_or_update_constraint", {"text": "Must know Rust", "bucket": "must"})],
            "tool_use",
        ),
        _resp([_text("This would re-screen one candidate. Run it?")], "end_turn"),
        # Even if the model misreads the negative response, the server rail
        # must refuse the paid action.
        _resp([_tool("t2", "rescreen_role", {})], "tool_use"),
        _resp([_text("I did not start it.")], "end_turn"),
    ]
    with patch(
        "app.agent_chat.engine.get_client_for_org", return_value=_FakeClient(scripted)
    ), patch("app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=1) as stale:
        first = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "add Rust"},
        )
        second = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "no, do not re-screen"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    stale.assert_not_called()


def test_list_conversations_and_timeline_merge_decision(client, db):
    headers, email = auth_headers(client, organization_name="ListOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Data Eng")
    app = _scored_app(db, org_id, role, score=40, name="Eve")
    # A pending decision should surface as a card in the timeline.
    db.add(
        AgentDecision(
            organization_id=org_id, role_id=role.id, application_id=app.id,
            decision_type="skip_assessment_reject", recommendation="reject",
            status="pending", reasoning="below cut-off", model_version="m",
            prompt_version="p", idempotency_key="t-eve",
        )
    )
    db.commit()

    # Timeline (also creates the conversation + marks read).
    tl = client.get(
        f"/api/v1/agent-chat/conversations/{role.id}/timeline", headers=headers
    )
    assert tl.status_code == 200, tl.text
    timeline = tl.json()["timeline"]
    decision_cards = [it for it in timeline if it["kind"] == "decision"]
    assert len(decision_cards) == 1
    assert decision_cards[0]["candidate_name"] == "Eve"
    assert decision_cards[0]["decision_type"] == "skip_assessment_reject"

    # Sidebar lists the active agent with its pending count.
    lst = client.get("/api/v1/agent-chat/conversations", headers=headers)
    assert lst.status_code == 200
    agents = lst.json()["agents"]
    mine = next(a for a in agents if a["role_id"] == role.id)
    assert mine["agent_enabled"] is True
    assert mine["pending_decisions"] == 1


def test_timeline_caps_decisions_to_newest_window_in_chronological_order(client, db):
    headers, email = auth_headers(client, organization_name="TimelineWindowOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Busy Role")
    app = _scored_app(db, org_id, role, score=75, name="Window Candidate")
    base = datetime.now(timezone.utc) - timedelta(hours=2)

    for index in range(61):
        db.add(
            AgentDecision(
                organization_id=org_id,
                role_id=role.id,
                application_id=app.id,
                decision_type="advance_to_interview",
                recommendation="advance",
                status="pending",
                reasoning=f"decision-{index}",
                model_version="m",
                prompt_version="p",
                idempotency_key=f"timeline-window-{index}",
                created_at=base + timedelta(minutes=index),
            )
        )
    db.commit()

    cards = [
        item
        for item in _timeline_json(client, role.id, headers)["timeline"]
        if item["kind"] == "decision"
    ]

    assert len(cards) == 60
    assert [card["reasoning"] for card in cards] == [
        f"decision-{index}" for index in range(1, 61)
    ]


def test_timeline_excludes_pending_decision_while_snoozed(client, db):
    headers, email = auth_headers(client, organization_name="TimelineSnoozeOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Snooze Role")
    app = _scored_app(db, org_id, role, score=55, name="Snooze Candidate")
    now = datetime.now(timezone.utc)

    for label, snoozed_until in (
        ("not-snoozed", None),
        ("elapsed-snooze", now - timedelta(minutes=1)),
        ("active-snooze", now + timedelta(hours=1)),
    ):
        db.add(
            AgentDecision(
                organization_id=org_id,
                role_id=role.id,
                application_id=app.id,
                decision_type="skip_assessment_reject",
                recommendation="reject",
                status="pending",
                reasoning=label,
                model_version="m",
                prompt_version="p",
                idempotency_key=f"timeline-snooze-{label}",
                snoozed_until=snoozed_until,
                created_at=now,
            )
        )
    db.commit()

    cards = [
        item
        for item in _timeline_json(client, role.id, headers)["timeline"]
        if item["kind"] == "decision"
    ]

    assert {card["reasoning"] for card in cards} == {
        "not-snoozed",
        "elapsed-snooze",
    }


def test_timeline_404_for_other_orgs_role(client, db):
    headers_a, email_a = auth_headers(client, organization_name="OrgA")
    _headers_b, email_b = auth_headers(client, organization_name="OrgB")
    org_b = _org_id(db, email_b)
    role_b = _role(db, org_b, name="Stranger")
    db.commit()

    resp = client.get(
        f"/api/v1/agent-chat/conversations/{role_b.id}/timeline", headers=headers_a
    )
    assert resp.status_code == 404
