"""Pydantic v2 schemas for the cv_matching pipeline.

Single scoring path. The LLM emits per-requirement assessments + six
dimension scores; ``aggregation.py`` derives ``requirements_match_score``,
``cv_fit_score``, ``role_fit_score``, and ``recommendation`` deterministically.
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


MatchTier = Literal[
    "exact",
    "strong_substitute",
    "weak_substitute",
    "unrelated",
    "missing",
]
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


class RequirementAssessment(BaseModel):
    """Per-requirement output from the LLM.

    Field ordering is deliberate: ``evidence_quotes`` and ``reasoning``
    appear BEFORE ``status``, ``match_tier``, ``impact``, ``confidence``
    because the autoregressive output of an LLM commits to earlier
    fields before later ones. Forcing evidence-first reduces score
    drift driven by status hallucination.
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
    recommendation: Recommendation = Recommendation.NO

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
