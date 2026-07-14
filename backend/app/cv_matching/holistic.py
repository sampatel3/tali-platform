"""Holistic Sonnet scoring engine (``cv_match holistic_v1``).

Drop-in alternative to the Haiku ``run_cv_match`` pipeline, activated
per-org behind ``HOLISTIC_SCORING_ENABLED`` / ``HOLISTIC_SCORING_ORG_IDS``.
Two Sonnet stages:

  1. **Requirements derivation** — atomic split + criticality + a
     *framework-flexible* core-capability definition (acceptable
     equivalents named), replacing the regex/criteria bundling. Cached
     per job-spec hash in Redis so it amortises across a role's cohort.

  2. **Scoring** — ONE holistic Sonnet call that produces the COMPLETE
     report the candidate surfaces render: the calibrated ``overall``
     (→ ``role_fit_score`` directly — no ``0.40·cv_fit + 0.60·req_match``
     aggregation, that's the miscalibration this replaces), plus the
     candidate snapshot (years / tech-stack / employers), the six
     dimensions, and a per-requirement assessment WITH verbatim CV
     evidence. The detail comes from the same judgment that sets the
     score — there is no second "enrichment" pass and nothing is left
     ungraded. Requirements are graded by INDEX (not re-emitting their
     text) so the rich output stays roughly cost-neutral with the old
     pipeline.

Returns a fully-populated :class:`CVMatchOutput` so EVERY downstream
surface keeps working: the decision policy reads ``role_fit_score``; the
report reads ``candidate_snapshot`` / ``dimension_scores`` /
``requirements_assessment`` / ``summary`` / skills.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..llm import MeteringContext, fuzzy_locate, generate_structured
from .cache import compute_cache_key, get as _cache_get, set as _cache_set
from ..services.fraud_detection import (
    apply_grounding_discount,
    apply_integrity_penalty,
    build_integrity_signals_payload,
    compute_grounding_coverage,
    compute_integrity_penalty,
    detect_timeline_inconsistencies,
)
from .schemas import (
    CandidateSnapshot,
    ClaimToVerify,
    Confidence,
    CVMatchOutput,
    DimensionScores,
    Priority,
    RequirementAssessment,
    ScoringStatus,
    Status,
    TimelineEntry,
)

logger = logging.getLogger(__name__)

# Deliberately Sonnet (not FAST_MODEL); pricing_service prices this id.
HOLISTIC_MODEL = "claude-sonnet-4-6"
# prompt_version identifies the wire shape; ENGINE_VERSION is the semantic
# a.b.c surfaced to recruiters. 2.x = the holistic Sonnet engine; 2.1.0 =
# the two-call complete-report build. Bump c when a calibration/prompt change
# alters scoring semantics, b for new report features, and a for an engine-
# architecture change. Presentation-only prompt revisions use prompt_version.
# Prompt-only revision: the score/report wire shape and semantic engine stay
# at v2 / 2.1.0, but the candidate-summary contract is now deliberately
# concise.  A distinct prompt version prevents the result cache from serving
# verbose summaries produced under the previous instruction.
HOLISTIC_PROMPT_VERSION = "holistic_v2_1"
HOLISTIC_ENGINE_VERSION = "2.1.0"


def resolve_engine_version(details: dict | None) -> str:
    """Map any stored cv_match_details to its semantic a.b.c engine version.

    New holistic outputs carry ``engine_version`` directly; legacy
    ``cv_match_vN`` blobs map to ``1.N.0``. Used by serializers to surface
    score provenance everywhere the score is shown.
    """
    if not isinstance(details, dict):
        return ""
    ev = (details.get("engine_version") or "").strip()
    if ev:
        return ev
    pv = (details.get("prompt_version") or "").strip()
    if pv.startswith("holistic_v2"):
        return HOLISTIC_ENGINE_VERSION
    if pv == "holistic_v1":
        return "2.0.0"
    if pv.startswith("cv_match_v"):
        n = pv.replace("cv_match_v", "").strip()
        return f"1.{n}.0" if n.isdigit() else "1.x"
    return ""


def is_engine_outdated(details: dict | None) -> bool:
    """True when a stored score was produced by an engine OLDER than the
    current default (:data:`HOLISTIC_ENGINE_VERSION`).

    The single source of truth for "this score is from an old model", shared by
    the agent-chat re-score offer (``agent_chat.rescore``) and the
    decision-staleness banner (``services.decision_staleness``). An unscored
    blob (no resolvable version) is NOT outdated — it's simply unscored — so
    this returns ``False`` there. Pure version comparison; org-gating (does
    re-scoring this app actually move it forward?) lives in
    ``cv_score_orchestrator.score_is_outdated``.
    """
    ev = resolve_engine_version(details if isinstance(details, dict) else {})
    return bool(ev) and ev != HOLISTIC_ENGINE_VERSION

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


class _TL(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: str = ""
    role: str = ""
    start_year: int | None = None
    end_year: int | None = None
    is_current: bool = False


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    years_experience: float | None = None
    top_skills: list[str] = Field(default_factory=list)
    timeline: list[_TL] = Field(default_factory=list)


class _Dims(BaseModel):
    model_config = ConfigDict(extra="ignore")
    skills_coverage: int = Field(default=0, ge=0, le=100)
    skills_depth: int = Field(default=0, ge=0, le=100)
    title_trajectory: int = Field(default=0, ge=0, le=100)
    seniority_alignment: int = Field(default=0, ge=0, le=100)
    industry_match: int = Field(default=0, ge=0, le=100)
    tenure_pattern: int = Field(default=0, ge=0, le=100)


class _ReqGrade(BaseModel):
    model_config = ConfigDict(extra="ignore")
    index: int = -1  # refers to the numbered requirement in the prompt
    status: str = "unknown"  # met | partial | missing | unknown
    score: int = Field(default=-1, ge=-1, le=100)
    evidence: str = ""  # SHORT verbatim quote copied from the CV
    impact: str = ""  # why a gap matters (shown when not met)


class _LeanScore(BaseModel):
    """Call 1 — the calibrated holistic judgment. Deliberately has NO
    per-requirement itemisation: grading each requirement in the same call
    inflates ``overall`` upward (rejects creep above the bar — measured
    8/38 vs 6/38 GT-inconsistent). Skills lists are flat descriptors, not
    a re-score, so they're safe to keep here for the summary."""

    model_config = ConfigDict(extra="ignore")
    # ``overall`` is REQUIRED (no default) on purpose: it maps directly to
    # ``role_fit_score`` (see ``_to_output``). A default here let a degraded
    # but schema-valid tool emission that omitted ``overall`` validate as
    # ok=True with overall=0 → the orchestrator persisted cv_match_score=0
    # with status OK = a silent 0-score auto-reject of a real candidate.
    # Required → an absent field raises ValidationFailure, which the
    # structured layer retries (with feedback) and, only if still missing,
    # returns ok=False → ``_failed_output`` (FAILED → cv_match_score=None,
    # retried later — never a 0 auto-reject). A genuine model-emitted
    # ``overall=0`` (real clear-misfit verdict) is a valid int and still passes.
    overall: int = Field(ge=0, le=100)
    core_capability_score: int = Field(default=0, ge=0, le=100)
    verdict: str = Field(
        default="",
        max_length=60,
        description=(
            "A 2-4 word plain-English fit label, such as Strong fit, "
            "Partial fit, Weak fit, or Clear misfit."
        ),
    )
    reasoning: str = Field(
        default="",
        max_length=1000,
        description=(
            "The candidate summary: 2-3 concise plain-English sentences, "
            "aiming for about 75 words rather than a hard word cutoff. State "
            "the strongest role-relevant evidence and the one or two most "
            "material gaps or uncertainties; leave supporting detail to the "
            "candidate report."
        ),
    )
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class _Claim(BaseModel):
    """One extraordinary, externally-verifiable claim the report flags for human
    review (award / competition placement / publication / certification). Mirror
    of ``schemas.ClaimToVerify``; plain-string fields fail open on anything
    unrecognised so the bounded integrity penalty never bites a real-but-obscure
    achievement."""

    model_config = ConfigDict(extra="ignore")
    claim_text: str = ""
    claim_type: str = ""  # award | competition | publication | certification | employer | other
    corroboration: str = ""  # corroborated | uncorroborated
    model_familiarity: str = ""  # known | unknown | implausible
    reasoning: str = ""


