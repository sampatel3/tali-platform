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

from sqlalchemy import desc, or_
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
from ..platform.config import settings
from ..domains.assessments_runtime.pipeline_service import append_application_event
from .fit_matching_service import (
    CV_MATCH_V4_PROMPT_VERSION,
    CvMatchValidationError,
    calculate_cv_job_match_sync,
    calculate_cv_job_match_v4_sync,
)
from .claude_client_resolver import get_client_for_org as _resolve_anthropic_client
from .pricing_service import Feature
from .spec_normalizer import normalize_spec
from .usage_metering_service import (
    InsufficientCreditsError,
    record_event as _meter_record_event,
    reserve as _meter_reserve,
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


def enqueue_score(
    db: Session,
    application: CandidateApplication,
    *,
    force: bool = False,
    bypass_pre_screen: bool = False,
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

    # Pre-flight credit gate. In shadow mode (USAGE_METER_LIVE=False) this
    # is a no-op. In live mode, orgs without enough balance get a silent
    # skip — the caller (batch loops or single-app routes) sees None and
    # can render the 402 surface separately if needed.
    try:
        _meter_reserve(
            db,
            organization_id=int(getattr(application, "organization_id", 0) or 0) or None,
            feature=Feature.SCORE,
        )
    except InsufficientCreditsError:
        logger.info(
            "enqueue_score skipped for application=%s: insufficient credits",
            application.id,
        )
        return None
    except Exception:  # pragma: no cover — defensive, never block scoring on metering
        logger.exception(
            "enqueue_score reserve check failed for application=%s",
            application.id,
        )

    # Universal role-level monthly USD cap. When the recruiter has set a
    # cap (typically because they activated agentic mode and chose a
    # budget), every Anthropic call on this role — scoring included —
    # must check it before spending. Skipped scores show up in the
    # scoring batch's "skipped" tally; the agent's monthly budget guard
    # surfaces the same paused state on the bar.
    try:
        from .role_budget_gate import can_spend_on_role

        if not can_spend_on_role(db, role=role):
            logger.info(
                "enqueue_score skipped for application=%s: role monthly cap reached (role_id=%s)",
                application.id,
                role.id,
            )
            return None
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "role_budget_gate check failed for application=%s — proceeding",
            application.id,
        )

    if not force:
        existing = _latest_job(db, application.id)
        if existing is not None and existing.status in {SCORE_JOB_PENDING, SCORE_JOB_RUNNING}:
            return existing

    job = CvScoreJob(
        application_id=application.id,
        role_id=application.role_id,
        status=SCORE_JOB_PENDING,
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

    async_result = score_application_job.delay(
        application.id,
        job_id=int(job.id),
        force_full_score=bypass_pre_screen,
    )
    job.celery_task_id = str(async_result.id)
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

        gated_score = application.pre_screen_score_100
        threshold = settings.PRE_SCREEN_THRESHOLD
        evidence = application.pre_screen_evidence if isinstance(application.pre_screen_evidence, dict) else {}
        fraud_capped = bool(evidence.get("fraud_capped", False))
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
            # in via a different path.
            application.cv_match_score = None
            application.cv_match_scored_at = None
            return
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

    # Archetype synthesis uses Sonnet and is metered separately by the
    # wrapper using ``metering_context``. The score call itself is metered
    # by ``_record_usage_safe`` below — the runner sets metering={skip}.
    archetype_metering_context = {
        "organization_id": getattr(application, "organization_id", None),
        "role_id": getattr(application, "role_id", None),
        "entity_id": f"application:{application.id}",
        "db": db,
    }
    output = run_cv_match(
        cv_text,
        job_spec_text,
        requirements,
        client=org_client,
        metering_context=archetype_metering_context,
    )
    job.cache_hit = "hit" if getattr(output, "cache_hit", False) else "miss"
    _record_usage_safe(
        db,
        organization_id=getattr(application, "organization_id", None),
        role_id=getattr(application, "role_id", None),
        feature=Feature.SCORE,
        model=V3_MODEL_VERSION,
        input_tokens=int(getattr(output, "input_tokens", 0) or 0),
        output_tokens=int(getattr(output, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(output, "cache_read_tokens", 0) or 0),
        cache_creation_tokens=int(getattr(output, "cache_creation_tokens", 0) or 0),
        cache_hit=bool(getattr(output, "cache_hit", False)),
        entity_id=f"application:{application.id}",
    )

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
    application.cv_match_details = output.model_dump(mode="json")
    application.cv_match_scored_at = datetime.now(timezone.utc)
    job.status = SCORE_JOB_DONE
    job.finished_at = datetime.now(timezone.utc)
    _emit_cv_scored_event(
        db,
        application=application,
        job=job,
        score_100=output.role_fit_score,
        recommendation=getattr(output.recommendation, "value", str(output.recommendation or "")),
        prompt_version=V3_PROMPT_VERSION,
        model_version=V3_MODEL_VERSION,
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
    """NULL out every score-related field on an application.

    Used by every invalidation path (role intent change, candidate
    input change, error recovery) so the UI surfaces "needs rescore"
    instead of a stale numeric score that no longer reflects the
    agent's current view of the candidate.

    Keeps ``pre_screen_run_at`` (audit: when did pre-screen last
    attempt) and the existing ``pre_screen_evidence`` blob (audit:
    what was the last attempt's reasoning). Surfacing logic in the
    UI / queues should check ``pre_screen_score_100 IS NULL`` to
    detect "needs rescore" rather than reading ``pre_screen_evidence``.
    """
    app.pre_screen_score_100 = None
    app.requirements_fit_score_100 = None
    app.cv_match_score = None
    app.cv_match_details = None
    app.cv_match_scored_at = None
    app.pre_screen_recommendation = None
    # Rank falls back to workable_score so the directory still has
    # *some* ordering signal during the rescore window.
    app.rank_score = app.workable_score


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


def mark_role_scores_stale(db: Session, role_id: int) -> int:
    """Invalidate every scored application for a role.

    Called when the role's must-have / constraint criteria or its job
    spec change (preferred-criteria edits don't trigger because
    pre-screen ignores nice-to-haves). NULLs every score field on
    affected applications AND enqueues a stale CvScoreJob row so the
    background worker picks them back up. Returns the number of
    applications invalidated.

    The score fields are cleared (not just job-row-tagged) because the
    UI's "Strong match — 87" is the recruiter's primary signal; a
    stale numeric is worse than no numeric until rescore lands.
    """
    apps = (
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
        .all()
    )
    marked = 0
    now = datetime.now(timezone.utc)
    for app in apps:
        if not _enqueue_stale_job(db, app=app, role_id=role_id, now=now):
            continue
        _clear_application_scores(app)
        marked += 1
    return marked


def mark_application_scores_stale(db: Session, application_id: int) -> bool:
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
    now = datetime.now(timezone.utc)
    if not _enqueue_stale_job(db, app=app, role_id=app.role_id, now=now):
        return False
    _clear_application_scores(app)
    return True
