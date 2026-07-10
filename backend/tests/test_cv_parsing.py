"""Tests for backend/app/cv_parsing/{schemas, runner}.

Stub Anthropic client; never hits the real API. ``parse_cv`` runs in
forced tool-use mode (Phase 2), so the stubs return ``tool_use`` content
blocks instead of text. The ``_text()`` helper is kept for the negative
tests that simulate a model that emitted prose instead of using the tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.cv_parsing import (
    MODEL_VERSION,
    PROMPT_VERSION,
    ParsedCV,
    parse_cv,
)
from app.cv_parsing.schemas import EducationEntry, ExperienceEntry, ParsedCVSections


# ---------- Stub Anthropic client ----------

# Tool name derived by the gateway from the Pydantic class
# ``ParsedCVSections``. Stable so cached tool definitions stay warm.
TOOL_NAME = "emit_parsed_cv_sections"


@dataclass
class _StubBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class _StubResponse:
    """Anthropic-shaped response carrying arbitrary content blocks."""

    blocks: list[Any]

    @property
    def content(self):
        return self.blocks


def _text(text: str) -> _StubResponse:
    """Response with a single text block — simulates a model that emitted
    prose instead of using the tool (the gateway treats this as a
    ``ValidationFailure`` and retries)."""
    return _StubResponse(blocks=[_StubBlock(text=text)])


def _tu(input_dict: dict, name: str = TOOL_NAME) -> _StubResponse:
    """Response with a single ``tool_use`` block carrying the structured
    output as the tool's ``.input`` dict (the happy path)."""
    return _StubResponse(blocks=[_ToolUseBlock(name=name, input=input_dict)])


