"""Single-call CV match runner with validation, retry, caching, and telemetry.

Public entry point: ``run_cv_match(cv_text, jd_text, requirements)``.

The runner dispatches between two pipeline configurations:

- ``v3`` (cv_match_v3.0): production default. Free-form JSON output, 8192 token
  output ceiling, ``CVMatchResult``/``CVMatchOutput`` schemas.
- ``v4.1`` (cv_match_v4.1): Phase 1 of the v4 migration. UNTRUSTED_CV
  spotlighting + anchored 25-point rubric + anti-default rule + evidence-first
  per-requirement schema. 2000 token output ceiling. ``CVMatchResultV4`` /
  ``CVMatchOutputV4`` schemas.

Selection precedence:
1. Explicit ``version=`` argument (used by eval harness shadow-mode).
2. ``settings.USE_CV_MATCH_V4_PHASE1`` flag (used in production rollout).
3. Defaults to ``"v3"`` when neither is provided.

Cost discipline (non-negotiable per the handover):
- Model: ``claude-haiku-4-5-20251001`` only (no Sonnet/Opus fallback).
- Temperature: 0.
- Token ceilings enforced per pipeline config.
- Single Claude call per match. Max 1 retry on validation failure.
- Caching is mandatory: identical inputs hit the cache, no second API call.

Failure modes return a ``CVMatchOutput``/``CVMatchOutputV4`` with
``scoring_status="failed"`` and a populated ``error_reason``. The runner
never raises to its caller — that contract is what lets
``cv_score_orchestrator`` integrate the path behind a feature flag without
changing the failure-handling shape.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from . import (
    MODEL_VERSION,
    PROMPT_VERSION,
    PROMPT_VERSION_V4,
    PROMPT_VERSION_V4_2,
    PROMPT_VERSION_V4_3,
)
from .aggregation import aggregate
from .prompts import (
    build_cv_match_prompt,
    build_cv_match_prompt_v4,
    build_cv_match_prompt_v4_2,
    build_cv_match_prompt_v4_3,
)
from .schemas import (
    CVMatchOutput,
    CVMatchOutputV4,
    CVMatchResult,
    CVMatchResultV4,
    RequirementInput,
    ScoringStatus,
)
from .validation import (
    ValidationFailure,
    check_suspicious_score,
    scan_for_injection,
    validate_cross_field_consistency,
    validate_cross_field_consistency_v4,
    validate_evidence_grounding,
    validate_evidence_grounding_v4,
)

logger = logging.getLogger("taali.cv_match.runner")


PipelineVersion = Literal["v3", "v4.1", "v4.2", "v4.3"]


# --- Cost discipline constants ---------------------------------------------

INPUT_TOKEN_CEILING = 3500
# Output ceiling: started at handover's 1500 → 4096 (still truncated for
# rich evidence quotes on 5-criterion assessments) → 8192. Production
# logs showed JSON failing parse at char 13516 (~3400 tokens) which is
# *under* 4096 but the model emits structural issues when it gets close
# to the cap, so the safety margin matters. Haiku 4.5 supports much
# higher output budgets; 8192 still costs only ~$0.0016 per call.
OUTPUT_TOKEN_CEILING = 8192
# v4 schema is denser (no full JSON-prose summaries inside per-requirement),
# so 2000 output tokens is sufficient. If a real golden case truncates,
# raise to 2500 and document why in calibration.md.
OUTPUT_TOKEN_CEILING_V4 = 2000
MAX_RETRIES = 1  # exactly one retry on validation failure
TEMPERATURE = 0.0


@dataclass
class _PipelineConfig:
    """Per-version pipeline parameterization. v3, v4.1, and v4.2 share the
    same structural pipeline (build prompt → call Claude → parse → ground →
    consistency-check → aggregate); only the bound names differ. v4.2 adds
    a ``pre_build`` hook that resolves the matching archetype before the
    prompt is rendered.
    """

    version: PipelineVersion
    prompt_version: str
    output_token_ceiling: int
    build_prompt: Callable[..., str]
    result_schema: type[BaseModel]
    output_schema: type[BaseModel]
    validate_grounding: Callable[[Any, str], int]
    validate_consistency: Callable[[Any, list[RequirementInput]], None]
    pre_build: Callable[[str, list[RequirementInput]], dict[str, Any]] | None = None


def _no_pre_build(
    jd_text: str, requirements: list[RequirementInput]
) -> dict[str, Any]:
    return {}


def _v4_2_pre_build(
    jd_text: str, requirements: list[RequirementInput]
) -> dict[str, Any]:
    """Run the archetype router before building the v4.2 prompt.

    Returns a dict that's splat into ``build_prompt`` as kwargs. None
    archetype is fine — the v4.2 prompt is well-defined for the
    archetype-less case (renders an empty archetype block).
    """
    try:
        from .archetype_router import pick_archetype
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Archetype router unavailable: %s", exc)
        return {"archetype": None}

    try:
        match = pick_archetype(jd_text, requirements)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Archetype routing failed: %s", exc)
        return {"archetype": None}
    return {"archetype": match.rubric if match is not None else None}


_V3_CONFIG = _PipelineConfig(
    version="v3",
    prompt_version=PROMPT_VERSION,
    output_token_ceiling=OUTPUT_TOKEN_CEILING,
    build_prompt=build_cv_match_prompt,
    result_schema=CVMatchResult,
    output_schema=CVMatchOutput,
    validate_grounding=validate_evidence_grounding,
    validate_consistency=validate_cross_field_consistency,
    pre_build=_no_pre_build,
)

_V4_1_CONFIG = _PipelineConfig(
    version="v4.1",
    prompt_version=PROMPT_VERSION_V4,
    output_token_ceiling=OUTPUT_TOKEN_CEILING_V4,
    build_prompt=build_cv_match_prompt_v4,
    result_schema=CVMatchResultV4,
    output_schema=CVMatchOutputV4,
    validate_grounding=validate_evidence_grounding_v4,
    validate_consistency=validate_cross_field_consistency_v4,
    pre_build=_no_pre_build,
)

_V4_2_CONFIG = _PipelineConfig(
    version="v4.2",
    prompt_version=PROMPT_VERSION_V4_2,
    output_token_ceiling=OUTPUT_TOKEN_CEILING_V4,
    build_prompt=build_cv_match_prompt_v4_2,
    result_schema=CVMatchResultV4,
    output_schema=CVMatchOutputV4,
    validate_grounding=validate_evidence_grounding_v4,
    validate_consistency=validate_cross_field_consistency_v4,
    pre_build=_v4_2_pre_build,
)

_V4_3_CONFIG = _PipelineConfig(
    version="v4.3",
    prompt_version=PROMPT_VERSION_V4_3,
    output_token_ceiling=OUTPUT_TOKEN_CEILING_V4,
    build_prompt=build_cv_match_prompt_v4_3,
    result_schema=CVMatchResultV4,
    output_schema=CVMatchOutputV4,
    validate_grounding=validate_evidence_grounding_v4,
    validate_consistency=validate_cross_field_consistency_v4,
    pre_build=_v4_2_pre_build,  # same archetype routing as v4.2
)


def _resolve_config(version: PipelineVersion | None) -> _PipelineConfig:
    """Decide which pipeline to run.

    Precedence: explicit arg > flag > "v3" default. We import settings
    lazily so test suites that don't construct the full settings object
    can still run the v3 path with explicit ``version="v3"``.
    """
    if version is not None:
        if version == "v4.3":
            return _V4_3_CONFIG
        if version == "v4.2":
            return _V4_2_CONFIG
        if version == "v4.1":
            return _V4_1_CONFIG
        if version == "v3":
            return _V3_CONFIG
        raise ValueError(f"Unknown CV match pipeline version: {version!r}")

    try:
        from ..platform.config import settings

        # Highest active phase wins. Defaults: all off, v3 stays primary.
        if getattr(settings, "USE_CV_MATCH_V4_PHASE3", False):
            return _V4_3_CONFIG
        if getattr(settings, "USE_CV_MATCH_V4_PHASE2", False):
            return _V4_2_CONFIG
        if getattr(settings, "USE_CV_MATCH_V4_PHASE1", False):
            return _V4_1_CONFIG
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Settings unavailable for version selection: %s", exc)
    return _V3_CONFIG


@dataclass
class _RunContext:
    """Per-call mutable state, threaded through retries.

    Kept internal — callers never see this. Surfaces back to telemetry via
    the returned output object.
    """

    trace_id: str
    cv_hash: str
    jd_hash: str
    started_at: float
    pipeline_version: PipelineVersion = "v3"
    retry_count: int = 0
    validation_failures: int = 0
    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


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
    cfg: _PipelineConfig,
    error_reason: str,
    ctx: _RunContext,
):
    """Build the canonical failed-run output. Never raises."""
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
        trace_id=ctx.trace_id,
    )


def _call_claude(
    client,
    *,
    cfg: _PipelineConfig,
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
        max_tokens=cfg.output_token_ceiling,
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
    cfg: _PipelineConfig,
    raw_text: str,
    cv_text: str,
    requirements: list[RequirementInput],
):
    """Parse JSON → Pydantic, then run grounding + consistency validation.

    Returns a ``cfg.result_schema`` instance.

    Raises:
        ValidationFailure on Pydantic schema mismatch or consistency violation.
    """
    text = _strip_json_fences(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(f"Response was not valid JSON: {exc}") from exc

    try:
        result = cfg.result_schema.model_validate(parsed)
    except ValidationError as exc:
        raise ValidationFailure(f"Response failed schema: {exc}") from exc

    # Grounding mutates result in place; doesn't raise.
    cfg.validate_grounding(result, cv_text)

    # Consistency raises ValidationFailure on the first violation.
    cfg.validate_consistency(result, requirements)

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
    version: PipelineVersion | None = None,
):
    """Run a CV match end-to-end.

    Returns a fully populated ``CVMatchOutput`` (v3) or ``CVMatchOutputV4``
    (v4.1) — both share the same top-level field names so call sites that
    read ``role_fit_score``, ``recommendation``, ``scoring_status``, etc.
    work against either.

    Args:
        cv_text: candidate CV text (verbatim).
        jd_text: job specification text.
        requirements: optional recruiter-added requirements; if empty, the LLM
            extracts must-haves from the JD itself.
        client: optional pre-built Anthropic client (used by tests).
        skip_cache: bypass the cache layer (used by eval harness for
            calibration runs).
        version: explicit pipeline selection ("v3" or "v4.1"). When None,
            falls back to ``settings.USE_CV_MATCH_V4_PHASE1`` then "v3".

    On any failure (config missing, token ceiling, schema/consistency after
    retry), returns the version-appropriate output with
    ``scoring_status=FAILED`` and ``error_reason`` set, rather than raising.
    Telemetry is emitted regardless.
    """
    requirements = requirements or []
    cfg = _resolve_config(version)
    ctx = _RunContext(
        trace_id=str(uuid.uuid4()),
        cv_hash=_hash_text(cv_text),
        jd_hash=_hash_text(jd_text),
        started_at=time.monotonic(),
        pipeline_version=cfg.version,
    )

    # Late-imported so module loads even if telemetry/cache import paths fail
    # in lightweight tests.
    from . import cache as cache_module
    from . import telemetry as telemetry_module

    # 1. Cache lookup (per-version cache key — different prompt_version
    #    keeps v3 and v4.1 hits from colliding).
    cache_key = cache_module.compute_cache_key(
        cv_text=cv_text,
        jd_text=jd_text,
        requirements=requirements,
        prompt_version=cfg.prompt_version,
        model_version=MODEL_VERSION,
    )
    if not skip_cache:
        cached = cache_module.get(cache_key, result_schema=cfg.output_schema)
        if cached is not None:
            ctx.cache_hit = True
            cached_with_trace = cached.model_copy(
                update={"trace_id": ctx.trace_id, "cache_hit": True}
            )
            telemetry_module.emit_trace(
                ctx, final_status=cached_with_trace.scoring_status
            )
            return cached_with_trace

    # 2. Build prompt + token guardrail (pre-build hook resolves any
    #    per-version context such as the v4.2 archetype match).
    try:
        pre = (cfg.pre_build or _no_pre_build)(jd_text, requirements)
        prompt = cfg.build_prompt(cv_text, jd_text, requirements, **pre)
    except Exception as exc:
        logger.exception("Failed to render prompt")
        out = _failed_output(
            cfg=cfg, error_reason=f"prompt_render_failed: {exc}", ctx=ctx
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 3. Resolve client (allow injection for tests)
    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            out = _failed_output(
                cfg=cfg, error_reason=f"client_init_failed: {exc}", ctx=ctx
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
            cfg=cfg,
            error_reason=(
                f"input_token_ceiling_exceeded: counted={counted_in}, "
                f"ceiling={INPUT_TOKEN_CEILING}"
            ),
            ctx=ctx,
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 4. Call Claude with at most 1 retry on validation failure
    last_err: str = ""
    parsed = None
    current_prompt = prompt
    for attempt in range(MAX_RETRIES + 1):
        try:
            raw_text, _, _ = _call_claude(
                client, cfg=cfg, prompt=current_prompt, ctx=ctx
            )
        except Exception as exc:
            logger.exception("Claude call failed (attempt %d)", attempt + 1)
            out = _failed_output(
                cfg=cfg, error_reason=f"claude_call_failed: {exc}", ctx=ctx
            )
            telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
            return out

        try:
            parsed = _parse_and_validate(cfg, raw_text, cv_text, requirements)
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
            cfg=cfg,
            error_reason=f"validation_failed_after_retry: {last_err}",
            ctx=ctx,
        )
        telemetry_module.emit_trace(ctx, final_status=out.scoring_status)
        return out

    # 5. Aggregate + injection/sanity flags. Aggregation reads only
    #    ``priority`` and ``status`` on each assessment so it works for
    #    both v3 (RequirementAssessment) and v4 (RequirementAssessmentV4).
    #    For v4.2 with ``dimension_scores`` populated, we back-fill the
    #    v3-compat (skills, experience) pair from the six dimensions and
    #    derive cv_fit from the archetype-weighted dimensions instead of
    #    the simple average.
    skills_score = parsed.skills_match_score
    experience_score = parsed.experience_relevance_score
    dimension_scores = getattr(parsed, "dimension_scores", None)
    archetype_weights: dict[str, float] | None = None
    if dimension_scores is not None and cfg.version == "v4.2":
        from .aggregation import compute_cv_fit_v4_2, derive_v3_compat_scores

        skills_score, experience_score = derive_v3_compat_scores(dimension_scores)
        archetype_for_weights = pre.get("archetype") if isinstance(pre, dict) else None
        if archetype_for_weights is not None:
            archetype_weights = archetype_for_weights.normalised_dimension_weights()

    req_match, cv_fit, role_fit, recommendation = aggregate(
        skills_match_score=skills_score,
        experience_relevance_score=experience_score,
        assessments=parsed.requirements_assessment,
    )
    if dimension_scores is not None and cfg.version == "v4.2":
        from .aggregation import compute_cv_fit_v4_2, compute_role_fit

        cv_fit = compute_cv_fit_v4_2(dimension_scores, weights=archetype_weights)
        role_fit = compute_role_fit(cv_fit, req_match)

    output_kwargs = dict(
        prompt_version=cfg.prompt_version,
        skills_match_score=skills_score,
        experience_relevance_score=experience_score,
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
    # ``dimension_scores`` only exists on the v4 schemas — passing the
    # kwarg to the v3 schema would fail Pydantic. Only include it when
    # the active output schema accepts it.
    if "dimension_scores" in cfg.output_schema.model_fields:
        output_kwargs["dimension_scores"] = dimension_scores

    # Phase 3.4: attach calibrated_p_advance when a calibrator exists
    # for the active (role_family, "role_fit") pair. role_family comes
    # from the matched archetype on the v4.2 path; v3 / v4.1 skip.
    if "calibrated_p_advance" in cfg.output_schema.model_fields:
        archetype_for_calibrator = (
            pre.get("archetype") if isinstance(pre, dict) else None
        )
        if archetype_for_calibrator is not None:
            try:
                from .calibrators import apply_calibrator

                output_kwargs["calibrated_p_advance"] = apply_calibrator(
                    role_family=archetype_for_calibrator.archetype_id,
                    dimension="role_fit",
                    raw_score=role_fit,
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("Calibrator lookup failed: %s", exc)
    output = cfg.output_schema(**output_kwargs)

    # 6. Cache + telemetry
    if not skip_cache:
        try:
            cache_module.set(cache_key, output)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Cache write failed for trace %s: %s", ctx.trace_id, exc)

    telemetry_module.emit_trace(ctx, final_status=output.scoring_status)
    return output


def maybe_run_v4_shadow(
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None = None,
    *,
    client=None,
    sample_rate: float | None = None,
) -> None:
    """Fire-and-forget v4.1 shadow run for online comparison.

    Called by orchestrators after a primary v3 call to opportunistically
    score the same inputs through the v4.1 pipeline. The shadow run:

    - is sampled at ``sample_rate`` (defaults to
      ``settings.CV_MATCH_V4_SHADOW_SAMPLE_RATE``)
    - writes a telemetry trace with ``shadow=True``
    - swallows all exceptions (must not affect the primary response)
    - bypasses the cache so the v4.1 path is exercised end-to-end
    - returns ``None`` regardless of outcome

    Acceptance for RALPH 1.8: shadow rows appear in the trace log with
    ``prompt_version=cv_match_v4.1`` and a ``shadow=true`` flag.
    """
    import random

    try:
        if sample_rate is None:
            try:
                from ..platform.config import settings

                sample_rate = float(
                    getattr(settings, "CV_MATCH_V4_SHADOW_SAMPLE_RATE", 0.0) or 0.0
                )
            except Exception:
                sample_rate = 0.0

        if sample_rate <= 0.0:
            return
        if random.random() >= sample_rate:
            return

        # Inline call (do not spawn a thread — the orchestrator decides
        # threading). The sample rate is the only knob; keeping the call
        # synchronous keeps failure modes simple.
        ctx = _RunContext(
            trace_id=str(uuid.uuid4()),
            cv_hash=_hash_text(cv_text),
            jd_hash=_hash_text(jd_text),
            started_at=time.monotonic(),
            pipeline_version="v4.1",
        )
        ctx.extra["shadow"] = True

        from . import telemetry as telemetry_module

        try:
            output = run_cv_match(
                cv_text,
                jd_text,
                requirements,
                client=client,
                skip_cache=True,
                version="v4.1",
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("v4 shadow run failed: %s", exc)
            return

        # Stamp the shadow context with telemetry (in addition to whatever
        # the inner run_cv_match emitted — that inner trace lacks the
        # shadow flag because it has its own context).
        ctx.input_tokens = getattr(output, "input_tokens", 0) or 0
        ctx.output_tokens = getattr(output, "output_tokens", 0) or 0
        telemetry_module.emit_trace(ctx, final_status=output.scoring_status)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("maybe_run_v4_shadow outer failed: %s", exc)
