"""Independent deterministic oracle derived solely from constructed facts."""

from __future__ import annotations

import math
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from numbers import Real

from ..search_plan import (
    BooleanOperator,
    ComparisonOperator,
    Criterion,
    Expression,
    Modality,
    Scalar,
)
from .contracts import (
    Citation,
    ConstructedWorld,
    CriterionJudgment,
    Fact,
    OracleJudgment,
    QueryIntent,
    TruthValue,
    WorldEntity,
)


@dataclass(frozen=True)
class _CriterionResult:
    truth: TruthValue
    citations: tuple[Citation, ...] = ()


def _same(left: Scalar | None, right: Scalar | None) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().casefold() == right.strip().casefold()
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _add_calendar_months(start: date, months: int) -> date:
    month_index = start.year * 12 + start.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))


def _duration_satisfies(start: date, end: date, required_months: float) -> bool:
    """Compare calendar duration without average-day boundary errors."""

    whole_months = math.floor(required_months)
    threshold = _add_calendar_months(start, whole_months)
    fraction = required_months - whole_months
    if fraction:
        following = _add_calendar_months(start, whole_months + 1)
        days = (following - threshold).days
        threshold += timedelta(days=math.ceil(days * fraction))
    return end >= threshold


def _temporal_matches(criterion: Criterion, fact: Fact) -> bool:
    temporal = criterion.temporal
    if temporal is None:
        return True

    if temporal.minimum_duration_months is not None:
        if fact.valid_from is None:
            return False
        duration_end = fact.valid_to
        if duration_end is None and fact.ongoing:
            duration_end = temporal.as_of
        if duration_end is None or not _duration_satisfies(
            fact.valid_from,
            duration_end,
            temporal.minimum_duration_months,
        ):
            return False

    if temporal.starts_on_or_before is not None:
        if fact.valid_from is None or fact.valid_from > temporal.starts_on_or_before:
            return False

    if temporal.ends_on_or_after is not None:
        if not fact.ongoing and (
            fact.valid_to is None or fact.valid_to < temporal.ends_on_or_after
        ):
            return False

    if temporal.overlaps_from is not None:
        if not fact.ongoing and (
            fact.valid_to is None or fact.valid_to < temporal.overlaps_from
        ):
            return False

    if temporal.overlaps_to is not None:
        if fact.valid_from is None or fact.valid_from > temporal.overlaps_to:
            return False

    if temporal.current_only:
        assert temporal.as_of is not None
        if fact.valid_from is None or fact.valid_from > temporal.as_of:
            return False
        if not fact.ongoing and (
            fact.valid_to is None or fact.valid_to < temporal.as_of
        ):
            return False
    return True


def _ordered(
    left: Scalar | None,
    right: Scalar | None,
    operator: ComparisonOperator,
) -> bool:
    if not isinstance(left, Real) or isinstance(left, bool):
        return False
    if not isinstance(right, Real) or isinstance(right, bool):
        return False
    if operator is ComparisonOperator.GT:
        return left > right
    if operator is ComparisonOperator.GTE:
        return left >= right
    if operator is ComparisonOperator.LT:
        return left < right
    return left <= right


def _comparison_matches(criterion: Criterion, fact: Fact) -> bool:
    comparison = criterion.comparison
    if comparison.operator is ComparisonOperator.EXISTS:
        return True
    actual = fact.value if fact.value is not None else fact.object.value
    expected = comparison.value
    if comparison.operator is ComparisonOperator.EQ:
        return _same(actual, expected)  # type: ignore[arg-type]
    if comparison.operator is ComparisonOperator.NE:
        return not _same(actual, expected)  # type: ignore[arg-type]
    if comparison.operator is ComparisonOperator.CONTAINS:
        if isinstance(actual, str) and isinstance(expected, str):
            return expected.casefold() in actual.casefold()
        return False
    if comparison.operator is ComparisonOperator.IN:
        assert isinstance(expected, tuple)
        return any(_same(actual, option) for option in expected)
    return _ordered(actual, expected, comparison.operator)  # type: ignore[arg-type]


def _fact_matches_value(
    entity: WorldEntity,
    criterion: Criterion,
    fact: Fact,
) -> bool:
    if fact.subject_id != entity.id:
        return False
    if fact.predicate.casefold() != criterion.predicate.name.casefold():
        return False
    if fact.object.kind.casefold() != criterion.object.kind.casefold():
        return False
    if criterion.object.value is not None and not _same(
        fact.object.value,
        criterion.object.value,
    ):
        return False
    if not _comparison_matches(criterion, fact):
        return False
    return _temporal_matches(criterion, fact)


def _valid_fact_citations(
    world: ConstructedWorld,
    criterion: Criterion,
    fact: Fact,
) -> tuple[Citation, ...] | None:
    policy = criterion.evidence
    if fact.confidence < policy.minimum_confidence:
        return None
    if policy.require_direct_subject and not fact.direct_subject:
        return None
    documents = {document.id: document for document in world.documents}
    citations: list[Citation] = []
    for citation in fact.provenance:
        document = documents.get(citation.document_id)
        if document is None:
            continue
        if policy.allowed_source_types and (
            document.source_type not in policy.allowed_source_types
        ):
            continue
        if policy.require_direct_subject and document.entity_id != fact.subject_id:
            continue
        if citation.end > len(document.content):
            continue
        if document.content[citation.start : citation.end] != citation.quote:
            continue
        citations.append(citation)
    return tuple(citations)


