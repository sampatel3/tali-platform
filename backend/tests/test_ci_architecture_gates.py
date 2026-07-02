from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_no_endpoint_decorators_in_legacy_paths() -> None:
    legacy_roots = [
        PROJECT_ROOT / "app" / "api" / "v1",
        PROJECT_ROOT / "app" / "components",
    ]
    # Files awaiting migration to canonical domain modules. New entries
    # are not welcome — fix the migration instead of expanding this list.
    allowlist: dict[str, str] = {
        "app/api/v1/background_jobs.py": "background-job status endpoints, pending domain split",
    }
    violations: list[str] = []
    pattern = re.compile(r"@router\.(?:get|post|put|patch|delete)\(")

    for root in legacy_roots:
        for path in _python_files(root):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel in allowlist:
                continue
            content = path.read_text(encoding="utf-8")
            if pattern.search(content):
                violations.append(str(path))

    assert not violations, (
        "Endpoint decorators must only live in canonical domain route files. "
        f"Violations: {violations}"
    )


def test_no_duplicate_endpoint_signatures_across_domains() -> None:
    domain_root = PROJECT_ROOT / "app" / "domains"
    prefix_re = re.compile(r"APIRouter\([^)]*prefix\s*=\s*['\"]([^'\"]+)['\"]")
    route_re = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]")

    signatures: dict[str, list[str]] = {}
    for path in _python_files(domain_root):
        content = path.read_text(encoding="utf-8")
        prefix_match = prefix_re.search(content)
        prefix = prefix_match.group(1) if prefix_match else ""

        for method, route_path in route_re.findall(content):
            if route_path.startswith("/"):
                combined = f"{prefix}{route_path}"
            else:
                combined = f"{prefix}/{route_path}"
            normalized = re.sub(r"/{2,}", "/", combined) or "/"
            signature = f"{method.upper()} {normalized}"
            signatures.setdefault(signature, []).append(str(path))

    duplicates = {sig: files for sig, files in signatures.items() if len(set(files)) > 1}
    assert not duplicates, f"Duplicate endpoint signatures detected across domain routers: {duplicates}"


def test_file_size_guard_for_api_and_service_paths() -> None:
    size_limit = 500
    allowlist: dict[str, str] = {
        "app/components/assessments/service.py": "assessment orchestration",
        "app/components/integrations/workable/sync_service.py": "Workable sync flow",
        "app/components/integrations/workable/service.py": "legacy Workable integration service",
        "app/domains/agentic/routes.py": "agent decisions queue + status + run-now (cohesive surface, 7 LOC over)",
        "app/domains/assessments_runtime/analytics_routes.py": "Mission Control reporting summary aggregator",
        "app/domains/assessments_runtime/applications_routes.py": "applications API",
        "app/domains/assessments_runtime/candidate_runtime_routes.py": "candidate runtime API",
        "app/domains/assessments_runtime/candidate_terminal_routes.py": "candidate terminal API",
        "app/domains/assessments_runtime/pipeline_service.py": "assessment runtime pipeline orchestration",
        "app/domains/assessments_runtime/roles_management_routes.py": "roles + job-spec upload API",
        "app/domains/billing_webhooks/billing_routes.py": "Stripe + credit-pack billing routes (TODO: split webhook handlers)",
        "app/domains/workable_sync/routes.py": "legacy Workable sync API",
        "app/services/fit_matching_service.py": "CV-to-role fit scoring pipeline",
        "app/services/interview_support_service.py": "interview pack builder (1 LOC over after chip-helper extraction)",
    }

    target_files = set(_python_files(PROJECT_ROOT / "app" / "api" / "v1"))
    target_files.update((PROJECT_ROOT / "app").rglob("*service.py"))
    target_files.update((PROJECT_ROOT / "app" / "domains").rglob("*routes.py"))

    violations: list[str] = []
    for path in sorted(target_files):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        if lines <= size_limit:
            continue
        if rel in allowlist:
            continue
        violations.append(f"{rel} ({lines} LOC)")

    assert not violations, (
        f"API/service paths must stay <= {size_limit} LOC unless explicitly allowlisted. "
        f"Violations: {violations}"
    )


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
# P0.5 RBAC — authz gate: every state-changing route must be authenticated.
# --------------------------------------------------------------------------- #

