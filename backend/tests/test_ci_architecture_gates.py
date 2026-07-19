from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


_ENDPOINT_DECORATOR_METHODS = {
    "api_route",
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "route",
    "trace",
    "websocket",
    "websocket_route",
}
_ENDPOINT_REGISTRATION_METHODS = {
    "add_api_route",
    "add_api_websocket_route",
    "add_route",
    "add_websocket_route",
    "mount",
}
_LEGACY_ENDPOINT_MODULES: dict[str, str] = {
    "app/main.py": "top-level liveness/readiness and legacy operator diagnostics",
    "app/agent_runtime/needs_input_routes.py": "agent-runtime review API pending domain move",
    "app/api/v1/background_jobs.py": "background-job status API pending domain move",
    "app/cv_matching/routes.py": "CV-match override and telemetry API pending domain move",
    "app/decision_policy/routes.py": "decision-policy API pending domain move",
    "app/services/threshold_calibration/routes.py": "calibration API pending domain move",
}


def _has_endpoint_decorator(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in _ENDPOINT_DECORATOR_METHODS
                ):
                    return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _ENDPOINT_REGISTRATION_METHODS
        ):
            return True
    return False


def _is_canonical_domain_route(rel: str) -> bool:
    path = Path(rel)
    parts = path.parts
    if len(parts) < 4 or parts[:2] != ("app", "domains"):
        return False
    stem = path.stem
    return stem == "routes" or stem == "router" or stem.endswith("_routes")


def test_endpoint_decorators_live_only_in_canonical_route_modules() -> None:
    # Scan the whole application. Restricting the search to known legacy
    # directories lets a new ``app/domains/foo/api.py`` silently create routes
    # outside both the ownership and file-size ratchets.
    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        if not _has_endpoint_decorator(path):
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if _is_canonical_domain_route(rel) or rel in _LEGACY_ENDPOINT_MODULES:
            continue
        violations.append(rel)

    assert not violations, (
        "Endpoint decorators must live in canonical app/domains/** route files "
        "or the exact reviewed legacy-module list. "
        f"Violations: {violations}"
    )


def test_noncanonical_domain_api_filename_cannot_evade_route_gate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app" / "domains" / "example"
    root.mkdir(parents=True)
    declarations = [
        "@router.post('/items')\ndef endpoint():\n    return {}\n",
        "@router.api_route('/items', methods=['POST'])\ndef endpoint():\n    return {}\n",
        "@router.head('/items')\ndef endpoint():\n    return {}\n",
        "@router.options('/items')\ndef endpoint():\n    return {}\n",
        "@router.trace('/items')\ndef endpoint():\n    return {}\n",
        "@router.websocket('/items')\nasync def endpoint():\n    return None\n",
        "router.add_api_route('/items', endpoint, methods=['POST'])\n",
        "router.add_api_websocket_route('/items', endpoint)\n",
        "app.mount('/nested', nested_app)\n",
    ]
    for index, source in enumerate(declarations):
        path = root / f"api_{index}.py"
        path.write_text(source, encoding="utf-8")
        assert _has_endpoint_decorator(path) is True, source

    ordinary_call = root / "query.py"
    ordinary_call.write_text("query.options(joinedload(User.team))\n", encoding="utf-8")
    assert _has_endpoint_decorator(ordinary_call) is False
    assert _is_canonical_domain_route("app/domains/example/api.py") is False


_PATH_PARAMETER_RE = re.compile(r"\{[^}:]+(?P<converter>:[^}]+)?\}")


def _normalized_route_path(path: str) -> str:
    return _PATH_PARAMETER_RE.sub(
        lambda match: "{param" + (match.group("converter") or "") + "}",
        path,
    )


def _assembled_routes(routes: list) -> list:
    """Flatten FastAPI's lazy included-router wrappers into effective routes."""

    assembled: list = []
    for route in routes:
        effective_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_contexts):
            assembled.extend(effective_contexts())
        else:
            assembled.append(route)
    return assembled


def _duplicate_route_signatures(routes: list) -> dict[str, list[str]]:
    """Inspect the assembled router, after every include-time prefix is applied."""
    from starlette.routing import WebSocketRoute

    signatures: dict[str, list[str]] = {}
    for route in _assembled_routes(routes):
        endpoint = getattr(route, "endpoint", None)
        label = (
            f"{getattr(endpoint, '__module__', '<unknown>')}."
            f"{getattr(endpoint, '__name__', getattr(route, 'name', '<unnamed>'))}"
        )
        normalized_path = _normalized_route_path(str(getattr(route, "path", "")))
        original_route = getattr(route, "original_route", route)
        if isinstance(original_route, WebSocketRoute):
            signatures.setdefault(f"WEBSOCKET {normalized_path}", []).append(label)
            continue
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        for method in sorted(methods):
            signatures.setdefault(f"{method.upper()} {normalized_path}", []).append(label)
    return {signature: labels for signature, labels in signatures.items() if len(labels) > 1}


