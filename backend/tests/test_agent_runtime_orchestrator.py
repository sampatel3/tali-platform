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

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


from app.agent_runtime import orchestrator
from app.models.agent_conversation import AgentConversationMessage, MESSAGE_KIND_EVENT
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.usage_credit_reservations import InsufficientRoleBudgetError
from app.services.usage_metering_service import InsufficientCreditsError


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
        # Legacy zero values resolve to the documented $50 default cap.
        monthly_usd_budget_cents=0,
        # Data-readiness gate requires a job spec before the agent runs.
        job_spec_text="Requirements\n- 5+ years backend engineering\n",
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


def _role_event_cards(db, role: Role) -> list[dict]:
    rows = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.role_id == int(role.id),
            AgentConversationMessage.kind == MESSAGE_KIND_EVENT,
        )
        .all()
    )
    return [card for row in rows for card in (row.actions or [])]


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
    metering = client.messages.create.call_args.kwargs["metering"]
    assert metering["credit_reservation"]["feature"] == "agent_autonomous"


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


def test_run_cycle_threads_agent_run_trace_id_to_every_anthropic_round(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
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
                    input_={"summary": "Reviewed."},
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

    expected_trace_id = f"agent-run:{int(run.id)}"
    meterings = [
        call.kwargs["metering"]
        for call in client.messages.create.call_args_list
    ]
    assert len(meterings) == 2
    assert [metering["metadata"]["round"] for metering in meterings] == [0, 1]
    assert all(metering["trace_id"] == expected_trace_id for metering in meterings)
    assert all(
        metering["metadata"]["agent_run_id"] == int(run.id)
        for metering in meterings
    )


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


def test_run_cycle_can_queue_low_confidence_escalation(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_escalate",
                    name="queue_escalate_decision",
                    input_={
                        "application_id": int(app.id),
                        "reasoning": "The CV and assessment evidence disagree.",
                        "evidence": {"rule_path": ["abstention_overlay:disagreement"]},
                        "confidence": 0.4,
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_done",
                    name="agent_run_complete",
                    input_={"summary": "Escalated one uncertain candidate."},
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
    assert run.decisions_emitted == 1
    assert {entry["name"] for entry in (run.tools_called or [])} == {
        "queue_escalate_decision",
        "agent_run_complete",
    }
    decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == app.id)
        .one()
    )
    assert decision.status == "pending"
    assert decision.decision_type == "escalate_low_confidence"


def test_run_cycle_drops_actions_when_agent_is_disabled_during_provider_call(db):
    """Turning the agent off in-flight must prevent the response taking effect."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_stale",
                name="queue_advance_decision",
                input_={
                    "application_id": int(app.id),
                    "reasoning": "This action arrived after shutdown.",
                    "evidence": {"taali_score": 80},
                    "confidence": 0.9,
                },
            )
        ],
        stop_reason="tool_use",
    )

    def _disable_before_return(**kwargs):
        role.agentic_mode_enabled = False
        role.version = int(role.version or 1) + 1
        db.flush()
        return response

    client = MagicMock()
    client.messages.create = MagicMock(side_effect=_disable_before_return)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error == "agent_disabled_during_cycle"
    assert run.decisions_emitted == 0
    assert db.query(AgentDecision).filter(AgentDecision.role_id == role.id).count() == 0


@pytest.mark.parametrize("revocation", ["pause", "disable"])
def test_run_cycle_provider_admission_observes_control_change_after_precheck(
    db, revocation,
):
    """A control change that commits before admission must prevent paid work."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    client = _scripted_client(
        [
            _response(
                blocks=[
                    _block_tool_use(
                        tool_use_id="tu_must_not_run",
                        name="agent_run_complete",
                        input_={"summary": "This provider call must not run."},
                    ),
                ],
                stop_reason="tool_use",
            ),
        ]
    )
    real_reserve = orchestrator.reserve_provider_usage
    admissions: list[dict] = []

    def _revoke_then_reserve(**kwargs):
        admissions.append(dict(kwargs))
        if revocation == "pause":
            role.agent_paused_at = datetime.now(timezone.utc)
            role.agent_paused_reason = "recruiter paused during admission"
        else:
            role.agentic_mode_enabled = False
        db.commit()
        return real_reserve(**kwargs)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.reserve_provider_usage",
        side_effect=_revoke_then_reserve,
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error in {
        "agent_paused_during_cycle",
        "agent_disabled_during_cycle",
    }
    assert admissions[0]["require_role_authority"] is True
    client.messages.create.assert_not_called()