# Write routes that are DELIBERATELY not user-authenticated, each guarded by a
# different mechanism. A NEW write route must either depend on
# ``get_current_user`` / ``require_role`` OR be justified here — the gate fails
# otherwise, so an unguarded write endpoint can't ship by accident.
_CANDIDATE_ASSESSMENT_WRITES = frozenset({
    # The candidate assessment-taking surface — the candidate has no login;
    # access is authorised by the per-assessment token / session.
    "/api/v1/assessments/demo/request",
    "/api/v1/assessments/demo/start",
    "/api/v1/assessments/token/{token}/start",
    "/api/v1/assessments/token/{token}/upload-cv",
    "/api/v1/assessments/{assessment_id}/claude",
    "/api/v1/assessments/{assessment_id}/claude/chat",
    "/api/v1/assessments/{assessment_id}/claude/retry",
    "/api/v1/assessments/{assessment_id}/execute",
    "/api/v1/assessments/{assessment_id}/repo-file",
    "/api/v1/assessments/{assessment_id}/submit",
    "/api/v1/assessments/{assessment_id}/terminal/stop",
    "/api/v1/assessments/{assessment_id}/upload-cv",
})

# Prefixes whose write routes are authenticated by a NON-user mechanism.
_NON_USER_AUTH_PREFIXES = (
    "/api/v1/auth/",       # fastapi-users public auth (register/login/reset/verify)
    "/api/v1/users",       # fastapi-users self / superuser management (own guard)
    "/api/v1/webhooks/",   # provider webhooks — verified by signature
    "/public/v1/",         # public API — authenticated by API key
    "/api/v1/public/",     # public no-login surfaces (careers apply / hiring-manager intake)
    "/careers/",           # public careers pages
)


def _authz_allowed_without_user(path: str) -> bool:
    # ``/admin/`` diagnostics verify an ``X-Admin-Secret`` header in-body (not a
    # dependency), so dependency introspection can't see it — allow by path.
    if "/admin/" in path:
        return True
    if any(path.startswith(p) for p in _NON_USER_AUTH_PREFIXES):
        return True
    return path in _CANDIDATE_ASSESSMENT_WRITES


def test_every_write_route_is_authenticated_or_justified() -> None:
    """Every state-changing route (POST/PUT/PATCH/DELETE) must depend on the
    authenticated user (``get_current_user`` — which ``require_role`` also pulls
    in), unless it's one of the explicitly-justified non-user-auth surfaces
    above. Catches an unguarded write endpoint slipping in — the P0.5 authz
    invariant.
    """
    from fastapi.routing import APIRoute

    from app.domains.identity_access.users_fastapi import current_active_user
    from app.main import app

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}

    def _deep_calls(dependant) -> list:
        acc, stack = [], [dependant]
        while stack:
            node = stack.pop()
            if getattr(node, "call", None) is not None:
                acc.append(node.call)
            stack.extend(getattr(node, "dependencies", []) or [])
        return acc

    offenders: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = (route.methods or set()) & write_methods
        if not methods:
            continue
        if current_active_user in _deep_calls(route.dependant):
            continue
        if _authz_allowed_without_user(route.path):
            continue
        offenders.append(f"{','.join(sorted(methods))} {route.path}")

    assert not offenders, (
        "Unauthenticated write route(s) — add `Depends(get_current_user)` / "
        "`require_role(...)`, or justify in the allowlist in this test:\n  "
        + "\n  ".join(sorted(offenders))
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
    content = registry_path.read_text(encoding="utf-8")

    handler_def_re = re.compile(r"^def (_tool_[a-z_]+)\(", re.MULTILINE)
    handler_names = handler_def_re.findall(content)

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
        # Decision-queueing tools call queue_decision via the _queue() helper
        # rather than directly. We verify _queue itself below.
        "_tool_queue_advance_decision",
        "_tool_queue_reject_decision",
        "_tool_queue_skip_assessment_reject_decision",
    }

    # For each mutation handler, slice the function body and require it to
    # mention ``<action_name>.run(`` or call ``_queue(``.
    body_re = re.compile(
        r"^def (_tool_[a-z_]+)\([^)]*\)[^:]*:\n((?:(?:    .*\n)|\n)+)",
        re.MULTILINE,
    )
    violations: list[str] = []
    for handler_name, body in body_re.findall(content):
        if handler_name in read_only_or_internal:
            continue
        if ".run(" not in body and "_queue(" not in body:
            violations.append(handler_name)

    assert not violations, (
        "Agent mutation tool handlers must call a shared action "
        "(<action>.run(...) or _queue(...)). Inline business logic is "
        f"forbidden. Violations: {violations}"
    )


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

    The four approved sites that construct the bare SDK client are:
    - ``app/services/claude_client_resolver.py`` (the factory itself,
      wraps it on the way out)
    - ``app/services/metered_anthropic_client.py`` (defines the wrapper,
      needs the bare class for typing)
    - ``app/components/integrations/claude/service.py`` (constructs +
      immediately hands to the wrapper inside the same file)
    - ``app/components/integrations/anthropic_admin/*`` (admin API,
      not the billable inference API)

    Any other file containing either ``Anthropic(api_key`` or a literal
    construction of ``Anthropic()`` must route through the resolver
    instead.
    """
    approved = {
        "app/services/claude_client_resolver.py",
        "app/services/metered_anthropic_client.py",
        "app/components/integrations/claude/service.py",
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