_REVIEWED_DUPLICATE_ROUTES: dict[str, list[str]] = {
    # The org-owner soft removal intentionally precedes FastAPI-Users' generated
    # superuser hard-delete route. Keep both registrations visible to this gate
    # until the generated router can expose route-level inclusion controls.
    "DELETE /api/v1/users/{param}": [
        "app.domains.identity_access.user_routes.remove_team_member",
        "fastapi_users.router.users.delete_user",
    ],
}


def test_no_duplicate_endpoint_signatures_across_domains() -> None:
    from app.main import app

    duplicates = _duplicate_route_signatures(app.routes)
    assert duplicates == _REVIEWED_DUPLICATE_ROUTES, (
        "Assembled endpoint duplicates changed; remove the collision or add an "
        f"exact reviewed entry. Expected={_REVIEWED_DUPLICATE_ROUTES}, actual={duplicates}"
    )


def test_duplicate_route_gate_uses_include_prefixes_and_normalizes_parameters() -> None:
    from fastapi import APIRouter, FastAPI

    first = APIRouter(prefix="/v1")
    second = APIRouter()

    @first.get("/items/{item_id}")
    def by_item_id(item_id: int) -> dict:
        return {"id": item_id}

    @second.get("/items/{candidate_id}")
    def by_candidate_id(candidate_id: int) -> dict:
        return {"id": candidate_id}

    synthetic = FastAPI()
    synthetic.include_router(first)
    synthetic.include_router(second, prefix="/v1")

    duplicates = _duplicate_route_signatures(synthetic.routes)
    assert "GET /v1/items/{param}" in duplicates


