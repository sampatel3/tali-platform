#!/usr/bin/env python3
"""Backend file-size gate for bloat-prone modules and merge hotspots.

New API route/service modules must stay at or below ``SIZE_LIMIT``. Existing
large modules and known merge hotspots have per-file burn-down caps: they may
shrink, but may not grow. This prevents an allowlist from becoming an unlimited
exception and makes concurrent edits to central composition files explicit.

Stdlib-only, so CI needs no pip install.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

SIZE_LIMIT = 500

# Value = (maximum LOC, reason). Keep caps at the checked-in baseline; lower a
# cap after an extraction. Do not increase one merely to land another feature.
RATCHETED_FILES: dict[str, tuple[int, str]] = {
    "app/components/assessments/service.py": (1422, "assessment orchestration"),
    "app/components/integrations/claude_agent/service.py": (
        732,
        "assessment interrogation service",
    ),
    "app/components/integrations/workable/sync_service.py": (2624, "Workable sync flow"),
    "app/components/integrations/workable/service.py": (
        801,
        "legacy Workable integration service",
    ),
    "app/domains/agentic/routes.py": (2479, "agent decisions API"),
    "app/domains/assessments_runtime/analytics_routes.py": (
        1971,
        "Mission Control reporting summary aggregator",
    ),
    "app/domains/assessments_runtime/applications_routes.py": (6206, "applications API"),
    "app/domains/assessments_runtime/candidate_runtime_routes.py": (
        894,
        "candidate runtime API",
    ),
    "app/domains/assessments_runtime/interview_feedback_routes.py": (
        558,
        "interview-feedback and scorecard lifecycle",
    ),
    "app/domains/assessments_runtime/pipeline_service.py": (
        1377,
        "assessment runtime orchestration",
    ),
    "app/domains/assessments_runtime/roles_management_routes.py": (
        2699,
        "roles and job-spec API",
    ),
    "app/domains/billing_webhooks/billing_routes.py": (871, "billing and webhook handlers"),
    "app/domains/workable_sync/routes.py": (1116, "legacy Workable sync API"),
    "app/services/fit_matching_service.py": (1607, "CV-to-role fit scoring pipeline"),
    "app/services/workable_actions_service.py": (650, "Workable write helpers"),
    "app/services/interview_support_service.py": (504, "interview pack builder"),
    "app/services/pricing_service.py": (552, "feature pricing and reservation tables"),
    # Central files outside the normal route/service glob. These were recurring
    # conflict-resolution hotspots and previously had no size protection.
    "app/main.py": (1319, "application and router composition"),
    "app/agent_chat/tools.py": (2337, "agent-chat tool surface"),
    "app/candidate_search/top_candidates.py": (1413, "candidate search orchestration"),
    "app/models/__init__.py": (396, "Alembic model metadata registry"),
}

MERGE_HOTSPOTS = frozenset(
    {
        "app/main.py",
        "app/agent_chat/tools.py",
        "app/candidate_search/top_candidates.py",
        "app/models/__init__.py",
    }
)


def _target_files() -> list[Path]:
    """Return default policy targets plus every existing ratcheted module."""
    app = BACKEND_ROOT / "app"
    targets: set[Path] = set()
    api_v1 = app / "api" / "v1"
    if api_v1.exists():
        targets.update(path for path in api_v1.rglob("*.py") if path.is_file())
    if app.exists():
        targets.update(app.rglob("*service.py"))
    domains = app / "domains"
    if domains.exists():
        targets.update(domains.rglob("*routes.py"))
    for relative_path in RATCHETED_FILES:
        path = BACKEND_ROOT / relative_path
        if path.is_file():
            targets.add(path)
    return sorted(targets)


def find_violations() -> list[str]:
    """Return a descriptive entry for every file above its applicable cap."""
    violations: list[str] = []
    for path in _target_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        limit = RATCHETED_FILES.get(rel, (SIZE_LIMIT, "default policy"))[0]
        if lines > limit:
            violations.append(f"{rel} ({lines} LOC, max {limit})")
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(
            "Backend file-size gate FAILED — API/service modules must stay <= "
            f"{SIZE_LIMIT} LOC and ratcheted hotspots may not grow:"
        )
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(
        "Backend file-size gate passed "
        f"(<= {SIZE_LIMIT} LOC by default, {len(RATCHETED_FILES)} ratcheted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
