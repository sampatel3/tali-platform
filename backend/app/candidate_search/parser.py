"""NL query → ``ParsedFilter``.

One Claude call per uncached query (``PARSER_MODEL``, Sonnet by default — the
extraction makes subtle judgement calls a smaller model gets wrong). On any
failure we degrade to a keyword-only filter so the user still gets best-effort
ILIKE matches.
"""

from __future__ import annotations

import logging

from ..llm import MeteringContext, generate_structured, structured_tool_params
from ..llm.models import SONNET_MODEL
from ..platform.config import settings
from ..services.claude_model_pricing import require_priceable_claude_model
from ..services.pricing_service import Feature
from ..services.provider_error_evidence import (
    safe_provider_error_code,
    safe_structured_error_code,
)
from .input_contracts import bounded_candidate_search_query
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
# Parse on Sonnet rather than the general fast-model default: the
# NL→filter extraction makes subtle judgement calls (is "a Western company" the
# candidate's location or the employer's origin? is "salary <= 30k" one
# constraint?) where the stronger default materially improves correctness. It
# remains one bounded call per ambiguous, uncached query and is env-overridable.
PARSER_MODEL = (settings.CLAUDE_SEARCH_PARSER_MODEL or "").strip() or SONNET_MODEL


def _normalise(filter_obj: ParsedFilter, query: str) -> ParsedFilter:
    """Server-side cleanup applied AFTER schema validation.

    Defensive: even if the configured parser misses an alias, normalise here.
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
    organization_id: int | None = None,
    role_id: int | None = None,
    metering: dict | None = None,
) -> ParsedFilter:
    """Parse one bounded NL query; provider failures return a best-effort filter.

    Paid parsing requires an organization so it can be hard-admitted before
    the SDK call. ``role_id`` adds the role's monthly ceiling to that admission;
    leaving it unset is an intentional workspace-level search.
    """
    cleaned_query = bounded_candidate_search_query(query, allow_empty=True)
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
            logger.warning(
                "Parser client init failed error_code=%s",
                safe_provider_error_code(exc, operation="candidate_search_parser_client"),
            )
            return _fallback_filter(cleaned_query)

    # A candidate-search parse without org attribution cannot be safely billed.
    # Degrade to deterministic keyword search instead of making an unadmitted
    # paid call through a shared/unscoped client.
    if meter_org_id is None:
        logger.warning("Parser skipped paid call: organization_id is required")
        return _fallback_filter(cleaned_query)

    # Build the exact provider request before admission so its immutable hold
    # cannot be replayed for a different prompt, schema, model, or token cap.
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    parser_messages = [{"role": "user", "content": user_prompt}]
    tools, tool_choice, _ = structured_tool_params(ParsedFilter)
    provider_request = {
        "model": PARSER_MODEL,
        "max_tokens": PARSER_MAX_TOKENS,
        "temperature": PARSER_TEMPERATURE,
        "messages": parser_messages,
        "system": system_blocks,
        "tools": tools,
        "tool_choice": tool_choice,
    }
    try:
        require_priceable_claude_model(PARSER_MODEL)
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
            provider_request=provider_request,
        )
    except Exception as exc:
        logger.warning(
            "Parser blocked by usage admission error_code=%s",
            safe_provider_error_code(exc, operation="candidate_search_parser_admission"),
        )
        return _fallback_filter(cleaned_query)

    # No retry: the parser fast-fails to a keyword-only filter on any
    # call / parse / schema failure, so the user still gets ILIKE matches.
    # Forced tool-use: the model emits ParsedFilter as the tool's ``.input``
    # dict — one schema source, no JSON repair.
    result = generate_structured(
        client,
        model=PARSER_MODEL,
        system=system_blocks,
        messages=parser_messages,
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
        logger.warning(
            "Parser failed error_code=%s; falling back to keywords",
            safe_structured_error_code(
                result.error_reason,
                operation="candidate_search_parser",
            ),
        )
        return _fallback_filter(cleaned_query)

    return _normalise(result.value, cleaned_query)
