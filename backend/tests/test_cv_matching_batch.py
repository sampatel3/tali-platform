"""Tests for ``app.cv_matching.batch``.

Stubs the Anthropic ``messages.batches`` surface so the SDK upgrade is
not required to exercise the prompt-building / result-parsing logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import (
    PROMPT_VERSION,
    PROMPT_VERSION_V4,
    CVMatchOutput,
    CVMatchOutputV4,
    Priority,
    RequirementInput,
    ScoringStatus,
)
from app.cv_matching.batch import (
    BatchMatchInput,
    poll_batch,
    run_batch,
    submit_batch,
)


# --------------------------------------------------------------------------- #
# Stub batches surface                                                         #
# --------------------------------------------------------------------------- #


@dataclass
class _StubBatchStatus:
    id: str
    processing_status: str = "ended"


@dataclass
class _StubResultBlock:
    type: str
    message: dict


@dataclass
class _StubResultRow:
    custom_id: str
    result: _StubResultBlock


@dataclass
class _StubBatches:
    """Mimics ``client.messages.batches``."""

    next_id: str = "batch_test_001"
    submitted: list[dict] = field(default_factory=list)
    canned_results: list[_StubResultRow] = field(default_factory=list)

    def create(self, *, requests):
        self.submitted.append(requests)
        return _StubBatchStatus(id=self.next_id)

    def retrieve(self, batch_id):
        return _StubBatchStatus(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        return iter(self.canned_results)


@dataclass
class _StubMessages:
    batches: _StubBatches = field(default_factory=_StubBatches)


@dataclass
class _StubClient:
    messages: _StubMessages = field(default_factory=_StubMessages)


def _v4_response_payload(cv_text: str = "Python developer for 6 years") -> dict:
    return {
        "prompt_version": "cv_match_v4.1",
        "skills_match_score": 80,
        "experience_relevance_score": 75,
        "requirements_assessment": [
            {
                "requirement_id": "jd_req_1",
                "requirement": "5+ years Python",
                "priority": "must_have",
                "evidence_quotes": [cv_text],
                "evidence_start_char": 0,
                "evidence_end_char": len(cv_text),
                "reasoning": "Candidate names Python explicitly.",
                "status": "met",
                "match_tier": "exact",
                "impact": "Core language requirement met.",
                "confidence": "high",
            }
        ],
        "matching_skills": ["Python"],
        "missing_skills": [],
        "experience_highlights": [],
        "concerns": [],
        "summary": "Strong Python signal.",
    }


def test_submit_batch_builds_one_request_per_item():
    client = _StubClient()
    items = [
        BatchMatchInput(
            custom_id="app-1",
            cv_text="Python developer for 6 years",
            jd_text="Senior Python role",
            requirements=[],
            version="v4.1",
        ),
        BatchMatchInput(
            custom_id="app-2",
            cv_text="Frontend dev",
            jd_text="Senior Python role",
            requirements=[],
            version="v3",
        ),
    ]
    batch_id = submit_batch(items, client=client)
    assert batch_id == "batch_test_001"

    submitted = client.messages.batches.submitted[0]
    assert len(submitted) == 2
    # First (v4.1) carries the v4 wrapper + 2000 max_tokens.
    assert "<UNTRUSTED_CV id=" in submitted[0]["params"]["messages"][0]["content"]
    assert submitted[0]["params"]["max_tokens"] == 2000
    # Second (v3) carries the v3 prompt + 8192 max_tokens.
    assert "<CANDIDATE_CV>" in submitted[1]["params"]["messages"][0]["content"]
    assert submitted[1]["params"]["max_tokens"] == 8192
    assert submitted[0]["custom_id"] == "app-1"
    assert submitted[1]["custom_id"] == "app-2"


def test_poll_batch_parses_succeeded_results_into_v4_output():
    client = _StubClient()
    cv = "Python developer for 6 years"
    payload = _v4_response_payload(cv)
    client.messages.batches.canned_results = [
        _StubResultRow(
            custom_id="app-1",
            result=_StubResultBlock(
                type="succeeded",
                message={"content": [{"type": "text", "text": json.dumps(payload)}]},
            ),
        )
    ]

    items = [
        BatchMatchInput(
            custom_id="app-1",
            cv_text=cv,
            jd_text="Senior Python role",
            requirements=[
                RequirementInput(
                    id="jd_req_1",
                    requirement="5+ years Python",
                    priority=Priority.MUST_HAVE,
                )
            ],
            version="v4.1",
        )
    ]
    out = poll_batch("batch_test_001", items, client=client, poll_interval_s=0.0)
    assert "app-1" in out
    result = out["app-1"]
    assert isinstance(result, CVMatchOutputV4)
    assert result.scoring_status == ScoringStatus.OK
    assert result.prompt_version == PROMPT_VERSION_V4
    assert result.requirements_assessment[0].match_tier == "exact"


def test_poll_batch_marks_errored_rows_as_failed():
    client = _StubClient()
    client.messages.batches.canned_results = [
        _StubResultRow(
            custom_id="app-1",
            result=_StubResultBlock(type="errored", message={}),
        )
    ]
    items = [
        BatchMatchInput(
            custom_id="app-1",
            cv_text="x",
            jd_text="y",
            version="v4.1",
        )
    ]
    out = poll_batch("batch", items, client=client, poll_interval_s=0.0)
    assert out["app-1"].scoring_status == ScoringStatus.FAILED
    assert "errored" in out["app-1"].error_reason


def test_run_batch_round_trip():
    client = _StubClient()
    cv = "Python developer for 6 years"
    payload = _v4_response_payload(cv)
    client.messages.batches.canned_results = [
        _StubResultRow(
            custom_id="app-1",
            result=_StubResultBlock(
                type="succeeded",
                message={"content": [{"type": "text", "text": json.dumps(payload)}]},
            ),
        )
    ]
    items = [
        BatchMatchInput(
            custom_id="app-1",
            cv_text=cv,
            jd_text="JD",
            requirements=[
                RequirementInput(
                    id="jd_req_1",
                    requirement="5+ years Python",
                    priority=Priority.MUST_HAVE,
                )
            ],
            version="v4.1",
        )
    ]
    out = run_batch(items, client=client, poll_interval_s=0.0)
    assert isinstance(out["app-1"], CVMatchOutputV4)
    assert out["app-1"].role_fit_score > 0


def test_submit_batch_raises_on_old_sdk_without_batches():
    """When the Anthropic SDK predates messages.batches (current 0.34
    pinned version), submit_batch must fail loudly so production can't
    silently fall back to a worse path."""

    @dataclass
    class _OldMessages:
        # No `batches` attribute.
        pass

    @dataclass
    class _OldClient:
        messages: _OldMessages = field(default_factory=_OldMessages)

    items = [
        BatchMatchInput(
            custom_id="x", cv_text="x", jd_text="y", version="v4.1"
        )
    ]
    try:
        submit_batch(items, client=_OldClient())
    except RuntimeError as exc:
        assert "messages.batches" in str(exc)
        return
    raise AssertionError("expected RuntimeError on missing batches surface")
