"""Supply-chain and cost regressions for repository CI workflows."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import runpy

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
PRODUCTION_SMOKE_WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "production-smoke.yml"
)
PRODUCTION_SMOKE_TEST = (
    Path(__file__).resolve().parent / "test_qa_production_smoke.py"
)
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
FRONTEND_PACKAGE = Path(__file__).resolve().parents[2] / "frontend" / "package.json"
FRONTEND_PACKAGE_LOCK = (
    Path(__file__).resolve().parents[2] / "frontend" / "package-lock.json"
)

BUILT_SITE_PUBLIC_ROUTES = (
    "/",
    "/login",
    "/register",
    "/forgot-password",
    "/developers",
    "/demo",
    "/demo-lead",
    "/showcase",
    "/showcase/jobs",
    "/blog",
    "/blog/ai-native-coding-and-knowledge-work",
    "/terms",
    "/privacy",
    "/outreach/thanks",
)

PRODUCTION_SMOKE_NETWORK_SURFACE = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/admin/health"),
        ("GET", "/admin/health/graphiti"),
        ("GET", "/healthz/graphiti"),
        ("GET", "/admin/health/github"),
        ("GET", "/healthz/github"),
        ("GET", "/api/v1/users/me"),
        ("GET", "/api/v1/assessments/"),
        ("GET", "/api/v1/candidates/"),
        ("GET", "/api/v1/analytics/"),
        ("GET", "/api/v1/billing/usage"),
        ("GET", "/api/v1/organizations/me"),
        ("GET", "/public/v1/roles"),
        ("GET", "/api/docs"),
        ("GET", "/api/openapi.json"),
        ("OPTIONS", "/health"),
    }
)
PRODUCTION_SMOKE_IMPORTS = (
    ("from", "__future__", "annotations", None),
    ("import", None, "os", None),
    ("import", None, "pytest", None),
    ("import", None, "requests", None),
)
_FORBIDDEN_TRANSPORT_NAMES = frozenset(
    {
        "aiohttp",
        "http",
        "httpx",
        "socket",
        "subprocess",
        "urllib",
        "urllib3",
    }
)
_FORBIDDEN_DYNAMIC_CALLS = frozenset(
    {"__import__", "compile", "eval", "exec"}
)

IMMUTABLE_COMPOSE_IMAGES = {
    "postgres": (
        "postgres:16.14@sha256:"
        "17e67d7b9890c99b055ba1e0d5c5be4ec27c9d3a72bda32db24a5e5d8a85af0c"
    ),
    "redis": (
        "redis:7.4.9-alpine@sha256:"
        "6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
    ),
}


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_workflow_parses_and_pins_external_execution_inputs() -> None:
    source = _source()
    assert isinstance(yaml.safe_load(source), dict)
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", source, flags=re.MULTILINE)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", value) for value in uses), uses
    assert "ubuntu-latest" not in source
    assert "postgres:16.14@sha256:" in source
    assert "node-version: '22.23.1'" in source


def _parse_bounded_node_engine(engine: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    match = re.fullmatch(
        r"\s*>=\s*(\d+)\.(\d+)\.(\d+)\s+<\s*(\d+)(?:\.0\.0)?\s*",
        engine,
    )
    assert match is not None, "Node engine must have an inclusive floor and exclusive major cap"
    lower = tuple(int(part) for part in match.groups()[:3])
    upper = (int(match.group(4)), 0, 0)
    return lower, upper


def test_frontend_host_runtime_enforces_vite_floor_and_ci_node_major() -> None:
    package = json.loads(FRONTEND_PACKAGE.read_text(encoding="utf-8"))
    package_lock = json.loads(FRONTEND_PACKAGE_LOCK.read_text(encoding="utf-8"))
    engine = package["engines"]["node"]

    # The lower bound is Vite 8's Node 22 floor. The exclusive upper bound
    # prevents a host from selecting Node 23+ while CI remains on Node 22, and
    # still permits compatible security/patch releases within the major.
    lower, upper = _parse_bounded_node_engine(engine)
    assert lower == (22, 12, 0)
    assert upper == (23, 0, 0)
    assert package_lock["packages"][""]["engines"]["node"] == engine

    ci_match = re.search(r"node-version: '([0-9]+\.[0-9]+\.[0-9]+)'", _source())
    assert ci_match is not None
    ci_version = tuple(int(part) for part in ci_match.group(1).split("."))
    assert lower <= ci_version < upper
    assert not (lower <= (22, 11, 99) < upper)
    assert not (lower <= (23, 0, 0) < upper)


def test_local_compose_uses_exact_digest_pinned_service_images() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    images = {
        service_name: service["image"]
        for service_name, service in compose["services"].items()
    }

    # Exact-map equality makes a mutable replacement or an unreviewed new
    # service fail closed instead of silently expanding the supply-chain edge.
    assert images == IMMUTABLE_COMPOSE_IMAGES


def test_ci_uses_fresh_hashed_python_lock_in_both_backend_jobs() -> None:
    source = _source()
    install = "python -m pip install --require-hashes -r requirements-lock.txt"
    assert source.count(install) == 2
    assert source.count("python scripts/check_requirements_lock.py") == 2
    assert "pip install --upgrade pip" not in source


def test_ci_deduplicates_branch_events_and_skips_unaffected_expensive_jobs() -> None:
    source = _source()
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]

    assert "github.head_ref || github.ref_name" in source
    assert "github.event.pull_request.head.repo.full_name || github.repository" in source
    assert "docs/*|*.md" in source

    backend = jobs["backend"]
    assert backend["needs"] == ["changes", "merge-safety"]
    assert "needs.changes.outputs.backend == 'true'" in backend["if"]
    assert "needs.changes.result == 'success'" in backend["if"]

    merge_safety = jobs["merge-safety"]
    assert merge_safety["if"] == "github.event_name == 'pull_request'"
    merge_safety_gate = next(
        step
        for step in backend["steps"]
        if step["name"] == "Require merge-safety success for pull requests"
    )
    assert merge_safety_gate["if"] == "github.event_name == 'pull_request'"
    assert merge_safety_gate["env"]["MERGE_SAFETY_RESULT"] == (
        "${{ needs.merge-safety.result }}"
    )

    postgres = jobs["postgres-contract"]
    assert postgres["needs"] == "changes"
    assert "needs.changes.outputs.backend == 'true'" in postgres["if"]

    frontend = jobs["frontend"]
    assert frontend["needs"] == "changes"
    assert "needs.changes.outputs.frontend == 'true'" in frontend["if"]


def test_frontend_ci_fails_on_test_warnings() -> None:
    source = _source()
    assert "run: npm run test:ci -- --maxWorkers=4" in source
    assert "run: npm test -- --maxWorkers=4" not in source


def test_frontend_built_site_smoke_pins_the_complete_public_route_union() -> None:
    workflow = yaml.safe_load(_source())
    smoke_step = next(
        step
        for step in workflow["jobs"]["frontend"]["steps"]
        if step["name"] == "Built assets and SPA-host fallback smoke"
    )
    route_loop = re.search(
        r"for route in (?P<routes>.*?); do",
        smoke_step["run"],
        flags=re.DOTALL,
    )
    assert route_loop is not None
    routes = tuple(route_loop.group("routes").replace("\\", " ").split())

    # Exact equality prevents both accidental shrinkage and authenticated or
    # internal application routes leaking into the unauthenticated preview.
    assert routes == BUILT_SITE_PUBLIC_ROUTES
    assert len(routes) == len(set(routes)) == 14


def test_production_smoke_uses_pinned_hashed_supported_toolchain() -> None:
    source = PRODUCTION_SMOKE_WORKFLOW.read_text(encoding="utf-8")
    assert isinstance(yaml.safe_load(source), dict)
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", source, flags=re.MULTILINE)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", value) for value in uses), uses
    assert "runs-on: ubuntu-24.04" in source
    assert "ubuntu-latest" not in source
    assert "python-version: '3.11.9'" in source
    assert source.count("python scripts/check_requirements_lock.py") == 1
    assert source.count(
        "python -m pip install --require-hashes -r requirements-lock.txt"
    ) == 1
    assert source.count("python -m pip check") == 1
    assert source.count("pip-audit --local") == 1
    assert "pip install --upgrade pip" not in source
    assert "pip install -r requirements-dev.txt" not in source
    assert "- cron: '0 */12 * * *'" in source
    assert 'run: test -n "$TALI_PROD_URL"' in source
    assert source.count(
        "TALI_PROD_URL: ${{ inputs.tali_prod_url || secrets.TALI_PROD_URL }}"
    ) == 2
    assert (
        "pytest -q -m production tests/test_qa_production_smoke.py" in source
    )


def _assert_production_smoke_transport_surface(source: str) -> None:
    tree = ast.parse(source)

    imports: list[tuple[str, str | None, str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                ("import", None, alias.name, alias.asname) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imports.extend(
                ("from", node.module, alias.name, alias.asname)
                for alias in node.names
            )
    # Walking the whole tree also rejects imports hidden inside functions. Exact
    # aliases prevent a second name from becoming an unreviewed transport.
    assert tuple(imports) == PRODUCTION_SMOKE_IMPORTS

    forbidden_names = sorted(
        {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id in _FORBIDDEN_TRANSPORT_NAMES
        }
    )
    assert forbidden_names == []

    dynamic_calls = sorted(
        {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FORBIDDEN_DYNAMIC_CALLS
        }
    )
    assert dynamic_calls == []

    allowlist_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_ALLOWED_PRODUCTION_REQUESTS"
            for target in node.targets
        )
    )
    assert isinstance(allowlist_assignment.value, ast.Call)
    assert isinstance(allowlist_assignment.value.func, ast.Name)
    assert allowlist_assignment.value.func.id == "frozenset"
    assert len(allowlist_assignment.value.args) == 1
    configured_surface = frozenset(
        ast.literal_eval(allowlist_assignment.value.args[0])
    )
    assert configured_surface == PRODUCTION_SMOKE_NETWORK_SURFACE
    assert {method for method, _path in configured_surface} == {"GET", "OPTIONS"}
    assert {
        path for method, path in configured_surface if method == "OPTIONS"
    } == {"/health"}

    requests_attributes = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "requests"
    ]
    assert sorted(requests_attributes) == ["Response", "Response", "request"]

    parent_by_child = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    bare_requests_names = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "requests"
        and not (
            isinstance(parent_by_child.get(node), ast.Attribute)
            and parent_by_child[node].value is node
        )
    ]
    assert bare_requests_names == []

    os_attributes = sorted(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )
    assert os_attributes == ["getenv", "getenv"]

    raw_transport_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
    ]
    assert len(raw_transport_calls) == 1
    assert raw_transport_calls[0].func.attr == "request"

    request_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_request"
    )
    assert raw_transport_calls[0] in list(ast.walk(request_helper))
    assert [argument.arg for argument in request_helper.args.args] == [
        "method",
        "path",
    ]
    assert [argument.arg for argument in request_helper.args.kwonlyargs] == ["headers"]
    assert request_helper.args.vararg is None
    assert request_helper.args.kwarg is None

    request_keywords = {
        keyword.arg: keyword.value for keyword in raw_transport_calls[0].keywords
    }
    assert set(request_keywords) == {
        "allow_redirects",
        "headers",
        "method",
        "timeout",
        "url",
    }
    assert isinstance(request_keywords["timeout"], ast.Constant)
    assert request_keywords["timeout"].value == 15
    assert isinstance(request_keywords["allow_redirects"], ast.Constant)
    assert request_keywords["allow_redirects"].value is False


def test_production_smoke_remains_cleanup_free_and_non_mutating() -> None:
    _assert_production_smoke_transport_surface(
        PRODUCTION_SMOKE_TEST.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "escape_source",
    [
        "import requests as transport",
        "from requests import get as transport",
        "def escape():\n    import httpx\n    return httpx.get('https://example.test')",
        "transport = requests\ntransport.get('https://example.test')",
        "transport = requests.request\ntransport(method='GET', url='https://example.test')",
        "urllib.request.urlopen('https://example.test')",
        "httpx.get('https://example.test')",
        "subprocess.run(['curl', 'https://example.test'])",
        "__import__('urllib.request').request.urlopen('https://example.test')",
    ],
)
def test_production_smoke_transport_guard_rejects_escape_paths(
    escape_source: str,
) -> None:
    source = PRODUCTION_SMOKE_TEST.read_text(encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_production_smoke_transport_surface(f"{source}\n{escape_source}\n")


def test_production_cors_executes_through_the_fixed_transport_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = runpy.run_path(str(PRODUCTION_SMOKE_TEST))
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = ""
        headers = {"access-control-allow-origin": namespace["FRONTEND_URL"]}

    def fake_request(**kwargs: object) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(namespace["requests"], "request", fake_request)
    namespace["TestProductionCors"]().test_cors_preflight_for_canonical_frontend()

    assert calls == [
        {
            "method": "OPTIONS",
            "url": f"{namespace['PROD_URL']}/health",
            "headers": {
                "Origin": namespace["FRONTEND_URL"],
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
            "timeout": 15,
            "allow_redirects": False,
        }
    ]
