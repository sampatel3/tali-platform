"""Shadow re-score harness — validate a scoring change before it touches prod.

Every change that can move the authoritative assessment score (flipping
``ASSESSMENT_GRADER_PROCESS_TRACE``, adding a weighted rubric dimension, a
new grader lens) must be shadow-validated first: re-grade a sample of past
assessments under the new config, compare against the baseline, and confirm
it doesn't pathologically re-rank candidates. See
docs/ASSESSMENT_AI_NATIVE_IMPL_PLAN.md (PR-A).

Default mode isolates the **process-trace flag**: each sampled assessment is
re-graded twice from the SAME reconstructed artifacts — once with the trace
OFF (baseline), once ON (candidate) — so the only variable is whether the
grader sees the agent's tool actions + git diff. (Historical repo files are
not fully persisted, so deliverable-lens re-grades are approximate; because
both runs share the identical artifacts, the *delta* stays meaningful.)

Run from a worktree with prod DB + API key configured:

    DATABASE_URL='postgresql://...' ANTHROPIC_API_KEY='sk-...' \
        python -m scripts.shadow_rescore_assessments --limit 30

    # cheap eligibility check, no Anthropic calls:
    DATABASE_URL='postgresql://...' python -m scripts.shadow_rescore_assessments --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("shadow_rescore")


# ---- Pure helpers (no app / DB / network deps — unit-tested) ----------------


def band(score_100: Optional[float]) -> str:
    """Coarse hire-signal band for an overall 0-100 score."""
    if score_100 is None:
        return "none"
    if score_100 < 50:
        return "poor"
    if score_100 < 80:
        return "good"
    return "excellent"


def compare_runs(
    assessment_id: int,
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """Diff one assessment's baseline vs candidate grading run.

    Each run is ``{"overall": float, "dimensions": {id: score10}, "fluency_4d": {...}}``.
    """
    b_overall = baseline.get("overall")
    c_overall = candidate.get("overall")
    overall_delta = (
        round(float(c_overall) - float(b_overall), 2)
        if b_overall is not None and c_overall is not None
        else None
    )
    dim_deltas: Dict[str, float] = {}
    for dim_id, b_score in (baseline.get("dimensions") or {}).items():
        c_score = (candidate.get("dimensions") or {}).get(dim_id)
        if b_score is not None and c_score is not None:
            dim_deltas[dim_id] = round(float(c_score) - float(b_score), 2)
    return {
        "assessment_id": assessment_id,
        "baseline_overall": b_overall,
        "candidate_overall": c_overall,
        "overall_delta": overall_delta,
        "band_flip": band(b_overall) != band(c_overall),
        "baseline_band": band(b_overall),
        "candidate_band": band(c_overall),
        "dimension_deltas": dim_deltas,
    }


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman rank correlation, pure-python (no scipy). None if degenerate."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None

    def ranks(vals: List[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0  # average rank for ties (1-based)
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    denx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    deny = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if denx == 0 or deny == 0:
        return None
    return round(num / (denx * deny), 4)


def summarize_comparisons(comparisons: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-assessment comparisons into a go/no-go summary."""
    scored = [c for c in comparisons if c.get("overall_delta") is not None]
    deltas = [c["overall_delta"] for c in scored]
    band_flips = [c for c in scored if c.get("band_flip")]
    b_overalls = [float(c["baseline_overall"]) for c in scored]
    c_overalls = [float(c["candidate_overall"]) for c in scored]
    return {
        "n": len(comparisons),
        "n_scored": len(scored),
        "mean_abs_delta": round(sum(abs(d) for d in deltas) / len(deltas), 2) if deltas else None,
        "max_abs_delta": round(max((abs(d) for d in deltas), default=0.0), 2) if deltas else None,
        "mean_signed_delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
        "n_band_flips": len(band_flips),
        "band_flip_ids": [c["assessment_id"] for c in band_flips],
        "rank_correlation": _spearman(b_overalls, c_overalls),
    }


# ---- DB + grading plumbing (imported lazily in main) ------------------------


def _result_to_run(rubric_result: Any, evaluation_rubric: Dict[str, Any]) -> Dict[str, Any]:
    from app.components.assessments.rubric_scoring import summarize_fluency_4d

    return {
        "overall": round(float(rubric_result.weighted_score_100), 2),
        "dimensions": {d.dimension_id: d.score for d in rubric_result.dimensions if not d.error},
        "fluency_4d": summarize_fluency_4d(evaluation_rubric, rubric_result.dimensions),
    }


