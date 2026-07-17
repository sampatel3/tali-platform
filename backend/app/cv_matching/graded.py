"""Graded per-requirement scoring — a focused second Haiku pass.

The main CV-match call (``runner.run_cv_match``) produces dimension
scores, grounded evidence, and a *coarse* per-requirement ``status`` +
``match_tier``. This module adds a SECOND, focused pass that grades each
requirement on a continuous 0-100 ``match_score`` ("how much of a match
is this candidate to THIS requirement") with substitution and
directional-skill implication baked into anchored bands.

``aggregation.compute_requirements_match_score`` uses ``match_score``
when present. This fixes two failures of the binary ``status × tier``
model, both confirmed on production candidates:

1. **Double-penalty.** ``partially_met × strong_substitute`` = 0.5 × 0.85
   = 0.425 scored *below* ``partially_met × exact`` (0.5) — i.e. a
   recognised equivalent skill (deep Spark for an "AWS Glue" requirement)
   lowered the score. A graded score puts the strong equivalent at ~85.
2. **Discarded evidence.** The coarse model marked requirements
   ``unknown``/``missing`` while its own reasoning evidenced them
   (e.g. DR, Jira) — flattening real signal to ~0. Graded credits it.

Why a SEPARATE call and not a merged prompt: at temperature 0, adding the
graded instructions to the main prompt perturbs the *dimension* scores
(prompt brittleness — a strong candidate's cv_fit swung +16 in testing)
and dilutes the grading. The isolated pass is stable and reproducible,
and only runs for candidates that pass pre-screen, so the extra Haiku
call is cheap.
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from . import MODEL_VERSION
from .prompts import render_archetype_block
from ..llm import MeteringContext, generate_structured

logger = logging.getLogger("taali.cv_match.graded")

_MAX_TOKENS = 8000
_SYSTEM = "You are an expert hiring evaluator. Respond ONLY via the provided tool."

_INSTRUCTIONS = """You are an expert hiring evaluator. For EACH requirement listed, grade how well THIS candidate's evidence matches it on a 0-100 scale, via the provided tool.

Evidence rules:
- Evidence may live in the CV OR the <WORKABLE_*> blocks (questionnaire answers, recruiter comments, activity log). All of it is candidate evidence.
- If the evidence shows the candidate HAS something, credit it. Do not mark a present capability as absent.
- Set assessable=false ONLY when there is genuinely NO evidence either way. Do NOT guess a low score for missing info - abstain instead.

