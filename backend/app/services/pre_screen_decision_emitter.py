"""Emit a Decision Hub card when pre-screen scores a candidate below threshold.

Pre-screen is deterministic — if the score is < threshold, the recruiter
needs to see this candidate in the Review queue and either approve the
reject or override and keep them in pipeline. The original design (see
``application_automation_service.run_auto_reject_if_needed`` comment) was
for the agent's next cycle to queue this via ``queue_skip_assessment_reject_decision``,
but the agent's cohort planner never surveyed "below-threshold" candidates,
so they sat invisibly. 270 candidates went missing this way before this
emitter was wired in.

We don't run this through the agent because:
- The decision is deterministic (threshold check, no AI reasoning needed).
- Surfacing them via the agent means waiting for the next cron tick.
- Pre-screen rejects can volume up to thousands; the agent's per-cycle
  decision budget would just throttle them anyway (and we just removed
  that gate, so the only knob left would be cron cadence).

Decision type ``skip_assessment_reject`` is reused (existing type), so the
recruiter UI and downstream handlers don't change — these decisions look
and behave like any agent-queued reject. ``agent_run_id`` is NULL since
no agent run produced them (the column is already nullable).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import Role

logger = logging.getLogger("taali.pre_screen_decision_emitter")


_DECISION_TYPE = "skip_assessment_reject"
_PROMPT_VERSION = "pre_screen_threshold.v1"
_MODEL_VERSION = "pre_screen_v1"  # deterministic; not an LLM


def _idempotency_key(application_id: int) -> str:
    return f"pre_screen_reject:{int(application_id)}"


def _below_threshold(
    score: float | None, recommendation: str | None, threshold: float | None
) -> bool:
    """Whether this candidate is a pre-screen reject under ``threshold`` —
    mirrors the deterministic gate in ``evaluate_auto_reject_decision``.

    A numeric score is authoritative AND only meaningful against a configured
    cutoff: with a numeric score we reject iff ``score < threshold`` and
    ``threshold is not None``. With no threshold there is no score-based
    reject — the ``'Below threshold'`` recommendation is a hard-coded ``< 50``
    label, not a role verdict, so it must NOT keep a numeric-score card alive
    after the cutoff is cleared.

    The recommendation only justifies a reject when there is *no* numeric
    score (must-have miss / invalidated score), with or without a threshold.
    """
    if score is not None:
        return threshold is not None and float(score) < float(threshold)
    return (recommendation or "").strip().lower() == "below threshold"


def _qualitative_reject_reason(application: CandidateApplication) -> str:
    """A recruiter-facing, candidate-specific reason for the pre-screen
    reject. We deliberately do NOT surface the numeric score/threshold —
    those are internal calibration knobs, unhelpful and often "unknown" on
    the card (a must-have miss has no score). Preference order:

      1. Fraud cap — plagiarised CV; the most serious, specific verdict.
      2. The stored pre-screen ``summary`` — the LLM's one-sentence rationale
         naming the specific gap (e.g. a missing must-have). It's qualitative
         prose by construction (the prompt forbids restating the score).
      3. Generic fallback for must-have misses / invalidated scores that
         carry a "Below threshold" verdict but no stored summary.
    """
    evidence = (
        application.pre_screen_evidence
        if isinstance(getattr(application, "pre_screen_evidence", None), dict)
        else {}
    )
    if evidence.get("fraud_capped"):
        return "Flagged for potential fraud — CV copies text verbatim from the job description."
    summary = str(evidence.get("summary") or "").strip()
    if summary:
        return summary if summary[-1:] in ".!?" else f"{summary}."
    return "Does not meet the role's requirements."


def _format_reasoning(application: CandidateApplication) -> str:
    return (
        f"{_qualitative_reject_reason(application)} "
        f"Surfaced for recruiter review — approve to reject, override to keep in pipeline."
    )


def queue_pre_screen_reject(
    db: Session,
    *,
    organization_id: int,
    role: Role,
    application: CandidateApplication,
    pre_screen_score: float | None,
    threshold: float | None,
    evidence: Optional[dict[str, Any]] = None,
) -> AgentDecision | None:
    """Create a pending ``skip_assessment_reject`` ``AgentDecision`` for
    a candidate that failed pre-screen, *or* return the existing one if
    already queued. Returns None on unexpected error (never raises).

    Idempotent on ``application_id`` — re-running pre-screen against the
    same application produces at most one row.

    Gated on ``role.agentic_mode_enabled``: agent-OFF roles aren't under
    agent management, so we shouldn't auto-create Decision Hub cards on
    their behalf. The recruiter would see decisions appearing for roles
    they didn't enable the agent on — surprising and unwelcome.
    """
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return None
    # Defer to full scoring. The pre-screen reject is a CHEAP gate whose only
    # job is to reject before paying for full cv_match scoring. Once a
    # candidate has a cv_match score, that score is authoritative and the
    # agent's cv_match flow owns the reject/send decision — surfacing a
    # *pre-screen* reject card on top conflates the two scorers (this is what
    # mislabelled 42 fully-scored candidates: strong ones, cv up to 84/100,
    # sat in the reject queue). Backstops the gate-level guard in
    # ``evaluate_auto_reject_decision`` (reconcile/backfill also land here).
    if getattr(application, "cv_match_score", None) is not None:
        return None
    try:
        key = _idempotency_key(int(application.id))
        # Skip if THIS app already has any pending decision — not just one
        # with our exact idempotency key. The original backfill (#201 + the
        # post-deploy run) only deduped on its own ``pre_screen_reject:*``
        # key, so apps that already had an agent-emitted ``reject`` row
        # got a *second* row from the backfill — recruiter saw the same
        # candidate twice. One pending per app, always.
        existing_pending = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == int(application.id),
                AgentDecision.status == "pending",
            )
            .order_by(AgentDecision.created_at.desc())
            .first()
        )
        if existing_pending is not None:
            return existing_pending

        body = {"pre_screen_score_100": pre_screen_score, "threshold_100": threshold}
        if evidence:
            body.update(evidence)

        decision = AgentDecision(
            organization_id=int(organization_id),
            role_id=int(role.id),
            application_id=int(application.id),
            agent_run_id=None,  # system-emitted; no agent cycle ran
            decision_type=_DECISION_TYPE,
            recommendation=_DECISION_TYPE,
            status="pending",
            reasoning=_format_reasoning(application),
            evidence=body,
            confidence=None,
            model_version=_MODEL_VERSION,
            prompt_version=_PROMPT_VERSION,
            idempotency_key=key,
            active_capabilities={},
            token_spend={},
        )
        db.add(decision)
        try:
            db.flush()
        except IntegrityError:
            # A row with this app's idempotency key already exists. Two cases:
            #   1. Race — a concurrent path inserted a *pending* row.
            #   2. Replay — a prior card for this app was discarded (e.g. a
            #      threshold 50→30 reconcile), and now we want one again
            #      (30→50). The key is per-application, so we can't insert a
            #      second row; the existing row is non-pending.
            # In case 2 we must REVIVE the existing row to pending, otherwise
            # the candidate is left with no pending card while ``created`` is
            # incremented — the silent-miss the reconcile replay path hit.
            db.rollback()
            existing = (
                db.query(AgentDecision)
                .filter(AgentDecision.idempotency_key == key)
                .first()
            )
            # Revive a previously system-discarded card only when ALL hold:
            #  1. status == 'discarded' with NO human resolver. A recruiter
            #     resolution (``overridden`` / ``approved`` /
            #     ``reverted_for_feedback``, or the toggle-off bulk discard
            #     which sets ``resolved_by_user_id``) must never be reopened.
            #  2. the candidate is STILL below the current threshold by the
            #     same deterministic rule reconcile discards on. Without this,
            #     a card discarded because the threshold was *cleared*
            #     (threshold=None ⇒ no score-based reject) would be flipped
            #     back to pending by a later ``run_auto_reject_if_needed``
            #     (whose decider still treats a stale 'Below threshold' label
            #     as eligible), and the next cohort tick would re-discard it —
            #     churning pending↔discarded every cycle. Gating revival on
            #     ``_below_threshold`` keeps revive and discard in agreement.
            if (
                existing is not None
                and existing.status == "discarded"
                and existing.resolved_by_user_id is None
                and _below_threshold(
                    pre_screen_score,
                    getattr(application, "pre_screen_recommendation", None),
                    threshold,
                )
            ):
                existing.status = "pending"
                existing.resolved_at = None
                existing.resolution_note = None
                existing.agent_run_id = None
                existing.reasoning = _format_reasoning(application)
                existing.evidence = body
                db.flush()
            return existing

        db.add(
            CandidateApplicationEvent(
                application_id=int(application.id),
                organization_id=int(organization_id),
                event_type="agent_decision_queued",
                actor_type="system",
                actor_id=None,
                reason="Queued pre-screen reject",
                idempotency_key=f"agent_decision_queued:pre_screen:{decision.id}",
                event_metadata={
                    "decision_id": int(decision.id),
                    "decision_type": _DECISION_TYPE,
                    "source": "pre_screen_threshold",
                    "pre_screen_score_100": pre_screen_score,
                    "threshold_100": threshold,
                },
            )
        )
        return decision
    except Exception:
        logger.exception(
            "queue_pre_screen_reject failed for application_id=%s",
            getattr(application, "id", None),
        )
        return None


def supersede_pre_screen_reject_on_full_score(
    db: Session, *, application: CandidateApplication, threshold: float | None
) -> int:
    """Discard a pending pre-screen reject once full cv_match scoring lands
    and *clears* the candidate above the pre-screen threshold.

    The pre-screen reject is a cheap pre-scoring gate. A candidate can fail
    that gate (e.g. a truncated CV, a misread questionnaire answer) and then
    score well on the rigorous full scorer — we saw cv_match scores up to
    84/100 on candidates the gate had rejected at 28. Once the authoritative
    full score clears the bar, the pre-screen reject card is wrong; discard it
    so the agent's cv_match flow can send/advance them. Called from the
    scoring orchestrator right after ``cv_match_score`` is written.

    Only discards system cards with no human resolver, and only when the
    score clears the threshold — a full score that's *also* below the bar
    leaves the reject standing. Returns the number discarded.
    """
    score = getattr(application, "cv_match_score", None)
    if score is None or threshold is None or float(score) < float(threshold):
        return 0
    cards = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application.id),
            AgentDecision.status == "pending",
            AgentDecision.decision_type == _DECISION_TYPE,
            AgentDecision.resolved_by_user_id.is_(None),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    note = (
        f"superseded: full cv_match score {float(score):.1f} clears the "
        f"pre-screen threshold {float(threshold):.1f} — agent owns the decision"
    )[:500]
    discarded = 0
    for card in cards:
        card.status = "discarded"
        card.resolved_at = now
        card.resolution_note = note
        discarded += 1
    return discarded


def backfill_pre_screen_reject_reasoning(
    db: Session, *, organization_id: int | None = None
) -> dict:
    """Rewrite the stored ``reasoning`` of existing *pending*
    ``skip_assessment_reject`` cards to the qualitative format.

    Cards created before the reasoning dropped the numeric
    "(score: X, threshold: Y)" template keep that stale text until they're
    revived or re-emitted. This one-shot rewrites them in place from the
    candidate's current pre-screen evidence (fraud verdict / LLM summary /
    generic role-requirements fallback) so the Review queue reads cleanly.
    Idempotent — a card already on the new text is left untouched.

    Returns ``{"updated": int, "scanned": int}``.
    """
    q = (
        db.query(AgentDecision, CandidateApplication)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.status == "pending",
            AgentDecision.decision_type == _DECISION_TYPE,
        )
    )
    if organization_id is not None:
        q = q.filter(AgentDecision.organization_id == int(organization_id))

    updated = 0
    scanned = 0
    for decision, app in q.all():
        scanned += 1
        new_reasoning = _format_reasoning(app)
        if (decision.reasoning or "") != new_reasoning:
            decision.reasoning = new_reasoning
            updated += 1
    if updated:
        db.commit()
    return {"updated": updated, "scanned": scanned}


def supersede_mislabeled_pre_screen_rejects(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """One-shot: discard pending pre-screen reject cards that should never
    have been pre-screen rejects, because the candidate was already fully
    cv_match-scored when (or before) the card fired.

    Background — ``pre_screen_score_100`` is a shared field: pre-screen writes
    it first, then full cv_match scoring *overwrites* it with the cv_match
    score (for ranking) while leaving the pre-screen verdict text alone. The
    pre-screen auto-reject gate then re-fired on that overwritten number,
    producing two wrong cohorts among fully-scored candidates:

      * **A** — passed pre-screen (``llm_score_100 >= threshold``) but got
        carded because the overwritten cv_match score was low. They're real
        rejects, but on the *full* score, not pre-screen — the agent owns
        those.
      * **B** — failed pre-screen but the full score *cleared* them
        (``pre_screen_score_100 >= threshold``; cv up to 84/100). Not rejects
        at all — they belong in send/advance.

    Both are ``NOT-C`` where ``C`` (genuine pre-screen reject) is
    ``llm_score_100 < threshold AND pre_screen_score_100 < threshold``. We
    discard A∪B (no human resolver only) and let the agent re-triage on the
    authoritative cv_match score. ``C`` cards are left untouched.

    Returns ``{"discarded": int, "scanned": int, "skipped_human": int}``
    (``dry_run=True`` reports the same counts without writing).
    """
    rows = (
        db.query(AgentDecision, CandidateApplication)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.status == "pending",
            AgentDecision.decision_type == _DECISION_TYPE,
        )
    )
    if organization_id is not None:
        rows = rows.filter(AgentDecision.organization_id == int(organization_id))

    now = datetime.now(timezone.utc)
    discarded = 0
    scanned = 0
    skipped_human = 0
    for decision, app in rows.all():
        scanned += 1
        evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
        thr = evidence.get("threshold_100")
        ps_evidence = (
            app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        )
        llm = ps_evidence.get("llm_score_100")
        disp = app.pre_screen_score_100
        if thr is None:
            continue  # can't classify without the card's threshold — leave it
        thr = float(thr)
        passed_pre_screen = llm is not None and float(llm) >= thr  # A
        cleared_on_full = disp is not None and float(disp) >= thr  # B
        if not (passed_pre_screen or cleared_on_full):
            continue  # C — genuine pre-screen reject, leave it
        if decision.resolved_by_user_id is not None:
            skipped_human += 1
            continue
        if dry_run:
            discarded += 1
            continue
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = (
            "superseded: candidate fully cv_match-scored; not a pre-screen "
            "reject — agent owns the cv_match decision"
        )[:500]
        discarded += 1
    if discarded and not dry_run:
        db.commit()
    return {"discarded": discarded, "scanned": scanned, "skipped_human": skipped_human}


def backfill_existing_below_threshold(
    db: Session, *, organization_id: int | None = None
) -> dict:
    """One-shot: queue a pre-screen-reject decision for every application
    that's currently below threshold and doesn't already have one.

    Two cohorts qualify:
      * ``pre_screen_score_100 < 50`` with a numeric score — the original
        case from the 270-stranded-candidates incident.
      * ``pre_screen_recommendation = 'Below threshold'`` with NULL score
        — covers candidates whose numeric score got invalidated (#209) or
        who short-circuited on a must-have miss without an LLM call. The
        recommendation is still authoritative; we shouldn't refuse to
        surface them just because the cache-invalidation path nulled the
        number.

    Intended to be invoked once after deploy so historical stranded
    candidates surface in the Review queue. Idempotent — safe to re-run;
    each application gets at most one row via the idempotency key.
    """
    from sqlalchemy import and_, or_

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            or_(
                and_(
                    CandidateApplication.pre_screen_score_100.isnot(None),
                    CandidateApplication.pre_screen_score_100 < 50,
                ),
                CandidateApplication.pre_screen_recommendation == "Below threshold",
            ),
            CandidateApplication.application_outcome == "open",
            Role.deleted_at.is_(None),
            # Only agent-on roles. Agent-off roles aren't under agent
            # management; surfacing decisions for them would surprise the
            # recruiter ("why are these candidates in my queue when I
            # never enabled the agent here?").
            Role.agentic_mode_enabled.is_(True),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))

    created = 0
    skipped_existing = 0
    failed = 0
    for app, role in q.all():
        # Skip apps that already have ANY pending decision, not just our
        # own pre_screen_reject key. The first version of this loop only
        # checked the latter and produced 21 duplicate rows in prod when
        # an agent-emitted reject already existed on the app.
        existing_pending = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == int(app.id),
                AgentDecision.status == "pending",
            )
            .first()
        )
        if existing_pending is not None:
            skipped_existing += 1
            continue
        result = queue_pre_screen_reject(
            db,
            organization_id=int(app.organization_id),
            role=role,
            application=app,
            pre_screen_score=float(app.pre_screen_score_100)
            if app.pre_screen_score_100 is not None
            else None,
            threshold=50.0,
        )
        if result is None:
            failed += 1
        else:
            created += 1
            db.commit()  # commit each row so a single failure doesn't roll back the batch

    return {"created": created, "skipped_existing": skipped_existing, "failed": failed}


def reconcile_pre_screen_reject_decisions(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    threshold: float | None,
) -> dict:
    """Re-align the ``skip_assessment_reject`` queue with a role's new
    pre-screen threshold. Call this when the role's *effective* threshold
    changes (the ``score_threshold`` override or ``auto_reject_threshold_mode``).

    Moving the threshold changes the reject *verdict* for every candidate
    without changing any *score*. So — unlike ``mark_role_scores_stale`` —
    this does NOT invalidate or re-score anything. It only reconciles the
    deterministic reject cards:

    - **Discard** pending cards for candidates now at/above the new
      threshold. Asking the recruiter to approve a reject the current
      cutoff wouldn't produce is exactly the stale-card problem this
      fixes. Cards whose ``pre_screen_recommendation == 'Below threshold'``
      are kept — that verdict is independent of the numeric cutoff
      (must-have miss / invalidated score), same carve-out the emitter and
      backfill already honour.
    - **Emit** a card for every open candidate now below the new threshold
      that doesn't already have a pending decision (reuses
      ``queue_pre_screen_reject`` and its one-pending-per-app invariant).

    No-op for agent-off roles (we don't manage cards there) and for roles
    with ``auto_reject`` on (those disqualify in Workable directly rather
    than carding — that path is the auto-reject task's job, not the Hub).

    Returns ``{"discarded": int, "created": int, "skipped_existing": int}``.
    """
    from sqlalchemy import and_, func, or_

    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return {"discarded": 0, "created": 0, "skipped_existing": 0}
    if bool(getattr(role, "auto_reject", False)):
        return {"discarded": 0, "created": 0, "skipped_existing": 0}

    now = datetime.now(timezone.utc)

    # --- Discard cards the new threshold no longer justifies -------------
    if threshold is not None:
        discard_note = f"superseded: pre-screen threshold changed to {threshold:.1f}"
    else:
        discard_note = "superseded: pre-screen threshold cleared"
    pending_cards = (
        db.query(AgentDecision, CandidateApplication)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
            AgentDecision.decision_type == _DECISION_TYPE,
        )
        .all()
    )
    discarded = 0
    for decision, app in pending_cards:
        if _below_threshold(
            app.pre_screen_score_100, app.pre_screen_recommendation, threshold
        ):
            continue  # still a valid reject under the new cutoff — keep
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = discard_note[:500]
        discarded += 1
    if discarded:
        # Commit the discards before the emit loop. ``queue_pre_screen_reject``
        # issues a full ``db.rollback()`` if it loses an insert race, which
        # would otherwise drop these discards along with the racing row.
        db.commit()

    # --- Emit cards for candidates now below the new threshold -----------
    created = 0
    skipped_existing = 0
    # Case/space-insensitive match — the decider and the discard path both
    # normalize ``pre_screen_recommendation``, so non-canonical stored values
    # ("below threshold", trailing space) must count here too or they'd be
    # treated as below-threshold by policy yet never get a reconciled card.
    rec_below = (
        func.lower(func.trim(func.coalesce(CandidateApplication.pre_screen_recommendation, "")))
        == "below threshold"
    )
    # No numeric score → the 'Below threshold' recommendation (must-have miss
    # / invalidated score) is the reject signal. This branch holds even when
    # ``threshold`` is None (a cleared/auto-fallback threshold), so rec-only
    # rejects aren't stranded.
    below_conditions = [
        and_(CandidateApplication.pre_screen_score_100.is_(None), rec_below)
    ]
    if threshold is not None:
        # Numeric score is authoritative against the cutoff.
        below_conditions.append(
            and_(
                CandidateApplication.pre_screen_score_100.isnot(None),
                CandidateApplication.pre_screen_score_100 < float(threshold),
            )
        )
    below = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.application_outcome == "open",
            or_(*below_conditions),
        )
        .all()
    )
    for app in below:
            existing_pending = (
                db.query(AgentDecision)
                .filter(
                    AgentDecision.application_id == int(app.id),
                    AgentDecision.status == "pending",
                )
                .first()
            )
            if existing_pending is not None:
                skipped_existing += 1
                continue
            result = queue_pre_screen_reject(
                db,
                organization_id=int(app.organization_id),
                role=role,
                application=app,
                pre_screen_score=float(app.pre_screen_score_100)
                if app.pre_screen_score_100 is not None
                else None,
                threshold=float(threshold) if threshold is not None else None,
            )
            if result is not None:
                created += 1
                db.commit()  # per-row so one race doesn't roll back the batch

    return {
        "discarded": discarded,
        "created": created,
        "skipped_existing": skipped_existing,
    }


def rederive_pre_screen_recommendations(
    db: Session,
    *,
    role_id: int | None = None,
    organization_id: int | None = None,
) -> dict:
    """Correct stored ``pre_screen_recommendation`` labels that the old
    hard-coded ``< 50`` rule over-flagged as "Below threshold".

    Rows scored before the label became threshold-aware keep a stale
    "Below threshold" even when the candidate is *above* the role's actual
    cutoff (e.g. a 40-scorer on a role that rejects at 30). This relabels
    them in place — no LLM re-run — so the displayed verdict matches the
    threshold the agent actually rejects on.

    Deliberately **relax-only**: it only touches rows currently labelled
    "Below threshold" whose score is at/above the role's cutoff, and only
    ever moves them *off* the reject label. It never introduces a new
    "Below threshold" (which could trip the label-keyed auto-reject hooks
    in applications_routes). The reject *decisions* are reconciled
    separately and score-authoritatively by
    ``reconcile_pre_screen_reject_decisions``.

    Skips:
    - NULL-score rows — their "Below threshold" is a must-have-miss /
      invalidated verdict, not score-derivable.
    - fraud-capped rows — "Below threshold" there is a fraud verdict
      forced independently of the score band.
    - roles with no configured threshold — nothing to compare against.

    Returns ``{"updated": int, "scanned": int}``.
    """
    # Lazy imports: the emitter is imported by application_automation_service
    # alongside pre_screening_service, so importing the latter at module load
    # would risk a cycle. Inside the function it's safe.
    from sqlalchemy import func

    from .pre_screening_service import resolved_auto_reject_config
    from .pre_screening_snapshot import pre_screen_recommendation_label

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.pre_screen_score_100.isnot(None),
            # Case/space-insensitive — the decider normalizes too, so
            # non-canonical stored labels ("below threshold", trailing space)
            # must be corrected here as well or the self-heal never converges.
            func.lower(func.trim(func.coalesce(CandidateApplication.pre_screen_recommendation, "")))
            == "below threshold",
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    )
    if role_id is not None:
        q = q.filter(CandidateApplication.role_id == int(role_id))
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))

    threshold_cache: dict[int, float | None] = {}
    updated = 0
    scanned = 0
    for app, role in q.all():
        scanned += 1
        evidence = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        if evidence.get("fraud_capped"):
            continue
        if role.id not in threshold_cache:
            threshold_cache[role.id] = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
        threshold = threshold_cache[role.id]
        if threshold is None:
            continue
        if float(app.pre_screen_score_100) < float(threshold):
            continue  # genuinely below the cutoff — keep the reject label
        new_label = pre_screen_recommendation_label(app.pre_screen_score_100, threshold)
        if new_label and new_label != "Below threshold":
            app.pre_screen_recommendation = new_label
            updated += 1
    if updated:
        db.commit()
    return {"updated": updated, "scanned": scanned}


# ---------------------------------------------------------------------------
# Score/decision consistency repairs. A per-app discard helper used by the
# outcome-close path, plus one-shot backfills + a divergence monitor.
# ---------------------------------------------------------------------------


def discard_pending_decisions_for_app(
    db: Session, *, application_id: int, reason: str
) -> int:
    """Discard every pending agent decision for an application — used when the
    application closes (rejected / hired / withdrawn). A closed candidate's
    queued decisions are moot; leaving them pending shows the recruiter live
    cards for people already out of the funnel.

    Never touches a human-resolved row (defensive — a pending row shouldn't
    have a human resolver). Returns the number discarded. Does NOT commit;
    the caller's transaction owns that.
    """
    cards = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application_id),
            AgentDecision.status == "pending",
            AgentDecision.resolved_by_user_id.is_(None),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    discarded = 0
    for card in cards:
        card.status = "discarded"
        card.resolved_at = now
        card.resolution_note = reason[:500]
        discarded += 1
    return discarded


def backfill_discard_decisions_on_closed_apps(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """P1: discard pending agent decisions whose application is no longer open.

    These accumulated because closing an application via a path other than
    approving the decision (Workable sync, manual outcome change, direct
    auto-reject) didn't clear its queued cards. Returns
    ``{"discarded": int, "scanned": int}``.
    """
    q = (
        db.query(AgentDecision, CandidateApplication)
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
        .filter(
            AgentDecision.status == "pending",
            AgentDecision.resolved_by_user_id.is_(None),
            CandidateApplication.application_outcome != "open",
        )
    )
    if organization_id is not None:
        q = q.filter(AgentDecision.organization_id == int(organization_id))
    now = datetime.now(timezone.utc)
    discarded = 0
    scanned = 0
    for decision, app in q.all():
        scanned += 1
        if dry_run:
            discarded += 1
            continue
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = (
            f"superseded: application already closed ({app.application_outcome})"
        )[:500]
        discarded += 1
    if discarded and not dry_run:
        db.commit()
    return {"discarded": discarded, "scanned": scanned}


def backfill_recommendations_from_cvmatch(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """P2: re-derive ``pre_screen_recommendation`` so it matches the current
    score. cv_match scoring overwrites the numeric score but left the frozen
    pre-screen label, leaving "Strong match" on a 12/100 and "Below
    threshold" on a 55/100. Re-label from the current score + role threshold,
    both directions. Fraud-capped rows keep their verdict (not score-derived).

    Returns ``{"updated": int, "scanned": int}``.
    """
    from .pre_screening_service import resolved_auto_reject_config
    from .pre_screening_snapshot import pre_screen_recommendation_label

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            CandidateApplication.pre_screen_score_100.isnot(None),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    threshold_cache: dict[int, float | None] = {}
    updated = 0
    scanned = 0
    for app, role in q.all():
        scanned += 1
        evidence = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        if evidence.get("fraud_capped"):
            continue
        if role.id not in threshold_cache:
            threshold_cache[role.id] = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
        new_label = pre_screen_recommendation_label(app.pre_screen_score_100, threshold_cache[role.id])
        if new_label and new_label != (app.pre_screen_recommendation or ""):
            if not dry_run:
                app.pre_screen_recommendation = new_label
            updated += 1
    if updated and not dry_run:
        db.commit()
    return {"updated": updated, "scanned": scanned}


def backfill_summaries_from_cvmatch(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """P3: fill a missing ``pre_screen_evidence.summary`` from the richer
    ``cv_match_details.summary`` so the card/report has a reason line.
    Only touches rows that have a score but no summary. Returns
    ``{"updated": int, "scanned": int}``.
    """
    q = db.query(CandidateApplication).filter(
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.pre_screen_score_100.isnot(None),
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    updated = 0
    scanned = 0
    for app in q.all():
        scanned += 1
        ev = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        if str(ev.get("summary") or "").strip():
            continue
        details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
        cv_summary = str(details.get("summary") or "").strip()
        if not cv_summary:
            continue
        if not dry_run:
            new_ev = dict(ev)
            new_ev["summary"] = cv_summary[:240]
            app.pre_screen_evidence = new_ev
        updated += 1
    if updated and not dry_run:
        db.commit()
    return {"updated": updated, "scanned": scanned}


def pre_screen_gate_divergence_report(
    db: Session, *, organization_id: int | None = None
) -> dict:
    """P4 monitor (read-only): quantify disagreement between the cheap
    pre-screen gate (``llm_score_100``) and the authoritative full cv_match
    score, for fully-scored candidates. A high divergence rate is the root
    signal behind mislabelled rejects — surfaced so the gate prompt/threshold
    can be recalibrated deliberately.

    Returns counts: candidates scored by both, |gap|>20, gate false-negatives
    (gate<30 but full>=50) and gate false-positives (gate>=50 but full<30).
    """
    q = db.query(CandidateApplication).filter(
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.cv_match_score.isnot(None),
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    both = diverge = false_neg = false_pos = 0
    for app in q.all():
        ev = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        llm = ev.get("llm_score_100")
        if llm is None:
            continue
        llm = float(llm)
        cv = float(app.cv_match_score)
        both += 1
        if abs(llm - cv) > 20:
            diverge += 1
        if llm < 30 and cv >= 50:
            false_neg += 1
        if llm >= 50 and cv < 30:
            false_pos += 1
    return {
        "both_scored": both,
        "diverge_gt20": diverge,
        "gate_false_negatives": false_neg,
        "gate_false_positives": false_pos,
    }
