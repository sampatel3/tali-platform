"""NL query → ``ParsedFilter``.

One Claude call per uncached query (``PARSER_MODEL``, Sonnet by default — the
extraction makes subtle judgement calls a smaller model gets wrong). On any
failure we degrade to a keyword-only filter so the user still gets best-effort
ILIKE matches.
"""

from __future__ import annotations

import logging
import os

from ..llm import MeteringContext, generate_structured
from ..services.pricing_service import Feature
from .metering import admitted_search_metering
from .prompts import (
    build_parser_prompt,
    expand_region,
    normalise_country,
)
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.parser")

PARSER_MAX_TOKENS = 512
PARSER_TEMPERATURE = 0.0
# Parse on a stronger model than the codebase FAST_MODEL (Haiku): the
# NL→filter extraction makes subtle judgement calls (is "a Western company" the
# candidate's location or the employer's origin? is "salary <= 30k" one
# constraint?) that Haiku gets wrong and Sonnet gets right. It's ONE call per
# query (~$0.004), negligible beside the grounding fan-out. Env-overridable.
PARSER_MODEL = os.getenv("CLAUDE_SEARCH_PARSER_MODEL") or "claude-sonnet-4-6"


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
    titles_all = [s.strip() for s in filter_obj.titles_all if s and s.strip()]
    titles_any = [s.strip() for s in filter_obj.titles_any if s and s.strip()]
    soft = [s.strip() for s in filter_obj.soft_criteria if s and s.strip()]
    preferred = [
        s.strip()
        for s in filter_obj.preferred_criteria
        if s and s.strip()
    ]
    keywords = [s.strip() for s in filter_obj.keywords if s and s.strip()]

    return filter_obj.model_copy(
        update={
            "locations_country": countries,
            "locations_region": regions,
            "skills_all": skills_all,
            "skills_any": skills_any,
            "titles_all": titles_all,
            "titles_any": titles_any,
            "soft_criteria": soft,
            "preferred_criteria": preferred,
            "keywords": keywords,
            "free_text": (filter_obj.free_text or query).strip(),
            "parse_degraded": False,
        }
    )


def _fallback_filter(query: str) -> ParsedFilter:
    """Last-resort filter when parsing fails. Keywords-only."""
    cleaned = (query or "").strip()
    return ParsedFilter(
        keywords=[cleaned] if cleaned else [],
        free_text=cleaned,
        parse_degraded=True,
    )


def _resolve_anthropic_client(*, organization_id: int | None = None):
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client(organization_id=organization_id)


def parse_nl_query(
    query: str,
    *,
    client=None,
    organization_id: int | None = None,
    role_id: int | None = None,
    metering: dict | None = None,
) -> ParsedFilter:
    """Parse one NL query. Never raises; returns a best-effort ``ParsedFilter``.

    Paid parsing requires an organization so it can be hard-admitted before
    the SDK call. ``role_id`` adds the role's monthly ceiling to that admission;
    leaving it unset is an intentional workspace-level search.
    """
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return ParsedFilter(free_text="")

    # Common structured searches are deterministic and free. Be conservative:
    # ambiguous prose returns None and continues to the model parser below.
    from .deterministic_parser import parse_common_query

    deterministic = parse_common_query(cleaned_query)
    if deterministic is not None:
        return deterministic

    system_prompt, user_prompt = build_parser_prompt(cleaned_query)

    base_metering = dict(metering or {})
    meter_org_id = organization_id
    if meter_org_id is None:
        try:
            meter_org_id = int(base_metering["organization_id"])
        except (KeyError, TypeError, ValueError):
            meter_org_id = None
    meter_role_id = role_id
    if meter_role_id is None and base_metering.get("role_id") is not None:
        try:
            meter_role_id = int(base_metering["role_id"])
        except (TypeError, ValueError):
            meter_role_id = None

    if client is None:
        try:
            client = _resolve_anthropic_client(
                organization_id=meter_org_id,
            )
        except Exception as exc:
            logger.warning("Parser client init failed: %s", exc)
            return _fallback_filter(cleaned_query)

    # A candidate-search parse without org attribution cannot be safely billed.
    # Degrade to deterministic keyword search instead of making an unadmitted
    # paid call through a shared/unscoped client.
    if meter_org_id is None:
        logger.warning("Parser skipped paid call: organization_id is required")
        return _fallback_filter(cleaned_query)

    try:
        call_metering = admitted_search_metering(
            organization_id=int(meter_org_id),
            role_id=meter_role_id,
            feature=Feature.SEARCH_PARSE,
            entity_id=(
                str(base_metering["entity_id"])
                if base_metering.get("entity_id") is not None
                else None
            ),
            sub_feature="candidate_search_parse",
            trace_id=(
                str(base_metering["trace_id"])
                if base_metering.get("trace_id")
                else None
            ),
            base_metering=base_metering,
        )
    except Exception as exc:
        logger.warning("Parser blocked by usage admission: %s", exc)
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
        model=PARSER_MODEL,
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
        output_model=ParsedFilter,
        metering=MeteringContext.from_dict(
            call_metering, default_feature=Feature.SEARCH_PARSE
        ),
        max_tokens=PARSER_MAX_TOKENS,
        temperature=PARSER_TEMPERATURE,
        max_retries=0,
        use_tool_use=True,
    )
    if not result.ok or result.value is None:
        logger.warning("Parser failed (%s); falling back to keywords", result.error_reason)
        return _fallback_filter(cleaned_query)

    return _normalise(result.value, cleaned_query)