match_score anchors (use the FULL range):
- 90-100: Fully meets - the exact skill/tool/experience named, evidenced with depth (years, scale, named systems).
- 75-89: Strong match - meets via a STRONG EQUIVALENT (a term in the archetype's strong_substitutes - e.g. Apache Spark on EMR / Databricks for an "AWS Glue" requirement; PostgreSQL for "relational SQL"), OR the exact tool with only minor depth/recency gaps. A recognised strong equivalent is scored HERE - never penalise it twice by also calling it partial or missing for being a substitute.
- 55-74: Solid partial - clearly related/transferable capability needing some ramp (a weaker substitute, adjacent experience, or the exact skill evidenced thinly).
- 35-54: Weak/tangential - some relevant signal, material gaps.
- 1-34: Minimal relevance.
- 0: No relevant evidence / unrelated.

Use the archetype substitution rules below to decide strong vs weak equivalents.
Directional skill implication: if the candidate evidences a HIGHER/encompassing skill that NECESSARILY implies this requirement (building & tuning AWS Glue jobs implies PySpark; architecting a Lakehouse implies S3), score the implied requirement high (85+). Implication is one-directional: PySpark alone does NOT imply AWS Glue.

Output exactly one entry per requirement_id below. Keep reasoning under 25 words."""


class GradedRequirement(BaseModel):
    """One requirement's graded fit. ``extra="ignore"`` for LLM-added keys."""

    model_config = ConfigDict(extra="ignore")

    requirement_id: str
    reasoning: str = ""
    assessable: bool = True
    match_score: int = Field(default=0, ge=0, le=100)


class GradedRequirements(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirements: list[GradedRequirement] = Field(default_factory=list)


def _build_prompt(jd_text, archetype, requirements, cv_text, workable_context) -> str:
    arch_block = render_archetype_block(archetype) or "(none)"
    # Accept either RequirementInput (``.id``) or RequirementAssessment
    # (``.requirement_id``) — the runner passes the parsed assessments so the
    # graded ids always line up with what was assessed.
    def _rid(r):
        return getattr(r, "id", None) or getattr(r, "requirement_id", None)

    req_lines = "\n".join(
        f"- requirement_id={_rid(r)} | priority="
        f"{r.priority.value if hasattr(r.priority, 'value') else r.priority} | {r.requirement}"
        for r in requirements
        if _rid(r)
    )
    parts = [
        _INSTRUCTIONS,
        "\n=== ARCHETYPE / SUBSTITUTION RULES ===\n" + arch_block,
        "\n=== JOB DESCRIPTION ===\n" + (jd_text or "")[:6000],
        "\n=== REQUIREMENTS TO GRADE ===\n" + req_lines,
    ]
    if workable_context:
        # The runner already applies the exact protected-evidence safety rail.
        # Slicing again here could hide a late salary/work-authorisation answer
        # from the focused grade that directly affects the final score.
        parts.append("\n=== CANDIDATE WORKABLE DATA ===\n" + workable_context)
    parts.append("\n=== CANDIDATE CV ===\n" + (cv_text or "")[:14000])
    return "\n".join(parts)


def _metering(metering_context, trace_id) -> MeteringContext:
    if metering_context:
        return MeteringContext(
            # Metered as "score" (a valid Feature) — the graded pass is part of
            # scoring (same model, same purpose), so its cost rolls into score.
            feature="score",
            organization_id=metering_context.get("organization_id"),
            role_id=metering_context.get("role_id"),
            entity_id=metering_context.get("entity_id"),
            user_id=metering_context.get("user_id"),
            trace_id=trace_id,
        )
    return MeteringContext.skipped(metered_by="graded_no_context", trace_id=trace_id)


def grade_requirements(
    *,
    cv_text: str,
    jd_text: str,
    requirements: Sequence,
    archetype=None,
    client=None,
    workable_context: str | None = None,
    metering_context: dict | None = None,
    trace_id: str | None = None,
    before_provider_call: Callable[[str], None] | None = None,
) -> dict[str, GradedRequirement]:
    """Grade each requirement 0-100. Returns ``{requirement_id: GradedRequirement}``.

    Provider failures do not raise — the caller falls back to coarse
    ``status × tier`` aggregation.  An authority callback is allowed to raise
    so the outer worker can discard prior phases and defer safely.
    """
    if not requirements or client is None:
        return {}
    try:
        prompt = _build_prompt(jd_text, archetype, requirements, cv_text, workable_context)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("graded requirement prompt raised: %s", exc)
        return {}
    authority_failure: Exception | None = None

    def authorize(attempt: int) -> None:
        nonlocal authority_failure
        if before_provider_call is None:
            return
        try:
            before_provider_call(
                "full_score.graded"
                if attempt == 0
                else f"full_score.graded.retry_{attempt}"
            )
        except Exception as exc:
            authority_failure = exc
            raise

    try:
        res = generate_structured(
            client,
            model=MODEL_VERSION,
            messages=[{"role": "user", "content": prompt}],
            output_model=GradedRequirements,
            metering=_metering(metering_context, trace_id),
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            temperature=0.0,
            max_retries=1,
            use_tool_use=True,
            tool_name="grade_requirements",
            before_provider_call=(
                authorize if before_provider_call is not None else None
            ),
        )
    except Exception as exc:  # pragma: no cover — defensive
        if exc is authority_failure:
            raise
        logger.warning("graded requirement pass raised: %s", exc)
        return {}
    if not res.ok or res.value is None:
        logger.warning("graded requirement pass failed: %s", res.error_reason)
        return {}
    return {g.requirement_id: g for g in res.value.requirements if g.requirement_id}


__all__ = ["GradedRequirement", "GradedRequirements", "grade_requirements"]
