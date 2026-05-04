"""Bulk CV-match runner via the Anthropic Message Batches API.

Use this when you need to score many (CV × JD) pairs at once and don't
need each result back synchronously — e.g. recruiter posts a job and the
system rescores every existing candidate against it. Output is identical
to ``runner.run_cv_match`` (same ``CVMatchOutput`` schema, same cache,
same telemetry shape) but at **50% of standard pricing** plus the usual
prompt-caching discount on the static role block.

Three entry points:

* :func:`submit_cv_match_batch` — render prompts, write cache hits
  through, submit the rest, return immediately with a
  :class:`BatchSubmission` cursor. Cheap.
* :func:`retrieve_cv_match_batch` — given a cursor, return
  ``(status, results)``. ``status == "ended"`` means every request
  finished. Safe to call repeatedly.
* :func:`run_cv_match_batch` — submit + poll + retrieve in one blocking
  call. Convenience wrapper over the two above.

The batch path reuses the *same* prompt template, validation, aggregation,
calibration, and DB cache as the sync runner. Only the API surface differs:
synchronous ``messages.create`` vs asynchronous ``messages.batches``.

Failures inside a batch (one request fails validation, the API returns an
error, etc.) do **not** abort the rest. Each job lands in the result map
with ``scoring_status=FAILED`` and an ``error_reason`` — the caller can
re-batch the failures or surface them in the UI.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Literal

from . import MODEL_VERSION, PROMPT_VERSION
from .aggregation import aggregate
from .prompts import build_cv_match_messages
from .runner import (
    INPUT_TOKEN_CEILING,
    OUTPUT_TOKEN_CEILING,
    TEMPERATURE,
    _SYSTEM_PROMPT,
    _count_input_tokens,
    _failed_output,
    _hash_text,
    _parse_and_validate,
    _resolve_anthropic_client,
    _RunContext,
)
from .schemas import (
    CVMatchOutput,
    RequirementInput,
    ScoringStatus,
)
from .validation import (
    ValidationFailure,
    check_suspicious_score,
    scan_for_injection,
)

logger = logging.getLogger("taali.cv_match.runner_batch")

# Anthropic enforces a 100K-request hard limit per batch. We chunk well below
# that so a single batch finishes faster (typical SLA is "most batches end
# within 1 hour" but smaller batches end sooner).
DEFAULT_BATCH_CHUNK = 1000


@dataclass
class BatchJob:
    """One (CV × JD) pair to score in a batch."""

    custom_id: str
    cv_text: str
    jd_text: str
    requirements: list[RequirementInput] = field(default_factory=list)


@dataclass
class BatchSubmission:
    """Cursor returned by :func:`submit_cv_match_batch`.

    Pass this to :func:`retrieve_cv_match_batch` to fetch results once
    Anthropic finishes processing.
    """

    batch_id: str | None  # None when every job hit the local cache
    submitted_custom_ids: list[str]
    cached_results: dict[str, CVMatchOutput]
    cache_keys: dict[str, str]
    # Archetype rubric per unique JD hash, captured at submit time so the
    # retrieval phase can run aggregation with the same dimension weights.
    archetype_weights_by_jd: dict[str, dict[str, float]]
    archetype_id_by_jd: dict[str, str]
    # Original job inputs keyed by custom_id, needed for evidence-grounding
    # validation and cache write-through during retrieval.
    jobs_by_custom_id: dict[str, BatchJob]
    submitted_at: float


BatchStatus = Literal["in_progress", "ended"]


def submit_cv_match_batch(
    jobs: Iterable[BatchJob],
    *,
    client=None,
    skip_cache: bool = False,
) -> BatchSubmission:
    """Render prompts and submit the un-cached jobs to the Batches API.

    Local DB cache is consulted first; hits are returned immediately in
    ``submission.cached_results`` and never touch the API. Misses are
    bundled into one batch request.
    """
    job_list = list(jobs)
    if not job_list:
        return BatchSubmission(
            batch_id=None,
            submitted_custom_ids=[],
            cached_results={},
            cache_keys={},
            archetype_weights_by_jd={},
            archetype_id_by_jd={},
            jobs_by_custom_id={},
            submitted_at=time.time(),
        )

    # De-duplicate custom_ids — Anthropic rejects batches with duplicates.
    seen_ids: set[str] = set()
    for job in job_list:
        if job.custom_id in seen_ids:
            raise ValueError(
                f"Duplicate custom_id in batch submission: {job.custom_id!r}"
            )
        seen_ids.add(job.custom_id)

    if client is None:
        client = _resolve_anthropic_client()

    from . import cache as cache_module

    cached_results: dict[str, CVMatchOutput] = {}
    cache_keys: dict[str, str] = {}
    requests_payload: list[dict] = []
    archetype_weights_by_jd: dict[str, dict[str, float]] = {}
    archetype_id_by_jd: dict[str, str] = {}
    jobs_by_custom_id: dict[str, BatchJob] = {}
    archetype_cache: dict[str, object] = {}  # jd_hash -> ArchetypeRubric|None

    for job in job_list:
        jobs_by_custom_id[job.custom_id] = job
        cache_key = cache_module.compute_cache_key(
            cv_text=job.cv_text,
            jd_text=job.jd_text,
            requirements=job.requirements,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
        cache_keys[job.custom_id] = cache_key

        if not skip_cache:
            hit = cache_module.get(cache_key)
            if hit is not None:
                cached_results[job.custom_id] = hit.model_copy(
                    update={"cache_hit": True}
                )
                continue

        # Archetype synthesis: cache per unique JD so we don't pay the
        # ~$0.05 Sonnet call once per CV in the batch.
        jd_hash = _hash_text(job.jd_text)
        if jd_hash not in archetype_cache:
            try:
                from .archetype_synthesizer import synthesize_archetype

                archetype_cache[jd_hash] = synthesize_archetype(
                    job.jd_text, job.requirements, client=client
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "Archetype synthesis failed for jd_hash=%s; proceeding without: %s",
                    jd_hash,
                    exc,
                )
                archetype_cache[jd_hash] = None
        archetype = archetype_cache[jd_hash]
        if archetype is not None:
            archetype_weights_by_jd[jd_hash] = archetype.normalised_dimension_weights()
            archetype_id_by_jd[jd_hash] = archetype.archetype_id

        try:
            messages = build_cv_match_messages(
                job.cv_text,
                job.jd_text,
                job.requirements,
                archetype=archetype,
                prompt_version=PROMPT_VERSION,
            )
        except Exception as exc:
            logger.warning(
                "Failed to render prompt for custom_id=%s: %s", job.custom_id, exc
            )
            ctx = _RunContext(
                trace_id=str(uuid.uuid4()),
                cv_hash=_hash_text(job.cv_text),
                jd_hash=jd_hash,
                started_at=time.monotonic(),
            )
            cached_results[job.custom_id] = _failed_output(
                error_reason=f"prompt_render_failed: {exc}", ctx=ctx
            )
            continue

        counted_in = _count_input_tokens(messages, _SYSTEM_PROMPT)
        if counted_in > INPUT_TOKEN_CEILING:
            ctx = _RunContext(
                trace_id=str(uuid.uuid4()),
                cv_hash=_hash_text(job.cv_text),
                jd_hash=jd_hash,
                started_at=time.monotonic(),
            )
            cached_results[job.custom_id] = _failed_output(
                error_reason=(
                    f"input_token_ceiling_exceeded: counted={counted_in}, "
                    f"ceiling={INPUT_TOKEN_CEILING}"
                ),
                ctx=ctx,
            )
            continue

        requests_payload.append(
            {
                "custom_id": job.custom_id,
                "params": {
                    "model": MODEL_VERSION,
                    "max_tokens": OUTPUT_TOKEN_CEILING,
                    "temperature": TEMPERATURE,
                    "system": _SYSTEM_PROMPT,
                    "messages": messages,
                },
            }
        )

    submitted_ids = [r["custom_id"] for r in requests_payload]

    if not requests_payload:
        # Every job hit the cache.
        return BatchSubmission(
            batch_id=None,
            submitted_custom_ids=[],
            cached_results=cached_results,
            cache_keys=cache_keys,
            archetype_weights_by_jd=archetype_weights_by_jd,
            archetype_id_by_jd=archetype_id_by_jd,
            jobs_by_custom_id=jobs_by_custom_id,
            submitted_at=time.time(),
        )

    batch = client.messages.batches.create(requests=requests_payload)
    logger.info(
        "Submitted CV-match batch: id=%s submitted=%d cached=%d",
        batch.id,
        len(submitted_ids),
        len(cached_results),
    )

    return BatchSubmission(
        batch_id=batch.id,
        submitted_custom_ids=submitted_ids,
        cached_results=cached_results,
        cache_keys=cache_keys,
        archetype_weights_by_jd=archetype_weights_by_jd,
        archetype_id_by_jd=archetype_id_by_jd,
        jobs_by_custom_id=jobs_by_custom_id,
        submitted_at=time.time(),
    )


def retrieve_cv_match_batch(
    submission: BatchSubmission,
    *,
    client=None,
    skip_cache: bool = False,
) -> tuple[BatchStatus, dict[str, CVMatchOutput]]:
    """Poll batch status; if ended, parse + validate + persist every result.

    Returns ``(status, results)``. When ``status == "in_progress"``,
    ``results`` contains only the locally-cached hits captured at submit
    time. When ``status == "ended"``, ``results`` is the full output map
    keyed by ``custom_id`` (cached + just-completed). Safe to call
    repeatedly — re-fetching ``ended`` batches is idempotent.
    """
    if submission.batch_id is None:
        # Cache-only submission — everything is already in cached_results.
        return "ended", dict(submission.cached_results)

    if client is None:
        client = _resolve_anthropic_client()

    batch = client.messages.batches.retrieve(submission.batch_id)
    if batch.processing_status != "ended":
        return "in_progress", dict(submission.cached_results)

    from . import cache as cache_module

    results: dict[str, CVMatchOutput] = dict(submission.cached_results)

    for entry in client.messages.batches.results(submission.batch_id):
        custom_id = entry.custom_id
        job = submission.jobs_by_custom_id.get(custom_id)
        if job is None:
            logger.warning(
                "Batch result for unknown custom_id=%s — skipping", custom_id
            )
            continue

        ctx = _RunContext(
            trace_id=str(uuid.uuid4()),
            cv_hash=_hash_text(job.cv_text),
            jd_hash=_hash_text(job.jd_text),
            started_at=time.monotonic(),
        )

        result_type = entry.result.type
        if result_type != "succeeded":
            err = "batch_request_errored"
            try:
                err = f"batch_{result_type}: {entry.result.error.type}"  # type: ignore[attr-defined]
            except AttributeError:
                pass
            results[custom_id] = _failed_output(error_reason=err, ctx=ctx)
            continue

        message = entry.result.message
        usage = getattr(message, "usage", None)
        if usage is not None:
            ctx.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            ctx.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            ctx.cache_read_tokens = int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            )
            ctx.cache_creation_tokens = int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )

        raw_text = ""
        try:
            raw_text = message.content[0].text  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            raw_text = ""

        try:
            parsed = _parse_and_validate(raw_text, job.cv_text, job.requirements)
        except ValidationFailure as exc:
            logger.warning(
                "Batch result validation failed for custom_id=%s: %s", custom_id, exc
            )
            results[custom_id] = _failed_output(
                error_reason=f"validation_failed: {exc}", ctx=ctx
            )
            continue

        jd_hash = ctx.jd_hash
        archetype_weights = submission.archetype_weights_by_jd.get(jd_hash)
        archetype_id = submission.archetype_id_by_jd.get(jd_hash)

        (
            skills_match,
            experience_relevance,
            req_match,
            cv_fit,
            role_fit,
        ) = aggregate(
            dimension_scores=parsed.dimension_scores,
            assessments=parsed.requirements_assessment,
            archetype_weights=archetype_weights,
        )

        calibrated_p_advance = None
        if archetype_id is not None:
            try:
                from .calibrators import apply_calibrator

                calibrated_p_advance = apply_calibrator(
                    role_family=archetype_id,
                    dimension="role_fit",
                    raw_score=role_fit,
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("Calibrator lookup failed: %s", exc)

        output = CVMatchOutput(
            prompt_version=PROMPT_VERSION,
            skills_match_score=skills_match,
            experience_relevance_score=experience_relevance,
            dimension_scores=parsed.dimension_scores,
            requirements_assessment=parsed.requirements_assessment,
            matching_skills=parsed.matching_skills,
            missing_skills=parsed.missing_skills,
            experience_highlights=parsed.experience_highlights,
            concerns=parsed.concerns,
            summary=parsed.summary,
            requirements_match_score=req_match,
            cv_fit_score=cv_fit,
            role_fit_score=role_fit,
            injection_suspected=scan_for_injection(job.cv_text),
            suspicious_score=check_suspicious_score(
                requirements_match_score=req_match, cv_text=job.cv_text
            ),
            scoring_status=ScoringStatus.OK,
            error_reason="",
            model_version=MODEL_VERSION,
            trace_id=ctx.trace_id,
            calibrated_p_advance=calibrated_p_advance,
            input_tokens=ctx.input_tokens,
            output_tokens=ctx.output_tokens,
            cache_read_tokens=ctx.cache_read_tokens,
            cache_creation_tokens=ctx.cache_creation_tokens,
        )

        if not skip_cache:
            cache_key = submission.cache_keys.get(custom_id)
            if cache_key:
                try:
                    cache_module.set(cache_key, output)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "Cache write failed (batch) for custom_id=%s: %s",
                        custom_id,
                        exc,
                    )

        results[custom_id] = output

    return "ended", results


def run_cv_match_batch(
    jobs: Iterable[BatchJob],
    *,
    client=None,
    skip_cache: bool = False,
    poll_interval: float = 60.0,
    timeout: float | None = 24 * 60 * 60.0,
) -> dict[str, CVMatchOutput]:
    """Submit + block-poll + retrieve. Returns the full results map.

    ``poll_interval`` is the wait between status checks (default 60s).
    ``timeout`` caps total wait (default 24h, matching the API SLA).
    Returns whatever results are available if the timeout fires before
    the batch ends — caller should inspect ``scoring_status`` per entry.
    """
    if client is None:
        client = _resolve_anthropic_client()

    submission = submit_cv_match_batch(jobs, client=client, skip_cache=skip_cache)
    if submission.batch_id is None:
        return submission.cached_results

    deadline = (time.monotonic() + timeout) if timeout else None
    while True:
        status, results = retrieve_cv_match_batch(
            submission, client=client, skip_cache=skip_cache
        )
        if status == "ended":
            return results
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Batch %s exceeded timeout; returning partial results",
                submission.batch_id,
            )
            return results
        time.sleep(poll_interval)