def test_file_size_guard_for_api_and_service_paths(tmp_path: Path) -> None:
    # The policy (limit + scope + allowlist) lives in
    # scripts/check_file_sizes.py, which CI runs directly. This test asserts
    # the same gate from the suite so a local `pytest` run still catches it.
    import importlib.util

    script = PROJECT_ROOT / "scripts" / "check_file_sizes.py"
    spec = importlib.util.spec_from_file_location("check_file_sizes", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    violations = module.find_violations()
    assert not violations, (
        f"API/service paths must stay <= {module.SIZE_LIMIT} LOC and ratcheted "
        f"hotspots may not grow. Violations: {violations}"
    )

    decorated = tmp_path / "decorated.py"
    decorated.write_text(
        "@router.api_route('/items', methods=['POST'])\ndef endpoint():\n    return {}\n",
        encoding="utf-8",
    )
    registered = tmp_path / "registered.py"
    registered.write_text(
        "router.add_api_route('/items', endpoint, methods=['POST'])\n",
        encoding="utf-8",
    )
    ordinary = tmp_path / "ordinary.py"
    ordinary.write_text("query.options(joinedload(User.team))\n", encoding="utf-8")

    assert module._has_endpoint_decorator(decorated) is True
    assert module._has_endpoint_decorator(registered) is True
    assert module._has_endpoint_decorator(ordinary) is False


def test_file_size_guard_enforces_ratcheted_merge_hotspots(tmp_path, monkeypatch) -> None:
    import importlib.util

    script = PROJECT_ROOT / "scripts" / "check_file_sizes.py"
    spec = importlib.util.spec_from_file_location("check_file_sizes_hotspots", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    assert module.MERGE_HOTSPOTS <= module.RATCHETED_FILES.keys()
    hotspot = "app/main.py"
    limit = module.RATCHETED_FILES[hotspot][0]
    hotspot_path = tmp_path / hotspot
    hotspot_path.parent.mkdir(parents=True)
    hotspot_path.write_text("# line\n" * (limit + 1), encoding="utf-8")
    monkeypatch.setattr(module, "BACKEND_ROOT", tmp_path)

    assert module.find_violations() == [f"{hotspot} ({limit + 1} LOC, max {limit})"]


def test_alembic_resolves_to_a_single_head() -> None:
    """The migration graph must always reduce to one head.

    Two PRs landing on main with overlapping migration ancestry can leave
    alembic with multiple heads. ``alembic upgrade head`` then refuses to
    pick between them, the Railway start script fails fast on the
    migration step, and uvicorn never boots — production restart-loops.
    GitHub marks such a pair as a CLEAN merge (the conflict is semantic,
    not textual), so this is the only thing that catches it.

    The CI ``backend`` job runs ``scripts/check_alembic_single_head.py``
    (stdlib-only, no pip install) for the same assertion; this test mirrors
    it for local ``pytest`` runs. When it fails, add a small merge-marker
    migration whose ``down_revision`` is a tuple of the current heads.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # script_location in alembic.ini is relative to the config's directory;
    # set it explicitly so this test is independent of pytest's cwd.
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    heads = list(ScriptDirectory.from_config(cfg).get_heads())

    assert len(heads) == 1, (
        "Alembic must resolve to exactly one head; found "
        f"{len(heads)}: {heads}. Add a merge migration with these "
        "as its `down_revision` tuple."
    )


# --------------------------------------------------------------------------- #
# Authz gate: every state-changing route must be authenticated or justified.
# --------------------------------------------------------------------------- #

# These legacy top-level admin writes authenticate inside the endpoint with
# ``_require_admin(request)`` rather than a FastAPI dependency. Keep the list
# exact: a prefix exemption would let any newly added webhook/public/admin write
# bypass this architecture gate merely by choosing a trusted-looking path.
_ADMIN_SECRET_WRITES = frozenset({
    "/admin/graphiti/backfill",
    "/admin/cv-score/cancel-all",
    "/admin/pre-screen-rejects/backfill",
    "/admin/pre-screen-rejects/rewrite-reasoning",
    "/admin/pre-screen-rejects/supersede-mislabeled",
    "/admin/decisions/discard-on-closed",
    "/admin/scores/rederive-recommendations",
    "/admin/scores/backfill-summaries",
    "/admin/pre-screen-rejects/repair-passed",
    "/admin/decisions/discard-on-agent-off",
    "/admin/scores/normalize-recommendation-labels",
    "/admin/scores/sample-prescreen-calibration",
    "/admin/scores/rescore-wrongly-filtered",
    "/admin/graphiti/test-episode",
})

# Exact ingress surfaces that intentionally cannot require a recruiter JWT.
# These are either credential-establishment endpoints, signed provider
# webhooks, candidate-token assessment endpoints, or deliberately public lead /
# unsubscribe forms. A new unauthenticated write must be reviewed explicitly;
# stale entries fail once a route is removed or gains a normal auth dependency.
_REVIEWED_NON_USER_WRITES = frozenset({
    # FastAPI-Users credential establishment.
    "POST /api/v1/auth/jwt/login",
    "POST /api/v1/auth/register",
    "POST /api/v1/auth/forgot-password",
    "POST /api/v1/auth/reset-password",
    "POST /api/v1/auth/request-verify-token",
    "POST /api/v1/auth/verify",
    # Candidate assessment capability/token surfaces.
    "POST /api/v1/assessments/{assessment_id}/claude/chat",
    "POST /api/v1/assessments/token/{token}/start",
    "POST /api/v1/assessments/demo/start",
    "POST /api/v1/assessments/demo/request",
    "POST /api/v1/assessments/{assessment_id}/upload-cv",
    "POST /api/v1/assessments/token/{token}/upload-cv",
    "POST /api/v1/assessments/{assessment_id}/execute",
    "POST /api/v1/assessments/{assessment_id}/runtime-event",
    "POST /api/v1/assessments/{assessment_id}/repo-file",
    "POST /api/v1/assessments/{assessment_id}/submit",
    # Invite/SSO bootstrap.
    "POST /api/v1/auth/sso-check",
    "POST /api/v1/auth/accept-invite",
    # Provider-signed webhooks.
    "POST /api/v1/webhooks/workable",
    "POST /api/v1/webhooks/fireflies/{organization_id}",
    "POST /api/v1/webhooks/fireflies",
    "POST /api/v1/webhooks/resend",
    "POST /api/v1/webhooks/stripe",
    # Deliberately public forms/capability links.
    "POST /api/v1/public/demo-lead",
    "POST /api/v1/public/bespoke-task",
    "POST /api/v1/public/unsubscribe/{token}",
})


def _executable_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Return calls in this function body, excluding uninvoked nested definitions."""

    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - AST visitor API
            calls.append(node)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            return

    visitor = _Visitor()
    for statement in function.body:
        visitor.visit(statement)
    return calls


def _source_has_required_admin_call(source: str) -> bool:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return False
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        None,
    )
    if function is None:
        return False
    for call in _executable_calls(function):
        if not isinstance(call.func, ast.Name) or call.func.id != "_require_admin":
            continue
        if (
            len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "request"
        ):
            return True
    return False


def _authz_allowed_without_user(route) -> bool:
    if route.path not in _ADMIN_SECRET_WRITES:
        return False

    # Dependency introspection cannot see an in-body check. Pin both the exact
    # endpoint inventory and the check itself so removing `_require_admin` from
    # an existing route also fails this test.
    try:
        source = inspect.getsource(route.endpoint)
    except (OSError, TypeError):
        return False
    return _source_has_required_admin_call(source)


def _dependency_calls(dependant) -> list:
    calls: list = []
    stack = [dependant]
    while stack:
        node = stack.pop()
        if getattr(node, "call", None) is not None:
            calls.append(node.call)
        stack.extend(getattr(node, "dependencies", []) or [])
    return calls


def _route_has_authenticated_principal(route) -> bool:
    """Recognize the project's JWT, API-key, and admin-secret dependencies."""
    from app.domains.identity_access.api_key_auth import get_api_principal
    from app.domains.identity_access.users_fastapi import current_active_user
    from app.platform.admin_auth import require_admin_secret

    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    for call in _dependency_calls(dependant):
        if call in {current_active_user, get_api_principal, require_admin_secret}:
            return True
        # FastAPI-Users generates distinct closures for its /users and logout
        # routers, so object identity cannot match our exported dependency.
        if getattr(call, "__module__", "") != "fastapi_users.authentication.authenticator":
            continue
        qualname = getattr(call, "__qualname__", "")
        if (
            "current_user_dependency" in qualname
            or "current_user_token_dependency" in qualname
        ):
            return True
    return False


def test_admin_authz_gate_requires_an_executable_guard_call() -> None:
    comment_only = """
        def endpoint(request):
            # _require_admin(request)
            return {'ok': True}
    """
    docstring_only = """
        def endpoint(request):
            \"\"\"Remember to call _require_admin(request).\"\"\"
            return {'ok': True}
    """
    nested_only = """
        def endpoint(request):
            def unused():
                _require_admin(request)
            return {'ok': True}
    """
    guarded = """
        async def endpoint(request):
            _require_admin(request)
            return {'ok': True}
    """

    assert _source_has_required_admin_call(comment_only) is False
    assert _source_has_required_admin_call(docstring_only) is False
    assert _source_has_required_admin_call(nested_only) is False
    assert _source_has_required_admin_call(guarded) is True


_REVIEWED_MOUNTS: dict[str, str] = {
    "/mcp": "read-only FastMCP transport; auth and exact tool inventory have dedicated contracts",
}


def _mount_paths(routes: list) -> set[str]:
    from starlette.routing import Mount

    paths: set[str] = set()
    for route in _assembled_routes(routes):
        original_route = getattr(route, "original_route", route)
        if isinstance(original_route, Mount):
            effective_route = getattr(route, "starlette_route", None)
            paths.add(
                str(
                    effective_route.path
                    if isinstance(effective_route, Mount)
                    else original_route.path
                )
            )
    return paths


def test_mounted_asgi_surfaces_are_explicitly_reviewed() -> None:
    """Opaque mounted apps cannot silently bypass the APIRoute auth inventory."""
    from app.main import app

    actual = _mount_paths(app.routes)
    expected = set(_REVIEWED_MOUNTS)
    assert actual == expected, (
        "Mounted ASGI surfaces require an explicit security review and exact "
        f"inventory entry. Unexpected={sorted(actual - expected)}, "
        f"missing={sorted(expected - actual)}"
    )


def test_unreviewed_mount_cannot_evade_inventory() -> None:
    from fastapi import APIRouter, FastAPI
    from starlette.applications import Starlette

    synthetic = FastAPI()
    nested = APIRouter()
    nested.mount("/unreviewed", Starlette())
    synthetic.include_router(nested, prefix="/v1")
    assert _mount_paths(synthetic.routes) - set(_REVIEWED_MOUNTS) == {
        "/v1/unreviewed"
    }


def test_every_write_route_is_authenticated_or_justified() -> None:
    """Every state-changing route (POST/PUT/PATCH/DELETE) must depend on the
    authenticated user (``get_current_user`` / ``current_active_user``), unless
    it's one of the explicitly-justified non-user-auth surfaces above. Catches
    an unguarded write endpoint slipping in — the authz invariant.
    """
    from app.main import app

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}

    offenders: list[str] = []
    reviewed_non_user_seen: set[str] = set()
    admin_secret_seen: set[str] = set()
    for route in _assembled_routes(app.routes):
        methods = (getattr(route, "methods", None) or set()) & write_methods
        if not methods:
            continue
        if _route_has_authenticated_principal(route):
            continue
        if _authz_allowed_without_user(route):
            admin_secret_seen.add(str(route.path))
            continue
        for method in sorted(methods):
            signature = f"{method} {route.path}"
            if signature in _REVIEWED_NON_USER_WRITES:
                reviewed_non_user_seen.add(signature)
                continue
            offenders.append(signature)

    stale_non_user = sorted(_REVIEWED_NON_USER_WRITES - reviewed_non_user_seen)
    stale_admin = sorted(_ADMIN_SECRET_WRITES - admin_secret_seen)

    assert not offenders and not stale_non_user and not stale_admin, (
        "Write-route auth inventory changed. Add a recognized dependency, "
        "an executable _require_admin(request) call, or an exact reviewed "
        "non-user ingress entry. Remove stale entries when routes gain auth or "
        f"are deleted. Offenders={sorted(offenders)}, "
        f"stale_non_user={stale_non_user}, stale_admin={stale_admin}"
    )


