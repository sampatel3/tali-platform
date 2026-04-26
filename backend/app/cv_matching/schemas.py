"""Pydantic v2 schemas for the cv_match_v3.0 pipeline.

Spec source: ``cv_matching_handover/schemas_spec.md``.

The split between ``CVMatchResult`` (LLM-produced) and ``CVMatchOutput``
(post-aggregation, what callers consume) is deliberate: the LLM emits only
``skills_match_score``, ``experience_relevance_score`` and per-requirement
assessments. ``aggregation.py`` derives ``requirements_match_score``,
``cv_fit_score``, ``role_fit_score``, and ``recommendation`` from those
inputs deterministically.
"""

from __future__ import annotations

from enum import Enum

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
