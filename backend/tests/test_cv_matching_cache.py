"""Cache adapter integration tests.

Uses the project's SQLite-in-memory ``db`` fixture so cache writes and reads
hit the real ORM, not a mock. The runner is exercised end-to-end with a
stubbed Anthropic client; verifies that identical inputs produce a single
Claude call and matching outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.cv_matching import cache as cv_cache
from app.cv_matching import (
    MODEL_VERSION,
    PROMPT_VERSION,
    Priority,
    RequirementInput,
    ScoringStatus,
)
from app.cv_matching.runner import run_cv_match
from app.cv_matching.schemas import CVMatchOutput, Recommendation


CV = "Senior engineer with AWS Glue experience and strong Python skills."
JD = "Hiring AWS Glue engineer."


def _reqs():
    return [
        RequirementInput(
            id="req_1",
            requirement="AWS Glue",
            priority=Priority.MUST_HAVE,
        )
    ]


def _valid_response_json() -> str:
    quote = "AWS Glue experience"
    return json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "skills_match_score": 90,
            "experience_relevance_score": 85,
            "requirements_assessment": [
                {
                    "requirement_id": "req_1",
                    "requirement": "AWS Glue",
                    "priority": "must_have",
                    "status": "met",
                    "evidence_quote": quote,
                    "evidence_start_char": CV.find(quote),
                    "evidence_end_char": CV.find(quote) + len(quote),
                    "impact": "Direct match.",
                    "confidence": "high",
                }
            ],
            "matching_skills": ["AWS Glue", "Python"],
            "missing_skills": [],
            "experience_highlights": ["AWS Glue experience"],
            "concerns": [],
            "summary": "Direct match on AWS Glue.",
        }
    )


# ---------- Stub client (mirrors test_cv_matching_runner) ----------


@dataclass
class _StubResponse:
    text: str

    @property
    def content(self):
        return [_StubBlock(text=self.text)]

    @property
    def usage(self):
        return _StubUsage(100, 200)


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _StubCount:
    input_tokens: int


@dataclass
class _StubMessages:
    body: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(text=self.body)

    def count_tokens(self, **kwargs):
        return _StubCount(100)


@dataclass
class _StubClient:
    messages: _StubMessages


# ---------- compute_cache_key ----------


def test_cache_key_is_deterministic():
    k1 = cv_cache.compute_cache_key(
        cv_text=CV,
        jd_text=JD,
        requirements=_reqs(),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    k2 = cv_cache.compute_cache_key(
        cv_text=CV,
        jd_text=JD,
        requirements=_reqs(),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_cache_key_differs_on_any_input_change():
    base = cv_cache.compute_cache_key(
        cv_text=CV,
        jd_text=JD,
        requirements=_reqs(),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    different_cv = cv_cache.compute_cache_key(
        cv_text=CV + " ",
        jd_text=JD,
        requirements=_reqs(),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    different_prompt = cv_cache.compute_cache_key(
        cv_text=CV,
        jd_text=JD,
        requirements=_reqs(),
        prompt_version="cv_match_v999",
        model_version=MODEL_VERSION,
    )
    different_model = cv_cache.compute_cache_key(
        cv_text=CV,
        jd_text=JD,
        requirements=_reqs(),
        prompt_version=PROMPT_VERSION,
        model_version="claude-other",
    )
    assert different_cv != base
    assert different_prompt != base
    assert different_model != base


# ---------- DB round-trip ----------


def test_cache_round_trip_through_db(db, monkeypatch):
    """get/set against the real CvScoreCache table on the SQLite memory DB."""
    # Make the cache module use the test SessionLocal.
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(
        "app.cv_matching.cache.SessionLocal",
        TestingSessionLocal,
        raising=False,
    )
    # Patch the import inside cache.get/cache.set
    import app.cv_matching.cache as cv_cache_module

    real_get = cv_cache_module.get
    real_set = cv_cache_module.set

    # Build a fully-populated CVMatchOutput as if a run had succeeded.
    out = CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        skills_match_score=90,
        experience_relevance_score=80,
        requirements_assessment=[],
        matching_skills=["AWS Glue"],
        missing_skills=[],
        experience_highlights=[],
        concerns=[],
        summary="Synthetic.",
        requirements_match_score=70,
        cv_fit_score=85,
        role_fit_score=75,
        recommendation=Recommendation.YES,
        scoring_status=ScoringStatus.OK,
        model_version=MODEL_VERSION,
        trace_id="trace-1",
    )
    key = "test-key-" + "0" * 50

    # cache.set/get use SessionLocal at call time — patch the lazy import.
    def _patched_session():
        return TestingSessionLocal()

    monkeypatch.setattr(
        "app.platform.database.SessionLocal",
        TestingSessionLocal,
        raising=False,
    )

    real_set(key, out)
    fetched = real_get(key)
    assert fetched is not None
    assert fetched.role_fit_score == 75
    assert fetched.prompt_version == PROMPT_VERSION
    # Failed runs are not cached.
    out_failed = out.model_copy(update={"scoring_status": ScoringStatus.FAILED})
    real_set("failed-key-" + "0" * 50, out_failed)
    assert real_get("failed-key-" + "0" * 50) is None


def test_runner_caches_and_reuses(db, monkeypatch):
    """End-to-end: same inputs twice = one Claude call, identical output."""
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(
        "app.platform.database.SessionLocal",
        TestingSessionLocal,
        raising=False,
    )

    client = _StubClient(messages=_StubMessages(body=_valid_response_json()))

    out1 = run_cv_match(CV, JD, _reqs(), client=client, skip_cache=False)
    assert out1.scoring_status == ScoringStatus.OK
    assert len(client.messages.calls) == 1

    out2 = run_cv_match(CV, JD, _reqs(), client=client, skip_cache=False)
    assert out2.scoring_status == ScoringStatus.OK
    # No new call — second run hit the cache.
    assert len(client.messages.calls) == 1
    # Outputs are identical except for trace_id (each call gets its own)
    # and cache_hit (the second run sets it to True).
    a = out1.model_dump()
    b = out2.model_dump()
    for key in ("trace_id", "cache_hit"):
        a.pop(key)
        b.pop(key)
    assert a == b
    assert out1.cache_hit is False
    assert out2.cache_hit is True


def test_runner_skip_cache_bypasses(db, monkeypatch):
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(
        "app.platform.database.SessionLocal",
        TestingSessionLocal,
        raising=False,
    )

    client = _StubClient(messages=_StubMessages(body=_valid_response_json()))

    run_cv_match(CV, JD, _reqs(), client=client, skip_cache=True)
    run_cv_match(CV, JD, _reqs(), client=client, skip_cache=True)
    assert len(client.messages.calls) == 2
