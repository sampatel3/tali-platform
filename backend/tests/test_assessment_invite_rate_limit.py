"""Regression tests for the 2026-06-25 bulk-invite incident.

A bulk "send assessment" approval in the Home review queue fans out 10-12
invite sends near-simultaneously, and several trip Resend's ~2 req/s 429 rate
limit. Before the fix those sends were dropped silently (``logger.error`` only)
with no retry and no recruiter-visible signal — and even successfully-sent
invites lost their ``invite_email_id`` to a racing writeback. These tests pin
the new behavior:

- the in-process Resend send retries on 429 with backoff (email_client)
- a persistent rate-limit is reported up as retryable/rate_limited, not swallowed
- a permanent 4xx (auth/validation) is NOT retried
- an exhausted transient send enters durable ``retry_wait`` recovery
- only an explicit permanent provider 4xx surfaces ``failed`` / HITL
- a transient failure reschedules (retry) rather than dropping the invite
- the email_id / status writeback is robust to the producer-commit race
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from resend.exceptions import ResendError

from app.components.notifications import email_client as ec
from app.components.notifications import tasks as email_tasks
from app.components.notifications.email_client import EmailService
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task


def _rate_limit_error(message: str = "Too many requests") -> ResendError:
    """A faithful Resend rate-limit error (base ResendError, code 429)."""
    return ResendError(
        code=429, error_type="rate_limit_exceeded", message=message, suggested_action=""
    )


def _invite_kwargs(**overrides) -> dict:
    base = dict(
        candidate_email="cand@x.test",
        candidate_name="Cand",
        token="tok-1",
        assessment_id=1,
        org_name="Acme",
        position="Backend",
        frontend_url="https://app.taali.test",
    )
    base.update(overrides)
    return base


# ===========================================================================
# email_client: in-process retry/backoff on 429 (the core fix)
# ===========================================================================


def test_send_assessment_invite_retries_on_429_then_succeeds():
    svc = EmailService(api_key="rk_test", from_email="TAALI <noreply@taali.ai>")
    calls = {"n": 0}

    def _send(payload, options=None):
        calls["n"] += 1
        assert options == {"idempotency_key": "assessment-invite/1"}
        if calls["n"] == 1:
            raise _rate_limit_error()
        return {"id": "em_ok"}

    with patch(
        "app.components.notifications.email_client.resend.Emails.send", side_effect=_send
    ) as mock_send, patch(
        "app.components.notifications.email_client.time.sleep"
    ) as mock_sleep:
        result = svc.send_assessment_invite(**_invite_kwargs())

    assert result["success"] is True
    assert result["email_id"] == "em_ok"
    assert mock_send.call_count == 2  # retried the 429 rather than dropping it
    assert mock_sleep.call_count == 1  # backed off once before the retry


def test_send_assessment_invite_persistent_429_is_reported_not_swallowed():
    svc = EmailService(api_key="rk_test", from_email="TAALI <noreply@taali.ai>")
    with patch(
        "app.components.notifications.email_client.resend.Emails.send",
        side_effect=_rate_limit_error(),
    ) as mock_send, patch("app.components.notifications.email_client.time.sleep"):
        result = svc.send_assessment_invite(**_invite_kwargs())

    assert result["success"] is False
    assert result["rate_limited"] is True
    assert result["retryable"] is True
    # Exhausted the in-process attempt budget — not a single unguarded call.
    assert mock_send.call_count == ec._MAX_SEND_ATTEMPTS


def test_send_assessment_invite_permanent_4xx_is_not_retried():
    """An auth/validation error won't self-heal — fail fast, don't burn retries."""
    svc = EmailService(api_key="rk_test", from_email="TAALI <noreply@taali.ai>")
    bad = ResendError(
        code=401, error_type="missing_api_key", message="nope", suggested_action=""
    )
    with patch(
        "app.components.notifications.email_client.resend.Emails.send", side_effect=bad
    ) as mock_send, patch(
        "app.components.notifications.email_client.time.sleep"
    ) as mock_sleep:
        result = svc.send_assessment_invite(**_invite_kwargs())

    assert result["success"] is False
    assert result["rate_limited"] is False
    assert result["retryable"] is False
    assert mock_send.call_count == 1
    assert mock_sleep.call_count == 0


def test_bulk_burst_all_delivered_despite_rate_limiting():
    """11 invites fired in a burst: Resend 429s the first attempt of each, but
    the in-process retry clears them so every invite is delivered (none dropped
    — the symptom the incident reported)."""
    svc = EmailService(api_key="rk_test", from_email="TAALI <noreply@taali.ai>")
    seen: dict[str, int] = {}

    def _send(payload, options=None):
        to = payload["to"][0]
        assessment_id = int(to.removeprefix("c").split("@", 1)[0])
        assert options == {
            "idempotency_key": f"assessment-invite/{assessment_id}"
        }
        seen[to] = seen.get(to, 0) + 1
        if seen[to] == 1:  # first attempt for each recipient trips the rate limit
            raise _rate_limit_error()
        return {"id": f"em-{to}"}

    with patch(
        "app.components.notifications.email_client.resend.Emails.send", side_effect=_send
    ), patch("app.components.notifications.email_client.time.sleep"):
        results = [
            svc.send_assessment_invite(
                **_invite_kwargs(candidate_email=f"c{i}@x.test", assessment_id=i)
            )
            for i in range(11)
        ]

    assert all(r["success"] for r in results)  # every invite went out
    assert all(r["email_id"] == f"em-c{i}@x.test" for i, r in enumerate(results))
    assert all(count == 2 for count in seen.values())  # each retried exactly once


# ===========================================================================
# Task: bulk burst → failure surfaced / retried (not silently dropped)
# ===========================================================================


def _seed_assessment(db) -> Assessment:
    org = Organization(name="Acme", slug=f"o-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    task = Task(name="T", task_key=f"t-{id(db)}", organization_id=org.id, is_active=True)
    db.add(task)
    db.flush()
    cand = Candidate(organization_id=org.id, email="cand@x.test", full_name="Cand")
    db.add(cand)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="review",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    a = Assessment(
        organization_id=org.id,
        candidate_id=cand.id,
        task_id=task.id,
        role_id=role.id,
        application_id=application.id,
        token="tok-1",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
    )
    db.add(a)
    db.flush()
    # Commit so the task's own SessionLocal (separate connection) sees the row.
    db.commit()
    return a


def _task_kwargs(assessment_id: int) -> dict:
    return dict(
        candidate_email="cand@x.test",
        candidate_name="Cand",
        token="tok-1",
        org_name="Acme",
        position="Backend",
        assessment_id=assessment_id,
    )


@pytest.fixture
def _resend_key(monkeypatch):
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "RESEND_API_KEY", "rk_test")
    monkeypatch.setattr(cfg, "EMAIL_FROM", "TAALI <noreply@taali.ai>")
    monkeypatch.setattr(email_tasks, "_invalidate_resend_probe", lambda error: None)


_RATE_LIMITED_FAILURE = {
    "success": False,
    "email_id": "",
    "error": "429 Too Many Requests",
    "rate_limited": True,
    "retryable": True,
    "retry_after": None,
}


def test_bulk_send_enters_durable_recovery_when_retries_exhausted(db, _resend_key):
    """A persistent 429 exhausts the short chain but remains autonomous."""
    a = _seed_assessment(db)
    with patch.object(
        EmailService, "send_assessment_invite", return_value=_RATE_LIMITED_FAILURE
    ):
        result = email_tasks.send_assessment_email.apply(
            kwargs=_task_kwargs(int(a.id)),
            retries=email_tasks.send_assessment_email.max_retries,  # final attempt
        )

    out = result.get()
    assert out["success"] is False
    assert out["retry_wait"] is True

    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).first()
    assert refreshed.invite_email_status == "retry_wait"
    assert refreshed.invite_email_next_attempt_at is not None
    assert refreshed.invite_email_claimed_at is None
    assert refreshed.invite_email_last_error == "429 Too Many Requests"


def test_bulk_send_permanent_provider_4xx_requires_hitl(db, _resend_key):
    a = _seed_assessment(db)
    failure = {
        "success": False,
        "email_id": "",
        "error": "401 API key invalid",
        "error_code": "401",
        "rate_limited": False,
        "retryable": False,
    }
    with patch.object(EmailService, "send_assessment_invite", return_value=failure):
        out = email_tasks.send_assessment_email.apply(
            kwargs=_task_kwargs(int(a.id)), retries=0
        ).get()

    assert out["failed"] is True
    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).one()
    assert refreshed.invite_email_status == "failed"
    assert refreshed.invite_email_next_attempt_at is None
    assert refreshed.invite_email_last_error == "401 API key invalid"
    assert refreshed.invite_sent_at is None
    assert refreshed.application.pipeline_stage == "review"
    assert refreshed.invite_workable_handoff_status is None
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == refreshed.application_id,
            CandidateApplicationEvent.event_type.in_(
                ("assessment_invite_sent", "pipeline_stage_changed")
            ),
        )
        .count()
        == 0
    )


def test_bulk_send_reschedules_retry_on_transient_failure(db, _resend_key):
    """A failure with retries remaining reschedules (raises Celery ``Retry``)
    rather than returning a dropped result — and must NOT prematurely mark the
    invite failed while it's still in flight."""
    a = _seed_assessment(db)
    with patch.object(
        EmailService, "send_assessment_invite", return_value=_RATE_LIMITED_FAILURE
    ):
        with pytest.raises(Retry):
            email_tasks.send_assessment_email.apply(
                kwargs=_task_kwargs(int(a.id)), retries=0
            )

    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).first()
    assert refreshed.invite_email_status == "retrying"
    assert refreshed.invite_email_retry_count == 1
    assert refreshed.invite_email_next_attempt_at is not None
    assert refreshed.invite_email_claimed_at is not None


