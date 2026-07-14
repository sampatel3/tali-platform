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

from ..domains.assessments_runtime.pipeline_service import normalize_pipeline_key
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import Role
from ..platform.config import settings

logger = logging.getLogger("taali.pre_screen_decision_emitter")


_DECISION_TYPE = "skip_assessment_reject"
_PROMPT_VERSION = "pre_screen_threshold.v1"
_MODEL_VERSION = "pre_screen_v1"  # deterministic; not an LLM

# Native apply knockout gate — a distinct deterministic source that reuses the
# SAME decision type / card / event shape as the pre-screen reject (so the
# Decision Hub and downstream handlers are unchanged) but is keyed off the
# application-form knockout answers, which are evaluated at apply time BEFORE any
# pre-screen has run.
_KNOCKOUT_PROMPT_VERSION = "knockout_screening.v1"
_KNOCKOUT_MODEL_VERSION = "knockout_v1"  # deterministic; not an LLM


def _idempotency_key(application_id: int) -> str:
    return f"pre_screen_reject:{int(application_id)}"


def _knockout_idempotency_key(application_id: int) -> str:
    return f"knockout_reject:{int(application_id)}"


def _below_threshold(
    score: float | None, recommendation: str | None, threshold: float | None
) -> bool:
    """Whether this candidate is a pre-screen reject under ``threshold`` —
    mirrors the deterministic gate in ``evaluate_auto_reject_decision``.

    When the role carries no explicit cutoff (manual mode with no override, or
    a cleared threshold), the reject is still defined by the GLOBAL pre-screen
    gate (``settings.PRE_SCREEN_THRESHOLD``) — the same cutoff the emitter and
    the auto-scorer use to decide who is skipped from full scoring and so stays
    a pre-screen reject. The prior behaviour treated ``threshold is None`` as
    "no score-based reject", which disagreed with both the auto-scorer (it
    skips every sub-gate candidate from full scoring) and
    ``evaluate_auto_reject_decision`` (it rejects on a ``'Below threshold'``
    verdict even with no role threshold). That divergence let the reconcile
    discard a numerically scored sub-gate candidate's card without re-emitting
    it, stranding the candidate in Applied with no decision to action.

    A numeric score is authoritative against the effective cutoff (reject iff
    ``score < effective``). With no score, the ``'Below threshold'``
    recommendation (must-have miss / invalidated score) is the reject signal.
    """
    effective = (
        float(threshold)
        if threshold is not None
        else float(settings.PRE_SCREEN_THRESHOLD)
    )
    if score is not None:
        return float(score) < effective
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


