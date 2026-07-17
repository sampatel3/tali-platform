"""Outbound brain-feed: enqueue idempotency, the flag gate, and drain modes.

Covers the durable-queue contract end to end without network:
- the feature is OFF by default → enqueue is a no-op (live platform untouched).
- with the flag on, the sweep enqueues anonymized resolved decisions, teach
  outcomes, and whole-day usage rollups, and is idempotent on ``event_id``.
- drain is disabled / shadow / live depending on config; a failing POST leaves
  the row pending until a retry cap, then ``failed`` (signal never silently lost).

httpx is mocked — these run offline and with no API key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.brain_feed import outbox, sweep
from app.models.agent_decision import AgentDecision
from app.models.brain_feed_outbox import (
    BRAIN_FEED_STATUS_FAILED,
    BRAIN_FEED_STATUS_PENDING,
    BRAIN_FEED_STATUS_SENT,
    BrainFeedOutbox,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import DecisionFeedback
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.platform.config import settings
from app.tasks.brain_feed_tasks import flush_brain_feed


@pytest.fixture
def feed_on(monkeypatch):
    """Enable the feed; default ingest URL/token empty (shadow)."""
    monkeypatch.setattr(settings, "MAINSPRING_BRAIN_FEED_ENABLED", True)
    monkeypatch.setattr(settings, "MAINSPRING_INGEST_URL", "")
    monkeypatch.setattr(settings, "MAINSPRING_BRAND_TOKEN", "")
    return settings


def _seed_resolved_decision(db, *, resolved_at=None, disposition="approved"):
    org = Organization(name="Feed Org", slug=f"feed-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(
        organization_id=org.id, email=f"c-{id(db)}@x.test", full_name="Feed Cand"
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="approved",
        human_disposition=disposition,
        reasoning="strong CV — should not leak",
        confidence=0.9,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key=f"feed:{app.id}:advance",
        resolved_at=resolved_at or datetime.now(timezone.utc),
    )
    db.add(decision)
    db.flush()
    db.commit()
    return org, role, app, decision


# ---------------------------------------------------------------------------
# The flag gate
# ---------------------------------------------------------------------------


def test_enqueue_is_noop_when_disabled(db):
    # Flag defaults off — enqueue must write nothing.
    assert settings.MAINSPRING_BRAIN_FEED_ENABLED is False
    row = outbox.enqueue(db, record_kind="decision", event_id="decision-1", payload={})
    assert row is None
    assert db.query(BrainFeedOutbox).count() == 0


def test_sweep_is_noop_when_disabled(db):
    _seed_resolved_decision(db)
    summary = sweep.sweep_and_enqueue(db)
    assert summary["status"] == "disabled"
    assert db.query(BrainFeedOutbox).count() == 0


# ---------------------------------------------------------------------------
# Enqueue idempotency
# ---------------------------------------------------------------------------


def test_enqueue_idempotent_on_event_id(db, feed_on):
    r1 = outbox.enqueue(db, record_kind="decision", event_id="decision-7", payload={"a": 1})
    r2 = outbox.enqueue(db, record_kind="decision", event_id="decision-7", payload={"a": 2})
    db.commit()
    assert r1 is not None  # first call creates the row
    assert r2 is None  # duplicate event_id → no new row
    assert db.query(BrainFeedOutbox).count() == 1
    # First write wins; the re-enqueue does not overwrite.
    assert db.query(BrainFeedOutbox).one().payload == {"a": 1}


def test_enqueue_rejects_unknown_kind(db, feed_on):
    with pytest.raises(ValueError):
        outbox.enqueue(db, record_kind="bogus", event_id="x", payload={})


# ---------------------------------------------------------------------------
# Sweep enqueues each record kind, anonymized + idempotent
# ---------------------------------------------------------------------------


def test_sweep_enqueues_resolved_decision_and_outcome(db, feed_on):
    org, role, app, decision = _seed_resolved_decision(db)
    reviewer = User(email=f"r-{id(db)}@x.test", hashed_password="x", organization_id=org.id)
    db.add(reviewer)
    db.flush()
    fb = DecisionFeedback(
        decision_id=decision.id,
        reviewer_id=reviewer.id,
        organization_id=org.id,
        role_id=role.id,
        failure_mode="over_confident",
        correction_text="should not leak",
        scope="role",
        attributed_to="cv_scoring",
        direction="over",
    )
    db.add(fb)
    db.commit()

    summary = sweep.sweep_and_enqueue(db)
    assert summary["decisions"] == 1
    assert summary["outcomes"] == 1

    kinds = {r.record_kind for r in db.query(BrainFeedOutbox).all()}
    assert kinds == {"decision", "outcome"}

    dec_row = db.query(BrainFeedOutbox).filter_by(record_kind="decision").one()
    out_row = db.query(BrainFeedOutbox).filter_by(record_kind="outcome").one()
    # Correlatable refs, no raw ids / free text.
    assert out_row.payload["decision_ref"] == dec_row.payload["ref"]
    assert "should not leak" not in str(dec_row.payload)
    assert "should not leak" not in str(out_row.payload)

    # Idempotent: a second sweep enqueues nothing new.
    summary2 = sweep.sweep_and_enqueue(db)
    assert summary2["decisions"] == 0 and summary2["outcomes"] == 0
    assert db.query(BrainFeedOutbox).count() == 2


def test_sweep_usage_rollup_only_whole_past_days(db, feed_on):
    org = Organization(name="Usage Org", slug=f"usage-{id(db)}")
    db.add(org)
    db.flush()
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    # Two events yesterday (same feature+model → one bucket), one today (excluded).
    for toks in (100, 250):
        db.add(UsageEvent(
            organization_id=org.id, feature="score", model="claude-haiku-4-5",
            input_tokens=toks, output_tokens=10, cost_usd_micro=toks * 2,
            markup_multiplier=1.0, created_at=yesterday,
        ))
    db.add(UsageEvent(
        organization_id=org.id, feature="score", model="claude-haiku-4-5",
        input_tokens=999, output_tokens=10, cost_usd_micro=999,
        markup_multiplier=1.0, created_at=now,
    ))
    db.commit()

    summary = sweep.sweep_and_enqueue(db)
    assert summary["usage"] == 1  # only yesterday's bucket
    row = db.query(BrainFeedOutbox).filter_by(record_kind="usage").one()
    assert row.payload["day"] == yesterday.date().isoformat()
    assert row.payload["input_tokens"] == 350  # 100 + 250, today's 999 excluded
    assert row.payload["event_count"] == 2


# ---------------------------------------------------------------------------
# Drain: disabled / shadow / live
# ---------------------------------------------------------------------------


def test_drain_disabled_is_noop(db):
    assert outbox.drain(db)["status"] == "disabled"


def test_drain_shadow_leaves_rows_pending(db, feed_on):
    outbox.enqueue(db, record_kind="decision", event_id="decision-9", payload={"x": 1})
    db.commit()
    summary = outbox.drain(db)
    assert summary["status"] == "shadow"
    assert summary["scanned"] == 1
    assert db.query(BrainFeedOutbox).one().status == BRAIN_FEED_STATUS_PENDING


def test_drain_posts_and_is_idempotent(db, feed_on, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_INGEST_URL", "https://ms.test")
    monkeypatch.setattr(settings, "MAINSPRING_BRAND_TOKEN", "tok-123")
    outbox.enqueue(db, record_kind="decision", event_id="decision-9", payload={"x": 1})
    db.commit()

    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    with patch.object(outbox.httpx, "post", return_value=fake_resp) as post:
        summary = outbox.drain(db)

    assert summary == {"status": "ok", "scanned": 1, "sent": 1, "failed": 0, "pending": 0}
    args, kwargs = post.call_args
    assert args[0] == "https://ms.test/api/v1/ingest/decisions"
    assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
    assert kwargs["json"]["event_id"] == "decision-9"
    assert db.query(BrainFeedOutbox).one().status == BRAIN_FEED_STATUS_SENT

    # Idempotent: already-sent row not re-posted.
    with patch.object(outbox.httpx, "post", return_value=fake_resp) as post2:
        summary2 = outbox.drain(db)
    assert summary2["scanned"] == 0
    post2.assert_not_called()


def test_drain_posts_only_after_releasing_database_transaction(
    db, feed_on, monkeypatch
):
    monkeypatch.setattr(settings, "MAINSPRING_INGEST_URL", "https://ms.test")
    outbox.enqueue(
        db,
        record_kind="decision",
        event_id="decision-no-open-transaction",
        payload={"x": 1},
    )
    db.commit()

    response = MagicMock()
    response.raise_for_status.return_value = None

    def post_without_transaction(*_args, **_kwargs):
        assert db.in_transaction() is False
        return response

    monkeypatch.setattr(outbox.httpx, "post", post_without_transaction)
    assert outbox.drain(db)["sent"] == 1


def test_stale_delivery_claim_cannot_overwrite_a_newer_lease(db, feed_on):
    row = outbox.enqueue(
        db,
        record_kind="decision",
        event_id="decision-stale-claim",
        payload={"x": 1},
    )
    assert row is not None
    row_id = int(row.id)
    db.commit()
    claim = outbox._claim(db, batch_size=1)[0]

    current = db.query(BrainFeedOutbox).filter_by(id=row_id).one()
    current.attempts = int(current.attempts) + 1
    db.commit()

    outcome = outbox._finalize_claim(
        db,
        claim=claim,
        delivered=True,
        max_attempts=outbox._MAX_ATTEMPTS,
        now=datetime.now(timezone.utc),
    )
    assert outcome == "stale"
    current = db.query(BrainFeedOutbox).filter_by(id=row_id).one()
    assert current.status == "processing"
    assert int(current.attempts) == 2


def test_drain_failing_post_leaves_pending_then_fails_at_cap(db, feed_on, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_INGEST_URL", "https://ms.test")
    outbox.enqueue(db, record_kind="decision", event_id="decision-9", payload={"x": 1})
    db.commit()
    provider_secret = "Authorization: Bearer mainspring-secret"

    with patch.object(
        outbox.httpx, "post", side_effect=RuntimeError(provider_secret)
    ):
        summary = outbox.drain(db, max_attempts=2)
    assert summary["sent"] == 0 and summary["pending"] == 1
    row = db.query(BrainFeedOutbox).one()
    assert row.status == BRAIN_FEED_STATUS_PENDING
    assert row.attempts == 1
    assert row.last_error == "brain_feed_delivery_failed"
    assert provider_secret not in row.last_error
    assert row.next_attempt_at is not None

    # Second failing attempt hits the cap → failed.
    row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with patch.object(
        outbox.httpx, "post", side_effect=RuntimeError(provider_secret)
    ):
        summary2 = outbox.drain(db, max_attempts=2)
    assert summary2["failed"] == 1
    assert db.query(BrainFeedOutbox).one().status == BRAIN_FEED_STATUS_FAILED


def test_drain_recovers_expired_lease_and_acknowledges_rows_independently(
    db, feed_on, monkeypatch
):
    monkeypatch.setattr(settings, "MAINSPRING_INGEST_URL", "https://ms.test")
    first = outbox.enqueue(
        db, record_kind="decision", event_id="leased-1", payload={"n": 1}
    )
    second = outbox.enqueue(
        db, record_kind="decision", event_id="leased-2", payload={"n": 2}
    )
    db.commit()
    first.status = "processing"
    first.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    responses = [MagicMock(), RuntimeError("transient")]
    responses[0].raise_for_status.return_value = None
    with patch.object(outbox.httpx, "post", side_effect=responses):
        result = outbox.drain(db)

    assert result["sent"] == 1 and result["pending"] == 1
    db.refresh(first)
    db.refresh(second)
    assert first.status == BRAIN_FEED_STATUS_SENT
    assert second.status == BRAIN_FEED_STATUS_PENDING
    assert second.next_attempt_at is not None


# ---------------------------------------------------------------------------
# The Celery task wires sweep + drain together
# ---------------------------------------------------------------------------


def test_flush_task_noop_when_disabled(db):
    # Task opens its own SessionLocal; with the flag off it must do nothing.
    result = flush_brain_feed()
    assert result["swept"]["status"] == "disabled"
    assert result["drained"]["status"] == "disabled"