def test_run_cycle_rechecks_role_version_before_a_second_provider_round(db):
    """A config change after a tool round must stop the next paid request."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    first_response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_read",
                name="get_application",
                input_={"application_id": int(app.id)},
            )
        ],
        stop_reason="tool_use",
    )
    unused_second_response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_done",
                name="agent_run_complete",
                input_={"summary": "This round must never run."},
            )
        ],
        stop_reason="tool_use",
    )
    client = _scripted_client([first_response, unused_second_response])
    original_dispatch = orchestrator.dispatch

    def _dispatch_then_change_version(name, args, *, db, agent_run, role):
        result = original_dispatch(
            name,
            args,
            db=db,
            agent_run=agent_run,
            role=role,
        )
        role.version = int(role.version or 1) + 1
        db.flush()
        return result

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.dispatch",
        side_effect=_dispatch_then_change_version,
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error == "role_configuration_changed_during_cycle"
    assert client.messages.create.call_count == 1


def test_run_cycle_rechecks_power_between_tools_in_one_response(db):
    """A multi-tool response cannot continue after the first tool turns stale."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_first",
                name="get_application",
                input_={"application_id": int(app.id)},
            ),
            _block_tool_use(
                tool_use_id="tu_must_not_run",
                name="queue_advance_decision",
                input_={
                    "application_id": int(app.id),
                    "reasoning": "Must be discarded after shutdown.",
                    "evidence": {},
                    "confidence": 0.9,
                },
            ),
        ],
        stop_reason="tool_use",
    )
    client = _scripted_client([response])
    original_dispatch = orchestrator.dispatch
    dispatched_names: list[str] = []

    def _dispatch_then_disable(name, args, *, db, agent_run, role):
        dispatched_names.append(name)
        result = original_dispatch(
            name,
            args,
            db=db,
            agent_run=agent_run,
            role=role,
        )
        if len(dispatched_names) == 1:
            role.agentic_mode_enabled = False
            role.version = int(role.version or 1) + 1
            db.flush()
        return result

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.dispatch",
        side_effect=_dispatch_then_disable,
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error == "agent_disabled_during_cycle"
    assert dispatched_names == ["get_application"]
    assert db.query(AgentDecision).filter(AgentDecision.role_id == role.id).count() == 0


def test_focused_manual_run_rechecks_application_before_a_second_provider_round(db):
    """A revoked focus must stop the next paid round after a completed tool."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    first_response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_read",
                name="get_application",
                input_={"application_id": int(app.id)},
            )
        ],
        stop_reason="tool_use",
    )
    unused_second_response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_must_not_run",
                name="queue_advance_decision",
                input_={
                    "application_id": int(app.id),
                    "reasoning": "Must not act after the focused application closes.",
                    "evidence": {},
                    "confidence": 0.9,
                },
            )
        ],
        stop_reason="tool_use",
    )
    client = _scripted_client([first_response, unused_second_response])
    original_dispatch = orchestrator.dispatch

    def _dispatch_then_revoke(name, args, *, db, agent_run, role):
        result = original_dispatch(
            name,
            args,
            db=db,
            agent_run=agent_run,
            role=role,
        )
        app.workable_disqualified = True
        db.flush()
        return result

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.dispatch",
        side_effect=_dispatch_then_revoke,
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error == "application_unavailable_during_cycle"
    assert client.messages.create.call_count == 1
    assert db.query(AgentDecision).filter(AgentDecision.role_id == role.id).count() == 0


def test_focused_manual_run_drops_provider_response_after_application_revocation(db):
    """Provider output cannot take effect after its focused app is revoked."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_stale",
                name="queue_advance_decision",
                input_={
                    "application_id": int(app.id),
                    "reasoning": "This response arrived after revocation.",
                    "evidence": {},
                    "confidence": 0.9,
                },
            )
        ],
        stop_reason="tool_use",
    )

    def _revoke_before_return(**kwargs):
        app.workable_disqualified = True
        db.flush()
        return response

    client = MagicMock()
    client.messages.create = MagicMock(side_effect=_revoke_before_return)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error == "application_unavailable_during_cycle"
    assert run.tools_called == []
    assert db.query(AgentDecision).filter(AgentDecision.role_id == role.id).count() == 0


