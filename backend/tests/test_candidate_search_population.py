"""Canonical PostgreSQL scope helpers for hybrid candidate retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.candidate_search.population import (
    apply_searchable_candidate_scope,
    application_map_from_rows,
    estimate_graph_coverage,
    population_filter,
)
from app.candidate_search.query_builder_sql import apply_parsed_filter
from app.candidate_search.schemas import ParsedFilter
from app.models.candidate_application import CandidateApplication


def test_searchable_scope_requires_live_candidate_in_same_organization(db):
    query = apply_searchable_candidate_scope(
        db.query(CandidateApplication),
        organization_id=17,
    )

    sql = str(
        query.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "join candidates as searchable_candidate_lifecycle" in sql
    assert (
        "searchable_candidate_lifecycle.id = candidate_applications.candidate_id"
        in sql
    )
    assert "searchable_candidate_lifecycle.organization_id = 17" in sql
    assert "searchable_candidate_lifecycle.deleted_at is null" in sql


def test_searchable_scope_composes_with_candidate_joining_skill_filter(db):
    scoped_query = apply_searchable_candidate_scope(
        db.query(CandidateApplication),
        organization_id=17,
    )
    filtered_query = apply_parsed_filter(
        scoped_query,
        ParsedFilter(
            skills_all=["PySpark"],
            free_text="candidates with PySpark experience",
        ),
    )

    sql = str(
        filtered_query.with_entities(CandidateApplication.id).statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "join candidates" in sql
    assert "join candidates as searchable_candidate_lifecycle" in sql
    assert "searchable_candidate_lifecycle.organization_id = 17" in sql
    assert "searchable_candidate_lifecycle.deleted_at is null" in sql


def test_searchable_scope_is_idempotent_for_layered_canonical_readers(db):
    scoped_once = apply_searchable_candidate_scope(
        db.query(CandidateApplication),
        organization_id=17,
    )
    scoped_twice = apply_searchable_candidate_scope(
        scoped_once,
        organization_id=17,
    )

    sql = str(
        scoped_twice.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert sql.count("join candidates as searchable_candidate_lifecycle") == 1


def test_searchable_scope_fails_closed_if_layered_reader_changes_tenant(db):
    scoped = apply_searchable_candidate_scope(
        db.query(CandidateApplication),
        organization_id=17,
    )
    crossed = apply_searchable_candidate_scope(
        scoped,
        organization_id=18,
    )

    sql = str(
        crossed.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "false" in sql
    assert sql.count("join candidates as searchable_candidate_lifecycle") == 1


def test_population_filter_keeps_minimum_years_when_relaxing_evidence_fields():
    parsed = ParsedFilter(
        skills_all=["Agentforce"],
        titles_all=["AI Engineer"],
        locations_country=["United Arab Emirates"],
        min_years_experience=2,
        soft_criteria=["two years of hands-on Agentforce experience"],
        preferred_criteria=["Salesforce consulting background"],
        keywords=["autonomous agents"],
        graph_predicates=[{"type": "worked_at", "value": "Acme"}],
        free_text=(
            "AI engineers in the UAE with two years of hands-on Agentforce "
            "experience and autonomous agents who worked at Acme"
        ),
    )

    population = population_filter(parsed)

    assert population.skills_all == []
    assert population.min_years_experience == 2
    assert population.titles_all == ["AI Engineer"]
    assert population.locations_country == ["United Arab Emirates"]
    assert population.soft_criteria == []
    assert population.preferred_criteria == []
    assert population.keywords == []
    assert population.graph_predicates == []


def test_population_filter_keeps_plain_structured_skill_and_years():
    parsed = ParsedFilter(
        skills_all=["Python"],
        min_years_experience=5,
        free_text="Python developers with five years of experience",
    )

    population = population_filter(parsed)

    assert population.skills_all == ["Python"]
    assert population.min_years_experience == 5


def test_application_map_selects_first_application_per_person():
    assert application_map_from_rows(
        [(20, 2), (10, 1), (11, 1), (30, None)]
    ) == {2: 20, 1: 10}


def test_graph_coverage_is_pool_fraction_and_read_failure_is_unknown():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [(1,), (3,), (3,)]

    assert estimate_graph_coverage(db, [1, 2, 3, 4]) == 0.5
    assert estimate_graph_coverage(db, []) == 1.0

    db.query.side_effect = RuntimeError("read failed")
    assert estimate_graph_coverage(db, [1]) is None
