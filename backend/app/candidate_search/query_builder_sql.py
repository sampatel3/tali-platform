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
import re
from typing import Optional

from sqlalchemy import Text, and_, bindparam, cast, func, literal_column, or_, text
from sqlalchemy.dialects.postgresql import JSONB

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from .prompts import expand_region, normalise_country
from .schemas import ParsedFilter
from .skill_aliases import expand_skill_term, normalize_term

logger = logging.getLogger("taali.candidate_search.sql")
_ENGLISH = literal_column("'english'")
_RELEVANCE_TOKEN_RE = re.compile(r"[a-z0-9+#]+", re.IGNORECASE)
_RELEVANCE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "background",
        "domain",
        "experience",
        "expertise",
        "for",
        "in",
        "of",
        "the",
        "with",
        "within",
    }
)


def _relevance_tokens(items: list[str]) -> list[str]:
    """Lexical recall signals, without qualitative scaffolding words."""

    tokens: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = normalize_term(item)
        for token in _RELEVANCE_TOKEN_RE.findall(normalized):
            lowered = token.lower()
            if lowered in _RELEVANCE_STOPWORDS or lowered in seen:
                continue
            seen.add(lowered)
            tokens.append(lowered)
    return tokens


def _summed_rank(vector, tokens: list[str]):
    ranks = [
        func.ts_rank_cd(vector, func.plainto_tsquery(_ENGLISH, token))
        for token in tokens
    ]
    if not ranks:
        return None
    total = ranks[0]
    for rank in ranks[1:]:
        total = total + rank
    return total


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


def _title_clause(term: str, *, bind_name: str):
    """Match a parser "skill" against structured current/historical titles.

    Recruiters routinely express occupations ("project manager", "scrum
    master", "data engineer") in the same grammatical slot as technologies.
    The parser therefore puts both into ``skills_*``.  Candidate enrichment,
    however, stores occupations primarily in ``position`` / ``headline`` and
    ``experience_entries[].title`` rather than the skills array.  Searching
    those fields here recovers that evidence without another model call or a
    vector index.

    ``bind_name`` is caller-generated and unique because a query can contain
    several AND/OR terms.
    """
    pattern = f"%{term}%"
    history = text(
        "EXISTS (SELECT 1 FROM jsonb_array_elements("
        "COALESCE(candidates.experience_entries::jsonb, '[]'::jsonb)) e "
        f"WHERE COALESCE(e->>'title', '') ILIKE :{bind_name})"
    ).bindparams(bindparam(bind_name, pattern))
    return or_(
        Candidate.position.ilike(pattern),
        Candidate.headline.ilike(pattern),
        history,
    )


def _skill_or_title_clause(skill: str, *, bind_name: str):
    """Taxonomy-aware, case-insensitive skill match with title fallback.

    ``skills`` contains labels such as ``Python (Programming Language)`` and
    ``Amazon Web Services (AWS)``. Exact JSON containment alone misses those
    when a recruiter types the natural short form. The lower(JSON text) LIKE
    expression is backed by a trigram GIN index in migration 159.
    """
    skills_jsonb = cast(Candidate.skills, JSONB)
    skill_match = skills_jsonb.op("@>")(func.jsonb_build_array(skill))
    skills_text = func.lower(func.coalesce(cast(skills_jsonb, Text), ""))
    variants = expand_skill_term(skill)
    normalized_matches = []
    for variant in variants:
        # Avoid substring false positives for tiny language names (Go, R, C).
        pattern = f'%"{variant}"%' if len(variant) <= 2 else f"%{variant}%"
        normalized_matches.append(skills_text.like(pattern))
    return or_(
        skill_match,
        *normalized_matches,
        # Backward compatibility for older parser results that put occupations
        # in skills_* before titles_* existed.
        _title_clause(skill, bind_name=bind_name),
    )


def _skills_all_clause(skills: list[str]):
    """AND-match: every term must appear as a skill or occupation title.

    Builds one ``@>`` containment per skill so the planner can pick a
    GIN index if/when one is added (``CREATE INDEX ... USING gin
    (skills jsonb_path_ops)``). The right-hand side is built with
    ``jsonb_build_array(skill)`` so Postgres handles literal escaping.
    """
    if not skills:
        return None
    clauses = []
    for index, skill in enumerate(skills):
        clauses.append(
            _skill_or_title_clause(skill, bind_name=f"skills_all_title_{index}")
        )
    return and_(*clauses)


def _skills_any_clause(skills: list[str]):
    """OR-match: at least one term must appear as a skill or title."""
    if not skills:
        return None
    clauses = []
    for index, skill in enumerate(skills):
        clauses.append(
            _skill_or_title_clause(skill, bind_name=f"skills_any_title_{index}")
        )
    return or_(*clauses)


def _titles_all_clause(titles: list[str]):
    if not titles:
        return None
    return and_(
        *[
            _title_clause(title, bind_name=f"titles_all_{index}")
            for index, title in enumerate(titles)
        ]
    )


