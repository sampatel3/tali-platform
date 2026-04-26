"""Smoke test for the cv_match_v3.0 pipeline against the placeholder fixture.

Run from the repo root::

    cd backend && ../scripts/cv_match_smoke_test.py
    # or, with explicit python:
    cd backend && python ../scripts/cv_match_smoke_test.py

What this checks:
- The new pipeline imports cleanly.
- A placeholder CV + JD round-trips through ``run_cv_match`` and produces
  a valid ``CVMatchOutput`` with ``scoring_status == "ok"``.
- The output JSON serializes via ``model_dump(mode="json")``.

Requires ``ANTHROPIC_API_KEY`` to be set. Without it, exits non-zero with a
clear message — this script is intentionally not a unit test, and should
not be run in CI.

Cost: one Haiku 4.5 call per invocation (~$0.003). Cached on subsequent
runs against identical inputs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    backend_dir = repo_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    try:
        from app.cv_matching import (
            MODEL_VERSION,
            PROMPT_VERSION,
            Priority,
            RequirementInput,
            run_cv_match,
        )
        from app.platform.config import settings
    except Exception as exc:
        print(f"ERROR: failed to import cv_matching module: {exc}", file=sys.stderr)
        return 2

    if not settings.ANTHROPIC_API_KEY:
        print(
            "ERROR: ANTHROPIC_API_KEY is not set. "
            "Set it in backend/.env or the environment and re-run.",
            file=sys.stderr,
        )
        return 2

    fixtures_dir = (
        backend_dir / "app" / "cv_matching" / "evals" / "fixtures"
    )
    cv_text = (fixtures_dir / "cvs" / "placeholder_eng.txt").read_text(encoding="utf-8")
    jd_text = (fixtures_dir / "jds" / "placeholder_eng.txt").read_text(encoding="utf-8")

    requirements = [
        RequirementInput(
            id="req_1",
            requirement="5+ years building data pipelines on AWS",
            priority=Priority.MUST_HAVE,
            evidence_hints=["AWS", "Glue", "EMR", "ETL"],
        ),
        RequirementInput(
            id="req_2",
            requirement="Strong Python and SQL",
            priority=Priority.MUST_HAVE,
            evidence_hints=["Python", "SQL"],
        ),
    ]

    print(f"Running cv_match_v3.0 smoke test (prompt={PROMPT_VERSION}, model={MODEL_VERSION})")
    output = run_cv_match(cv_text, jd_text, requirements)

    print(f"  scoring_status:           {output.scoring_status.value}")
    print(f"  recommendation:           {output.recommendation.value}")
    print(f"  role_fit_score:           {output.role_fit_score}")
    print(f"  cv_fit_score:             {output.cv_fit_score}")
    print(f"  requirements_match_score: {output.requirements_match_score}")
    print(f"  injection_suspected:      {output.injection_suspected}")
    print(f"  trace_id:                 {output.trace_id}")
    if output.error_reason:
        print(f"  error_reason:             {output.error_reason}")

    if output.scoring_status.value != "ok":
        print("FAIL: scoring_status was not ok", file=sys.stderr)
        return 1

    # Round-trip serialization sanity check.
    blob = output.model_dump(mode="json")
    json.dumps(blob)  # must not raise
    print(f"\nSerialized blob: {len(json.dumps(blob))} bytes")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
