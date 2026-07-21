"""Compile source-evidence requirements from a backend-independent plan."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .hybrid import GraphEvidenceClause, GraphEvidenceRequirement
from .schemas import ParsedFilter
from .search_plan import SearchPlan

_OR_SEPARATOR_RE = re.compile(r"\bor\b", re.IGNORECASE)
_LEADING_EITHER_RE = re.compile(r"^\s*either\b", re.IGNORECASE)
_EXPERIENCE_DEMAND_RE = re.compile(
    r"\b(?:experience|background|expertise|hands[\s-]*on)\b",
    re.IGNORECASE,
)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        key = _normalise(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _contains_value(text: str, value: str) -> bool:
    normalised_text = _normalise(text)
    normalised_value = _normalise(value)
    return bool(normalised_text and normalised_value in normalised_text)


def _disjunctive_evidence_values(value: str) -> tuple[str, ...]:
    """Split an internal OR while preserving a shared experience qualifier."""

    parts = [part.strip(" ,;:") for part in _OR_SEPARATOR_RE.split(value)]
    if len(parts) < 2 or any(not part for part in parts):
        return (value,)
    parts[0] = _LEADING_EITHER_RE.sub("", parts[0]).strip(" ,;:")
    if any(not part for part in parts):
        return (value,)

    qualifier_match = _EXPERIENCE_DEMAND_RE.search(value)
    qualifier = qualifier_match.group(0) if qualifier_match else None
    if qualifier and qualifier.casefold().replace("-", " ") == "hands on":
        qualifier = "hands-on experience"
    if qualifier:
        parts = [
            part
            if _EXPERIENCE_DEMAND_RE.search(part)
            else f"{part} {qualifier}"
            for part in parts
        ]
    return tuple(_dedupe(parts))


def evidence_scoped_structured_fields(parsed: ParsedFilter) -> frozenset[str]:
    """Identify structured predicates whose proof lives in source evidence."""

    fields: set[str] = set()
    required_claims = [*parsed.soft_criteria, *parsed.keywords]
    for field_name in ("skills_all", "skills_any"):
        values = getattr(parsed, field_name)
        if any(
            _contains_value(claim, skill)
            for claim in required_claims
            for skill in values
        ):
            fields.add(field_name)
    # Minimum years remains a deterministic PostgreSQL population constraint.
    # Source-text token matching cannot prove an interval or accumulated
    # duration, so relaxing it here would drop a mandatory SearchPlan clause.
    if parsed.graph_predicates:
        fields.add("graph_predicates")
    return frozenset(fields)


def graph_evidence_requirements(
    parsed: ParsedFilter,
    plan: SearchPlan,
) -> tuple[GraphEvidenceRequirement, ...]:
    """Build source checks for every graph-promoted required criterion."""

    by_key = {
        (criterion.predicate.name, _normalise(criterion.object.value)): criterion.id
        for criterion in plan.criteria
        if criterion.object.value is not None
    }

    def clauses(predicate: str, values: Iterable[str]) -> tuple[GraphEvidenceClause, ...]:
        out: list[GraphEvidenceClause] = []
        for value in _dedupe(values):
            criterion_id = by_key.get((predicate, _normalise(value)))
            if criterion_id:
                out.append(GraphEvidenceClause(criterion_id, value, predicate))
        return tuple(out)

    def claim_clauses(value: str) -> tuple[GraphEvidenceClause, ...]:
        criterion_id = by_key.get(("matches_claim", _normalise(value)))
        if not criterion_id:
            return ()
        return tuple(
            GraphEvidenceClause(criterion_id, branch, "matches_claim")
            for branch in _disjunctive_evidence_values(value)
        )

    requirements: list[GraphEvidenceRequirement] = []
    scoped = evidence_scoped_structured_fields(parsed)
    for field_name, operator in (("skills_all", "all"), ("skills_any", "any")):
        if field_name not in scoped:
            continue
        skill_clauses = clauses("demonstrated", getattr(parsed, field_name))
        if skill_clauses:
            requirements.append(GraphEvidenceRequirement(operator, skill_clauses))

    for claim in parsed.soft_criteria:
        required = claim_clauses(claim)
        if required:
            requirements.append(
                GraphEvidenceRequirement("any" if len(required) > 1 else "all", required)
            )

    keyword_clauses = tuple(
        clause
        for keyword in parsed.keywords
        for clause in claim_clauses(keyword)
    )
    if keyword_clauses:
        requirements.append(GraphEvidenceRequirement("any", keyword_clauses))

    graph_clauses: list[GraphEvidenceClause] = []
    for predicate in parsed.graph_predicates:
        criterion_id = by_key.get((predicate.type, _normalise(predicate.value)))
        if criterion_id:
            graph_clauses.append(
                GraphEvidenceClause(
                    criterion_id,
                    predicate.value,
                    predicate.type,
                )
            )
    if graph_clauses:
        requirements.append(
            GraphEvidenceRequirement(
                parsed.graph_predicate_operator,
                tuple(graph_clauses),
            )
        )
    return tuple(requirements)


__all__ = [
    "evidence_scoped_structured_fields",
    "graph_evidence_requirements",
]