def test_bulk_send_success_persists_email_id_and_sent_status(db, _resend_key):
    """The writeback fix: a successful send records both the Resend message id
    and a 'sent' status so the invite tracker / delivery webhook work."""
    a = _seed_assessment(db)
    with patch.object(
        EmailService,
        "send_assessment_invite",
        return_value={"success": True, "email_id": "em_live"},
    ) as send:
        out = email_tasks.send_assessment_email.apply(
            kwargs=_task_kwargs(int(a.id)), retries=0
        ).get()

    assert out["success"] is True
    assert send.call_args.kwargs["idempotency_key"] == (
        f"assessment-invite/{int(a.id)}"
    )
    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).first()
    assert refreshed.invite_email_id == "em_live"
    assert refreshed.invite_email_status == "sent"
    assert refreshed.invite_sent_at is not None
    assert refreshed.invite_email_confirmed_generation == 0
    assert refreshed.application.pipeline_stage == "invited"
    assert refreshed.invite_email_retry_count == 0
    assert refreshed.invite_email_next_attempt_at is None
    assert refreshed.invite_email_claimed_at is None


def test_provider_success_without_message_id_stays_recoverable(db, _resend_key):
    a = _seed_assessment(db)
    with patch.object(
        EmailService,
        "send_assessment_invite",
        return_value={"success": True, "email_id": ""},
    ):
        out = email_tasks.send_assessment_email.apply(
            kwargs=_task_kwargs(int(a.id)),
            retries=email_tasks.send_assessment_email.max_retries,
        ).get()

    assert out["retry_wait"] is True
    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).one()
    assert refreshed.invite_email_id is None
    assert refreshed.invite_email_status == "retry_wait"


