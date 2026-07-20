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
from .skill_aliases import (
    COMMON_TITLES,
    is_common_skill,
    is_common_title,
    normalize_term,
)


_LEADING = re.compile(
    r"^(?:(?:show|find|list|search)(?:\s+me)?\s+)?"
    r"(?:(?:all|every|any)\s+)?(?:the\s+)?(?:candidates?|people|profiles?)\s+",
    re.IGNORECASE,
)
_YEARS = re.compile(r"\b(?:at\s+least\s+)?(\d{1,2})\s*\+?\s*years?(?:\s+of)?\s+experience\b", re.IGNORECASE)
_SPELLED_YEARS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+"
    r"years?(?:\s+of)?\s+experience\b",
    re.IGNORECASE,
)
_LOCATION = re.compile(
    r"\b(?:based|located|living)\s+in\s+([A-Za-z .]+?)(?=$|[,;]|\s+(?:with|who|and\s+\d))",
    re.IGNORECASE,
)
_PREFIXES = re.compile(r"^(?:with|having|skilled\s+in|skills?\s*:?|experience\s+in)\s+", re.IGNORECASE)
_SUFFIXES = re.compile(r"\s+(?:experience|skills?|expertise|background)$", re.IGNORECASE)
_TITLE_PREFIX = re.compile(r"^(?:looking\s+for\s+)?(?:an?\s+)?", re.IGNORECASE)
_PREFERENCE_CUE = re.compile(
    r"\b(?:ideally|prefer(?:red|ably)?|preference|nice[-\s]+to[-\s]+have|bonus|optional)\b",
    re.IGNORECASE,
)
_NEGATION_CUE = re.compile(
    r"\b(?:no|not|without|exclude|excluding|must\s+not|should\s+not)\b",
    re.IGNORECASE,
)
_EXPLICIT_PREFERENCE = (
    re.compile(r"^ideally\s+(?:with\s+|in\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"^preferably\s+(?:with\s+|in\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"^(?:we\s+)?prefer\s+(?:someone\s+)?(?:with\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"^(?:a\s+)?preference\s+for\s+(.+)$", re.IGNORECASE),
    re.compile(r"^nice[-\s]+to[-\s]+have\s*[:,-]?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:as\s+a\s+)?bonus(?:\s+if|\s*:|\s+-)\s*(.+)$", re.IGNORECASE),
    re.compile(r"^optional(?:\s*[:,-]\s*|\s+)(.+)$", re.IGNORECASE),
)
_SIMPLE_QUALITATIVE = re.compile(
    r"^[A-Za-z0-9+#&()./' -]{2,160}\b(?:experience|background|domain|expertise)\s*$",
    re.IGNORECASE,
)
_DISCOVERY_LEADING = re.compile(
    r"^(?:(?:can|could|would)\s+you\s+)?"
    r"(?:(?:show|find|list|search|give)(?:\s+me)?\s+)?"
    r"(?:(?:the\s+)?(?:top|best)(?:\s+\d+)?\s+)?"
    r"(?:candidates?|people|profiles?)\s+(?:with\s+)?",
    re.IGNORECASE,
)
_TITLE_REQUEST_LEADING = re.compile(
    r"^(?:(?:(?:can|could|would)\s+you\s+)?"
    r"(?:please\s+)?(?:show|find|list|search|give)(?:\s+me)?|looking\s+for)\s+",
    re.IGNORECASE,
)


def _atomic_quality(text: str) -> str:
    """Keep a domain-qualified experience phrase as one grounding criterion."""

    quality = (text or "").strip(" ,.;")
    parenthetical = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", quality)
    if parenthetical and "domain" in parenthetical.group(2).lower():
        base = parenthetical.group(1).strip()
        domain = normalize_term(parenthetical.group(2))
        return f"{base} within the {domain}"
    return quality


def _explicit_preference_quality(text: str) -> str | None:
    """Return the quality only when the hedge is syntactically unambiguous.

    Words such as "preferred" and "bonus" are also domain nouns/adjectives
    ("preferred supplier", "bonus payments"). A lexical match would silently
    weaken those requirements, so ambiguous forms stay required or go to the
    model parser.
    """

    quality = (text or "").strip(" ,.;")
    for pattern in _EXPLICIT_PREFERENCE:
        match = pattern.fullmatch(quality)
        if match is not None:
            return match.group(1).strip(" ,.;") or None
    return None


def _is_single_qualitative_phrase(text: str) -> bool:
    """Conservative guard for the title + one-quality fast path."""

    quality = (text or "").strip()
    if (
        not quality
        or _NEGATION_CUE.search(quality)
        or _YEARS.search(quality)
        or _SPELLED_YEARS.search(quality)
    ):
        return False
    if re.search(r"\s+(?:and|or)\s+|[,;]", quality, re.IGNORECASE):
        return False
    parenthetical = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", quality)
    if parenthetical and "domain" in parenthetical.group(2).lower():
        return True
    if not _SIMPLE_QUALITATIVE.fullmatch(quality):
        return False
    structured_term = _SUFFIXES.sub("", _PREFIXES.sub("", quality)).strip(" ,")
    return not (is_common_title(structured_term) or is_common_skill(structured_term))


def _contains_known_title(text: str) -> bool:
    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(title)}s?(?![a-z0-9])",
            text,
            re.IGNORECASE,
        )
        for title in COMMON_TITLES
    )


