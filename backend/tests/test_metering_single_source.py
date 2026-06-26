"""The MeteredAnthropicClient wrapper is the single writer of usage_event
for cv_match scoring — one event per actual Anthropic call, FK-linked to
claude_call_log.

Before this, the cv_match runner passed metering={"skip": True} and
cv_score_orchestrator recorded a single post-call usage_event. That
missed errored/retried calls and left usage_event ~73% short of actual
Anthropic spend (claude_call_log proved it on 2026-05-22). Now the
wrapper records every call; the orchestrator records only cache hits.

The cv_match score call now goes through the forced-tool-use gateway
(``generate_structured(..., use_tool_use=True)``): the model emits
``CVMatchResult`` as the ``emit_cv_match_result`` tool's ``.input`` dict,
not as a ``content[0].text`` JSON blob. The stub Anthropic responses below
therefore return a ``tool_use`` block whose ``.input`` is the parsed dict —
the shape the gateway's ``_extract_tool_input`` reads — rather than a text
block (which the gateway would reject as "did not emit the expected tool").

A cache-miss ``run_cv_match`` now makes TWO real Anthropic calls, each
forcing a different tool:

1. the main score call, forcing ``emit_cv_match_result`` (``CVMatchResult``);
2. a focused graded-requirement pass (``cv_matching.graded``), forcing
   ``grade_requirements`` (``GradedRequirements``).

The stub therefore inspects the forced ``tool_choice`` on each ``create``
call and returns the matching tool block — the cv-match payload for the
former, a valid ``grade_requirements`` payload for the latter. If it always
returned the cv-match block (as it used to), the graded pass would reject
the wrong block, retry once, and the wrapper would record those extra
*real* retried calls as extra usage_events — which is the wrapper behaving
correctly (one event per actual call), not a bug. Satisfying the graded
tool keeps the graded pass to a single successful call, so the cache-miss
path makes exactly two metered ``score`` calls.

(Archetype synthesis is monkeypatched to ``None`` in the tests, so it
makes no call; only the score + graded calls hit the stub.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import archetype_synthesizer
from app.cv_matching.runner import run_cv_match
from app.cv_matching.schemas import Priority, RequirementInput
from app.llm.structured import _default_tool_name
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.metered_anthropic_client import MeteredAnthropicClient

# The tool name the gateway forces for CVMatchResult — derived once so the
# stub block names match what _extract_tool_input looks for.
from app.cv_matching.schemas import CVMatchResult as _CVMatchResult

_CV_MATCH_TOOL = _default_tool_name(_CVMatchResult)

# The graded pass forces an explicit tool_name="grade_requirements"
# (see cv_matching.graded.grade_requirements), NOT the derived default — so
# we hard-code the same literal the runner uses.
_GRADE_TOOL = "grade_requirements"


def _valid_grade_payload() -> dict:
    """A minimal valid ``GradedRequirements`` tool input.

    One graded entry for the single ``jd_req_1`` requirement the tests pass,
    so the graded pass succeeds on the FIRST attempt (no retries) and the
    runner credits a real graded match_score rather than the -1 fallback.
    """
    return {
        "requirements": [
            {
                "requirement_id": "jd_req_1",
                "reasoning": "Evidences Python.",
                "assessable": True,
                "match_score": 80,
            }
        ]
    }


def _valid_cv_match_payload() -> dict:
    from app.cv_matching import PROMPT_VERSION

    return {
        "prompt_version": PROMPT_VERSION,
        "dimension_scores": {
            "skills_coverage": 80.0, "skills_depth": 75.0, "title_trajectory": 70.0,
            "seniority_alignment": 65.0, "industry_match": 60.0, "tenure_pattern": 55.0,
        },
        "skills_match_score": 0, "experience_relevance_score": 0,
        "requirements_assessment": [{
            "requirement_id": "jd_req_1", "requirement": "5+ years Python",
            "priority": "must_have", "evidence_quotes": ["Python developer for 6 years"],
            "evidence_start_char": 0, "evidence_end_char": 28, "reasoning": "Evidences Python.",
            "status": "met", "match_tier": "exact", "impact": "Core.", "confidence": "high",
        }],
        "matching_skills": ["Python"], "missing_skills": [],
        "experience_highlights": [], "concerns": [], "summary": "Strong fit.",
    }


@dataclass
class _Usage:
    input_tokens: int = 1000
    output_tokens: int = 2000
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _ToolUseBlock:
    """A forced-tool-use content block: the gateway reads ``.input``."""
    input: dict
    name: str
    type: str = "tool_use"


@dataclass
class _Resp:
    payload: dict
    tool_name: str = _CV_MATCH_TOOL
    @property
    def content(self):
        # Forced-tool-use shape: one tool_use block whose .input IS the
        # structured result the gateway validates. ``tool_name`` matches the
        # tool the call forced (cv-match vs grade_requirements) so
        # ``_extract_tool_input`` finds it on the FIRST attempt.
        return [_ToolUseBlock(input=self.payload, name=self.tool_name)]
    @property
    def usage(self):
        return _Usage()
    id = "req_stub_1"
    stop_reason = "tool_use"


@dataclass
class _Msgs:
    """Stub ``messages`` resource that answers the FORCED tool per call.

    ``run_cv_match`` makes two forced-tool calls — the score call forces
    ``emit_cv_match_result`` and the graded pass forces ``grade_requirements``
    — so a single fixed payload can't satisfy both. We read the forced tool
    off ``tool_choice`` (built by ``llm.core.one_call``) and return the
    matching block; an unexpected/absent tool_choice falls back to the
    cv-match payload so the simpler text-mode stubs still work.
    """

    payload: dict
    grade_payload: dict = field(default_factory=_valid_grade_payload)
    calls: list = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        forced = (kwargs.get("tool_choice") or {}).get("name")
        if forced == _GRADE_TOOL:
            return _Resp(payload=self.grade_payload, tool_name=_GRADE_TOOL)
        return _Resp(payload=self.payload, tool_name=_CV_MATCH_TOOL)


@dataclass
class _Inner:
    messages: _Msgs


def test_score_cache_miss_writes_one_linked_usage_event(db, monkeypatch):
    """Cache-miss score through the wrapper → one usage_event PER ACTUAL
    Anthropic call, each FK-linked to its own call_log row. No skip, no
    double-count.

    A cache-miss run makes two real ``score`` calls — the main score call
    and the graded-requirement pass (both metered ``feature="score"``,
    same entity) — so we assert exactly two ``score`` usage_events and two
    FK-linked call_log rows. The invariant under test is one event per real
    call, not "one event total"; with the graded pass succeeding in a
    single call there are two real calls."""
    monkeypatch.setattr(archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None)
    # Wrapper's fresh-session writes go to the test DB.
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    inner = _Inner(messages=_Msgs(payload=_valid_cv_match_payload()))
    wrapped = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    out = run_cv_match(
        cv_text="Python developer for 6 years",
        jd_text="Senior Python role",
        requirements=[RequirementInput(id="jd_req_1", requirement="5+ years Python", priority=Priority.MUST_HAVE)],
        client=wrapped,
        skip_cache=True,
        # No ``db`` here: the wrapper self-manages fresh sessions for both
        # writes, which avoids SQLite write-lock contention with the open
        # test session. In prod the orchestrator passes db for txn coupling
        # (Postgres MVCC handles the concurrent call_log session fine).
        metering_context={
            "organization_id": int(org.id),
            "role_id": None,
            "entity_id": "application:42",
        },
    )
    assert out.scoring_status.value == "ok"

    # Read via a fresh session so we see the wrapper's committed rows.
    from tests.conftest import TestingSessionLocal
    check = TestingSessionLocal()
    try:
        events = check.query(UsageEvent).filter(
            UsageEvent.organization_id == org.id, UsageEvent.feature == "score",
        ).all()
        # Two real score calls: the main score call + the graded pass.
        assert len(events) == 2
        # Both attributed to the same application entity.
        assert {e.entity_id for e in events} == {"application:42"}

        logs = check.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
        # One call_log per real call, each FK-linked to a distinct usage_event.
        assert len(logs) == 2
        assert all(log.feature_hint == "score" for log in logs)
        linked_event_ids = {log.usage_event_id for log in logs}
        assert linked_event_ids == {e.id for e in events}
        # No double-count: the FK links are 1:1 (no two call_logs share an event).
        assert len(linked_event_ids) == 2
    finally:
        check.close()


def test_score_with_caller_db_in_context_still_links_call_log(db, monkeypatch):
    """Regression for the #253 FK race: production threads the caller's
    open session as ``metering_context["db"]``. The wrapper used to write
    the usage_event into that *uncommitted* transaction and then write
    claude_call_log in a *separate* fresh session — which couldn't see
    the uncommitted parent and raised
    ``claude_call_log_usage_event_id_fkey`` violation, silently dropping
    every score + pre-screen call_log row in prod.

    The wrapper now ignores ``db`` and self-manages fresh, committed
    sessions for both writes, so the call_log row lands FK-linked even
    when a caller db is supplied. This test pins that: passing ``db`` must
    NOT regress linkage."""
    monkeypatch.setattr(archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None)
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    org = Organization(name="O2", slug=f"o2-{id(db)}")
    db.add(org); db.commit()

    inner = _Inner(messages=_Msgs(payload=_valid_cv_match_payload()))
    wrapped = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    out = run_cv_match(
        cv_text="Python developer for 6 years",
        jd_text="Senior Python role",
        requirements=[RequirementInput(id="jd_req_1", requirement="5+ years Python", priority=Priority.MUST_HAVE)],
        client=wrapped,
        skip_cache=True,
        metering_context={
            "organization_id": int(org.id),
            "role_id": None,
            "entity_id": "application:99",
            "db": db,  # prod threads the open caller session here
        },
    )
    assert out.scoring_status.value == "ok"

    check = TestingSessionLocal()
    try:
        events = check.query(UsageEvent).filter(
            UsageEvent.organization_id == org.id, UsageEvent.feature == "score",
        ).all()
        # Two real score calls (main score + graded pass).
        assert len(events) == 2

        logs = check.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
        assert len(logs) == 2, "call_log row dropped — FK race regressed"
        # The #253 invariant: every call_log row is FK-linked to a committed
        # usage_event even when the caller threads its open ``db`` session.
        linked = {log.usage_event_id for log in logs}
        assert None not in linked, "call_log not FK-linked to usage_event"
        assert linked == {e.id for e in events}, "call_log not FK-linked to usage_event"
    finally:
        check.close()


def test_score_call_does_not_use_skip_when_context_present(monkeypatch):
    """Guard: the runner must NOT pass metering={'skip': True} when a
    metering_context is supplied — that's what caused the leak. Use a
    BARE stub client (no wrapper) so the metering kwarg isn't stripped
    and we can inspect exactly what the runner built."""
    monkeypatch.setattr(archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None)

    inner = _Inner(messages=_Msgs(payload=_valid_cv_match_payload()))
    run_cv_match(
        cv_text="cv", jd_text="jd",
        requirements=[RequirementInput(id="jd_req_1", requirement="x", priority=Priority.MUST_HAVE)],
        client=inner, skip_cache=True,  # bare stub — sees the metering kwarg
        metering_context={"organization_id": 1, "entity_id": "application:1"},
    )
    assert inner.messages.calls, "expected at least one Claude call"
    for c in inner.messages.calls:
        metering = c.get("metering") or {}
        assert metering.get("feature") == "score"
        assert not metering.get("skip"), "score call must not skip metering"


def test_score_call_skips_without_context(monkeypatch):
    """Conversely: with no metering_context (direct/eval call, bare
    client) the runner falls back to skip so it doesn't fabricate
    org-less events."""
    monkeypatch.setattr(archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None)

    inner = _Inner(messages=_Msgs(payload=_valid_cv_match_payload()))
    run_cv_match(
        cv_text="cv", jd_text="jd",
        requirements=[RequirementInput(id="jd_req_1", requirement="x", priority=Priority.MUST_HAVE)],
        client=inner, skip_cache=True,
        metering_context=None,
    )
    assert inner.messages.calls
    for c in inner.messages.calls:
        assert (c.get("metering") or {}).get("skip") is True