class _Report(BaseModel):
    """Call 2 — the descriptive report. Snapshot + dimensions + factual
    per-requirement grades with verbatim evidence. Never feeds the score,
    so its itemisation can't bias ``overall``."""

    model_config = ConfigDict(extra="ignore")
    snapshot: _Snapshot = Field(default_factory=_Snapshot)
    dimensions: _Dims = Field(default_factory=_Dims)
    requirements: list[_ReqGrade] = Field(default_factory=list)
    claims: list[_Claim] = Field(default_factory=list)


_GRADE_TO_STATUS = {
    "met": Status.MET,
    "partial": Status.PARTIALLY_MET,
    "partially_met": Status.PARTIALLY_MET,
    "missing": Status.MISSING,
    "unknown": Status.UNKNOWN,
    "": Status.UNKNOWN,
}


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
_DERIVE_SYS = "Respond only via the tool."
_DERIVE_PROMPT = """Extract an ATOMIC requirements list from this JOB SPEC.
(1) split compound requirements into separate items;
(2) importance per item = critical (defines the job's core capability) / important / peripheral;
(3) mark is_core=true for the few items that ARE the core capability;
(4) define the CORE CAPABILITY framework-flexibly — name acceptable equivalent tools/stacks so a candidate who did the same work with a different tool still matches.

JOB SPEC:
{jd}"""

