#!/usr/bin/env python3
"""Backend file-size gate for bloat-prone modules and merge hotspots.

This is the single source of truth for the policy that CI and the architecture
tests enforce. New route/service modules stay at or below ``SIZE_LIMIT`` and
every other application module stays at or below ``GENERAL_SIZE_LIMIT`` so a
rename cannot evade the guard. Existing large modules and central merge
hotspots have exact burn-down ratchets: they may neither regrow nor quietly
spend lines removed by an extraction.

Stdlib-only, so CI needs no pip install.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

CANONICAL_BACKEND_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = CANONICAL_BACKEND_ROOT

SIZE_LIMIT = 500
GENERAL_SIZE_LIMIT = 1000

# Values are exact LOC ratchets, not exemptions. Do not raise one merely to
# land another feature. Lower it whenever the corresponding module shrinks.
LEGACY_SIZE_BASELINES: dict[str, int] = {
    "app/agent_chat/tools.py": 391,
    "app/agent_runtime/tool_registry.py": 2692,
    "app/candidate_search/top_candidates.py": 1160,
    "app/components/assessments/rubric_scoring.py": 1200,
    "app/components/assessments/service.py": 1417,
    "app/components/assessments/submission_runtime.py": 1621,
    "app/components/integrations/claude_agent/service.py": 732,
    "app/components/integrations/workable/sync_service.py": 2299,
    "app/components/integrations/workable/service.py": 809,
    "app/components/notifications/tasks.py": 1061,
    "app/main.py": 1336,
    "app/models/__init__.py": 414,
    "app/domains/agentic/routes.py": 1526,
    "app/domains/assessments_runtime/analytics_routes.py": 1901,
    "app/domains/assessments_runtime/applications_routes.py": 5765,
    "app/domains/assessments_runtime/candidate_runtime_routes.py": 893,
    "app/domains/assessments_runtime/interview_feedback_routes.py": 558,
    "app/domains/assessments_runtime/pipeline_service.py": 1289,
    "app/domains/assessments_runtime/role_support.py": 1432,
    "app/domains/assessments_runtime/roles_management_routes.py": 1987,
    "app/domains/billing_webhooks/billing_routes.py": 865,
    "app/domains/workable_sync/routes.py": 1063,
    "app/services/agent_activation_readiness.py": 623,
    "app/services/assessment_invite_workable_handoff.py": 727,
    "app/services/candidate_feedback_engine.py": 1903,
    "app/services/cv_score_orchestrator.py": 1473,
    "app/services/fit_matching_service.py": 1605,
    "app/services/fraud_detection.py": 1190,
    "app/services/metered_anthropic_client.py": 1496,
    "app/services/pre_screen_decision_emitter.py": 1512,
    "app/services/workable_actions_service.py": 650,
    "app/services/interview_support_service.py": 504,
    "app/services/pricing_service.py": 553,
    "app/services/process_role_dispatch.py": 556,
    "app/services/role_activation_intent.py": 692,
    "app/services/task_spec_generator.py": 550,
    "app/services/task_spec_loader.py": 541,
    "app/services/usage_credit_reservations.py": 518,
    "app/services/workable_op_runner.py": 884,
    "app/tasks/agent_tasks.py": 1716,
    "app/tasks/assessment_tasks.py": 2132,
    "app/tasks/scoring_tasks.py": 1000,
    "app/mcp/handlers.py": 894,
}

# Newer release-safety checks consume a reason-bearing ratchet mapping. Keep it
# derived from the exact policy above so the two contracts cannot drift.
RATCHETED_FILES: dict[str, tuple[int, str]] = {
    path: (limit, "exact legacy burn-down ratchet")
    for path, limit in LEGACY_SIZE_BASELINES.items()
}
MERGE_HOTSPOTS = frozenset(
    {
        "app/main.py",
        "app/agent_chat/tools.py",
        "app/candidate_search/top_candidates.py",
        "app/models/__init__.py",
    }
)

# Compatibility for tooling that imports the historical name. It deliberately
# carries no exemption semantics; ``find_violations`` enforces every value.
ALLOWLIST = LEGACY_SIZE_BASELINES


def _has_endpoint_decorator(path: Path) -> bool:
    """Return whether a module declares or imperatively registers a route."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return True
    decorator_methods = {
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
    registration_methods = {
        "add_api_route",
        "add_api_websocket_route",
        "add_route",
        "add_websocket_route",
        "mount",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute) and target.attr in decorator_methods:
                    return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in registration_methods
        ):
            return True
    return False


def _target_files() -> list[Path]:
    """Return all strict route/service targets plus existing ratcheted files."""
    app = BACKEND_ROOT / "app"
    targets: set[Path] = set()
    api_v1 = app / "api" / "v1"
    if api_v1.exists():
        targets.update(path for path in api_v1.rglob("*.py") if path.is_file())
    services = app / "services"
    if services.exists():
        targets.update(path for path in services.rglob("*.py") if path.is_file())
    if app.exists():
        targets.update(path for path in app.rglob("*service.py") if path.is_file())
        targets.update(
            path
            for path in app.rglob("*.py")
            if path.is_file() and _has_endpoint_decorator(path)
        )
    for relative_path in RATCHETED_FILES:
        path = BACKEND_ROOT / relative_path
        if path.is_file():
            targets.add(path)
    return sorted(targets)


def find_violations() -> list[str]:
    """Return actionable size, growth, shrinkage, and stale-ratchet failures."""
    violations: list[str] = []
    seen: set[str] = set()
    strict_targets = set(_target_files())
    app_root = BACKEND_ROOT / "app"
    if not app_root.exists():
        return violations

    for path in sorted(candidate for candidate in app_root.rglob("*.py") if candidate.is_file()):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        baseline = LEGACY_SIZE_BASELINES.get(rel)
        if baseline is not None:
            seen.add(rel)
            if lines > baseline:
                violations.append(f"{rel} ({lines} LOC, max {baseline})")
            elif lines < baseline:
                violations.append(
                    f"{rel} shrunk: {lines} LOC (ratchet baseline {baseline}); "
                    "lower the baseline"
                )
            continue

        limit = SIZE_LIMIT if path in strict_targets else GENERAL_SIZE_LIMIT
        if lines > limit:
            scope = "route/service" if path in strict_targets else "general"
            violations.append(f"{rel} ({lines} LOC; {scope} limit {limit})")

    # Synthetic tests replace BACKEND_ROOT with a small fixture. Only the real
    # repository should report missing policy entries as stale.
    if BACKEND_ROOT.resolve() == CANONICAL_BACKEND_ROOT.resolve():
        for rel in sorted(set(LEGACY_SIZE_BASELINES) - seen):
            violations.append(f"stale baseline for missing/non-target file: {rel}")
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(
            "Backend file-size gate FAILED — route/service modules must stay "
            f"<= {SIZE_LIMIT} LOC, all other modules <= {GENERAL_SIZE_LIMIT} LOC, "
            "and exact ratchets may never regrow:"
        )
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(
        f"Backend file-size gate passed (route/service <= {SIZE_LIMIT} LOC; "
        f"all modules <= {GENERAL_SIZE_LIMIT} LOC; "
        f"{len(LEGACY_SIZE_BASELINES)} exact ratchets enforced)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
