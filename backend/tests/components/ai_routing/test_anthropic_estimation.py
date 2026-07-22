from __future__ import annotations

from app.components.ai_routing.anthropic_estimation import (
    conservative_raw_cost_micro_usd,
    estimate_anthropic_messages,
)
from app.components.ai_routing.contracts import InputCostBasis, TaskKey
from app.components.ai_routing.model_registry import (
    ANTHROPIC_SONNET_4_6,
    DEFAULT_MODEL_REGISTRY,
)
from app.components.ai_routing.runtime import plan_route
from app.candidate_search.parser import PARSER_MAX_TOKENS, build_parser_prompt
from app.candidate_search.schemas import ParsedFilter
from app.llm.structured import structured_tool_params


def test_estimator_detects_default_and_one_hour_cache_writes() -> None:
    standard = estimate_anthropic_messages(
        messages=[{"role": "user", "content": "hello"}], max_tokens=10
    )
    five_minutes = estimate_anthropic_messages(
        system=[
            {
                "type": "text",
                "text": "cached",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[],
        max_tokens=10,
    )
    one_hour = estimate_anthropic_messages(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "cached",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            }
        ],
        max_tokens=10,
    )

    assert standard.input_cost_basis is InputCostBasis.STANDARD
    assert five_minutes.input_cost_basis is InputCostBasis.CACHE_WRITE_5M
    assert one_hour.input_cost_basis is InputCostBasis.CACHE_WRITE_1H


def test_cache_write_cost_uses_worst_applicable_rate() -> None:
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None

    standard = conservative_raw_cost_micro_usd(
        deployment, input_tokens=100, output_tokens=0
    )
    five_minutes = conservative_raw_cost_micro_usd(
        deployment,
        input_tokens=100,
        output_tokens=0,
        input_cost_basis=InputCostBasis.CACHE_WRITE_5M,
    )
    one_hour = conservative_raw_cost_micro_usd(
        deployment,
        input_tokens=100,
        output_tokens=0,
        input_cost_basis=InputCostBasis.CACHE_WRITE_1H,
    )

    assert (standard, five_minutes, one_hour) == (300, 375, 600)


def test_real_parser_request_fits_profile_and_has_nonzero_planning_cost() -> None:
    system_prompt, user_prompt = build_parser_prompt(
        "senior data engineers with banking experience in Dubai"
    )
    tools, tool_choice, _ = structured_tool_params(ParsedFilter)
    estimate = estimate_anthropic_messages(
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        tools=tools,
        tool_choice=tool_choice,
        max_tokens=PARSER_MAX_TOKENS,
    )

    decision = plan_route(
        TaskKey.SEARCH_PARSE,
        estimated_input_tokens=estimate.input_tokens,
        estimated_output_tokens=estimate.output_tokens,
        estimated_input_cost_basis=estimate.input_cost_basis,
        environ={},
    )

    assert 8_000 < estimate.input_tokens <= decision.limits.max_input_tokens
    assert decision.attempts[0].expected_cost_micro_usd > 0
    assert decision.selected_deployment_id == ANTHROPIC_SONNET_4_6
