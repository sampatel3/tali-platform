"""One-time backfill: give every scored, UNDECIDED candidate its deterministic
pending verdict, so the existing "not yet decided" backlog (candidates stranded
because their role's agent was paused when they were scored) gets a real HITL
decision — the same invariant ensure_deterministic_decision now enforces at
score-time going forward.

Idempotent + safe on PAUSED roles: it calls ensure_deterministic_decision, which
touches zero role/agent state, runs no LLM, emits no episode, and does NOT call
the volume-guard or decide_role_cohort. Re-running is a no-op (the one-pending
guard short-circuits anything already decided).

Run INSIDE the container (Railway public proxy drops after a few hundred rows):
  railway ssh --service taali-worker-scoring \\
    "cd /app && PYTHONPATH=/app /opt/venv/bin/python scripts/backfill_pending_decisions.py --org-id 2 --dry-run"
then drop --dry-run to apply.
"""
from __future__ import annotations

import argparse
from collections import Counter

from app.domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from app.models.agent_decision import AgentDecision
from app.models.candidate_application import CandidateApplication as CAm
from app.models.role import Role
from app.platform.database import SessionLocal
from app.services.bulk_decision_service import (
    ensure_deterministic_decision,
    recompute_persisted_verdict,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", type=int, default=None)
    ap.add_argument("--role-id", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    q = db.query(CAm).filter(
        CAm.application_outcome == "open",
        CAm.pipeline_stage.in_(["applied", "review"]),
        CAm.cv_match_score.isnot(None),
        CAm.workable_disqualified_at.is_(None),
        ~db.query(AgentDecision.id)
        .filter(
            AgentDecision.application_id == CAm.id,
            AgentDecision.status.in_(["pending", "processing"]),
        )
        .exists(),
    )
    if args.org_id is not None:
        q = q.filter(CAm.organization_id == args.org_id)
    if args.role_id is not None:
        q = q.filter(CAm.role_id == args.role_id)
    q = q.order_by(CAm.cv_match_score.desc())
    if args.limit is not None:
        q = q.limit(args.limit)
    apps = q.all()
    print(f"scored+undecided candidates to backfill: {len(apps)} (dry_run={args.dry_run})")

    roles: dict[int, Role | None] = {}

    def role_of(rid):
        if rid not in roles:
            roles[rid] = db.query(Role).filter(Role.id == rid).first()
        return roles[rid]

    created: Counter = Counter()
    by_role_paused: Counter = Counter()
    skipped = 0
    n = 0
    for app in apps:
        role = role_of(app.role_id)
        if role is None:
            skipped += 1
            continue
        if args.dry_run:
            # read-only preview: verdict + the post-handover guard ensure() applies
            if is_post_handover_workable_stage(getattr(app, "workable_stage", None)):
                skipped += 1
                continue
            v = recompute_persisted_verdict(db, role=role, app=app)
            if v:
                created[v] += 1
                if getattr(role, "agent_paused_at", None) is not None:
                    by_role_paused["paused"] += 1
                else:
                    by_role_paused["active"] += 1
            else:
                skipped += 1
            continue
        out = ensure_deterministic_decision(db, app=app, role=role)
        if out:
            created[out] += 1
        else:
            skipped += 1
        n += 1
        if n % args.batch == 0:
            db.commit()
            print(f"  ...{n} processed | created={dict(created)} skipped={skipped}")

    if not args.dry_run:
        db.commit()
    print(
        f"DONE — created={dict(created)} total={sum(created.values())} skipped={skipped}"
        + (f" | preview split={dict(by_role_paused)}" if args.dry_run else "")
    )
    db.close()


if __name__ == "__main__":
    main()