def _pre_screen_passed(application: CandidateApplication) -> bool:
    """True if the pre-screen gate PASSED this candidate (decision 'yes'/'maybe').

    The stored ``decision`` is the authoritative pre-screen verdict. A passed
    candidate must NEVER be treated as a pre-screen reject — even when the
    numeric ``pre_screen_score_100`` has been contaminated by a later cv_match
    write that disagrees with it. (Real case, app 48632: decision 'yes',
    llm_score 75, yet the column held a stale 16.7 from a cv_match run, which
    re-created the reject card on the next re-score.)
    """
    ev = (
        application.pre_screen_evidence
        if isinstance(getattr(application, "pre_screen_evidence", None), dict)
        else {}
    )
    return str(ev.get("decision") or "").strip().lower() in ("yes", "maybe")


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

    A pre-screen reject is DETERMINISTIC policy, so the card is created
    regardless of ``role.agentic_mode_enabled`` — the agent toggle governs
    autonomous *execution* (advancing, sending assessments, Workable
    disqualify), not whether a deterministic reject is queued for human
    review. The irreversible Workable auto-disqualify stays gated upstream
    (``run_auto_reject_if_needed`` → ``auto_disqualify_eligible``).
    """
    # HARD GUARD: a `sourced` prospect is pre-applied — no CV, never scored,
    # never in the decision queue. It has no verdict, so it must never produce a
    # reject card. It reaches `applied` (and only then gets scored/decided) when
    # the person engages. This backstops the natural exclusion (a sourced app has
    # no pre_screen_run_at, caught below) with an explicit stage gate.
    if normalize_pipeline_key(getattr(application, "pipeline_stage", None)) == "sourced":
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
    # Respect the pre-screen verdict. The decision ('yes'/'maybe') is the
    # authoritative gate result; never card (or revive) a candidate the gate
    # passed, even if the numeric ``pre_screen_score_100`` was contaminated by
    # a later cv_match write that disagrees with it. Placed before the
    # creation/revival logic so both paths are covered.
    if _pre_screen_passed(application):
        return None
    # A post-handover Workable stage (phone/technical/final interview, offer,
    # hired — e.g. the recruiter advanced them there before the application
    # entered Taali) does NOT suppress the card: the candidate is decided like
    # everyone else, and every approve surface warns that acting on the reject
    # disqualifies someone already advanced in Workable. Only the AUTOMATED
    # Workable disqualify is hard-blocked for such candidates (the
    # ``auto_disqualify_eligible`` rail in decision_policy.auto_reject).
    # Require a GENUINE pre-screen run. ``pre_screen_recommendation`` /
    # ``pre_screen_score_100`` can be stamped by a cv_match snapshot refresh
    # without any pre-screen ever running (e.g. a cv_match 'no' whose numeric
    # score was later invalidated), which would surface a "pre-screen reject"
    # card for a candidate that was never pre-screened. ``pre_screen_run_at`` is
    # set ONLY by the pre-screen engine (execute_pre_screen_only / fraud gate),
    # never by the snapshot — so it cleanly separates a real reject from a stale
    # label.
    if getattr(application, "pre_screen_run_at", None) is None:
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
            # Revive a previously system-terminated card only when ALL hold:
            #  1. status is 'discarded' OR 'expired' with NO human resolver. A
            #     recruiter resolution (``overridden`` / ``approved`` /
            #     ``reverted_for_feedback``, or the toggle-off bulk discard
            #     which sets ``resolved_by_user_id``) must never be reopened.
            #     'expired' is included because the SLA sweep used to age out a
            #     still-valid pre-screen reject after 14 days, stranding the
            #     candidate with no pending card — reviving it (rather than
            #     leaving it expired) keeps the reject actionable.
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
                and existing.status in ("discarded", "expired")
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


def queue_knockout_reject(
    db: Session,
    *,
    organization_id: int,
    role: Role,
    application: CandidateApplication,
    reason: str,
    failed_question_ids: list[int] | None = None,
    disqualification_reason_id: int | None = None,
) -> AgentDecision | None:
    """Create a pending ``skip_assessment_reject`` ``AgentDecision`` for an
    application that failed the native apply knockout gate — the same card +
    ``agent_decision_queued`` event a pre-screen reject emits, so it surfaces on
    the Decision Hub identically and resolves through the same approve path.

    Unlike ``queue_pre_screen_reject`` this carries NONE of the pre-screen guards
    (cv_match / pre_screen_run_at / verdict): the knockout gate runs at apply
    time, before any scoring, so those signals don't exist yet. ``reason`` is a
    recruiter-facing string sourced from the org's disqualification-reason
    catalog; ``failed_question_ids`` / ``disqualification_reason_id`` are stored
    in the decision evidence for the audit trail (never surfaced to the
    applicant). Idempotent per application; returns the existing pending card if
    one exists. Never raises.
    """
    # HARD GUARD: a `sourced` prospect never enters the decision queue (it hasn't
    # applied, so it can't have failed a knockout gate). Belt-and-braces — the
    # apply path only knockouts a real application — but keeps the invariant
    # explicit at every card-creation entry point.
    if normalize_pipeline_key(getattr(application, "pipeline_stage", None)) == "sourced":
        return None
    try:
        # One pending decision per app — never double-card a candidate.
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

        clean_reason = (reason or "").strip() or "Did not meet the role's required screening criteria."
        reasoning = (
            f"{clean_reason if clean_reason[-1:] in '.!?' else clean_reason + '.'} "
            f"Surfaced for recruiter review — approve to reject, override to keep in pipeline."
        )
        body: dict[str, Any] = {
            "source": "knockout_screening",
            "failed_question_ids": list(failed_question_ids or []),
        }
        if disqualification_reason_id is not None:
            body["disqualification_reason_id"] = int(disqualification_reason_id)

        key = _knockout_idempotency_key(int(application.id))

        # A prior card can exist NON-pending under this app's knockout key — a
        # re-application over a soft-deleted row whose earlier card was
        # discarded/expired. Revive it (fresh knockout verdict, same card)
        # unless a HUMAN resolved it; a recruiter resolution is never reopened
        # (the reject still surfaces via the app's auto_reject_* stamps).
        prior = (
            db.query(AgentDecision)
            .filter(AgentDecision.idempotency_key == key)
            .first()
        )
        if prior is not None:
            if prior.resolved_by_user_id is None:
                prior.status = "pending"
                prior.resolved_at = None
                prior.resolution_note = None
                prior.reasoning = reasoning
                prior.evidence = body
                db.flush()
            return prior

        decision = AgentDecision(
            organization_id=int(organization_id),
            role_id=int(role.id),
            application_id=int(application.id),
            agent_run_id=None,  # system-emitted; no agent cycle ran
            decision_type=_DECISION_TYPE,
            recommendation=_DECISION_TYPE,
            status="pending",
            reasoning=reasoning,
            evidence=body,
            confidence=None,
            model_version=_KNOCKOUT_MODEL_VERSION,
            prompt_version=_KNOCKOUT_PROMPT_VERSION,
            idempotency_key=key,
            active_capabilities={},
            token_spend={},
        )
        try:
            # SAVEPOINT, not a session rollback: the caller's transaction also
            # carries the just-created application (and, on re-apply, its
            # restore) — a full rollback on an idempotency-key race would
            # strand the applicant with no application at all.
            with db.begin_nested():
                db.add(decision)
                db.flush()
        except IntegrityError:
            # A concurrent request inserted the card between the pre-check and
            # this flush. Return the winning row rather than duplicating.
            return (
                db.query(AgentDecision)
                .filter(AgentDecision.idempotency_key == key)
                .first()
            )

        db.add(
            CandidateApplicationEvent(
                application_id=int(application.id),
                organization_id=int(organization_id),
                event_type="agent_decision_queued",
                actor_type="system",
                actor_id=None,
                reason="Queued screening knockout reject",
                idempotency_key=f"agent_decision_queued:knockout:{decision.id}",
                event_metadata={
                    "decision_id": int(decision.id),
                    "decision_type": _DECISION_TYPE,
                    "source": "knockout_screening",
                    "failed_question_ids": list(failed_question_ids or []),
                },
            )
        )
        return decision
    except Exception:
        logger.exception(
            "queue_knockout_reject failed for application_id=%s",
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
      * ``pre_screen_score_100`` below the role's configured cutoff
        (``role.score_threshold``) with a numeric score — the original
        case from the 270-stranded-candidates incident. Anchoring to the
        role's real cutoff (not a flat 50) keeps the backfill consistent
        with live auto-reject eligibility (#201).
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
    from sqlalchemy import and_, func, or_

    from .pre_screening_service import resolved_auto_reject_config

    # Roles without an explicit cutoff fall back to the documented default
    # of 50 (same number the legacy backfill hard-coded), so unconfigured
    # roles keep surfacing below-threshold candidates instead of silently
    # selecting nothing. Roles WITH a cutoff use their own value (#201).
    _DEFAULT_CUTOFF = 50
    effective_cutoff = func.coalesce(Role.score_threshold, _DEFAULT_CUTOFF)

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            or_(
                and_(
                    CandidateApplication.pre_screen_score_100.isnot(None),
                    CandidateApplication.pre_screen_score_100 < effective_cutoff,
                ),
                CandidateApplication.pre_screen_recommendation == "Below threshold",
            ),
            CandidateApplication.application_outcome == "open",
            Role.deleted_at.is_(None),
            # GENUINE pre-screen only: a stale 'Below threshold' label can be set
            # by a cv_match snapshot refresh with no pre-screen ever run.
            CandidateApplication.pre_screen_run_at.isnot(None),
            # Surface deterministic pre-screen rejects for EVERY role, agent on
            # or off — a below-threshold verdict is policy, not an agent action.
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
        role_threshold = resolved_auto_reject_config(None, role, db=db).get("threshold_100")
        if role_threshold is None:
            role_threshold = float(_DEFAULT_CUTOFF)
        result = queue_pre_screen_reject(
            db,
            organization_id=int(app.organization_id),
            role=role,
            application=app,
            pre_screen_score=float(app.pre_screen_score_100)
            if app.pre_screen_score_100 is not None
            else None,
            threshold=role_threshold,
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

    # The role may carry no explicit cutoff (manual mode with no override, or a
    # cleared threshold). A pre-screen reject is still defined then by the
    # GLOBAL gate the emitter and the auto-scorer use
    # (``settings.PRE_SCREEN_THRESHOLD``): below it the candidate is skipped
    # from full scoring and stays a reject. Reconciling against a bare ``None``
    # instead made every numerically scored sub-gate candidate read as "not a
    # reject", so the discard loop dropped its card and the emit loop never
    # re-created it (its numeric branch was gated on a non-None threshold) —
    # stranding the candidate in Applied with no decision.
    effective_threshold = (
        float(threshold)
        if threshold is not None
        else float(settings.PRE_SCREEN_THRESHOLD)
    )

    # --- Discard cards the effective cutoff no longer justifies -----------
    if threshold is not None:
        discard_note = f"superseded: pre-screen threshold changed to {threshold:.1f}"
    else:
        discard_note = (
            f"superseded: at/above pre-screen gate {effective_threshold:.1f}"
        )
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
        # A post-handover Workable stage does NOT discard the card — Taali's
        # pre-screen reject stays a live HITL second opinion (approve surfaces
        # warn the recruiter; nothing auto-executes it). Only a threshold
        # change that invalidates the verdict discards below.
        if _below_threshold(
            app.pre_screen_score_100,
            app.pre_screen_recommendation,
            effective_threshold,
        ):
            continue  # still a valid reject under the effective cutoff — keep
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
    # / invalidated score) is the reject signal. A numeric score is judged
    # against the effective cutoff (the role override, else the global gate),
    # so numerically scored sub-gate candidates are re-emitted even when the
    # role itself has no threshold — the case that previously stranded them.
    below_conditions = [
        and_(CandidateApplication.pre_screen_score_100.is_(None), rec_below),
        and_(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_score_100 < effective_threshold,
        ),
    ]
    below = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.application_outcome == "open",
            # A `sourced` prospect is never in the decision queue (see the
            # queue_* guards); exclude it here so a threshold reconcile can't
            # emit a reject card for one.
            CandidateApplication.pipeline_stage != "sourced",
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
                threshold=effective_threshold,
            )
            if result is not None:
                created += 1
                db.commit()  # per-row so one race doesn't roll back the batch

    return {
        "discarded": discarded,
        "created": created,
        "skipped_existing": skipped_existing,
    }


# Advance-family decision types a changed threshold can supersede — mirrors
# the Decision Hub "advance" bucket (routes.DECISION_TYPE_CATEGORIES["advance"]).
_ADVANCE_DECISION_TYPES = (
    "advance_to_interview",
    "send_assessment",
    "resend_assessment_invite",
)


def retract_advances_below_threshold(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    threshold: float | None,
) -> dict:
    """Discard pending advance / send_assessment cards for candidates the
    current pre-screen threshold now rejects.

    ``reconcile_pre_screen_reject_decisions`` only manages
    ``skip_assessment_reject`` cards, so changing the threshold otherwise
    leaves a stale "advance" card standing for a candidate now below the cutoff
    — and because the reject emit loop skips applications that already have a
    pending decision, that stale advance even *blocks* the reject card from
    being created. Run this BEFORE the reject reconcile so the freed slot gets
    the correct ``skip_assessment_reject`` in its place.

    Uses the same below-threshold test as the reject path
    (``pre_screen_score_100`` vs the cutoff), so every advance discarded here is
    one the reject reconcile re-emits as a reject — never left card-less.

    No-op for agent-off roles, for ``auto_reject`` roles (same carve-out as the
    reject reconcile), and when ``threshold`` is None (no cutoff to judge
    against). A post-handover Workable stage does not exempt the candidate —
    they are re-decided like everyone else and the replacement reject card
    carries the mid-interview warning on every approve surface. Returns
    ``{"discarded": int}``.
    """
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return {"discarded": 0}
    if bool(getattr(role, "auto_reject", False)):
        return {"discarded": 0}
    if threshold is None:
        return {"discarded": 0}

    now = datetime.now(timezone.utc)
    note = (
        f"superseded: pre-screen threshold changed to {threshold:.1f}; "
        "candidate now below cutoff"
    )[:500]
    pending = (
        db.query(AgentDecision, CandidateApplication)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
            AgentDecision.decision_type.in_(_ADVANCE_DECISION_TYPES),
            AgentDecision.resolved_by_user_id.is_(None),
        )
        .all()
    )
    discarded = 0
    for decision, app in pending:
        if not _below_threshold(
            app.pre_screen_score_100, app.pre_screen_recommendation, threshold
        ):
            continue
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = note
        discarded += 1
    if discarded:
        db.commit()
    return {"discarded": discarded}


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
    db: Session,
    *,
    application_id: int,
    reason: str,
    decision_types: tuple[str, ...] | None = None,
) -> int:
    """Discard pending agent decisions for an application — used when the
    application closes (rejected / hired / withdrawn). A closed candidate's
    queued decisions are moot; leaving them pending shows the recruiter live
    cards for people already out of the funnel.

    ``decision_types`` optionally restricts the discard to specific decision
    types (e.g. ``("reject", "skip_assessment_reject")`` to clear only stale
    reject cards while leaving legitimate advance/send cards live, for a
    candidate who is being interviewed but not yet terminally resolved).
    Defaults to all pending decisions.

    Never touches a human-resolved row (defensive — a pending row shouldn't
    have a human resolver). Returns the number discarded. Does NOT commit;
    the caller's transaction owns that.
    """
    query = db.query(AgentDecision).filter(
        AgentDecision.application_id == int(application_id),
        AgentDecision.status == "pending",
        AgentDecision.resolved_by_user_id.is_(None),
    )
    if decision_types:
        query = query.filter(AgentDecision.decision_type.in_(tuple(decision_types)))
    cards = query.all()
    now = datetime.now(timezone.utc)
    discarded = 0
    for card in cards:
        card.status = "discarded"
        card.resolved_at = now
        card.resolution_note = reason[:500]
        discarded += 1
    return discarded


def discard_pending_decisions_for_role(
    db: Session,
    *,
    role_id: int,
    reason: str,
    resolved_by_user_id: int | None = None,
) -> int:
    """Discard pending AGENT-SUBJECTIVE decisions for a role — used when the
    agent is turned OFF. With the agent disabled, advance / send-assessment /
    full-score-reject cards it raised should clear from the Review queue.

    EXCEPTION: ``skip_assessment_reject`` (pre-screen reject) cards are
    DETERMINISTIC policy and agent-independent — they survive turning the agent
    off, so the recruiter keeps a complete below-threshold reject queue either
    way.

    ``resolved_by_user_id`` attributes the discard to the recruiter who
    toggled the agent off (a deliberate human resolution, so the emitter's
    revival path won't reopen it). Does NOT commit; the caller owns the txn.
    Returns the number discarded.
    """
    cards = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role_id),
            AgentDecision.status == "pending",
            AgentDecision.resolved_by_user_id.is_(None),
            AgentDecision.decision_type != "skip_assessment_reject",
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    discarded = 0
    for card in cards:
        card.status = "discarded"
        card.resolved_at = now
        card.resolution_note = reason[:500]
        if resolved_by_user_id is not None:
            card.resolved_by_user_id = int(resolved_by_user_id)
        discarded += 1
    return discarded


def backfill_discard_decisions_on_agent_off_roles(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """Discard pending AGENT-SUBJECTIVE decisions on roles whose agent is OFF
    (``agentic_mode_enabled`` not true). These are orphaned agent cards a
    toggle-off should have cleared. Deterministic ``skip_assessment_reject``
    (pre-screen reject) cards are agent-independent and are left intact.
    Returns ``{"discarded": int, "scanned": int}``.
    """
    q = (
        db.query(AgentDecision)
        .join(Role, Role.id == AgentDecision.role_id)
        .filter(
            AgentDecision.status == "pending",
            AgentDecision.resolved_by_user_id.is_(None),
            Role.agentic_mode_enabled.isnot(True),
            AgentDecision.decision_type != "skip_assessment_reject",
        )
    )
    if organization_id is not None:
        q = q.filter(AgentDecision.organization_id == int(organization_id))
    now = datetime.now(timezone.utc)
    discarded = 0
    scanned = 0
    for card in q.all():
        scanned += 1
        if dry_run:
            discarded += 1
            continue
        card.status = "discarded"
        card.resolved_at = now
        card.resolution_note = "superseded: agent disabled for this role"[:500]
        discarded += 1
    if discarded and not dry_run:
        db.commit()
    return {"discarded": discarded, "scanned": scanned}


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


def repair_passed_prescreen_contamination(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """Deterministic repair for candidates the pre-screen gate PASSED
    (``decision`` 'yes'/'maybe') but who were treated as rejects because a
    later cv_match write contaminated the numeric ``pre_screen_score_100``.

    Two corrections:
      1. Discard their pending ``skip_assessment_reject`` cards.
      2. Clear the false "Below threshold" recommendation, re-labelling from
         the genuine pre-screen score (never "Below threshold" for a passed
         candidate — falls back to "Manual review recommended").

    Returns ``{"cards_discarded": int, "recs_fixed": int}``.
    """
    from sqlalchemy import func

    from .pre_screening_service import resolved_auto_reject_config
    from .pre_screening_snapshot import pre_screen_recommendation_label

    now = datetime.now(timezone.utc)

    # (1) Discard pending reject cards for passed candidates.
    cq = (
        db.query(AgentDecision, CandidateApplication)
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
        .filter(
            AgentDecision.status == "pending",
            AgentDecision.decision_type == _DECISION_TYPE,
            AgentDecision.resolved_by_user_id.is_(None),
        )
    )
    if organization_id is not None:
        cq = cq.filter(AgentDecision.organization_id == int(organization_id))
    cards_discarded = 0
    for decision, app in cq.all():
        if not _pre_screen_passed(app):
            continue
        if not dry_run:
            decision.status = "discarded"
            decision.resolved_at = now
            decision.resolution_note = (
                "superseded: candidate passed pre-screen (decision=yes) — not a reject"
            )[:500]
        cards_discarded += 1

    # (2) Clear the false 'Below threshold' label on passed candidates.
    rq = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            func.lower(func.trim(func.coalesce(CandidateApplication.pre_screen_recommendation, "")))
            == "below threshold",
        )
    )
    if organization_id is not None:
        rq = rq.filter(CandidateApplication.organization_id == int(organization_id))
    threshold_cache: dict[int, float | None] = {}
    recs_fixed = 0
    for app, role in rq.all():
        if not _pre_screen_passed(app):
            continue
        if role.id not in threshold_cache:
            threshold_cache[role.id] = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
        ev = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        llm = ev.get("llm_score_100")
        new_label = (
            pre_screen_recommendation_label(float(llm), threshold_cache[role.id])
            if llm is not None
            else None
        )
        # A passed candidate must never carry "Below threshold".
        if not new_label or new_label == "Below threshold":
            new_label = "Manual review recommended"
        if not dry_run:
            app.pre_screen_recommendation = new_label
        recs_fixed += 1

    if (cards_discarded or recs_fixed) and not dry_run:
        db.commit()
    return {"cards_discarded": cards_discarded, "recs_fixed": recs_fixed}


def backfill_normalize_raw_recommendation_labels(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """Replace raw cv_match recommendation enums ('no'/'lean_no'/'yes'/
    'strong_yes') that leaked into ``pre_screen_recommendation`` (a display
    field) with proper recruiter-facing labels. The leak came from the
    snapshot fallback to ``cv_match_details.recommendation``; that path now
    normalizes too, so this just catches up existing rows.

    Returns ``{"updated": int, "scanned": int}``.
    """
    from sqlalchemy import func

    from .pre_screening_snapshot import normalize_recommendation_label

    q = db.query(CandidateApplication).filter(
        CandidateApplication.deleted_at.is_(None),
        func.lower(func.trim(func.coalesce(CandidateApplication.pre_screen_recommendation, ""))).in_(
            ["strong_yes", "yes", "lean_no", "no"]
        ),
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    updated = 0
    scanned = 0
    for app in q.all():
        scanned += 1
        new_label = normalize_recommendation_label(app.pre_screen_recommendation)
        if new_label and new_label != app.pre_screen_recommendation:
            if not dry_run:
                app.pre_screen_recommendation = new_label
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