def _parse_title_with_quality(original: str, working: str) -> ParsedFilter | None:
    """Conservatively parse ``<known title> with <one qualitative phrase>``.

    This covers the trust-critical search seam without asking a model to retain
    the occupation. Mixed required/preferred prose still goes to the model
    parser, which can assign each phrase independently.
    """

    working = _TITLE_REQUEST_LEADING.sub("", working).strip(" ,")
    for title in sorted(COMMON_TITLES, key=len, reverse=True):
        match = re.match(
            rf"^(?:an?\s+)?{re.escape(title)}s?\s+"
            rf"(?:with|having|who\s+(?:has|have))\s+(.+)$",
            working,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        remainder = match.group(1).strip()
        preferred = _explicit_preference_quality(remainder)
        if preferred and _is_single_qualitative_phrase(preferred):
            return ParsedFilter(
                titles_all=[title],
                preferred_criteria=[_atomic_quality(preferred)],
                free_text=original,
            )
        # A mixed or ambiguous priority phrase needs the richer parser; do not
        # flatten an "ideally" clause into a required phrase beside it.
        if _PREFERENCE_CUE.search(remainder):
            return None
        if not _is_single_qualitative_phrase(remainder):
            return None
        quality = _atomic_quality(remainder)
        if not quality:
            return None
        return ParsedFilter(
            titles_all=[title],
            soft_criteria=[quality],
            free_text=original,
        )
    return None


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
    title_with_quality = _parse_title_with_quality(original, working)
    if title_with_quality is not None:
        return title_with_quality
    # If the title fast path deliberately rejected an ambiguous hedge, stop
    # here. The generic qualitative branch must not consume the whole sentence,
    # lose the title, and silently promote the preference to a requirement.
    if _PREFERENCE_CUE.search(working) and _contains_known_title(working):
        return None
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

    # Check the untouched suffix before generic cleanup removes the word
    # "experience". This catches tool-cleaned phrases such as
    # "Treasury banking experience" and bounded requests prefixed with
    # "top candidates with".
    qualitative = _DISCOVERY_LEADING.sub("", working).strip(" ,")
    if (
        _SIMPLE_QUALITATIVE.fullmatch(qualitative)
        and not _SPELLED_YEARS.search(qualitative)
        and not _NEGATION_CUE.search(qualitative)
        and not re.search(r"\s+(?:and|or)\s+", qualitative, re.IGNORECASE)
    ):
        structured_term = _SUFFIXES.sub("", _PREFIXES.sub("", qualitative)).strip(" ,")
        if is_common_title(structured_term):
            return ParsedFilter(titles_all=[structured_term], free_text=original)
        if is_common_skill(structured_term):
            return ParsedFilter(skills_all=[structured_term], free_text=original)
        quality = _atomic_quality(qualitative)
        preferred_quality = _explicit_preference_quality(quality)
        if preferred_quality:
            return ParsedFilter(
                preferred_criteria=[preferred_quality], free_text=original
            )
        # "preferred X" is often a genuine hedge, but also appears in domain
        # nouns such as "preferred supplier". Keep the known noun phrase intact;
        # route other ambiguous preferred-adjective forms to the richer parser.
        if re.match(r"^preferred\s+", quality, re.IGNORECASE) and not re.match(
            r"^preferred\s+suppliers?\b", quality, re.IGNORECASE
        ):
            return None
        return ParsedFilter(soft_criteria=[quality], free_text=original)

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
