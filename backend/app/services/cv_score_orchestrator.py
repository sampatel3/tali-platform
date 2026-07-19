"""Async + cached CV scoring orchestration.

Replaces the synchronous in-request Claude calls in
``applications_routes._compute_cv_match_for_application``. The flow is:

  enqueue_score(application)
      → creates a CvScoreJob row in `pending`
      → dispatches the Celery scoring task (eager mode in tests)

  score_application_job(application_id)  [Celery task]
      → computes cache_key from (cv_text, normalized_spec, criteria, prompt_version, model)
      → on cache hit: copies result from CvScoreCache, marks job done, no Claude call
      → on cache miss: calls Claude (v4 if criteria, v3 fallback), stores result in cache, marks job done
      → on error: stores a stable public failure code on the job and application

Cache invalidation is implicit: changing criteria, the spec, the prompt
version, or the model produces a different cache_key, so the next score
yields a cache miss and a fresh result. There is no explicit invalidation
sweep — cache rows are immutable and accumulate (acceptable for now; a TTL
or LRU eviction can be bolted on later if storage becomes a concern).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import (
    CvScoreJob,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
    SCORE_JOB_STALE,
)
from ..models.role import Role
from ..models.organization import Organization
from ..platform.config import settings
from ..domains.assessments_runtime.pipeline_service import append_application_event
from .claude_client_resolver import get_client_for_org as _resolve_anthropic_client
from .pricing_service import Feature, estimate_reservation
from .usage_metering_service import (
    InsufficientCreditsError,
    record_event as _meter_record_event,
    reserve as _meter_reserve,
)
from .usage_credit_reservations import (
    InsufficientRoleBudgetError,
    ensure_role_capacity,
)
from .cv_score_cache import (
    _V3_PROMPT_VERSION as _V3_PROMPT_VERSION,
    _criteria_payload as _criteria_payload,
    compute_cache_key as compute_cache_key,
    get_cached_result as get_cached_result,
    store_cached_result as store_cached_result,
)
from .cv_score_integrity import (
    _augment_integrity_signals as _augment_integrity_signals,
)
from .provider_error_evidence import public_scoring_failure_code

logger = logging.getLogger("taali.cv_score_orchestrator")


class AutonomousScoringDeferred(RuntimeError):
    """Stop an autonomous score before its next paid provider phase.

    A workspace pause cannot cancel a request that the provider has already
    accepted.  It must, however, prevent every *subsequent* request in the
    multi-phase scoring pipeline.  The worker treats this exception as a
    durable stale/deferred attempt and rolls back any tentative outputs from
    earlier phases.
    """

    def __init__(self, *, phase: str, detail: str) -> None:
        super().__init__(detail)
        self.phase = str(phase)
        self.detail = str(detail)


def _authorize_autonomous_scoring_phase(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
    phase: str,
) -> None:
    """Re-read workspace authority immediately before a provider phase.

    Do not lock the Organization row here.  Holding that lock across a remote
    request would prevent Pause from committing until the entire scoring
    pipeline finished, defeating the between-phase fence.  PostgreSQL's
    READ COMMITTED isolation gives each explicit query the latest committed
    overlay; any already-in-flight request may finish, while this check stops
    the next one.  Recruiter-requested jobs deliberately bypass the autonomous
    overlay via ``requires_active_agent=False``.
    """

    from .score_dispatch_authority import require_score_phase_authority

    require_score_phase_authority(db, application=application, job=job, phase=phase)
    if not bool(getattr(job, "requires_active_agent", True)):
        return
    organization_id = getattr(application, "organization_id", None)
    if organization_id is None:
        raise AutonomousScoringDeferred(
            phase=phase,
            detail="role is unavailable",
        )

    role_id = getattr(application, "role_id", None) or getattr(job, "role_id", None)
    if role_id is None:
        raise AutonomousScoringDeferred(
            phase=phase,
            detail="role is unavailable",
        )
    # One joined SELECT gives all workspace + role controls the same live
    # READ COMMITTED snapshot.  Separate reads could observe a workspace state,
    # then miss a role pause that commits between the statements.  Suppress
    # autoflush so this authority read never publishes tentative outputs from
    # an earlier scoring phase.
    with db.no_autoflush:
        live_control = (
            db.query(
                Organization.agent_workspace_paused_at,
                Role.agentic_mode_enabled,
                Role.agent_paused_at,
            )
            .join(Role, Role.organization_id == Organization.id)
            .filter(
                Organization.id == int(organization_id),
                Role.id == int(role_id),
                Role.organization_id == int(organization_id),
                Role.deleted_at.is_(None),
            )
            .one_or_none()
        )
    if live_control is None:
        raise AutonomousScoringDeferred(
            phase=phase,
            detail="role is unavailable",
        )
    if not bool(live_control.agentic_mode_enabled):
        raise AutonomousScoringDeferred(
            phase=phase,
            detail="role agent is disabled",
        )
    if live_control.agent_workspace_paused_at is not None:
        raise AutonomousScoringDeferred(
            phase=phase,
            detail="workspace agent is paused",
        )
    if live_control.agent_paused_at is not None:
        raise AutonomousScoringDeferred(
            phase=phase,
            detail="role agent is paused",
        )


def _latest_job(db: Session, application_id: int) -> CvScoreJob | None:
    return (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == application_id)
        .order_by(desc(CvScoreJob.queued_at), desc(CvScoreJob.id))
        .first()
    )


def latest_score_status(db: Session, application_id: int) -> str | None:
    """Latest job status for an application, or ``None`` if never scored.

    Used by the listing endpoint to surface ``score_status`` alongside the
    persisted ``cv_match_score`` so the UI knows whether to show a spinner.
    """
    job = _latest_job(db, application_id)
    return job.status if job else None


def rescore_wrongly_filtered_prescreen(
    db: Session, *, organization_id: int | None = None, dry_run: bool = False
) -> dict:
    """Re-score apps the pre-screen gate WRONGLY filtered.

    Before the gate read the genuine pre-screen evidence, it gated on the
    shared ``pre_screen_score_100`` column — which a prior cv_match run could
    have overwritten — and so skipped full scoring for candidates the
    pre-screen actually passed (decision 'yes', llm >= threshold). They were
    marked pre-screen-filtered (``cv_match_score`` NULL, ``cv_match_scored_at``
    set). Re-enqueue them so the corrected gate full-scores them.

    Scopes to filtered apps (``cv_match_score`` NULL + ``cv_match_scored_at``
    set), then keeps only the non-fraud, evidence-passed ones. Returns
    ``{"rescored": int, "scanned": int}``.
    """
    threshold = float(settings.PRE_SCREEN_THRESHOLD)
    q = db.query(CandidateApplication).filter(
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.application_outcome == "open",
        CandidateApplication.cv_match_score.is_(None),
        CandidateApplication.cv_match_scored_at.isnot(None),
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    rescored = 0
    scanned = 0
    for app in q.all():
        details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
        if not details.get("pre_screen_decision"):
            continue  # not a pre-screen-filter record
        ev = (
            app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        )
        if ev.get("fraud_capped"):
            continue  # fraud → correctly filtered
        llm = ev.get("llm_score_100")
        if llm is None or float(llm) < threshold:
            continue  # genuinely below threshold → correctly filtered
        scanned += 1
        if dry_run:
            rescored += 1
            continue
        if enqueue_score(db, app, force=True) is not None:
            rescored += 1
    if not dry_run:
        db.commit()
    return {"rescored": rescored, "scanned": scanned}


def enqueue_score(
    db: Session,
    application: CandidateApplication,
    *,
    force: bool = False,
    bypass_pre_screen: bool = False,
    requires_active_agent: bool = False,
    batch_run_id: int | None = None,
    batch_delivery_id: str | None = None,
) -> CvScoreJob | None:
    """Queue a CV score for an application.

    Returns the new job, or the existing active job if one is already
    pending/running and ``force`` is False. Returns ``None`` when the
    application can't be scored (no CV, no spec, no API key).

    ``force`` bypasses duplicate detection, not pre-screen. The separate
    ``bypass_pre_screen`` flag is recruiter authority to skip that gate.
    ``batch_run_id`` binds newly-created work to one durable recruiter batch;
    ``batch_delivery_id`` fences an expired fan-out worker. A reused active
    attempt keeps its original ownership.
    """
    if not application or application.id is None:
        return None
    role = application.role
    if not (application.cv_text or "").strip():
        return None
    if not role or not (role.job_spec_text or "").strip():
        return None
    if not settings.ANTHROPIC_API_KEY:
        return None

    # A6: resolved applications are frozen — never spend on scoring them
    # again. Catches the straggler case where a Workable webhook arrives
    # after a manual reject, or a batch rescore loop iterates past the
    # rejected/advanced filter. Zero-cost early return.
    from ..domains.assessments_runtime.role_support import is_resolved as _is_resolved

    if _is_resolved(application):
        logger.info(
            "resolved_app_skipped action=enqueue_score application_id=%s "
            "pipeline_stage=%s application_outcome=%s",
            application.id,
            application.pipeline_stage,
            application.application_outcome,
        )
        return None

    # Workable-disqualified candidates are out of the recruiter's funnel — never
    # spend a score on them (it'd only produce a reject). The Workable sync sets
    # this flag; scoring historically ignored it and burned credits on thousands
    # of already-disqualified candidates.
    if getattr(application, "workable_disqualified", False):
        logger.info(
            "disqualified_app_skipped action=enqueue_score application_id=%s",
            application.id,
        )
        return None

    # Pre-screen integrity guard (flag-gated). A bypass is only safe for
    # REFRESHING the score of a candidate that genuinely PASSED pre-screen.
    # For never-screened, stale, or below-threshold candidates, do NOT bypass —
    # route through the gate so the cheap pre-screen runs (and filters) before
    # the expensive holistic score. Without this, bulk / engine-migration
    # re-scores (bypass_pre_screen=True) paid for full holistic scores on
    # candidates the gate would have filtered (2026-06 cost audit: ~56% of the
    # score line went to fail / never-pre-screened candidates).
    if bypass_pre_screen and settings.PRE_SCREEN_GATE_GUARD_RESCORE:
        from .pre_screening_service import application_needs_pre_screen

        genuine = getattr(application, "genuine_pre_screen_score_100", None)
        if (
            application_needs_pre_screen(application)
            or genuine is None
            or genuine < int(settings.PRE_SCREEN_THRESHOLD)
        ):
            logger.info(
                "pre_screen_guard: not bypassing pre-screen application_id=%s "
                "genuine=%s threshold=%s",
                application.id,
                genuine,
                settings.PRE_SCREEN_THRESHOLD,
            )
            bypass_pre_screen = False

    # Provider-job admission.  Lock order is org -> role, matching the hard
    # reservation path used by assessment/task calls.  The role lock makes
    # the active CvScoreJob rows a durable, serialized budget commitment:
    # concurrent public applications cannot all observe the same remaining
    # cap and enqueue past it.
    organization_id = int(getattr(application, "organization_id", 0) or 0)
    score_reservation = int(estimate_reservation(Feature.SCORE))
    locked_org = None
    try:
        if bool(settings.USAGE_METER_LIVE):
            locked_org = (
                db.query(Organization)
                .filter(Organization.id == organization_id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if locked_org is None:
                logger.error(
                    "enqueue_score skipped for application=%s: organization missing",
                    application.id,
                )
                return None
        if bool(requires_active_agent):
            # Global pause is an overlay on the autonomous authority.  Take the
            # organization lock before the Role lock below so Pause/Resume and
            # this paid enqueue have one deterministic order even when usage
            # metering is disabled.
            from .workspace_agent_control import workspace_agent_control_snapshot

            if locked_org is not None:
                workspace_paused = locked_org.agent_workspace_paused_at is not None
            else:
                workspace_paused, _workspace_version = workspace_agent_control_snapshot(
                    db,
                    organization_id=organization_id,
                    lock=True,
                )
            if workspace_paused:
                logger.info(
                    "autonomous score enqueue held application_id=%s: "
                    "workspace agent is paused",
                    application.id,
                )
                return None
        score_reservation = _meter_reserve(
            db,
            organization_id=organization_id,
            feature=Feature.SCORE,
        )
    except InsufficientCreditsError:
        logger.info(
            "enqueue_score skipped for application=%s: insufficient credits",
            application.id,
        )
        return None
    except Exception:
        logger.exception(
            "enqueue_score reserve check failed for application=%s — blocked",
            application.id,
        )
        return None

    try:
        locked_role = (
            db.query(Role)
            .filter(
                Role.id == int(role.id),
                Role.organization_id == organization_id,
            )
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if locked_role is None:
            logger.error(
                "enqueue_score skipped for application=%s: role missing",
                application.id,
            )
            return None

        latest_attempt = _latest_job(db, application.id)
        if (
            bool(requires_active_agent)
            and latest_attempt is not None
            and latest_attempt.status == SCORE_JOB_STALE
            and not bool(latest_attempt.dispatch_approved)
        ):
            logger.info(
                "autonomous score enqueue awaiting rescreen approval application_id=%s",
                application.id,
            )
            return None
        if not force:
            existing = latest_attempt
            if existing is not None and existing.status in {
                SCORE_JOB_PENDING,
                SCORE_JOB_RUNNING,
            }:
                # An explicit recruiter action is fresh authority to finish an
                # already-queued autonomous score. Persist the promotion before
                # returning so the separate worker cannot observe the old flag.
                if not bool(requires_active_agent) and bool(
                    getattr(existing, "requires_active_agent", True)
                ):
                    existing.requires_active_agent = False
                    existing.force_full_score = bool(
                        getattr(existing, "force_full_score", False)
                        or bypass_pre_screen
                    )
                    db.add(existing)
                    db.commit()
                return existing

        if bool(requires_active_agent):
            from .role_execution_guard import automatic_role_action_block_reason

            authority_block = automatic_role_action_block_reason(
                locked_role,
                db=db,
            )
            if authority_block is not None:
                logger.info(
                    "autonomous score enqueue held application_id=%s role_id=%s "
                    "detail=%s",
                    application.id,
                    locked_role.id,
                    authority_block,
                )
                return None

        ensure_role_capacity(
            db,
            organization_id=organization_id,
            role_id=int(locked_role.id),
            required=int(score_reservation),
        )

        if locked_org is not None:
            # Org credits are also committed while score jobs are in flight;
            # actual debits do not land until workers finish.  Count those
            # jobs under the org lock so direct enqueues cannot overdraw even
            # though the legacy reserve() check is intentionally soft.
            active_org_jobs = int(
                db.query(func.count(CvScoreJob.id))
                .join(Role, CvScoreJob.role_id == Role.id)
                .filter(
                    Role.organization_id == organization_id,
                    CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
                )
                .scalar()
                or 0
            )
            available = int(locked_org.credits_balance or 0)
            required_with_commitments = (active_org_jobs + 1) * int(score_reservation)
            if available < required_with_commitments:
                logger.info(
                    "enqueue_score skipped for application=%s: org credit "
                    "commitments need=%s available=%s active_jobs=%s",
                    application.id,
                    required_with_commitments,
                    available,
                    active_org_jobs,
                )
                return None
    except InsufficientRoleBudgetError as exc:
        logger.info(
            "enqueue_score skipped for application=%s: role monthly cap "
            "reached (role_id=%s required=%s available=%s)",
            application.id,
            exc.role_id,
            exc.required,
            exc.available,
        )
        return None
    except Exception:
        # A broken cap query is not permission to spend.  This path used to
        # log "proceeding", which made a transient DB error a budget bypass.
        logger.exception(
            "score admission check failed for application=%s — blocked",
            application.id,
        )
        return None

    claimed_batch_run_id = None
    if batch_run_id is not None:
        from .score_job_batch_ownership import claim_live_scoring_batch

        claimed_batch_run_id = claim_live_scoring_batch(
            db,
            batch_run_id=batch_run_id,
            role_id=int(locked_role.id),
            organization_id=organization_id,
            application_id=int(application.id),
            owner_delivery_id=batch_delivery_id,
        )
        if claimed_batch_run_id is None:
            logger.info(
                "score enqueue held by inactive batch application_id=%s run_id=%s",
                application.id,
                batch_run_id,
            )
            return None

    from .score_job_dispatch import create_and_dispatch_score_job

    return create_and_dispatch_score_job(
        db,
        application=application,
        requires_active_agent=requires_active_agent,
        bypass_pre_screen=bypass_pre_screen,
        batch_run_id=claimed_batch_run_id,
    )


def _execute_scoring(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
    force_full_score: bool = False,
) -> None:
    """Run the scoring pipeline for one application + job pair.

    Updates ``application.cv_match_score`` / ``cv_match_details`` and the
    ``job`` row in place. Call inside a session that the caller will commit.

    Single scoring path: routes through ``app.cv_matching.runner.run_cv_match``.
    """
    _execute_scoring_v3(
        db, application=application, job=job, force_full_score=force_full_score
    )
    # Sync the cached score columns (role_fit_score_cache_100,
    # taali_score_cache_100, score_mode_cache, pre_screen_score_100) so
    # the candidate-detail endpoint — which reads from the cache — sees
    # the fresh CV score. Without this the directory list (live-computed)
    # and detail page (cache-read) drift apart after every rescore.
    from ..domains.assessments_runtime.role_support import (
        refresh_application_score_cache,
    )
    from .interview_support_service import refresh_application_interview_support

    try:
        refresh_application_score_cache(application, db=db)
    except Exception:  # pragma: no cover — cache refresh must not break scoring
        logger.exception(
            "Failed to refresh score cache for application=%s job=%s",
            getattr(application, "id", None),
            getattr(job, "id", None),
        )

    # Pre-build interview-support pack from the fresh details. Detail
    # endpoint reads this from cache instead of regenerating Claude
    # questions inline (which previously made every page load 20s+).
    #
    # Three gates, all required:
    # 1. Skip on cache hits — pack was built when the score was first
    #    computed, so rebuilding would make an unnecessary Haiku call.
    # 2. Skip when the application has no cv_match_score (pre-screen
    #    filtered, or v3 failed). Building an interview pack for a
    #    candidate we already rejected wastes ~$0.013 in Haiku 4.5
    #    spend per candidate — historically the dominant cost driver
    #    on the platform when imports ran without recruiter intent.
    # 3. Skip when scoring errored (job.status == ERROR) — same reason.
    has_real_score = application.cv_match_score is not None
    job_succeeded = job.status == SCORE_JOB_DONE
    if job.cache_hit != "hit" and has_real_score and job_succeeded:
        _authorize_autonomous_scoring_phase(
            db,
            application=application,
            job=job,
            phase="interview_support",
        )
        try:
            refresh_application_interview_support(
                application,
                organization=getattr(application, "organization", None),
            )
        except (
            Exception
        ):  # pragma: no cover — interview-pack refresh must not break scoring
            logger.exception(
                "Failed to refresh interview support for application=%s job=%s",
                getattr(application, "id", None),
                getattr(job, "id", None),
            )


def _emit_cv_scored_event(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
    score_100: float | None,
    recommendation: str,
    prompt_version: str | None,
    model_version: str | None,
    trace_id: str,
    cache_hit: str,
) -> None:
    """Emit a `cv_scored` activity event so the candidate timeline reflects
    every successful CV score. Idempotent on (application, trace_id)."""
    try:
        score_label = (
            f"{float(score_100):.0f}%"
            if isinstance(score_100, (int, float)) and score_100 is not None
            else "—"
        )
        rec_label = recommendation.replace("_", " ").strip() or "scored"
        reason = f"CV scored: {rec_label} ({score_label})"
        append_application_event(
            db,
            app=application,
            event_type="cv_scored",
            actor_type="system",
            reason=reason,
            metadata={
                "prompt_version": prompt_version,
                "model_version": model_version,
                "role_fit_score": score_100,
                "recommendation": recommendation or None,
                "trace_id": trace_id,
                "cache_hit": cache_hit,
                "job_id": job.id,
            },
            idempotency_key=f"cv_scored:{application.id}:{trace_id}",
        )
    except Exception:  # pragma: no cover — telemetry must never break scoring
        logger.exception(
            "Failed to emit cv_scored event for application=%s job=%s",
            getattr(application, "id", None),
            getattr(job, "id", None),
        )


def _holistic_enabled_for(application: CandidateApplication) -> bool:
    """True when the holistic Sonnet engine is enabled for this app's org.

    Gated by two settings so deploy is zero-behaviour-change until both are
    set: ``HOLISTIC_SCORING_ENABLED`` (master switch) and
    ``HOLISTIC_SCORING_ORG_IDS`` (comma-separated org allowlist, or "*").
    """
    if not getattr(settings, "HOLISTIC_SCORING_ENABLED", False):
        return False
    allow = (getattr(settings, "HOLISTIC_SCORING_ORG_IDS", "") or "").strip()
    if not allow:
        return False
    if allow == "*":
        return True
    org_id = getattr(application, "organization_id", None)
    if org_id is None:
        return False
    return str(int(org_id)) in {x.strip() for x in allow.split(",") if x.strip()}


def score_is_outdated(application: CandidateApplication) -> bool:
    """True when re-scoring this application would move it to a NEWER engine
    than the one its stored score came from.

    Two conditions: the holistic engine is enabled for the app's org (so a
    re-score would actually produce the current ``HOLISTIC_ENGINE_VERSION``,
    not just reproduce the same legacy score in a loop) AND the stored score
    predates that version. The org-aware single source of truth behind both the
    agent-chat re-score offer and the decision-staleness "older model" flag.
    """
    from ..cv_matching.holistic import is_engine_outdated

    return _holistic_enabled_for(application) and is_engine_outdated(
        getattr(application, "cv_match_details", None)
    )


def _execute_scoring_v3(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
    force_full_score: bool = False,
) -> None:
    """Score one application via the cv_match_v3.0 pipeline.

    Translates recruiter ``role_criteria`` into ``RequirementInput`` objects,
    invokes ``app.cv_matching.runner.run_cv_match`` (which manages its own
    cache via the shared ``cv_score_cache`` table), and writes the result
    into ``application.cv_match_details`` / ``cv_match_score``.

    Failure handling: ``run_cv_match`` never raises — it returns a
    ``CVMatchOutput`` with ``scoring_status="failed"`` and an error reason.
    We surface that as the same ``job.error_message`` shape the legacy path
    uses, so the UI's "score_status" badge keeps working.
    """
    from ..cv_matching import (
        MODEL_VERSION as V3_MODEL_VERSION,
        PROMPT_VERSION as V3_PROMPT_VERSION,
        ScoringStatus,
    )
    from ..cv_matching.runner import run_cv_match

    role = application.role
    cv_text = (application.cv_text or "").strip()
    from .role_requirement_service import (
        build_scoring_requirements,
        resolve_role_job_spec,
    )

    job_spec_text = resolve_role_job_spec(
        role,
        db=db,
        agent_name="cv_scoring",
    )
    job.started_at = datetime.now(timezone.utc)
    job.status = SCORE_JOB_RUNNING
    job.prompt_version = V3_PROMPT_VERSION
    job.model = V3_MODEL_VERSION

    if not cv_text or not job_spec_text:
        job.status = SCORE_JOB_ERROR
        job.error_message = "missing_inputs"
        job.finished_at = datetime.now(timezone.utc)
        application.cv_match_score = None
        application.cv_match_details = {"error": "Missing CV or job spec text"}
        application.cv_match_scored_at = None
        return

    # One criteria conversion is shared by Stage 1, agent sub-agents and this
    # full scorer.  In particular, ``constraint`` must remain a real
    # ``Priority.CONSTRAINT`` instead of drifting to a soft preference here.
    requirements = build_scoring_requirements(role)

    # Resolve the org's Anthropic client once (workspace-scoped key when
    # provisioned; falls back to shared Taali key otherwise). Reused for
    # both pre-screen and the full v3 call so a freshly-provisioned key
    # is used consistently within a single scoring job.
    org_client = _resolve_anthropic_client(getattr(application, "organization", None))

    # Two-tier scoring gate. When enabled, the canonical Stage 1 pre-screen
    # engine runs first (LLM filter + deterministic fraud detection); CVs
    # scoring below PRE_SCREEN_THRESHOLD — including fraud-capped CVs that
    # copy-pasted the JD — skip the expensive v3 call entirely.
    #
    # Fraud detection lives ONLY in the pre-screen engine. Stage 2 trusts
    # its result. Recruiter manual rescores (force_full_score) bypass the
    # gate entirely.
    if settings.ENABLE_PRE_SCREEN_GATE and not force_full_score:
        from .pre_screening_service import (
            application_needs_pre_screen,
            execute_pre_screen_only,
        )

        # Idempotent: re-run Stage 1 when it's never been run OR when
        # the candidate uploaded a newer CV after the last pre-screen.
        # ``application_needs_pre_screen`` already encodes the
        # "stale CV" check (cv_uploaded_at > pre_screen_run_at) used by
        # the manual batch button, so the two entry points stay aligned.
        if application_needs_pre_screen(application):
            _authorize_autonomous_scoring_phase(
                db,
                application=application,
                job=job,
                phase="pre_screen",
            )
            execute_pre_screen_only(application, db=db, client=org_client)

        static_threshold = int(settings.PRE_SCREEN_THRESHOLD)
        # Stage-1 gate threshold is data-driven (shadow-first). We ALWAYS compute
        # the dynamic, false-reject-budgeted cut for measurement and stamp it
        # below; it only DECIDES when PRE_SCREEN_DYNAMIC_GATE_ENFORCE is on.
        # Until then the static env value governs, so behaviour is unchanged.
        dynamic_rec = None
        try:
            from .prescreen_gate_calibration import compute_gate_threshold_cached

            if role is not None:
                dynamic_rec = compute_gate_threshold_cached(db, role=role)
        except Exception:  # never let calibration break scoring
            logger.warning(
                "dynamic pre-screen gate threshold failed; using static", exc_info=True
            )
        dynamic_threshold = (
            int(dynamic_rec.value)
            if dynamic_rec is not None and dynamic_rec.source == "calibrated"
            else None
        )
        threshold = (
            dynamic_threshold
            if (
                settings.PRE_SCREEN_DYNAMIC_GATE_ENFORCE
                and dynamic_threshold is not None
            )
            else static_threshold
        )
        evidence = (
            application.pre_screen_evidence
            if isinstance(application.pre_screen_evidence, dict)
            else {}
        )
        # Preserve the exact policy that evaluated this candidate even when a
        # successful full score later replaces ``cv_match_details``. Reports
        # can then classify historical disagreements against the cut that
        # actually ran, instead of today's potentially retuned environment.
        evidence = {
            **evidence,
            "gate_threshold_static": static_threshold,
            "gate_threshold_dynamic": dynamic_threshold,
            "gate_threshold_enforced": threshold,
            "gate_dynamic_source": getattr(dynamic_rec, "source", None),
        }
        application.pre_screen_evidence = evidence
        fraud_capped = bool(evidence.get("fraud_capped", False))
        # The durable genuine score is the authoritative Stage-1 result.  It
        # includes any bounded score policy that was actually applied; the
        # evidence ``llm_score_100`` is deliberately raw calibration data.
        # Never fall back to ``pre_screen_score_100``: older full-score cache
        # refreshes overwrote that shared column.  A legacy row without a
        # genuine score therefore fails open to full scoring below.
        genuine_score = getattr(application, "genuine_pre_screen_score_100", None)
        gated_score = float(genuine_score) if genuine_score is not None else None
        # Pre-screen errored (Anthropic credit exhaustion, network
        # timeout, JSON parse failure, etc.) — DON'T fall through to v3
        # cv_match. Previously we did, and the v3 score got mirrored
        # into ``pre_screen_score_100`` via the refresh helpers, hiding
        # the error from the recruiter. Now we surface a clear error
        # state and bail; the next sweeper tick (or manual rescore)
        # picks the application back up.
        pre_screen_errored = (evidence.get("decision") == "error") or bool(
            application.pre_screen_error_reason
        )
        if pre_screen_errored:
            now = datetime.now(timezone.utc)
            reason = (
                application.pre_screen_error_reason
                or evidence.get("summary")
                or "pre_screen_unknown_error"
            )
            failure_code = public_scoring_failure_code(reason)
            logger.warning(
                "pre-screen failed application_id=%s failure_code=%s reason=%s",
                application.id,
                failure_code,
                reason,
            )
            job.status = SCORE_JOB_ERROR
            job.error_message = f"pre_screen_errored:{failure_code}"
            job.cache_hit = "pre_screen_errored"
            job.finished_at = now
            # Make sure no stale scores remain — pre-screen handler
            # already NULLs these, but defensive in case caller wired
            # in via a different path. Critical: also clear
            # ``cv_match_details`` so ``refresh_pre_screening_fields``
            # (called downstream by the cache refresher) can't
            # resurrect a stale pre-screen score from a prior run's
            # ``cv_match_details['pre_screen_score_100']`` field — that
            # would re-hide the error we're trying to surface.
            application.cv_match_score = None
            application.cv_match_details = None
            application.cv_match_scored_at = None
            return
        # Shadow measurement: record what the dynamic cut WOULD do vs the static
        # one for EVERY gated candidate (survivor or filtered), so the divergence
        # and false-reject impact are observable before we ever enforce.
        if gated_score is not None:
            logger.info(
                "pre_screen_gate org=%s role=%s score=%.1f static=%s dynamic=%s enforced=%s "
                "static_filter=%s dynamic_filter=%s source=%s",
                getattr(application, "organization_id", None),
                getattr(application, "role_id", None),
                float(gated_score),
                static_threshold,
                dynamic_threshold,
                threshold,
                gated_score < static_threshold,
                (dynamic_threshold is not None and gated_score < dynamic_threshold),
                getattr(dynamic_rec, "source", None),
            )
        # Only filter when we have a numeric score AND it's below threshold.
        if gated_score is not None and gated_score < threshold:
            now = datetime.now(timezone.utc)
            if fraud_capped:
                summary = evidence.get("summary") or (
                    "Pre-screen filtered: CV contains text copied verbatim "
                    "from the job description."
                )
                cache_hit_label = "fraud_filtered"
                recommendation_label = "fraud_filtered"
            else:
                summary = (
                    f"Pre-screen filtered: score {gated_score:.0f}/100 "
                    f"(threshold {threshold})."
                )
                cache_hit_label = "pre_screen_filtered"
                recommendation_label = "pre_screened_out"
            details = {
                "scoring_version": V3_PROMPT_VERSION,
                "pre_screen_score_100": gated_score,
                "pre_screen_decision": evidence.get("decision") or "no",
                "pre_screen_reason": evidence.get("summary"),
                "pre_screen_trace_id": evidence.get("trace_id"),
                "pre_screen_prompt_version": evidence.get("prompt_version"),
                "summary": summary,
                "recommendation": "no",
                "fraud_signals": evidence.get("fraud_signals", {}),
                "fraud_capped": fraud_capped,
                "llm_score_100": evidence.get("llm_score_100"),
                # Gate-threshold provenance (audit + shadow measurement): which
                # cut actually decided, plus the dynamic recommendation alongside.
                "gate_threshold_static": static_threshold,
                "gate_threshold_dynamic": dynamic_threshold,
                "gate_threshold_enforced": threshold,
                "gate_dynamic_source": getattr(dynamic_rec, "source", None),
            }
            application.cv_match_score = None
            application.cv_match_details = details
            application.cv_match_scored_at = now
            job.cache_hit = cache_hit_label
            job.status = SCORE_JOB_DONE
            job.finished_at = now
            _emit_cv_scored_event(
                db,
                application=application,
                job=job,
                score_100=gated_score,
                recommendation=recommendation_label,
                prompt_version=evidence.get("prompt_version"),
                model_version=V3_MODEL_VERSION,
                trace_id=evidence.get("trace_id") or f"job-{job.id}",
                cache_hit=job.cache_hit,
            )
            return

    # The score + archetype Anthropic calls are now metered by the
    # MeteredAnthropicClient wrapper itself — it writes one usage_event
    # per call (FK-linked to claude_call_log), threaded with this
    # context. That captures errored/retried calls the old post-call
    # record missed (usage_event was ~73% short of actual spend on
    # 2026-05-22; claude_call_log proved it).
    # No ``db`` here: the wrapper self-manages fresh, committed sessions
    # for both the usage_event and the FK-linked claude_call_log row.
    # Passing the caller's open transaction left the usage_event
    # uncommitted and invisible to call_log's separate session, which
    # raised a FK violation and silently dropped every score call_log row.
    score_metering_context = {
        "organization_id": getattr(application, "organization_id", None),
        "role_id": getattr(application, "role_id", None),
        "entity_id": f"application:{application.id}",
        "candidate_id": getattr(application, "candidate_id", None),
    }
    # Workable metadata (questionnaire answers, recruiter comments, activity
    # log) carries hard-constraint evidence the CV often lacks — e.g. a salary
    # expectation given on a LinkedIn apply. Feed it so the full score assesses
    # those requirements instead of leaving them "unknown". Same source the
    # pre-screen gate already uses; empty string when there's no footprint.
    workable_context = ""
    try:
        from .workable_context_service import format_workable_context

        workable_context = format_workable_context(
            candidate=getattr(application, "candidate", None),
            application=application,
        )
    except Exception:  # pragma: no cover — scoring must not break on context render
        logger.exception(
            "format_workable_context failed for application=%s; scoring without it",
            getattr(application, "id", None),
        )

    def authorize_full_score_provider(phase: str) -> None:
        _authorize_autonomous_scoring_phase(
            db,
            application=application,
            job=job,
            phase=phase,
        )

    authorize_full_score_provider("full_score.cache_or_provider")
    if _holistic_enabled_for(application):
        # Holistic Sonnet engine: single calibrated call whose ``overall``
        # becomes role_fit_score directly. The pre-screen gate above already
        # filtered this candidate in, so this is the "spend more on the
        # survivors" tier of the two-tier strategy.
        from ..cv_matching.holistic import run_holistic_match

        output = run_holistic_match(
            cv_text,
            job_spec_text,
            client=org_client,
            metering_context=score_metering_context,
            workable_context=workable_context or None,
            before_provider_call=authorize_full_score_provider,
        )
    else:
        output = run_cv_match(
            cv_text,
            job_spec_text,
            requirements,
            client=org_client,
            metering_context=score_metering_context,
            workable_context=workable_context or None,
            before_provider_call=authorize_full_score_provider,
        )
    job.cache_hit = "hit" if getattr(output, "cache_hit", False) else "miss"
    # CACHE HITS ONLY: a cache hit makes no Anthropic call, so the wrapper
    # never runs and never records. Record it here so cached scores still
    # bill (unchanged behaviour). Cache MISSES are already recorded by the
    # wrapper per-call above — recording them here too would double-count.
    if bool(getattr(output, "cache_hit", False)):
        # A cache hit makes no provider call, but it still carries the small
        # platform cache fee. Give that debit the same org+role hard-admission
        # contract as provider spend; the old soft enqueue preflight could race
        # with other jobs and let this direct debit cross a balance/cap.
        from .cv_score_cache_metering import record_cv_score_cache_fee

        cache_model = getattr(output, "model_version", None) or V3_MODEL_VERSION
        cache_candidate_id = getattr(application, "candidate_id", None)
        record_cv_score_cache_fee(
            db,
            organization_id=int(application.organization_id),
            role_id=int(application.role_id),
            application_id=int(application.id),
            candidate_id=(
                int(cache_candidate_id) if cache_candidate_id is not None else None
            ),
            score_job_id=int(job.id),
            trace_id=(str(getattr(output, "trace_id", None) or f"score-job:{job.id}")),
            model=cache_model,
            input_tokens=int(getattr(output, "input_tokens", 0) or 0),
            output_tokens=int(getattr(output, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(output, "cache_read_tokens", 0) or 0),
            cache_creation_tokens=int(getattr(output, "cache_creation_tokens", 0) or 0),
            record_event=_meter_record_event,
        )

    if output.scoring_status == ScoringStatus.FAILED:
        failure_code = public_scoring_failure_code(output.error_reason)
        logger.warning(
            "CV scoring failed application_id=%s failure_code=%s",
            application.id,
            failure_code,
        )
        job.status = SCORE_JOB_ERROR
        job.error_message = f"v3_failed:{failure_code}"
        job.finished_at = datetime.now(timezone.utc)
        application.cv_match_score = None
        application.cv_match_details = {
            "error": failure_code,
            "scoring_version": V3_PROMPT_VERSION,
            "trace_id": output.trace_id,
        }
        application.cv_match_scored_at = None
        return

    application.cv_match_score = output.role_fit_score
    # Promote the ingest-time PDF-hygiene stash (written to the OLD
    # cv_match_details before this wholesale overwrite) so it reaches the report.
    from ..services.document_hygiene import PENDING_PDF_HYGIENE_KEY

    prev_details = (
        application.cv_match_details
        if isinstance(application.cv_match_details, dict)
        else {}
    )
    pending_pdf_hygiene = prev_details.get(PENDING_PDF_HYGIENE_KEY)
    details = output.model_dump(mode="json")
    details["integrity_signals"] = _augment_integrity_signals(
        details.get("integrity_signals"),
        application,
        cv_text,
        job_spec_text,
        snapshot=details.get("candidate_snapshot"),
        pdf_hygiene=pending_pdf_hygiene
        if isinstance(pending_pdf_hygiene, dict)
        else None,
    )
    application.cv_match_details = details
    application.cv_match_scored_at = datetime.now(timezone.utc)
    # Record the engine that ACTUALLY scored this candidate on the job row +
    # timeline event (the top-of-function defaults assume the Haiku v3 path;
    # the holistic engine runs Sonnet / holistic_v2).
    if getattr(output, "prompt_version", None):
        job.prompt_version = output.prompt_version
    if getattr(output, "model_version", None):
        job.model = output.model_version
    job.status = SCORE_JOB_DONE
    job.finished_at = datetime.now(timezone.utc)
    # The authoritative full score just landed. If a pending pre-screen reject
    # card exists and this score clears the pre-screen threshold, the cheap
    # gate's verdict is moot — discard it so the agent's cv_match flow can
    # send/advance the candidate instead of leaving them in the reject queue.
    try:
        from .pre_screen_decision_emitter import (
            supersede_pre_screen_reject_on_full_score,
        )
        from .pre_screening_service import resolved_auto_reject_config
        from .pre_screening_snapshot import pre_screen_recommendation_label

        threshold_100 = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
        supersede_pre_screen_reject_on_full_score(
            db, application=application, threshold=threshold_100
        )
        # Keep the recommendation label aligned with the authoritative score.
        # The shared ``pre_screen_score_100`` is overwritten with this score,
        # so the frozen pre-screen label would otherwise contradict it
        # ("Strong match" on a 12/100). Fraud-capped rows keep their verdict.
        ps_ev = (
            application.pre_screen_evidence
            if isinstance(application.pre_screen_evidence, dict)
            else {}
        )
        if not ps_ev.get("fraud_capped"):
            application.pre_screen_recommendation = pre_screen_recommendation_label(
                output.role_fit_score, threshold_100
            )
    except Exception:  # pragma: no cover — never fail scoring on a card cleanup
        logger.exception(
            "supersede_pre_screen_reject_on_full_score failed for app=%s",
            getattr(application, "id", None),
        )
    _emit_cv_scored_event(
        db,
        application=application,
        job=job,
        score_100=output.role_fit_score,
        recommendation=getattr(
            output.recommendation, "value", str(output.recommendation or "")
        ),
        prompt_version=getattr(output, "prompt_version", None) or V3_PROMPT_VERSION,
        model_version=getattr(output, "model_version", None) or V3_MODEL_VERSION,
        trace_id=output.trace_id or f"job-{job.id}",
        cache_hit="hit" if getattr(output, "cache_hit", False) else "miss",
    )


def _clear_application_scores(app: CandidateApplication) -> None:
    """Mark application scores as stale WITHOUT wiping the numeric values.

    The previous version NULL'd every score field on invalidation so the
    UI would show "needs rescore" instead of the old number. In
    practice that broke recruiter trust badly: roles where 400+ scored
    candidates suddenly all showed blank, with no indication of WHY
    the system had forgotten them. Agent decisions on the Home page
    were also orphaned from their backing scores.

    The honest UX is to keep the old score visible AND flag it as
    stale. The caller adds a ``CvScoreJob(status="stale")`` row (via
    ``_enqueue_stale_job``); the frontend renders that as a "stale"
    badge alongside the existing number so the recruiter sees:
       "Strong match — 87 ⓘ stale: re-evaluating against updated salary cap"
    until the rescore lands and atomically replaces the value.

    What this helper now DOES clear:
    - ``pre_screen_run_at`` so ``application_needs_pre_screen``
      correctly returns True on the next orchestrator pass and
      Stage-1 actually re-runs.
    - ``pre_screen_error_reason`` so the next successful attempt
      doesn't get masked by a leftover error reason from a previous
      attempt (a credit-exhaustion error from yesterday shouldn't
      block today's retry).

    What it deliberately KEEPS (so the UI keeps showing them with a
    stale badge):
    - ``pre_screen_score_100``, ``requirements_fit_score_100``
    - ``cv_match_score``, ``cv_match_details``, ``cv_match_scored_at``
    - ``pre_screen_recommendation``
    - ``taali_score_cache_100``, ``assessment_score_cache_100``,
      ``role_fit_score_cache_100``, ``score_mode_cache``,
      ``score_cached_at``
    - ``rank_score`` (preserves directory ordering)

    Pending agent decisions that reference this application are
    superseded separately by ``supersede_pending_decisions_for_app``;
    the agent's next cohort tick generates fresh decisions once the
    rescore lands.
    """
    app.pre_screen_run_at = None
    app.pre_screen_error_reason = None


def supersede_pending_decisions_for_app(
    db: Session,
    application_id: int,
    *,
    reason: str = "score_invalidated",
    role_id: int | None = None,
) -> int:
    """Discard any ``pending`` ``AgentDecision`` rows for an application
    whose backing score has just been invalidated.

    Without this, the Home review queue would keep showing the agent's
    old recommendation (e.g. "advance to interview") even though that
    recommendation was based on a score that no longer reflects current
    role criteria. The recruiter would approve a decision the agent
    itself would reverse on its next cohort tick.

    Setting status to ``discarded`` is the cleanest exit:
    - Removes the row from the Home queue immediately
    - Preserves the audit trail (resolved_at + resolution_note)
    - Lets the agent's next cohort tick generate a fresh decision
      based on the new score once it lands

    Returns the number of decisions superseded.
    """
    from ..models.agent_decision import AgentDecision

    now = datetime.now(timezone.utc)
    query = db.query(AgentDecision).filter(
        AgentDecision.application_id == application_id,
        AgentDecision.status == "pending",
    )
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    pending = query.all()
    for decision in pending:
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = (
            f"superseded: {reason}; agent will re-decide once the new score lands"
        )[:500]
    if pending:
        db.flush()
    return len(pending)


def _enqueue_stale_job(
    db: Session,
    *,
    app: CandidateApplication,
    role_id: int,
    now: datetime,
    requires_active_agent: bool = True,
    dispatch_approved: bool = True,
    supersede_existing_stale: bool = False,
) -> bool:
    """Add a stale job, or durably promote an existing stale job's authority."""
    latest = _latest_job(db, app.id)
    if latest is not None and latest.status == "stale" and not supersede_existing_stale:
        changed = False
        if not bool(requires_active_agent) and bool(latest.requires_active_agent):
            latest.requires_active_agent = False
            changed = True
        if bool(dispatch_approved) and not bool(latest.dispatch_approved):
            latest.dispatch_approved = True
            changed = True
        if changed:
            db.add(latest)
            db.flush()
        return False
    db.add(
        CvScoreJob(
            application_id=app.id,
            role_id=role_id,
            status="stale",
            queued_at=now,
            requires_active_agent=bool(requires_active_agent),
            dispatch_approved=bool(dispatch_approved),
        )
    )
    db.flush()
    return True


def mark_role_scores_stale(
    db: Session,
    role_id: int,
    *,
    reason: str = "role_intent_changed",
    application_ids: list[int] | None = None,
    dispatch_tech_questions: bool = True,
    requires_active_agent: bool = True,
    dispatch_approved: bool = True,
    supersede_existing_stale: bool = False,
) -> int:
    """Invalidate every scored application for a role.
    Optional ids scope the change; an empty list is a no-op. Existing values
    remain visibly stale and ``dispatch_approved=False`` authorizes no spend.
    """
    if application_ids is not None and not application_ids:
        return 0
    apps_q = db.query(CandidateApplication).filter(
        CandidateApplication.role_id == role_id,
        CandidateApplication.deleted_at.is_(None),
        # Anything that was ever scored OR pre-screened — covers
        # apps below the gate threshold (cv_match_score is NULL
        # but pre_screen_score_100 is set) that still need a fresh
        # decision against the new role intent.
        or_(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.cv_match_score.isnot(None),
        ),
    )
    if application_ids is not None:
        apps_q = apps_q.filter(CandidateApplication.id.in_(list(application_ids)))
    apps = apps_q.all()
    # A6: resolved applications are frozen — invalidation hooks must
    # never touch them. The decision snapshot stays as the immutable
    # audit record. We do this filter inside the loop (rather than in
    # the query) so other consumers of this code path can't accidentally
    # drop the guard.
    from ..domains.assessments_runtime.role_support import is_resolved as _is_resolved

    marked = 0
    now = datetime.now(timezone.utc)
    for app in apps:
        if _is_resolved(app):
            continue
        if not _enqueue_stale_job(
            db,
            app=app,
            role_id=role_id,
            now=now,
            requires_active_agent=requires_active_agent,
            dispatch_approved=dispatch_approved,
            supersede_existing_stale=supersede_existing_stale,
        ):
            continue
        _clear_application_scores(app)
        supersede_pending_decisions_for_app(db, app.id, reason=reason)
        marked += 1

    # Role-level tech-screening questions are derived from job_spec +
    # criteria — the same inputs that invalidate CV scoring. Null the
    # signature so the cache surfaces as stale, then dispatch an async
    # regen so the recruiter's PATCH / chip CRUD doesn't block on a
    # ~3s Anthropic call. ``regenerate_role_tech_questions`` is
    # idempotent against the signature so back-to-back chip edits
    # collapse to one effective regen once they settle.
    try:
        role = db.query(Role).filter(Role.id == role_id).one_or_none()
        if role is not None:
            from .role_tech_questions_service import (
                invalidate as _invalidate_tech_questions,
            )

            _invalidate_tech_questions(role)
            db.add(role)
            try:
                from ..tasks.automation_tasks import regenerate_role_tech_questions

                # Dispatch with a short countdown so the nulled signature is
                # committed by the caller's outer transaction before a worker
                # picks the task up — otherwise the signature-gated regen can
                # read the still-old committed signature and skip itself.
                if dispatch_tech_questions:
                    regenerate_role_tech_questions.apply_async(
                        args=[int(role_id)], countdown=10
                    )
            except Exception:
                logger.exception(
                    "mark_role_scores_stale: failed to dispatch tech_questions regen role_id=%s",
                    role_id,
                )
    except Exception:
        logger.exception(
            "mark_role_scores_stale: tech_questions invalidation failed role_id=%s",
            role_id,
        )

    return marked


def mark_application_scores_stale(
    db: Session,
    application_id: int,
    *,
    reason: str = "candidate_data_changed",
) -> bool:
    """Invalidate a single application's scores (e.g. on CV upload or
    Workable-context change for that one candidate). Mirror of
    ``mark_role_scores_stale`` scoped to one app. Returns True if the
    app was actually invalidated.
    """
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if app is None:
        return False
    # A6: resolved applications are frozen — never invalidate.
    from ..domains.assessments_runtime.role_support import is_resolved as _is_resolved

    if _is_resolved(app):
        logger.info(
            "resolved_app_skipped action=mark_application_scores_stale "
            "application_id=%s pipeline_stage=%s application_outcome=%s",
            app.id,
            app.pipeline_stage,
            app.application_outcome,
        )
        return False
    now = datetime.now(timezone.utc)
    if not _enqueue_stale_job(db, app=app, role_id=app.role_id, now=now):
        return False
    _clear_application_scores(app)
    supersede_pending_decisions_for_app(db, app.id, reason=reason)
    return True
