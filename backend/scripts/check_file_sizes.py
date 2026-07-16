#!/usr/bin/env python3
"""Backend file-size gate — keeps API/service and general modules bounded.

This is the single source of truth for the file-size policy that
``tests/test_ci_architecture_gates.py`` asserts AND that CI enforces. New route
and service modules must stay <= ``SIZE_LIMIT`` lines; every other application
module stays <= ``GENERAL_SIZE_LIMIT`` so a rename cannot evade the ratchet.
Legacy oversized files have exact baselines: they may not grow, and their
baseline must be lowered whenever they shrink. This turns the legacy list into
a real burn-down ratchet.

Stdlib-only, so CI needs no pip install (same as the alembic head check).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

SIZE_LIMIT = 500
GENERAL_SIZE_LIMIT = 1000

# Files awaiting a split. Values are exact LOC baselines, not exemptions.
# Do not add new entries for new/growing modules: split them instead. Entries
# added when this gate's scope expands must pin the exact pre-existing size.
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
    "app/services/cv_score_orchestrator.py": 1477,
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
    "app/tasks/scoring_tasks.py": 1011,
    "app/mcp/handlers.py": 894,
}

# Compatibility for tooling that imports the old name. It deliberately carries
# no exemption semantics; ``find_violations`` enforces every value above.
ALLOWLIST = LEGACY_SIZE_BASELINES


def _target_files() -> list[Path]:
    """Every decorated route file and service module — the bloat-prone surfaces."""
    app = BACKEND_ROOT / "app"
    targets: set[Path] = set()
    api_v1 = app / "api" / "v1"
    if api_v1.exists():
        targets.update(p for p in api_v1.rglob("*.py") if p.is_file())
    service_root = app / "services"
    if service_root.exists():
        targets.update(p for p in service_root.rglob("*.py") if p.is_file())
    # Component/domain service modules live outside ``app/services``.
    targets.update(app.rglob("*service.py"))
    targets.update(
        path for path in app.rglob("*.py") if _has_endpoint_decorator(path)
    )
    return sorted(targets)


def _has_endpoint_decorator(path: Path) -> bool:
    """Return whether a module declares a FastAPI HTTP route.

    Detect the decorator shape rather than trusting filenames such as
    ``*routes.py``; otherwise a new ``api.py`` can evade the size ratchet.
    Syntax failures are handled by the compile/Ruff gates, so return True here
    to keep an unparsable route-like module in scope rather than silently skip.
    """
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


def find_violations() -> list[str]:
    """Return actionable violations for size regressions or stale baselines."""
    violations: list[str] = []
    seen: set[str] = set()
    strict_targets = set(_target_files())
    app_root = BACKEND_ROOT / "app"
    for path in sorted(p for p in app_root.rglob("*.py") if p.is_file()):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        baseline = LEGACY_SIZE_BASELINES.get(rel)
        if baseline is not None:
            seen.add(rel)
            if lines != baseline:
                direction = "grew" if lines > baseline else "shrunk"
                violations.append(
                    f"{rel} {direction}: {lines} LOC (ratchet baseline {baseline}); "
                    + ("split the growth" if lines > baseline else "lower the baseline")
                )
            continue
        limit = SIZE_LIMIT if path in strict_targets else GENERAL_SIZE_LIMIT
        if lines > limit:
            scope = "route/service" if path in strict_targets else "general"
            violations.append(f"{rel} ({lines} LOC; {scope} limit {limit})")
    for rel in sorted(set(LEGACY_SIZE_BASELINES) - seen):
        violations.append(f"stale baseline for missing/non-target file: {rel}")
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(
            "Backend file-size gate FAILED — new API/service paths must stay "
            f"<= {SIZE_LIMIT} LOC, all other modules <= {GENERAL_SIZE_LIMIT} LOC, "
            "and legacy baselines may never grow:"
        )
        for v in violations:
            print(f"  - {v}")
        return 1
    print(
        f"Backend file-size gate passed (route/service <= {SIZE_LIMIT} LOC; "
        f"all modules <= {GENERAL_SIZE_LIMIT} LOC; "
        f"{len(LEGACY_SIZE_BASELINES)} legacy baselines ratcheted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
