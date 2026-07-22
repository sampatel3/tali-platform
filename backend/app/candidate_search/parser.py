"""NL query → ``ParsedFilter``.

One routed model call per uncached ambiguous query. On any failure we degrade
to a keyword-only filter so the user still gets best-effort ILIKE matches.
"""

from __future__ import annotations

import logging

from ..components.ai_routing import (
    RoutingAttribution,
    TaskKey,
    estimate_anthropic_messages,
    prepare_route,
    routed_messages_client,
)
from ..llm import MeteringContext, generate_structured
from ..llm.structured import structured_tool_params
from ..services.pricing_service import Feature
from .metering import search_metering
from .prompts import (
    build_parser_prompt,
    expand_region,
    normalise_country,
)
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.parser")

PARSER_MAX_TOKENS = 512
PARSER_TEMPERATURE = 0.0


class ProviderCallsForbiddenError(ValueError):
    """The requested search cannot run under a zero-provider policy."""


def _normalise(filter_obj: ParsedFilter, query: str) -> ParsedFilter:
    """Server-side cleanup applied AFTER schema validation.

    Defensive: even if the selected model misses an alias, normalise it here.
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
    preferred = [s.strip() for s in filter_obj.preferred_criteria if s and s.strip()]
    keywords = [s.strip() for s in filter_obj.keywords if s and s.strip()]

    normalized = filter_obj.model_copy(
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
    # A schema-valid tool response can still carry no executable meaning.  Do
    # not let that shape become an unfiltered, apparently exhaustive search of
    # the whole organization; retain the query as an explicitly degraded
    # lexical fallback instead.
    if normalized.is_empty() and query.strip():
        return _fallback_filter(query)
    return normalized


def _fallback_filter(query: str) -> ParsedFilter:
    """Last-resort filter when parsing fails. Keywords-only."""
    cleaned = (query or "").strip()
    return ParsedFilter(
        keywords=[cleaned] if cleaned else [],
        free_text=cleaned,
        parse_degraded=True,
    )


def _metering_int(metering: dict, key: str) -> int | None:
    try:
        return int(metering[key])
    except (KeyError, TypeError, ValueError):
        return None


def parse_nl_query(
    query: str,
    *,
    route_client_factory=None,
    organization_id: int | None = None,
    role_id: int | None = None,
    metering: dict | None = None,
    require_role_authority: bool = False,
    provider_mode: str = "auto",
) -> ParsedFilter:
    """Parse one NL query, failing closed when providers are forbidden.

    Paid parsing requires an organization so it can be hard-admitted before
    the SDK call. ``role_id`` adds the role's monthly ceiling to that admission;
    leaving it unset is an intentional workspace-level search.
    """
    if provider_mode not in {"auto", "forbid"}:
        raise ValueError("provider_mode must be 'auto' or 'forbid'")
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return ParsedFilter(free_text="")

    # Common structured searches are deterministic and free. Be conservative:
    # ambiguous prose returns None and continues to the model parser below.
    from .deterministic_parser import parse_common_query

    deterministic = parse_common_query(cleaned_query)
    if deterministic is not None:
        return deterministic

    if provider_mode == "forbid":
        # This check deliberately happens before prompt construction, client
        # resolution, metering admission, or any SDK access. Production
        # canaries can therefore exercise the real search route with a hard
        # guarantee that an ambiguous query cannot incur provider work.
        raise ProviderCallsForbiddenError(
            "This query requires the model parser and cannot run with providers forbidden."
        )
    system_prompt, user_prompt = build_parser_prompt(cleaned_query)

    base_metering = dict(metering or {})
    meter_org_id = organization_id
    if meter_org_id is None:
        meter_org_id = _metering_int(base_metering, "organization_id")
    meter_role_id = role_id
    if meter_role_id is None:
        meter_role_id = _metering_int(base_metering, "role_id")

    # A candidate-search parse without org attribution cannot be safely billed.
    # Degrade to deterministic keyword search instead of making an unadmitted
    # paid call through a shared/unscoped client.
    if meter_org_id is None:
        logger.warning("Parser skipped paid call: organization_id is required")
        return _fallback_filter(cleaned_query)

    entity_id = (
        str(base_metering["entity_id"])
        if base_metering.get("entity_id") is not None
        else None
    )
    # The same cacheable request shape is estimated before planning and sent
    # after planning. Prompt content itself is never persisted by the router.
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    structured_tools, structured_tool_choice, _ = structured_tool_params(ParsedFilter)
    request_estimate = estimate_anthropic_messages(
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
        tools=structured_tools,
        tool_choice=structured_tool_choice,
        max_tokens=PARSER_MAX_TOKENS,
    )
    try:
        execution = prepare_route(
            TaskKey.SEARCH_PARSE,
            request_estimate=request_estimate,
            attribution=RoutingAttribution(
                organization_id=int(meter_org_id),
                user_id=_metering_int(base_metering, "user_id"),
                role_id=meter_role_id,
                entity_id=entity_id,
            ),
            operation="candidate_search.parse",
            require_role_authority=bool(require_role_authority),
        )
    except Exception as exc:
        logger.warning("Parser routing failed: %s", exc)
        return _fallback_filter(cleaned_query)

    # No retry: the parser fast-fails to a keyword-only filter on any
    # call / parse / schema failure, so the user still gets ILIKE matches.
    # Forced tool-use: the model emits ParsedFilter as the tool's ``.input``
    # dict — one schema source, no JSON repair.
    workflow_succeeded = False
    try:
        try:
            call_metering = search_metering(
                organization_id=int(meter_org_id),
                role_id=meter_role_id,
                feature=Feature.SEARCH_PARSE,
                entity_id=entity_id,
                sub_feature="candidate_search_parse",
                trace_id=(
                    str(base_metering["trace_id"])
                    if base_metering.get("trace_id")
                    else None
                ),
                base_metering=base_metering,
                require_role_authority=bool(require_role_authority),
            )
        except Exception as exc:
            logger.warning("Parser blocked by usage admission: %s", exc)
            return _fallback_filter(cleaned_query)

        client_factory = route_client_factory or routed_messages_client
        try:
            result = generate_structured(
                client_factory(execution),
                model=execution.selected_model_id,
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
        except Exception as exc:
            logger.warning("Parser provider call failed: %s", exc)
            return _fallback_filter(cleaned_query)
        if not result.ok or result.value is None:
            logger.warning(
                "Parser failed (%s); falling back to keywords",
                result.error_reason,
            )
            return _fallback_filter(cleaned_query)

        parsed = _normalise(result.value, cleaned_query)
        workflow_succeeded = not parsed.parse_degraded
        return parsed
    finally:
        execution.finish_workflow(succeeded=workflow_succeeded)
