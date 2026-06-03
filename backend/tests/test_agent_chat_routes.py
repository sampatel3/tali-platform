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

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event

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
    assert len(data["messages"]) == 2
    agent_msg = data["messages"][-1]
    assert agent_msg["author"] == "agent"
    assert "Bo" in agent_msg["text"]
    cards = agent_msg["actions"]
    assert any(c["type"] == "threshold_simulation" and c["simulated_threshold"] == 60 for c in cards)
    # The timeline carries the same conversation.
    assert any(it["kind"] == "message" for it in data["timeline"])


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
        card = next(c for c in r1.json()["messages"][-1]["actions"] if c["type"] == "constraint_change")
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

    from app.models.role_criterion import RoleCriterion

    chip = (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == role.id, RoleCriterion.deleted_at.is_(None))
        .one()
    )
    assert "25,000" in chip.text


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
