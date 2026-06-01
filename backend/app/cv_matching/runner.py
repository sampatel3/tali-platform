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

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from functools import partial

from ..llm import MeteringContext, generate_structured
from ..platform.config import settings
from ..services.fraud_detection import (
    apply_integrity_penalty,
    compute_integrity_penalty,
    detect_timeline_inconsistencies,
)
from . import MODEL_VERSION, PROMPT_VERSION
from . import cache as cache_module
from . import telemetry as telemetry_module
from .aggregation import aggregate
from .prompts import build_cv_match_messages
from .schemas import (
    CVMatchOutput,
    CVMatchResult,
    RequirementInput,
    ScoringStatus,
)
from .validation import (
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
# Each requirement assessment costs ~350 output tokens (evidence_quotes,
# reasoning, status, match_tier, impact, confidence). The agent-on roles
# in production carry 20-22 criteria → ~7,700 tokens for the assessment
# list alone, before the 6-dim scores, summary, and skills lists. At an
# 8K ceiling those responses truncated mid-JSON → invalid JSON →
# validation_failed_after_retry → the whole scoring run errored.
#
# 2026-05-22 data: 76% of cv_match runs were erroring post the
# missing-criteria validator fix, almost all "Response was not valid
# JSON". A truncating role burned ~16K output tokens across two failed
# attempts ($0.08 at Haiku $5/1M output) and produced ZERO usable score.
# A single complete 16K-ceiling response uses ~8K tokens ($0.04) and
# SUCCEEDS — half the cost, real result. Raising the ceiling is a pure
# win: small responses are unaffected (model stops when done), large
# ones stop failing.
OUTPUT_TOKEN_CEILING = 16000
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
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _count_input_tokens(messages: list[dict], system: str) -> int:
    """Estimate input tokens from character counts — no API call, no billing."""
    total_chars = sum(
        len(block.get("text", "") if isinstance(block, dict) else str(block))
        for msg in messages
        for block in (
            msg["content"] if isinstance(msg.get("content"), list) else [{"text": msg.get("content", "")}]
        )
    )
    # ~3.5 chars/token for English; slightly conservative to avoid undercounting.
    return int((total_chars + len(system)) / 3.5)


def _failed_output(*, error_reason: str, ctx: _RunContext) -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        scoring_status=ScoringStatus.FAILED,
        error_reason=error_reason,
        model_version=MODEL_VERSION,
        trace_id=ctx.trace_id,
    )


_SYSTEM_PROMPT = "You are an expert recruiter. Respond ONLY with valid JSON."


def _resolve_anthropic_client(*, organization_id: int | None = None):
    # Always return a ``MeteredAnthropicClient`` so a metering wrapper is
    # always available. v3 scoring sets ``metering={"skip": True}`` because
    # cv_score_orchestrator records the event post-call from the typed
    # CVMatchOutput, but going through the wrapper means a future
    # direct-invocation path can't accidentally bypass metering.
    #
    # ``organization_id`` flows to the gated resolver: with the per-org
    # workspace-key flag OFF (default) it just binds the org for metering on
    # the shared key; with it ON, scoring routes through the org's own key.
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client(organization_id=organization_id)


