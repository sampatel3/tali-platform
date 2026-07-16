"""Stable public error shapes for assessment API and durable JSON boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


_PUBLIC_RUBRIC_DIMENSION_ERRORS = {
    "missing_decision_points",
    "no_valid_decision_points",
}


def public_rubric_dimension_error(error: object) -> str | None:
    """Map grader internals to stable codes before durable serialization."""
    if error is None:
        return None
    code = str(error).strip()
    if code in _PUBLIC_RUBRIC_DIMENSION_ERRORS:
        return code
    return "rubric_dimension_failed"


def public_test_results(result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep candidate test evidence while removing provider exception text."""
    payload = dict(result)
    if payload.get("error"):
        infrastructure_failure = (
            payload.get("source") == "task_test_runner"
            and payload.get("exit_code") is None
        )
        if infrastructure_failure:
            payload["stdout"] = ""
            payload["stderr"] = ""
        has_command_evidence = bool(payload.get("stdout") or payload.get("stderr"))
        payload["error"] = (
            "test_execution_failed"
            if has_command_evidence
            else "test_runner_unavailable"
        )
    return payload


def public_git_evidence(evidence: object) -> Dict[str, Any]:
    """Remove raw Git/provider error output from recruiter-visible evidence."""
    payload = dict(evidence) if isinstance(evidence, dict) else {}
    for key in tuple(payload):
        if key.endswith("_stderr"):
            payload.pop(key, None)
    if payload.get("diff_main_error") not in (None, "git_diff_failed"):
        payload["diff_main_error"] = "git_diff_failed"
    if payload.get("error") not in {
        None,
        "repo_root_missing",
        "not_a_git_repository",
        "git_evidence_capture_failed",
    }:
        payload["error"] = "git_evidence_capture_failed"
    return payload


def public_score_breakdown(value: object) -> Dict[str, Any]:
    """Scrub legacy raw grader/provider failures without mutating ORM JSON."""
    payload = deepcopy(value) if isinstance(value, dict) else {}
    rubric = payload.get("rubric_grading")
    if isinstance(rubric, dict):
        safe_rubric = dict(rubric)
        if safe_rubric.get("error") not in {
            None,
            "rubric_returned_no_dimensions",
            "rubric_scoring_failed",
            "rubric_grader_unavailable",
            "submission_pipeline_failed",
        }:
            safe_rubric["error"] = "rubric_scoring_failed"
        dimensions = safe_rubric.get("dimensions")
        if isinstance(dimensions, list):
            safe_rubric["dimensions"] = [
                {
                    **dimension,
                    "error": public_rubric_dimension_error(dimension.get("error")),
                }
                if isinstance(dimension, dict)
                else dimension
                for dimension in dimensions
            ]
        retry = safe_rubric.get("retry")
        if isinstance(retry, dict) and retry.get("last_error") not in {
            None,
            "rubric_returned_no_dimensions",
            "rubric_scoring_failed",
            "rubric_grader_unavailable",
            "rubric_grading_incomplete",
            "submission_pipeline_failed",
        }:
            safe_rubric["retry"] = {
                **retry,
                "last_error": "rubric_grading_incomplete",
            }
        payload["rubric_grading"] = safe_rubric

    errors = payload.get("errors")
    if isinstance(errors, list):
        safe_errors = []
        for item in errors:
            if not isinstance(item, dict) or not item.get("error"):
                safe_errors.append(item)
                continue
            error = item.get("error")
            if error not in {
                "cv_match_validation_failed",
                "cv_match_scoring_failed",
                "Missing CV or job spec text — fit scoring skipped",
            }:
                error = "scoring_component_failed"
            safe_errors.append({**item, "error": error})
        payload["errors"] = safe_errors

    failure = payload.get("scoring_failure")
    if isinstance(failure, dict):
        safe_failure = dict(failure)
        safe_failure.pop("error", None)
        safe_failure.pop("error_type", None)
        safe_failure["error_code"] = "submission_pipeline_failed"
        payload["scoring_failure"] = safe_failure
    return payload


def public_timeline(value: object) -> list[Any]:
    """Scrub historical infrastructure exceptions from assessment events."""
    events = deepcopy(value) if isinstance(value, list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type") or event.get("type")
        if event_type == "assessment_scoring_failed":
            event.pop("error", None)
            event.pop("error_type", None)
            event["error_code"] = "submission_pipeline_failed"
        if event_type != "workspace_bootstrap":
            continue
        steps = event.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if (
                isinstance(step, dict)
                and not step.get("success")
                and step.get("exit_code") is None
            ):
                step["stdout_tail"] = ""
                step["stderr_tail"] = ""
                step["error_code"] = "workspace_command_failed"
    return events


__all__ = [
    "public_git_evidence",
    "public_rubric_dimension_error",
    "public_score_breakdown",
    "public_test_results",
    "public_timeline",
]