def test_agent_mutation_tools_call_shared_action_layer() -> None:
    """Every agent mutation tool must call into ``app.actions.<name>.run``,
    not implement business logic inline. The same actions are called by
    recruiter routes, so this gate enforces agent/recruiter parity at the
    code level.

    Read-only tools (``get_*``, ``search_*``, ``compare_*``, ``find_*``,
    ``survey_*``, ``read_*``, ``nl_search_*``, ``graph_search_*``,
    ``refresh_candidate_graph``, ``get_cohort_signals``, ``evaluate_policy``,
    ``ask_recruiter``, ``agent_run_complete``, ``batch_score_cv``) are
    exempt — they either delegate to ``mcp_handlers``/``cohort_tools`` or
    are agent-only loops over an action.
    """

    registry_path = PROJECT_ROOT / "app" / "agent_runtime" / "tool_registry.py"
    tree = ast.parse(registry_path.read_text(encoding="utf-8"))

    read_only_or_internal = {
        "_tool_get_application",
        "_tool_get_candidate",
        "_tool_get_candidate_cv",
        "_tool_search_applications",
        "_tool_compare_applications",
        "_tool_nl_search_candidates",
        "_tool_graph_search_candidates",
        "_tool_refresh_candidate_graph",
        "_tool_get_cohort_signals",
        "_tool_evaluate_policy",
        "_tool_survey_role_state",
        "_tool_find_apps_in_state",
        "_tool_read_pending_recruiter_inputs",
        "_tool_batch_score_cv",
        "_tool_ask_recruiter",
        "_tool_agent_run_complete",
        # Internal agent-memory breadcrumb — appends to
        # role.agent_calibration.notes via calibration.save(). Doesn't
        # mutate candidate/application state, so it doesn't go through
        # the shared action layer (which exists for agent/recruiter
        # parity on candidate-facing actions).
        "_tool_record_observation",
    }

    action_modules = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module in {"actions", "app.actions"}
        for alias in node.names
    }
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def _calls_shared_action(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        allow_queue_helper: bool = True,
        required_action: str | None = None,
    ) -> bool:
        for call in _executable_calls(function):
            if (
                allow_queue_helper
                and isinstance(call.func, ast.Name)
                and call.func.id == "_queue"
            ):
                return True
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "run":
                continue
            if (
                isinstance(call.func.value, ast.Name)
                and call.func.value.id in action_modules
                and (
                    required_action is None
                    or call.func.value.id == required_action
                )
            ):
                return True
        return False

    violations: list[str] = []
    for handler_name, function in functions.items():
        if not handler_name.startswith("_tool_"):
            continue
        if handler_name in read_only_or_internal:
            continue
        if not _calls_shared_action(function):
            violations.append(handler_name)

    queue_helper = functions.get("_queue")
    if queue_helper is None or not _calls_shared_action(
        queue_helper,
        allow_queue_helper=False,
        required_action="queue_decision",
    ):
        violations.append("_queue")

    assert not violations, (
        "Agent mutation tool handlers must make an executable shared-action call "
        "(<action>.run(...) or _queue(...)). Inline business logic is "
        f"forbidden. Violations: {violations}"
    )


