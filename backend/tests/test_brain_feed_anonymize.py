"""Privacy invariant for the outbound mainspring brain feed.

The single most important property of this feed: a payload must never carry
PII, free text, raw row ids, or role titles. These tests build source-row-shaped
objects stuffed with sentinel secrets and assert none of them survive the
transform, while the aggregable learning shape (disposition, attribution, cost)
does. Pure functions, no DB.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.brain_feed import anonymize


_SECRETS = [
    "Jane Candidate",
    "jane@example.com",
    "SECRET REASONING TEXT",
    "Senior Staff Engineer, Platform",  # role title
    "please reconsider, strong portfolio",  # correction free text
]


def _fake_decision():
    return SimpleNamespace(
        id=987654321,
        organization_id=44444,
        role_id=55555,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        confidence=0.8421,
        model_version="claude-haiku-4-5",
        prompt_version="v7",
        status="overridden",
        human_disposition="overridden",
        override_action="reject",
        active_capabilities={"fitted_policy": True},
        token_spend={
            "input_tokens": 1200,
            "output_tokens": 300,
            "cache_read_tokens": 50,
            "cache_creation_tokens": 10,
            "total_micro_usd": 4200,
            "by_agent": {"cv_scoring": 3000},
        },
        # PII / free-text the transform must drop:
        reasoning="SECRET REASONING TEXT",
        evidence={"candidate_name": "Jane Candidate", "email": "jane@example.com"},
        created_at=datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )


def _fake_feedback():
    return SimpleNamespace(
        id=321,
        decision_id=987654321,
        failure_mode="over_confident",
        scope="role",
        attributed_to="cv_scoring",
        direction="over",
        applied_at=datetime(2026, 5, 28, 13, 0, tzinfo=timezone.utc),
        reverted_at=None,
        created_at=datetime(2026, 5, 28, 12, 30, tzinfo=timezone.utc),
        # PII / free-text the transform must drop:
        correction_text="please reconsider, strong portfolio",
        reviewer_id=7,
        graph_write_hints=[{"note": "Jane Candidate is strong"}],
    )


def _assert_no_secrets(payload: dict):
    blob = json.dumps(payload)
    for secret in _SECRETS:
        assert secret not in blob, f"PII leaked into brain-feed payload: {secret!r}"


def test_decision_payload_carries_learning_shape_not_pii():
    d = _fake_decision()
    payload = anonymize.decision_payload(d)

    # Learning shape is present.
    assert payload["vertical"] == "hiring"
    assert payload["decision_type"] == "advance_to_interview"
    assert payload["human_disposition"] == "overridden"
    assert payload["override_action"] == "reject"
    assert payload["agreed"] is False
    assert payload["confidence"] == 0.842  # rounded to 3dp
    assert payload["token_shape"]["total_micro_usd"] == 4200
    # by_agent decomposition is dropped — only numeric totals survive.
    assert "by_agent" not in payload["token_shape"]

    # No PII / free text / raw ids / role titles.
    _assert_no_secrets(payload)
    assert "reasoning" not in payload
    assert "evidence" not in payload
    blob = json.dumps(payload)
    assert "987654321" not in blob  # raw decision id
    assert "55555" not in blob  # raw role id
    assert "44444" not in blob  # raw org id


def test_outcome_payload_carries_attribution_not_pii():
    f = _fake_feedback()
    payload = anonymize.outcome_payload(f)

    assert payload["vertical"] == "hiring"
    assert payload["failure_mode"] == "over_confident"
    assert payload["attributed_to"] == "cv_scoring"
    assert payload["direction"] == "over"
    assert payload["applied"] is True
    assert payload["reverted"] is False

    _assert_no_secrets(payload)
    assert "correction_text" not in payload
    assert "graph_write_hints" not in payload
    blob = json.dumps(payload)
    assert "321" not in blob  # raw feedback id
    assert "987654321" not in blob  # raw decision id


def test_decision_and_outcome_share_a_correlatable_ref():
    """The outcome's ``decision_ref`` must match the decision payload's ``ref``
    so the brain can correlate them — without either carrying the raw id."""
    d = _fake_decision()
    f = _fake_feedback()  # decision_id == d.id
    assert anonymize.outcome_payload(f)["decision_ref"] == anonymize.decision_payload(d)["ref"]


def test_refs_are_stable_and_opaque():
    d = _fake_decision()
    p1 = anonymize.decision_payload(d)
    p2 = anonymize.decision_payload(d)
    assert p1["ref"] == p2["ref"]  # deterministic
    assert str(d.id) not in p1["ref"]  # opaque
    assert len(p1["ref"]) == 16


def test_usage_payload_is_a_pure_cost_rollup():
    payload = anonymize.usage_payload(
        day="2026-05-28",
        feature="score",
        model="claude-haiku-4-5",
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=10,
        cache_creation_tokens=5,
        cost_usd_micro=1500,
        event_count=12,
    )
    assert payload["vertical"] == "hiring"
    assert payload["day"] == "2026-05-28"
    assert payload["feature"] == "score"
    assert payload["event_count"] == 12
    # No identity columns at all.
    for forbidden in ("organization_id", "user_id", "role_id", "entity_id"):
        assert forbidden not in payload


def test_event_ids_are_stable():
    assert anonymize.decision_event_id(5) == "decision-5"
    assert anonymize.outcome_event_id(9) == "outcome-9"
    assert (
        anonymize.usage_event_id("2026-05-28", "score", "claude-haiku-4-5")
        == "usage-2026-05-28-score-claude-haiku-4-5"
    )
