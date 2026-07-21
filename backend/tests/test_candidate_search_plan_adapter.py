"""Contract tests for the legacy-filter to domain-neutral plan adapter."""

from __future__ import annotations

from app.candidate_search.plan_adapter import parsed_filter_to_search_plan
from app.candidate_search.plan_evidence import (
    evidence_scoped_structured_fields,
    graph_evidence_requirements,
)
from app.candidate_search.schemas import GraphPredicate, ParsedFilter
from app.candidate_search.search_plan import (
    BooleanOperator,
    ComparisonOperator,
    Modality,
)


def _criterion_ids(expression) -> set[str]:
    return expression.referenced_criterion_ids()


def _criterion_values(plan, predicate: str) -> list[object]:
    return [
        criterion.object.value
        for criterion in plan.criteria
        if criterion.predicate.name == predicate
    ]


def test_adapter_preserves_structured_all_any_and_location_semantics():
    parsed = ParsedFilter(
        skills_all=["Python", "Kubernetes"],
        skills_any=["Go", "Rust"],
        titles_all=["Platform Engineer", "Team Lead"],
        titles_any=["SRE", "DevOps Engineer"],
        locations_country=["UAE", "UK"],
        locations_region=["GCC"],
        free_text="platform engineers in the UAE, UK, or GCC",
    )

    plan = parsed_filter_to_search_plan(parsed)

    assert plan.root.operator is BooleanOperator.ALL
    group_operators = [child.operator for child in plan.root.children]
    assert group_operators.count(BooleanOperator.ANY) == 3
    assert _criterion_values(plan, "demonstrated") == [
        "Python",
        "Kubernetes",
        "Go",
        "Rust",
    ]
    assert _criterion_values(plan, "held_title") == [
        "Platform Engineer",
        "Team Lead",
        "SRE",
        "DevOps Engineer",
    ]
    assert set(_criterion_values(plan, "located_in")) == {"UAE", "UK", "GCC"}
    assert _criterion_ids(plan.root) == {
        criterion.id for criterion in plan.eligibility_criteria
    }


def test_graph_predicates_honor_any_and_keep_hop_comparison():
    parsed = ParsedFilter(
        graph_predicates=[
            GraphPredicate(type="worked_at", value="Google"),
            GraphPredicate(type="studied_at", value="MIT"),
            GraphPredicate(type="n_hop_from", value="candidate-42", n_hops=2),
        ],
        graph_predicate_operator="any",
        free_text="worked at Google, studied at MIT, or near candidate 42",
    )

    plan = parsed_filter_to_search_plan(parsed)

    assert plan.root.operator is BooleanOperator.ANY
    by_predicate = {criterion.predicate.name: criterion for criterion in plan.criteria}
    assert by_predicate["worked_at"].object.kind == "organization"
    assert by_predicate["studied_at"].object.kind == "institution"
    assert by_predicate["n_hop_from"].comparison.operator is ComparisonOperator.LTE
    assert by_predicate["n_hop_from"].comparison.value == 2


def test_required_claims_are_eligible_but_preferences_only_rank():
    parsed = ParsedFilter(
        soft_criteria=["built payment systems in production"],
        keywords=["PCI DSS", "tokenization"],
        preferred_criteria=["banking experience"],
        free_text="production payments, PCI or tokenization; banking preferred",
    )

    plan = parsed_filter_to_search_plan(parsed)

    required = [c for c in plan.criteria if c.modality is Modality.MUST]
    preferred = [c for c in plan.criteria if c.modality is Modality.SHOULD]
    assert {c.object.value for c in required} == {
        "built payment systems in production",
        "PCI DSS",
        "tokenization",
    }
    assert [c.object.value for c in preferred] == ["banking experience"]
    assert preferred[0].id not in _criterion_ids(plan.root)
    assert all(c.evidence.require_citation_span for c in required + preferred)
    assert all(c.evidence.minimum_sources >= 1 for c in required + preferred)

    keyword_ids = {
        c.id for c in required if c.object.value in {"PCI DSS", "tokenization"}
    }
    any_nodes = [
        child
        for child in plan.root.children
        if child.operator is BooleanOperator.ANY
    ]
    assert any(_criterion_ids(node) == keyword_ids for node in any_nodes)


def test_minimum_years_becomes_a_typed_comparison():
    plan = parsed_filter_to_search_plan(
        ParsedFilter(
            min_years_experience=7,
            free_text="at least seven years of experience",
        )
    )

    criterion = plan.criteria[0]
    assert criterion.predicate.name == "years_experience"
    assert criterion.object.kind == "professional_experience"
    assert criterion.comparison.operator is ComparisonOperator.GTE
    assert criterion.comparison.value == 7


