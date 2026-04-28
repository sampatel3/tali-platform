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
    """

    model_config = ConfigDict(extra="forbid")

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
    """

    model_config = ConfigDict(extra="forbid")

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


class CVMatchResult(BaseModel):
    """Raw LLM output after JSON parsing.

    The legacy v3 ``skills_match_score`` / ``experience_relevance_score``
    pair is back-filled by aggregation from the six dimensions for
    downstream-consumer compatibility.
    """

    model_config = ConfigDict(extra="forbid")

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

    @field_validator("experience_highlights", "concerns", mode="before")
    @classmethod
    def _truncate_capped_lists(cls, v):
        return _truncate_to_5(v)


class CVMatchOutput(BaseModel):
    """Final output after deterministic aggregation. Caller-facing.

    Wire shape for ``candidate_applications.cv_match_details``.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

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

    requirements_match_score: float = Field(default=0.0, ge=0, le=100)
    cv_fit_score: float = Field(default=0.0, ge=0, le=100)
    role_fit_score: float = Field(default=0.0, ge=0, le=100)
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
