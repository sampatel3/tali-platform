"""One-time backfill: revert candidates that sync wrongly pushed to ``advanced``.

Background
----------
Tali's ``advanced`` pipeline stage means "handed out of Tali into the
recruiter's Workable flow", and it triggers the A6 freeze (the agent never
touches the candidate again — see ``role_support.is_resolved``). Historically
the Workable sync *also* moved candidates to ``advanced`` purely because their
Workable stage was past the handover point (e.g. "Technical Interview"), even
though no Tali hand-back decision was ever made. Those candidates were frozen
out of Tali's funnel by accident.

The sync no longer does this (``advanced`` now only comes from the recruiter
hand-back action). This script repairs the rows that were already mis-advanced
so Tali resumes ownership of them.

Scope (rows reverted)
---------------------
* ``pipeline_stage == 'advanced'``
* ``pipeline_stage_source == 'sync'``    (excludes legitimate recruiter handoffs)
* ``application_outcome == 'open'``       (a real terminal outcome — hired /
                                          rejected / withdrawn / disqualified —
                                          means the advance is genuine; only the
                                          spurious stage-derived advances are
                                          still ``open``)
* not soft-deleted

Each row is reverted to the stage it held *before* the sync auto-advance,
recovered from the pipeline event log; rows that sync created directly at
``advanced`` (no prior stage) fall back to ``applied``. A correction event is
written for audit.

Safety
------
Reverting un-freezes candidates, so the agent may re-evaluate them on its next
run (which can incur Anthropic spend on agent-enabled roles). For that reason
this script is **dry-run by default** — it prints the plan and changes nothing.
Pass ``--apply`` to commit. Consider pausing agent-enabled roles, or running it
during a low-spend window, before applying.

Usage::

    python -m app.scripts.revert_sync_advanced_applications            # dry run
    python -m app.scripts.revert_sync_advanced_applications --apply    # commit
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..domains.assessments_runtime.pipeline_service import (
    PIPELINE_STAGES,
    is_terminal_workable_stage,
    normalize_pipeline_key,
    status_from_pipeline,
)

logger = logging.getLogger(__name__)

_FALLBACK_STAGE = "applied"


def _revert_target_stage(db: Session, app: CandidateApplication) -> str:
    """Recover the stage the candidate held before sync auto-advanced them.

    Reads the most recent ``pipeline_stage_changed`` event that moved the
    candidate *to* ``advanced`` via sync and returns its ``from_stage``. Falls
    back to ``applied`` when no such event exists (e.g. sync created the row
    directly at ``advanced``).
    """
    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "pipeline_stage_changed",
            CandidateApplicationEvent.to_stage == "advanced",
        )
        .order_by(CandidateApplicationEvent.created_at.desc(), CandidateApplicationEvent.id.desc())
        .all()
    )
    for event in events:
        metadata = event.event_metadata or {}
        if metadata.get("source") == "sync":
            from_stage = normalize_pipeline_key(event.from_stage)
            if from_stage in PIPELINE_STAGES and from_stage != "advanced":
                return from_stage
    return _FALLBACK_STAGE


def revert_sync_advanced_applications(
    db: Session,
    *,
    apply: bool = False,
    scored_only: bool = False,
    skip_terminal: bool = True,
) -> dict:
    """Revert sync-mis-advanced applications. Returns a summary dict.

    Idempotent: once reverted, a row's ``pipeline_stage_source`` is set to
    ``system`` and its stage is no longer ``advanced``, so re-running matches
    zero rows.

    ``skip_terminal`` (default True): leave rows whose ``workable_stage`` is a
    TERMINAL hand-off (offer / hired) alone — post the freeze-only-on-terminal
    change these are *legitimately* ``advanced``; only mid-interview rows were
    mis-frozen. ``scored_only`` (default False): only revert rows that already
    have a ``cv_match_score`` — un-freezing an unscored row lets the agent
    re-score it (a cv_match LLM call); restricting to scored rows keeps the
    backfill zero-re-score-cost (cached score is reused, the sync re-evaluation
    is the deterministic policy).
    """
    rows = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.pipeline_stage == "advanced",
            CandidateApplication.pipeline_stage_source == "sync",
            CandidateApplication.application_outcome == "open",
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )

    skipped_terminal = 0
    skipped_unscored = 0
    selected = []
    for app in rows:
        if skip_terminal and is_terminal_workable_stage(app.workable_stage):
            skipped_terminal += 1
            continue
        if scored_only and app.cv_match_score is None:
            skipped_unscored += 1
            continue
        selected.append(app)
    rows = selected

    now = datetime.now(timezone.utc)
    summary = {
        "matched": len(rows),
        "reverted": 0,
        "by_target_stage": {},
        "applied": apply,
        "skipped_terminal": skipped_terminal,
        "skipped_unscored": skipped_unscored,
    }

    for app in rows:
        target = _revert_target_stage(db, app)
        summary["by_target_stage"][target] = summary["by_target_stage"].get(target, 0) + 1
        print(
            f"  app_id={app.id} role_id={app.role_id} candidate_id={app.candidate_id} "
            f"advanced -> {target} (workable_stage={app.workable_stage!r})",
            flush=True,
        )
        if not apply:
            continue

        from_stage = app.pipeline_stage
        previous_status = app.status
        app.pipeline_stage = target
        app.pipeline_stage_updated_at = now
        app.pipeline_stage_source = "system"
        app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
        app.version = int(app.version or 1) + 1

        db.add(
            CandidateApplicationEvent(
                application_id=app.id,
                organization_id=app.organization_id,
                role_id=int(app.role_id),
                event_type="pipeline_stage_changed",
                from_stage=from_stage,
                to_stage=target,
                from_outcome=app.application_outcome,
                to_outcome=app.application_outcome,
                actor_type="system",
                actor_id=None,
                reason="Backfill: reverted accidental sync auto-advance; Tali resumes ownership",
                event_metadata={
                    "source": "system",
                    "backfill": "revert_sync_advanced_applications",
                    "legacy_status_before": previous_status,
                },
                # Run-scoped key: a candidate can be mis-advanced again (e.g.
                # a later sync regression) and need re-reverting, so the event
                # key must differ from a prior backfill's — else the unique
                # (application_id, idempotency_key) constraint rolls back the
                # whole batch. Date-stamped: same-day re-runs are idempotent
                # because reverted rows stop matching the advanced+sync filter.
                idempotency_key=f"backfill_revert_advanced:{app.id}:{now:%Y%m%d}",
            )
        )
        summary["reverted"] += 1

    if apply:
        db.commit()
    return summary


def main() -> int:
    from ..platform.database import SessionLocal

    args = sys.argv[1:]
    apply = "--apply" in args
    scored_only = "--scored-only" in args
    db = SessionLocal()
    try:
        print(
            f"[revert_sync_advanced_applications] mode={'APPLY' if apply else 'DRY-RUN'} "
            f"scored_only={scored_only}",
            flush=True,
        )
        summary = revert_sync_advanced_applications(db, apply=apply, scored_only=scored_only)
        print(
            f"[revert_sync_advanced_applications] matched={summary['matched']} "
            f"reverted={summary['reverted']} by_target_stage={summary['by_target_stage']} "
            f"skipped_terminal={summary['skipped_terminal']} "
            f"skipped_unscored={summary['skipped_unscored']}",
            flush=True,
        )
        if not apply and summary["matched"]:
            print("  (dry run — re-run with --apply to commit)", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
