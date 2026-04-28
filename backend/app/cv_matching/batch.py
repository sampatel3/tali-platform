"""Anthropic Message Batches API path for offline CV match jobs.

Use cases:
- Overnight rescoring of an entire pipeline
- Bulk re-rank when role criteria change
- Candidate-database re-indexing

Batches give a 50% discount on input + output tokens (Anthropic pricing
docs) and stack with prompt caching, so this is the cheapest CV-match
path at scale. Latency is hours, not seconds — never use this on the
recruiter request path.

Public surface:

    submit_batch(items)   -> batch_id
    poll_batch(batch_id)  -> {custom_id: CVMatchOutput | CVMatchOutputV4}
    run_batch(items)      -> {custom_id: ...}   # convenience (submit + poll)

SDK requirement: ``anthropic >= 0.40`` for ``client.messages.batches``.
The current pinned version (0.34.0) lacks this API. Calling
``submit_batch`` on an older SDK raises a clear error rather than
silently degrading. The SDK upgrade is its own follow-up — see the
v4 migration handover for the rollout sequence.

Tests inject a stub ``client`` to exercise the prompt-building and
result-parsing logic without depending on the real SDK.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from . import MODEL_VERSION
from .runner import (
    PipelineVersion,
    _PipelineConfig,
    _V3_CONFIG,
    _V4_1_CONFIG,
    _strip_json_fences,
    TEMPERATURE,
)
from .schemas import RequirementInput
from .validation import ValidationFailure

logger = logging.getLogger("taali.cv_match.batch")


@dataclass
class BatchMatchInput:
    """One CV match request inside a batch.

    ``custom_id`` is opaque — the caller uses it to map results back to
    their domain models (typically ``application_id`` cast to str).
    Anthropic returns results keyed on this id.
    """

    custom_id: str
    cv_text: str
    jd_text: str
    requirements: list[RequirementInput] | None = None
    version: PipelineVersion = "v4.1"


def _config_for(version: PipelineVersion) -> _PipelineConfig:
    if version == "v3":
        return _V3_CONFIG
    if version == "v4.1":
        return _V4_1_CONFIG
    raise ValueError(f"Unknown batch pipeline version: {version!r}")


def _build_request(item: BatchMatchInput) -> dict[str, Any]:
    """Translate a BatchMatchInput into one Anthropic batch request dict.

    The structure follows the Anthropic Message Batches API:

        {
          "custom_id": "<opaque>",
          "params": {
            "model": "...",
            "max_tokens": int,
            "system": "...",
            "messages": [{"role": "user", "content": "..."}]
          }
        }
    """
    cfg = _config_for(item.version)
    prompt = cfg.build_prompt(
        item.cv_text, item.jd_text, item.requirements or []
    )
    return {
        "custom_id": item.custom_id,
        "params": {
            "model": MODEL_VERSION,
            "max_tokens": cfg.output_token_ceiling,
            "temperature": TEMPERATURE,
            "system": "You are an expert recruiter. Respond ONLY with valid JSON.",
            "messages": [{"role": "user", "content": prompt}],
        },
    }


def _resolve_batches_client(client: Any | None):
    """Return the ``messages.batches`` SDK surface or raise a clear error.

    Tests inject a fake client with a ``messages.batches`` attribute and
    skip the SDK version check.
    """
    if client is None:
        from .runner import _resolve_anthropic_client

        client = _resolve_anthropic_client()
    batches = getattr(getattr(client, "messages", None), "batches", None)
    if batches is None:
        raise RuntimeError(
            "anthropic SDK does not expose messages.batches. "
            "Upgrade to anthropic>=0.40 to use the Batch API."
        )
    return batches, client


def submit_batch(
    items: list[BatchMatchInput],
    *,
    client: Any | None = None,
) -> str:
    """Submit a batch of CV match requests. Returns Anthropic batch_id.

    ``items`` may mix versions — each request carries its own version,
    prompt, and max_tokens. The whole batch posts in a single API call.
    """
    if not items:
        raise ValueError("submit_batch requires at least one item")

    batches, _client = _resolve_batches_client(client)
    requests = [_build_request(it) for it in items]
    response = batches.create(requests=requests)

    batch_id = getattr(response, "id", None) or response["id"]
    logger.info("Submitted batch id=%s with %d items", batch_id, len(requests))
    return str(batch_id)


def _parse_one_result(
    item: BatchMatchInput,
    raw_text: str,
):
    """Parse one batch response body into a populated output object.

    Mirrors the runner's parse + validate + aggregate pipeline so batch
    results are interchangeable with synchronous run_cv_match outputs.
    Failed parses are returned as a ``cfg.output_schema`` with
    ``scoring_status=FAILED`` so the caller can iterate without raising.
    """
    cfg = _config_for(item.version)
    text = _strip_json_fences(raw_text)
    try:
        parsed_blob = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Batch result %s: JSON parse failed: %s", item.custom_id, exc)
        return _failed_batch_output(cfg, error_reason=f"json_parse_failed: {exc}")

    try:
        result = cfg.result_schema.model_validate(parsed_blob)
    except ValidationError as exc:
        logger.warning(
            "Batch result %s failed schema: %s", item.custom_id, exc
        )
        return _failed_batch_output(cfg, error_reason=f"schema_failed: {exc}")

    cfg.validate_grounding(result, item.cv_text)
    try:
        cfg.validate_consistency(result, item.requirements or [])
    except ValidationFailure as exc:
        return _failed_batch_output(cfg, error_reason=f"consistency_failed: {exc}")

    from .aggregation import aggregate
    from .schemas import ScoringStatus
    from .validation import check_suspicious_score, scan_for_injection

    req_match, cv_fit, role_fit, recommendation = aggregate(
        skills_match_score=result.skills_match_score,
        experience_relevance_score=result.experience_relevance_score,
        assessments=result.requirements_assessment,
    )
    return cfg.output_schema(
        prompt_version=cfg.prompt_version,
        skills_match_score=result.skills_match_score,
        experience_relevance_score=result.experience_relevance_score,
        requirements_assessment=result.requirements_assessment,
        matching_skills=result.matching_skills,
        missing_skills=result.missing_skills,
        experience_highlights=result.experience_highlights,
        concerns=result.concerns,
        summary=result.summary,
        requirements_match_score=req_match,
        cv_fit_score=cv_fit,
        role_fit_score=role_fit,
        recommendation=recommendation,
        injection_suspected=scan_for_injection(item.cv_text),
        suspicious_score=check_suspicious_score(
            requirements_match_score=req_match, cv_text=item.cv_text
        ),
        scoring_status=ScoringStatus.OK,
        error_reason="",
        model_version=MODEL_VERSION,
        trace_id=str(uuid.uuid4()),
    )


def _failed_batch_output(cfg: _PipelineConfig, *, error_reason: str):
    from .schemas import ScoringStatus

    return cfg.output_schema(
        prompt_version=cfg.prompt_version,
        skills_match_score=0.0,
        experience_relevance_score=0.0,
        requirements_assessment=[],
        matching_skills=[],
        missing_skills=[],
        experience_highlights=[],
        concerns=[],
        summary="",
        requirements_match_score=0.0,
        cv_fit_score=0.0,
        role_fit_score=0.0,
        scoring_status=ScoringStatus.FAILED,
        error_reason=error_reason,
        model_version=MODEL_VERSION,
        trace_id=str(uuid.uuid4()),
    )


def poll_batch(
    batch_id: str,
    items: list[BatchMatchInput],
    *,
    client: Any | None = None,
    poll_interval_s: float = 30.0,
    timeout_s: float = 24 * 3600.0,
) -> dict[str, Any]:
    """Block until ``batch_id`` is complete, return parsed results.

    Result map: ``{custom_id: CVMatchOutput | CVMatchOutputV4}``.
    Items in ``items`` whose custom_id never appears in the batch
    response are absent from the returned map (callers should check
    membership rather than indexing blindly).

    Polling is intentionally simple — the Batch API's stated SLA is
    "within 24 hours". A real production caller may swap this for an
    async/Celery flow that returns control to the caller and requeues
    the poll, but the synchronous loop is enough for the offline jobs
    this module is designed for.
    """
    batches, _client = _resolve_batches_client(client)

    deadline = time.monotonic() + timeout_s
    while True:
        status = batches.retrieve(batch_id)
        state = (
            getattr(status, "processing_status", None)
            or status.get("processing_status")
            or "in_progress"
        )
        if state in ("ended", "completed"):
            break
        if state in ("canceled", "errored"):
            raise RuntimeError(f"Batch {batch_id} ended in state {state!r}")
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Batch {batch_id} did not complete within {timeout_s}s "
                f"(last state: {state!r})"
            )
        time.sleep(poll_interval_s)

    items_by_id = {it.custom_id: it for it in items}
    results: dict[str, Any] = {}
    for row in batches.results(batch_id):
        custom_id = (
            getattr(row, "custom_id", None)
            or row.get("custom_id")
            or ""
        )
        item = items_by_id.get(custom_id)
        if item is None:
            logger.warning("Batch %s returned unknown custom_id=%s", batch_id, custom_id)
            continue

        result_block = (
            getattr(row, "result", None) or row.get("result") or {}
        )
        result_type = (
            getattr(result_block, "type", None) or result_block.get("type")
        )
        if result_type != "succeeded":
            cfg = _config_for(item.version)
            results[custom_id] = _failed_batch_output(
                cfg, error_reason=f"batch_row_{result_type}"
            )
            continue

        message = (
            getattr(result_block, "message", None) or result_block.get("message", {})
        )
        content = (
            getattr(message, "content", None) or message.get("content", [])
        )
        # Anthropic returns a list of content blocks; the first text block
        # is the assistant's JSON.
        raw_text = ""
        for block in content:
            txt = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if txt:
                raw_text = txt
                break

        results[custom_id] = _parse_one_result(item, raw_text)

    return results


def run_batch(
    items: list[BatchMatchInput],
    *,
    client: Any | None = None,
    poll_interval_s: float = 30.0,
    timeout_s: float = 24 * 3600.0,
) -> dict[str, Any]:
    """Convenience: submit + poll. Caller-friendly when synchronous.

    Returns the same dict as ``poll_batch``.
    """
    batch_id = submit_batch(items, client=client)
    return poll_batch(
        batch_id,
        items,
        client=client,
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
    )


__all__ = [
    "BatchMatchInput",
    "submit_batch",
    "poll_batch",
    "run_batch",
]
