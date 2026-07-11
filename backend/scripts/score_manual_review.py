"""One-shot: full-score pre-screen "Manual review recommended" candidates that
never got a full CV score.

These candidates PASSED pre-screen (promising) but were never full-scored — on
agent-off roles nothing auto-scores them, and on budget-paused agent-on roles
the scoring gate skipped them. They sit in Applied with no score and no verdict.
This enqueues a real CV score for each so they get a deterministic verdict.

This SPENDS (a paid Anthropic call per candidate, ~$0.015 avg). Dry-run by
default; ``--execute`` enqueues the scoring jobs (dispatched to the prod
scoring workers via Celery — run this ON the box so the broker is reachable).

Budget-capped roles: ``enqueue_score`` refuses to spend past a role's monthly
cap, so candidates on a paused capped role are reported as ``skipped_budget``
and NOT scored — raising that cap is a deliberate recruiter/budget decision,
not something this script does silently.

Run (dry-run prints counts; ``--execute`` spends):

    railway ssh --service resourceful-adaptation
    python scripts/score_manual_review.py --org 2            # dry-run
    python scripts/score_manual_review.py --org 2 --execute  # spend
"""
from __future__ import annotations

import argparse

from app.domains.assessments_runtime.pipeline_service import _not_post_handover_sql
from app.models.agent_decision import AgentDecision
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.platform.database import SessionLocal
from app.services.cv_score_orchestrator import enqueue_score
from app.services.role_budget_gate import can_spend_on_role


def _manual_review_unscored(db, organization_id):
    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.cv_match_score.is_(None),
            CandidateApplication.pre_screen_recommendation == "Manual review recommended",
            _not_post_handover_sql(),
            ~(
                db.query(AgentDecision.id)
                .filter(
                    AgentDecision.application_id == CandidateApplication.id,
                    AgentDecision.status.in_(
                        ("pending", "processing", "approved", "overridden")
                    ),
                )
                .exists()
            ),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    return q.all()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", type=int, default=None, help="limit to one org (default: all)")
    ap.add_argument("--execute", action="store_true", help="spend (default: dry-run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = _manual_review_unscored(db, args.org)
        capped = [(a, r) for a, r in rows if not can_spend_on_role(db, role=r)]
        scorable = [(a, r) for a, r in rows if can_spend_on_role(db, role=r)]
        print(f"manual-review unscored: {len(rows)}")
        print(f"  scorable now (no cap block): {len(scorable)}")
        print(f"  skipped_budget (capped paused role — needs a cap bump): {len(capped)}")
        for a, r in capped:
            print(f"    · app={a.id} role={r.name!r}")

        if not args.execute:
            print("\nDRY-RUN — no spend. Re-run with --execute to enqueue scores.")
            return

        enqueued = 0
        skipped = 0
        for app, role in scorable:
            job = enqueue_score(db, app)
            if job is not None:
                enqueued += 1
            else:
                skipped += 1
        db.commit()
        print(f"enqueued {enqueued} score job(s); {skipped} not enqueued (guard/other)")
        if capped:
            print(
                f"{len(capped)} on capped paused roles left unscored — bump the cap to score them."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
