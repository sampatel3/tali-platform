"""Adapt the legacy parser shape to the backend-independent search plan.

``ParsedFilter`` is intentionally kept as a compatibility contract for the
HTTP and MCP surfaces.  This module is the one-way boundary into the richer
boolean/evidence model used by hybrid retrieval and offline evaluation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from .schemas import GraphPredicate, ParsedFilter
from .search_plan import (
    BooleanOperator,
    Comparison,
    ComparisonOperator,
    Criterion,
    EvidencePolicy,
    Expression,
    Modality,
    Predicate,
    SearchObject,
    SearchPlan,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_EVIDENCE_POLICY = EvidencePolicy(
    require_direct_subject=True,
    require_citation_span=True,
    minimum_sources=1,
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


def _clause_id(namespace: str, value: object, qualifier: object = "") -> str:
    """Return an order-independent, readable identity for one semantic clause."""

    canonical = f"{namespace}\x00{_normalise(value)}\x00{_normalise(qualifier)}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    slug = _SLUG_RE.sub("-", _normalise(value)).strip("-")[:48] or "value"
    return f"{namespace}-{slug}-{digest}"[:120]


def _group(
    operator: Literal["all", "any"], expressions: Sequence[Expression]
) -> Expression | None:
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    if operator == "any":
        return Expression.any(*expressions)
    return Expression.all(*expressions)


class _PlanBuilder:
    def __init__(self) -> None:
        self.criteria: list[Criterion] = []
        self.eligibility: list[Expression] = []

    def criterion(
        self,
        *,
        namespace: str,
        predicate: str,
        object_kind: str,
        value: str | int | float | bool | None,
        modality: Modality = Modality.MUST,
        comparison: Comparison | None = None,
        evidence: EvidencePolicy = _EVIDENCE_POLICY,
        qualifier: object = "",
    ) -> Expression:
        criterion = Criterion(
            id=_clause_id(namespace, value, qualifier),
            predicate=Predicate(name=predicate),
            subject=SearchObject(kind="person"),
            object=SearchObject(kind=object_kind, value=value),
            comparison=comparison or Comparison(),
            modality=modality,
            evidence=evidence,
        )
        self.criteria.append(criterion)
        return Expression.leaf(criterion.id)

    def add_group(
        self,
        expressions: Sequence[Expression],
        operator: Literal["all", "any"],
    ) -> None:
        expression = _group(operator, expressions)
        if expression is not None:
            self.eligibility.append(expression)

    def add_text_values(
        self,
        values: Iterable[str],
        *,
        namespace: str,
        predicate: str,
        object_kind: str,
        operator: Literal["all", "any"],
        modality: Modality = Modality.MUST,
        eligible: bool = True,
    ) -> list[Expression]:
        expressions = [
            self.criterion(
                namespace=namespace,
                predicate=predicate,
                object_kind=object_kind,
                value=value,
                modality=modality,
            )
            for value in _dedupe(values)
        ]
        if eligible:
            self.add_group(expressions, operator)
        return expressions


_GRAPH_OBJECT_KINDS = {
    "worked_at": "organization",
    "studied_at": "institution",
    "colleague_of": "person",
    "n_hop_from": "person",
}


def _graph_expression(builder: _PlanBuilder, item: GraphPredicate) -> Expression:
    comparison = Comparison()
    qualifier: object = ""
    if item.type == "n_hop_from" and item.n_hops is not None:
        comparison = Comparison(
            operator=ComparisonOperator.LTE,
            value=item.n_hops,
        )
        qualifier = item.n_hops
    return builder.criterion(
        namespace=f"graph-{item.type.replace('_', '-')}",
        predicate=item.type,
        object_kind=_GRAPH_OBJECT_KINDS[item.type],
        value=item.value.strip(),
        comparison=comparison,
        qualifier=qualifier,
    )


def _add_graph_predicates(builder: _PlanBuilder, parsed: ParsedFilter) -> None:
    expressions: list[Expression] = []
    seen: set[tuple[str, str, int | None]] = set()
    for item in parsed.graph_predicates:
        key = (item.type, _normalise(item.value), item.n_hops)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        expressions.append(_graph_expression(builder, item))
    builder.add_group(expressions, parsed.graph_predicate_operator)


def _optional_values(parsed: ParsedFilter, names: Sequence[str]) -> list[str]:
    """Read future exclusion fields without expanding today's API schema."""

    for name in names:
        value = getattr(parsed, name, None)
        if value:
            return _dedupe(value)
    return []


