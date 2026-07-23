"""Insert a queued ``AgentDecision`` for recruiter approval.

Called only by the agent (via MCP tool). High-stakes decisions —
``advance_to_interview``, ``reject``, ``skip_assessment_reject`` — never
auto-execute; they queue here and surface in the recruiter's pending
panel for one-click approve or override.

Idempotency key ``{run_id}:{application_id}:{decision_type}`` prevents
the agent re-queuing the same decision on retry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.agent_decision import AGENT_DECISION_TYPES, AgentDecision
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import Role
from ..services.decision_input_fingerprint import (
    capture_input_fingerprint as _capture_input_fingerprint,
)
from ..services.agent_decision_admission import (
    latest_active_decision,
    lock_decision_application,
)
from ..services.logical_role_application_authority import (
    LogicalRoleApplicationAuthorizationError,
    authorize_logical_role_application,
)
from .types import ACTOR_AGENT, Actor

if TYPE_CHECKING:
    from ..components.scoring.freshness import ScoreGenerationToken


def _capture_token_spend(
    db: Session, *, agent_run_id: int | None
) -> dict:
    """Discipline §8.5: roll up usage_events for this agent_run_id.

    Defers to ``token_spend_aggregator.aggregate`` which returns an
    empty dict on any failure or when no events match.
    """
    try:
        from ..agent_runtime import token_spend_aggregator
        return token_spend_aggregator.aggregate(db, agent_run_id=agent_run_id)
    except Exception:
        return {}


def _compute_dedup_key(
    db: Session,
    *,
    role_id: int,
    application_id: int,
    decision_type: str,
    evidence: dict[str, Any] | None = None,
    policy_generation: dict[str, Any] | None = None,
) -> str | None:
    """C4: build the cross-cycle dedup key for this would-be decision.

    Hash inputs that meaningfully change the verdict:
    ``application_id`` (which candidate),
    ``decision_type`` (advance vs reject vs send_assessment),
    ``criteria_fingerprint`` (role criteria revision),
    ``cv_fingerprint`` (which CV),
    ``pre_screen_bucket`` (5-pt bucket of pre-screen score),
    ``cv_match_bucket`` (5-pt bucket of cv-match score).

    Bucketing the scores by 5 points means trivial re-scoring noise
    (e.g. prompt-seed jitter) doesn't break dedup; a 5-pt swing in
    either direction (the same threshold the staleness service uses)
    does break it, which is what we want — material input change =>
    fresh decision allowed.

    Returns None on any error so the caller falls through to the
    existing pending/discarded guards.
    """
    import hashlib
    try:
        from ..models.candidate_application import CandidateApplication
        from ..services.decision_staleness import criteria_content_fingerprint

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .one_or_none()
        )
        if app is None:
            return None

        # Content-only criteria fingerprint (shared with the staleness
        # recompute). Excludes volatile row ids so re-deriving identical
        # criteria doesn't churn the dedup key. None => no criteria; "" keeps
        # the composite stable.
        criteria_fp = criteria_content_fingerprint(db, int(role_id)) or ""

        cv_text = (app.cv_text or "").strip()
        cv_fp = (
            hashlib.sha256(cv_text.encode("utf-8")).hexdigest()
            if cv_text else ""
        )

        def _bucket(value):
            try:
                if value is None:
                    return ""
                return str(int(float(value) // 5) * 5)
            except (TypeError, ValueError):
                return ""

        from ..services.related_role_application_runtime import (
            related_role_evaluation_for_application,
        )

        related_evaluation = related_role_evaluation_for_application(
            db,
            role_id=int(role_id),
            application=app,
        )
        cross_role = related_evaluation is not None
        role_score = getattr(app, "cv_match_score", None)
        pre_screen_dimension = _bucket(getattr(app, "pre_screen_score_100", None))
        if cross_role:
            role_score = getattr(related_evaluation, "role_fit_score", None)
            frozen = evidence if isinstance(evidence, dict) else {}
            role_score = next(
                (
                    value
                    for value in (
                        frozen.get("taali_score"),
                        frozen.get("assessment_score"),
                        frozen.get("role_fit_score"),
                        role_score,
                    )
                    if value is not None
                ),
                None,
            )
            # Related roles do not consume the ATS owner's pre-screen score.
            # Their decision boundary is the role-owned effective threshold.
            threshold = frozen.get("effective_threshold")
            pre_screen_dimension = (
                f"threshold:{float(threshold):g}"
                if threshold is not None
                else ""
            )
            evaluation_cv_fp = (
                frozen.get("evaluation_cv_fingerprint")
                or getattr(related_evaluation, "cv_fingerprint", None)
            )
            if evaluation_cv_fp:
                cv_fp = str(evaluation_cv_fp)

        parts = [
            str(role_id),
            str(application_id),
            decision_type,
            criteria_fp,
            cv_fp,
            pre_screen_dimension,
            _bucket(role_score),
        ]
        if isinstance(policy_generation, dict):
            import json

            parts.append(
                json.dumps(
                    policy_generation,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        composite = "|".join(parts)
        return hashlib.sha256(composite.encode("utf-8")).hexdigest()
    except Exception:
        import logging
        logging.getLogger("taali.actions.queue_decision").warning(
            "dedup_key compute failed for app=%s type=%s",
            application_id, decision_type, exc_info=True,
        )
        return None


def _capture_active_capabilities(
    db: Session,
    *,
    organization_id: int,
    decision_id: str,
    role_id: int | None,
) -> dict[str, bool]:
    """Snapshot every registered v10 capability for this decision.

    Captured at the moment the decision is queued — the resulting dict
    is what the audit query later relies on to reconstruct the runtime
    state. Failures here NEVER block decision queueing; an empty dict
    is the safe-degrade ("treat as v1/v2 era").
    """
    try:
        from ..capabilities import ALL_CAPABILITIES, get_shared
        return get_shared().snapshot(
            ALL_CAPABILITIES,
            db=db,
            organization_id=organization_id,
            decision_id=decision_id,
            role_id=role_id,
        )
    except Exception:
        return {}


def _human_suppressed(
    db: Session, *, role_id: int, application_id: int, decision_type: str
) -> AgentDecision | None:
    """BUG-1: honour an explicit human "no" until the inputs change.

    When a recruiter discards or overrides a decision, re-queuing the same
    verdict next cycle silently overrides that signal. Suppress a same-type
    re-emit while a discarded/overridden decision exists whose cited inputs
    have NOT materially changed (per the staleness service). A material
    change — new score, new assessment, new CV, edited criteria, a recruiter
    note — releases the suppression so the agent can re-decide on fresh
    information.

    ONLY an explicit HUMAN no suppresses (``resolved_by_user_id`` set). SYSTEM
    discards — the re-score supersede (``_supersede_decisions_for_rescore``,
    "candidate_data_changed") and the threshold reconcile — leave
    ``resolved_by_user_id`` NULL precisely BECAUSE they expect the agent to
    re-decide. Treating those as a human "no" stranded re-scored candidates:
    a re-score discards the pending decision, then (with verdict-aware
    staleness, #615) a held verdict reads as "not stale" so the suppression
    never releases and the card is never re-created. Filtering to human-resolved
    rows fixes that — the system discard no longer blocks its own re-decision.

    Returns the suppressing decision (caller dedups to it) or None when a
    fresh emit is allowed.
    """
    suppressing = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role_id),
            AgentDecision.application_id == application_id,
            AgentDecision.decision_type == decision_type,
            AgentDecision.status.in_(("discarded", "overridden")),
            AgentDecision.resolved_by_user_id.isnot(None),
        )
        .order_by(
            AgentDecision.resolved_at.desc().nullslast(),
            AgentDecision.id.desc(),
        )
        .first()
    )
    if suppressing is None:
        return None

    # Fingerprinted rows (the only kind new decisions produce): suppress
    # until the staleness service reports the cited inputs have drifted.
    if suppressing.input_fingerprint or {}:
        try:
            from ..models.candidate_application import CandidateApplication

            application = db.get(CandidateApplication, int(application_id))
            role = db.get(Role, int(role_id))
            if application is not None and role is not None:
                from ..services.decision_role_context import (
                    is_cross_role_decision,
                    load_related_evaluation,
                    related_decision_staleness,
                )
                from ..services.logical_role_batch_operations import (
                    is_related_role,
                )

                # Physical application ownership is not the logical-role
                # boundary. Use the shared decision classifier plus the
                # rolling-compatible role identity rule: a direct application
                # may physically belong to the related role, and mixed-version
                # rows may carry only ``ats_owner_role_id``.
                if is_related_role(role) or is_cross_role_decision(
                    suppressing,
                    application,
                ):
                    evaluation = load_related_evaluation(
                        db,
                        decision=suppressing,
                        application=application,
                    )
                    if evaluation is None:
                        # A removed membership cannot be reclassified as an
                        # ordinary application merely because its physical row
                        # points at this related role. Queue admission rejects
                        # it; preserve the recruiter's "no" if this helper is
                        # reached while membership state is unavailable.
                        return suppressing
                    report = related_decision_staleness(
                        db,
                        suppressing,
                        evaluation,
                        application=application,
                        role=role,
                    )
                    return suppressing if not report.is_stale else None

            from ..services.decision_staleness import is_human_suppression_live

            return (
                suppressing
                if is_human_suppression_live(
                    db, suppressing, application=application
                )
                else None
            )
        except Exception:
            import logging
            logging.getLogger("taali.actions.queue_decision").warning(
                "discard-suppression staleness check failed app=%s type=%s",
                application_id, decision_type, exc_info=True,
            )
            # Fail safe toward honouring the human "no".
            return suppressing

    # Pre-A1 rows have no fingerprint baseline to compare against — fall back
    # to the original short cooldown so a months-old discard can't suppress
    # forever.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    floor = _dt.now(_tz.utc) - _td(minutes=10)
    resolved_at = suppressing.resolved_at
    if resolved_at is not None and resolved_at >= floor:
        return suppressing
    return None


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    decision_type: str,
    reasoning: str,
    evidence: Optional[dict[str, Any]] = None,
    confidence: Optional[float] = None,
    model_version: str,
    prompt_version: str,
    recommendation: Optional[str] = None,
    idempotency_key_suffix: Optional[str] = None,
    skip_episode: bool = False,
    expected_score_generation: ScoreGenerationToken | None = None,
) -> AgentDecision:
    if actor.type != ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="queue_decision is agent-only; recruiters take direct actions.",
        )
    if decision_type not in AGENT_DECISION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown decision_type={decision_type!r}",
        )
    if actor.agent_run_id is None:
        raise HTTPException(status_code=422, detail="agent actor missing agent_run_id")

    # Resolve the logical role before applying application-row lifecycle
    # rules. An ordinary role's membership is its live CandidateApplication,
    # while a related role's membership is its live SisterRoleEvaluation;
    # deleting that role's source/evidence row must not make a still-active
    # related candidate visible-but-unactionable.
    acting_role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if acting_role is None:
        raise HTTPException(status_code=422, detail="role is unavailable")
    try:
        logical_context = authorize_logical_role_application(
            db,
            role=acting_role,
            application_id=int(application_id),
        )
    except LogicalRoleApplicationAuthorizationError as exc:
        raise HTTPException(
            status_code=404,
            detail="Application not found",
        ) from exc
    app = logical_context.source_application
    cross_role = logical_context.is_related

    from ..services.related_role_application_runtime import (
        related_role_evaluation_for_application,
        role_application_is_resolved,
    )

    # Related-role queue admission must serialize with role-local stage and
    # outcome transitions. Those transitions lock Application -> membership
    # and discard any pending decisions when the role becomes terminal. If the
    # queue path merely reads the membership, it can observe ``open``, lose the
    # race to a terminal transition, and then insert a new pending decision
    # after that transition's discard query has already run. Establish the
    # platform lock order first, then hold both role-local state rows through
    # the decision insert. The two possible outcomes are now deterministic:
    # queue-first is discarded by the later transition; transition-first is
    # observed here and refused.
    locked_role = None
    related_evaluation = None
    if cross_role:
        from ..services.role_execution_guard import lock_live_role

        locked_role = lock_live_role(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
        )
        if locked_role is None:
            raise HTTPException(status_code=422, detail="role is unavailable")

        from ..models.candidate_application import CandidateApplication

        locked_app_query = db.query(CandidateApplication).filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            locked_app_query = locked_app_query.with_for_update()
        locked_app = locked_app_query.populate_existing().one_or_none()
        if locked_app is None:
            raise HTTPException(status_code=422, detail="application is unavailable")
        app = locked_app

        from ..services.related_role_action_service import (
            lock_related_role_membership,
        )

        locked_membership = lock_related_role_membership(
            db,
            application=app,
            acting_role_id=int(role_id),
        )
        if locked_membership is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"application {application_id} does not belong to role "
                    f"{role_id}"
                ),
            )
        _membership_role, related_evaluation = locked_membership

    # Serialize score-backed owner-role decisions with recruiter RoleIntent
    # edits. Queue-first means the later edit sees and discards this pending
    # row; edit-first means this lock waits, then the stale latest score job
    # refuses the old verdict. Related-role decisions use their independent
    # SisterRoleEvaluation lifecycle and are deliberately excluded.
    from ..components.scoring.freshness import (
        SCORE_BACKED_STANDARD_DECISION_TYPES,
        score_generation_is_current,
        standard_owner_score_guard_applies,
    )

    if not cross_role and decision_type in SCORE_BACKED_STANDARD_DECISION_TYPES:
        # Keep the platform-wide Organization -> Role order. Fresh cards may
        # immediately auto-execute in this same transaction, and that action
        # boundary takes both locks; a Role-only queue lock would invert the
        # order against scoring/provider admission. Workable progress commits
        # release any earlier DB Role lock, so every caller safely re-acquires
        # the canonical pair here rather than passing unverifiable lock state.
        from ..services.role_execution_guard import lock_live_role

        locked_role = lock_live_role(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
        )
        if locked_role is None:
            raise HTTPException(status_code=422, detail="role is unavailable")
        from ..models.candidate_application import CandidateApplication

        locked_app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.role_id == int(role_id),
            )
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if locked_app is None:
            raise HTTPException(status_code=422, detail="application is unavailable")
        app = locked_app
        if (
            standard_owner_score_guard_applies(
                application_role_id=int(app.role_id),
                decision_role_id=int(role_id),
                role_kind=getattr(locked_role, "role_kind", None),
                decision_type=decision_type,
            )
            and not score_generation_is_current(
                db,
                expected=expected_score_generation,
                locked_role=locked_role,
                application=app,
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "candidate score refresh is pending; refusing to queue a "
                    "decision from superseded scores"
                ),
            )

    # Every decision type shares the same logical role/candidate queue slot.
    # Lock the supplied application as the current producer's serialization
    # point; the database candidate-keyed partial unique index also closes races
    # between owner and direct physical applications for the same subject.
    locked_subject = lock_decision_application(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
    )
    if locked_subject is None:
        raise HTTPException(status_code=422, detail="application is unavailable")
    app = locked_subject
    # Close the read/lock race for ordinary roles. Related roles deliberately
    # remain actionable while their explicit membership is live even if this
    # evidence row is soft-deleted; their membership lock above is authoritative.
    if not cross_role and app.deleted_at is not None:
        raise HTTPException(status_code=422, detail="application is unavailable")

    from ..services.decision_policy_generation import (
        validate_queue_policy_generation,
    )

    policy_generation = validate_queue_policy_generation(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
        evidence=evidence,
        locked_role=locked_role,
    )

    # One summary, one shape — regardless of producer. queue_decision is the
    # single funnel BOTH the LLM agent and the deterministic bulk pass call, so
    # this is the one place that guarantees every card carries a real,
    # recruiter-facing reasoning. When the producer didn't supply one (the LLM
    # agent frequently omits it on send_assessment, leaving a generic
    # placeholder), derive it from the candidate's cv_match analysis — the same
    # source the bulk pass uses — and only fall back to the audit-oriented
    # policy basis if that is empty too.
    if not (reasoning or "").strip() and cross_role:
        evaluation = related_evaluation or related_role_evaluation_for_application(
            db, role_id=int(role_id), application=app
        )
        reasoning = (
            str(getattr(evaluation, "summary", None) or "").strip()
            if evaluation is not None
            else ""
        )
    if not (reasoning or "").strip() and not cross_role:
        from ..services.decision_reasoning import recruiter_decision_reasoning
        reasoning = (recruiter_decision_reasoning(app) or "").strip()
    if not (reasoning or "").strip():
        reasoning = (
            "Recommended by the decision policy from the candidate's "
            "role-fit score and stage."
        )
    # A6: terminal-state invariant. Resolved applications (rejected,
    # hired, advanced) are frozen forever — the agent must not queue,
    # modify, or re-evaluate decisions for them. This refuses cleanly
    # rather than silently no-opping so the orchestrator sees the
    # mistake in its error path and stops looping.
    if role_application_is_resolved(
        db,
        role_id=int(role_id),
        application=app,
    ):
        import logging
        evaluation = (
            related_evaluation
            or related_role_evaluation_for_application(
                db, role_id=int(role_id), application=app
            )
            if cross_role
            else None
        )
        local_stage = getattr(evaluation, "pipeline_stage", app.pipeline_stage)
        local_outcome = getattr(
            evaluation,
            "application_outcome",
            app.application_outcome,
        )
        logging.getLogger("taali.actions.queue_decision").info(
            "resolved_app_skipped action=queue_decision application_id=%s "
            "pipeline_stage=%s application_outcome=%s decision_type=%s",
            application_id, local_stage, local_outcome, decision_type,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"application {application_id} is resolved "
                f"(pipeline_stage={local_stage!r}, "
                f"application_outcome={local_outcome!r}); "
                "refusing to queue decision"
            ),
        )

    # One pending decision per application at a time. The cohort planner
    # already filters apps-with-pending out of ``find_apps_in_state`` for
    # the triage states, but the agent can still call queue tools with an
    # arbitrary application_id (e.g. via ``get_application`` or a memory of
    # an id from an earlier cycle). When that happens we'd otherwise stack
    # multiple pendings on the same candidate — recruiter sees the same
    # person twice in the queue (e.g. "advance" + "send_assessment" both
    # waiting). Existing pending wins; return it so the caller treats this
    # as a dedup, not a new emit.
    # Count every live queue state. A taught ``reverted_for_feedback`` card is
    # still actionable, while ``processing`` represents an approved action
    # whose provider writeback is in flight (or stuck after a failed dispatch).
    # Either must block a duplicate recommendation for this candidate.
    existing_pending = latest_active_decision(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
        application_id=int(application_id),
    )
    if existing_pending is not None:
        existing_pending._just_created = False  # type: ignore[attr-defined]
        return existing_pending

    # C3 (BUG-1): honour discard/override as an explicit human "no" until
    # the candidate's inputs materially change. The old guard was a flat
    # 10-minute cooldown that only covered ``discarded`` — a later cohort tick
    # could re-queue
    # the same verdict the recruiter had just rejected, silently overriding
    # the human signal. Now we suppress a same-type re-emit while a
    # discarded/overridden decision exists whose cited inputs are unchanged,
    # releasing only when a new score / assessment / CV / criteria edit /
    # recruiter note makes a fresh verdict legitimate.
    human_suppressed = _human_suppressed(
        db,
        role_id=int(role_id),
        application_id=application_id,
        decision_type=decision_type,
    )
    if human_suppressed is not None:
        human_suppressed._just_created = False  # type: ignore[attr-defined]
        return human_suppressed

    # C4: cross-cycle dedup. If a decision with the same dedup_key
    # (same inputs, same decision type) was approved in the last 7 days,
    # dedup. We pre-fetch the application+role once so the fingerprint and
    # dedup_key compute share the same baseline state.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    dedup_key = _compute_dedup_key(
        db,
        role_id=int(role_id),
        application_id=application_id,
        decision_type=decision_type,
        evidence=evidence,
        policy_generation=(
            policy_generation.as_fingerprint()
            if policy_generation is not None
            else None
        ),
    )
    if dedup_key:
        approved_window_floor = _dt.now(_tz.utc) - _td(days=7)
        prior_approved = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.role_id == int(role_id),
                AgentDecision.application_id == application_id,
                AgentDecision.decision_type == decision_type,
                AgentDecision.decision_dedup_key == dedup_key,
                AgentDecision.status == "approved",
                AgentDecision.resolved_at >= approved_window_floor,
            )
            .order_by(AgentDecision.resolved_at.desc())
            .first()
        )
        if prior_approved is not None:
            prior_approved._just_created = False  # type: ignore[attr-defined]
            return prior_approved

    # Optional suffix lets the caller scope the key to a sub-identity
    # below (application_id, decision_type) — used by resend_assessment_invite
    # to keep separate approvals for separate assessments on the same
    # application from colliding (Codex #176).
    base_key = f"{actor.agent_run_id}:{application_id}:{decision_type}"
    idempotency_key = f"{base_key}:{idempotency_key_suffix}" if idempotency_key_suffix else base_key
    active_capabilities = _capture_active_capabilities(
        db,
        organization_id=organization_id,
        decision_id=idempotency_key,
        role_id=role_id,
    )
    # Discipline §8.5: roll up usage_events for this agent_run_id into
    # a single token_spend JSON blob on the decision row. Empty dict on
    # any failure — never blocks the queue.
    token_spend = _capture_token_spend(db, agent_run_id=actor.agent_run_id)
    # A1: snapshot the inputs the decision cited. Drives staleness
    # detection (A2) for pending decisions and forms the immutable
    # audit record for resolved decisions.
    input_fingerprint, criteria_fp, cv_fp = _capture_input_fingerprint(
        db,
        application_id=application_id,
        role_id=role_id,
        evidence=evidence,
        score_generation=expected_score_generation,
    )
    if policy_generation is not None:
        input_fingerprint["decision_policy_generation"] = (
            policy_generation.as_fingerprint()
        )

    decision = AgentDecision(
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        candidate_id=int(app.candidate_id),
        agent_run_id=actor.agent_run_id,
        decision_type=decision_type,
        recommendation=recommendation or decision_type,
        status="pending",
        reasoning=reasoning.strip(),
        evidence=evidence,
        confidence=confidence,
        model_version=model_version,
        prompt_version=prompt_version,
        idempotency_key=idempotency_key,
        active_capabilities=active_capabilities,
        token_spend=token_spend,
        input_fingerprint=input_fingerprint,
        criteria_fingerprint=criteria_fp,
        cv_fingerprint=cv_fp,
        decision_dedup_key=dedup_key,
    )
    # Scope the insert in a savepoint so an idempotency-key collision rolls
    # back only this nested transaction, not the whole session — a full
    # ``db.rollback()`` here would abort the outer agent-cycle transaction
    # and discard every prior write in the cycle. (Codex #42)
    nested = db.begin_nested()
    try:
        db.add(decision)
        db.flush()
        nested.commit()
    except IntegrityError:
        nested.rollback()
        # The database invariant may have been won by a different decision
        # type/idempotency key, so recover by the canonical logical subject
        # before falling back to a same-request replay.
        existing = latest_active_decision(
            db,
            organization_id=int(organization_id),
            role_id=int(role_id),
            application_id=int(application_id),
        )
        if existing is None:
            existing = (
                db.query(AgentDecision)
                .filter(AgentDecision.idempotency_key == idempotency_key)
                .first()
            )
        if existing is not None:
            # Surface the dedup-existing branch on the returned object so
            # callers can decide whether to count it against the per-cycle
            # decision budget. Tracking this here (rather than via a
            # tuple-return API change) keeps the dozens of test callers
            # working unchanged. (Codex #179)
            existing._just_created = False  # type: ignore[attr-defined]
            return existing
        raise

    decision._just_created = True  # type: ignore[attr-defined]

    # Phase 2 §6.7: one consolidated Graphiti episode per decision.
    # Folds the four sub-agent scores into the decision body so we get
    # one LLM extraction pass per decision instead of one per score —
    # keeps Graphiti billing bounded to the decision volume. Failure
    # is logged and ignored; the Postgres row is the source of truth.
    # ``skip_episode`` is set by the deterministic bulk-decision pass:
    # it can emit hundreds of decisions in one go, and a per-decision
    # Graphiti LLM extraction would blow the cost + Celery time budget.
    # Threshold-application decisions aren't useful learning signal, and
    # Postgres remains the source of truth, so the episode is skipped.
    if not skip_episode:
        _emit_decision_episode_safe(db, decision=decision)

    # CandidateApplicationEvent so the per-role /agent/status endpoint's
    # ``last_activity`` reflects this decision the moment it's queued
    # — that's what the AgentBar tick reads. The Graphiti listener has
    # ``agent_decision_queued`` in its _NOISE_EVENT_TYPES set so this
    # doesn't double-ingest alongside _emit_decision_episode_safe above.
    db.add(
        CandidateApplicationEvent(
            application_id=application_id,
            organization_id=organization_id,
            role_id=int(role_id),
            agent_decision_id=int(decision.id),
            event_type="agent_decision_queued",
            actor_type="agent",
            actor_id=actor.agent_run_id,
            reason=f"Queued {decision_type.replace('_', ' ')}",
            idempotency_key=f"agent_decision_queued:{decision.id}",
            event_metadata={"decision_id": int(decision.id), "decision_type": decision_type},
        )
    )
    return decision


def _emit_decision_episode_safe(db: Session, *, decision: AgentDecision) -> None:
    """Durably enqueue the consolidated decision episode. Never raises.

    Looks up candidate + role context inline so the orchestrator caller
    doesn't have to thread them through, then writes a
    ``graph_episode_outbox`` row in the caller's transaction instead of
    dispatching to Graphiti inline. A Celery drain task ships it with
    retry, so a graph outage no longer silently drops the episode. See
    candidate_graph.episode_outbox.
    """
    try:
        from ..candidate_graph import episode_outbox
        from ..models.candidate import Candidate
        from ..models.candidate_application import CandidateApplication

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == decision.application_id)
            .one_or_none()
        )
        if app is None:
            return
        candidate = (
            db.query(Candidate).filter(Candidate.id == app.candidate_id).one_or_none()
        )
        full_name = candidate.full_name if candidate is not None else None
        candidate_id = int(candidate.id) if candidate is not None else int(app.candidate_id)
        # Serialise the decision's feature vector into the episode so the
        # nightly policy fitter can recover graph-side training examples;
        # without this the graph collector has no features and silently
        # falls back to weaker Postgres labels.
        from ..decision_policy.nightly_policy_fit import _features_for_decision

        episode_outbox.enqueue_decision(
            db,
            organization_id=int(decision.organization_id),
            candidate_full_name=full_name,
            candidate_taali_id=candidate_id,
            application_id=int(decision.application_id),
            role_id=int(decision.role_id),
            decision_id=int(decision.id),
            recommended_action=str(decision.recommendation),
            confidence=float(decision.confidence or 0.0),
            policy_revision_id=None,
            reasoning=str(decision.reasoning or ""),
            created_at=decision.created_at or _now(),
            features_json=_features_for_decision(decision) or None,
        )
    except Exception:
        import logging
        logging.getLogger("taali.actions.queue_decision").warning(
            "decision episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
            exc_info=False,
        )


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
