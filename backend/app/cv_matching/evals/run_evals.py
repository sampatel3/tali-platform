"""Eval harness: run golden cases through ``run_cv_match`` and check assertions.

Each case in ``golden_cases.yaml`` lists a CV file, JD file, optional
recruiter requirements, and expected outcomes. The runner produces a
``CVMatchOutput``; we assert on three criteria per the handover:

1. ``recommendation`` is in the expected set
2. ``role_fit_score`` is within the expected range
3. specified ``must_meet_requirements`` have status=met

Snapshot of the full result blob is written to ``baseline_results/`` so the
next prompt version can diff against the prior baseline before promotion.

Exit code is 0 if all cases pass, non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .. import (
    PROMPT_VERSION,
    Priority,
    RequirementInput,
    Status,
)
from ..runner import run_cv_match
from ..schemas import CVMatchOutput

EVALS_DIR = Path(__file__).resolve().parent
GOLDEN_FILE = EVALS_DIR / "golden_cases.yaml"
BASELINE_DIR = EVALS_DIR / "baseline_results"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    recommendation: str
    role_fit_score: float
    failures: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)


def _load_text(rel_path: str) -> str:
    path = EVALS_DIR / rel_path
    return path.read_text(encoding="utf-8")


def _build_requirements(raw: list[dict] | None) -> list[RequirementInput]:
    if not raw:
        return []
    out: list[RequirementInput] = []
    for item in raw:
        # Tolerate priorities passed as plain strings.
        priority = item.get("priority", "must_have")
        if isinstance(priority, str):
            priority = Priority(priority)
        out.append(
            RequirementInput(
                id=item["id"],
                requirement=item["requirement"],
                priority=priority,
                rationale=item.get("rationale", ""),
                evidence_hints=item.get("evidence_hints", []) or [],
                acceptable_alternatives=item.get("acceptable_alternatives", []) or [],
                depth_signal=item.get("depth_signal", ""),
                disqualifying_if_missing=bool(
                    item.get("disqualifying_if_missing", False)
                ),
                flag_only=bool(item.get("flag_only", False)),
            )
        )
    return out


def _check_case(case: dict, output: CVMatchOutput) -> list[str]:
    """Return a list of failure messages. Empty list means pass."""
    failures: list[str] = []
    expected = case.get("expected", {}) or {}

    expected_recs = expected.get("recommendation_in") or []
    if expected_recs and output.recommendation.value not in expected_recs:
        failures.append(
            f"recommendation={output.recommendation.value} not in {expected_recs}"
        )

    score_range = expected.get("role_fit_score_range")
    if score_range:
        lo, hi = score_range[0], score_range[1]
        if not (lo <= output.role_fit_score <= hi):
            failures.append(
                f"role_fit_score={output.role_fit_score} outside [{lo}, {hi}]"
            )

    must_meet = expected.get("must_meet_requirements", []) or []
    if must_meet:
        statuses = {a.requirement_id: a.status for a in output.requirements_assessment}
        for rid in must_meet:
            actual = statuses.get(rid)
            if actual != Status.MET:
                failures.append(
                    f"requirement {rid} status={getattr(actual, 'value', None)}, expected met"
                )

    return failures


def run_one(case: dict, *, skip_cache: bool = False) -> CaseResult:
    cv_text = _load_text(case["cv_file"])
    jd_text = _load_text(case["jd_file"])
    requirements = _build_requirements(case.get("additional_requirements"))

    output = run_cv_match(
        cv_text,
        jd_text,
        requirements,
        skip_cache=skip_cache,
    )

    failures = _check_case(case, output)
    return CaseResult(
        case_id=case["case_id"],
        passed=not failures,
        recommendation=output.recommendation.value,
        role_fit_score=output.role_fit_score,
        failures=failures,
        output=output.model_dump(mode="json"),
    )


def _print_summary(results: list[CaseResult]) -> None:
    print()
    print(f"{'case_id':<32} {'passed':<6} {'recommendation':<16} {'score':<6} {'failures'}")
    print("-" * 110)
    for r in results:
        marker = "✓" if r.passed else "✗"
        failures = "; ".join(r.failures) if r.failures else ""
        print(
            f"{r.case_id:<32} {marker:<6} {r.recommendation:<16} "
            f"{r.role_fit_score:<6.1f} {failures}"
        )
    passed = sum(1 for r in results if r.passed)
    print("-" * 110)
    print(f"{passed}/{len(results)} cases passed")
    print()


def _snapshot(results: list[CaseResult]) -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BASELINE_DIR / f"{PROMPT_VERSION}_{timestamp}.json"
    payload = {
        "prompt_version": PROMPT_VERSION,
        "timestamp": timestamp,
        "results": [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "recommendation": r.recommendation,
                "role_fit_score": r.role_fit_score,
                "failures": r.failures,
                "output": r.output,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CV match golden eval cases.")
    parser.add_argument(
        "--cases-file",
        default=str(GOLDEN_FILE),
        help="Path to golden_cases.yaml (default: package-relative file).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the result cache (forces fresh Claude calls).",
    )
    parser.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip writing the baseline snapshot (for local iteration).",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="If set, only run cases whose case_id contains this substring.",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases_file)
    if not cases_path.exists():
        print(f"ERROR: cases file not found: {cases_path}", file=sys.stderr)
        return 2

    cases = yaml.safe_load(cases_path.read_text(encoding="utf-8")) or []
    if args.filter:
        cases = [c for c in cases if args.filter in c.get("case_id", "")]
    if not cases:
        print("No cases to run.", file=sys.stderr)
        return 2

    print(f"Running {len(cases)} case(s) against prompt {PROMPT_VERSION}...")
    results = [run_one(c, skip_cache=args.no_cache) for c in cases]
    _print_summary(results)

    if not args.no_snapshot:
        snapshot_path = _snapshot(results)
        print(f"Baseline snapshot: {snapshot_path}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
