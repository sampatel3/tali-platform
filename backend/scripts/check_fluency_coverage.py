#!/usr/bin/env python3
"""CI gate: every production task rubric must grade all five fluency axes.

We publish a five-axis scorecard per candidate. A rubric that only reaches two
axes still renders five spokes, with the ungraded ones backfilled from
behavioural heuristics — which is telemetry, not a grade. This gate makes that
state unshippable.

Imports only ``app.components.assessments.fluency_axes``, which is stdlib-only,
so this gate runs before ``pip install`` in CI and fails fast. The companion
check — that every lens the spec validator accepts has an explicit axis entry —
needs the loader's vocabulary and so lives in the backend test suite
(tests/test_unit_fluency_axes.py).

Run: python scripts/check_fluency_coverage.py [tasks_dir]
Exit 0 = clean, 1 = at least one violation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.components.assessments.fluency_axes import (  # noqa: E402
    FLUENCY_AXES,
    axes_covered_by_rubric,
    validate_fluency_coverage,
)


def main(argv: list[str]) -> int:
    tasks_dir = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "tasks"
    failures: list[str] = []

    task_files = sorted(tasks_dir.glob("*.json"))
    if not task_files:
        print(f"FAIL  no task specs found in {tasks_dir}")
        return 1

    for path in task_files:
        try:
            spec = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path.name}: unreadable ({exc})")
            continue

        rubric = spec.get("evaluation_rubric")
        exemption = spec.get("fluency_coverage_exemption")
        errors = validate_fluency_coverage(rubric, exemption)
        covered = axes_covered_by_rubric(rubric)
        mark = "FAIL" if errors else ("EXEMPT" if exemption else "ok")
        print(f"{mark:6s} {path.name:48s} {len(covered)}/{len(FLUENCY_AXES)} axes")
        for err in errors:
            failures.append(f"{path.name}: {err}")

    if failures:
        print(f"\n{len(failures)} fluency-coverage violation(s):\n")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\nAll {len(task_files)} task specs grade all {len(FLUENCY_AXES)} fluency axes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
