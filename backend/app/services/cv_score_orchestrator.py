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
      → on error: marks job error with the message; the application's cv_match_details
        gets an error blob so the UI can surface it

Cache invalidation is implicit: changing criteria, the spec, the prompt
version, or the model produces a different cache_key, so the next score
yields a cache miss and a fresh result. There is no explicit invalidation
sweep — cache rows are immutable and accumulate (acceptable for now; a TTL
or LRU eviction can be bolted on later if storage becomes a concern).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_cache import CvScoreCache
from ..models.cv_score_job import (
    CvScoreJob,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
)
from ..models.role import Role
from ..models.organization import Organization
from ..platform.config import settings
from ..domains.assessments_runtime.pipeline_service import append_application_event
from .fit_matching_service import (
    CV_MATCH_V4_PROMPT_VERSION,
    CvMatchValidationError,
    calculate_cv_job_match_sync,
    calculate_cv_job_match_v4_sync,
)
from .claude_client_resolver import get_client_for_org as _resolve_anthropic_client
from .pricing_service import Feature, estimate_reservation
from .spec_normalizer import normalize_spec
from .usage_metering_service import (
    InsufficientCreditsError,
    record_event as _meter_record_event,
    reserve as _meter_reserve,
)
from .usage_credit_reservations import (
    InsufficientRoleBudgetError,
    ensure_role_capacity,
)

logger = logging.getLogger("taali.cv_score_orchestrator")


_V3_PROMPT_VERSION = "cv_fit_v3_evidence_enriched"


def _criteria_payload(role: Role | None) -> list[dict]:
    if role is None:
        return []
    try:
        rows = list(role.criteria or [])
    except Exception:
        return []
    items: list[dict] = []
    for c in sorted(rows, key=lambda c: getattr(c, "ordering", 0)):
        if getattr(c, "deleted_at", None) is not None:
            continue
        items.append(
            {
                "id": int(c.id),
                "text": str(c.text or "").strip(),
                "must_have": bool(c.must_have),
                "bucket": str(getattr(c, "bucket", None) or ("must" if bool(c.must_have) else "preferred")),
                "source": str(c.source or "recruiter"),
            }
        )
    return items


def compute_cache_key(
    *,
    cv_text: str,
    spec_description: str,
    spec_requirements: str,
    criteria: list[dict],
    prompt_version: str,
    model: str,
) -> str:
    """Hash the v4 (or v3) inputs into a deterministic cache key.

    ``bucket`` is included so a recruiter changing must → preferred
    invalidates the cache (the agent reasoning weights buckets differently)."""
    payload = {
        "cv": cv_text or "",
        "spec_description": spec_description or "",
        "spec_requirements": spec_requirements or "",
        "criteria": [
            {
                "id": int(c["id"]),
                "text": str(c.get("text") or ""),
                "must_have": bool(c.get("must_have")),
                "bucket": str(c.get("bucket") or ("must" if bool(c.get("must_have")) else "preferred")),
            }
            for c in criteria
        ],
        "prompt_version": str(prompt_version),
        "model": str(model),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def get_cached_result(db: Session, cache_key: str) -> CvScoreCache | None:
    return db.query(CvScoreCache).filter(CvScoreCache.cache_key == cache_key).first()


def store_cached_result(
    db: Session,
    *,
    cache_key: str,
    prompt_version: str,
    model: str,
    score_100: float | None,
    result: dict,
) -> CvScoreCache:
    existing = get_cached_result(db, cache_key)
    if existing is not None:
        existing.hit_count = (existing.hit_count or 0) + 1
        existing.last_hit_at = datetime.now(timezone.utc)
        return existing
    row = CvScoreCache(
        cache_key=cache_key,
        prompt_version=prompt_version,
        model=model,
        score_100=score_100,
        result=result,
    )
    db.add(row)
    return row


def _latest_job(db: Session, application_id: int) -> CvScoreJob | None:
    return (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == application_id)
        .order_by(desc(CvScoreJob.queued_at), desc(CvScoreJob.id))
        .first()
    )


