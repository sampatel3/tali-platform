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
