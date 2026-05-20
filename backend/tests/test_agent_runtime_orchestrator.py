"""Orchestrator tests with a stubbed Anthropic client.

These cover the core control-flow paths in ``run_cycle``:
- Agent calls ``agent_run_complete`` immediately → status=succeeded
- Agent calls a tool then ``agent_run_complete`` → tools recorded
- Agent never calls complete → MAX_TOOL_ROUNDS hit → status=aborted
- Budget pre-check fails → status=budget_paused, role paused
- Anthropic call raises → status=failed

The Anthropic client is patched at ``app.agent_runtime.orchestrator.get_client_for_org``;
the stub returns scripted responses round-by-round so the test can shape
the loop precisely.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.agent_runtime import orchestrator
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


# SQLite BigInteger PK workaround (same as other agent_runtime tests).
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — fired by SQLA
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
# AgentDecision was added to the orchestrator's queue-tool flow but never
# wired to the SQLite BigInteger-PK workaround above, so any test path
# that emits a decision via _tool_queue_advance_decision blew up with
# NOT NULL on agent_decisions.id. Hook it here too.
event.listen(AgentDecision, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Fixtures + scripted Anthropic response factory
# ---------------------------------------------------------------------------


def _make_org(db) -> Organization:
    org = Organization(name="Orch Org", slug=f"orch-org-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization) -> Role:
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,  # 0 disables the monthly check
    )
    db.add(role)
    db.flush()
    return role


def _make_app(db, *, org: Organization, role: Role) -> CandidateApplication:
    candidate = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=80.0,
    )
    db.add(app)
    db.flush()
    return app


def _block_text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _block_tool_use(*, tool_use_id: str, name: str, input_: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_use_id, name=name, input=input_)


def _response(*, blocks, stop_reason: str, input_tokens: int = 50, output_tokens: int = 30) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def _scripted_client(responses: list):
    """Return a mock client whose ``messages.create`` yields ``responses`` in order."""
    iterator = iter(responses)

    def _create(**kwargs):
        return next(iterator)

    client = MagicMock()
    client.messages.create = MagicMock(side_effect=_create)
    return client


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_run_cycle_calls_agent_run_complete_immediately(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_1",
                    name="agent_run_complete",
                    input_={"summary": "Nothing to do this cycle."},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "succeeded"
    assert run.error is None
    assert run.finished_at is not None
    # tools_called records each tool with its count.
    assert {entry["name"]: entry["count"] for entry in (run.tools_called or [])} == {
        "agent_run_complete": 1,
    }
    # decisions_emitted unchanged (no queue tools called).
    assert run.decisions_emitted == 0


def test_run_cycle_records_tool_call_then_finishes(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_text("Looking at the application."),
                _block_tool_use(
                    tool_use_id="tu_1",
                    name="get_application",
                    input_={"application_id": int(app.id)},
                ),
            ],
            stop_reason="tool_use",
        ),
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_2",
                    name="agent_run_complete",
                    input_={"summary": "Reviewed; no decision needed."},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "succeeded"
    counts = {entry["name"]: entry["count"] for entry in (run.tools_called or [])}
    assert counts == {"get_application": 1, "agent_run_complete": 1}


def test_run_cycle_increments_decisions_when_queue_tool_called(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_1",
                    name="queue_advance_decision",
                    input_={
                        "application_id": int(app.id),
                        "reasoning": "Strong CV match; meets all requirements.",
                        "evidence": {"taali_score": 80},
                        "confidence": 0.85,
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_2",
                    name="agent_run_complete",
                    input_={"summary": "Queued advance for top candidate."},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="event", application_id=app.id
        )
    db.commit()

    assert run.status == "succeeded"
    assert run.decisions_emitted == 1
    assert run.trigger == "event"


# ---------------------------------------------------------------------------
# Edge cases — the orchestrator's safety nets
# ---------------------------------------------------------------------------


def test_run_cycle_aborts_when_max_rounds_exceeded(db):
    """Agent that never calls complete should abort cleanly."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    # Same response forever — no termination signal.
    spinning = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_x",
                name="get_application",
                input_={"application_id": int(app.id)},
            ),
        ],
        stop_reason="tool_use",
    )
    client = MagicMock()
    client.messages.create = MagicMock(return_value=spinning)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "aborted"
    assert "MAX_TOOL_ROUNDS" in (run.error or "")
    # Loop ran exactly the cap.
    assert client.messages.create.call_count == orchestrator.MAX_TOOL_ROUNDS


def test_run_cycle_pauses_role_on_monthly_budget_exhausted(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.monthly_usd_budget_cents = 100  # active cap
    db.flush()
    app = _make_app(db, org=org, role=role)

    fake_check = SimpleNamespace(ok=False, reason="monthly USD cap reached: 100c >= 100c")
    with patch(
        "app.agent_runtime.orchestrator.budget_guard.check_monthly_usd",
        return_value=fake_check,
    ), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=MagicMock()
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "budget_paused"
    assert "monthly USD cap" in (run.error or "")
    db.refresh(role)
    assert role.agent_paused_at is not None
    assert role.agent_paused_reason


def test_run_cycle_marks_failed_when_anthropic_call_raises(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    boom_client = MagicMock()
    boom_client.messages.create = MagicMock(side_effect=RuntimeError("network down"))

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=boom_client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "failed"
    assert "anthropic call failed" in (run.error or "").lower()


def test_run_cycle_uses_role_agent_model_when_set(db):
    """Per-role override should be passed to messages.create and stamped on AgentRun."""
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_model = "claude-sonnet-4-5"
    db.flush()
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_1",
                    name="agent_run_complete",
                    input_={"summary": "done"},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    # Run record stamps the model that was actually used.
    assert run.model_version == "claude-sonnet-4-5"
    # And the Anthropic call was invoked with that model id.
    create_kwargs = client.messages.create.call_args.kwargs
    assert create_kwargs["model"] == "claude-sonnet-4-5"


def test_run_cycle_falls_back_to_settings_model_when_role_override_blank(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_model = "  "  # whitespace-only must be treated as unset
    db.flush()
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_1",
                    name="agent_run_complete",
                    input_={"summary": "done"},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    # Default conftest CLAUDE_MODEL is claude-3-5-haiku-latest.
    assert run.model_version == "claude-3-5-haiku-latest"


def test_run_cycle_finishes_on_end_turn_without_complete(db):
    """Anthropic responding with stop_reason='end_turn' without the agent
    having called ``agent_run_complete`` ends the cycle as ``aborted``.

    Agents are required to explicitly signal completion via the complete
    tool — otherwise we can't distinguish "model ran out of things to do
    and stopped" (legitimate) from "model dropped the work mid-task"
    (silent failure). Test was written against the older contract where
    any end_turn was treated as success; the orchestrator was tightened
    so only the complete-tool path promotes to succeeded.
    """
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[_block_text("OK, I have nothing to do.")],
            stop_reason="end_turn",
        ),
    ])

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "aborted"
    assert run.finished_at is not None
