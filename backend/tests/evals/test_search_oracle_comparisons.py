"""Typed comparison semantics used by the deterministic oracle."""

from __future__ import annotations

import pytest

from app.candidate_search.evals.contracts import (
    ConstructedWorld,
    Fact,
    QueryIntent,
    WorldEntity,
)
from app.candidate_search.evals.oracle import derive_judgments
from app.candidate_search.search_plan import (
    Comparison,
    ComparisonOperator,
    Criterion,
    EvidencePolicy,
    Expression,
    Predicate,
    SearchObject,
    SearchPlan,
)


@pytest.mark.parametrize(
    ("actual", "operator", "expected", "eligible"),
    (
        (7, ComparisonOperator.EQ, 7, True),
        (7, ComparisonOperator.NE, 7, False),
        ("Platform Engineering", ComparisonOperator.CONTAINS, "form eng", True),
        ("Python", ComparisonOperator.IN, ("Go", "Python"), True),
        (7, ComparisonOperator.GT, 6, True),
        (7, ComparisonOperator.GTE, 7, True),
        (7, ComparisonOperator.LT, 8, True),
        (7, ComparisonOperator.LTE, 7, True),
        (True, ComparisonOperator.EQ, 1, False),
    ),
)
def test_oracle_comparison_operators_are_typed(
    actual: str | int | bool,
    operator: ComparisonOperator,
    expected: str | int | tuple[str, ...],
    eligible: bool,
) -> None:
    world = ConstructedWorld(
        id="comparison-world",
        entities=(WorldEntity(id="person", kind="person"),),
        facts=(
            Fact(
                id="value",
                subject_id="person",
                predicate="attribute",
                object=SearchObject(kind="attribute", value="value"),
                value=actual,
            ),
        ),
        closed_world_predicates=("attribute",),
    )
    criterion = Criterion(
        id="comparison",
        predicate=Predicate(name="attribute"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="attribute", value="value"),
        comparison=Comparison(operator=operator, value=expected),
        evidence=EvidencePolicy(
            minimum_sources=0,
            require_citation_span=False,
        ),
    )
    intent = QueryIntent(
        id="comparison",
        plan=SearchPlan(
            query="Typed comparison",
            criteria=(criterion,),
            root=Expression.leaf(criterion.id),
        ),
    )

    assert derive_judgments(world, intent)[0].eligible is eligible
