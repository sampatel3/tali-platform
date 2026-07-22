"""Contract tests for the shared MCP / Taali Chat tool catalogue."""

from __future__ import annotations

import asyncio

import pytest

from app.mcp.catalog import (
    PUBLIC_MCP,
    TAALI_CHAT,
    TOOL_SPECS,
    get_tool_spec,
    tools_for,
)
from app.models.api_key import (
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
    SCOPE_ROLES_READ,
)
from app.taali_chat.tool_registry import TAALI_CHAT_SPECS, TAALI_CHAT_TOOLS


def test_catalog_names_are_unique_and_chat_is_generated_from_catalog():
    names = [spec.name for spec in TOOL_SPECS]
    assert len(names) == len(set(names))
    assert [spec.name for spec in TAALI_CHAT_SPECS] == [
        spec.name for spec in tools_for(TAALI_CHAT)
    ]
    assert [tool["name"] for tool in TAALI_CHAT_TOOLS] == [
        spec.name for spec in tools_for(TAALI_CHAT)
    ]


def test_chat_handler_resolution_has_exact_catalog_parity():
    from app.taali_chat.tool_registry import _HANDLER_BY_NAME

    assert set(_HANDLER_BY_NAME) == {
        spec.name for spec in tools_for(TAALI_CHAT)
    }
    assert all(callable(handler) for handler in _HANDLER_BY_NAME.values())


def test_public_mcp_is_an_explicit_catalog_subset():
    assert {spec.name for spec in tools_for(PUBLIC_MCP)} == {
        "list_roles",
        "get_role",
        "search_applications",
        "search_role_candidates",
        "get_application",
        "get_role_candidate",
        "get_candidate",
        "compare_applications",
        "nl_search_candidates",
        "graph_search_candidates",
        "get_candidate_cv",
        "list_recent_agent_decisions",
        "list_candidate_actions",
        "get_recruiting_overview",
        "list_assessments",
    }


def test_search_contract_supports_real_pipeline_and_pagination():
    args = get_tool_spec("search_applications").validate(
        {"pipeline_stage": "sourced", "offset": 25, "limit": 25}
    )
    assert args["pipeline_stage"] == "sourced"
    assert args["offset"] == 25

    args = get_tool_spec("search_applications").validate(
        {"pipeline_stage": "advanced"}
    )
    assert args["pipeline_stage"] == "advanced"

    args = get_tool_spec("search_applications").validate(
        {"score_type": "assessment", "sort_by": "assessment_score"}
    )
    assert args == {"score_type": "assessment", "sort_by": "assessment_score"}

    graph_args = get_tool_spec("graph_search_candidates").validate(
        {"query": "worked at Stripe", "role_id": 42, "limit": 10}
    )
    assert graph_args == {"query": "worked at Stripe", "role_id": 42, "limit": 10}


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_applications", {"pipeline_stage": "made_up"}),
        ("search_applications", {"offset": -1}),
        ("search_applications", {"surprise": True}),
        ("compare_applications", {"application_ids": [1]}),
        ("compare_applications", {"application_ids": [1, 2, 3, 4, 5, 6]}),
    ],
)
def test_model_generated_arguments_are_rejected_before_dispatch(name, arguments):
    with pytest.raises(ValueError, match=f"invalid arguments for {name}"):
        get_tool_spec(name).validate(arguments)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_role", {"role_id": True}),
        ("get_role", {"role_id": "1"}),
        ("search_applications", {"min_score": "70"}),
        ("search_applications", {"limit": "25"}),
        ("search_applications", {"offset": False}),
        ("compare_applications", {"application_ids": [1, "2"]}),
        ("compare_applications", {"application_ids": [1, True]}),
    ],
)
def test_numeric_contracts_do_not_coerce_strings_or_booleans(name, arguments):
    with pytest.raises(ValueError, match=f"invalid arguments for {name}"):
        get_tool_spec(name).validate(arguments)


