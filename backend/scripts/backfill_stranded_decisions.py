"""One-shot: re-emit deterministic verdicts stranded by the 14-day SLA expiry.

Two cohorts of OPEN candidates lost their pending decision when the SLA sweep
aged it out to ``expired`` (or a system reconcile ``discarded`` it) and nothing
re-emitted it — leaving them "not yet decided" even though the score never
changed and the verdict still holds:

  * REAL-SCORE (``cv_match_score`` set, agent-on role, not advanced in Workable):
    re-run the deterministic score-time decision
    (``ensure_deterministic_decision``) — recomputes the CURRENT verdict from
    the existing score and queues it as a pending HITL card.
  * PRE-SCREEN below-threshold (no full score): re-run the pre-screen reject
    emitter (``backfill_existing_below_threshold`` → ``queue_pre_screen_reject``,
    which now revives an ``expired`` card as well as a system-``discarded`` one).

Both paths respect recruiter resolutions: a card a human discarded/overrode
(``resolved_by_user_id`` set) is never revived. Deterministic and FREE — no LLM
calls, no paid scoring.

The companion fix in ``agent_expire_stale_decisions`` stops OPEN candidates'
verdicts from expiring in the first place, so this backfill only ever needs to
run once to clear the existing backlog.

Run (dry-run prints counts; ``--execute`` writes):

    railway run --service resourceful-adaptation \
        python scripts/backfill_stranded_decisions.py --org 2            # dry-run
    railway run --service resourceful-adaptation \
        python scripts/backfill_stranded_decisions.py --org 2 --execute  # apply
"""
from __future__ import annotations

import argparse

from app.domains.assessments_runtime.pipeline_service import _not_post_handover_sql
from app.models.agent_decision import AgentDecision
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.platform.database import SessionLocal
from app.services.bulk_decision_service import ensure_deterministic_decision
from app.services.pre_screen_decision_emitter import backfill_existing_below_threshold


def _stranded_real_score(db, organization_id):
    """Open, scored, agent-on candidates with NO active agent decision — the
    exact population the funnel counts as 'not yet decided'."""
    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.cv_match_score.isnot(None),
            Role.agentic_mode_enabled.is_(True),
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
    ap.add_argument("--execute", action="store_true", help="write (default: dry-run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = _stranded_real_score(db, args.org)
        print(f"real-score stranded (open, scored, agent-on, no active decision): {len(rows)}")

        if not args.execute:
            print("\nDRY-RUN — no writes. Re-run with --execute to apply.")
            print(
                "  real-score → ensure_deterministic_decision() per candidate\n"
                "  pre-screen → backfill_existing_below_threshold() "
                "(revives expired/system-discarded below-threshold cards)"
            )
            return

        emitted = 0
        by_type: dict[str, int] = {}
        for app, role in rows:
            dt = ensure_deterministic_decision(db, app=app, role=role)
            if dt:
                emitted += 1
                by_type[dt] = by_type.get(dt, 0) + 1
        db.commit()
        print(f"real-score: emitted {emitted} verdict(s)  by_type={by_type}")

        ps = backfill_existing_below_threshold(db, organization_id=args.org)
        db.commit()
        print(f"pre-screen: {ps}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
