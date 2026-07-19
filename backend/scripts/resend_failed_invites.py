"""One-off remediation for the 2026-06-25 bulk-invite incident.

Bulk "send assessment" approvals fanned out faster than Resend's ~2 req/s API
limit, so several invites were rate-limited (429) and dropped silently, and
some that *were* delivered lost their ``invite_email_id`` to a racing
writeback. Symptom: Assessment rows whose ``invite_sent_at`` falls inside an
incident window but whose ``invite_email_id IS NULL`` (never tracked).

This script LISTS those suspect invites (default, dry-run) so you can
cross-reference the Resend dashboard, and — with ``--execute`` — re-sends them
through the audited ``resend_assessment_invite`` action. With the accompanying
code fix deployed, the re-send now persists ``invite_email_id`` / status
correctly, and any genuine failure surfaces as ``invite_email_status='failed'``.

SAFETY
------
- Dry-run by default: nothing is sent without ``--execute``.
- Use ``--only-emails`` to restrict the re-send to the recruiter/Resend-confirmed
  failures, so a candidate who DID receive the invite (but lost tracking) isn't
  double-sent.
- Re-sending creates no new Assessment row; it re-emails the same link and
  re-stamps ``invite_sent_at`` (which also moves the row out of the window, so
  a second dry-run won't re-list an already-remediated invite).

Run INSIDE the container (the Railway public proxy drops long sessions)::

  railway ssh --service taali-worker \\
    "cd /app && PYTHONPATH=/app /opt/venv/bin/python scripts/resend_failed_invites.py \\
       --window-start 2026-06-25T10:25:00Z --window-end 2026-06-25T10:35:00Z --dry-run"

  # confirm the list against the Resend dashboard, then:
  railway ssh --service taali-worker \\
    "cd /app && PYTHONPATH=/app /opt/venv/bin/python scripts/resend_failed_invites.py \\
       --window-start 2026-06-25T10:25:00Z --window-end 2026-06-25T10:35:00Z \\
       --execute --only-emails a@x.com,b@y.com"

The two known incident windows were ~10:29 and ~14:24 UTC on 2026-06-25; run
once per window.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from app.actions import resend_assessment_invite
from app.actions.types import Actor
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.platform.database import SessionLocal


logger = logging.getLogger("taali.scripts.resend_failed_invites")


def _parse_ts(raw: str) -> datetime:
    """Parse a UTC ISO8601 timestamp (``...Z`` or ``+00:00``) to aware UTC."""
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Re-send assessment invites that failed to track in an incident window."
    )
    ap.add_argument("--window-start", required=True, help="UTC ISO8601, e.g. 2026-06-25T10:25:00Z")
    ap.add_argument("--window-end", required=True, help="UTC ISO8601, e.g. 2026-06-25T10:35:00Z")
    ap.add_argument("--org-id", type=int, default=None, help="Restrict to one organization.")
    ap.add_argument(
        "--only-emails",
        default=None,
        help="CSV of candidate emails to restrict the re-send to (Resend-confirmed failures).",
    )
    ap.add_argument("--execute", action="store_true", help="Actually re-send (default lists only).")
    args = ap.parse_args()

    from app.platform.logging import setup_logging

    setup_logging()

    start = _parse_ts(args.window_start)
    end = _parse_ts(args.window_end)
    only = {e.strip().lower() for e in (args.only_emails or "").split(",") if e.strip()} or None

    db = SessionLocal()
    try:
        q = db.query(Assessment).filter(
            Assessment.invite_sent_at >= start,
            Assessment.invite_sent_at < end,
            Assessment.invite_email_id.is_(None),
        )
        if args.org_id is not None:
            q = q.filter(Assessment.organization_id == args.org_id)
        if getattr(Assessment, "is_voided", None) is not None:
            q = q.filter(Assessment.is_voided.is_(False))
        suspects = q.order_by(Assessment.invite_sent_at.asc()).all()

        logger.info(
            "Invite remediation scan start=%s end=%s suspects=%s execute=%s restricted_email_count=%s",
            start.isoformat(),
            end.isoformat(),
            len(suspects),
            bool(args.execute),
            len(only or ()),
        )

        resent = skipped = failed = 0
        for a in suspects:
            cand = db.query(Candidate).filter(Candidate.id == a.candidate_id).first()
            email = (getattr(cand, "email", "") or "").strip()
            tag = (
                f"assessment_id={a.id} organization_id={a.organization_id} "
                f"candidate_id={a.candidate_id} sent_at={a.invite_sent_at}"
            )

            if only is not None and email.lower() not in only:
                skipped += 1
                continue

            if not args.execute:
                logger.info("Invite remediation dry_run %s", tag)
                continue

            try:
                result = resend_assessment_invite.run(
                    db,
                    Actor.system(),
                    organization_id=int(a.organization_id),
                    assessment_id=int(a.id),
                )
                db.commit()
                logger.info("Invite remediation result status=%s %s", result.status, tag)
                resent += 1 if result.status == "resent" else 0
                skipped += 0 if result.status == "resent" else 1
            except Exception as exc:  # keep going on the rest of the batch
                db.rollback()
                failed += 1
                logger.error(
                    "Invite remediation failed assessment_id=%s candidate_id=%s error_type=%s",
                    a.id,
                    a.candidate_id,
                    type(exc).__name__,
                )

        logger.info(
            "Invite remediation complete resent=%s skipped=%s failed=%s suspects=%s",
            resent,
            skipped,
            failed,
            len(suspects),
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
