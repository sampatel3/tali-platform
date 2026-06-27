#!/usr/bin/env python3
"""Backend file-size gate — keeps API route files and service modules small.

This is the single source of truth for the file-size policy that
``tests/test_ci_architecture_gates.py`` asserts AND that CI now enforces (the
backend mirror of the frontend architecture gate). Route/service modules must
stay <= ``SIZE_LIMIT`` lines unless explicitly allowlisted. The allowlist is a
burn-down list of files that predate the gate — fix the split, don't grow the
list.

Stdlib-only, so CI needs no pip install (same as the alembic head check).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

SIZE_LIMIT = 500

# Files awaiting a split, kept explicitly so new bloat can't sneak in. New
# entries are not welcome — split the file (into a package) instead of
# expanding this list. Value = why it's still here.
ALLOWLIST: dict[str, str] = {
    "app/components/assessments/service.py": "assessment orchestration",
    "app/components/integrations/claude_agent/service.py": "assessment interrogation service; 552 LOC after #716, pending split",
    "app/components/integrations/workable/sync_service.py": "Workable sync flow",
    "app/components/integrations/workable/service.py": "legacy Workable integration service",
    "app/domains/agentic/routes.py": "agent decisions queue + status + run-now (cohesive surface, 7 LOC over)",
    "app/domains/assessments_runtime/analytics_routes.py": "Mission Control reporting summary aggregator",
    "app/domains/assessments_runtime/applications_routes.py": "applications API",
    "app/domains/assessments_runtime/candidate_runtime_routes.py": "candidate runtime API",
    "app/domains/assessments_runtime/pipeline_service.py": "assessment runtime pipeline orchestration",
    "app/domains/assessments_runtime/roles_management_routes.py": "roles + job-spec upload API",
    "app/domains/billing_webhooks/billing_routes.py": "Stripe + credit-pack billing routes (TODO: split webhook handlers)",
    "app/domains/workable_sync/routes.py": "legacy Workable sync API",
    "app/services/fit_matching_service.py": "CV-to-role fit scoring pipeline",
    "app/services/interview_support_service.py": "interview pack builder (1 LOC over after chip-helper extraction)",
    "app/services/pricing_service.py": "single source of truth for the per-feature pricing + reservation tables; grows one entry per metered Feature",
}


def _target_files() -> list[Path]:
    """API route files and service modules — the bloat-prone surfaces."""
    app = BACKEND_ROOT / "app"
    targets: set[Path] = set()
    api_v1 = app / "api" / "v1"
    if api_v1.exists():
        targets.update(p for p in api_v1.rglob("*.py") if p.is_file())
    targets.update(app.rglob("*service.py"))
    targets.update((app / "domains").rglob("*routes.py"))
    return sorted(targets)


def find_violations() -> list[str]:
    """Return ``"rel/path.py (N LOC)"`` for each over-limit, non-allowlisted file."""
    violations: list[str] = []
    for path in _target_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        if lines > SIZE_LIMIT:
            violations.append(f"{rel} ({lines} LOC)")
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(f"Backend file-size gate FAILED — API/service paths must stay <= {SIZE_LIMIT} LOC "
              "unless allowlisted in scripts/check_file_sizes.py:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print(f"Backend file-size gate passed (<= {SIZE_LIMIT} LOC, {len(ALLOWLIST)} allowlisted).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
