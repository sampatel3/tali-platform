"""Three metering leaks closed after the 2026-05-21 reconciliation showed
-73.7% drift (Anthropic billed $69.78, platform metered $18.35):

1. **Orchestrator double-record** (Sonnet 4.5 over-counted at exactly 2×).
   The agent loop called ``client.messages.create(...)`` on a
   ``MeteredAnthropicClient`` *without* a ``metering=`` kwarg, so the
   wrapper auto-recorded an event as ``Feature.OTHER``. Immediately
   after, the loop also called ``record_event(..., Feature.AGENT_AUTONOMOUS)``
   explicitly. Two rows per Anthropic call. Fix: pass
   ``metering={"skip": True}`` so the wrapper stays out of the way.

2. **cv_match runner retry overwrite** (Haiku under-counted).
   ``_call_claude`` set ``ctx.input_tokens = ...`` on every retry,
   so when validation failed and the runner looped, the first attempt's
   token counts were silently dropped. Fix: accumulate with ``+=``.

3. **Pre-screen error path drops tokens** (Haiku under-counted, biggest
   single leak). When the Anthropic call returned a 200 but the JSON
   parse failed, ``run_pre_screen`` returned ``decision="error"`` with
   real ``input_tokens`` / ``output_tokens`` populated. The orchestrator
   then early-returned at the error guard, skipping the metering block
   below. 7,668 such errors on 2026-05-21 alone. Fix: meter BEFORE the
   error guard so charged tokens always get recorded, regardless of
   whether the response was parseable.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent


# SQLite BigInteger PK workaround for AgentRun.
_BIG_PK = {"agent_runs": 0}

def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]

event.listen(AgentRun, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Bug 1: orchestrator must not double-record
# ---------------------------------------------------------------------------

def test_orchestrator_passes_metering_skip_to_messages_create():
    """Read the orchestrator source and verify the ``client.messages.create``
    call carries ``metering={"skip": True}``. This is the only way to keep
    the wrapper from auto-recording an event alongside the explicit
    ``record_event`` below."""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agent_runtime" / "orchestrator.py"
    content = src.read_text()
    # The whole client.messages.create call site
    create_idx = content.find("response = client.messages.create(")
    assert create_idx > -1, "could not find messages.create call site"
    # Look at the next ~600 chars (the call args)
    call_block = content[create_idx:create_idx + 600]
    assert '"skip": True' in call_block, (
        "orchestrator client.messages.create must pass metering={'skip': True}"
        " or the wrapper auto-records a duplicate UsageEvent as Feature.OTHER. "
        f"Got:\n{call_block}"
    )


# ---------------------------------------------------------------------------
# Bug 2: cv_match retry tokens accumulate, not overwrite
# ---------------------------------------------------------------------------

def test_call_claude_accumulates_tokens_across_retries():
    """If the runner retries (validation failure), tokens from each
    attempt must add — not overwrite. Anthropic charges for every call;
    the platform must record all of them."""
    from app.cv_matching.runner import _RunContext, _call_claude

    ctx = _RunContext(trace_id="t", cv_hash="c", jd_hash="j", started_at=0.0)

    def make_response(in_tok, out_tok):
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"role_fit_score": 50}')],
            usage=SimpleNamespace(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    client = MagicMock()
    client.messages.create.side_effect = [
        make_response(in_tok=1000, out_tok=200),  # first attempt
        make_response(in_tok=800, out_tok=180),   # retry
    ]

    _call_claude(client, messages=[{"role": "user", "content": "x"}], ctx=ctx)
    _call_claude(client, messages=[{"role": "user", "content": "x"}], ctx=ctx)

    # Sum across both calls — not just the last one.
    assert ctx.input_tokens == 1800, f"expected 1800, got {ctx.input_tokens}"
    assert ctx.output_tokens == 380, f"expected 380, got {ctx.output_tokens}"


# ---------------------------------------------------------------------------
# Bug 3: pre-screen error path still records tokens
# ---------------------------------------------------------------------------

def test_pre_screen_error_with_tokens_still_writes_event(db):
    """When Anthropic returned a 200 OK but JSON parsing failed, the
    response carries real token counts. The function used to early-
    return on the error guard before recording the event — losing the
    charged tokens. Now metering happens BEFORE the guard."""
    from app.services.pre_screening_service import execute_pre_screen_only

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        job_spec_text="hire backend", agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        cv_text="real cv text" * 50,
    )
    db.add(app); db.commit()

    # Force run_pre_screen to return an error decision but with tokens
    # populated — same shape the runner returns on JSON parse failure.
    fake_pre = SimpleNamespace(
        decision="error",
        reason="json_parse_failed: ...",
        prompt_version="pre_screen_v2.0",
        model_version="claude-haiku-4-5-20251001",
        trace_id="t-1",
        cache_hit=False,
        score=None,
        input_tokens=2400,
        output_tokens=300,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )
    with patch("app.cv_matching.runner_pre_screen.run_pre_screen", return_value=fake_pre):
        result = execute_pre_screen_only(app, db=db)

    assert result["status"] == "error"
    # The error guard ran. But metering should still have written a row
    # for the tokens the LLM call actually consumed.
    events = db.query(UsageEvent).filter(
        UsageEvent.organization_id == org.id,
        UsageEvent.feature == "prescreen",
    ).all()
    assert len(events) == 1, (
        "pre-screen error with non-zero tokens must still write a UsageEvent "
        "— Anthropic billed for those tokens. Found {len(events)} events."
    )
    ev = events[0]
    assert ev.input_tokens == 2400
    assert ev.output_tokens == 300


def test_pre_screen_zero_token_path_does_not_write_event(db):
    """The exception path (client.messages.create raised) returns a
    PreScreenResult with no tokens. Don't pollute the table with
    zero-token rows in that case — the call never consumed credits."""
    from app.services.pre_screening_service import execute_pre_screen_only

    org = Organization(name="O2", slug=f"o2-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        job_spec_text="hire backend", agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        cv_text="real cv text" * 50,
    )
    db.add(app); db.commit()

    fake_pre = SimpleNamespace(
        decision="error", reason="claude_call_failed: timeout",
        prompt_version="pre_screen_v2.0", model_version="claude-haiku-4-5-20251001",
        trace_id="t-2", cache_hit=False, score=None,
        input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
    )
    with patch("app.cv_matching.runner_pre_screen.run_pre_screen", return_value=fake_pre):
        execute_pre_screen_only(app, db=db)

    events = db.query(UsageEvent).filter(
        UsageEvent.organization_id == org.id,
        UsageEvent.feature == "prescreen",
    ).all()
    assert len(events) == 0