def test_focused_manual_run_rechecks_application_between_tools(db):
    """One tool revoking the focus prevents every later tool in that response."""

    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_first",
                name="get_application",
                input_={"application_id": int(app.id)},
            ),
            _block_tool_use(
                tool_use_id="tu_must_not_run",
                name="queue_advance_decision",
                input_={
                    "application_id": int(app.id),
                    "reasoning": "Must be discarded after focus revocation.",
                    "evidence": {},
                    "confidence": 0.9,
                },
            ),
        ],
        stop_reason="tool_use",
    )
    client = _scripted_client([response])
    original_dispatch = orchestrator.dispatch
    dispatched_names: list[str] = []

    def _dispatch_then_revoke(name, args, *, db, agent_run, role):
        dispatched_names.append(name)
        result = original_dispatch(
            name,
            args,
            db=db,
            agent_run=agent_run,
            role=role,
        )
        if len(dispatched_names) == 1:
            app.workable_disqualified = True
            db.flush()
        return result

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.dispatch",
        side_effect=_dispatch_then_revoke,
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "aborted"
    assert run.error == "application_unavailable_during_cycle"
    assert dispatched_names == ["get_application"]
    assert db.query(AgentDecision).filter(AgentDecision.role_id == role.id).count() == 0


# ---------------------------------------------------------------------------
# Edge cases — the orchestrator's safety nets
# ---------------------------------------------------------------------------


def test_run_cycle_aborts_repeated_tool_loop_before_max_rounds(db):
    """An identical no-progress tool loop should trip the cheap breaker."""
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
    assert "no-progress circuit breaker" in (run.error or "")
    assert client.messages.create.call_count == orchestrator.MAX_IDENTICAL_TOOL_ROUNDS + 1


def test_build_system_prompt_called_once_per_cycle(db):
    """Perf regression guard: the system prompt is static within a cycle,
    so it must be built ONCE — not once per round. Building it inside the
    round loop re-ran ~4s of slow DB queries (role intent + recruiter
    notes, each opening a fresh SessionLocal) up to 18× per cycle, which
    under connection-pool contention caused the 600s+ pre-LLM hangs on
    role 31. This pins the build to a single call regardless of rounds.
    """
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    # Spin for the full round cap with no termination signal.
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
    ), patch(
        "app.agent_runtime.orchestrator.build_system_prompt",
        wraps=orchestrator.build_system_prompt,
    ) as spy_build:
        orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    # Multiple rounds executed before the no-progress breaker...
    assert client.messages.create.call_count == orchestrator.MAX_IDENTICAL_TOOL_ROUNDS + 1
    # ...but the system prompt was built exactly once.
    assert spy_build.call_count == 1


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
    ) as resolve_client:
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "budget_paused"
    assert "monthly USD cap" in (run.error or "")
    db.refresh(role)
    assert role.agent_paused_at is not None
    assert role.agent_paused_reason
    cards = _role_event_cards(db, role)
    assert len(cards) == 1
    assert cards[0]["event_type"] == "agent_budget_guard"
    assert cards[0]["severity"] == "warning"
    assert "budget" in cards[0]["title"].lower()
    resolve_client.assert_not_called()