def test_agent_action_gate_ignores_text_and_supports_async_handlers() -> None:
    source = """
        async def _tool_text_only():
            \"\"\"send_assessment.run(...)\"\"\"
            # _queue(...)
            return None

        async def _tool_nested_only():
            def unused():
                send_assessment.run()
            return None

        async def _tool_action():
            return send_assessment.run()

        def _tool_queue():
            return _queue()
    """
    tree = ast.parse(textwrap.dedent(source))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def _has_call(function_name: str) -> bool:
        for call in _executable_calls(functions[function_name]):
            if isinstance(call.func, ast.Name) and call.func.id == "_queue":
                return True
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "run"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "send_assessment"
            ):
                return True
        return False

    assert _has_call("_tool_text_only") is False
    assert _has_call("_tool_nested_only") is False
    assert _has_call("_tool_action") is True
    assert _has_call("_tool_queue") is True


def test_no_imports_of_removed_service_shims() -> None:
    removed_shim_names = [
        "access_control_service",
        "claude_service",
        "e2b_service",
        "email_service",
        "prompt_analytics",
        "scoring_service",
        "stripe_service",
        "workable_service",
    ]
    shim_group = "|".join(removed_shim_names)
    patterns = [
        re.compile(rf"(?:from|import)\s+app\.services\.({shim_group})\b"),
        re.compile(rf"(?:from|import)\s+\.\.\.?services\.({shim_group})\b"),
    ]

    scan_roots = [PROJECT_ROOT / "app", PROJECT_ROOT / "tests"]
    violations: list[str] = []
    for root in scan_roots:
        for path in _python_files(root):
            content = path.read_text(encoding="utf-8")
            if any(pattern.search(content) for pattern in patterns):
                violations.append(str(path))

    assert not violations, f"Removed service shims must not be imported: {violations}"


