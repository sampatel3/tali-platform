"""Async + cached CV scoring orchestration.

Replaces the synchronous in-request Claude calls in
``applications_routes._compute_cv_match_for_application``. The flow is:

  enqueue_score(application)
      → creates a CvScoreJob row in `pending`
      → if MVP_DISABLE_CELERY (tests, dev), runs the task inline
      → otherwise dispatches the Celery task

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

from sqlalchemy import desc
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
from .fit_matching_service import (
    CV_MATCH_V4_PROMPT_VERSION,
    CvMatchValidationError,
    calculate_cv_job_match_sync,
    calculate_cv_job_match_v4_sync,
)
from .spec_normalizer import normalize_spec

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
    """Hash the v4 (or v3) inputs into a deterministic cache key."""
    payload = {
        "cv": cv_text or "",
        "spec_description": spec_description or "",
        "spec_requirements": spec_requirements or "",
        "criteria": [
            {
                "id": int(c["id"]),
                "text": str(c.get("text") or ""),
                "must_have": bool(c.get("must_have")),
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
) -> CvScoreJob | None:
    """Queue a CV score for an application.

    Returns the new job, or the existing active job if one is already
    pending/running and ``force`` is False. Returns ``None`` when the
    application can't be scored (no CV, no spec, no API key).
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

    if settings.MVP_DISABLE_CELERY:
        # Run inline so unit/dev environments don't need a broker. The job
        # object is mutated in place by _execute_scoring; the caller commits.
        _run_score_job_inline(db, job_id=job.id)
    else:
        from ..tasks.scoring_tasks import score_application_job

        async_result = score_application_job.delay(application.id)
        job.celery_task_id = str(async_result.id)
    return job


def _run_score_job_inline(db: Session, *, job_id: int) -> None:
    """Run a job within the current request's session (used when Celery is disabled).

    Mirrors the Celery task body but reuses the active ``db`` so the work
    participates in the request's transaction.
    """
    job = db.query(CvScoreJob).filter(CvScoreJob.id == job_id).first()
    if job is None:
        return
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == job.application_id)
        .first()
    )
    if application is None:
        job.status = SCORE_JOB_ERROR
        job.error_message = "application_not_found"
        job.finished_at = datetime.now(timezone.utc)
        return
    _execute_scoring(db, application=application, job=job)


def _execute_scoring(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
) -> None:
    """Run the scoring pipeline for one application + job pair.

    Updates ``application.cv_match_score`` / ``cv_match_details`` and the
    ``job`` row in place. Call inside a session that the caller will commit.

    Branches on ``settings.USE_CV_MATCH_V3``: when True, routes to the
    cv_match_v3.0 module for grounded, retried, schema-validated scoring.
    """
    if settings.USE_CV_MATCH_V3:
        _execute_scoring_v3(db, application=application, job=job)
        return

    role = application.role
    cv_text = (application.cv_text or "").strip()
    job_spec_text = ((role.job_spec_text if role else None) or "").strip()
    job.started_at = datetime.now(timezone.utc)
    job.status = SCORE_JOB_RUNNING

    if not cv_text or not job_spec_text:
        job.status = SCORE_JOB_ERROR
        job.error_message = "missing_inputs"
        job.finished_at = datetime.now(timezone.utc)
        application.cv_match_score = None
        application.cv_match_details = {"error": "Missing CV or job spec text"}
        application.cv_match_scored_at = None
        return

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        job.status = SCORE_JOB_ERROR
        job.error_message = "missing_api_key"
        job.finished_at = datetime.now(timezone.utc)
        application.cv_match_details = {"error": "CV match unavailable: Anthropic API key is not configured"}
        return

    criteria = _criteria_payload(role)
    spec = normalize_spec(job_spec_text)
    use_v4 = bool(criteria)
    prompt_version = CV_MATCH_V4_PROMPT_VERSION if use_v4 else _V3_PROMPT_VERSION
    resolved_model = (settings.resolved_claude_scoring_model or "").strip()

    cache_key = compute_cache_key(
        cv_text=cv_text,
        spec_description=spec.description,
        spec_requirements=spec.requirements,
        criteria=criteria,
        prompt_version=prompt_version,
        model=resolved_model,
    )
    job.cache_key = cache_key
    job.prompt_version = prompt_version
    job.model = resolved_model

    cached = get_cached_result(db, cache_key)
    if cached is not None:
        cached.hit_count = (cached.hit_count or 0) + 1
        cached.last_hit_at = datetime.now(timezone.utc)
        match_details = dict(cached.result or {})
        normalized_score = cached.score_100
        job.cache_hit = "hit"
    else:
        job.cache_hit = "miss"
        try:
            if use_v4:
                result = calculate_cv_job_match_v4_sync(
                    cv_text=cv_text,
                    role_criteria=criteria,
                    spec_description=spec.description,
                    spec_requirements=spec.requirements,
                    api_key=api_key,
                    model=resolved_model,
                )
            else:
                result = calculate_cv_job_match_sync(
                    cv_text=cv_text,
                    job_spec_text=job_spec_text,
                    api_key=api_key,
                    model=resolved_model,
                    additional_requirements=(role.additional_requirements or "").strip() or None,
                )
        except CvMatchValidationError as exc:
            job.status = SCORE_JOB_ERROR
            job.error_message = f"validation_failed: {exc.reason}"
            job.finished_at = datetime.now(timezone.utc)
            application.cv_match_details = {
                "error": f"CV match validation failed: {exc.reason}",
                "scoring_version": prompt_version,
            }
            application.cv_match_scored_at = None
            return
        except Exception as exc:  # pragma: no cover - upstream client errors
            job.status = SCORE_JOB_ERROR
            job.error_message = f"upstream_error: {exc}"
            job.finished_at = datetime.now(timezone.utc)
            application.cv_match_details = {
                "error": str(exc)[:300],
                "scoring_version": prompt_version,
            }
            application.cv_match_scored_at = None
            return

        match_details = result.get("match_details", {}) if isinstance(result, dict) else {}
        normalized_score = result.get("cv_job_match_score") if isinstance(result, dict) else None
        store_cached_result(
            db,
            cache_key=cache_key,
            prompt_version=prompt_version,
            model=resolved_model,
            score_100=normalized_score,
            result=match_details,
        )

    application.cv_match_score = normalized_score
    application.cv_match_details = match_details or None
    application.cv_match_scored_at = datetime.now(timezone.utc)
    job.status = SCORE_JOB_DONE
    job.finished_at = datetime.now(timezone.utc)


def _execute_scoring_v3(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
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

    output = run_cv_match(cv_text, job_spec_text, requirements)
    job.cache_hit = "hit" if output.trace_id and getattr(output, "trace_id", "") else "miss"

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


def mark_role_scores_stale(db: Session, role_id: int) -> int:
    """Flag every application for a role as needing rescoring.

    Called when the role's criteria or job spec change. Adds a stale row to
    each application's job history so the UI shows a "needs rescore" badge.
    Returns the number of applications marked.
    """
    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_score.isnot(None),
        )
        .all()
    )
    marked = 0
    now = datetime.now(timezone.utc)
    for app in apps:
        latest = _latest_job(db, app.id)
        if latest is not None and latest.status == "stale":
            continue
        db.add(
            CvScoreJob(
                application_id=app.id,
                role_id=role_id,
                status="stale",
                queued_at=now,
            )
        )
        marked += 1
    return marked