def test_run_cycle_durably_pauses_when_org_credits_are_exhausted(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    depleted = InsufficientCreditsError(
        organization_id=int(org.id), required=20_000, available=0
    )
    client = MagicMock()

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.reserve_provider_usage", side_effect=depleted
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "budget_paused"
    assert "top up to resume" in (run.error or "")
    db.refresh(role)
    assert role.agent_paused_at is not None
    assert "top up to resume" in (role.agent_paused_reason or "")
    assert not client.messages.create.called


def test_run_cycle_durably_pauses_when_hard_role_admission_is_exhausted(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    capped = InsufficientRoleBudgetError(
        role_id=int(role.id), required=20_000, available=5_000
    )
    client = MagicMock()

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.reserve_provider_usage", side_effect=capped
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()

    assert run.status == "budget_paused"
    assert "monthly USD cap admission blocked" in (run.error or "")
    db.refresh(role)
    assert role.agent_paused_at is not None
    assert not client.messages.create.called


def test_run_cycle_token_budget_blocks_actions_from_over_budget_response(db):
    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_token_budget_per_cycle = 1_000
    app = _make_app(db, org=org, role=role)
    response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_over",
                name="queue_advance_decision",
                input_={
                    "application_id": int(app.id),
                    "reasoning": "Strong",
                    "evidence": {},
                    "confidence": 0.9,
                },
            )
        ],
        stop_reason="tool_use",
        input_tokens=900,
        output_tokens=200,
    )
    client = _scripted_client([response])
    with patch("app.agent_runtime.orchestrator.get_client_for_org", return_value=client):
        run = orchestrator.run_cycle(db, role=role, trigger="manual", application_id=app.id)
    assert run.status == "aborted"
    assert "token budget exceeded" in (run.error or "")
    assert run.decisions_emitted == 0


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
    assert run.error == "model_provider_failure"
    failed_call_metering = boom_client.messages.create.call_args.kwargs["metering"]
    assert failed_call_metering["trace_id"] == f"agent-run:{int(run.id)}"
    assert failed_call_metering["metadata"]["agent_run_id"] == int(run.id)
    cards = _role_event_cards(db, role)
    assert len(cards) == 1
    assert cards[0]["severity"] == "error"
    assert "network down" not in str(cards[0])


def test_run_cycle_does_not_replay_tool_exception_details_to_model(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)
    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_secret",
                    name="get_application",
                    input_={"application_id": int(app.id)},
                ),
            ],
            stop_reason="tool_use",
        ),
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_done",
                    name="agent_run_complete",
                    input_={"summary": "Stopped after a tool failure."},
                ),
            ],
            stop_reason="tool_use",
        ),
    ])

    original_dispatch = orchestrator.dispatch

    def _dispatch(name, arguments, **kwargs):
        if name == "get_application":
            raise RuntimeError("Authorization: Bearer tenant-secret")
        return original_dispatch(name, arguments, **kwargs)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch(
        "app.agent_runtime.orchestrator.dispatch",
        side_effect=_dispatch,
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )

    assert run.status == "succeeded"
    replayed_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    assert "tenant-secret" not in str(replayed_messages)
    assert "tool_execution_failed" in str(replayed_messages)


def test_run_cycle_records_safe_failure_when_model_client_is_unavailable(db):
    org = _make_org(db)
    role = _make_role(db, org)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org",
        side_effect=RuntimeError("Authorization: Bearer sk-ant-SECRET"),
    ):
        run = orchestrator.run_cycle(db, role=role, trigger="cron")
    db.commit()

    assert run.status == "failed"
    assert run.error == "model_client_unavailable"
    cards = _role_event_cards(db, role)
    assert len(cards) == 1
    assert cards[0]["severity"] == "error"
    assert "sk-ant-SECRET" not in str(cards[0])


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

    # The test harness follows the current production-safe pinned default.
    assert run.model_version == "claude-haiku-4-5-20251001"


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


