"""Fail-closed capability checks for compatibility-parser search requests.

The typed ``SearchPlan`` is intentionally broader than today's production
retrievers.  This module prevents a plan from being treated as executed when a
legacy ``ParsedFilter`` cannot preserve or prove one of its semantics.
"""

from __future__ import annotations

import re

from .schemas import ParsedFilter

_UNSUPPORTED_PATH_PREDICATES = frozenset({"colleague_of", "n_hop_from"})
_YEAR_RE = re.compile(r"\byears?\b", re.IGNORECASE)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _has_skill_bound_duration(parsed: ParsedFilter) -> bool:
    """Detect duration that the flat DTO appears to bind to one skill.

    PostgreSQL can enforce total career duration, but the compatibility DTO has
    no field for "N years using X".  Treat that ambiguous shape as unsupported
    instead of silently applying N to the candidate's whole career.
    """

    if parsed.min_years_experience is None or not _YEAR_RE.search(parsed.free_text or ""):
        return False
    claims = [_normalise(value) for value in (*parsed.soft_criteria, *parsed.keywords)]
    skills = [_normalise(value) for value in (*parsed.skills_all, *parsed.skills_any)]
    return any(skill and skill in claim for skill in skills for claim in claims)


def unsupported_runtime_requirements(parsed: ParsedFilter) -> tuple[str, ...]:
    """Return request semantics that production cannot yet prove exactly."""

    unsupported: list[str] = []
    path_types = sorted(
        {
            predicate.type
            for predicate in parsed.graph_predicates
            if predicate.type in _UNSUPPORTED_PATH_PREDICATES
        }
    )
    if path_types:
        unsupported.append("exact graph path: " + ", ".join(path_types))
    if _has_skill_bound_duration(parsed):
        unsupported.append("skill-specific experience duration")
    return tuple(unsupported)


__all__ = ["unsupported_runtime_requirements"]
