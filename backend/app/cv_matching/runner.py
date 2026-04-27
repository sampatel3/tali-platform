"""Single-call CV match runner with validation, retry, caching, and telemetry.

Public entry point: ``run_cv_match(cv_text, jd_text, requirements)``.

Cost discipline (non-negotiable per the handover):
- Model: ``claude-haiku-4-5-20251001`` only (no Sonnet/Opus fallback).
- Temperature: 0.
- Token ceilings: 3500 input, 1500 output. Fail loud above input ceiling.
- Single Claude call per match. Max 1 retry on validation failure.
- Caching is mandatory: identical inputs hit the cache, no second API call.

Failure modes return a ``CVMatchOutput`` with ``scoring_status="failed"``
and a populated ``error_reason``. The runner never raises to its caller —
that contract is what lets ``cv_score_orchestrator`` integrate the v3 path
behind a feature flag without changing the failure-handling shape.
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
from .prompts import build_cv_match_prompt
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


# --- Cost discipline constants ---------------------------------------------

INPUT_TOKEN_CEILING = 3500
# Output ceiling: started at handover's 1500 → 4096 (still truncated for
# rich evidence quotes on 5-criterion assessments) → 8192. Production
# logs showed JSON failing parse at char 13516 (~3400 tokens) which is
# *under* 4096 but the model emits structural issues when it gets close
# to the cap, so the safety margin matters. Haiku 4.5 supports much
# higher output budgets; 8192 still costs only ~$0.0016 per call.
OUTPUT_TOKEN_CEILING = 8192
MAX_RETRIES = 1  # exactly one retry on validation failure
TEMPERATURE = 0.0


@dataclass
class _RunContext:
    """Per-call mutable state, threaded through retries.

    Kept internal — callers never see this. Surfaces back to telemetry via
    the returned ``CVMatchOutput``.
    """

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
    """Pull JSON object out of a possibly-fenced response.

    The prompt forbids fences, but at temperature 0 there is occasional
    leakage; cheaper to recover than retry.
    """
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = obj_match.group(0)
    return text


def _count_input_tokens(client, prompt: str, system: str) -> int:
    """Count tokens via the Anthropic SDK if available.

    ``client.messages.count_tokens`` was added in anthropic SDK 0.39+. On
    older SDKs the attribute doesn't exist; we fail open with a heuristic
    estimate based on character count (~4 chars/token for English).

    Anthropic enforces input limits server-side too — this is just for the
    fail-loud token-ceiling guard. A loose estimate is fine.
    """
    if not hasattr(client.messages, "count_tokens"):
        # Heuristic fallback for SDKs that don't expose count_tokens.
        return (len(prompt) + len(system)) // 4
    try:
        result = client.messages.count_tokens(
            model=MODEL_VERSION,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return int(getattr(result, "input_tokens", 0) or 0)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Token counting failed: %s", exc)
        return (len(prompt) + len(system)) // 4


def _failed_output(
    *,
    error_reason: str,
    requirements: list[RequirementInput],
    ctx: _RunContext,
) -> CVMatchOutput:
    """Build the canonical failed-run output. Never raises."""
    return CVMatchOutput(
        prompt_version=PROMPT_VERSION,
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
        trace_id=ctx.trace_id,
    )


def _call_claude(
    client,
    *,
    prompt: str,
    ctx: _RunContext,
) -> tuple[str, int, int]:
    """Single Claude call. Returns (raw_text, input_tokens, output_tokens).

    No model fallback — Haiku 4.5 only per cost discipline. Errors propagate
    to the caller.
    """
    system = "You are an expert recruiter. Respond ONLY with valid JSON."
    response = client.messages.create(
        model=MODEL_VERSION,
        max_tokens=OUTPUT_TOKEN_CEILING,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": prompt}],
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

    return raw_text, ctx.input_tokens, ctx.output_tokens


def _parse_and_validate(
    raw_text: str,
    cv_text: str,
    requirements: list[RequirementInput],
) -> CVMatchResult:
    """Parse JSON → Pydantic, then run grounding + consistency validation.

    Raises:
        ValidationFailure on Pydantic schema mismatch or consistency violation.
    """
    text = _strip_json_fences(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(f"Response was not valid JSON: {exc}") from exc

    try:
        result = CVMatchResult.model_validate(parsed)
    except ValidationError as exc:
        raise ValidationFailure(f"Response failed schema: {exc}") from exc

    # Grounding mutates result in place; doesn't raise.
    validate_evidence_grounding(result, cv_text)

    # Consistency raises ValidationFailure on the first violation.
    validate_cross_field_consistency(result, requirements)

    return result


def _resolve_anthropic_client():
    """Build an Anthropic client from settings.

    Per ``memory/anthropic_key_routing.md``: all Claude calls use Taali's
    ``settings.ANTHROPIC_API_KEY``. Customers/orgs do NOT bring their own
    key. No per-org resolver.
    """
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
    """Run a CV match end-to-end. Returns a fully populated ``CVMatchOutput``.

    Args:
        cv_text: candidate CV text (verbatim).
        jd_text: job specification text.
        requirements: optional recruiter-added requirements; if empty, the LLM
            extracts must-haves from the JD itself (rule 4 in the prompt).
        client: optional pre-built Anthropic client (used by tests).
        skip_cache: bypass the cache layer (used by eval harness for
            calibration runs).

    On any failure (config missing, token ceiling, schema/consistency after
    retry), returns ``CVMatchOutput(scoring_status=FAILED, error_reason=...)``
    rather than raising. Telemetry is emitted regardless.
    """
    requirements = requirements or []
    ctx = _RunContext(
        trace_id=str(uuid.uuid4()),
        cv_hash=_hash_text(cv_text),
        jd_hash=_hash_text(jd_text),
        started_at=time.monotonic(),
    )

    # Late-imported so module loads even if telemetry/cache import paths fail
    # in lightweight tests.
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

    # 2. Build prompt + token guardrail
    try:
        prompt = build_cv_match_prompt(cv_text, jd_text, requirements)
    except Exception as exc:
        logger.exception("Failed to render prompt")
        out = _failed_output(
            error_reason=f"prompt_render_failed: {exc}",
            requirements=requirements,
            ctx=ctx,
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 3. Resolve client (allow injection for tests)
    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            out = _failed_output(
                error_reason=f"client_init_failed: {exc}",
                requirements=requirements,
                ctx=ctx,
            )
            telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
            return out

    # Token ceiling check (input). Output ceiling is enforced by max_tokens.
    counted_in = _count_input_tokens(
        client,
        prompt,
        system="You are an expert recruiter. Respond ONLY with valid JSON.",
    )
    if counted_in > INPUT_TOKEN_CEILING:
        out = _failed_output(
            error_reason=(
                f"input_token_ceiling_exceeded: counted={counted_in}, "
                f"ceiling={INPUT_TOKEN_CEILING}"
            ),
            requirements=requirements,
            ctx=ctx,
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 4. Call Claude with at most 1 retry on validation failure
    last_err: str = ""
    parsed: CVMatchResult | None = None
    current_prompt = prompt
    for attempt in range(MAX_RETRIES + 1):
        try:
            raw_text, _, _ = _call_claude(client, prompt=current_prompt, ctx=ctx)
        except Exception as exc:
            logger.exception("Claude call failed (attempt %d)", attempt + 1)
            out = _failed_output(
                error_reason=f"claude_call_failed: {exc}",
                requirements=requirements,
                ctx=ctx,
            )
            telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
            return out

        try:
            parsed = _parse_and_validate(raw_text, cv_text, requirements)
            break  # success
        except ValidationFailure as exc:
            ctx.validation_failures += 1
            last_err = str(exc)
            logger.warning(
                "Validation failed on attempt %d: %s", attempt + 1, last_err
            )
            if attempt >= MAX_RETRIES:
                break
            ctx.retry_count += 1
            current_prompt = (
                prompt
                + "\n\nYour previous response failed validation with this error:\n"
                + f"{last_err}\n"
                + "Return a corrected JSON response. Do not include any commentary."
            )

    if parsed is None:
        out = _failed_output(
            error_reason=f"validation_failed_after_retry: {last_err}",
            requirements=requirements,
            ctx=ctx,
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 5. Aggregate + injection/sanity flags
    req_match, cv_fit, role_fit, recommendation = aggregate(
        skills_match_score=parsed.skills_match_score,
        experience_relevance_score=parsed.experience_relevance_score,
        assessments=parsed.requirements_assessment,
    )

    output = CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        skills_match_score=parsed.skills_match_score,
        experience_relevance_score=parsed.experience_relevance_score,
        requirements_assessment=parsed.requirements_assessment,
        matching_skills=parsed.matching_skills,
        missing_skills=parsed.missing_skills,
        experience_highlights=parsed.experience_highlights,
        concerns=parsed.concerns,
        summary=parsed.summary,
        requirements_match_score=req_match,
        cv_fit_score=cv_fit,
        role_fit_score=role_fit,
        recommendation=recommendation,
        injection_suspected=scan_for_injection(cv_text),
        suspicious_score=check_suspicious_score(
            requirements_match_score=req_match, cv_text=cv_text
        ),
        scoring_status=ScoringStatus.OK,
        error_reason="",
        model_version=MODEL_VERSION,
        trace_id=ctx.trace_id,
    )

    # 6. Cache + telemetry
    if not skip_cache:
        try:
            cache_module.set(cache_key, output)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Cache write failed for trace %s: %s", ctx.trace_id, exc)

    telemetry_module.emit_trace(ctx, final_status=output.scoring_status)
    return output