# ---------------------------------------------------------------------------
# Cross-cycle memory — calibration writebacks
# ---------------------------------------------------------------------------


def test_aborted_cycle_persists_last_cycle_to_calibration(db):
    """An aborted run must still write a last_cycle summary so the next
    cycle's system prompt can render 'last cycle: aborted, rounds=18'."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    # Spin forever.
    spinning = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_spin",
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
    db.refresh(role)

    assert run.status == "aborted"
    cal = role.agent_calibration or {}
    assert "last_cycle" in cal
    assert cal["last_cycle"]["status"] == "aborted"
    assert cal["last_cycle"]["finished_via_complete"] is False
    assert cal["last_cycle"]["rounds_used"] == orchestrator.MAX_IDENTICAL_TOOL_ROUNDS + 1


def test_successful_cycle_persists_last_cycle_with_finished_via_complete(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_done",
                    name="agent_run_complete",
                    input_={"summary": "Nothing to do."},
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
    db.refresh(role)

    assert run.status == "succeeded"
    cal = role.agent_calibration or {}
    assert cal["last_cycle"]["status"] == "succeeded"
    assert cal["last_cycle"]["finished_via_complete"] is True
    assert cal["last_cycle"]["rounds_used"] == 1


def test_record_observation_tool_appends_to_calibration_notes(db):
    """The record_observation tool must persist a note that survives the
    cycle and would show up in the next cycle's system prompt."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_note",
                    name="record_observation",
                    input_={
                        "note": "cohort clusters around taali_score 60-65; threshold may be too high",
                        "kind": "pattern",
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_done",
                    name="agent_run_complete",
                    input_={"summary": "Noted a pattern, no action this cycle."},
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
    db.refresh(role)

    assert run.status == "succeeded"
    notes = (role.agent_calibration or {}).get("notes") or []
    assert len(notes) == 1
    assert notes[0]["kind"] == "pattern"
    assert "60-65" in notes[0]["note"]
    assert notes[0]["agent_run_id"] == int(run.id)


def test_record_observation_empty_note_is_skipped(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    client = _scripted_client([
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_empty",
                    name="record_observation",
                    input_={"note": "   ", "kind": "pattern"},
                ),
            ],
            stop_reason="tool_use",
        ),
        _response(
            blocks=[
                _block_tool_use(
                    tool_use_id="tu_done",
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
        _run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()
    db.refresh(role)

    notes = (role.agent_calibration or {}).get("notes") or []
    assert notes == []


def test_record_observation_survives_aborted_cycle(db):
    """A note written mid-cycle must persist even when the cycle later aborts.

    This is the whole point of record_observation: aborts no longer
    erase the agent's mid-cycle learning.
    """
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_app(db, org=org, role=role)

    # First response: record an observation. Subsequent responses: spin
    # on get_application so we never hit agent_run_complete and abort.
    note_response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_note",
                name="record_observation",
                input_={"note": "must remember this", "kind": "todo"},
            ),
        ],
        stop_reason="tool_use",
    )
    spin_response = _response(
        blocks=[
            _block_tool_use(
                tool_use_id="tu_spin",
                name="get_application",
                input_={"application_id": int(app.id)},
            ),
        ],
        stop_reason="tool_use",
    )

    responses = [note_response] + [spin_response] * (orchestrator.MAX_TOOL_ROUNDS - 1)
    client = _scripted_client(responses)

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        run = orchestrator.run_cycle(
            db, role=role, trigger="manual", application_id=app.id
        )
    db.commit()
    db.refresh(role)

    assert run.status == "aborted"
    notes = (role.agent_calibration or {}).get("notes") or []
    assert len(notes) == 1
    assert notes[0]["note"] == "must remember this"
    assert notes[0]["kind"] == "todo"