# Prompt-caching split (billing-only; the model receives the SAME instruction
# text + CV as before). The stable, per-role portion of each call — persona +
# rubric + core capability + requirements, byte-identical for every candidate
# in a role's cohort/re-score batch — lives in a ``cache_control``'d SYSTEM
# block (see ``_cached_system``); only the per-candidate CV + Workable context
# go in the (uncached) user message. Caching on a user-message block produced
# ZERO prod hits — the system param is the canonical fix (mirrors
# ``prompts_pre_screen.build_pre_screen_system``). This changes neither the
# model nor the scoring rubric, only where the (unchanged) text is placed so
# Anthropic can amortise the prefix across the cohort.
_SCORE_SYS = """You are a senior hiring manager. Respond only via the tool.

Score this candidate for the role, 0-100 — your real, calibrated hiring judgment.

CORE CAPABILITY (most important): {core}
REQUIREMENTS (importance noted):
{reqs}

Judge holistically how well this candidate fits, weighting the core capability and the CRITICAL requirements most. Credit DEMONSTRATED capability — including via EQUIVALENT tools and capability clearly IMPLIED by their work; discount mere tool-listing or support-only exposure; respect recency where specified.

Calibration for `overall` — use the FULL range, be decisive, do NOT cluster at 0:
  75-100 strong genuine fit
  55-74  solid / partial fit (does the core work with gaps, OR a strong adjacent profile that ramps in)
  35-54  weak — lacks the core capability but has relevant transferable skills
  0-30   clear misfit — wrong profile ENTIRELY for this kind of work

`overall` is your holistic calibrated judgment, weighting the core capability most — NOT a tally of how many requirements are partially met.

For the recruiter-facing candidate summary:
- `verdict`: ONLY a 2-4 word plain-English fit label (for example: Strong fit, Partial fit, Weak fit, Clear misfit).
- `reasoning`: 2-3 concise plain-English sentences, aiming for about 75 words. This is a guide, not a hard word cutoff. Name the strongest role-relevant evidence and the one or two most material gaps or uncertainties.
- This is a synthesis, not the report. Do NOT list every requirement, tool, employer, project, score, or caveat; the structured candidate report below carries that detail. Do NOT repeat the verdict in `reasoning`.

Then return `matching_skills` (role-relevant skills present), `missing_skills` (role-relevant skills absent), `highlights` (top achievements), and `concerns` (risks) — terse, <=5 each. Do not write prose outside the tool call."""

_SCORE_USER = """{workable}CANDIDATE CV:
{cv}"""