def test_clause_ids_are_stable_when_equivalent_values_are_reordered():
    first = parsed_filter_to_search_plan(
        ParsedFilter(skills_any=["Go", "Rust"], free_text="Go or Rust")
    )
    second = parsed_filter_to_search_plan(
        ParsedFilter(skills_any=["Rust", "Go"], free_text="Go or Rust")
    )

    first_ids = {c.object.value: c.id for c in first.criteria}
    second_ids = {c.object.value: c.id for c in second.criteria}
    assert first_ids == second_ids


def test_evidence_scoped_fields_never_relax_unproven_minimum_duration():
    scoped = ParsedFilter(
        skills_all=["AWS Glue"],
        min_years_experience=3,
        soft_criteria=["AWS Glue production experience"],
        free_text="3 years of AWS Glue production experience",
    )
    unrelated = ParsedFilter(
        skills_all=["Python"],
        soft_criteria=["banking domain experience"],
        free_text="Python and banking domain experience",
    )

    assert evidence_scoped_structured_fields(scoped) == frozenset({"skills_all"})
    assert evidence_scoped_structured_fields(unrelated) == frozenset()


def test_forward_compatible_exclusions_compile_to_not_expressions():
    class FilterWithExclusions(ParsedFilter):
        skills_not: list[str]
        excluded_criteria: list[str]

    parsed = FilterWithExclusions(
        skills_all=["Python"],
        skills_not=["COBOL"],
        excluded_criteria=["training-only evidence"],
        free_text="Python excluding COBOL and training-only evidence",
    )

    plan = parsed_filter_to_search_plan(parsed)

    not_nodes = [
        child
        for child in plan.root.children
        if child.operator is BooleanOperator.NOT
    ]
    assert len(not_nodes) == 2
    excluded = [c for c in plan.criteria if c.modality is Modality.MUST_NOT]
    assert {c.object.value for c in excluded} == {
        "COBOL",
        "training-only evidence",
    }


def test_graph_evidence_requirements_cover_bound_skills_and_graph_boolean_group():
    parsed = ParsedFilter(
        skills_all=["Agentforce"],
        soft_criteria=["hands-on Agentforce experience"],
        graph_predicates=[
            {"type": "worked_at", "value": "Google"},
            {"type": "worked_at", "value": "Meta"},
        ],
        graph_predicate_operator="any",
        free_text="Agentforce experience at Google or Meta",
    )
    plan = parsed_filter_to_search_plan(parsed)

    requirements = graph_evidence_requirements(parsed, plan)

    assert [(group.operator, [c.value for c in group.clauses]) for group in requirements] == [
        ("all", ["Agentforce"]),
        ("all", ["hands-on Agentforce experience"]),
        ("any", ["Google", "Meta"]),
    ]
    known_ids = {criterion.id for criterion in plan.criteria}
    assert {
        clause.clause_id for group in requirements for clause in group.clauses
    } <= known_ids
    relationship_group = requirements[-1]
    assert [clause.predicate for clause in relationship_group.clauses] == [
        "worked_at",
        "worked_at",
    ]


def test_disjunctive_claim_requires_any_source_backed_branch():
    parsed = ParsedFilter(
        skills_any=["Python", "Kubernetes"],
        soft_criteria=["Python or Kubernetes experience"],
        free_text="Python or Kubernetes experience",
    )
    plan = parsed_filter_to_search_plan(parsed)

    requirements = graph_evidence_requirements(parsed, plan)

    assert [
        (group.operator, [c.value for c in group.clauses])
        for group in requirements
    ] == [
        ("any", ["Python", "Kubernetes"]),
        ("any", ["Python experience", "Kubernetes experience"]),
    ]
    disjunction = requirements[-1]
    assert len({clause.clause_id for clause in disjunction.clauses}) == 1


def test_disjunctive_keywords_share_one_any_evidence_group():
    parsed = ParsedFilter(
        keywords=["either fintech or banking experience", "payments"],
        free_text="fintech or banking experience, or payments",
    )
    plan = parsed_filter_to_search_plan(parsed)

    requirements = graph_evidence_requirements(parsed, plan)

    assert [
        (group.operator, [c.value for c in group.clauses])
        for group in requirements
    ] == [
        (
            "any",
            ["fintech experience", "banking experience", "payments"],
        )
    ]
