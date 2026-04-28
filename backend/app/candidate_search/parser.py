"""Haiku-based parser: NL query → ``ParsedFilter``.

One Claude call per uncached query. ~$0.0005 / call at Haiku 4.5 rates.
On any failure we degrade to a keyword-only filter so the user still
gets best-effort ILIKE matches.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

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


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = obj_match.group(0)
    return text


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


def _resolve_anthropic_client():
    from anthropic import Anthropic

    from ..platform.config import settings

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return Anthropic(api_key=api_key)


def parse_nl_query(query: str, *, client=None) -> ParsedFilter:
    """Parse one NL query. Never raises; returns a best-effort ``ParsedFilter``."""
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return ParsedFilter(free_text="")

    system_prompt, user_prompt = build_parser_prompt(cleaned_query)

    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            logger.warning("Parser client init failed: %s", exc)
            return _fallback_filter(cleaned_query)

    try:
        response = client.messages.create(
            model=MODEL_VERSION,
            max_tokens=PARSER_MAX_TOKENS,
            temperature=PARSER_TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = ""
        try:
            raw_text = response.content[0].text  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            raw_text = ""
    except Exception as exc:
        logger.warning("Parser Claude call failed: %s", exc)
        return _fallback_filter(cleaned_query)

    text = _strip_json_fences(raw_text)
    try:
        parsed_dict = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Parser returned non-JSON: %s", exc)
        return _fallback_filter(cleaned_query)

    try:
        parsed_filter = ParsedFilter.model_validate(parsed_dict)
    except ValidationError as exc:
        logger.warning("Parser output failed schema: %s", exc)
        return _fallback_filter(cleaned_query)

    return _normalise(parsed_filter, cleaned_query)