_REPORT_SYS = """You extract structured candidate-report facts. Respond only via the tool.

Produce the structured report facts for this candidate against the role.

CORE CAPABILITY: {core}
REQUIREMENTS (numbered):
{reqs}

Produce:
- `snapshot`: years_experience (number), top_skills (the 6-strongest tech stack), timeline (up to 5 most-recent employers, most recent FIRST: company, role, start_year, end_year [null if current], is_current).
- `dimensions` (each 0-100): skills_coverage, skills_depth, title_trajectory, seniority_alignment, industry_match, tenure_pattern.
- `requirements`: one row per NUMBERED requirement above, referencing its `index`, with status (met|partial|missing|unknown), score 0-100 for how well the CV satisfies it, a SHORT `evidence` quote copied VERBATIM from the CV (empty if unknown/missing), and `impact` (why a gap matters). Credit equivalent tools and clearly-implied capability; "unknown" only when the CV gives no signal either way.
- `claims`: list any EXTRAORDINARY, externally-verifiable claims on the CV (a named award, competition placement, publication, or named certification — NOT ordinary duties). For each: `claim_text`, `claim_type` (award|competition|publication|certification|employer|other), `corroboration` (corroborated if the CV gives concrete supporting context — employer/date/role/specifics — else uncorroborated), `model_familiarity` (known if you recognise the named event/credential as a real thing, unknown if you don't, implausible if it likely doesn't exist), and a one-line `reasoning`. Empty list if none. This is a flag for human review, never a judgement — do not lower the score for it.

Do not write prose outside the tool call."""

_REPORT_USER = """{workable}CANDIDATE CV:
{cv}"""


