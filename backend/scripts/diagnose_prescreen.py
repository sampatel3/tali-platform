"""Read-only diagnostic: why are pre-screen rejects (not) flowing for a role?

Compares one or more roles side by side and prints, per role:
  - the config that gates pre-screen-reject (threshold, mode, auto_reject,
    agent on/paused) + the resolved numeric threshold the decider uses
  - the global pre-screen gate flag + cull threshold
  - a breakdown of OPEN applications by ``auto_reject_state`` (the column that
    records exactly why each candidate's reject did/didn't fire), by whether
    they were fully cv_match-scored, and by whether they were ever
    pre-screened (``genuine_pre_screen_score_100``)
  - of the candidates below the role's reject threshold: how many already got
    a full cv_match score (= got full-scored instead of pre-screen-rejected)
  - pending AgentDecisions by type (skip_assessment_reject vs reject)

Run it against the BROKEN role and a WORKING role; the difference will be
obvious in the auto_reject_state breakdown.

Usage (from backend/, read-only — never writes):
    DATABASE_URL=... python -m scripts.diagnose_prescreen --role-ids 53 110
"""

from __future__ import annotations

import argparse
from collections import Counter


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.0f}%" if total else "0%"


def _report_role(db, role_id: int) -> None:
    from app.decision_policy.engine import evaluate as _noop  # noqa: F401 (warm imports)
    from app.models.agent_decision import AgentDecision
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.models.role import Role
    from app.platform.config import settings
    from app.services.pre_screening_service import resolved_auto_reject_config

    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        print(f"\n=== role {role_id}: NOT FOUND ===")
        return
    org = db.query(Organization).filter(Organization.id == role.organization_id).first()
    cfg = resolved_auto_reject_config(org, role, db=db)
    thr = cfg.get("threshold_100")

    print(f"\n=================== ROLE {role_id} · {role.name!r} ===================")
    print("CONFIG (what gates the deterministic pre-screen reject):")
    print(f"  score_threshold (raw col)     = {role.score_threshold}")
    print(f"  auto_reject_threshold_mode    = {getattr(role, 'auto_reject_threshold_mode', None)}")
    print(f"  RESOLVED threshold_100        = {thr}   <-- None here = reject DISABLED")
    print(f"  auto_reject (execute vs card) = {bool(role.auto_reject)}")
    print(f"  agentic_mode_enabled          = {bool(role.agentic_mode_enabled)}")
    print(f"  agent_paused_at               = {role.agent_paused_at}")
    print(f"  org auto_reject_enabled (wk)  = {cfg.get('enabled')}")
    print("GLOBAL:")
    print(f"  ENABLE_PRE_SCREEN_GATE        = {settings.ENABLE_PRE_SCREEN_GATE}")
    print(f"  PRE_SCREEN_THRESHOLD (gate)   = {settings.PRE_SCREEN_THRESHOLD}")

    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )
    open_apps = [a for a in apps if (a.application_outcome or "") == "open"]
    rejected = sum(1 for a in apps if (a.application_outcome or "") == "rejected")
    total = len(apps)

    by_state = Counter((a.auto_reject_state or "—") for a in open_apps)
    cv_scored = sum(1 for a in open_apps if a.cv_match_score is not None)
    prescreened = sum(1 for a in open_apps if a.genuine_pre_screen_score_100 is not None)
    rec = Counter((a.pre_screen_recommendation or "—") for a in open_apps)

    # Below the role's reject cutoff by the GENUINE cheap pre-screen score —
    # these are exactly who should have been pre-screen-rejected first.
    below = [
        a for a in open_apps
        if thr is not None
        and a.genuine_pre_screen_score_100 is not None
        and float(a.genuine_pre_screen_score_100) < float(thr)
    ]
    below_then_fullscored = sum(1 for a in below if a.cv_match_score is not None)

    print(f"\nAPPLICATIONS: {total} total · {len(open_apps)} open · {rejected} rejected(outcome)")
    print(f"  open & cv_match-scored        = {cv_scored} ({_pct(cv_scored, len(open_apps))})")
    print(f"  open & ever pre-screened      = {prescreened} ({_pct(prescreened, len(open_apps))})")
    print("  OPEN by auto_reject_state (WHY each reject did/didn't fire):")
    for state, n in by_state.most_common():
        print(f"      {state:<28} {n}")
    print("  OPEN by pre_screen_recommendation:")
    for r, n in rec.most_common():
        print(f"      {r:<28} {n}")
    print(f"  below role cutoff (cheap score) = {len(below)}")
    print(f"     ...of those, ALREADY full-scored = {below_then_fullscored}  "
          f"<-- got full-scored instead of pre-screen-rejected")

    pend = Counter(
        d.decision_type
        for d in db.query(AgentDecision).filter(
            AgentDecision.role_id == role_id,
            AgentDecision.status == "pending",
        ).all()
    )
    print("  PENDING decisions by type:")
    for t, n in pend.most_common():
        print(f"      {t:<28} {n}")

    # Per-candidate sample of below-cutoff applications. The two timestamps are
    # the crux: if cv_match_scored_at is set and pre_screen_run_at is NULL (or
    # later), the candidate was full-scored without/ before a pre-screen reject
    # — the ordering flip that defers the reject to the agent's full-score path.
    sample = below[:10]
    if sample:
        print("  SAMPLE below-cutoff candidates (ordering = the crux):")
        print(f"      {'app_id':>7} {'genuine':>7} {'ps_col':>7} {'cv':>6} "
              f"{'recommendation':<22} {'state':<24} {'pre_screen_run_at':<26} {'cv_match_scored_at'}")
        for a in sample:
            print(
                f"      {a.id:>7} "
                f"{('' if a.genuine_pre_screen_score_100 is None else round(float(a.genuine_pre_screen_score_100),1)):>7} "
                f"{('' if a.pre_screen_score_100 is None else round(float(a.pre_screen_score_100),1)):>7} "
                f"{('' if a.cv_match_score is None else round(float(a.cv_match_score),1)):>6} "
                f"{str(a.pre_screen_recommendation or '—'):<22} "
                f"{str(a.auto_reject_state or '—'):<24} "
                f"{str(a.pre_screen_run_at or '—'):<26} "
                f"{str(a.cv_match_scored_at or '—')}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role-ids", nargs="+", type=int, required=True,
                        help="One or more role ids: the broken one + a working one.")
    args = parser.parse_args()

    from app.platform.database import SessionLocal

    db = SessionLocal()
    try:
        for rid in args.role_ids:
            _report_role(db, rid)
        print("\nHOW TO READ THIS:")
        print("  * If 'below role cutoff ...ALREADY full-scored' is high on the broken")
        print("    role but ~0 on the working one -> candidates got full-scored before")
        print("    the pre-screen reject could fire (the reject defers once cv_match is set).")
        print("  * If RESOLVED threshold_100 is None on the broken role -> the reject is")
        print("    DISABLED for it (no cutoff); check score_threshold / threshold mode.")
        print("  * Compare the auto_reject_state breakdown: 'deferred_to_full_scoring' or")
        print("    'pending_score'/'disabled' dominating shows the failure mode directly.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
