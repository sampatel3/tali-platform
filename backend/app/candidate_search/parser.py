"""Haiku-based parser: NL query → ``ParsedFilter``.

One Claude call per uncached query. ~$0.0005 / call at Haiku 4.5 rates.
On any failure we degrade to a keyword-only filter so the user still
gets best-effort ILIKE matches.
"""

from __future__ import annotations

import logging

from ..llm import MeteringContext, generate_structured
from . import MODEL_VERSION
from .prompts import (
    build_parser_prompt,
    expand_region,
    normalise_country,
)
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.parser")

PARSER_MAX_TOKENS = 512
PARSER_TEMPERATURE = 0.0


def _normalise(filter_obj: ParsedFilter, query: str) -> ParsedFilter:
    """Server-side cleanup applied AFTER schema validation.

    Defensive: even if Haiku misses an alias, we still normalise here.
    """
    countries = []
    seen = set()
    for raw in filter_obj.locations_country:
        canonical = normalise_country(raw)
        if canonical and canonical.lower() not in seen:
            countries.append(canonical)
            seen.add(canonical.lower())

    # Region keys: only keep ones we actually know how to expand.
    regions = []
    for raw in filter_obj.locations_region:
        if expand_region(raw):
            regions.append(raw.strip().lower())

    # Trim whitespace on every list element.
    skills_all = [s.strip() for s in filter_obj.skills_all if s and s.strip()]
    skills_any = [s.strip() for s in filter_obj.skills_any if s and s.strip()]
    soft = [s.strip() for s in filter_obj.soft_criteria if s and s.strip()]
    keywords = [s.strip() for s in filter_obj.keywords if s and s.strip()]

    return filter_obj.model_copy(
        update={
            "locations_country": countries,
            "locations_region": regions,
            "skills_all": skills_all,
            "skills_any": skills_any,
            "soft_criteria": soft,
            "keywords": keywords,
            "free_text": (filter_obj.free_text or query).strip(),
        }
    )


def _fallback_filter(query: str) -> ParsedFilter:
    """Last-resort filter when parsing fails. Keywords-only."""
    cleaned = (query or "").strip()
    return ParsedFilter(
        keywords=[cleaned] if cleaned else [],
        free_text=cleaned,
    )


def _resolve_anthropic_client(*, organization_id: int | None = None):
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client(organization_id=organization_id)


def parse_nl_query(
    query: str,
    *,
    client=None,
    metering: dict | None = None,
) -> ParsedFilter:
    """Parse one NL query. Never raises; returns a best-effort ``ParsedFilter``.

    ``metering`` should at minimum contain ``organization_id`` and ``user_id``
    for accurate attribution; defaults to ``{"feature": "search_parse"}``
    which records the call but without per-org context.
    """
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return ParsedFilter(free_text="")

    system_prompt, user_prompt = build_parser_prompt(cleaned_query)

    if client is None:
        try:
            client = _resolve_anthropic_client(
                organization_id=(metering or {}).get("organization_id"),
            )
        except Exception as exc:
            logger.warning("Parser client init failed: %s", exc)
            return _fallback_filter(cleaned_query)

    # System prompt is identical across every parser call (only the user
    # query changes). We mark it cacheable so successive queries from any
    # org can hit the cache. The prompt currently sits ~800 tokens, below
    # Haiku 4.5's 4096-token minimum cacheable prefix — Anthropic silently
    # skips caching when the prefix is too short, so this is free today
    # and activates automatically if the prompt grows past the threshold.
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # No retry: the parser fast-fails to a keyword-only filter on any
    # call / parse / schema failure, so the user still gets ILIKE matches.
    # Forced tool-use: the model emits ParsedFilter as the tool's ``.input``
    # dict — one schema source, no JSON repair.
    result = generate_structured(
        client,
        model=MODEL_VERSION,
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
        output_model=ParsedFilter,
        metering=MeteringContext.from_dict(metering, default_feature="search_parse"),
        max_tokens=PARSER_MAX_TOKENS,
        temperature=PARSER_TEMPERATURE,
        max_retries=0,
        use_tool_use=True,
    )
    if not result.ok or result.value is None:
        logger.warning("Parser failed (%s); falling back to keywords", result.error_reason)
        return _fallback_filter(cleaned_query)

    return _normalise(result.value, cleaned_query)