@dataclass
class _StubMessages:
    responses: list[_StubResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        return self.responses[min(idx, len(self.responses) - 1)]


@dataclass
class _StubClient:
    messages: _StubMessages


def _stub(*responses: _StubResponse) -> _StubClient:
    return _StubClient(messages=_StubMessages(responses=list(responses)))


# ---------- Sample CV ----------


SAMPLE_CV = """\
Jane Doe
Senior Data Engineer

Summary
7 years of experience building production data pipelines on AWS.

Experience
Senior Data Engineer, Regional Bank (Dubai, UAE) — Jan 2022 to Present
  - Operated 30+ data pipelines on AWS Glue, Step Functions, Athena
  - Migrated 12 legacy Hadoop jobs to Spark on EMR

Data Engineer, Careem — 2018 to 2022
  - Built streaming pipelines on Kinesis + Spark Structured Streaming

Education
B.Eng., Computer Science, IIT Delhi — 2014 to 2018

Skills
Python, SQL, AWS Glue, Spark, Airflow, dbt

Languages
English (native), Hindi
"""


VALID_PARSE_PAYLOAD = {
    "headline": "Senior Data Engineer",
    "summary": "7 years of experience building production data pipelines on AWS.",
    "experience": [
        {
            "company": "Regional Bank",
            "title": "Senior Data Engineer",
            "location": "Dubai, UAE",
            "start": "Jan 2022",
            "end": "Present",
            "bullets": [
                "Operated 30+ data pipelines on AWS Glue, Step Functions, Athena",
                "Migrated 12 legacy Hadoop jobs to Spark on EMR",
            ],
        },
        {
            "company": "Careem",
            "title": "Data Engineer",
            "location": "",
            "start": "2018",
            "end": "2022",
            "bullets": [
                "Built streaming pipelines on Kinesis + Spark Structured Streaming",
            ],
        },
    ],
    "education": [
        {
            "institution": "IIT Delhi",
            "degree": "B.Eng.",
            "field": "Computer Science",
            "start": "2014",
            "end": "2018",
            "notes": "",
        }
    ],
    "projects": [
        {
            "name": "Realtime fraud-detection pipeline",
            "bullets": ["Cut false positives by 30% with a streaming feature store"],
        }
    ],
    "skills": ["Python", "SQL", "AWS Glue", "Spark", "Airflow", "dbt"],
    "certifications": [],
    "languages": ["English (native)", "Hindi"],
    "links": [],
}


# ---------- Schemas ----------


def test_parsed_cv_round_trip():
    cv = ParsedCV.from_sections(
        ParsedCVSections.model_validate(VALID_PARSE_PAYLOAD),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    blob = cv.model_dump(mode="json")
    cv2 = ParsedCV.model_validate(blob)
    assert cv2.headline == "Senior Data Engineer"
    assert len(cv2.experience) == 2
    assert cv2.skills == ["Python", "SQL", "AWS Glue", "Spark", "Airflow", "dbt"]
    assert len(cv2.projects) == 1
    assert cv2.projects[0].name == "Realtime fraud-detection pipeline"
    assert cv2.projects[0].bullets == ["Cut false positives by 30% with a streaming feature store"]
    assert cv2.parse_failed is False


def test_parsed_cv_failed_factory():
    cv = ParsedCV.failed(
        reason="bad json", prompt_version="v", model_version="m"
    )
    assert cv.parse_failed is True
    assert cv.error_reason == "bad json"
    assert cv.experience == []


def test_experience_entry_extra_forbid():
    with pytest.raises(Exception):
        ExperienceEntry.model_validate({"company": "X", "ulta_field": True})


# ---------- Runner ----------


def test_parser_happy_path():
    client = _stub(_tu(VALID_PARSE_PAYLOAD))
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)

    assert isinstance(out, ParsedCV)
    assert out.parse_failed is False
    assert out.headline == "Senior Data Engineer"
    assert len(out.experience) == 2
    assert out.experience[0].company == "Regional Bank"
    assert out.skills == ["Python", "SQL", "AWS Glue", "Spark", "Airflow", "dbt"]
    assert out.prompt_version == PROMPT_VERSION
    assert out.model_version == MODEL_VERSION

    assert len(client.messages.calls) == 1
    sent = client.messages.calls[0]
    assert sent["model"] == MODEL_VERSION
    assert sent["temperature"] == 0.0
    # Forced tool-use: gateway sends a single synthetic tool whose
    # input_schema is ParsedCVSections.model_json_schema().
    assert sent["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert sent["tools"][0]["name"] == TOOL_NAME
    assert sent["tools"][0]["input_schema"]["type"] == "object"


def test_parser_returns_failed_on_empty_cv_text():
    out = parse_cv("", skip_cache=True)
    assert out.parse_failed is True
    assert out.error_reason == "empty_cv_text"


def test_parser_retries_when_first_response_is_text_then_succeeds():
    """Model emits prose first (no tool_use) → gateway treats as
    ValidationFailure → retries → second attempt is a proper tool_use."""
    client = _stub(
        _text("Sure, here's the CV — but no tool call."),
        _tu(VALID_PARSE_PAYLOAD),
    )
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is False
    assert out.headline == "Senior Data Engineer"
    assert len(client.messages.calls) == 2


def test_parser_returns_failed_when_model_never_uses_the_tool():
    """Both attempts emit prose; runner fails with validation_failed_after_retry."""
    client = _stub(_text("no tool here"), _text("still no tool"))
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is True
    assert "validation_failed_after_retry" in out.error_reason


def test_parser_returns_failed_when_tool_input_schema_mismatch_persists():
    """tool_use input fails Pydantic schema both times (ParsedCVSections
    forbids unknown fields)."""
    bad_input = {"headline": "X", "extra": "should_fail"}
    client = _stub(_tu(bad_input), _tu(bad_input))
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is True
    assert "validation_failed_after_retry" in out.error_reason


def test_parser_returns_failed_on_claude_exception():
    class _ExplodingMessages:
        def create(self, **kwargs):
            raise RuntimeError("rate limit")

    client = _StubClient(messages=_ExplodingMessages())
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is True
    assert "claude_call_failed" in out.error_reason


# ---------- apply (ORM bridge) ----------


from types import SimpleNamespace  # noqa: E402

from app.cv_parsing.apply import parse_and_store_cv_sections  # noqa: E402


def _ok_parsed() -> ParsedCV:
    return ParsedCV.from_sections(
        ParsedCVSections.model_validate(VALID_PARSE_PAYLOAD),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )


def _app(**kw) -> SimpleNamespace:
    base = dict(
        id=1, organization_id=2, role_id=3,
        cv_text="raw scrambled cv text", cv_sections=None, candidate=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_apply_stores_sections_on_app_and_candidate(monkeypatch):
    candidate = SimpleNamespace(cv_text=None, cv_sections=None)
    app = _app(candidate=candidate)
    monkeypatch.setattr("app.cv_parsing.runner.parse_cv", lambda *a, **k: _ok_parsed())

    wrote = parse_and_store_cv_sections(app, db=None)

    assert wrote is True
    assert app.cv_sections["headline"] == "Senior Data Engineer"
    # Mirrored onto the candidate so candidate-level reads see it too.
    assert candidate.cv_sections == app.cv_sections


def test_apply_noop_when_already_parsed(monkeypatch):
    app = _app(cv_sections={"existing": True})
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        return _ok_parsed()

    monkeypatch.setattr("app.cv_parsing.runner.parse_cv", _spy)

    wrote = parse_and_store_cv_sections(app)

    assert wrote is False
    assert calls["n"] == 0  # short-circuits before any Claude call
    assert app.cv_sections == {"existing": True}


def test_apply_force_reparses_even_when_present(monkeypatch):
    app = _app(cv_sections={"existing": True})
    monkeypatch.setattr("app.cv_parsing.runner.parse_cv", lambda *a, **k: _ok_parsed())

    wrote = parse_and_store_cv_sections(app, force=True)

    assert wrote is True
    assert app.cv_sections["headline"] == "Senior Data Engineer"


def test_apply_noop_when_no_text(monkeypatch):
    app = _app(cv_text="   ", candidate=None)

    def _boom(*a, **k):
        raise AssertionError("parse_cv should not be called when there's no text")

    monkeypatch.setattr("app.cv_parsing.runner.parse_cv", _boom)

    wrote = parse_and_store_cv_sections(app)

    assert wrote is False
    assert app.cv_sections is None


def test_apply_leaves_null_on_parse_failure(monkeypatch):
    app = _app()
    monkeypatch.setattr(
        "app.cv_parsing.runner.parse_cv",
        lambda *a, **k: ParsedCV.failed(
            reason="boom", prompt_version=PROMPT_VERSION, model_version=MODEL_VERSION
        ),
    )

    wrote = parse_and_store_cv_sections(app)

    assert wrote is False
    assert app.cv_sections is None  # retryable, not pinned to the fallback


def test_apply_falls_back_to_candidate_text(monkeypatch):
    candidate = SimpleNamespace(cv_text="candidate-level raw text", cv_sections=None)
    app = _app(cv_text=None, candidate=candidate)
    captured = {}

    def _cap(text, **k):
        captured["text"] = text
        return _ok_parsed()

    monkeypatch.setattr("app.cv_parsing.runner.parse_cv", _cap)

    wrote = parse_and_store_cv_sections(app)

    assert wrote is True
    assert captured["text"] == "candidate-level raw text"


# ---------- failure caching (deterministic failures stop re-billing) ----------
#
# 2026-07 cost audit: a CV whose parse deterministically failed schema
# validation was re-billed on every sync trigger (86 parses of one
# application over 18 days) because only successes were cached. These pin
# the fix: deterministic failures land in the cache and short-circuit the
# next attempt; transient failures stay uncached so they genuinely retry.

from app.cv_parsing import cache as cache_module  # noqa: E402
from app.cv_parsing.schemas import ParsedCV  # noqa: E402


@pytest.fixture()
def _cache_db(monkeypatch):
    from tests.conftest import TestingSessionLocal, engine

    import app.platform.database as pdb
    from app.models.cv_parse_cache import CvParseCache

    CvParseCache.__table__.create(bind=engine, checkfirst=True)
    monkeypatch.setattr(pdb, "SessionLocal", TestingSessionLocal)
    yield
    with engine.connect() as conn:
        conn.execute(CvParseCache.__table__.delete())
        conn.commit()


def test_deterministic_failure_is_cached_and_stops_rebilling(_cache_db):
    cv = SAMPLE_CV + "\nunique-marker: failure-cache-1"
    out = parse_cv(cv, client=_stub(_text("no tool"), _text("still no tool")))
    assert out.parse_failed is True

    class _MustNotCall:
        def create(self, **kwargs):
            raise AssertionError("cached failure must prevent a billed API call")

    out2 = parse_cv(cv, client=_StubClient(messages=_MustNotCall()))
    assert out2.parse_failed is True
    assert out2.cache_hit is True
    assert "validation_failed_after_retry" in out2.error_reason


def test_transient_failure_is_not_cached(_cache_db):
    cv = SAMPLE_CV + "\nunique-marker: failure-cache-2"

    class _Exploding:
        def create(self, **kwargs):
            raise RuntimeError("overloaded")

    out = parse_cv(cv, client=_StubClient(messages=_Exploding()))
    assert out.parse_failed is True
    assert "claude_call_failed" in out.error_reason

    # The next trigger retries for real — and can succeed.
    out2 = parse_cv(cv, client=_stub(_tu(VALID_PARSE_PAYLOAD)))
    assert out2.parse_failed is False
    assert out2.cache_hit is False


def test_cache_set_success_overwrites_cached_failure(_cache_db):
    key = cache_module.compute_cache_key(
        cv_text="overwrite-case", prompt_version="p1", model_version="m1"
    )
    failed = ParsedCV.failed(
        reason="validation_failed_after_retry: nope",
        prompt_version="p1",
        model_version="m1",
    )
    cache_module.set(key, failed)
    cached = cache_module.get(key)
    assert cached is not None and cached.parse_failed is True

    ok = ParsedCV.from_sections(
        ParsedCVSections.model_validate(VALID_PARSE_PAYLOAD),
        prompt_version="p1",
        model_version="m1",
    )
    cache_module.set(key, ok)
    cached = cache_module.get(key)
    assert cached is not None and cached.parse_failed is False


def test_cache_set_failure_never_overwrites_cached_success(_cache_db):
    key = cache_module.compute_cache_key(
        cv_text="no-downgrade-case", prompt_version="p1", model_version="m1"
    )
    ok = ParsedCV.from_sections(
        ParsedCVSections.model_validate(VALID_PARSE_PAYLOAD),
        prompt_version="p1",
        model_version="m1",
    )
    cache_module.set(key, ok)
    failed = ParsedCV.failed(
        reason="validation_failed_after_retry: nope",
        prompt_version="p1",
        model_version="m1",
    )
    cache_module.set(key, failed)
    cached = cache_module.get(key)
    assert cached is not None and cached.parse_failed is False
