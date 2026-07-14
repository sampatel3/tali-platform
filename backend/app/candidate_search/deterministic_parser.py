"""Zero-model parser for conservative, common candidate searches.

This deliberately returns ``None`` whenever a query is ambiguous.  It handles
the high-volume cases recruiters expect to be instant (explicit skills, job
titles, candidate location and minimum years); the existing model parser remains
the fallback for narrative requirements and nuanced constraints.
"""

from __future__ import annotations

import re

from .prompts import CANONICAL_COUNTRIES, REGION_ALIASES, normalise_country
from .schemas import ParsedFilter
from .skill_aliases import is_common_skill, is_common_title, normalize_term


_LEADING = re.compile(
    r"^(?:(?:show|find|list|search)(?:\s+me)?\s+)?"
    r"(?:(?:all|every|any)\s+)?(?:the\s+)?(?:candidates?|people|profiles?)\s+",
    re.IGNORECASE,
)
_YEARS = re.compile(r"\b(?:at\s+least\s+)?(\d{1,2})\s*\+?\s*years?(?:\s+of)?\s+experience\b", re.IGNORECASE)
_LOCATION = re.compile(
    r"\b(?:based|located|living)\s+in\s+([A-Za-z .]+?)(?=$|[,;]|\s+(?:with|who|and\s+\d))",
    re.IGNORECASE,
)
_PREFIXES = re.compile(r"^(?:with|having|skilled\s+in|skills?\s*:?|experience\s+in)\s+", re.IGNORECASE)
_SUFFIXES = re.compile(r"\s+(?:experience|skills?|expertise|background)$", re.IGNORECASE)
_TITLE_PREFIX = re.compile(r"^(?:looking\s+for\s+)?(?:an?\s+)?", re.IGNORECASE)


def _split_terms(text: str) -> tuple[list[str], bool]:
    """Return terms plus whether the connector was OR (otherwise AND)."""
    has_or = bool(re.search(r"\s+or\s+", text, re.IGNORECASE))
    connector = r"\s+or\s+" if has_or else r"\s+and\s+|\s*,\s*"
    terms = [p.strip() for p in re.split(connector, text) if normalize_term(p)]
    return terms, has_or


def parse_common_query(query: str) -> ParsedFilter | None:
    """Parse a simple structured query, or return ``None`` for model fallback."""
    original = (query or "").strip()
    if not original:
        return ParsedFilter(free_text="")

    working = _LEADING.sub("", original).strip(" ,")
    years = None
    years_match = _YEARS.search(working)
    if years_match:
        years = int(years_match.group(1))
        working = (working[: years_match.start()] + working[years_match.end() :]).strip(" ,")

    countries: list[str] = []
    regions: list[str] = []
    location_match = _LOCATION.search(working)
    if location_match:
        raw_location = normalize_term(location_match.group(1))
        if raw_location in REGION_ALIASES:
            regions.append(raw_location)
        else:
            country = normalise_country(raw_location)
            # Do not guess city→country mappings here; the model parser handles
            # those. Deterministic mode only accepts known countries/aliases.
            if country not in CANONICAL_COUNTRIES:
                return None
            countries.append(country)
        working = (working[: location_match.start()] + working[location_match.end() :]).strip(" ,")

    working = re.sub(r"\b(?:and\s+)?(?:with\s+)?$", "", working, flags=re.IGNORECASE).strip(" ,")
    working = _PREFIXES.sub("", working)
    working = _SUFFIXES.sub("", working).strip(" ,")
    working = _TITLE_PREFIX.sub("", working).strip(" ,")
    if not working:
        return None

    terms, has_or = _split_terms(working)
    if not terms or len(terms) > 8:
        return None

    title_flags = [is_common_title(term) for term in terms]
    skill_flags = [is_common_skill(term) for term in terms]
    # Mixed recognised skills/titles are valid; any unrecognised prose makes
    # the query ambiguous and routes it to the richer parser.
    if not all(t or s for t, s in zip(title_flags, skill_flags)):
        return None

    titles = [term for term, is_title in zip(terms, title_flags) if is_title]
    skills = [term for term, is_title in zip(terms, title_flags) if not is_title]
    return ParsedFilter(
        titles_any=titles if has_or else [],
        titles_all=titles if not has_or else [],
        skills_any=skills if has_or else [],
        skills_all=skills if not has_or else [],
        locations_country=countries,
        locations_region=regions,
        min_years_experience=years,
        free_text=original,
    )