def _add_forward_compatible_exclusions(
    builder: _PlanBuilder, parsed: ParsedFilter
) -> None:
    specs = (
        (
            ("skills_not", "excluded_skills", "skills_exclude"),
            "excluded-skill",
            "demonstrated",
            "capability",
        ),
        (
            ("titles_not", "excluded_titles", "titles_exclude"),
            "excluded-title",
            "held_title",
            "job_title",
        ),
        (
            ("locations_not", "excluded_locations", "locations_exclude"),
            "excluded-location",
            "located_in",
            "location",
        ),
        (
            ("excluded_criteria", "soft_criteria_not"),
            "excluded-claim",
            "matches_claim",
            "claim",
        ),
    )
    for names, namespace, predicate, object_kind in specs:
        for value in _optional_values(parsed, names):
            leaf = builder.criterion(
                namespace=namespace,
                predicate=predicate,
                object_kind=object_kind,
                value=value,
                modality=Modality.MUST_NOT,
            )
            builder.eligibility.append(Expression.not_(leaf))


def parsed_filter_to_search_plan(
    parsed: ParsedFilter,
    *,
    query: str | None = None,
    limit: int = 50,
) -> SearchPlan:
    """Compile a compatibility ``ParsedFilter`` into a typed ``SearchPlan``.

    Array order never affects clause identity.  The legacy field semantics are
    retained: ``*_all`` clauses use conjunction, ``*_any`` and location or
    keyword alternatives use disjunction, and preferences influence ranking
    without entering the eligibility expression.
    """

    query_text = str(query if query is not None else parsed.free_text or "").strip()
    if not query_text:
        raise ValueError("query or parsed.free_text must contain search text")

    builder = _PlanBuilder()
    builder.add_text_values(
        parsed.skills_all,
        namespace="skill-all",
        predicate="demonstrated",
        object_kind="capability",
        operator="all",
    )
    builder.add_text_values(
        parsed.skills_any,
        namespace="skill-any",
        predicate="demonstrated",
        object_kind="capability",
        operator="any",
    )
    builder.add_text_values(
        parsed.titles_all,
        namespace="title-all",
        predicate="held_title",
        object_kind="job_title",
        operator="all",
    )
    builder.add_text_values(
        parsed.titles_any,
        namespace="title-any",
        predicate="held_title",
        object_kind="job_title",
        operator="any",
    )

    location_expressions = builder.add_text_values(
        parsed.locations_country,
        namespace="location-country",
        predicate="located_in",
        object_kind="country",
        operator="any",
        eligible=False,
    )
    location_expressions.extend(
        builder.add_text_values(
            parsed.locations_region,
            namespace="location-region",
            predicate="located_in",
            object_kind="region",
            operator="any",
            eligible=False,
        )
    )
    builder.add_group(location_expressions, "any")

    if parsed.min_years_experience is not None:
        builder.eligibility.append(
            builder.criterion(
                namespace="experience-years-gte",
                predicate="years_experience",
                object_kind="professional_experience",
                value=None,
                comparison=Comparison(
                    operator=ComparisonOperator.GTE,
                    value=parsed.min_years_experience,
                ),
                qualifier=parsed.min_years_experience,
            )
        )

    _add_graph_predicates(builder, parsed)
    builder.add_text_values(
        parsed.soft_criteria,
        namespace="required-claim",
        predicate="matches_claim",
        object_kind="claim",
        operator="all",
    )
    builder.add_text_values(
        parsed.keywords,
        namespace="required-keyword",
        predicate="matches_claim",
        object_kind="claim",
        operator="any",
    )
    builder.add_text_values(
        parsed.preferred_criteria,
        namespace="preferred-claim",
        predicate="matches_claim",
        object_kind="claim",
        operator="all",
        modality=Modality.SHOULD,
        eligible=False,
    )
    _add_forward_compatible_exclusions(builder, parsed)

    if not builder.eligibility:
        if parsed.preferred_criteria:
            baseline = builder.criterion(
                namespace="population",
                predicate="eligible",
                object_kind="search_population",
                value="candidate",
                evidence=EvidencePolicy(
                    require_direct_subject=False,
                    require_citation_span=False,
                    minimum_sources=0,
                ),
            )
            builder.eligibility.append(baseline)
        else:
            fallback = builder.criterion(
                namespace="query-claim",
                predicate="matches_claim",
                object_kind="claim",
                value=query_text,
            )
            builder.eligibility.append(fallback)

    root = _group("all", builder.eligibility)
    assert root is not None
    return SearchPlan(
        query=query_text,
        criteria=tuple(builder.criteria),
        root=root,
        limit=limit,
    )


__all__ = ["parsed_filter_to_search_plan"]
