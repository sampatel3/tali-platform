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
    PROMPT_VERSION_V4,
    Priority,
    RequirementInput,
    Status,
)
from ..runner import run_cv_match
from ..schemas import CVMatchOutput

EVALS_DIR = Path(__file__).resolve().parent
GOLDEN_FILE = EVALS_DIR / "golden_cases.yaml"
BASELINE_DIR = EVALS_DIR / "baseline_results"

_VERSION_TO_PROMPT_VERSION = {
    "v3": PROMPT_VERSION,
    "v4.1": PROMPT_VERSION_V4,
}


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

    # v4.2 per-dimension expected ranges (RALPH 2.11).
    # ``expected.dimension_score_ranges: {dim_name: [lo, hi]}``. Only
    # checked when the output carries dimension_scores (i.e. v4.2 path).
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


def run_one(
    case: dict,
    *,
    skip_cache: bool = False,
    version: str | None = None,
) -> CaseResult:
    """Run one golden case end-to-end.

    ``version`` is an optional pipeline selector ("v3" or "v4.1"). When
    omitted, ``run_cv_match`` falls back to its own default (the
    ``USE_CV_MATCH_V4_PHASE1`` flag, then v3).
    """
    cv_text = _load_text(case["cv_file"])
    jd_text = _load_text(case["jd_file"])
    requirements = _build_requirements(case.get("additional_requirements"))

    extra_kwargs = {}
    if version is not None:
        extra_kwargs["version"] = version

    output = run_cv_match(
        cv_text,
        jd_text,
        requirements,
        skip_cache=skip_cache,
        **extra_kwargs,
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


def _snapshot(
    results: list[CaseResult], *, version: str | None = None
) -> Path:
    """Write a baseline snapshot. Filename is keyed on prompt_version so v3
    and v4.1 snapshots live side by side in baseline_results/."""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prompt_version_for_file = (
        _VERSION_TO_PROMPT_VERSION.get(version or "", PROMPT_VERSION)
        if version
        else PROMPT_VERSION
    )
    path = BASELINE_DIR / f"{prompt_version_for_file}_{timestamp}.json"
    payload = {
        "prompt_version": prompt_version_for_file,
        "version": version or "v3",
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


def _print_comparison(
    *,
    v3_results: list[CaseResult],
    v4_results: list[CaseResult],
) -> None:
    """When both versions are run, print a per-case head-to-head."""
    by_id_v3 = {r.case_id: r for r in v3_results}
    by_id_v4 = {r.case_id: r for r in v4_results}
    case_ids = sorted(set(by_id_v3) | set(by_id_v4))
    print()
    print(
        f"{'case_id':<32} {'v3':<8} {'rec_v3':<14} {'v4.1':<8} {'rec_v4':<14} "
        f"{'Δscore':<8}"
    )
    print("-" * 90)
    for cid in case_ids:
        r3 = by_id_v3.get(cid)
        r4 = by_id_v4.get(cid)
        s3 = f"{r3.role_fit_score:.1f}" if r3 else "-"
        s4 = f"{r4.role_fit_score:.1f}" if r4 else "-"
        rec3 = r3.recommendation if r3 else "-"
        rec4 = r4.recommendation if r4 else "-"
        delta = (
            f"{r4.role_fit_score - r3.role_fit_score:+.1f}"
            if r3 and r4
            else "-"
        )
        print(f"{cid:<32} {s3:<8} {rec3:<14} {s4:<8} {rec4:<14} {delta:<8}")
    print()


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
    parser.add_argument(
        "--version",
        choices=("v3", "v4.1", "v4.2", "v4.3", "both"),
        default="v3",
        help=(
            "Pipeline version to evaluate. 'v3' (default), 'v4.1', 'v4.2', "
            "'v4.3', or 'both' (runs v3 + v4.1 head-to-head)."
        ),
    )
    parser.add_argument(
        "--metrics-full",
        action="store_true",
        help=(
            "Compute and print agreement metrics (Krippendorff α, Cohen's κ, "
            "Spearman ρ, Brier, ECE) against expected.recruiter_action "
            "labels in golden_cases.yaml. Skipped per metric when the "
            "required label is absent."
        ),
    )
    parser.add_argument(
        "--counterfactual-probes",
        action="store_true",
        help=(
            "For each base case, generate 8 counterfactual variants "
            "(name / school / zip / graduation-year swaps) and assert "
            "pairwise flip rate <= 5%% AND mean |Δscore| <= 0.05. "
            "Wires into CI as the fairness gate (RALPH 4.3)."
        ),
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

    if args.version == "both":
        print(f"Running {len(cases)} case(s) — v3 and v4.1 head-to-head...")
        v3_results = [
            run_one(c, skip_cache=args.no_cache, version="v3") for c in cases
        ]
        v4_results = [
            run_one(c, skip_cache=args.no_cache, version="v4.1") for c in cases
        ]
        _print_summary(v3_results)
        _print_summary(v4_results)
        _print_comparison(v3_results=v3_results, v4_results=v4_results)
        if not args.no_snapshot:
            v3_path = _snapshot(v3_results, version="v3")
            v4_path = _snapshot(v4_results, version="v4.1")
            print(f"Baseline snapshots: {v3_path}, {v4_path}")
        all_passed = all(r.passed for r in v3_results) and all(
            r.passed for r in v4_results
        )
        return 0 if all_passed else 1

    target_prompt = _VERSION_TO_PROMPT_VERSION.get(args.version, PROMPT_VERSION)
    print(f"Running {len(cases)} case(s) against prompt {target_prompt}...")
    results = [
        run_one(c, skip_cache=args.no_cache, version=args.version) for c in cases
    ]
    _print_summary(results)

    if args.metrics_full:
        _print_agreement_metrics(cases, results)

    counterfactual_failed = False
    if args.counterfactual_probes:
        counterfactual_failed = _run_counterfactual_probes(
            cases, version=args.version, skip_cache=args.no_cache
        )

    if not args.no_snapshot:
        snapshot_path = _snapshot(results, version=args.version)
        print(f"Baseline snapshot: {snapshot_path}")

    return 0 if all(r.passed for r in results) and not counterfactual_failed else 1


def _run_counterfactual_probes(
    cases: list[dict],
    *,
    version: str,
    skip_cache: bool,
) -> bool:
    """Run probe assertions for every case. Returns True on any failure."""
    from ..fairness.probes import (
        generate_probes,
        pairwise_flip_rate,
        score_delta,
    )

    PAIR_FLIP_THRESHOLD = 0.05
    SCORE_DELTA_THRESHOLD = 0.05

    print("Counterfactual fairness probes")
    print("------------------------------")
    failed = False
    for case in cases:
        case_id = case.get("case_id", "<unknown>")
        cv_text = _load_text(case["cv_file"])
        probes = generate_probes(case_id, cv_text, n=8)
        recommendations: list[str] = []
        scores: list[float] = []
        for probe in probes:
            # Build a synthetic case by overriding cv_file with the
            # variant text. We do this by writing the variant CV to a
            # tmp path the harness can load.
            variant_case = dict(case)
            variant_case["cv_file"] = _stash_variant_cv(
                probe.probe_id, probe.cv_text
            )
            try:
                result = run_one(
                    variant_case, skip_cache=skip_cache, version=version
                )
                recommendations.append(result.recommendation)
                scores.append(result.role_fit_score)
            except Exception as exc:  # pragma: no cover — defensive
                print(f"  case={case_id} probe={probe.probe_id} ERROR: {exc}")
                failed = True
                continue

        flip = pairwise_flip_rate(recommendations)
        mean_d, max_d = score_delta(scores)
        flip_marker = "FAIL" if flip > PAIR_FLIP_THRESHOLD else "ok"
        delta_marker = "FAIL" if mean_d > SCORE_DELTA_THRESHOLD else "ok"
        if flip > PAIR_FLIP_THRESHOLD or mean_d > SCORE_DELTA_THRESHOLD:
            failed = True
        print(
            f"  case={case_id} flip={flip:.3f} [{flip_marker}] "
            f"mean_Δ={mean_d:.4f} max_Δ={max_d:.4f} [{delta_marker}]"
        )

    print()
    return failed


_VARIANT_TMP_DIR = EVALS_DIR / "fixtures" / "_counterfactual_tmp"


def _stash_variant_cv(probe_id: str, cv_text: str) -> str:
    """Write a probe's CV text under a deterministic relative path.

    The path is returned relative to ``EVALS_DIR`` so the existing
    ``_load_text`` helper resolves it correctly.
    """
    _VARIANT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = _VARIANT_TMP_DIR / f"{probe_id}.txt"
    path.write_text(cv_text, encoding="utf-8")
    return str(path.relative_to(EVALS_DIR))


def _print_agreement_metrics(
    cases: list[dict], results: list[CaseResult]
) -> None:
    """Compute and print Krippendorff α, Cohen's κ, Spearman ρ, Brier, ECE.

    Each metric is computed only when the necessary labels exist on at
    least 2 cases. Otherwise the row prints "n/a (need …)".
    """
    from .metrics import (
        brier_score,
        cohens_kappa,
        expected_calibration_error,
        krippendorff_alpha_nominal,
        spearman_rho,
    )

    by_id = {c["case_id"]: c for c in cases}
    advance_set = {"yes", "strong_yes", "advance"}

    pred_actions: list[bool] = []  # advanced?
    label_actions: list[bool] = []

    pred_scores: list[float] = []
    label_ranks: list[float] = []  # recruiter rank within case set

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
        # Calibrated p_advance for Brier / ECE.
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


if __name__ == "__main__":
    sys.exit(main())
