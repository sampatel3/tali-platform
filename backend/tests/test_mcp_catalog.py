"""Contract tests for the shared MCP / Taali Chat tool catalogue."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

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


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_MCP_DOCS = (
    _REPO_ROOT / "backend" / "docs" / "MCP_SERVER.md",
    _REPO_ROOT / "docs" / "API.md",
)
_PUBLIC_TOOL_TABLE_START = "<!-- public-mcp-tools:start -->"
_PUBLIC_TOOL_TABLE_END = "<!-- public-mcp-tools:end -->"


def _mcp_jwt_fallback_script() -> str:
    text = _PUBLIC_MCP_DOCS[0].read_text(encoding="utf-8")
    section = text.split("### Short-lived JWT fallback", 1)[1]
    match = re.search(r"```bash\n(.*?)\n```", section, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def _documented_public_tools(
    path: Path,
) -> dict[str, tuple[frozenset[str], str, str]]:
    text = path.read_text(encoding="utf-8")
    assert text.count(_PUBLIC_TOOL_TABLE_START) == 1, path
    assert text.count(_PUBLIC_TOOL_TABLE_END) == 1, path
    section = text.split(_PUBLIC_TOOL_TABLE_START, 1)[1].split(
        _PUBLIC_TOOL_TABLE_END, 1
    )[0]

    documented: dict[str, tuple[frozenset[str], str, str]] = {}
    for line in section.splitlines():
        match = re.match(
            r"^\|\s*`([^`]+)`\s*\|\s*([^|]+)\|\s*`(free|paid)`\s*\|\s*([^|]+)\|",
            line,
        )
        if match is None:
            continue
        name, scope_cell, cost, purpose = match.groups()
        assert name not in documented, f"duplicate {name!r} in {path}"
        documented[name] = (
            frozenset(re.findall(r"`([a-z-]+:read)`", scope_cell)),
            cost,
            purpose.strip(),
        )
    return documented


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
        "get_application",
        "get_candidate",
        "compare_applications",
        "nl_search_candidates",
        "graph_search_candidates",
        "get_candidate_cv",
        "get_recruiting_overview",
        "list_assessments",
    }


@pytest.mark.parametrize(
    "doc_path",
    _PUBLIC_MCP_DOCS,
    ids=("mcp-server-guide", "api-reference"),
)
def test_public_mcp_documented_names_scopes_and_costs_match_catalog(doc_path: Path):
    expected = {
        spec.name: (spec.required_scopes, spec.cost)
        for spec in tools_for(PUBLIC_MCP)
    }
    documented = _documented_public_tools(doc_path)

    assert {
        name: (scopes, cost)
        for name, (scopes, cost, _purpose) in documented.items()
    } == expected


@pytest.mark.parametrize(
    "doc_path",
    _PUBLIC_MCP_DOCS,
    ids=("mcp-server-guide", "api-reference"),
)
def test_nl_search_credit_disclosure_matches_catalog_and_docs(doc_path: Path):
    description = get_tool_spec("nl_search_candidates").description
    purpose = _documented_public_tools(doc_path)["nl_search_candidates"][2]

    for text in (description, purpose):
        lowered = text.lower()
        assert "organization credits" in lowered
        assert "sonnet" in lowered
        assert "bounded" in lowered
        assert "verification" in lowered
        assert "deterministic" in lowered
        assert "cached" in lowered
        assert "free" in lowered


@pytest.mark.parametrize(
    "doc_path",
    _PUBLIC_MCP_DOCS,
    ids=("mcp-server-guide", "api-reference"),
)
def test_public_mcp_documented_resources_name_their_required_scope(
    doc_path: Path,
):
    text = doc_path.read_text(encoding="utf-8")
    expected = {
        "tali://role/{role_id}": SCOPE_ROLES_READ,
        "tali://application/{application_id}": SCOPE_APPLICATIONS_READ,
        "tali://candidate/{candidate_id}/cv": SCOPE_APPLICATIONS_READ,
    }

    for template, scope in expected.items():
        assert re.search(
            rf"`{re.escape(template)}`[^\n]*`{re.escape(scope)}`",
            text,
        ), f"{doc_path}: {template} must document {scope}"


def test_mcp_jwt_fallback_keeps_credentials_out_of_process_arguments_and_env():
    text = _PUBLIC_MCP_DOCS[0].read_text(encoding="utf-8")

    for unsafe_fragment in (
        "TALI_TOKEN=$(",
        'password=$TALI_PASSWORD',
        'username=$TALI_EMAIL',
        "jq -r .access_token",
        "export TALI_API TALI_EMAIL",
        'os.environ["TALI_EMAIL"]',
        'os.environ["TALI_PASSWORD"]',
        "trap cleanup EXIT HUP INT TERM",
    ):
        assert unsafe_fragment not in text

    for required_fragment in (
        "umask 077",
        "set +x",
        "email.raw",
        "password.raw",
        "login.form",
        "login.headers",
        "login.curl",
        "login.response.json",
        "0o600",
        "chmod 600",
        "connect-timeout = 5",
        "max-time = 20",
        'local_http_hosts = {"localhost", "127.0.0.1", "::1"}',
        'curl --config "$AUTH_DIR/login.curl"',
        "trap cleanup EXIT",
        "trap 'exit 129' HUP",
        "trap 'exit 130' INT",
        "trap 'exit 143' TERM",
    ):
        assert required_fragment in text


def test_mcp_jwt_fallback_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n"],
        input=_mcp_jwt_fallback_script(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _write_fake_mcp_curl(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import stat
            import sys
            from pathlib import Path
            from urllib.parse import parse_qs

            if sys.argv[1:2] != ["--config"] or len(sys.argv) != 3:
                raise SystemExit("curl must receive only --config and its path")
            config_path = Path(sys.argv[2])
            config_text = config_path.read_text(encoding="utf-8")
            entries = {{}}
            for raw_line in config_text.splitlines():
                if " = " not in raw_line:
                    continue
                key, raw_value = raw_line.split(" = ", 1)
                try:
                    entries[key] = json.loads(raw_value)
                except json.JSONDecodeError:
                    entries[key] = raw_value

            form_path = Path(entries["data"].removeprefix("@"))
            header_path = Path(entries["header"].removeprefix("@"))
            response_path = Path(entries["output"])
            credentials = parse_qs(
                form_path.read_text(encoding="utf-8"),
                keep_blank_values=True,
            )
            sensitive_values = [
                *credentials.get("username", []),
                *credentials.get("password", []),
            ]
            mode_paths = {{
                "auth_dir": config_path.parent,
                "form": form_path,
                "headers": header_path,
                "response": response_path,
                "config": config_path,
            }}
            response_path.write_text(
                json.dumps({{"access_token": "runtime-jwt-secret"}}),
                encoding="utf-8",
            )
            response_path.chmod(0o600)
            report = {{
                "argv": sys.argv[1:],
                "sensitive_env_present": sorted(
                    key
                    for key in (
                        "TALI_EMAIL",
                        "TALI_PASSWORD",
                        "TALI_TOKEN",
                        "TALI_API_KEY",
                    )
                    if key in os.environ
                ),
                "modes": {{
                    name: stat.S_IMODE(target.stat().st_mode)
                    for name, target in mode_paths.items()
                }},
                "credentials_received": (
                    len(credentials.get("username", [])) == 1
                    and len(credentials.get("password", [])) == 1
                ),
                "config_contains_credentials": any(
                    value and value in config_text for value in sensitive_values
                ),
                "auth_dir": str(config_path.parent),
                "jwt_path": str(config_path.parent / "jwt.headers.json"),
            }}
            Path(os.environ["MCP_FAKE_CURL_REPORT"]).write_text(
                json.dumps(report),
                encoding="utf-8",
            )
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def test_mcp_jwt_fallback_runtime_hides_secrets_and_cleans_private_files(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_mcp_curl(fake_bin / "curl")
    auth_tmp = tmp_path / "auth-tmp"
    auth_tmp.mkdir()
    report_path = tmp_path / "curl-report.json"
    email = "mcp-runtime@example.test"
    password = "runtime password &= secret"
    token = "runtime-jwt-secret"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "TMPDIR": str(auth_tmp),
        "TALI_API": "http://127.0.0.1:8000",
        "MCP_FAKE_CURL_REPORT": str(report_path),
    }

    result = subprocess.run(
        ["bash", "-c", _mcp_jwt_fallback_script()],
        input=f"{email}\n{password}\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    combined_output = result.stdout + result.stderr
    assert email not in combined_output
    assert password not in combined_output
    assert token not in combined_output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["sensitive_env_present"] == []
    assert report["credentials_received"] is True
    assert report["config_contains_credentials"] is False
    assert report["argv"] == ["--config", f"{report['auth_dir']}/login.curl"]
    assert report["modes"] == {
        "auth_dir": 0o700,
        "form": 0o600,
        "headers": 0o600,
        "response": 0o600,
        "config": 0o600,
    }
    assert not Path(report["auth_dir"]).exists()
    assert not Path(report["jwt_path"]).exists()
    assert list(auth_tmp.iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_api",
    (
        "http://example.test",
        "http://localhost.example.test",
        "http://127.0.0.2:8000",
        "ftp://example.test",
    ),
)
def test_mcp_jwt_fallback_rejects_unsafe_api_before_curl(
    tmp_path: Path,
    unsafe_api: str,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "curl-was-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        f"#!/bin/sh\n: > {str(marker)!r}\nexit 99\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o700)
    auth_tmp = tmp_path / "auth-tmp"
    auth_tmp.mkdir()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "TMPDIR": str(auth_tmp),
        "TALI_API": unsafe_api,
    }

    result = subprocess.run(
        ["bash", "-c", _mcp_jwt_fallback_script()],
        input="",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "HTTPS" in result.stderr
    assert unsafe_api not in result.stdout + result.stderr
    assert list(auth_tmp.iterdir()) == []


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


@pytest.mark.parametrize(
    ("name", "field", "maximum"),
    [
        ("search_applications", "q", 500),
        ("nl_search_candidates", "query", 2_000),
        ("graph_search_candidates", "query", 500),
        ("find_top_candidates", "query", 2_000),
        ("screen_pool_against_requirement", "requirement_text", 2_000),
    ],
)
def test_search_text_contracts_reject_oversize_without_truncation(
    name: str,
    field: str,
    maximum: int,
):
    spec = get_tool_spec(name)
    accepted = spec.validate({field: "x" * maximum})
    assert accepted[field] == "x" * maximum
    field_schema = spec.input_schema["properties"][field]
    string_schema = next(
        (
            candidate
            for candidate in field_schema.get("anyOf", [field_schema])
            if candidate.get("type") == "string"
        ),
        None,
    )
    assert string_schema is not None
    assert string_schema["maxLength"] == maximum

    with pytest.raises(ValueError, match=f"invalid arguments for {name}"):
        spec.validate({field: "x" * (maximum + 1)})
    with pytest.raises(ValueError, match=f"invalid arguments for {name}"):
        spec.validate({field: "   "})


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


def test_candidate_report_contracts_are_explicit_chat_only_writes():
    top = get_tool_spec("create_top_candidates_report")
    screen = get_tool_spec("create_screen_pool_report")

    for spec in (top, screen):
        assert spec.exposures == frozenset({TAALI_CHAT})
        assert spec.effect == "external_write"
        assert spec.cost == "paid"
        assert spec.confirmation == "explicit"
        assert spec.persistence == "sensitive"
        assert spec.required_scopes == frozenset(
            {SCOPE_ROLES_READ, SCOPE_APPLICATIONS_READ}
        )

    assert top.validate(
        {"role_id": 1, "query": "banking", "limit": 5}
    ) == {"role_id": 1, "query": "banking", "limit": 5}
    assert screen.validate(
        {"role_id": 1, "requirement_text": "payments", "deep_verify": True}
    ) == {
        "role_id": 1,
        "requirement_text": "payments",
        "deep_verify": True,
    }
    with pytest.raises(ValueError, match="invalid arguments"):
        top.validate({"query": "missing role"})
    with pytest.raises(ValueError, match="invalid arguments"):
        screen.validate({"role_id": 1, "requirement_text": "x" * 2001})


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