def _titles_any_clause(titles: list[str]):
    if not titles:
        return None
    return or_(
        *[
            _title_clause(title, bind_name=f"titles_any_{index}")
            for index, title in enumerate(titles)
        ]
    )


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
    for index, country in enumerate(countries):
        pattern = f"%{country}%"
        bind_name = f"history_country_{index}"
        history_clauses.append(
            text(
                "EXISTS (SELECT 1 FROM jsonb_array_elements("
                "candidates.experience_entries::jsonb) e "
                f"WHERE COALESCE(e->>'country', e->>'location', '') ILIKE :{bind_name})"
            ).bindparams(bindparam(bind_name, pattern))
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


def _keywords_clause(keywords: list[str], *, match_all: bool = False):
    """Indexed full-text retrieval across CVs plus enriched profile fallback.

    Each phrase uses ``plainto_tsquery`` (all meaningful words in that phrase).
    Residual keywords remain OR alternatives; separately parsed soft
    requirements are ANDed so "Treasury and data" cannot qualify on only one.
    Candidate titles/summary/experience cover people whose CV text has not been
    fetched yet.
    """
    if not keywords:
        return None
    clauses = []
    app_vector = func.to_tsvector(
        _ENGLISH, func.coalesce(CandidateApplication.cv_text, "")
    )
    candidate_vector = func.to_tsvector(
        _ENGLISH, func.coalesce(Candidate.cv_text, "")
    )
    experience_text = func.lower(
        func.coalesce(cast(cast(Candidate.experience_entries, JSONB), Text), "")
    )
    for term in keywords:
        if not term:
            continue
        normalized = normalize_term(term)
        if not normalized:
            continue
        ts_query = func.plainto_tsquery(_ENGLISH, normalized)
        pattern = f"%{term}%"
        clauses.append(
            or_(
                app_vector.op("@@")(ts_query),
                candidate_vector.op("@@")(ts_query),
                Candidate.position.ilike(pattern),
                Candidate.headline.ilike(pattern),
                Candidate.summary.ilike(pattern),
                experience_text.like(f"%{normalized}%"),
            )
        )
    if not clauses:
        return None
    return and_(*clauses) if match_all else or_(*clauses)


def needs_candidate_join(parsed: ParsedFilter) -> bool:
    """True when filtering or relevance ordering needs ``Candidate`` fields."""
    return bool(
        parsed.skills_all
        or parsed.skills_any
        or parsed.titles_all
        or parsed.titles_any
        or parsed.locations_country
        or parsed.locations_region
        or parsed.min_years_experience
        or parsed.keywords
        or parsed.soft_criteria
        or parsed.preferred_criteria
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

    titles_all_c = _titles_all_clause(parsed.titles_all)
    if titles_all_c is not None:
        query = query.filter(titles_all_c)

    titles_any_c = _titles_any_clause(parsed.titles_any)
    if titles_any_c is not None:
        query = query.filter(titles_any_c)

    countries = _expand_countries(parsed)
    country_c = _country_clause(countries)
    if country_c is not None:
        query = query.filter(country_c)

    years_c = _min_years_clause(parsed.min_years_experience)
    if years_c is not None:
        query = query.filter(years_c)

    effective_keywords = list(parsed.keywords)
    effective_soft = list(parsed.soft_criteria) if soft_criteria_as_keywords else []
    # Keyword/profile clauses reference Candidate fields even when no explicit
    # structured candidate filter was parsed.
    if (effective_keywords or effective_soft) and not needs_candidate_join(parsed):
        query = query.join(Candidate, Candidate.id == CandidateApplication.candidate_id)
    keywords_c = _keywords_clause(effective_keywords, match_all=False)
    if keywords_c is not None:
        query = query.filter(keywords_c)
    soft_c = _keywords_clause(effective_soft, match_all=True)
    if soft_c is not None:
        query = query.filter(soft_c)

    return query


def apply_relevance_order(base_query, parsed: ParsedFilter):
    """Order the full deterministic match set before any bounded verification.

    This removes the old database-natural "first 50" behaviour. Lexical CV and
    profile relevance selects the bounded evidence window; recency and id provide
    stable tie-breakers for structural-only searches.
    """
    required_tokens = _relevance_tokens(
        [*parsed.soft_criteria, *parsed.keywords]
    )
    preferred_tokens = _relevance_tokens(list(parsed.preferred_criteria))
    order = []
    if required_tokens or preferred_tokens:
        query = base_query
        if not needs_candidate_join(parsed):
            query = query.join(
                Candidate, Candidate.id == CandidateApplication.candidate_id
            )
        vector = func.to_tsvector(
            _ENGLISH,
            func.concat_ws(
                " ",
                func.coalesce(CandidateApplication.cv_text, ""),
                func.coalesce(Candidate.cv_text, ""),
                func.coalesce(Candidate.position, ""),
                func.coalesce(Candidate.headline, ""),
                func.coalesce(Candidate.summary, ""),
                func.coalesce(cast(Candidate.experience_entries, Text), ""),
            ),
        )
        # Required signals are additive and sort before preferences. A CV that
        # says "Treasury Manager" therefore gets non-zero relevance even if it
        # lacks parser scaffolding such as "experience" or "domain"; an optional
        # Big Four phrase can never zero-out a genuine Treasury match.
        required_rank = _summed_rank(vector, required_tokens)
        preferred_rank = _summed_rank(vector, preferred_tokens)
        if required_rank is not None:
            order.append(required_rank.desc())
        if preferred_rank is not None:
            order.append(preferred_rank.desc())
    else:
        query = base_query
    order.extend(
        [
            CandidateApplication.updated_at.desc().nullslast(),
            CandidateApplication.created_at.desc().nullslast(),
            CandidateApplication.id.desc(),
        ]
    )
    return query.order_by(None).order_by(*order)