def test_no_bare_anthropic_client_construction() -> None:
    """Anthropic API calls must flow through ``MeteredAnthropicClient``.

    The wrapper is what writes ``UsageEvent`` rows. A bare
    ``Anthropic(api_key=...)`` instantiation outside the approved
    factory + adapter files = invisible spend = the
    73% reconciliation gap that surfaced on 2026-05-20.

    The approved sites that construct the bare SDK client are:
    - ``app/services/claude_client_resolver.py`` (the factory itself,
      wraps it on the way out)
    - ``app/services/metered_anthropic_client.py`` (defines the wrapper,
      needs the bare class for typing)
    - ``app/components/integrations/anthropic_admin/*`` (admin API,
      not the billable inference API)

    Any other file containing either ``Anthropic(api_key`` or a literal
    construction of ``Anthropic()`` must route through the resolver
    instead.
    """
    approved = {
        "app/services/claude_client_resolver.py",
        "app/services/metered_anthropic_client.py",
    }
    # Admin API client lives under anthropic_admin/* — uses a different
    # SDK surface (admin endpoints), not billable inference. Allow the
    # entire subtree.
    approved_subtrees = (
        "app/components/integrations/anthropic_admin/",
    )

    constructor_re = re.compile(r"\bAnthropic\s*\(\s*api_key\s*=")
    # A file constructing the bare SDK is acceptable IFF it immediately
    # wraps the result in ``MeteredAnthropicClient(inner=...)`` so the
    # meter still fires. We treat the presence of that wrapper call in
    # the same file as proof.
    wrapper_re = re.compile(r"MeteredAnthropicClient\s*\(\s*inner\s*=")

    violations: list[tuple[str, str]] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in approved:
            continue
        if any(rel.startswith(t) for t in approved_subtrees):
            continue
        content = path.read_text(encoding="utf-8")
        if constructor_re.search(content) and not wrapper_re.search(content):
            violations.append((rel, "constructs Anthropic(api_key=...) without wrapping in MeteredAnthropicClient(inner=...)"))

    assert not violations, (
        "Every Anthropic client must flow through MeteredAnthropicClient "
        "so the meter writes a UsageEvent for each call. Direct "
        "`Anthropic(api_key=...)` without wrapping produces invisible "
        "spend (reconciliation gap on 2026-05-20 was 73% via this exact "
        f"pattern). Violations: {violations}"
    )


def test_explicit_key_anthropic_seam_is_narrow_and_metered() -> None:
    """Only interrogation may request the explicit-key metered client."""

    factory = "get_metered_interrogation_client"
    retired_raw_escapes = {
        "build_bounded_anthropic_client",
        "get_raw_shared_client",
    }
    consumers: set[str] = set()
    violations: list[tuple[str, str]] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        escaped = sorted(names & retired_raw_escapes)
        if escaped:
            violations.append((rel, f"uses retired raw client escape(s): {escaped}"))
        if rel != "app/services/claude_client_resolver.py" and factory in names:
            consumers.add(rel)

    assert not violations
    assert consumers == {"app/components/assessments/interrogation.py"}


def test_no_bare_async_anthropic_client_construction() -> None:
    """The async sister rule: ``AsyncAnthropic(...)`` must be wrapped in
    ``MeteredAsyncAnthropic(inner=...)`` in the same file.

    Background: Graphiti's ``AnthropicClient`` accepts an ``AsyncAnthropic``
    instance and runs all entity-extraction calls through it. Until
    2026-05-26 we built a bare ``AsyncAnthropic`` inside
    ``candidate_graph/client.py``, so every candidate sync's Haiku calls
    bypassed the meter entirely (no call_log, no usage_event). On
    2026-05-23 this hid 16.15M of 19.18M Haiku input tokens — Anthropic
    billed $60.31, our records showed $35.48. The async wrapper closes
    that hole; this gate prevents it from re-opening.

    Approved sites mirror the sync gate: the wrapper itself, and the
    candidate_graph client factory (constructs + immediately wraps).
    """
    approved = {
        "app/services/metered_async_anthropic_client.py",
        "app/candidate_graph/client.py",
    }

    constructor_re = re.compile(r"\bAsyncAnthropic\s*\(")
    wrapper_re = re.compile(r"MeteredAsyncAnthropic\s*\(\s*inner\s*=")

    violations: list[tuple[str, str]] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in approved:
            continue
        content = path.read_text(encoding="utf-8")
        if constructor_re.search(content) and not wrapper_re.search(content):
            violations.append((rel, "constructs AsyncAnthropic(...) without wrapping in MeteredAsyncAnthropic(inner=...)"))

    assert not violations, (
        "Every AsyncAnthropic client must flow through "
        "MeteredAsyncAnthropic so claude_call_log captures the spend. "
        "Bare AsyncAnthropic produces invisible Haiku spend (Graphiti "
        f"path leaked 16M tokens/day before this gate). Violations: {violations}"
    )


def test_anthropic_sdk_constructors_disable_hidden_retries() -> None:
    """Every wire attempt must pass through our per-attempt meter."""

    violations: list[str] = []
    for root in (PROJECT_ROOT / "app", PROJECT_ROOT / "vendor"):
        for path in _python_files(root):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            zero_constants = {
                target.id
                for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and type(node.value.value) is int
                and node.value.value == 0
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else None
                )
                if name not in {"Anthropic", "AsyncAnthropic"}:
                    continue
                retry_kw = next(
                    (kw.value for kw in node.keywords if kw.arg == "max_retries"),
                    None,
                )
                explicit_zero = (
                    isinstance(retry_kw, ast.Constant)
                    and type(retry_kw.value) is int
                    and retry_kw.value == 0
                ) or (
                    isinstance(retry_kw, ast.Name)
                    and retry_kw.id in zero_constants
                )
                if not explicit_zero:
                    violations.append(f"{rel}:{node.lineno}")

    assert not violations, (
        "Anthropic SDK hidden retries bypass one-reservation-per-wire-attempt: "
        f"{violations}"
    )


