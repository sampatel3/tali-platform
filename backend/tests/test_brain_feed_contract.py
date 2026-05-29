"""Anti-drift contract for the outbound mainspring brain feed.

This is the *wire contract* between the live Taali platform and the
cross-vertical mainspring brain. Mainspring's ``/api/v1/ingest/{decisions,
outcomes,usage}`` endpoints parse exactly these payload shapes; if Taali's
``anonymize`` transform silently grows, drops, or renames a field, the two
ends drift and ingest breaks (or — worse — a new field leaks PII before
mainspring is ready to receive it).

So these assertions are deliberately **exact** (``==`` on the full key set,
not ``issubset``). A failure here is not a bug to paper over: it means the
contract moved, and the change must be made on *both* ends together —
update mainspring's ingest schema, then update the frozen sets below. The
privacy invariant (no PII / free text / raw ids) is proven structurally in
``test_brain_feed_anonymize.py``; this file pins the *shape* that invariant
is allowed to take.

Pure functions only — no DB, no clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.brain_feed import anonymize, outbox
from app.models.brain_feed_outbox import BRAIN_FEED_KINDS


# ---------------------------------------------------------------------------
# Frozen wire shapes. Changing any of these is a cross-repo contract change.
# ---------------------------------------------------------------------------

_DECISION_KEYS = {
    "vertical",
    "ref",
    "cohort",
    "decision_type",
    "recommendation",
    "confidence",
    "model_version",
    "prompt_version",
    "status",
    "human_disposition",
    "override_action",
    "agreed",
    "active_capabilities",
    "token_shape",
    "created_at",
    "resolved_at",
}

_OUTCOME_KEYS = {
    "vertical",
    "ref",
    "decision_ref",
    "failure_mode",
    "scope",
    "attributed_to",
    "direction",
    "applied",
    "reverted",
    "created_at",
}

_USAGE_KEYS = {
    "vertical",
    "day",
    "feature",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cost_usd_micro",
    "event_count",
}

_TOKEN_SHAPE_KEYS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_micro_usd",
}

# record_kind (singular) -> mainspring ingest path segment (plural).
_INGEST_PATHS = {
    "decision": "decisions",
    "outcome": "outcomes",
    "usage": "usage",
}

_DRIFT_HINT = (
    "Brain-feed wire contract drifted. This payload shape is parsed by "
    "mainspring's /api/v1/ingest endpoints — update BOTH ends together "
    "(mainspring ingest schema + the frozen set in this test), never just one."
)


def _fake_decision() -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        organization_id=7,
        role_id=9,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        confidence=0.87,
        model_version="claude-haiku-4-5",
        prompt_version="v3",
        status="approved",
        human_disposition="approved",
        override_action=None,
        active_capabilities={"fitted_policy": True},
        token_spend={
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 5,
            "cache_creation_tokens": 2,
            "total_micro_usd": 300,
            "by_agent": {"cv_scoring": 200},
        },
        reasoning="free text that must never appear",
        evidence={"candidate_name": "Jane"},
        created_at=datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
    )


def _fake_feedback() -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        decision_id=42,
        failure_mode="over_confident",
        scope="role",
        attributed_to="cv_scoring",
        direction="over",
        applied_at=datetime(2026, 5, 28, 13, 0, tzinfo=timezone.utc),
        reverted_at=None,
        created_at=datetime(2026, 5, 28, 12, 30, tzinfo=timezone.utc),
        correction_text="free text that must never appear",
        reviewer_id=3,
        graph_write_hints=[{"note": "x"}],
    )


# ---------------------------------------------------------------------------
# Payload key sets are frozen exactly.
# ---------------------------------------------------------------------------


def test_decision_payload_keys_are_frozen():
    payload = anonymize.decision_payload(_fake_decision())
    assert set(payload.keys()) == _DECISION_KEYS, _DRIFT_HINT


def test_outcome_payload_keys_are_frozen():
    payload = anonymize.outcome_payload(_fake_feedback())
    assert set(payload.keys()) == _OUTCOME_KEYS, _DRIFT_HINT


def test_usage_payload_keys_are_frozen():
    payload = anonymize.usage_payload(
        day="2026-05-28",
        feature="score",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd_micro=1,
        event_count=1,
    )
    assert set(payload.keys()) == _USAGE_KEYS, _DRIFT_HINT


def test_token_shape_keys_are_frozen():
    payload = anonymize.decision_payload(_fake_decision())
    assert set(payload["token_shape"].keys()) == _TOKEN_SHAPE_KEYS, _DRIFT_HINT


def test_every_payload_declares_its_vertical():
    # The brain segregates signal by vertical; the field must always be present
    # and constant for this platform.
    assert anonymize.VERTICAL == "hiring"
    assert anonymize.decision_payload(_fake_decision())["vertical"] == "hiring"
    assert anonymize.outcome_payload(_fake_feedback())["vertical"] == "hiring"


# ---------------------------------------------------------------------------
# event_id formats are frozen (mainspring is idempotent on event_id).
# ---------------------------------------------------------------------------


def test_event_id_formats_are_frozen():
    assert anonymize.decision_event_id(42) == "decision-42"
    assert anonymize.outcome_event_id(11) == "outcome-11"
    assert (
        anonymize.usage_event_id("2026-05-28", "score", "claude-haiku-4-5")
        == "usage-2026-05-28-score-claude-haiku-4-5"
    )


# ---------------------------------------------------------------------------
# Ingest routing: every kind maps to exactly one path, and the set of kinds
# the model accepts is exactly the set the drain knows how to ship.
# ---------------------------------------------------------------------------


def test_ingest_path_map_is_frozen():
    assert outbox._INGEST_PATH == _INGEST_PATHS, _DRIFT_HINT


def test_every_record_kind_has_an_ingest_path():
    # A kind the model accepts but the drain can't route would KeyError at
    # send time — silent enqueue, hard failure on drain.
    assert set(BRAIN_FEED_KINDS) == set(outbox._INGEST_PATH.keys()), (
        "Every BRAIN_FEED_KIND must have an ingest path in outbox._INGEST_PATH. "
        + _DRIFT_HINT
    )


# ---------------------------------------------------------------------------
# The POST envelope is {event_id, payload} — pin it without a network call.
# ---------------------------------------------------------------------------


def test_post_envelope_shape_is_frozen(monkeypatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class _Resp:
            def raise_for_status(self):
                return None

        return _Resp()

    monkeypatch.setattr(outbox.httpx, "post", fake_post)

    row = SimpleNamespace(
        record_kind="decision",
        event_id="decision-42",
        payload={"vertical": "hiring", "ref": "abc"},
    )
    outbox._post(row, "https://ms.test", "tok-1")

    assert captured["url"] == "https://ms.test/api/v1/ingest/decisions"
    assert set(captured["json"].keys()) == {"event_id", "payload"}, _DRIFT_HINT
    assert captured["json"]["event_id"] == "decision-42"
    assert captured["json"]["payload"] == {"vertical": "hiring", "ref": "abc"}
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    assert captured["headers"]["Content-Type"] == "application/json"
