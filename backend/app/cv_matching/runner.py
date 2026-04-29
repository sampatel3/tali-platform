"""Single-call CV match runner with validation, retry, caching, and telemetry.

Public entry point: ``run_cv_match(cv_text, jd_text, requirements)``.

Single linear pipeline:
1. Cache lookup
2. Synthesize archetype rubric for this JD (cached; ~$0.05 per novel JD)
3. Build prompt with optional archetype context
4. Call Haiku
5. Parse + ground + consistency-check (one retry on failure)
6. Aggregate (priority × status × tier weights; archetype-weighted dimensions)
7. Apply calibrator (if one exists for this archetype)
8. Cache + telemetry
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from . import MODEL_VERSION, PROMPT_VERSION
from .aggregation import aggregate
from .prompts import build_cv_match_messages, build_cv_match_prompt
from .schemas import (
    CVMatchOutput,
    CVMatchResult,
    RequirementInput,
    ScoringStatus,
)
from .validation import (
    ValidationFailure,
    check_suspicious_score,
    scan_for_injection,
    validate_cross_field_consistency,
    validate_evidence_grounding,
)

logger = logging.getLogger("taali.cv_match.runner")


# Input ceiling: 32K tokens. Haiku 4.5 supports up to 200K input.
# Real CVs are routinely 3.5K-5.5K tokens; the prompt template is ~3K
# (with archetype block); long JDs another 1-2K. 32K leaves headroom for
# the worst-case CV. At Haiku input pricing ($0.25/1M), 32K input
# tokens is ~$0.008 per call.
INPUT_TOKEN_CEILING = 32_000
# Output ceiling: 8K tokens. The new prompt's per-requirement object is
# ~150 tokens (evidence_quotes list, reasoning, status, tier, etc.).
# At 8 requirements × 150 = 1200 + 6-dim scores + summary + matching/
# missing skills lists, real responses land around 3000-5000 tokens.
# Production showed truncation at 4000 (chars 16K-17K = ~4K tokens).
# 8K leaves room for 15+ requirements without truncation. At Haiku
# output pricing ($1.25/1M), 8K output is ~$0.01 per call.
OUTPUT_TOKEN_CEILING = 8000
MAX_RETRIES = 1
TEMPERATURE = 0.0


@dataclass
class _RunContext:
    trace_id: str
    cv_hash: str
    jd_hash: str
    started_at: float
    retry_count: int = 0
    validation_failures: int = 0
    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _strip_json_fences(raw: str) -> str:
    """Pull JSON object out of a possibly-fenced response."""
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = obj_match.group(0)
    return text


def _count_input_tokens(client, messages: list[dict], system: str) -> int:
    """Count input tokens via the SDK; heuristic fallback when unavailable."""
    if not hasattr(client.messages, "count_tokens"):
        total_chars = sum(
            len(block.get("text", "") if isinstance(block, dict) else block)
            for msg in messages
            for block in (
                msg["content"] if isinstance(msg.get("content"), list) else [{"text": msg.get("content", "")}]
            )
        )
        return (total_chars + len(system)) // 4
    try:
        result = client.messages.count_tokens(
            model=MODEL_VERSION,
            system=system,
            messages=messages,
        )
        return int(getattr(result, "input_tokens", 0) or 0)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Token counting failed: %s", exc)
        return 8000  # conservative fallback — below the 32K ceiling


def _failed_output(*, error_reason: str, ctx: _RunContext) -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        scoring_status=ScoringStatus.FAILED,
        error_reason=error_reason,
        model_version=MODEL_VERSION,
        trace_id=ctx.trace_id,
    )


_SYSTEM_PROMPT = "You are an expert recruiter. Respond ONLY with valid JSON."


def _call_claude(client, *, messages: list[dict], ctx: _RunContext) -> str:
    response = client.messages.create(
        model=MODEL_VERSION,
        max_tokens=OUTPUT_TOKEN_CEILING,
        temperature=TEMPERATURE,
        system=_SYSTEM_PROMPT,
        messages=messages,
    )

    usage = getattr(response, "usage", None)
    if usage is not None:
        ctx.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        ctx.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    raw_text = ""
    try:
        raw_text = response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        raw_text = ""
    return raw_text


def _parse_and_validate(
    raw_text: str,
    cv_text: str,
    requirements: list[RequirementInput],
) -> CVMatchResult:
    """Parse JSON → schema → ground → consistency. Raises ValidationFailure."""
    text = _strip_json_fences(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(f"Response was not valid JSON: {exc}") from exc

    try:
        result = CVMatchResult.model_validate(parsed)
    except ValidationError as exc:
        raise ValidationFailure(f"Response failed schema: {exc}") from exc

    validate_evidence_grounding(result, cv_text)
    validate_cross_field_consistency(result, requirements)
    return result


def _resolve_anthropic_client():
    from anthropic import Anthropic

    from ..platform.config import settings

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return Anthropic(api_key=api_key)


def run_cv_match(
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None = None,
    *,
    client=None,
    skip_cache: bool = False,
) -> CVMatchOutput:
    """Run a CV match end-to-end. Returns ``CVMatchOutput``.

    Never raises to the caller. On any failure the output carries
    ``scoring_status=FAILED`` and a populated ``error_reason``.
    """
    requirements = requirements or []
    ctx = _RunContext(
        trace_id=str(uuid.uuid4()),
        cv_hash=_hash_text(cv_text),
        jd_hash=_hash_text(jd_text),
        started_at=time.monotonic(),
    )

    from . import cache as cache_module
    from . import telemetry as telemetry_module

    # 1. Cache lookup
    cache_key = cache_module.compute_cache_key(
        cv_text=cv_text,
        jd_text=jd_text,
        requirements=requirements,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    if not skip_cache:
        cached = cache_module.get(cache_key)
        if cached is not None:
            ctx.cache_hit = True
            cached_with_trace = cached.model_copy(
                update={"trace_id": ctx.trace_id, "cache_hit": True}
            )
            telemetry_module.emit_trace(ctx, final_status=cached_with_trace.scoring_status)
            return cached_with_trace

    # 2. Resolve client (used for both archetype synthesis and the score call)
    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            out = _failed_output(error_reason=f"client_init_failed: {exc}", ctx=ctx)
            telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
            return out

    # 3. Synthesize / fetch archetype rubric (cached; cheap on hit, ~$0.05 on miss)
    archetype = None
    archetype_weights = None
    try:
        from .archetype_synthesizer import synthesize_archetype

        archetype = synthesize_archetype(jd_text, requirements, client=client)
        if archetype is not None:
            archetype_weights = archetype.normalised_dimension_weights()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Archetype synthesis failed; proceeding without: %s", exc)
        archetype = None

    # 4. Build messages (two content blocks: cached static role block + dynamic CV block)
    try:
        messages = build_cv_match_messages(
            cv_text,
            jd_text,
            requirements,
            archetype=archetype,
            prompt_version=PROMPT_VERSION,
        )
    except Exception as exc:
        logger.exception("Failed to render prompt")
        out = _failed_output(error_reason=f"prompt_render_failed: {exc}", ctx=ctx)
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 5. Token ceiling check
    counted_in = _count_input_tokens(client, messages, _SYSTEM_PROMPT)
    if counted_in > INPUT_TOKEN_CEILING:
        out = _failed_output(
            error_reason=(
                f"input_token_ceiling_exceeded: counted={counted_in}, "
                f"ceiling={INPUT_TOKEN_CEILING}"
            ),
            ctx=ctx,
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 6. Call Claude with at most one retry on validation failure.
    # On retry, append the error to the CV block so the static block stays
    # cached (no need to re-send JD / rules / schema).
    last_err: str = ""
    parsed: CVMatchResult | None = None
    current_messages = messages
    for attempt in range(MAX_RETRIES + 1):
        try:
            raw_text = _call_claude(client, messages=current_messages, ctx=ctx)
        except Exception as exc:
            logger.exception("Claude call failed (attempt %d)", attempt + 1)
            out = _failed_output(error_reason=f"claude_call_failed: {exc}", ctx=ctx)
            telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
            return out

        try:
            parsed = _parse_and_validate(raw_text, cv_text, requirements)
            break
        except ValidationFailure as exc:
            ctx.validation_failures += 1
            last_err = str(exc)
            logger.warning("Validation failed on attempt %d: %s", attempt + 1, last_err)
            if attempt >= MAX_RETRIES:
                break
            ctx.retry_count += 1
            # Keep the cached static block; append the correction request only
            # to the dynamic CV block so the next call still gets a cache hit.
            retry_suffix = (
                "\n\nYour previous response failed validation with this error:\n"
                + last_err
                + "\nReturn a corrected JSON response. Do not include any commentary."
            )
            base_content = messages[0]["content"]
            retry_cv_text = base_content[1]["text"] + retry_suffix
            current_messages = [
                {
                    "role": "user",
                    "content": [
                        base_content[0],  # cached static block (unchanged)
                        {"type": "text", "text": retry_cv_text},
                    ],
                }
            ]

    if parsed is None:
        out = _failed_output(
            error_reason=f"validation_failed_after_retry: {last_err}", ctx=ctx
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 7. Aggregate
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

    # 8. Calibration (None when no calibrator exists)
    calibrated_p_advance = None
    if archetype is not None:
        try:
            from .calibrators import apply_calibrator

            calibrated_p_advance = apply_calibrator(
                role_family=archetype.archetype_id,
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
        # recommendation deliberately omitted — derived at display time
        # from role_fit_score + the per-role reject threshold the
        # recruiter sets on the job page.
        injection_suspected=scan_for_injection(cv_text),
        suspicious_score=check_suspicious_score(
            requirements_match_score=req_match, cv_text=cv_text
        ),
        scoring_status=ScoringStatus.OK,
        error_reason="",
        model_version=MODEL_VERSION,
        trace_id=ctx.trace_id,
        calibrated_p_advance=calibrated_p_advance,
    )

    if not skip_cache:
        try:
            cache_module.set(cache_key, output)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Cache write failed for trace %s: %s", ctx.trace_id, exc)

    telemetry_module.emit_trace(ctx, final_status=output.scoring_status)
    return output