def test_voyage_sdk_construction_is_immediately_wrapped_and_surface_is_narrow() -> None:
    direct_sdk_constructors: list[str] = []
    embedder_factories: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        content = path.read_text(encoding="utf-8")
        if re.search(r"\bvoyageai\.AsyncClient\s*\(", content):
            direct_sdk_constructors.append(rel)
        if re.search(r"\bVoyageAIEmbedder\s*\(", content):
            embedder_factories.append(rel)
            assert "wrap_voyage_embedder(" in content
    assert direct_sdk_constructors == []
    assert embedder_factories == ["app/candidate_graph/client.py"]

    wrapper_path = PROJECT_ROOT / "app/services/metered_voyage_embedder.py"
    tree = ast.parse(wrapper_path.read_text(encoding="utf-8"))
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MeteredVoyageClient"
    )
    public_methods = {
        node.name
        for node in wrapper.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public_methods == {"embed"}
    getattr_method = next(
        node
        for node in wrapper.body
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        for node in ast.walk(getattr_method)
    )


# ---------------------------------------------------------------------------
# Metering-consistency gates
# ---------------------------------------------------------------------------
# These guard the *attribution* layer (which Feature a call books to, and
# whether that Feature/model can be priced) rather than the *transport* layer
# (the two gates above, which guard that calls flow through the metered
# wrapper at all). The class of bug they catch shipped for real:
# ``requisition_intake*`` feature strings were used at call sites before the
# matching ``Feature`` enum members existed, so ``record_event`` raised a
# ``ValueError`` on the ``Feature(...)`` conversion and the usage was silently
# dropped (metering must never raise, so the event just vanished).


def test_metering_feature_literals_resolve_to_enum() -> None:
    """Every metering ``feature`` string literal must be a ``Feature`` member.

    Scans ``app/**.py`` for both shapes that flow into the meter:
      * ``feature="..."`` kwargs (MeteringContext / record_event / call sites)
      * ``"feature": "..."`` dict keys (the ``metering={...}`` wrapper kwarg)

    and asserts each literal value resolves to a ``Feature`` member. A literal
    with no enum member is exactly the ``requisition_intake*`` bug: the wrapper
    calls ``Feature(value)`` inside ``record_event``, that raises, and the
    swallow-all metering path drops the usage_event with only a logged warning.

    The negative-lookbehind ``(?<![\\w.])`` keeps this from matching
    ``sub_feature=`` or attribute access like ``x.feature=``. A small explicit
    ignore-list covers the one known non-metering false positive (a
    ``"feature": "kubernetes"`` example inside a docstring).
    """
    from app.services.pricing_service import Feature

    valid_values = {f.value for f in Feature}

    # (relative_path, literal_value) pairs that are NOT metering features.
    # Keep this list TINY — every entry is a place the regex over-matched a
    # non-metering string. The dead ``"feature": "evaluate_policy"`` key in
    # tool_registry.py was deleted (not ignored), so it must not appear here.
    ignore: set[tuple[str, str]] = {
        # Docstring example in a `"skills": [{"feature": "kubernetes", ...}]`
        # blob — a candidate-signal shape, not a metering feature.
        ("app/services/cohort_signals_service.py", "kubernetes"),
    }

    kwarg_re = re.compile(
        r"(?<![\w.])feature\s*=\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
    )
    dict_re = re.compile(
        r"[\"']feature[\"']\s*:\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
    )

    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        content = path.read_text(encoding="utf-8")
        for match in [*kwarg_re.finditer(content), *dict_re.finditer(content)]:
            value = match.group(1)
            if (rel, value) in ignore:
                continue
            if value not in valid_values:
                violations.append(f"{rel}: feature={value!r}")

    assert not violations, (
        "Every metering `feature` string literal must resolve to a Feature "
        "enum member, or record_event raises and the usage_event is silently "
        "dropped (the requisition_intake* bug). Add the member to "
        "app/services/pricing_service.py::Feature (and its pricing + "
        "reservation entries), or fix the typo. Offending literals: "
        f"{violations}"
    )


def test_every_feature_is_priced_and_reservable() -> None:
    """Every ``Feature`` member must appear in BOTH pricing maps.

    - ``pricing_service._FEATURE_PRICING`` — consulted by ``credits_charged``
      / ``feature_pricing`` to apply the per-feature markup.
    - ``pricing_service.estimate_reservation`` — consulted by
      ``usage_metering_service.reserve`` for the pre-flight balance check.

    A member missing from either map ``KeyError``s at runtime the first time
    that feature is billed/reserved. This caught ``CANDIDATE_GROUNDING`` (it
    was priced but absent from the reservation map).
    """
    from app.services.pricing_service import (
        Feature,
        _FEATURE_PRICING,
        estimate_reservation,
        feature_pricing,
    )

    missing_pricing = [f.name for f in Feature if f not in _FEATURE_PRICING]
    assert not missing_pricing, (
        "Feature members missing from _FEATURE_PRICING (credits_charged would "
        f"KeyError): {missing_pricing}"
    )

    missing_reservation: list[str] = []
    for feature in Feature:
        try:
            estimate_reservation(feature)
        except KeyError:
            missing_reservation.append(feature.name)
    assert not missing_reservation, (
        "Feature members missing from estimate_reservation's map "
        f"(reserve() would KeyError): {missing_reservation}"
    )

    # Belt-and-suspenders: feature_pricing(...) must succeed for every member
    # (it also accepts the string value, the form record_event receives).
    for feature in Feature:
        feature_pricing(feature)
        feature_pricing(feature.value)


def test_configured_and_literal_claude_models_are_priceable() -> None:
    """Every outbound Claude model id must be explicitly reviewed and enabled.

    Two sources are scanned:
      * ``CLAUDE_*MODEL`` string defaults in ``platform/config.py`` and the
        ``resolved_claude_*`` fallbacks (the `or "claude-..."` literals).
      * every ``claude-...`` model literal used anywhere in ``app/``.

    Historical rates are deliberately broader than outbound admission: retired
    ids remain rated for durable reconciliation but may never become callable
    merely by existing in the rate table.
    """
    from app.services.claude_model_pricing import is_priceable_claude_model

    def _priceable(model_id: str) -> bool:
        return is_priceable_claude_model(model_id)

    # 1. config.py defaults + resolver fallbacks. Match assignment defaults
    #    (CLAUDE_*MODEL: str = "claude-...") and the `or "claude-..."` fallbacks.
    config_path = PROJECT_ROOT / "app" / "platform" / "config.py"
    config_src = config_path.read_text(encoding="utf-8")
    config_model_re = re.compile(r"[\"'](claude-[A-Za-z0-9.\-]+)[\"']")
    config_models = set(config_model_re.findall(config_src))

    bad_config = sorted(m for m in config_models if not _priceable(m))
    assert not bad_config, (
        "config.py references Claude model id(s) not enabled for outbound use. "
        "Review availability and price before enabling or fix the id: "
        f"{bad_config}"
    )

    # 2. Every claude-... literal anywhere in app/. Excludes the rate registry
    #    itself (it DEFINES the legacy rate keys, incl. ids new code shouldn't
    #    call) and the migration-doc-style comments are caught too — a real
    #    hardcoded model string that can't be priced is always a bug.
    literal_re = re.compile(r"[\"'](claude-[A-Za-z0-9.\-]+)[\"']")
    # Defines the legacy rate keys themselves (incl. retired ids kept for
    # historical recompute) — not call-site model selection.
    skip_rel = {"app/services/claude_model_pricing.py"}
    # (relative_path, model_id) pairs that are legitimately unpriceable: retired
    # ids kept ONLY as alias-detection keys, never billed at their own id.
    ignore_literals: set[tuple[str, str]] = {
        # model_fallback.py keeps retired Haiku ids so an explicit legacy
        # request still detects as a Haiku alias and resolves (via the fallback
        # chain) to CURRENT_HAIKU_MODEL = claude-haiku-4-5-20251001 — the only
        # id actually sent. See that module's docstring for why they must stay.
        ("app/components/integrations/claude/model_fallback.py", "claude-3-5-haiku-latest"),
        ("app/components/integrations/claude/model_fallback.py", "claude-3-5-haiku-20241022"),
        ("app/components/integrations/claude/model_fallback.py", "claude-3-haiku-20240307"),
    }
    offending: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in skip_rel:
            continue
        content = path.read_text(encoding="utf-8")
        for model_id in set(literal_re.findall(content)):
            if (rel, model_id) in ignore_literals:
                continue
            if not _priceable(model_id):
                offending.append(f"{rel}: {model_id}")

    assert not offending, (
        "Claude model literal(s) in app/ are not enabled for outbound use, so "
        "the provider boundary would reject them. A retired/absent id is the "
        "classic cause. "
        f"Offending: {sorted(offending)}"
    )


def test_eeo_model_is_segregated_from_scoring_and_decision() -> None:
    """The voluntary-EEO surface must never be reachable from the scoring/decision
    path — the agent must not see a protected characteristic. This pins the
    segregation architecturally: ``EEOResponse`` / the eeo model / eeo_service may
    be imported ONLY by the compliance domain (which owns them) and the job-pages
    public route (which mints the token + records the voluntary answer). Any
    reference from a scoring or decision module fails this gate.
    """
    # Files allowed to touch the EEO surface. Everything else — especially
    # anything under scoring/decision — must not.
    allowed = {
        "app/models/__init__.py",
        "app/models/eeo_response.py",
        "app/domains/compliance/__init__.py",
        "app/domains/compliance/eeo_service.py",
        "app/domains/compliance/prescreen_impact_service.py",
        "app/domains/compliance/routes.py",
        # The public apply route mints the token + records the voluntary answer.
        "app/domains/job_pages/routes.py",
    }
    needle = re.compile(r"eeo_response|EEOResponse|eeo_service", re.IGNORECASE)

    offenders: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in allowed:
            continue
        if needle.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)

    assert not offenders, (
        "EEO self-ID surface referenced outside the compliance domain / apply "
        "route — it must stay segregated from scoring/decision:\n  "
        + "\n  ".join(sorted(offenders))
    )