def _cached_system(text: str) -> list[dict]:
    """Wrap a stable per-role prefix in a ``cache_control``'d system block.

    Mirrors ``prompts_pre_screen.build_pre_screen_system``: Anthropic caches
    the prefix reliably only when it lives in the ``system`` param (a
    user-message cache block produced zero prod hits). 1h TTL keeps the cache
    warm across a role's cohort / re-score batch and across worker-queue gaps;
    a cache READ refreshes the TTL, so a continuous batch holds one write and
    pays ~0.1x input on every subsequent candidate. Breaks even at >=2
    candidates per role within the window — virtually always true for the
    cohort scoring + base-wide re-scores that dominate spend.
    """
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


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
    """Score one candidate + produce the complete report (two Sonnet calls).

    Call 1 is the calibrated holistic score; call 2 is the descriptive
    report (snapshot + dimensions + per-requirement grades). They're split
    so the report's itemisation can't inflate the score. Mirrors the slice
    of ``run_cv_match`` the orchestrator calls, so it is a drop-in branch.
    Never raises — failures come back as a ``CVMatchOutput`` with
    ``scoring_status=FAILED``. A failed report (call 2) still yields a valid
    scored output, just without the report detail.
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

    from ..platform.config import settings

    # Document hygiene — strip hidden text / prompt-injection from the CV BEFORE
    # it reaches either Sonnet call. The injection payload lands in cv_text after
    # PDF extraction, so sanitising the text the model sees is the actual defence
    # (the stored cv_text used for grounding is untouched). Detection always runs
    # for persistence; stripping is gated by CV_HIDDEN_TEXT_STRIP_ENABLED.
    hygiene = None
    if settings.CV_DOCUMENT_HYGIENE_ENABLED:
        from ..services.document_hygiene import sanitize_cv_for_llm

        cv_for_llm, hygiene = sanitize_cv_for_llm(
            cv, strip=settings.CV_HIDDEN_TEXT_STRIP_ENABLED
        )
        cv = (cv_for_llm or "").strip() or cv

    # Shared-result cache (same table run_cv_match uses) — an identical
    # re-score of an unchanged CV/JD/workable-context returns at ~zero
    # Anthropic cost instead of re-firing both Sonnet calls.
    cache_key = compute_cache_key(
        cv_text=cv, jd_text=jd, requirements=[],
        # Key on the engine version too, so a logic/calibration fix (which
        # bumps engine_version without changing the prompt) invalidates stale
        # cached scores instead of serving the old result. The integrity-penalty
        # flag is keyed in as well, so flipping it on re-scores (applies the
        # deduction) rather than serving a cached un-penalised score.
        prompt_version=(
            f"{HOLISTIC_PROMPT_VERSION}+{HOLISTIC_ENGINE_VERSION}"
            f"{'+ip' if settings.HOLISTIC_INTEGRITY_PENALTY_ENABLED else ''}"
            f"{'+htcap' if settings.FRAUD_HIDDEN_TEXT_ACTION == 'cap' else ''}"
            f"{'+gd' if settings.GROUNDING_COVERAGE_DISCOUNT_ENABLED else ''}"
        ),
        model_version=HOLISTIC_MODEL,
        workable_context=workable_context or "",
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        cached.cache_hit = True
        return cached

    deriv = derive_requirements(
        jd, client=client, organization_id=org_id, role_id=role_id, trace_id=trace_id
    )
    core = deriv.core_capability or "(infer from the job spec and requirements)"
    reqblock = "\n".join(
        f"{i}: ({r.importance}{'/CORE' if r.is_core else ''}) {r.requirement}"
        for i, r in enumerate(deriv.requirements)
    ) or "0: Overall fit to the role as described."
    wk = (
        f"WORKABLE CONTEXT (recruiter notes, questionnaire answers):\n{workable_context[:_WK_CHARS]}\n\n"
        if workable_context
        else ""
    )

    def _meter():
        return (
            MeteringContext(feature="score", organization_id=org_id, role_id=role_id,
                            entity_id=entity_id, trace_id=trace_id)
            if metering_context
            else MeteringContext.skipped(metered_by="holistic_direct", trace_id=trace_id)
        )

    # Call 1 — calibrated score. Stable rubric + requirements ride a cached
    # system block; only the CV + Workable context vary per candidate.
    score_res = generate_structured(
        client, model=HOLISTIC_MODEL,
        messages=[{"role": "user", "content": _SCORE_USER.format(workable=wk, cv=cv[:_CV_CHARS])}],
        output_model=_LeanScore, metering=_meter(), max_tokens=2000,
        system=_cached_system(_SCORE_SYS.format(core=core, reqs=reqblock)),
        temperature=0.0, use_tool_use=True, tool_name="score_candidate",
    )
    if not (score_res.ok and score_res.value):
        return _failed_output(
            f"holistic_score_failed: {score_res.error_reason}", trace_id, usage=score_res.usage
        )

    # Call 2 — descriptive report (best-effort; never fails the score). Same
    # cached-system / per-candidate-user split as call 1.
    report_res = generate_structured(
        client, model=HOLISTIC_MODEL,
        messages=[{"role": "user", "content": _REPORT_USER.format(workable=wk, cv=cv[:_CV_CHARS])}],
        output_model=_Report, metering=_meter(), max_tokens=5000,
        system=_cached_system(_REPORT_SYS.format(core=core, reqs=reqblock)),
        temperature=0.0, use_tool_use=True, tool_name="emit_report_facts",
    )
    report = report_res.value if (report_res.ok and report_res.value) else _Report()

    out = _to_output(score_res.value, report, deriv, trace_id, score_res, report_res, hygiene=hygiene)
    if (
        hygiene is not None
        and settings.FRAUD_HIDDEN_TEXT_ACTION == "cap"
        and (hygiene.injection_detected or hygiene.has_tag_chars)
    ):
        out = _cap_for_hidden_text(out, settings.FRAUD_PENALTY_CAP_SCORE)
    try:
        _ground_quotes(out, cv)
    except Exception:  # pragma: no cover — never fail a score on grounding
        logger.warning("holistic grounding pass failed", exc_info=True)

    # Prong 1 — evidence-grounded score integrity (anti spec-gaming). AFTER
    # grounding drops un-locatable quotes, measure how much of the MUST-HAVE
    # match is backed by verbatim CV evidence vs bare, spec-echoing assertions.
    # `high match × low grounding coverage` is the gamed-suspect tell — a high
    # match alone never is. Always computed + persisted; the bounded discount is
    # gated (default shadow) and keyed into the cache so a flip re-scores.
    coverage = compute_grounding_coverage(
        out.requirements_assessment,
        out.role_fit_score,
        high_match_threshold=settings.GROUNDING_COVERAGE_HIGH_MATCH,
        low_coverage_threshold=settings.GROUNDING_COVERAGE_LOW,
        min_must_haves=settings.GROUNDING_COVERAGE_MIN_MUSTHAVES,
    )
    sig = dict(out.integrity_signals or {})
    sig["grounding"] = coverage.to_dict()
    discount = 0.0
    new_score = out.role_fit_score
    if settings.GROUNDING_COVERAGE_DISCOUNT_ENABLED:
        new_score, discount = apply_grounding_discount(
            out.role_fit_score, coverage, max_discount=settings.GROUNDING_COVERAGE_MAX_DISCOUNT
        )
    sig["grounding"]["discount_applied"] = discount
    if discount > 0:
        out = out.model_copy(
            update={
                "role_fit_score": new_score,
                "cv_fit_score": new_score,
                "requirements_match_score": new_score,
                "skills_match_score": new_score,
                "experience_relevance_score": new_score,
                "integrity_signals": sig,
            }
        )
    else:
        out = out.model_copy(update={"integrity_signals": sig})

    _cache_set(cache_key, out)
    return out


def _cap_for_hidden_text(out: CVMatchOutput, cap: float) -> CVMatchOutput:
    """Cap a holistic score because the CV carried a hidden-text / prompt-
    injection payload aimed at the screener (``FRAUD_HIDDEN_TEXT_ACTION='cap'``).
    The attempt itself is the signal — independent of whether stripping already
    neutralised it — so all score fields drop to ``cap`` and the summary says
    why. Detection + stripping happen regardless of the action; only this hard
    cap is gated behind the opt-in setting."""
    sig = dict(out.integrity_signals or {})
    dh = dict(sig.get("document_hygiene") or {})
    dh["action"] = "capped"
    sig["document_hygiene"] = dh
    note = (
        "Filtered: the CV contains hidden text or a prompt-injection payload "
        "aimed at the automated screener. "
    )
    return out.model_copy(
        update={
            "role_fit_score": cap,
            "cv_fit_score": cap,
            "requirements_match_score": cap,
            "skills_match_score": cap,
            "experience_relevance_score": cap,
            "integrity_signals": sig,
            "summary": note + (out.summary or ""),
        }
    )


def _ground_quotes(out: CVMatchOutput, cv: str) -> None:
    """Verify evidence citations without erasing the per-requirement judgment.

    Each requirement's status/score is the model's holistic assessment
    (call 2), NOT derived from the quote — so unlike the canonical
    ``validate_evidence_grounding`` (which downgrades to UNKNOWN when no quote
    survives), we DROP any quote that doesn't fuzzy-locate in the CV but KEEP
    the status/score/impact. Net effect: a paraphrased citation is removed so
    it can never be shown as a verbatim quote, but the assessment stands — and
    downstream "grounded = status met/partial AND has quotes" correctly treats
    a quote-less requirement as ungrounded.
    """
    for ra in out.requirements_assessment:
        kept: list[str] = []
        first: tuple[int, int] | None = None
        for q in (ra.evidence_quotes or []):
            located = fuzzy_locate(q, cv)
            if located is not None:
                kept.append(q)
                if first is None:
                    first = located
        ra.evidence_quotes = kept
        if first is not None:
            ra.evidence_start_char, ra.evidence_end_char = first[0], first[1]
        else:
            ra.evidence_start_char = ra.evidence_end_char = -1


def _tier_from_score(score: int) -> str:
    return (
        "exact" if score >= 75
        else "strong_substitute" if score >= 55
        else "weak_substitute" if score >= 35
        else "missing"
    )


def _snapshot_from(sn: _Snapshot) -> CandidateSnapshot | None:
    if sn.years_experience is None and not sn.top_skills and not sn.timeline:
        return None
    return CandidateSnapshot(
        years_experience=sn.years_experience,
        top_skills=[x[:60] for x in (sn.top_skills or [])[:6]],
        timeline=[
            TimelineEntry(
                company=(t.company or "")[:120],
                role=(t.role or "")[:120],
                start_year=t.start_year,
                end_year=t.end_year,
                is_current=bool(t.is_current),
            )
            for t in (sn.timeline or [])[:5]
        ],
    )


def _claims_from(report: _Report) -> list[ClaimToVerify]:
    """Map the report's flagged extraordinary claims to the persisted
    ``ClaimToVerify`` shape (truncated; capped at 10). Empty claims dropped."""
    out: list[ClaimToVerify] = []
    for c in (report.claims or [])[:10]:
        ct = (c.claim_text or "").strip()
        if not ct:
            continue
        out.append(
            ClaimToVerify(
                claim_text=ct[:300],
                claim_type=(c.claim_type or "")[:40],
                corroboration=(c.corroboration or "")[:40],
                model_familiarity=(c.model_familiarity or "")[:40],
                reasoning=(c.reasoning or "")[:300],
            )
        )
    return out


def _dimensions_from(d: _Dims) -> DimensionScores:
    return DimensionScores(
        skills_coverage=float(d.skills_coverage),
        skills_depth=float(d.skills_depth),
        title_trajectory=float(d.title_trajectory),
        seniority_alignment=float(d.seniority_alignment),
        industry_match=float(d.industry_match),
        tenure_pattern=float(d.tenure_pattern),
    )


def _requirements_from(
    grades: list[_ReqGrade], deriv: _Derivation
) -> list[RequirementAssessment]:
    """Build the per-requirement assessment from the model's index-keyed grades.

    Priority comes from the derivation (is_core / critical → must_have).
    Evidence quotes pass through verbatim; the caller then runs the canonical
    ``validate_evidence_grounding`` pass, which fuzzy-locates each quote,
    DROPS any that isn't a real CV substring and downgrades the status — so a
    fabricated / paraphrased quote can never surface as cited evidence.
    """
    derived = deriv.requirements or []
    by_index = {g.index: g for g in (grades or []) if 0 <= g.index < len(derived)}
    out: list[RequirementAssessment] = []
    for i, item in enumerate(derived):
        crit = (item.importance or "").strip().lower() == "critical"
        priority = Priority.MUST_HAVE if (item.is_core or crit) else Priority.STRONG_PREFERENCE
        g = by_index.get(i)
        status = Status.UNKNOWN
        match_score = -1
        tier = "missing"
        quotes: list[str] = []
        reasoning = ""
        impact = ""
        if g is not None:
            status = _GRADE_TO_STATUS.get((g.status or "").strip().lower(), Status.UNKNOWN)
            score = int(g.score)
            if 0 <= score <= 100:
                match_score = score
                tier = _tier_from_score(score)
            elif status == Status.MET:  # model graded met but omitted a score
                tier = "exact"
            elif status == Status.PARTIALLY_MET:
                tier = "weak_substitute"
            ev = (g.evidence or "").strip()
            if ev:
                quotes = [ev[:300]]
                reasoning = ev[:300]
            impact = (g.impact or "")[:300]
        out.append(
            RequirementAssessment(
                requirement_id=f"holistic_{i}",
                requirement=(item.requirement or "(requirement)")[:300],
                priority=priority,
                evidence_quotes=quotes,
                reasoning=reasoning,
                status=status,
                match_tier=tier,
                impact=impact,
                confidence=Confidence.MEDIUM,
                match_score=match_score,
                assessable=status != Status.UNKNOWN,
            )
        )
    return out


def _usage_sum(*results: Any) -> dict:
    tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
           "cache_creation_tokens": 0, "retry_count": 0, "validation_failures": 0}
    for r in results:
        if r is None:
            continue
        u = getattr(r, "usage", None)
        tot["input_tokens"] += int(getattr(u, "input_tokens", 0) or 0)
        tot["output_tokens"] += int(getattr(u, "output_tokens", 0) or 0)
        tot["cache_read_tokens"] += int(getattr(u, "cache_read_tokens", 0) or 0)
        tot["cache_creation_tokens"] += int(getattr(u, "cache_creation_tokens", 0) or 0)
        tot["retry_count"] += int(getattr(r, "retry_count", 0) or 0)
        tot["validation_failures"] += int(getattr(r, "validation_failures", 0) or 0)
    return tot


def _to_output(
    s: _LeanScore,
    report: _Report,
    deriv: _Derivation,
    trace_id: str,
    score_res: Any,
    report_res: Any = None,
    hygiene: Any = None,
) -> CVMatchOutput:
    overall = float(max(0, min(100, int(s.overall))))
    reqs = _requirements_from(report.requirements, deriv)
    summary = ((s.verdict + " — ") if s.verdict else "") + (s.reasoning or "")
    u = _usage_sum(score_res, report_res)

    # Deterministic CV-integrity layer — timeline sanity + unverified claims.
    # Computed (and persisted) on EVERY holistic score; the penalty is only
    # DEDUCTED when HOLISTIC_INTEGRITY_PENALTY_ENABLED, so the signal ships in
    # shadow first and the rollout is flipped deliberately. (The legacy
    # run_cv_match path always deducts; here the deduction is gated because
    # holistic is the platform-wide default and would move live scores.)
    from ..platform.config import settings

    timeline_entries = [
        {
            "company": t.company, "role": t.role,
            "start_year": t.start_year, "end_year": t.end_year,
            "is_current": t.is_current,
        }
        for t in (report.snapshot.timeline or [])
    ]
    timeline_result = detect_timeline_inconsistencies(timeline_entries)
    claims = _claims_from(report)
    integrity = compute_integrity_penalty(
        claims, timeline_result,
        points_per_issue=settings.FRAUD_INTEGRITY_PENALTY_POINTS,
        max_penalty=settings.FRAUD_INTEGRITY_PENALTY_MAX,
    )
    apply_penalty = bool(settings.HOLISTIC_INTEGRITY_PENALTY_ENABLED)
    score = apply_integrity_penalty(overall, integrity.penalty) if apply_penalty else overall
    # Decision-2 requirement: when the penalty is actually deducted, log one
    # structured before/after line so the flip's live impact is auditable (which
    # candidates dropped, by how much, and whether it crossed a decision cutoff).
    if apply_penalty and integrity.penalty > 0:
        logger.info(
            "holistic integrity penalty applied trace=%s pre_penalty=%.2f "
            "penalty=%.2f post_penalty=%.2f timeline_issues=%d unverified_claims=%d",
            trace_id, overall, integrity.penalty, score,
            integrity.timeline_issue_count, integrity.unverified_claim_count,
        )
    integrity_signals = build_integrity_signals_payload(integrity, timeline_result)
    integrity_signals["applied"] = apply_penalty
    integrity_signals["penalty_computed"] = round(integrity.penalty, 2)
    if hygiene is not None:
        integrity_signals["document_hygiene"] = hygiene.to_dict()

    return CVMatchOutput(
        prompt_version=HOLISTIC_PROMPT_VERSION,
        engine_version=HOLISTIC_ENGINE_VERSION,
        dimension_scores=_dimensions_from(report.dimensions),
        candidate_snapshot=_snapshot_from(report.snapshot),
        requirements_assessment=reqs,
        matching_skills=[x[:120] for x in (s.matching_skills or [])[:8]],
        missing_skills=[x[:120] for x in (s.missing_skills or [])[:8]],
        experience_highlights=[x[:200] for x in (s.highlights or [])[:5]],
        concerns=[x[:200] for x in (s.concerns or [])[:5]],
        # Preserve Claude's authored summary verbatim (apart from the explicit
        # verdict separator above). Concision is a generation contract, not a
        # presentation-layer truncation rule.
        summary=summary,
        # CV-integrity surface — persisted on every score; timeline_flags +
        # claims_to_verify drive the "verify before interview" UI, and
        # integrity_signals.applied says whether the penalty was deducted.
        claims_to_verify=claims,
        timeline_flags=[i.detail for i in timeline_result.issues],
        integrity_penalty=(round(integrity.penalty, 2) if apply_penalty else 0.0),
        integrity_signals=integrity_signals,
        # role_fit_score = the holistic judgment (call 1), minus the integrity
        # penalty when enabled. cv_fit/requirements_match are kept == this so a
        # downstream 0.40·cv_fit+0.60·req_match recomposition returns the same.
        requirements_match_score=score,
        cv_fit_score=score,
        role_fit_score=score,
        skills_match_score=score,
        experience_relevance_score=score,
        score_scale="0-100",
        scoring_status=ScoringStatus.OK,
        model_version=HOLISTIC_MODEL,
        trace_id=trace_id,
        cache_hit=False,
        input_tokens=u["input_tokens"],
        output_tokens=u["output_tokens"],
        cache_read_tokens=u["cache_read_tokens"],
        cache_creation_tokens=u["cache_creation_tokens"],
        retry_count=u["retry_count"],
        validation_failures=u["validation_failures"],
    )


def _failed_output(reason: str, trace_id: str, usage: Any = None) -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=HOLISTIC_PROMPT_VERSION,
        engine_version=HOLISTIC_ENGINE_VERSION,
        scoring_status=ScoringStatus.FAILED,
        error_reason=reason[:500],
        role_fit_score=0.0,
        model_version=HOLISTIC_MODEL,
        trace_id=trace_id,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )
