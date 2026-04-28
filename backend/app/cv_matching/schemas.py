"""Pydantic v2 schemas for the cv_match pipeline.

Spec source: ``cv_matching_handover/schemas_spec.md``.

The split between ``CVMatchResult`` (LLM-produced) and ``CVMatchOutput``
(post-aggregation, what callers consume) is deliberate: the LLM emits only
``skills_match_score``, ``experience_relevance_score`` and per-requirement
assessments. ``aggregation.py`` derives ``requirements_match_score``,
``cv_fit_score``, ``role_fit_score``, and ``recommendation`` from those
inputs deterministically.

V4 (Phase 1) adds ``RequirementAssessmentV4`` and ``CVMatchResultV4`` /
``CVMatchOutputV4`` alongside the v3 types. V3 types are unchanged so the
v3 pipeline continues working without behavioural drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class RequirementAssessment(BaseModel):
    """Per-requirement output from the LLM."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    requirement: str
    priority: Priority
    status: Status
    evidence_quote: str = ""
    evidence_start_char: int = -1
    evidence_end_char: int = -1
    impact: str = ""
    confidence: Confidence = Confidence.MEDIUM


class CVMatchResult(BaseModel):
    """Raw LLM output after JSON parsing.

    Validated against this schema; fails the run on schema mismatch and
    triggers retry per ``runner.run_cv_match``.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_version: str
    skills_match_score: float = Field(ge=0, le=100)
    experience_relevance_score: float = Field(ge=0, le=100)
    requirements_assessment: list[RequirementAssessment] = Field(default_factory=list)
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    summary: str = ""


class CVMatchOutput(BaseModel):
    """Final output after deterministic aggregation. Caller-facing.

    Wire shape for ``candidate_applications.cv_match_details`` when
    ``USE_CV_MATCH_V3`` is enabled.
    """

    # protected_namespaces=() lets us keep the spec name `model_version`
    # without colliding with Pydantic's `model_*` reserved namespace.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    # Echoed from CVMatchResult
    prompt_version: str
    skills_match_score: float = Field(ge=0, le=100)
    experience_relevance_score: float = Field(ge=0, le=100)
    requirements_assessment: list[RequirementAssessment] = Field(default_factory=list)
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    summary: str = ""

    # Computed by aggregation.py
    requirements_match_score: float = Field(default=0.0, ge=0, le=100)
    cv_fit_score: float = Field(default=0.0, ge=0, le=100)
    role_fit_score: float = Field(default=0.0, ge=0, le=100)
    recommendation: Recommendation = Recommendation.NO

    # Set by validation.py
    injection_suspected: bool = False
    suspicious_score: bool = False

    # Run metadata
    scoring_status: ScoringStatus = ScoringStatus.OK
    error_reason: str = ""
    model_version: str = ""
    trace_id: str = ""
    cache_hit: bool = False


# =====================================================================
# v4 schemas (additive — v3 types above remain unchanged)
# =====================================================================


MatchTier = Literal[
    "exact",
    "strong_substitute",
    "weak_substitute",
    "unrelated",
    "missing",
]
"""Per-requirement classification of CV evidence vs JD ask.

The aggregation layer multiplies a tier weight on top of priority × status:
exact=1.0, strong_substitute=0.85, weak_substitute=0.55, unrelated=0.0,
missing=0.0. ``unrelated`` and ``missing`` are kept distinct in the output
because they convey different recruiter-facing meaning (we found something
loosely related vs we found nothing).
"""


class RequirementAssessmentV4(BaseModel):
    """Per-requirement output from the LLM (v4).

    Field ordering is deliberate: ``evidence_quotes`` and ``reasoning``
    appear BEFORE ``status``, ``match_tier``, ``impact``, and
    ``confidence`` because the autoregressive output of an LLM commits to
    earlier fields before later ones. Forcing evidence-first reduces score
    drift driven by status hallucination (research §5).

    Differences from v3 ``RequirementAssessment``:
    - ``evidence_quote`` (str) → ``evidence_quotes`` (list[str]). Multiple
      verbatim spans are common when a single requirement is supported by
      several CV sentences.
    - New field ``reasoning`` (2-3 sentence chain-of-thought).
    - New field ``match_tier`` (see ``MatchTier``).
    - ``confidence`` retained but the prompt narrows the rubric for it.
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


class DimensionScores(BaseModel):
    """v4.2 decomposed dimensions. Each is 0-100 and emitted by the LLM.

    The aggregation layer derives ``cv_fit_score`` as a weighted average
    using archetype-specific weights (from the rubric YAML), then
    back-fills ``skills_match_score`` and ``experience_relevance_score``
    on the output for backwards compatibility:

      skills_match_score          = (skills_coverage + skills_depth) / 2
      experience_relevance_score  = mean(title_trajectory,
                                         seniority_alignment,
                                         industry_match,
                                         tenure_pattern)
    """

    model_config = ConfigDict(extra="forbid")

    skills_coverage: float = Field(ge=0, le=100)
    skills_depth: float = Field(ge=0, le=100)
    title_trajectory: float = Field(ge=0, le=100)
    seniority_alignment: float = Field(ge=0, le=100)
    industry_match: float = Field(ge=0, le=100)
    tenure_pattern: float = Field(ge=0, le=100)


class CVMatchResultV4(BaseModel):
    """Raw LLM output (v4) after JSON parsing.

    Mirrors ``CVMatchResult`` with v4 per-requirement assessments.
    ``dimension_scores`` is populated by the v4.2 prompt; v4.1 leaves
    it None and the aggregation layer falls back to the v3-style
    ``skills_match_score`` / ``experience_relevance_score`` pair.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_version: str
    skills_match_score: float = Field(default=0.0, ge=0, le=100)
    experience_relevance_score: float = Field(default=0.0, ge=0, le=100)
    dimension_scores: DimensionScores | None = None
    requirements_assessment: list[RequirementAssessmentV4] = Field(default_factory=list)
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    summary: str = ""


class CVMatchOutputV4(BaseModel):
    """Final v4 output after deterministic aggregation. Caller-facing.

    Same caller contract as ``CVMatchOutput`` (top-level scores +
    recommendation), but the per-requirement list is the v4 shape so
    downstream consumers can read ``match_tier`` and richer evidence.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    prompt_version: str
    skills_match_score: float = Field(default=0.0, ge=0, le=100)
    experience_relevance_score: float = Field(default=0.0, ge=0, le=100)
    dimension_scores: DimensionScores | None = None
    requirements_assessment: list[RequirementAssessmentV4] = Field(default_factory=list)
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    summary: str = ""

    requirements_match_score: float = Field(default=0.0, ge=0, le=100)
    cv_fit_score: float = Field(default=0.0, ge=0, le=100)
    role_fit_score: float = Field(default=0.0, ge=0, le=100)
    recommendation: Recommendation = Recommendation.NO

    injection_suspected: bool = False
    suspicious_score: bool = False

    scoring_status: ScoringStatus = ScoringStatus.OK
    error_reason: str = ""
    model_version: str = ""
    trace_id: str = ""
    cache_hit: bool = False

    # Phase 3 calibration: probability that a recruiter would advance
    # this candidate, conditional on the role family. Populated by
    # ``calibrators.apply_calibrator`` when a calibrator exists for the
    # active (role_family, dimension) pair. None when no calibrator is
    # available — caller falls back to ``role_fit_score``.
    calibrated_p_advance: float | None = None
    # Phase 3 borderline + Phase 4 conformal flags.
    requires_human_review: bool = False
    score_std: float | None = None  # self-consistency std-dev band