def _reconstruct_artifacts(assessment: Any, task: Any, *, include_process_trace: bool) -> Any:
    """Rebuild ScoringArtifacts from stored columns (mirrors submission_runtime).

    Repo files are best-effort from code_snapshots (the live sandbox repo is
    gone post-submit) — fine for an isolate-the-flag comparison since both
    runs share these artifacts.
    """
    from app.components.assessments.rubric_scoring import ScoringArtifacts

    repo_files: Dict[str, str] = {}
    for snap in (assessment.code_snapshots or []):
        if isinstance(snap, dict):
            for k, v in snap.items():
                if isinstance(v, str) and "/" in k:
                    repo_files[k] = v
    task_extra = task.extra_data if isinstance(getattr(task, "extra_data", None), dict) else {}
    dps = task_extra.get("decision_points")
    decision_points = [d for d in dps if isinstance(d, dict)] if isinstance(dps, list) else []
    passed = assessment.tests_passed if assessment.tests_passed is not None else 0
    total = assessment.tests_total if assessment.tests_total is not None else 0
    git_ev = assessment.git_evidence if isinstance(assessment.git_evidence, dict) else {}
    return ScoringArtifacts(
        repo_files=repo_files,
        prompt_transcript=assessment.ai_prompts or [],
        test_results_summary=f"{passed} of {total} tests passed",
        task_scenario=task.scenario or "",
        candidate_role=str(task.role or ""),
        decision_points=decision_points,
        include_process_trace=include_process_trace,
        git_evidence=git_ev,
    )


def _grade(assessment: Any, task: Any, api_key: str, *, include_process_trace: bool) -> Optional[Dict[str, Any]]:
    from app.components.assessments.rubric_scoring import RubricScorer

    artifacts = _reconstruct_artifacts(assessment, task, include_process_trace=include_process_trace)
    scorer = RubricScorer(
        api_key=api_key,
        organization_id=int(assessment.organization_id),
        assessment_id=int(assessment.id),
    )
    result = scorer.grade_rubric(task.evaluation_rubric, artifacts)
    if not result.dimensions:
        return None
    return _result_to_run(result, task.evaluation_rubric)


def _eligible_assessments(session: Any, *, limit: int, org: Optional[int], task_id: Optional[int]) -> List[Any]:
    from app.models.assessment import Assessment, AssessmentStatus
    from app.models.task import Task

    q = (
        session.query(Assessment)
        .join(Task, Task.id == Assessment.task_id)
        .filter(Assessment.status == AssessmentStatus.COMPLETED)
        .filter(Assessment.ai_prompts.isnot(None))
        .filter(Task.evaluation_rubric.isnot(None))
    )
    if org is not None:
        q = q.filter(Assessment.organization_id == org)
    if task_id is not None:
        q = q.filter(Assessment.task_id == task_id)
    return q.order_by(Assessment.id.desc()).limit(limit).all()


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow re-score assessments to validate a scoring change.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--org", type=int, default=None)
    parser.add_argument("--task", type=int, default=None, help="task_id filter")
    parser.add_argument("--dry-run", action="store_true", help="list eligible + reconstruct; no Anthropic calls")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL is required")
        return 2
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not args.dry_run:
        logger.error("ANTHROPIC_API_KEY is required (or pass --dry-run)")
        return 2

    from app.platform.database import SessionLocal
    from app.models.task import Task

    comparisons: List[Dict[str, Any]] = []
    with SessionLocal() as session:
        rows = _eligible_assessments(session, limit=args.limit, org=args.org, task_id=args.task)
        logger.info("eligible assessments: %d (limit=%d)", len(rows), args.limit)
        if args.dry_run:
            for a in rows:
                logger.info("  assessment=%s org=%s task=%s prompts=%d",
                            a.id, a.organization_id, a.task_id, len(a.ai_prompts or []))
            return 0
        for a in rows:
            task = session.query(Task).filter(Task.id == a.task_id).first()
            if task is None or not task.evaluation_rubric:
                continue
            try:
                baseline = _grade(a, task, api_key, include_process_trace=False)
                candidate = _grade(a, task, api_key, include_process_trace=True)
            except Exception:
                logger.exception("grade failed for assessment=%s — skipping", a.id)
                continue
            if not baseline or not candidate:
                continue
            cmp = compare_runs(int(a.id), baseline, candidate)
            comparisons.append(cmp)
            logger.info(
                "  assessment=%s base=%.1f cand=%.1f delta=%s band=%s->%s%s",
                a.id, baseline["overall"], candidate["overall"], cmp["overall_delta"],
                cmp["baseline_band"], cmp["candidate_band"],
                "  *** BAND FLIP ***" if cmp["band_flip"] else "",
            )

    summary = summarize_comparisons(comparisons)
    logger.info("=== shadow re-score summary (process-trace OFF -> ON) ===")
    for k, v in summary.items():
        logger.info("  %s: %s", k, v)
    logger.info(
        "GUIDANCE: a low mean_abs_delta, few/zero band_flips and rank_correlation "
        "near 1.0 means the flag is safe to flip. Investigate band flips before flipping."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