def test_stale_send_generation_cannot_clobber_explicit_resend(db, _resend_key):
    a = _seed_assessment(db)
    a.invite_email_send_generation = 1
    a.invite_email_status = "pending_dispatch"
    db.commit()

    with patch.object(EmailService, "send_assessment_invite") as send:
        out = email_tasks.send_assessment_email.apply(
            kwargs={
                **_task_kwargs(int(a.id)),
                "idempotency_key": f"assessment-invite/{int(a.id)}",
            },
            retries=0,
        ).get()

    assert out["deduplicated"] is True
    assert out["reason"] == "superseded_generation"
    send.assert_not_called()
    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).one()
    assert refreshed.invite_email_status == "pending_dispatch"


def test_old_generation_writeback_does_not_overwrite_new_outbox_intent(db):
    a = _seed_assessment(db)
    a.invite_email_send_generation = 1
    a.invite_email_status = "pending_dispatch"
    db.commit()

    ok = email_tasks._persist_invite_email_state(
        int(a.id),
        email_id="em_old",
        status="sent",
        expected_generation=0,
    )

    assert ok is False
    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).one()
    assert refreshed.invite_email_id is None
    assert refreshed.invite_email_status == "pending_dispatch"


# ===========================================================================
# Writeback robustness (the COMPLETED-but-NULL-email_id race)
# ===========================================================================


def test_persist_invite_email_state_writes_when_row_present(db):
    a = _seed_assessment(db)
    ok = email_tasks._persist_invite_email_state(
        int(a.id), email_id="em_x", status="sent"
    )
    assert ok is True
    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).first()
    assert refreshed.invite_email_id == "em_x"
    assert refreshed.invite_email_status == "sent"


def test_persist_invite_email_state_retries_missing_row_then_gives_up(db):
    """Producer transaction never becomes visible → retry the full budget (not
    a single attempt) then return False without raising. This is the resilience
    that the original bare-except lacked."""
    with patch.object(email_tasks.time, "sleep") as mock_sleep:
        ok = email_tasks._persist_invite_email_state(10_000_000, status="sent")
    assert ok is False
    assert mock_sleep.call_count == email_tasks._WRITEBACK_MAX_ATTEMPTS - 1


def test_persist_invite_status_does_not_downgrade_delivered(db):
    """A late 'failed' (or 'sent') writeback must not clobber a real delivery
    confirmation that the Resend webhook already recorded."""
    a = _seed_assessment(db)
    a.invite_email_status = "delivered"
    db.commit()

    email_tasks._persist_invite_email_state(int(a.id), status="failed")

    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == a.id).first()
    assert refreshed.invite_email_status == "delivered"


# ===========================================================================
# Retry backoff helper
# ===========================================================================


def test_email_retry_countdown_honors_retry_after():
    out = email_tasks._email_retry_countdown(0, rate_limited=True, retry_after=30)
    assert 30 <= out <= int(30 * 1.25) + 1


def test_email_retry_countdown_backs_off_and_caps():
    c0 = email_tasks._email_retry_countdown(0, rate_limited=True)
    c3 = email_tasks._email_retry_countdown(3, rate_limited=True)
    assert c3 > c0
    capped = email_tasks._email_retry_countdown(20, rate_limited=True)
    assert capped <= email_tasks._EMAIL_RETRY_MAX_SECONDS
