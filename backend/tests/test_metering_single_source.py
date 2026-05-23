"""The MeteredAnthropicClient wrapper is the single writer of usage_event
for cv_match scoring — one event per actual Anthropic call, FK-linked to
claude_call_log.

Before this, the cv_match runner passed metering={"skip": True} and
cv_score_orchestrator recorded a single post-call usage_event. That
missed errored/retried calls and left usage_event ~73% short of actual
Anthropic spend (claude_call_log proved it on 2026-05-22). Now the
wrapper records every call; the orchestrator records only cache hits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import archetype_synthesizer
from app.cv_matching.runner import run_cv_match
from app.cv_matching.schemas import Priority, RequirementInput
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.metered_anthropic_client import MeteredAnthropicClient


def _valid_cv_match_json() -> str:
    import json
    from app.cv_matching import PROMPT_VERSION

    return json.dumps({
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
    })


@dataclass
class _Usage:
    input_tokens: int = 1000
    output_tokens: int = 2000
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Resp:
    text: str
    @property
    def content(self):
        @dataclass
        class _B:
            text: str
        return [_B(text=self.text)]
    @property
    def usage(self):
        return _Usage()
    id = "req_stub_1"


@dataclass
class _Msgs:
    body: str
    calls: list = field(default_factory=list)
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(text=self.body)


@dataclass
class _Inner:
    messages: _Msgs


def test_score_cache_miss_writes_one_linked_usage_event(db, monkeypatch):
    """Cache-miss score through the wrapper → exactly one usage_event
    (feature=score, role attributed) FK-linked to one call_log row.
    No skip, no double-count."""
    monkeypatch.setattr(archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None)
    # Wrapper's fresh-session writes go to the test DB.
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    inner = _Inner(messages=_Msgs(body=_valid_cv_match_json()))
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
        assert len(events) == 1
        assert events[0].entity_id == "application:42"

        logs = check.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
        assert len(logs) == 1
        assert logs[0].usage_event_id == events[0].id
        assert logs[0].feature_hint == "score"
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

    inner = _Inner(messages=_Msgs(body=_valid_cv_match_json()))
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
        assert len(events) == 1

        logs = check.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
        assert len(logs) == 1, "call_log row dropped — FK race regressed"
        assert logs[0].usage_event_id == events[0].id, "call_log not FK-linked to usage_event"
    finally:
        check.close()


def test_score_call_does_not_use_skip_when_context_present(monkeypatch):
    """Guard: the runner must NOT pass metering={'skip': True} when a
    metering_context is supplied — that's what caused the leak. Use a
    BARE stub client (no wrapper) so the metering kwarg isn't stripped
    and we can inspect exactly what the runner built."""
    monkeypatch.setattr(archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None)

    inner = _Inner(messages=_Msgs(body=_valid_cv_match_json()))
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

    inner = _Inner(messages=_Msgs(body=_valid_cv_match_json()))
    run_cv_match(
        cv_text="cv", jd_text="jd",
        requirements=[RequirementInput(id="jd_req_1", requirement="x", priority=Priority.MUST_HAVE)],
        client=inner, skip_cache=True,
        metering_context=None,
    )
    assert inner.messages.calls
    for c in inner.messages.calls:
        assert (c.get("metering") or {}).get("skip") is True
