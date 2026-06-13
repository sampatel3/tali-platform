"""Holistic Sonnet scoring engine (``cv_match holistic_v1``).

Drop-in alternative to the Haiku ``run_cv_match`` main+graded pipeline,
activated per-org behind ``HOLISTIC_SCORING_ENABLED`` /
``HOLISTIC_SCORING_ORG_IDS``. Two Sonnet stages:

  1. **Requirements derivation** — atomic split + criticality + a
     *framework-flexible* core-capability definition (acceptable
     equivalents named), replacing the regex/criteria bundling. Cached
     per job-spec hash in Redis so it amortises across a role's cohort
     (one derive per JD, not per candidate) — that's what keeps the
     engine cost-neutral-to-cheaper vs the two Haiku calls.

  2. **Scoring** — ONE holistic, no-mechanical-gate Sonnet call with
     calibrated full-range bands. Its ``overall`` becomes
     ``role_fit_score`` *directly*: the deterministic
     ``0.40·cv_fit + 0.60·req_match`` aggregation is the miscalibration
     this engine was built to replace, so we bypass it. ``overall`` is
     emitted FIRST in the tool schema so the autoregressive commit lands
     the validated score before the descriptive fields.

Returns a fully-populated :class:`CVMatchOutput` so every downstream
consumer keeps working unchanged — the decision policy reads
``role_fit_score``; the candidate report reads ``summary`` /
``matching_skills`` / ``requirements_assessment``.

Validated: reproduces the converged dual-ground-truth eval (37/38
consistent) on the 38-candidate sample across roles 26/64/92/98.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..llm import MeteringContext, generate_structured
from .schemas import (
    Confidence,
    CVMatchOutput,
    Priority,
    RequirementAssessment,
    ScoringStatus,
    Status,
)

logger = logging.getLogger(__name__)

# The validated model + prompt version. Hardcoded (not FAST_MODEL) — this
# engine is deliberately Sonnet; pricing_service prices this id.
HOLISTIC_MODEL = "claude-sonnet-4-6"
HOLISTIC_PROMPT_VERSION = "holistic_v1"

_REQ_CACHE_PREFIX = "holistic_reqs:v1:"
_REQ_CACHE_TTL = 7 * 24 * 3600  # 7 days; job-spec edits change the hash anyway
_CV_CHARS = 14000
_JD_CHARS = 8000
_WK_CHARS = 2500


# --------------------------------------------------------------------------
# LLM tool schemas (separate from the persisted CVMatchOutput contract)
# --------------------------------------------------------------------------
class _ReqItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    requirement: str
    importance: str = "important"  # critical | important | peripheral
    is_core: bool = False


class _Derivation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    core_capability: str = ""
    requirements: list[_ReqItem] = Field(default_factory=list)


class _HolisticScore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # The validated lean schema (iteration 4/5, 37/38 consistent). Deliberately
    # does NOT itemise per-requirement fit: forcing the model to score each
    # requirement in the same call rationalises the overall UPWARD (rejects
    # crept above the recruiter's reject line in a 38-cand A/B) and costs more
    # tokens. strengths/gaps are cheap descriptive lists that don't re-score,
    # so they're safe to keep for the report.
    overall: int = Field(default=0, ge=0, le=100)
    core_capability_score: int = Field(default=0, ge=0, le=100)
    verdict: str = ""
    reasoning: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Prompts (calibration text is the validated iteration-4/5 wording verbatim)
# --------------------------------------------------------------------------
_DERIVE_SYS = "Respond only via the tool."
_DERIVE_PROMPT = """Extract an ATOMIC requirements list from this JOB SPEC.
(1) split compound requirements into separate items;
(2) importance per item = critical (defines the job's core capability) / important / peripheral;
(3) mark is_core=true for the few items that ARE the core capability;
(4) define the CORE CAPABILITY framework-flexibly — name acceptable equivalent tools/stacks so a candidate who did the same work with a different tool still matches.

JOB SPEC:
{jd}"""

_SCORE_SYS = "You are a senior hiring manager. Respond only via the tool."
_SCORE_PROMPT = """You are scoring a candidate for this role, 0-100 — your real, calibrated hiring judgment.
CORE CAPABILITY (most important): {core}
REQUIREMENTS (importance noted):
{reqs}

Judge holistically how well this candidate fits, weighting the core capability and the CRITICAL requirements most. Credit DEMONSTRATED capability — including via EQUIVALENT tools and capability clearly IMPLIED by their work; discount mere tool-listing or support-only exposure; respect recency where specified.
Calibration — use the FULL range, be decisive, do NOT cluster at 0:
  75-100 strong genuine fit
  55-74  solid / partial fit (does the core work with gaps, OR a strong adjacent profile that ramps in)
  35-54  weak — lacks the core capability but has relevant transferable skills
  0-30   clear misfit — wrong profile ENTIRELY for this kind of work

Decide `overall` (the calibrated score) and `core_capability_score`, then a short `verdict`, a ONE-to-THREE sentence `reasoning`, and `strengths` / `gaps` (<=5 each, terse). Do not write prose outside the tool call.

{workable}CANDIDATE CV:
{cv}"""

def _redis():
    try:
        import redis

        from ..platform.config import settings

        return redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
    except Exception:  # pragma: no cover — cache is best-effort
        return None


def derive_requirements(
    job_spec_text: str,
    *,
    client: Any,
    organization_id: int | None = None,
    role_id: int | None = None,
    trace_id: str | None = None,
) -> _Derivation:
    """Sonnet atomic-requirements derivation, cached per job-spec hash."""
    jd = (job_spec_text or "").strip()
    if not jd:
        return _Derivation()

    key = _REQ_CACHE_PREFIX + hashlib.sha256(jd.encode("utf-8")).hexdigest()
    r = _redis()
    if r is not None:
        try:
            cached = r.get(key)
            if cached:
                return _Derivation.model_validate_json(cached)
        except Exception:  # pragma: no cover
            logger.warning("holistic derive cache read failed", exc_info=True)

    metering = MeteringContext(
        feature="archetype_synthesis",  # JD-level synthesis bucket
        organization_id=organization_id,
        role_id=role_id,
        entity_id=f"role:{role_id}" if role_id else None,
        trace_id=trace_id or uuid.uuid4().hex,
    )
    res = generate_structured(
        client,
        model=HOLISTIC_MODEL,
        messages=[{"role": "user", "content": _DERIVE_PROMPT.format(jd=jd[:_JD_CHARS])}],
        output_model=_Derivation,
        metering=metering,
        max_tokens=3000,
        system=_DERIVE_SYS,
        temperature=0.0,
        use_tool_use=True,
        tool_name="emit_requirements",
    )
    deriv = res.value if (res.ok and res.value) else _Derivation()
    if r is not None and deriv.requirements:
        try:
            r.setex(key, _REQ_CACHE_TTL, deriv.model_dump_json())
        except Exception:  # pragma: no cover
            logger.warning("holistic derive cache write failed", exc_info=True)
    return deriv


def run_holistic_match(
    cv_text: str,
    job_spec_text: str,
    *,
    client: Any,
    metering_context: dict | None = None,
    workable_context: str | None = None,
) -> CVMatchOutput:
    """Score one candidate with the holistic Sonnet engine.

    Signature mirrors the slice of ``run_cv_match`` the orchestrator calls,
    so it is a drop-in branch. Never raises — failures come back as a
    ``CVMatchOutput`` with ``scoring_status=FAILED``.
    """
    trace_id = uuid.uuid4().hex
    mc = metering_context or {}
    org_id = mc.get("organization_id")
    role_id = mc.get("role_id")
    entity_id = mc.get("entity_id")

    cv = (cv_text or "").strip()
    jd = (job_spec_text or "").strip()
    if not cv or not jd:
        return _failed_output("missing_inputs", trace_id)

    deriv = derive_requirements(
        jd, client=client, organization_id=org_id, role_id=role_id, trace_id=trace_id
    )
    reqblock = "\n".join(
        f"- ({r.importance}{'/CORE' if r.is_core else ''}) {r.requirement}"
        for r in deriv.requirements
    ) or "- (general) Overall fit to the role as described."
    wk = (
        f"WORKABLE CONTEXT (recruiter notes, questionnaire answers):\n{workable_context[:_WK_CHARS]}\n\n"
        if workable_context
        else ""
    )
    prompt = _SCORE_PROMPT.format(
        core=deriv.core_capability or "(infer from the job spec and requirements)",
        reqs=reqblock,
        workable=wk,
        cv=cv[:_CV_CHARS],
    )

    metering = (
        MeteringContext(
            feature="score",
            organization_id=org_id,
            role_id=role_id,
            entity_id=entity_id,
            trace_id=trace_id,
        )
        if metering_context
        else MeteringContext.skipped(metered_by="holistic_direct", trace_id=trace_id)
    )
    res = generate_structured(
        client,
        model=HOLISTIC_MODEL,
        messages=[{"role": "user", "content": prompt}],
        output_model=_HolisticScore,
        metering=metering,
        max_tokens=4000,
        system=_SCORE_SYS,
        temperature=0.0,
        use_tool_use=True,
        tool_name="score_candidate",
    )
    if not (res.ok and res.value):
        return _failed_output(
            f"holistic_score_failed: {res.error_reason}", trace_id, usage=res.usage
        )
    return _to_output(res.value, deriv, trace_id, res)


def _to_output(s: _HolisticScore, deriv: _Derivation, trace_id: str, res: Any) -> CVMatchOutput:
    overall = float(max(0, min(100, int(s.overall))))

    # The report's requirements section lists the Sonnet-derived ATOMIC
    # requirements that were considered (a better basis than the old regex
    # criteria). They're not individually graded here — per-requirement
    # evidence is a deliberate fast-follow, kept out of the scoring call so
    # it can't bias the calibrated overall. status=UNKNOWN / match_score=-1
    # marks them "considered, not separately graded".
    reqs: list[RequirementAssessment] = []
    for i, r in enumerate(deriv.requirements or []):
        crit = (r.importance or "").strip().lower() == "critical"
        priority = (
            Priority.MUST_HAVE if (r.is_core or crit) else Priority.STRONG_PREFERENCE
        )
        reqs.append(
            RequirementAssessment(
                requirement_id=f"holistic_{i}",
                requirement=(r.requirement or "(requirement)")[:300],
                priority=priority,
                status=Status.UNKNOWN,
                match_tier="missing",
                confidence=Confidence.MEDIUM,
                match_score=-1,
                assessable=False,
            )
        )
    summary = ((s.verdict + " — ") if s.verdict else "") + (s.reasoning or "")
    usage = getattr(res, "usage", None)

    return CVMatchOutput(
        prompt_version=HOLISTIC_PROMPT_VERSION,
        dimension_scores=None,
        requirements_assessment=reqs,
        matching_skills=[x[:120] for x in (s.strengths or [])[:5]],
        missing_skills=[x[:120] for x in (s.gaps or [])[:5]],
        experience_highlights=[x[:200] for x in (s.strengths or [])[:5]],
        concerns=[x[:200] for x in (s.gaps or [])[:5]],
        summary=summary[:2000],
        requirements_match_score=overall,  # display fallback; reqs not separately graded
        cv_fit_score=overall,  # display fallback; no per-dimension breakdown
        role_fit_score=overall,  # <-- validated holistic score, NOT re-aggregated
        skills_match_score=overall,
        experience_relevance_score=overall,
        score_scale="0-100",
        scoring_status=ScoringStatus.OK,
        model_version=HOLISTIC_MODEL,
        trace_id=trace_id,
        cache_hit=False,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_tokens", 0) or 0),
        cache_creation_tokens=int(getattr(usage, "cache_creation_tokens", 0) or 0),
        retry_count=int(getattr(res, "retry_count", 0) or 0),
        validation_failures=int(getattr(res, "validation_failures", 0) or 0),
    )


def _failed_output(reason: str, trace_id: str, usage: Any = None) -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=HOLISTIC_PROMPT_VERSION,
        scoring_status=ScoringStatus.FAILED,
        error_reason=reason[:500],
        role_fit_score=0.0,
        model_version=HOLISTIC_MODEL,
        trace_id=trace_id,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )
