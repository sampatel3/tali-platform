"""Celery tasks for the decision-policy retune loop.

One nightly task fans out across orgs that have agent activity in the
last 7 days. Auto-apply opt-in is per-org (workspace_settings) — the
default is to write the new policy as inactive and notify admins to
review in the Hub (Phase 6).
"""

from __future__ import annotations

import logging

from .celery_app import celery_app


logger = logging.getLogger("taali.tasks.decision_policy")


@celery_app.task(name="app.tasks.decision_policy_tasks.nightly_retune_sweep")
def nightly_retune_sweep() -> dict:
    """Beat-scheduled fan-out across all orgs."""
    from ..decision_policy.nightly_retune import run_for_all_orgs
    from ..platform.database import SessionLocal

    db = SessionLocal()
    summary: dict = {
        "orgs_seen": 0,
        "orgs_proposed": 0,
        "orgs_activated": 0,
        "orgs_skipped": 0,
        "details": [],
    }
    try:
        results = run_for_all_orgs(db)
        for r in results:
            summary["orgs_seen"] += 1
            if r.skipped_reason:
                summary["orgs_skipped"] += 1
                summary["details"].append(
                    {"organization_id": r.organization_id, "skipped": r.skipped_reason}
                )
                continue
            summary["orgs_proposed"] += 1
            if r.activated:
                summary["orgs_activated"] += 1
            summary["details"].append(
                {
                    "organization_id": r.organization_id,
                    "revision_id": r.revision_id,
                    "policy_id": r.policy_id,
                    "activated": r.activated,
                    "shifts": [
                        {
                            "field_path": s.field_path,
                            "old": s.old_value,
                            "new": s.new_value,
                            "cause": s.cause_summary,
                        }
                        for s in (r.proposal.shifts if r.proposal else [])
                    ],
                }
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("nightly_retune_sweep failed")
        raise
    finally:
        db.close()
    logger.info(
        "nightly_retune_sweep: orgs_seen=%d proposed=%d activated=%d skipped=%d",
        summary["orgs_seen"],
        summary["orgs_proposed"],
        summary["orgs_activated"],
        summary["orgs_skipped"],
    )
    return summary
