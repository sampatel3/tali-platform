"""Eval harness: run golden cases through ``run_cv_match`` and check assertions.

Each case in ``golden_cases.yaml`` lists a CV file, JD file, optional
recruiter requirements, and expected outcomes. The runner produces a
``CVMatchOutput``; we assert on:

1. ``recommendation`` is in the expected set
2. ``role_fit_score`` is within the expected range
3. specified ``must_meet_requirements`` have status=met
4. (optional) per-dimension ranges via ``expected.dimension_score_ranges``

Snapshot of the full result blob is written to ``baseline_results/``.
``--metrics-full`` additionally prints Krippendorff α / Cohen's κ /
Spearman ρ / Brier / ECE against ``expected.recruiter_action`` labels.
``--baseline-md`` autogens a markdown summary alongside the JSON snapshot.
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

    # recommendation_in: kept for backwards-compat but treated as advisory.
    # The runner no longer auto-derives a recommendation (the UI does that
    # dynamically against the per-role reject threshold), so output.recommendation
    # is typically None. We still surface the assertion when both sides have a value.
    expected_recs = expected.get("recommendation_in") or []
    if expected_recs and output.recommendation is not None:
        if output.recommendation.value not in expected_recs:
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

    dim_ranges = expected.get("dimension_score_ranges") or {}
    if dim_ranges:
        dimension_scores = getattr(output, "dimension_scores", None)
        if dimension_scores is None:
            failures.append(
                "dimension_score_ranges asserted but output has no dimension_scores"
            )
        else:
            for dim_name, rng in dim_ranges.items():
                lo, hi = rng[0], rng[1]
                actual_val = getattr(dimension_scores, dim_name, None)
                if actual_val is None:
                    failures.append(f"unknown dimension {dim_name!r}")
                    continue
                if not (lo <= actual_val <= hi):
                    failures.append(
                        f"dimension {dim_name}={actual_val} outside [{lo}, {hi}]"
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
        recommendation=(
            output.recommendation.value if output.recommendation is not None else "—"
        ),
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


def _print_agreement_metrics(
    cases: list[dict], results: list[CaseResult]
) -> None:
    """Compute and print Krippendorff α, Cohen's κ, Spearman ρ, Brier, ECE."""
    from .metrics import (
        brier_score,
        cohens_kappa,
        expected_calibration_error,
        krippendorff_alpha_nominal,
        spearman_rho,
    )

    by_id = {c["case_id"]: c for c in cases}
    advance_set = {"yes", "strong_yes", "advance"}

    pred_actions: list[bool] = []
    label_actions: list[bool] = []

    pred_scores: list[float] = []
    label_ranks: list[float] = []

    pred_probs: list[float] = []
    label_probs: list[bool] = []

    for r in results:
        case = by_id.get(r.case_id, {})
        expected = case.get("expected", {}) or {}
        recr = expected.get("recruiter_action")
        if recr:
            pred_actions.append(r.recommendation in advance_set)
            label_actions.append(recr in advance_set or recr == "advance")
        recr_rank = expected.get("recruiter_rank")
        if recr_rank is not None:
            pred_scores.append(r.role_fit_score)
            label_ranks.append(float(recr_rank))
        cal = (r.output or {}).get("calibrated_p_advance")
        if cal is not None and recr is not None:
            pred_probs.append(float(cal))
            label_probs.append(recr in advance_set or recr == "advance")

    print("Agreement metrics")
    print("-----------------")
    if len(pred_actions) >= 2:
        kappa = cohens_kappa(
            ["adv" if x else "rej" for x in pred_actions],
            ["adv" if x else "rej" for x in label_actions],
        )
        alpha = krippendorff_alpha_nominal(
            [
                ["adv" if x else "rej" for x in pred_actions],
                ["adv" if x else "rej" for x in label_actions],
            ]
        )
        print(f"Cohen's κ      : {kappa:+.4f}    (n={len(pred_actions)})")
        print(f"Krippendorff α : {alpha:+.4f}")
    else:
        print("Cohen's κ      : n/a (need expected.recruiter_action on >= 2 cases)")
    if len(pred_scores) >= 2:
        rho = spearman_rho(pred_scores, label_ranks)
        print(f"Spearman ρ     : {rho:+.4f}    (n={len(pred_scores)})")
    else:
        print("Spearman ρ     : n/a (need expected.recruiter_rank on >= 2 cases)")
    if pred_probs:
        bs = brier_score(pred_probs, label_probs)
        ece = expected_calibration_error(pred_probs, label_probs)
        print(f"Brier          : {bs:.4f}    (n={len(pred_probs)})")
        print(f"ECE            : {ece:.4f}")
    else:
        print(
            "Brier / ECE    : n/a (need calibrated_p_advance + recruiter_action)"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CV match golden eval cases.")
    parser.add_argument(
        "--cases-file",
        default=str(GOLDEN_FILE),
        help="Path to golden_cases.yaml.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the result cache (forces fresh Claude calls).",
    )
    parser.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip writing the baseline snapshot.",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="If set, only run cases whose case_id contains this substring.",
    )
    parser.add_argument(
        "--metrics-full",
        action="store_true",
        help=(
            "Compute and print agreement metrics (Krippendorff α, Cohen's κ, "
            "Spearman ρ, Brier, ECE) against expected.recruiter_action labels."
        ),
    )
    parser.add_argument(
        "--baseline-md",
        action="store_true",
        help="Autogen a markdown summary alongside the JSON snapshot.",
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

    if args.metrics_full:
        _print_agreement_metrics(cases, results)

    if not args.no_snapshot:
        snapshot_path = _snapshot(results)
        print(f"Baseline snapshot: {snapshot_path}")
        if args.baseline_md:
            from .baseline_diff import write_markdown_report

            md_path = write_markdown_report(snapshot_path)
            print(f"Markdown summary:  {md_path}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
