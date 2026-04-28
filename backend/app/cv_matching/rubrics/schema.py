"""Pydantic schema for archetype rubric YAML files.

Files live in ``backend/app/cv_matching/rubrics/*.yaml``. The loader
validates each one against ``ArchetypeRubric`` on read; an invalid
YAML raises at startup so a typo in a rubric never silently breaks the
matching pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field


_RUBRICS_DIR = Path(__file__).resolve().parent


class MustHaveArchetype(BaseModel):
    """One skill cluster the archetype expects.

    ``exact_matches`` / ``strong_substitutes`` / ``weak_substitutes``
    are surface terms (not regexes). At runtime the prompt receives
    these lists; the LLM uses them to populate ``match_tier`` per
    requirement.
    """

    model_config = ConfigDict(extra="forbid")

    cluster: str
    description: str
    exact_matches: list[str] = Field(default_factory=list)
    strong_substitutes: list[str] = Field(default_factory=list)
    weak_substitutes: list[str] = Field(default_factory=list)
    unrelated: list[str] = Field(default_factory=list)


class DepthSignals(BaseModel):
    """Concrete depth signals per scoring dimension.

    These are prose phrases that the prompt feeds to the LLM as
    "look for one of these to award depth credit". Examples:
    "owned a production pipeline serving >10TB/day", "led a Glue
    migration that shaved 40% off ETL cost", etc.

    Keys mirror the v4.2 six-dimension decomposition (RALPH 2.10).
    Empty lists are valid — not every archetype distinguishes every
    dimension.
    """

    model_config = ConfigDict(extra="forbid")

    skills_coverage: list[str] = Field(default_factory=list)
    skills_depth: list[str] = Field(default_factory=list)
    title_trajectory: list[str] = Field(default_factory=list)
    seniority_alignment: list[str] = Field(default_factory=list)
    industry_match: list[str] = Field(default_factory=list)
    tenure_pattern: list[str] = Field(default_factory=list)


class SeniorityAnchors(BaseModel):
    """Anchored verbal tiers tuned to this role family.

    Each anchor is a concrete candidate profile description at the
    given band. Used by the v4.2 prompt to override the generic 25-point
    rubric anchors with role-specific examples.
    """

    model_config = ConfigDict(extra="forbid")

    band_100: str = ""
    band_75: str = ""
    band_50: str = ""
    band_25: str = ""
    band_0: str = ""


class ArchetypeRubric(BaseModel):
    """One per-role-family rubric.

    ``jd_centroid_text`` is the canonical JD prose for this archetype.
    The runtime embeds it once and stores the embedding alongside the
    rubric (Phase 2.7) so JD routing is a single cosine lookup.

    ``dimension_weights`` lets each archetype override the default
    six-dimension weighting. Weights must sum to 1.0 (validated
    below). Dimensions absent from the dict default to 0.
    """

    model_config = ConfigDict(extra="forbid")

    archetype_id: str
    description: str
    jd_centroid_text: str
    must_have_archetypes: list[MustHaveArchetype]
    depth_signals: DepthSignals = Field(default_factory=DepthSignals)
    seniority_anchors: SeniorityAnchors = Field(default_factory=SeniorityAnchors)
    dimension_weights: dict[str, float] = Field(default_factory=dict)

    def normalised_dimension_weights(self) -> dict[str, float]:
        """Fill in defaults for missing dimensions and renormalise to 1.0."""
        defaults = {
            "skills_coverage": 0.25,
            "skills_depth": 0.20,
            "title_trajectory": 0.15,
            "seniority_alignment": 0.15,
            "industry_match": 0.15,
            "tenure_pattern": 0.10,
        }
        merged = {**defaults, **self.dimension_weights}
        total = sum(merged.values())
        if total <= 0:
            return defaults
        return {k: v / total for k, v in merged.items()}


def load_rubric(archetype_id: str) -> ArchetypeRubric:
    """Load and validate one rubric YAML by archetype_id.

    Raises ``FileNotFoundError`` if the YAML is missing,
    ``pydantic.ValidationError`` if the structure is invalid.
    """
    path = _RUBRICS_DIR / f"{archetype_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No rubric for archetype_id={archetype_id!r}")
    blob = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ArchetypeRubric.model_validate(blob)


def list_rubrics() -> Iterable[ArchetypeRubric]:
    """Iterate every valid rubric in this directory.

    YAML files starting with ``_`` (e.g. ``_generation_prompt.md``)
    or named ``schema.yaml`` are skipped — those are tooling, not
    archetypes. Non-archetype files (README.md, __init__.py, etc.)
    are skipped naturally because ``glob("*.yaml")`` only returns
    .yaml files.
    """
    for path in sorted(_RUBRICS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        blob = yaml.safe_load(path.read_text(encoding="utf-8"))
        yield ArchetypeRubric.model_validate(blob)
