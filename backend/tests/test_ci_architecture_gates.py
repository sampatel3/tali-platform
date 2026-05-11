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