def run_cv_match(
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None = None,
    *,
    client=None,
    skip_cache: bool = False,
    metering_context: dict | None = None,
    workable_context: str | None = None,
) -> CVMatchOutput:
    """Run a CV match end-to-end. Returns ``CVMatchOutput``.

    Never raises to the caller. On any failure the output carries
    ``scoring_status=FAILED`` and a populated ``error_reason``.

    ``workable_context`` is the candidate's per-application Workable evidence
    (questionnaire answers, recruiter comments, activity log) rendered by
    ``format_workable_context``. It feeds the prompt as first-class evidence
    AND the grounding corpus, so hard constraints answered outside the CV
    (e.g. a salary expectation given on a LinkedIn apply) are assessed rather
    than left ``unknown``.
    """
    requirements = requirements or []
    workable_context = (workable_context or "").strip()
    # Quotes may be drawn from the CV or the Workable blocks; ground against both.
    grounding_text = (
        f"{cv_text}\n\n{workable_context}" if workable_context else cv_text
    )
    ctx = _RunContext(
        trace_id=str(uuid.uuid4()),
        cv_hash=_hash_text(cv_text),
        jd_hash=_hash_text(jd_text),
        started_at=time.monotonic(),
    )

    # 1. Cache lookup
    cache_key = cache_module.compute_cache_key(
        cv_text=cv_text,
        jd_text=jd_text,
        requirements=requirements,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
        workable_context=workable_context,
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
            client = _resolve_anthropic_client(
                organization_id=(metering_context or {}).get("organization_id")
            )
        except Exception as exc:
            out = _failed_output(error_reason=f"client_init_failed: {exc}", ctx=ctx)
            telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
            return out

    # 3. Synthesize / fetch archetype rubric (cached; cheap on hit, ~$0.05 on miss)
    archetype = None
    archetype_weights = None
    try:
        # Function-level import preserved so test ``monkeypatch.setattr``
        # against ``app.cv_matching.archetype_synthesizer.synthesize_archetype``
        # reaches this call — a top-level binding would freeze the original
        # at module load and bypass the patch.
        from .archetype_synthesizer import synthesize_archetype

        archetype_metering = (
            {**(metering_context or {}), "feature": "archetype_synthesis"}
            if metering_context else None
        )
        archetype = synthesize_archetype(
            jd_text,
            requirements,
            client=client,
            metering=archetype_metering,
        )
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
            workable_context=workable_context,
        )
    except Exception as exc:
        logger.exception("Failed to render prompt")
        out = _failed_output(error_reason=f"prompt_render_failed: {exc}", ctx=ctx)
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 5. Metering for the score call. With a metering_context the wrapper
    # records a usage_event (feature="score"); without one (direct test/eval
    # calls on a bare client) we skip so no org-less event is fabricated.
    if metering_context:
        gw_metering = MeteringContext(
            feature="score",
            organization_id=metering_context.get("organization_id"),
            role_id=metering_context.get("role_id"),
            entity_id=metering_context.get("entity_id"),
            user_id=metering_context.get("user_id"),
            trace_id=ctx.trace_id,
        )
    else:
        gw_metering = MeteringContext.skipped(
            metered_by="direct_call_no_context", trace_id=ctx.trace_id
        )

    # 6. Call + parse + ground + consistency-check via the shared gateway
    # (one validation retry). On retry the error is appended to the dynamic
    # CV block ONLY, so the cached static block still gets a cache hit.
    def _retry_append_to_cv_block(base_messages, error):
        suffix = (
            "\n\nYour previous response failed validation with this error:\n"
            + error
            + "\nReturn a corrected JSON response. Do not include any commentary."
        )
        base_content = base_messages[0]["content"]
        return [
            {
                "role": "user",
                "content": [
                    base_content[0],  # cached static block (unchanged)
                    {"type": "text", "text": base_content[1]["text"] + suffix},
                ],
            }
        ]

    # Forced tool-use: the model emits CVMatchResult as the tool's
    # ``.input`` dict, killing the "Response was not valid JSON" failure
    # class behind the 2026-05-22 validation storm. Pydantic schema is the
    # single wire contract; grounding + consistency still run server-side.
    result = generate_structured(
        client,
        model=MODEL_VERSION,
        system=_SYSTEM_PROMPT,
        messages=messages,
        output_model=CVMatchResult,
        metering=gw_metering,
        max_tokens=OUTPUT_TOKEN_CEILING,
        temperature=TEMPERATURE,
        max_retries=MAX_RETRIES,
        max_input_tokens=INPUT_TOKEN_CEILING,
        estimate_input_tokens=_count_input_tokens,
        semantic_validators=[
            partial(validate_evidence_grounding, cv_text=grounding_text),
            partial(validate_cross_field_consistency, requirements=requirements),
        ],
        retry_message_builder=_retry_append_to_cv_block,
        use_tool_use=True,
    )

    # Mirror the gateway's token + retry accounting onto the run context so
    # telemetry and the typed output carry the same numbers as before.
    ctx.input_tokens = result.usage.input_tokens
    ctx.output_tokens = result.usage.output_tokens
    ctx.cache_read_tokens = result.usage.cache_read_tokens
    ctx.cache_creation_tokens = result.usage.cache_creation_tokens
    ctx.retry_count = result.retry_count
    ctx.validation_failures = result.validation_failures

    if not result.ok or result.value is None:
        out = _failed_output(error_reason=result.error_reason, ctx=ctx)
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    parsed = result.value

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

    # 7b. CV integrity — deterministic, bounded soft penalty on role_fit.
    # Two signals: unverified extraordinary claims the model flagged, and
    # timeline inconsistencies over the extracted career timeline. Capped
    # so fraud can't inflate a candidate into interview, but a false
    # positive (LLM-extracted timeline, model-prior familiarity) can't
    # auto-reject.
    timeline_entries = (
        parsed.candidate_snapshot.timeline if parsed.candidate_snapshot else []
    )
    timeline_result = detect_timeline_inconsistencies(timeline_entries)
    integrity = compute_integrity_penalty(
        parsed.claims_to_verify,
        timeline_result,
        points_per_issue=settings.FRAUD_INTEGRITY_PENALTY_POINTS,
        max_penalty=settings.FRAUD_INTEGRITY_PENALTY_MAX,
    )
    role_fit = apply_integrity_penalty(role_fit, integrity.penalty)
    timeline_flags = [issue.detail for issue in timeline_result.issues]

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
        candidate_snapshot=parsed.candidate_snapshot,
        claims_to_verify=parsed.claims_to_verify,
        timeline_flags=timeline_flags,
        integrity_penalty=integrity.penalty,
        requirements_match_score=req_match,
        cv_fit_score=cv_fit,
        role_fit_score=role_fit,
        # recommendation deliberately omitted — derived at display time
        # from role_fit_score + the per-role reject threshold the
        # recruiter sets on the job page.
        injection_suspected=scan_for_injection(grounding_text),
        suspicious_score=check_suspicious_score(
            requirements_match_score=req_match, cv_text=cv_text
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
        retry_count=ctx.retry_count,
        validation_failures=ctx.validation_failures,
    )

    if not skip_cache:
        try:
            cache_module.set(cache_key, output)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Cache write failed for trace %s: %s", ctx.trace_id, exc)

    telemetry_module.emit_trace(ctx, final_status=output.scoring_status)
    return output
