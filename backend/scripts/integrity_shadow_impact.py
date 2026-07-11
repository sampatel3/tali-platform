"""Read-only shadow analysis: what would flipping HOLISTIC_INTEGRITY_PENALTY_ENABLED
have changed?

Scans every scored application whose ``cv_match_details.integrity_signals`` was
COMPUTED with a non-zero penalty but NOT applied (the shadow state:
``penalty_computed > 0`` and ``applied == false``). For each, it recomputes what
the score WOULD have been after the bounded penalty and reports:

  - total shadow rows, grouped by org + role
  - the distribution of penalty sizes
  - how many would CROSS the role's deterministic decision threshold — i.e. the
    stored score is at/above the role's reject cutoff, but score − penalty falls
    below it. Those are the candidates whose verdict the flip would actually
    flip; everything else keeps the same decision, just a slightly lower number.

The decision threshold reused here is exactly the one the score-time decision
emitter uses: ``resolved_auto_reject_config(None, role, db=db)["threshold_100"]``
(see ``cv_score_orchestrator._persist_score`` /
``supersede_pre_screen_reject_on_full_score``).

This never re-scores or writes anything — it quantifies the flip's live impact
from already-persisted shadow signals, for the PR description.

Usage (from backend/, read-only — never writes):
    DATABASE_URL=... python -m scripts.integrity_shadow_impact
    DATABASE_URL=... python -m scripts.integrity_shadow_impact --org-ids 2 5
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict


def _shadow_row(details: dict) -> tuple[float, bool] | None:
    """Return ``(penalty_computed, applied)`` for a shadow row, or ``None`` when
    the row carries no computed-but-unapplied integrity penalty."""
    if not isinstance(details, dict):
        return None
    sig = details.get("integrity_signals")
    if not isinstance(sig, dict):
        return None
    penalty = sig.get("penalty_computed")
    if not isinstance(penalty, (int, float)) or penalty <= 0:
        return None
    return (float(penalty), bool(sig.get("applied")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--org-ids", type=int, nargs="*", default=None,
        help="Restrict to these organization ids (default: all).",
    )
    args = parser.parse_args()

    from app.models.candidate_application import CandidateApplication
    from app.models.role import Role
    from app.platform.database import SessionLocal
    from app.services.pre_screening_service import resolved_auto_reject_config

    db = SessionLocal()
    try:
        q = db.query(CandidateApplication).filter(
            CandidateApplication.cv_match_details.isnot(None),
            CandidateApplication.cv_match_score.isnot(None),
        )
        if args.org_ids:
            q = q.filter(CandidateApplication.organization_id.in_(args.org_ids))

        # Cache the resolved threshold per role — cheap, but avoids re-resolving
        # the same role for every application in a large pipeline.
        threshold_by_role: dict[int, float | None] = {}

        def _threshold_for(role: Role | None) -> float | None:
            if role is None:
                return None
            rid = int(role.id)
            if rid not in threshold_by_role:
                cfg = resolved_auto_reject_config(None, role, db=db)
                threshold_by_role[rid] = cfg.get("threshold_100")
            return threshold_by_role[rid]

        shadow_total = 0
        applied_total = 0
        by_org: Counter[int] = Counter()
        by_role: Counter[tuple[int, str]] = Counter()
        penalty_buckets: Counter[str] = Counter()
        crossings = 0
        crossings_by_role: dict[tuple[int, str], int] = defaultdict(int)
        no_threshold = 0

        for app in q.yield_per(500):
            row = _shadow_row(app.cv_match_details)
            if row is None:
                continue
            penalty, applied = row
            if applied:
                # Already deducted (newer score after the flip). Count separately
                # so the "shadow" bucket only holds rows the flip WOULD change.
                applied_total += 1
                continue
            shadow_total += 1
            by_org[int(app.organization_id)] += 1
            role = app.role
            role_key = (int(app.role_id), str(getattr(role, "name", "") or f"role {app.role_id}"))
            by_role[role_key] += 1

            if penalty >= 15:
                penalty_buckets["15 (capped)"] += 1
            elif penalty >= 10:
                penalty_buckets["10-14"] += 1
            elif penalty >= 5:
                penalty_buckets["5-9"] += 1
            else:
                penalty_buckets["<5"] += 1

            threshold = _threshold_for(role)
            if threshold is None:
                no_threshold += 1
                continue
            stored = float(app.cv_match_score)
            post = max(0.0, stored - penalty)
            # Crosses the cutoff: was at/above the reject threshold, now below it.
            if stored >= threshold and post < threshold:
                crossings += 1
                crossings_by_role[role_key] += 1

        print("=" * 68)
        print("INTEGRITY PENALTY — SHADOW IMPACT (read-only)")
        print("=" * 68)
        print(f"Shadow rows (penalty_computed>0, applied=false): {shadow_total}")
        print(f"Already-applied rows (post-flip scores):         {applied_total}")
        if shadow_total == 0:
            print("\nNo shadow rows — nothing the flip would change.")
            return 0

        print("\nBy organization:")
        for org_id, n in by_org.most_common():
            print(f"  org {org_id}: {n}")

        print("\nBy role (top 20):")
        for (role_id, name), n in by_role.most_common(20):
            print(f"  role {role_id} {name!r}: {n}")

        print("\nPenalty size distribution:")
        for bucket in ("<5", "5-9", "10-14", "15 (capped)"):
            if penalty_buckets[bucket]:
                print(f"  {bucket:>12}: {penalty_buckets[bucket]}")

        print("\nDECISION IMPACT (reuses the score-time reject threshold):")
        print(f"  Would cross the reject threshold: {crossings} of {shadow_total}")
        if no_threshold:
            print(f"  (no resolved threshold, decision unchanged): {no_threshold}")
        if crossings:
            print("  Crossings by role:")
            for (role_id, name), n in sorted(
                crossings_by_role.items(), key=lambda kv: -kv[1]
            )[:20]:
                print(f"    role {role_id} {name!r}: {n}")
        print(
            "\nInterpretation: the non-crossing rows keep the same verdict — only "
            "a slightly lower number. The crossings are the candidates whose "
            "decision the flip actually changes."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
