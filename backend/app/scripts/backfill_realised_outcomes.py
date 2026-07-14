"""One-time backfill: record realised outcomes for already-approved decisions.

Background
----------
The realised-outcomes loop (``app.agent_runtime.outcome_learning``) records
what actually happened to a candidate after an agent decision was approved —
surfaced in the Home "Is Taali learning from you?" / REALISED OUTCOMES column
(``GET /agent/realised-outcomes``, sourced from
``role.agent_calibration["outcomes"]``).

A latent ordering bug meant the recorder never fired for the agent's own
approve action: the transition ran *before* the decision was stamped
``approved``, so the
recorder's ``status="approved"`` lookup found nothing. The fix records the
outcome from the approve action itself going forward. This script repairs the
history that the bug missed.

Scope (entries written), mirroring the live recorder
----------------------------------------------------
For every ``approved`` AgentDecision on a non-deleted application:
* ``advance_to_interview`` + hiring stage interview/offer/hired
                                                              → ``interviewed``
* ``advance_to_interview`` + app outcome ``hired``            → ``hired``
* ``reject`` / ``skip_assessment_reject`` + outcome ``rejected``
                                                              → ``rejected_confirmed``

``observed_at`` is the relevant pipeline timestamp (stage/outcome updated_at),
falling back to the decision's ``resolved_at`` — so the column orders by when
the outcome actually happened, not when this backfill ran.

Idempotent
----------
Each (decision_id, outcome) pair is written at most once: existing entries on
the role are skipped, so re-running — or running after the going-forward fix has
already recorded some — adds nothing new. Per-role outcomes are FIFO-capped at
``calibration._MAX_OUTCOMES`` (most-recent kept); entries are inserted oldest
-first so the newest survive the cap.

Note
----
Only the JSON calibration path the UI reads is backfilled. HiringOutcome
Graphiti episodes (emitted live by ``_append_outcome``) are NOT replayed here —
low volume, separate concern, can be backfilled independently if the training
substrate needs them.

Usage::

    python -m app.scripts.backfill_realised_outcomes            # dry run
    python -m app.scripts.backfill_realised_outcomes --apply    # commit
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..agent_runtime import calibration as calibration_mod
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role


_DECISION_TYPES = ("advance_to_interview", "reject", "skip_assessment_reject")


def _entries_for(decision: AgentDecision, app: CandidateApplication):
    """Yield (outcome, observed_at) tuples for an approved decision + its app,
    mirroring outcome_learning.record_outcome_for_approved_decision (plus the
    later 'hired' transition the going-forward hook handles live)."""
    dtype = str(decision.decision_type)
    if dtype == "advance_to_interview":
        from ..services.recruiter_stage_service import recruiter_stage_from_external

        hiring_stage = str(getattr(app, "recruiter_stage", None) or "").lower()
        if (
            not hiring_stage
            or str(getattr(app, "recruiter_stage_source", None) or "") == "migration"
        ):
            hiring_stage = recruiter_stage_from_external(
                getattr(app, "external_stage_normalized", None)
                or getattr(app, "external_stage_raw", None)
                or getattr(app, "workable_stage", None)
                or getattr(app, "bullhorn_status", None)
            ) or hiring_stage
        if hiring_stage in {"interviewing", "offer", "hired"}:
            yield "interviewed", (
                getattr(app, "recruiter_stage_updated_at", None)
                or app.pipeline_stage_updated_at
            )
        if str(app.application_outcome) == "hired":
            yield "hired", app.application_outcome_updated_at
    elif dtype in ("reject", "skip_assessment_reject"):
        if str(app.application_outcome) == "rejected":
            yield "rejected_confirmed", app.application_outcome_updated_at


def backfill_realised_outcomes(db: Session, *, apply: bool = False) -> dict:
    """Backfill realised outcomes onto roles. Returns a summary dict."""
    rows = (
        db.query(AgentDecision, CandidateApplication)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.status == "approved",
            AgentDecision.decision_type.in_(_DECISION_TYPES),
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    by_role: dict[int, list[dict]] = defaultdict(list)
    for decision, app in rows:
        role_id = getattr(app, "role_id", None)
        if role_id is None:
            continue
        for outcome, ts in _entries_for(decision, app):
            observed = ts or decision.resolved_at or now
            observed_iso = (
                observed.isoformat() if hasattr(observed, "isoformat") else str(observed)
            )
            by_role[int(role_id)].append(
                {
                    "decision_type": str(decision.decision_type),
                    "decision_id": int(decision.id),
                    "outcome": outcome,
                    "observed_at": observed_iso,
                    "application_id": int(app.id),
                }
            )

    summary = {
        "roles_updated": 0,
        "entries_added": 0,
        "skipped_existing": 0,
        "by_outcome": {},
        "applied": apply,
    }

    for role_id, entries in sorted(by_role.items()):
        role = (
            db.query(Role)
            .filter(Role.id == role_id, Role.deleted_at.is_(None))
            .first()
        )
        if role is None:
            continue
        existing = (role.agent_calibration or {}).get("outcomes") or []
        seen = {
            (e.get("decision_id"), e.get("outcome"))
            for e in existing
            if isinstance(e, dict)
        }
        new_entries: list[dict] = []
        for entry in entries:
            key = (entry["decision_id"], entry["outcome"])
            if key in seen:
                summary["skipped_existing"] += 1
                continue
            seen.add(key)
            new_entries.append(entry)
        if not new_entries:
            continue
        # Oldest-first so the most-recent survive the FIFO cap.
        new_entries.sort(key=lambda e: e["observed_at"])
        summary["roles_updated"] += 1
        summary["entries_added"] += len(new_entries)
        for entry in new_entries:
            summary["by_outcome"][entry["outcome"]] = (
                summary["by_outcome"].get(entry["outcome"], 0) + 1
            )
        print(
            f"  role_id={role_id} ({role.name!r}) +{len(new_entries)} outcomes",
            flush=True,
        )
        if apply:
            calibration_mod.save(db, role=role, updates={"outcomes": new_entries})

    if apply:
        db.commit()
    return summary


def main() -> int:
    from ..platform.database import SessionLocal

    apply = "--apply" in sys.argv[1:]
    db = SessionLocal()
    try:
        print(
            f"[backfill_realised_outcomes] mode={'APPLY' if apply else 'DRY-RUN'}",
            flush=True,
        )
        summary = backfill_realised_outcomes(db, apply=apply)
        print(
            f"[backfill_realised_outcomes] roles_updated={summary['roles_updated']} "
            f"entries_added={summary['entries_added']} "
            f"skipped_existing={summary['skipped_existing']} "
            f"by_outcome={summary['by_outcome']}",
            flush=True,
        )
        print(
            "  (note: per-role outcomes are FIFO-capped at "
            f"{calibration_mod._MAX_OUTCOMES}; most-recent kept)",
            flush=True,
        )
        if not apply and summary["entries_added"]:
            print("  (dry run — re-run with --apply to commit)", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
