"""Translate ``ParsedFilter`` hard filters into SQLAlchemy query clauses.

Operates on a query already filtered by ``organization_id``. Adds a join
to ``Candidate`` lazily — only when the filter actually needs candidate
fields. Falls back to ``cv_text`` ILIKE for ``keywords``.

Postgres-specific: uses ``jsonb`` casts and ``@>`` containment for skills,
and ``jsonb_array_elements`` for experience-history scans. SQLite test
runs are not supported by this module.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import and_, cast, func, or_, text
from sqlalchemy.dialects.postgresql import JSONB

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from .prompts import expand_region, normalise_country
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.sql")


def _expand_countries(parsed: ParsedFilter) -> list[str]:
    """Combine ``locations_country`` + expanded regions, dedup case-insensitively."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in parsed.locations_country:
        canonical = normalise_country(raw)
        if canonical and canonical.lower() not in seen:
            out.append(canonical)
            seen.add(canonical.lower())
    for region in parsed.locations_region:
        for country in expand_region(region):
            if country.lower() not in seen:
                out.append(country)
                seen.add(country.lower())
    return out


def _skills_all_clause(skills: list[str]):
    """AND-match: every skill must appear in ``Candidate.skills``.

    Builds one ``@>`` containment per skill so the planner can pick a
    GIN index if/when one is added (``CREATE INDEX ... USING gin
    (skills jsonb_path_ops)``). The right-hand side is built with
    ``jsonb_build_array(skill)`` so Postgres handles literal escaping.
    """
    if not skills:
        return None
    skills_jsonb = cast(Candidate.skills, JSONB)
    clauses = []
    for skill in skills:
        # ``@>`` containment: ``["AWS Glue"] @> ["AWS Glue"]`` is true.
        literal = func.jsonb_build_array(skill)
        clauses.append(skills_jsonb.op("@>")(literal))
    return and_(*clauses)


def _skills_any_clause(skills: list[str]):
    """OR-match: at least one skill must appear."""
    if not skills:
        return None
    skills_jsonb = cast(Candidate.skills, JSONB)
    clauses = []
    for skill in skills:
        literal = func.jsonb_build_array(skill)
        clauses.append(skills_jsonb.op("@>")(literal))
    return or_(*clauses)


def _country_clause(countries: list[str]):
    """Match either current country OR any experience entry's country/location.

    Uses an EXISTS-over-jsonb_array_elements for the work-history side.
    The element's ``location`` or ``country`` field can be a free-form
    string ("London, UK"); an ILIKE on the country name matches both.
    """
    if not countries:
        return None
    current = Candidate.location_country.in_(countries)
    # Build an OR of ILIKE patterns across experience entries.
    # Implementation detail: jsonb_array_elements is a set-returning
    # function, so we wrap it in a correlated subquery via ``func.exists``.
    # The pattern uses ``%<country>%`` to tolerate "London, United Kingdom".
    history_clauses = []
    for country in countries:
        pattern = f"%{country}%"
        history_clauses.append(
            text(
                "EXISTS (SELECT 1 FROM jsonb_array_elements("
                "candidates.experience_entries::jsonb) e "
                "WHERE COALESCE(e->>'country', e->>'location', '') ILIKE :p)"
            ).bindparams(p=pattern)
        )
    if not history_clauses:
        return current
    return or_(current, *history_clauses)


def _min_years_clause(min_years: Optional[int]):
    """Approximate years-of-experience using earliest start_date in ``experience_entries``.

    Pragmatic v1: today minus earliest start ≈ years experience. Doesn't
    handle gaps, multiple concurrent jobs, or non-employed periods. Real
    summation across entries is deferred — needs a richer parser shape
    that we don't yet write to ``experience_entries``.
    """
    if min_years is None or min_years <= 0:
        return None
    # ``e->>'start_date'`` may be 'YYYY-MM-DD' or 'YYYY-MM' or empty.
    # ``substring(... from 1 for 4)::int`` extracts the year safely.
    return text(
        "EXISTS (SELECT 1 FROM jsonb_array_elements("
        "candidates.experience_entries::jsonb) e "
        "WHERE e->>'start_date' ~ '^[0-9]{4}' "
        "AND (EXTRACT(YEAR FROM CURRENT_DATE)::int "
        "- substring(e->>'start_date' from 1 for 4)::int) >= :years)"
    ).bindparams(years=int(min_years))


def _keywords_clause(keywords: list[str]):
    """Residual ILIKE OR-block on ``cv_text`` (and ``soft_criteria`` rides here too)."""
    if not keywords:
        return None
    clauses = []
    for term in keywords:
        if not term:
            continue
        pattern = f"%{term}%"
        clauses.append(CandidateApplication.cv_text.ilike(pattern))
    if not clauses:
        return None
    return or_(*clauses)


def needs_candidate_join(parsed: ParsedFilter) -> bool:
    """True if applying ``parsed`` requires joining ``Candidate``."""
    return bool(
        parsed.skills_all
        or parsed.skills_any
        or parsed.locations_country
        or parsed.locations_region
        or parsed.min_years_experience
    )


def apply_parsed_filter(
    base_query,
    parsed: ParsedFilter,
    *,
    soft_criteria_as_keywords: bool = True,
):
    """Apply hard SQL filters from ``parsed`` to ``base_query``.

    The base query MUST already filter ``CandidateApplication.organization_id``
    so org isolation is preserved.

    ``soft_criteria_as_keywords``: when True (default), soft criteria are
    appended to ``keywords`` and tested via ILIKE on ``cv_text``. This is
    the fastest path for the common case ("in production"). When the
    rerank step is enabled, set this to False to avoid double-filtering
    with the LLM.
    """
    query = base_query
    if needs_candidate_join(parsed):
        query = query.join(
            Candidate, Candidate.id == CandidateApplication.candidate_id
        )

    skills_all_c = _skills_all_clause(parsed.skills_all)
    if skills_all_c is not None:
        query = query.filter(skills_all_c)

    skills_any_c = _skills_any_clause(parsed.skills_any)
    if skills_any_c is not None:
        query = query.filter(skills_any_c)

    countries = _expand_countries(parsed)
    country_c = _country_clause(countries)
    if country_c is not None:
        query = query.filter(country_c)

    years_c = _min_years_clause(parsed.min_years_experience)
    if years_c is not None:
        query = query.filter(years_c)

    effective_keywords = list(parsed.keywords)
    if soft_criteria_as_keywords:
        effective_keywords.extend(parsed.soft_criteria)
    keywords_c = _keywords_clause(effective_keywords)
    if keywords_c is not None:
        query = query.filter(keywords_c)

    return query
