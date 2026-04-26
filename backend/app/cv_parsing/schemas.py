"""Pydantic v2 schemas for parsed CV sections.

The LLM is asked to return ParsedCV in JSON; we validate against this
schema. Strict ``extra='forbid'`` so prompt drift surfaces as a
ValidationError immediately. Failure modes are captured via
``parse_failed`` + ``error_reason`` on ParsedCV so the frontend can
gracefully fall back to raw text.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExperienceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = ""
    title: str = ""
    location: str = ""
    start: str = ""  # free-form date string; the model picks what's in the CV
    end: str = ""    # may include "Present"
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str = ""
    degree: str = ""
    field: str = ""
    start: str = ""
    end: str = ""
    notes: str = ""


class ParsedCVSections(BaseModel):
    """LLM-produced section breakdown. Validated post-parse."""

    model_config = ConfigDict(extra="forbid")

    headline: str = ""
    summary: str = ""
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class ParsedCV(BaseModel):
    """Caller-facing output. Wraps the parsed sections plus run metadata.

    Stored as the ``application.cv_sections`` JSON column so the candidate
    page renders without re-parsing. Frontend reads this shape directly.
    """

    # protected_namespaces=() lets us keep the spec name `model_version`.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    headline: str = ""
    summary: str = ""
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)

    parse_failed: bool = False
    error_reason: str = ""
    prompt_version: str = ""
    model_version: str = ""
    cache_hit: bool = False

    @classmethod
    def failed(cls, reason: str, *, prompt_version: str, model_version: str) -> "ParsedCV":
        return cls(
            parse_failed=True,
            error_reason=reason,
            prompt_version=prompt_version,
            model_version=model_version,
        )

    @classmethod
    def from_sections(
        cls,
        sections: ParsedCVSections,
        *,
        prompt_version: str,
        model_version: str,
        cache_hit: bool = False,
    ) -> "ParsedCV":
        return cls(
            headline=sections.headline,
            summary=sections.summary,
            experience=sections.experience,
            education=sections.education,
            skills=sections.skills,
            certifications=sections.certifications,
            languages=sections.languages,
            links=sections.links,
            prompt_version=prompt_version,
            model_version=model_version,
            cache_hit=cache_hit,
        )
