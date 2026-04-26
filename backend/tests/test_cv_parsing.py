"""Tests for backend/app/cv_parsing/{schemas, runner}.

Stub Anthropic client; never hits the real API.
"""

from __future__ import annotations

import json
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


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubResponse:
    text: str

    @property
    def content(self):
        return [_StubBlock(text=self.text)]


@dataclass
class _StubMessages:
    body: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(text=self.body)


@dataclass
class _StubClient:
    messages: _StubMessages


def _stub(body: str) -> _StubClient:
    return _StubClient(messages=_StubMessages(body=body))


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
    client = _stub(json.dumps(VALID_PARSE_PAYLOAD))
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
    assert client.messages.calls[0]["model"] == MODEL_VERSION
    assert client.messages.calls[0]["temperature"] == 0.0


def test_parser_returns_failed_on_empty_cv_text():
    out = parse_cv("", skip_cache=True)
    assert out.parse_failed is True
    assert out.error_reason == "empty_cv_text"


def test_parser_retries_on_invalid_json_then_succeeds():
    bad = "not json at all"
    good = json.dumps(VALID_PARSE_PAYLOAD)
    client = _StubClient(messages=_StubMessages(body=good))
    # Replace .create to return bad first, good second
    response_iter = iter([bad, good])

    def _alternating_create(**kwargs):
        client.messages.calls.append(kwargs)
        return _StubResponse(text=next(response_iter))

    client.messages.create = _alternating_create  # type: ignore[assignment]

    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is False
    assert len(client.messages.calls) == 2


def test_parser_returns_failed_after_two_invalid_responses():
    client = _stub("not json")
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is True
    assert "validation_failed_after_retry" in out.error_reason


def test_parser_returns_failed_when_schema_mismatch_persists():
    # Valid JSON but missing required structure (extra field that's forbidden)
    bad_payload = json.dumps({"headline": "X", "extra": "should_fail"})
    client = _stub(bad_payload)
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is True


def test_parser_returns_failed_on_claude_exception():
    class _ExplodingMessages:
        def create(self, **kwargs):
            raise RuntimeError("rate limit")

    client = _StubClient(messages=_ExplodingMessages())
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is True
    assert "claude_call_failed" in out.error_reason


def test_parser_strips_markdown_fences():
    fenced = "```json\n" + json.dumps(VALID_PARSE_PAYLOAD) + "\n```"
    client = _stub(fenced)
    out = parse_cv(SAMPLE_CV, client=client, skip_cache=True)
    assert out.parse_failed is False
    assert out.headline == "Senior Data Engineer"
