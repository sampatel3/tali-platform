"""Three metering leaks closed after the 2026-05-21 reconciliation showed
-73.7% drift (Anthropic billed $69.78, platform metered $18.35):

1. **Orchestrator double-record** (Sonnet 4.5 over-counted at exactly 2×).
   Every paid round now passes a feature-bearing ``MeteringContext`` to the
   shared wrapper. The wrapper durably writes the one UsageEvent and linked
   ClaudeCallLog; the orchestrator only keeps an AgentRun token/cost rollup.

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

from app.llm import MeteringContext
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent


# ---------------------------------------------------------------------------
# Bug 1: orchestrator must not double-record
# ---------------------------------------------------------------------------

def test_orchestrator_uses_one_feature_bearing_metering_context_per_round():
    """Each round is metered once by the wrapper with autonomous attribution.

    The AgentRun retains a raw-cost rollup for operational reporting, but the
    orchestrator must not create a second UsageEvent itself.
    """
    from app.llm import MeteringContext
    from app.services.pricing_service import Feature

    round_metering = MeteringContext(
        feature=Feature.AGENT_AUTONOMOUS,
        organization_id=12,
        role_id=34,
        entity_id="34",
        metadata={"agent_run_id": 56, "round": 2},
    )
    serialised = round_metering.as_dict()
    assert serialised["feature"] == Feature.AGENT_AUTONOMOUS
    assert serialised["organization_id"] == 12
    assert serialised["metadata"] == {"agent_run_id": 56, "round": 2}

    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agent_runtime" / "orchestrator.py"
    content = src.read_text()
    assert "feature=Feature.AGENT_AUTONOMOUS" in content
    create_idx = content.find("response = one_call(")
    assert create_idx > -1, "could not find one_call call site in orchestrator"
    call_block = content[create_idx:create_idx + 400]
    assert "metering=round_metering" in call_block, (
        "the per-round one_call must be passed the feature-bearing context "
        f"context. Got:\n{call_block}"
    )
    assert "record_event(" not in content, (
        "orchestrator must not write a second UsageEvent after the metered wrapper"
    )


# ---------------------------------------------------------------------------
# Bug 2: cv_match retry tokens accumulate, not overwrite
# ---------------------------------------------------------------------------

def test_call_claude_accumulates_tokens_across_retries():
    """If a logical operation makes more than one Anthropic call (e.g. a
    validation-failure retry), tokens from each attempt must ADD — not
    overwrite. Anthropic charges for every call; the platform must record
    all of them.

    The per-attempt token accumulation that ``cv_matching.runner`` used to
    do inline on ``_RunContext`` now lives in the shared
    ``app.llm.core.CallUsage`` sink, which ``one_call`` folds each response
    into. Pin the accumulation invariant at that layer."""
    from app.llm.core import CallUsage, one_call

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

    sink = CallUsage()
    metering = MeteringContext.skipped(metered_by="test")
    one_call(client, model="m", messages=[{"role": "user", "content": "x"}],
             max_tokens=16, metering=metering, usage_sink=sink)
    one_call(client, model="m", messages=[{"role": "user", "content": "x"}],
             max_tokens=16, metering=metering, usage_sink=sink)

    # Sum across both calls — not just the last one.
    assert sink.input_tokens == 1800, f"expected 1800, got {sink.input_tokens}"
    assert sink.output_tokens == 380, f"expected 380, got {sink.output_tokens}"


# ---------------------------------------------------------------------------
# Bug 3: pre-screen error path still records tokens
# ---------------------------------------------------------------------------

def test_pre_screen_error_with_tokens_still_meters_via_wrapper(db, monkeypatch):
    """Anthropic returned 200 OK with real token counts but the JSON was
    unparseable, so the runner returns decision="error". Those tokens
    were still billed, so a usage_event (FK-linked to claude_call_log)
    must be written.

    #253 moved this recording out of the pre-screen service and into the
    MeteredAnthropicClient wrapper, which meters every real call
    regardless of how the caller later parses the body. This pins the
    behaviour at that layer: a parse failure must NOT drop the row."""
    from dataclasses import dataclass, field

    from app.cv_matching.runner_pre_screen import run_pre_screen
    from app.cv_matching.schemas import Priority, RequirementInput
    from app.models.claude_call_log import ClaudeCallLog
    from app.services import metered_anthropic_client as mac
    from app.services.metered_anthropic_client import MeteredAnthropicClient
    from tests.conftest import TestingSessionLocal

    # Wrapper's fresh-session writes go to the test DB.
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    @dataclass
    class _Usage:
        input_tokens: int = 2400
        output_tokens: int = 300
        cache_read_input_tokens: int = 0
        cache_creation_input_tokens: int = 0

    @dataclass
    class _Resp:
        @property
        def content(self):
            @dataclass
            class _B:
                text: str
            return [_B(text="not valid json {{")]  # 200 OK, unparseable body

        @property
        def usage(self):
            return _Usage()

        id = "req_stub_err"

    @dataclass
    class _Msgs:
        calls: list = field(default_factory=list)

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Resp()

    @dataclass
    class _Inner:
        messages: _Msgs

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    wrapped = MeteredAnthropicClient(inner=_Inner(messages=_Msgs()), organization_id=int(org.id))
    pre = run_pre_screen(
        "real cv text",
        "hire backend",
        [RequirementInput(id="r1", requirement="python", priority=Priority.MUST_HAVE)],
        client=wrapped,
        skip_cache=True,
        metering_context={"organization_id": int(org.id), "role_id": None, "entity_id": "application:7"},
    )
    assert pre.decision == "error"

    # Read via a fresh session so we see the wrapper's committed rows.
    check = TestingSessionLocal()
    try:
        events = check.query(UsageEvent).filter(
            UsageEvent.organization_id == org.id,
            UsageEvent.feature == "prescreen",
        ).all()
        assert len(events) == 1, (
            "pre-screen 200-OK-but-unparseable call must still write a "
            "UsageEvent — Anthropic billed for those tokens"
        )
        assert events[0].input_tokens == 2400
        assert events[0].output_tokens == 300

        logs = check.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
        assert len(logs) == 1
        assert logs[0].usage_event_id == events[0].id
    finally:
        check.close()


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