def test_empty_non_object_arguments_are_not_treated_as_an_empty_object():
    with pytest.raises(ValueError, match="expected an object"):
        get_tool_spec("list_roles").validate([])  # type: ignore[arg-type]


def test_taali_dispatch_rejects_extra_fields_before_handler_execution():
    from app.taali_chat.tool_registry import dispatch_tool

    with pytest.raises(ValueError, match="surprise"):
        dispatch_tool(
            "list_roles",
            {"surprise": True},
            db=None,
            user=None,
        )


def test_sensitive_source_tool_has_nonstandard_persistence_policy():
    assert get_tool_spec("get_candidate_cv").persistence == "sensitive"
    assert get_tool_spec("get_application").persistence == "sensitive"
    grounded = get_tool_spec("find_top_candidates")
    assert grounded.effect == "read"
    assert grounded.cost == "paid"
    assert grounded.persistence == "sensitive"
    assert get_tool_spec("nl_search_candidates").cost == "paid"
    assert get_tool_spec("graph_search_candidates").cost == "paid"


def test_related_role_contracts_are_chat_only_and_describe_the_paid_mutation():
    preview = get_tool_spec("preview_related_role")
    create = get_tool_spec("create_related_role")

    assert preview.exposures == frozenset({TAALI_CHAT})
    assert preview.effect == "read"
    assert preview.input_schema["properties"]["job_spec_text"]["minLength"] == 80
    assert create.exposures == frozenset({TAALI_CHAT})
    assert create.effect == "internal_write"
    assert create.cost == "paid"
    assert create.confirmation == "explicit"
    assert create.execution == "queued"


@pytest.mark.parametrize(
    "arguments",
    [
        {"role_id": "1", "name": "Platform role", "job_spec_text": "x" * 80},
        {"role_id": 1, "name": " ", "job_spec_text": "x" * 80},
        {"role_id": 1, "name": "Platform role", "job_spec_text": "x" * 79},
        {
            "role_id": 1,
            "name": "Platform role",
            "job_spec_text": "x" * 80,
            "surprise": True,
        },
    ],
)
def test_related_role_preview_uses_the_strict_canonical_contract(arguments):
    with pytest.raises(ValueError, match="invalid arguments for preview_related_role"):
        get_tool_spec("preview_related_role").validate(arguments)


def test_catalog_declares_domain_specific_and_aggregate_read_scopes():
    assert get_tool_spec("list_assessments").required_scopes == frozenset(
        {SCOPE_ASSESSMENTS_READ}
    )
    assert get_tool_spec("get_recruiting_overview").required_scopes == frozenset(
        {SCOPE_ROLES_READ, SCOPE_APPLICATIONS_READ, SCOPE_ASSESSMENTS_READ}
    )


def test_anthropic_schema_is_derived_from_the_typed_model():
    definition = get_tool_spec("compare_applications").anthropic_definition()
    ids = definition["input_schema"]["properties"]["application_ids"]
    assert ids["minItems"] == 2
    assert ids["maxItems"] == 5
    assert "title" not in definition["input_schema"]


def _semantic_contract(value):
    """Keep transport-relevant schema semantics, dropping display metadata."""

    keys = {
        "type",
        "properties",
        "required",
        "items",
        "anyOf",
        "enum",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "properties" and isinstance(item, dict):
                result[key] = {
                    name: _semantic_contract(schema)
                    for name, schema in item.items()
                }
            elif key in keys:
                result[key] = _semantic_contract(item)
        return result
    if isinstance(value, list):
        return [_semantic_contract(item) for item in value]
    return value


def test_fastmcp_adapters_advertise_catalog_descriptions_and_constraints():
    from app.mcp.server import mcp_app

    advertised = {tool.name: tool for tool in asyncio.run(mcp_app.list_tools())}
    for spec in tools_for(PUBLIC_MCP):
        tool = advertised[spec.name]
        assert tool.description == spec.description
        assert _semantic_contract(tool.inputSchema) == _semantic_contract(
            spec.input_schema
        )
