"""Compiled-SQL tests for the NL search query builder.

The test DB is SQLite (see conftest.py), but the builder targets
Postgres-only operators (``@>``, ``jsonb_array_elements``). We compile
queries against the postgresql dialect with bound literals inlined and
inspect the rendered SQL string. No DB execution.
"""

from __future__ import annotations

import pytest
from sqlalchemy import literal_column, select
from sqlalchemy.dialects import postgresql

from app.candidate_search.query_builder_sql import (
    apply_parsed_filter,
    needs_candidate_join,
)
from app.candidate_search.schemas import ParsedFilter
from app.models.candidate_application import CandidateApplication


def _compile(query) -> str:
    """Render the SQL with literal binds — for snapshot-style assertions."""
    stmt = query.statement if hasattr(query, "statement") else query
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _base_query(db):
    return db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == 1
    )


def test_skills_all_emits_jsonb_containment(db):
    parsed = ParsedFilter(skills_all=["AWS Glue", "Python"])
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    # One @> per skill, both AND-ed; literals built with jsonb_build_array.
    assert sql.count('@>') == 2
    assert sql.count("jsonb_build_array") == 2
    assert "AWS Glue" in sql
    assert "Python" in sql
    # Join was added because skills filter needs Candidate.
    assert "candidates" in sql.lower()


def test_skills_any_uses_or(db):
    parsed = ParsedFilter(skills_any=["A", "B"])
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    # OR of two containment checks.
    assert sql.count('@>') == 2
    assert sql.count("jsonb_build_array") == 2
    assert " OR " in sql.upper()


def test_country_clause_combines_current_and_history(db):
    parsed = ParsedFilter(locations_country=["UK"])
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    # location_country IN match.
    assert "location_country IN" in sql
    # And EXISTS over experience_entries jsonb.
    assert "jsonb_array_elements" in sql
    # Country alias was applied — "United Kingdom" appears, not "UK".
    assert "United Kingdom" in sql


def test_region_expands_to_country_list(db):
    parsed = ParsedFilter(locations_region=["europe"])
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    # A few representative European countries should be in the IN clause.
    assert "United Kingdom" in sql
    assert "France" in sql
    assert "Germany" in sql


def test_min_years_clause_uses_substring_extraction(db):
    parsed = ParsedFilter(min_years_experience=5)
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    assert "jsonb_array_elements" in sql
    assert "start_date" in sql
    # The extracted-year arithmetic compares against 5.
    assert ">= 5" in sql


def test_keywords_emits_ilike_or(db):
    parsed = ParsedFilter(keywords=["production", "kubernetes"])
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    assert sql.lower().count("ilike") == 2


def test_empty_filter_does_not_join_or_filter(db):
    parsed = ParsedFilter()
    base = _base_query(db)
    out = apply_parsed_filter(base, parsed)
    assert _compile(out) == _compile(base)


def test_soft_criteria_routed_to_keywords_when_no_rerank(db):
    parsed = ParsedFilter(soft_criteria=["large enterprise"])
    sql = _compile(
        apply_parsed_filter(_base_query(db), parsed, soft_criteria_as_keywords=True)
    )
    assert "ILIKE" in sql or "ilike" in sql
    assert "large enterprise" in sql


def test_soft_criteria_skipped_when_rerank_will_handle(db):
    parsed = ParsedFilter(soft_criteria=["large enterprise"])
    sql = _compile(
        apply_parsed_filter(_base_query(db), parsed, soft_criteria_as_keywords=False)
    )
    assert "large enterprise" not in sql


def test_compound_filter_renders_all_clauses(db):
    parsed = ParsedFilter(
        skills_all=["Python"],
        locations_region=["europe"],
        min_years_experience=5,
        soft_criteria=["large enterprise", "in production"],
    )
    sql = _compile(
        apply_parsed_filter(_base_query(db), parsed, soft_criteria_as_keywords=True)
    )
    assert '@>' in sql
    assert "United Kingdom" in sql
    assert "start_date" in sql
    assert "large enterprise" in sql.lower() or "Large Enterprise" in sql or "large enterprise" in sql


def test_needs_candidate_join_for_skills_only():
    assert needs_candidate_join(ParsedFilter(skills_all=["x"]))
    assert needs_candidate_join(ParsedFilter(locations_country=["UK"]))
    assert needs_candidate_join(ParsedFilter(min_years_experience=3))
    assert not needs_candidate_join(ParsedFilter(keywords=["foo"]))
    assert not needs_candidate_join(ParsedFilter())


def test_string_with_double_quote_is_safely_passed(db):
    parsed = ParsedFilter(skills_all=['Foo"Bar'])
    sql = _compile(apply_parsed_filter(_base_query(db), parsed))
    # jsonb_build_array(...) handles JSON escaping at the DB level,
    # so we don't have to. The skill passes through as a plain literal.
    assert "jsonb_build_array" in sql
    assert "Foo" in sql
    assert "Bar" in sql