def _criterion_result(
    world: ConstructedWorld,
    entity: WorldEntity,
    criterion: Criterion,
) -> _CriterionResult:
    if entity.kind.casefold() != criterion.subject.kind.casefold():
        return _CriterionResult(TruthValue.FALSE)
    if criterion.subject.value is not None and not _same(
        entity.id,
        criterion.subject.value,
    ):
        return _CriterionResult(TruthValue.FALSE)

    matching_citations: list[Citation] = []
    matched_without_citation = False
    documents = {document.id: document for document in world.documents}
    for fact in world.facts:
        if not _fact_matches_value(entity, criterion, fact):
            continue
        citations = _valid_fact_citations(world, criterion, fact)
        if citations is None:
            continue
        matching_citations.extend(citations)
        if not criterion.evidence.require_citation_span:
            matched_without_citation = True

    deduplicated = tuple(dict.fromkeys(matching_citations))
    source_ids = {
        documents[citation.document_id].independent_source_id
        for citation in deduplicated
    }
    enough_sources = len(source_ids) >= criterion.evidence.minimum_sources
    enough_spans = bool(deduplicated) or not criterion.evidence.require_citation_span
    if enough_sources and enough_spans and (deduplicated or matched_without_citation):
        return _CriterionResult(TruthValue.TRUE, deduplicated)

    if world.predicate_is_closed(criterion.predicate.name):
        return _CriterionResult(TruthValue.FALSE)
    return _CriterionResult(TruthValue.UNKNOWN)


def evaluate_expression_truth(
    expression: Expression,
    matches: dict[str, TruthValue],
) -> TruthValue:
    if expression.operator is BooleanOperator.TRUE:
        return TruthValue.TRUE
    if expression.operator is BooleanOperator.CRITERION:
        assert expression.criterion_id is not None
        return matches[expression.criterion_id]
    if expression.operator is BooleanOperator.NOT:
        child = evaluate_expression_truth(expression.children[0], matches)
        if child is TruthValue.UNKNOWN:
            return child
        return TruthValue.FALSE if child is TruthValue.TRUE else TruthValue.TRUE

    children = [
        evaluate_expression_truth(child, matches) for child in expression.children
    ]
    if expression.operator is BooleanOperator.ALL:
        if TruthValue.FALSE in children:
            return TruthValue.FALSE
        if TruthValue.UNKNOWN in children:
            return TruthValue.UNKNOWN
        return TruthValue.TRUE
    if TruthValue.TRUE in children:
        return TruthValue.TRUE
    if TruthValue.UNKNOWN in children:
        return TruthValue.UNKNOWN
    return TruthValue.FALSE


def derive_judgments(
    world: ConstructedWorld,
    intent: QueryIntent,
) -> tuple[OracleJudgment, ...]:
    """Return entity judgments derived from facts, never stored expected IDs."""

    plan = intent.plan
    referenced = plan.root.referenced_criterion_ids()
    rows: list[OracleJudgment] = []
    for entity in world.entities:
        results = {
            criterion.id: _criterion_result(world, entity, criterion)
            for criterion in plan.criteria
        }
        truth_by_id = {
            criterion_id: result.truth for criterion_id, result in results.items()
        }
        eligibility = evaluate_expression_truth(plan.root, truth_by_id)
        matched = tuple(
            criterion.id
            for criterion in plan.criteria
            if results[criterion.id].truth is TruthValue.TRUE
            and criterion.modality is not Modality.MUST_NOT
        )
        failed = tuple(
            criterion.id
            for criterion in plan.criteria
            if criterion.id in referenced
            and (
                (
                    criterion.modality is Modality.MUST
                    and results[criterion.id].truth is TruthValue.FALSE
                )
                or (
                    criterion.modality is Modality.MUST_NOT
                    and results[criterion.id].truth is TruthValue.TRUE
                )
            )
        )
        unknown = tuple(
            criterion.id
            for criterion in plan.criteria
            if criterion.id in referenced
            and results[criterion.id].truth is TruthValue.UNKNOWN
        )
        relevance = 0.0
        if eligibility is TruthValue.TRUE:
            relevance = sum(
                criterion.weight
                for criterion in plan.criteria
                if results[criterion.id].truth is TruthValue.TRUE
                and criterion.modality is not Modality.MUST_NOT
            )
        rows.append(
            OracleJudgment(
                entity_id=entity.id,
                eligibility=eligibility,
                relevance=relevance,
                matched_criteria=matched,
                failed_criteria=failed,
                unknown_criteria=unknown,
                criterion_judgments=tuple(
                    CriterionJudgment(
                        criterion_id=criterion.id,
                        truth=results[criterion.id].truth,
                        supporting_citations=results[criterion.id].citations,
                    )
                    for criterion in plan.criteria
                ),
            )
        )
    return tuple(rows)


__all__ = ["derive_judgments", "evaluate_expression_truth"]