def _has_active_job(db: Session, application_id: int) -> bool:
    latest = _latest_job(db, application_id)
    return latest is not None and latest.status in {SCORE_JOB_PENDING, SCORE_JOB_RUNNING}


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
        ev = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
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
) -> CvScoreJob | None:
    """Queue a CV score for an application.

    Returns the new job, or the existing active job if one is already
    pending/running and ``force`` is False. Returns ``None`` when the
    application can't be scored (no CV, no spec, no API key).

    ``force`` only controls the duplicate-job check (allow re-enqueue
    even if a pending/running job already exists). It does NOT bypass
    the pre-screen gate — historically the two were conflated, which
    meant batch rescores accidentally ran the expensive v9 prompt on
    every candidate even when ``ENABLE_PRE_SCREEN_GATE`` was on.

    ``bypass_pre_screen`` is the explicit opt-out for the pre-screen
    gate: use only when a recruiter has reviewed and wants a full v9
    score regardless of the cheap filter's verdict.

    ``requires_active_agent`` is durable execution authority, not merely an
    enqueue-time hint. Ingest/cohort/agent callers set it to ``True`` so a
    queued job cannot begin after Pause or Turn off. Authenticated recruiter
    and administrator actions leave it ``False`` and may still run while the
    autonomous agent is held (subject to credits and the role cap).
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
            application.id, application.pipeline_stage, application.application_outcome,
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
                application.id, genuine, settings.PRE_SCREEN_THRESHOLD,
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
                workspace_paused = (
                    locked_org.agent_workspace_paused_at is not None
                )
            else:
                workspace_paused, _workspace_version = (
                    workspace_agent_control_snapshot(
                        db,
                        organization_id=organization_id,
                        lock=True,
                    )
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

        # Duplicate reuse belongs inside the role lock.  Otherwise two public
        # requests for the same application can both pass `_latest_job` before
        # either pending row becomes visible.
        if not force:
            existing = _latest_job(db, application.id)
            if existing is not None and existing.status in {
                SCORE_JOB_PENDING,
                SCORE_JOB_RUNNING,
            }:
                # An explicit recruiter action is fresh authority to finish an
                # already-queued autonomous score. Persist the promotion before
                # returning so the separate worker cannot observe the old flag.
                if (
                    not bool(requires_active_agent)
                    and bool(getattr(existing, "requires_active_agent", True))
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
                    CvScoreJob.status.in_(
                        (SCORE_JOB_PENDING, SCORE_JOB_RUNNING)
                    ),
                )
                .scalar()
                or 0
            )
            available = int(locked_org.credits_balance or 0)
            required_with_commitments = (
                active_org_jobs + 1
            ) * int(score_reservation)
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

    job = CvScoreJob(
        application_id=application.id,
        role_id=application.role_id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=bool(requires_active_agent),
        force_full_score=bool(bypass_pre_screen),
    )
    db.add(job)
    db.flush()  # populate job.id

    from ..tasks.scoring_tasks import score_application_job

    # Commit BEFORE dispatching so the worker (on a different DB
    # connection) sees the new pending job. Without this, batch
    # rescores raced: workers picked up the celery task, queried
    # _latest_job, found the previous error/done job because the
    # API server hadn't committed yet, and bailed out as "skipped".
    db.commit()

    try:
        async_result = score_application_job.delay(
            application.id,
            job_id=int(job.id),
            force_full_score=bypass_pre_screen,
        )
    except Exception as exc:
        # The row was committed before dispatch so a separate worker can see
        # it. Compensate if the broker rejects the message; otherwise this
        # "pending" row wins the duplicate guard forever and no later cohort
        # tick can retry the candidate.
        job.status = SCORE_JOB_ERROR
        job.error_message = f"broker_dispatch_failed: {exc}"[:1000]
        job.finished_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        logger.exception(
            "score dispatch failed application_id=%s job_id=%s",
            application.id,
            job.id,
        )
        raise
    job.celery_task_id = str(async_result.id)
    db.add(job)
    # Persist the broker receipt immediately. Callers often return without a
    # second commit, and the task id is essential for queue/attempt tracing.
    db.commit()
    return job


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
        try:
            refresh_application_interview_support(
                application,
                organization=getattr(application, "organization", None),
            )
        except Exception:  # pragma: no cover — interview-pack refresh must not break scoring
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
        Priority as V3Priority,
        RequirementInput,
        ScoringStatus,
    )
    from ..cv_matching.runner import run_cv_match

    role = application.role
    cv_text = (application.cv_text or "").strip()
    job_spec_text = ((role.job_spec_text if role else None) or "").strip()
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

    # Translate role_criterion rows into RequirementInput. The legacy v4
    # pathway uses integer criterion_ids; v3 uses string ids, so we prefix.
    requirements: list[RequirementInput] = []
    if role is not None:
        for c in sorted(role.criteria or [], key=lambda c: getattr(c, "ordering", 0)):
            if getattr(c, "deleted_at", None) is not None:
                continue
            priority = (
                V3Priority.MUST_HAVE if bool(c.must_have) else V3Priority.STRONG_PREFERENCE
            )
            requirements.append(
                RequirementInput(
                    id=f"crit_{int(c.id)}",
                    requirement=str(c.text or "").strip(),
                    priority=priority,
                )
            )

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
        from .pre_screening_service import application_needs_pre_screen, execute_pre_screen_only

        # Idempotent: re-run Stage 1 when it's never been run OR when
        # the candidate uploaded a newer CV after the last pre-screen.
        # ``application_needs_pre_screen`` already encodes the
        # "stale CV" check (cv_uploaded_at > pre_screen_run_at) used by
        # the manual batch button, so the two entry points stay aligned.
        if application_needs_pre_screen(application):
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
            logger.warning("dynamic pre-screen gate threshold failed; using static", exc_info=True)
        dynamic_threshold = (
            int(dynamic_rec.value)
            if dynamic_rec is not None and dynamic_rec.source == "calibrated"
            else None
        )
        threshold = (
            dynamic_threshold
            if (settings.PRE_SCREEN_DYNAMIC_GATE_ENFORCE and dynamic_threshold is not None)
            else static_threshold
        )
        evidence = application.pre_screen_evidence if isinstance(application.pre_screen_evidence, dict) else {}
        fraud_capped = bool(evidence.get("fraud_capped", False))
        # Gate on the GENUINE pre-screen score from THIS run's evidence — not
        # the shared ``pre_screen_score_100`` column, which a prior full
        # cv_match run may have overwritten. Reading the column filtered
        # candidates the pre-screen actually passed (decision 'yes', llm 75,
        # but the column held a stale 16.7). The effective pre-screen score is
        # the fraud cap when fraud-capped, else the raw LLM score; fall back to
        # the column only when evidence carries no score.
        _llm_score = evidence.get("llm_score_100")
        if fraud_capped:
            gated_score = float(settings.FRAUD_PENALTY_CAP_SCORE)
        elif _llm_score is not None:
            gated_score = float(_llm_score)
        else:
            gated_score = application.pre_screen_score_100
        # Pre-screen errored (Anthropic credit exhaustion, network
        # timeout, JSON parse failure, etc.) — DON'T fall through to v3
        # cv_match. Previously we did, and the v3 score got mirrored
        # into ``pre_screen_score_100`` via the refresh helpers, hiding
        # the error from the recruiter. Now we surface a clear error
        # state and bail; the next sweeper tick (or manual rescore)
        # picks the application back up.
        pre_screen_errored = (
            (evidence.get("decision") == "error")
            or bool(application.pre_screen_error_reason)
        )
        if pre_screen_errored:
            now = datetime.now(timezone.utc)
            reason = (
                application.pre_screen_error_reason
                or evidence.get("summary")
                or "pre_screen_unknown_error"
            )
            job.status = SCORE_JOB_ERROR
            job.error_message = f"pre_screen_errored: {reason}"[:500]
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
                float(gated_score), static_threshold, dynamic_threshold, threshold,
                gated_score < static_threshold,
                (dynamic_threshold is not None and gated_score < dynamic_threshold),
                getattr(dynamic_rec, "source", None),
            )
        # Only filter when we have a numeric score AND it's below threshold.
        if gated_score is not None and gated_score < threshold:
            now = datetime.now(timezone.utc)
            if fraud_capped:
                summary = evidence.get("summary") or (
                    f"Pre-screen filtered: CV contains text copied verbatim "
                    f"from the job description."
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
        )
    else:
        output = run_cv_match(
            cv_text,
            job_spec_text,
            requirements,
            client=org_client,
            metering_context=score_metering_context,
            workable_context=workable_context or None,
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
        from .provider_usage_admission import (
            release_provider_usage,
            reserve_provider_usage,
        )

        cache_reservation = reserve_provider_usage(
            organization_id=int(application.organization_id),
            role_id=int(application.role_id),
            feature=Feature.SCORE,
            trace_id=(
                str(getattr(output, "trace_id", None) or f"score-job:{job.id}")
                + ":cache-hit"
            ),
            entity_id=f"application:{application.id}",
            metadata={"source": "cv_score_cache_fee", "score_job_id": int(job.id)},
        )
        try:
            _meter_record_event(
                db,
                organization_id=int(application.organization_id),
                role_id=int(application.role_id),
                feature=Feature.SCORE,
                model=getattr(output, "model_version", None) or V3_MODEL_VERSION,
                input_tokens=int(getattr(output, "input_tokens", 0) or 0),
                output_tokens=int(getattr(output, "output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(output, "cache_read_tokens", 0) or 0),
                cache_creation_tokens=int(
                    getattr(output, "cache_creation_tokens", 0) or 0
                ),
                cache_hit=True,
                entity_id=f"application:{application.id}",
                metadata={"source": "cv_score_cache_fee", "score_job_id": int(job.id)},
                credit_reservation=cache_reservation.as_metering_payload(),
            )
        except Exception:
            release_provider_usage(
                cache_reservation, reason="cv_score_cache_fee_record_failed"
            )
            raise

    if output.scoring_status == ScoringStatus.FAILED:
        job.status = SCORE_JOB_ERROR
        job.error_message = f"v3_failed: {output.error_reason}"[:500]
        job.finished_at = datetime.now(timezone.utc)
        application.cv_match_score = None
        application.cv_match_details = {
            "error": output.error_reason or "cv_match_v3.0 failed",
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
        details.get("integrity_signals"), application, cv_text, job_spec_text,
        snapshot=details.get("candidate_snapshot"),
        pdf_hygiene=pending_pdf_hygiene if isinstance(pending_pdf_hygiene, dict) else None,
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
        recommendation=getattr(output.recommendation, "value", str(output.recommendation or "")),
        prompt_version=getattr(output, "prompt_version", None) or V3_PROMPT_VERSION,
        model_version=getattr(output, "model_version", None) or V3_MODEL_VERSION,
        trace_id=output.trace_id or f"job-{job.id}",
        cache_hit="hit" if getattr(output, "cache_hit", False) else "miss",
    )


def _record_usage_safe(db: Session, *, organization_id, **kwargs) -> None:
    """Record a usage_events row, swallowing errors. Telemetry must never
    fail a scoring job — if metering is broken, log and continue.

    No-op when organization_id is missing (e.g. legacy applications without
    an org link) — those would constitute orphan usage rows.
    """
    if not organization_id:
        return
    try:
        _meter_record_event(db, organization_id=int(organization_id), **kwargs)
    except Exception:
        logger.exception(
            "usage_metering record_event failed for org=%s feature=%s",
            organization_id, kwargs.get("feature"),
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
    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application_id,
            AgentDecision.status == "pending",
        )
        .all()
    )
    for decision in pending:
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = (
            f"superseded: {reason}; "
            "agent will re-decide once the new score lands"
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
) -> bool:
    """Add a ``status=stale`` CvScoreJob row if no active stale job
    already exists. Returns True if a row was added. Flushes so the
    row is visible to subsequent queries in the same session.
    """
    latest = _latest_job(db, app.id)
    if latest is not None and latest.status == "stale":
        return False
    db.add(
        CvScoreJob(
            application_id=app.id,
            role_id=role_id,
            status="stale",
            queued_at=now,
        )
    )
    db.flush()
    return True


def mark_role_scores_stale(
    db: Session, role_id: int, *, reason: str = "role_intent_changed",
    application_ids: list[int] | None = None,
    dispatch_tech_questions: bool = True,
) -> int:
    """Invalidate every scored application for a role.

    ``application_ids`` (optional) scopes the invalidation to just those
    applications — used by the agent's reasoned criteria change to re-screen
    only the genuinely-affected subset instead of the whole pool. ``None``
    (default) keeps the original role-wide behaviour unchanged.

    Called when the role's must-have / constraint criteria or its job
    spec change (preferred-criteria edits don't trigger because
    pre-screen ignores nice-to-haves). Marks each scored app as stale:
    keeps the numeric score visible so the UI can show "Strong match
    — 87 (stale)" until the rescore lands, enqueues a stale
    CvScoreJob row, and discards any pending agent decisions that
    were based on the old score.

    Returns the number of applications invalidated.
    """
    apps_q = (
        db.query(CandidateApplication)
        .filter(
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
    )
    if application_ids:
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
        if not _enqueue_stale_job(db, app=app, role_id=role_id, now=now):
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
            from .role_tech_questions_service import invalidate as _invalidate_tech_questions
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
                logger.exception("mark_role_scores_stale: failed to dispatch tech_questions regen role_id=%s", role_id)
    except Exception:
        logger.exception("mark_role_scores_stale: tech_questions invalidation failed role_id=%s", role_id)

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
            app.id, app.pipeline_stage, app.application_outcome,
        )
        return False
    now = datetime.now(timezone.utc)
    if not _enqueue_stale_job(db, app=app, role_id=app.role_id, now=now):
        return False
    _clear_application_scores(app)
    supersede_pending_decisions_for_app(db, app.id, reason=reason)
    return True


def _augment_integrity_signals(
    existing: dict | None,
    application: CandidateApplication,
    cv_text: str,
    job_spec_text: str,
    snapshot: dict | None = None,
    pdf_hygiene: dict | None = None,
) -> dict | None:
    """Merge the flag-only cross-source corroboration signals into the score's
    ``integrity_signals`` and triangulate them. Computed here because this is the
    one place with the CV text, the parsed ``cv_sections``, the candidate
    snapshot, the candidate's Workable/social history AND the role JD all in
    scope, so both scoring engines surface them uniformly.

    Layers here are all **$0 / deterministic** and run on every score: JD-shingle
    + CV↔Workable diff + unverified employers (supplementary); years-vs-span
    inflation + tech anachronism (CV-internal coherence); then a triangulation
    summary requiring multiple independent disagreements before "strong_review".

    The **slow** axes — graph collective corroboration and the GitHub URL
    fetch — are deliberately NOT here. They run async + shortlist-gated in
    ``corroboration_enrichment`` (fetching on every score would be the wrong
    placement), and re-triangulate after they land.
    Best-effort — never raises into the scoring path, returns ``existing`` on
    any failure."""
    try:
        from ..platform.config import settings
        from .fraud_detection import (
            aggregate_triangulation,
            build_integrity_warnings,
            build_supplementary_fraud_signals,
            detect_experience_inflation,
            detect_tech_anachronism,
        )

        cand = getattr(application, "candidate", None)
        cv_sections = (
            getattr(application, "cv_sections", None)
            or (getattr(cand, "cv_sections", None) if cand is not None else None)
            or {}
        )
        cv_exp = cv_sections.get("experience") if isinstance(cv_sections, dict) else None
        wk_exp = getattr(cand, "experience_entries", None) if cand is not None else None
        supp = build_supplementary_fraud_signals(
            cv_text=cv_text or "",
            jd_text=job_spec_text or "",
            cv_experience=cv_exp,
            workable_experience=wk_exp,
            shingle_threshold=settings.FRAUD_SHINGLE_THRESHOLD,
            workable_diff_enabled=settings.FRAUD_WORKABLE_DIFF_ENABLED,
        )
        merged = dict(existing or {})
        merged.update(supp)

        # CV-internal coherence (deterministic, flag-only).
        snap = snapshot if isinstance(snapshot, dict) else {}
        timeline = snap.get("timeline") or []
        # Feed the FULL parsed CV history alongside the snapshot timeline (which
        # is capped at the 5 most-recent employers). Without the full list a
        # candidate with >5 jobs has their oldest roles dropped, so the evidenced
        # span looks short and they're wrongly flagged for "inflating" their years.
        infl = detect_experience_inflation(
            snap.get("years_experience"),
            list(timeline) + list(cv_exp or []),
        )
        if infl.triggered:
            merged["experience_inflation"] = infl.to_dict()
        anach = detect_tech_anachronism(cv_exp)
        if anach.triggered:
            merged["tech_anachronism"] = anach.to_dict()

        # Promote the ingest-time PDF-bytes hygiene scan (flag-only) under
        # document_hygiene.pdf, preserving the LLM-path text hygiene already there.
        if isinstance(pdf_hygiene, dict):
            dh = dict(merged.get("document_hygiene") or {})
            dh["pdf"] = pdf_hygiene
            merged["document_hygiene"] = dh

        # Triangulate the deterministic picture — changes no score, adds the
        # verdict + trust band the report reads (and the gate the async
        # enrichment keys off — only flagged high-matches get an enrichment pass).
        merged["triangulation"] = aggregate_triangulation(merged)
        merged["warnings"] = build_integrity_warnings(merged)
        return merged or None
    except Exception:  # pragma: no cover — never break scoring on a flag
        logger.debug("supplementary fraud signals failed", exc_info=True)
        return existing
