"""Pydantic v2 schemas for the cv_matching pipeline.

Single scoring path. The LLM emits per-requirement assessments + six
dimension scores; ``aggregation.py`` derives ``requirements_match_score``,
``cv_fit_score``, ``role_fit_score``, and ``recommendation`` deterministically.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Priority(str, Enum):
    MUST_HAVE = "must_have"
    STRONG_PREFERENCE = "strong_preference"
    NICE_TO_HAVE = "nice_to_have"
    CONSTRAINT = "constraint"


class Category(str, Enum):
    TECHNICAL_SKILL = "technical_skill"
    DOMAIN_EXPERIENCE = "domain_experience"
    LOCATION = "location"
    LEADERSHIP = "leadership"
    STAKEHOLDER_MANAGEMENT = "stakeholder_management"
    TENURE = "tenure"
    CERTIFICATION = "certification"
    OTHER = "other"


class Status(str, Enum):
    MET = "met"
    PARTIALLY_MET = "partially_met"
    MISSING = "missing"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Recommendation(str, Enum):
    STRONG_YES = "strong_yes"
    YES = "yes"
    LEAN_NO = "lean_no"
    NO = "no"


class ScoringStatus(str, Enum):
    OK = "ok"
    DEFERRED = "deferred"
    FAILED = "failed"


MatchTier = Literal[
    "exact",
    "strong_substitute",
    "weak_substitute",
    "unrelated",
    "missing",
]


# ---------------------------------------------------------------------------
# Tolerant enum coercion
#
# The LLM frequently paraphrases enum values ("preferred" instead of
# "strong_preference", "must have" instead of "must_have", etc.). Strict
# validation forces a retry; tolerant coercion accepts common variants.
# Recruiters typing the same fields in admin UIs make the same kind of
# mistakes — same coercion handles both.
# ---------------------------------------------------------------------------


_PRIORITY_VARIANTS: dict[str, str] = {
    # canonical
    "must_have": "must_have",
    "strong_preference": "strong_preference",
    "nice_to_have": "nice_to_have",
    "constraint": "constraint",
    # must_have variants
    "must have": "must_have",
    "must-have": "must_have",
    "musthave": "must_have",
    "required": "must_have",
    "mandatory": "must_have",
    "essential": "must_have",
    "critical": "must_have",
    # strong_preference variants
    "strong preference": "strong_preference",
    "strong-preference": "strong_preference",
    "strongpreference": "strong_preference",
    "preferred": "strong_preference",
    "preference": "strong_preference",
    "preferences": "strong_preference",
    "desirable": "strong_preference",
    "highly preferred": "strong_preference",
    "highly desirable": "strong_preference",
    # nice_to_have variants
    "nice to have": "nice_to_have",
    "nice-to-have": "nice_to_have",
    "nicetohave": "nice_to_have",
    "optional": "nice_to_have",
    "bonus": "nice_to_have",
    "plus": "nice_to_have",
    # constraint variants
    "constraints": "constraint",
    "hard constraint": "constraint",
    "hard-constraint": "constraint",
    "disqualifying": "constraint",
    "disqualifier": "constraint",
}

_STATUS_VARIANTS: dict[str, str] = {
    # canonical
    "met": "met",
    "partially_met": "partially_met",
    "missing": "missing",
    "unknown": "unknown",
    # met
    "matches": "met",
    "satisfied": "met",
    "fulfilled": "met",
    "yes": "met",
    "true": "met",
    # partially_met
    "partially met": "partially_met",
    "partially-met": "partially_met",
    "partial": "partially_met",
    "partially": "partially_met",
    "partially_satisfied": "partially_met",
    # missing
    "not met": "missing",
    "not_met": "missing",
    "not-met": "missing",
    "absent": "missing",
    "no": "missing",
    "false": "missing",
    # unknown
    "uncertain": "unknown",
    "unclear": "unknown",
    "n/a": "unknown",
    "na": "unknown",
    "not applicable": "unknown",
    "not_applicable": "unknown",
    "no evidence": "unknown",
    "no_evidence": "unknown",
    "indeterminate": "unknown",
}

_MATCH_TIER_VARIANTS: dict[str, str] = {
    # canonical
    "exact": "exact",
    "strong_substitute": "strong_substitute",
    "weak_substitute": "weak_substitute",
    "unrelated": "unrelated",
    "missing": "missing",
    # exact
    "exact match": "exact",
    "exact_match": "exact",
    "perfect": "exact",
    "perfect match": "exact",
    # strong_substitute
    "strong substitute": "strong_substitute",
    "strong-substitute": "strong_substitute",
    "strong sub": "strong_substitute",
    "close match": "strong_substitute",
    "close-match": "strong_substitute",
    "similar": "strong_substitute",
    "equivalent": "strong_substitute",
    # weak_substitute
    "weak substitute": "weak_substitute",
    "weak-substitute": "weak_substitute",
    "weak sub": "weak_substitute",
    "loose match": "weak_substitute",
    "tangential": "weak_substitute",
    "related": "weak_substitute",
    # unrelated
    "not related": "unrelated",
    "not_related": "unrelated",
    "off topic": "unrelated",
    "off-topic": "unrelated",
    "irrelevant": "unrelated",
    # missing
    "absent": "missing",
    "none": "missing",
    "no match": "missing",
    "no_match": "missing",
}

_CONFIDENCE_VARIANTS: dict[str, str] = {
    # canonical
    "high": "high",
    "medium": "medium",
    "low": "low",
    # high
    "strong": "high",
    "confident": "high",
    "very high": "high",
    # medium
    "moderate": "medium",
    "med": "medium",
    "average": "medium",
    "mid": "medium",
    # low
    "weak": "low",
    "uncertain": "low",
    "very low": "low",
}


def _normalise_enum(value: Any, variants: dict[str, str]) -> Any:
    """Map common variants of an enum value to the canonical form.

    Non-string inputs pass through unchanged. Unrecognised strings also
    pass through (Pydantic's enum/Literal validation surfaces the error
    cleanly so the runner's retry kicks in with the original problem).
    """
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return variants.get(key, value)
"""Per-requirement classification of CV evidence vs JD ask.

Aggregation multiplies a tier weight on top of priority × status:
exact=1.0, strong_substitute=0.85, weak_substitute=0.55, unrelated=0.0,
missing=0.0.
"""


class RequirementInput(BaseModel):
    """Recruiter-added requirement, fed into the pipeline."""

    model_config = ConfigDict(extra="forbid")

    id: str
    requirement: str
    priority: Priority
    category: Category = Category.OTHER
    rationale: str = ""
    evidence_hints: list[str] = Field(default_factory=list)
    acceptable_alternatives: list[str] = Field(default_factory=list)
    depth_signal: str = ""
    disqualifying_if_missing: bool = False
    flag_only: bool = False

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, v):
        return _normalise_enum(v, _PRIORITY_VARIANTS)


class RequirementAssessment(BaseModel):
    """Per-requirement output from the LLM.

    Field ordering is deliberate: ``evidence_quotes`` and ``reasoning``
    appear BEFORE ``status``, ``match_tier``, ``impact``, ``confidence``
    because the autoregressive output of an LLM commits to earlier
    fields before later ones. Forcing evidence-first reduces score
    drift driven by status hallucination.

    Enum-valued fields (``priority``, ``status``, ``match_tier``,
    ``confidence``) accept common variants of the canonical values
    (e.g. "preferred" → "strong_preference", "must have" → "must_have").
    See the ``_VARIANTS`` tables above. This makes both the LLM's
    output and recruiter-typed input forgiving of small phrasing
    differences without forcing a retry / silent rejection.

    ``extra="ignore"``: the v9 prompt's anchoring instructions prompt
    the model to occasionally invent helper fields like
    ``reasoning_detail``. Strict ``forbid`` triggers a Claude retry that
    almost always fails the same way and burns the call. Ignoring the
    extras lands the score and drops the noise.
    """

    model_config = ConfigDict(extra="ignore")

    requirement_id: str
    requirement: str
    priority: Priority
    evidence_quotes: list[str] = Field(default_factory=list)
    evidence_start_char: int = -1
    evidence_end_char: int = -1
    reasoning: str = ""
    status: Status
    match_tier: MatchTier = "missing"
    impact: str = ""
    confidence: Confidence = Confidence.MEDIUM
    # Graded 0-100 fit for this requirement, populated by a focused second
    # pass (``cv_matching.graded``). ``-1`` = not graded → aggregation falls
    # back to the coarse status × tier weighting. ``assessable=False`` = no
    # evidence either way → excluded from the graded average (affects coverage
    # only, like ``status=unknown``). These two fields are SET by the runner
    # after the graded pass; the main scoring call does not produce them.
    match_score: int = Field(default=-1, ge=-1, le=100)
    assessable: bool = True

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, v):
        return _normalise_enum(v, _PRIORITY_VARIANTS)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        return _normalise_enum(v, _STATUS_VARIANTS)

    @field_validator("match_tier", mode="before")
    @classmethod
    def _coerce_match_tier(cls, v):
        return _normalise_enum(v, _MATCH_TIER_VARIANTS)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        return _normalise_enum(v, _CONFIDENCE_VARIANTS)


class DimensionScores(BaseModel):
    """Six 0-100 dimensions emitted by the LLM. Aggregation derives
    ``cv_fit_score`` as a weighted average using per-archetype weights
    (or default weights when no archetype matched).

    ``extra="ignore"`` — same reasoning as RequirementAssessment: the
    model sometimes adds helper fields (``concerns_flag``,
    ``confidence_band``) under v9's prompt; ignore vs. retry-and-fail.
    """

    model_config = ConfigDict(extra="ignore")

    skills_coverage: float = Field(ge=0, le=100)
    skills_depth: float = Field(ge=0, le=100)
    title_trajectory: float = Field(ge=0, le=100)
    seniority_alignment: float = Field(ge=0, le=100)
    industry_match: float = Field(ge=0, le=100)
    tenure_pattern: float = Field(ge=0, le=100)


def _truncate_to_5(v: Any) -> Any:
    """Truncate a list to its first 5 entries; pass non-lists through.

    The LLM occasionally emits 6+ items despite the prompt's "cap at 5"
    rule. Strict validation rejects → forces a retry. Truncation keeps
    the first 5 (autoregressively the most-relevant) and proceeds.
    """
    if isinstance(v, list) and len(v) > 5:
        return v[:5]
    return v


class TimelineEntry(BaseModel):
    """One employer/role on the at-a-glance career timeline.

    ``end_year`` is None when the candidate is still in role.
    ``start_year`` is None only when the CV omits the start date for that
    entry — the snapshot card still renders the row with a "—" placeholder.
    """

    model_config = ConfigDict(extra="ignore")

    company: str = ""
    role: str = ""
    start_year: int | None = None
    end_year: int | None = None
    is_current: bool = False


class CandidateSnapshot(BaseModel):
    """At-a-glance candidate snapshot for the recruiter/client summary card.

    Cheap secondary output the LLM emits alongside the scoring fields. The
    frontend renders it as a chip row above the prose ``summary``, so
    recruiters get years_experience / tech stack / last 3 employers without
    scrolling the full CV.

    ``extra="ignore"`` for the same reason as the other CV-match models —
    the LLM occasionally adds helper fields under the v9 anchoring prompt.
    """

    model_config = ConfigDict(extra="ignore")

    years_experience: float | None = None
    top_skills: list[str] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)

    @field_validator("top_skills", mode="before")
    @classmethod
    def _truncate_top_skills(cls, v):
        if isinstance(v, list) and len(v) > 6:
            return v[:6]
        return v

    @field_validator("timeline", mode="before")
    @classmethod
    def _truncate_timeline(cls, v):
        if isinstance(v, list) and len(v) > 5:
            return v[:5]
        return v


class ClaimToVerify(BaseModel):
    """An extraordinary, externally-verifiable claim asserted on the CV —
    e.g. "1st place, XYZ Global Hackathon 2023", an award, a named
    competition placement, a publication, a named certification.

    The LLM only *flags* these; it does not (and cannot reliably) verify
    them. ``corroboration`` records whether the surrounding CV gives the
    claim context (employer / role / date / concrete detail);
    ``model_familiarity`` is the model's prior on whether the named
    event/credential plausibly exists at all. The deterministic integrity
    penalty (``fraud_detection.compute_integrity_penalty``) only bites when
    a claim is BOTH uncorroborated AND low-familiarity — it fails open on
    anything unrecognised so a real-but-obscure achievement is never
    punished, and a recruiter still sees the flag either way.

    Plain-string fields (not Enum/Literal) on purpose: an unrecognised
    value degrades to "no penalty" rather than forcing a costly retry.
    """

    model_config = ConfigDict(extra="ignore")

    claim_text: str = ""
    claim_type: str = ""  # award | competition | publication | certification | employer | other
    corroboration: str = ""  # corroborated | uncorroborated
    model_familiarity: str = ""  # known | unknown | implausible
    reasoning: str = ""


def _truncate_to_10(v: Any) -> Any:
    if isinstance(v, list) and len(v) > 10:
        return v[:10]
    return v


class CVMatchResult(BaseModel):
    """Raw LLM output after JSON parsing.

    The legacy v3 ``skills_match_score`` / ``experience_relevance_score``
    pair is back-filled by aggregation from the six dimensions for
    downstream-consumer compatibility.

    ``extra="ignore"`` — see RequirementAssessment / DimensionScores.
    """

    model_config = ConfigDict(extra="ignore")

    prompt_version: str
    skills_match_score: float = Field(default=0.0, ge=0, le=100)
    experience_relevance_score: float = Field(default=0.0, ge=0, le=100)
    dimension_scores: DimensionScores | None = None
    requirements_assessment: list[RequirementAssessment] = Field(default_factory=list)
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    summary: str = ""
    candidate_snapshot: CandidateSnapshot | None = None
    claims_to_verify: list[ClaimToVerify] = Field(default_factory=list)

    @field_validator("experience_highlights", "concerns", mode="before")
    @classmethod
    def _truncate_capped_lists(cls, v):
        return _truncate_to_5(v)

    @field_validator("claims_to_verify", mode="before")
    @classmethod
    def _truncate_claims(cls, v):
        return _truncate_to_10(v)


class CVMatchOutput(BaseModel):
    """Final output after deterministic aggregation. Caller-facing.

    Wire shape for ``candidate_applications.cv_match_details``.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    prompt_version: str
    # Semantic scoring-engine version, a.b.c (a=major engine, b=feature,
    # c=fix). Empty on legacy engines; resolve_engine_version() maps those
    # from prompt_version. Surfaced under the score everywhere as provenance.
    engine_version: str = ""
    skills_match_score: float = Field(default=0.0, ge=0, le=100)
    experience_relevance_score: float = Field(default=0.0, ge=0, le=100)
    dimension_scores: DimensionScores | None = None
    requirements_assessment: list[RequirementAssessment] = Field(default_factory=list)
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    summary: str = ""
    candidate_snapshot: CandidateSnapshot | None = None
    # Extraordinary claims the model flagged for human verification (hackathon
    # wins, awards, publications). A flag for the recruiter, not a verdict.
    claims_to_verify: list[ClaimToVerify] = Field(default_factory=list)
    # Human-readable timeline inconsistencies found deterministically over the
    # extracted career timeline (future dates, end-before-start, etc.).
    timeline_flags: list[str] = Field(default_factory=list)
    # Points deducted from role_fit_score by the bounded integrity penalty
    # (unverified claims + timeline issues). 0.0 when clean. role_fit_score
    # below already reflects this deduction; this field makes it auditable.
    integrity_penalty: float = Field(default=0.0, ge=0, le=100)
    # Structured CV-integrity / fraud surface for the "verify before interview"
    # UI: penalty breakdown, timeline issues, and — when computed at scoring
    # time — document-hygiene (hidden-text / injection) findings. Built by
    # ``fraud_detection.build_integrity_signals_payload`` (+ extras); None on
    # engines/paths that don't compute it. ``applied`` says whether the penalty
    # was actually deducted (vs computed in shadow).
    integrity_signals: dict[str, Any] | None = None

    requirements_match_score: float = Field(default=0.0, ge=0, le=100)
    cv_fit_score: float = Field(default=0.0, ge=0, le=100)
    role_fit_score: float = Field(default=0.0, ge=0, le=100)
    # Belt-and-braces tag so downstream read-time normalizers can't
    # mistake a legitimate weak 0-100 score (e.g. 9.6) for a 0-10
    # value that needs to be multiplied. role_support's normalizer
    # branches on this field and skips the ``<=10 → ×10`` heuristic
    # when "100" is present.
    score_scale: str = "0-100"
    # Recommendation is no longer auto-derived from a fixed score
    # threshold. The recruiter sets a per-role reject threshold on the
    # job page; the UI renders the recommendation dynamically by
    # comparing ``role_fit_score`` against that user-configurable value.
    # Field kept on the schema so legacy consumers don't crash on its
    # absence; never populated by the runner. Override capture
    # endpoints in routes.py still use the Recommendation enum for
    # *recruiter-supplied* override values — that's a different surface.
    recommendation: Recommendation | None = None

    injection_suspected: bool = False
    suspicious_score: bool = False

    scoring_status: ScoringStatus = ScoringStatus.OK
    error_reason: str = ""
    model_version: str = ""
    trace_id: str = ""
    cache_hit: bool = False

    # Calibrated probability the candidate would be advanced.
    # None when no calibrator exists for the role family yet.
    calibrated_p_advance: float | None = None
    # Self-consistency uncertainty band on borderline cases (None otherwise).
    score_std: float | None = None

    # Token usage (populated by the runner, used by usage_metering_service).
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # Retry telemetry. Surfaced into ``UsageEvent.event_metadata`` by
    # cv_score_orchestrator so the validation-failure rate is queryable
    # from the same place as the cost (no separate telemetry pipeline).
    # When validation_failures > 0, the runner made N+1 Anthropic calls
    # for this score — that's compound spend, worth watching.
    retry_count: int = 0
    validation_failures: int = 0
