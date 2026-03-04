from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .task_catalog import canonical_task_catalog_dir
from .task_repo_service import normalize_repo_file_content


RUBRIC_WEIGHT_TOLERANCE = 1e-3
REQUIRED_TASK_FIELDS = (
    "task_id",
    "name",
    "role",
    "duration_minutes",
    "calibration_prompt",
    "scenario",
    "repo_structure",
    "evaluation_rubric",
    "expected_candidate_journey",
    "interviewer_signals",
    "scoring_hints",
    "test_runner",
    "workspace_bootstrap",
    "role_alignment",
    "human_testing_checklist",
)
HUMAN_TESTING_KEYS = (
    "candidate_clarity",
    "repo_boot_ok",
    "tests_collect_ok",
    "baseline_failures_meaningful",
    "rubric_matches_role",
    "timebox_realistic",
)
ROLE_ALIGNMENT_KEYS = (
    "source_user_email",
    "source_role_name",
    "source_role_identifier",
    "captured_at",
    "must_cover",
    "must_not_cover",
    "jd_to_signal_map",
)
TEST_RUNNER_KEYS = ("command", "working_dir", "parse_pattern", "timeout_seconds")
WORKSPACE_BOOTSTRAP_KEYS = ("commands", "working_dir", "timeout_seconds", "must_succeed")


@dataclass
class TaskSpecValidationResult:
    task_id: str
    valid: bool
    errors: List[str]


def validate_rubric_weights(evaluation_rubric: Dict[str, Any] | None) -> List[str]:
    if not evaluation_rubric:
        return []
    total = 0.0
    errors: List[str] = []
    for category, details in evaluation_rubric.items():
        if not isinstance(details, dict):
            errors.append(f"Category '{category}' must be an object")
            continue
        weight = details.get("weight")
        if weight is None:
            errors.append(f"Category '{category}' missing weight")
            continue
        try:
            total += float(weight)
        except (TypeError, ValueError):
            errors.append(f"Category '{category}' has invalid weight: {weight!r}")
    if abs(total - 1.0) > RUBRIC_WEIGHT_TOLERANCE:
        errors.append(f"Rubric weights must sum to 1.0 (+/- {RUBRIC_WEIGHT_TOLERANCE}); got {total:.6f}")
    return errors


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _repo_files(repo_structure: Dict[str, Any] | None) -> Dict[str, str]:
    files = (repo_structure or {}).get("files") or {}
    normalized: Dict[str, str] = {}
    if isinstance(files, dict):
        for path, content in files.items():
            if not _is_non_empty_string(path):
                continue
            normalized[str(path)] = normalize_repo_file_content(
                content if isinstance(content, str) else json.dumps(content, indent=2, sort_keys=True)
            )
    elif isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if not _is_non_empty_string(path):
                continue
            content = entry.get("content", "")
            normalized[str(path)] = normalize_repo_file_content(
                content if isinstance(content, str) else json.dumps(content, indent=2, sort_keys=True)
            )
    return normalized


def _normalize_task_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    repo_structure = spec.get("repo_structure")
    if not isinstance(repo_structure, dict):
        return spec

    repo_structure["files"] = _repo_files(repo_structure)
    return spec


def _validate_repo_structure(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    repo_structure = spec.get("repo_structure")
    if not isinstance(repo_structure, dict):
        return ["repo_structure must be an object"]

    repo_name = str(repo_structure.get("name") or "").strip()
    if not repo_name:
        errors.append("repo_structure.name is required")

    files = _repo_files(repo_structure)
    if not files:
        errors.append("repo_structure.files must contain at least one file")
        return errors

    file_paths = list(files.keys())
    if "README.md" not in files:
        errors.append("repo_structure.files must include README.md")

    if not any(path.lower().endswith(".md") and path != "README.md" for path in file_paths):
        errors.append("repo_structure.files must include at least one scenario or diagnostic document beyond README.md")

    has_test_file = any(
        path.lower().startswith("tests/") or "/tests/" in path.lower() or Path(path).name.lower().startswith("test_")
        for path in file_paths
    )
    if not has_test_file:
        errors.append("repo_structure.files must include at least one test file")

    source_file_paths = [
        path
        for path in file_paths
        if Path(path).suffix.lower() in {".py", ".js", ".ts", ".tsx", ".jsx", ".sh"}
        and not (
            path.lower().startswith("tests/")
            or "/tests/" in path.lower()
            or Path(path).name.lower().startswith("test_")
        )
    ]
    if not source_file_paths:
        errors.append("repo_structure.files must include executable source files")

    has_python_source = any(Path(path).suffix.lower() == ".py" for path in source_file_paths)
    if has_python_source and "requirements.txt" not in files:
        errors.append("Python task specs must include requirements.txt in repo_structure.files")

    return errors


def _validate_expected_candidate_journey(spec: Dict[str, Any]) -> List[str]:
    journey = spec.get("expected_candidate_journey")
    if not isinstance(journey, dict):
        return ["expected_candidate_journey must be an object"]
    if len(journey) < 3:
        return ["expected_candidate_journey must contain at least 3 phases"]
    errors: List[str] = []
    for phase, steps in journey.items():
        if not isinstance(steps, list) or not steps:
            errors.append(f"expected_candidate_journey phase '{phase}' must be a non-empty list")
    return errors


def _validate_interviewer_signals(spec: Dict[str, Any]) -> List[str]:
    signals = spec.get("interviewer_signals")
    if not isinstance(signals, dict):
        return ["interviewer_signals must be an object"]
    errors: List[str] = []
    for key in ("strong_positive", "red_flags"):
        values = signals.get(key)
        if not isinstance(values, list) or not values:
            errors.append(f"interviewer_signals.{key} must be a non-empty list")
    return errors


def _validate_test_runner(spec: Dict[str, Any]) -> List[str]:
    runner = spec.get("test_runner")
    if not isinstance(runner, dict):
        return ["test_runner must be an object"]

    errors: List[str] = []
    for key in TEST_RUNNER_KEYS:
        if key not in runner:
            errors.append(f"test_runner missing field: {key}")
    command = str(runner.get("command") or "").strip()
    if not command:
        errors.append("test_runner.command must be a non-empty string")
    working_dir = str(runner.get("working_dir") or "").strip()
    if not working_dir.startswith("/workspace/"):
        errors.append("test_runner.working_dir must be an absolute /workspace/... path")
    try:
        timeout = int(runner.get("timeout_seconds") or 0)
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("test_runner.timeout_seconds must be a positive integer")
    return errors


def _validate_workspace_bootstrap(spec: Dict[str, Any]) -> List[str]:
    bootstrap = spec.get("workspace_bootstrap")
    if not isinstance(bootstrap, dict):
        return ["workspace_bootstrap must be an object"]

    errors: List[str] = []
    for key in WORKSPACE_BOOTSTRAP_KEYS:
        if key not in bootstrap:
            errors.append(f"workspace_bootstrap missing field: {key}")
    commands = bootstrap.get("commands")
    if not isinstance(commands, list) or not commands or not all(_is_non_empty_string(command) for command in commands):
        errors.append("workspace_bootstrap.commands must be a non-empty list of commands")
    working_dir = str(bootstrap.get("working_dir") or "").strip()
    if not working_dir.startswith("/workspace/"):
        errors.append("workspace_bootstrap.working_dir must be an absolute /workspace/... path")
    try:
        timeout = int(bootstrap.get("timeout_seconds") or 0)
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("workspace_bootstrap.timeout_seconds must be a positive integer")
    if bootstrap.get("must_succeed") is not True:
        errors.append("workspace_bootstrap.must_succeed must be true")
    return errors


def _validate_role_alignment(spec: Dict[str, Any], rubric_dimensions: set[str]) -> List[str]:
    role_alignment = spec.get("role_alignment")
    if not isinstance(role_alignment, dict):
        return ["role_alignment must be an object"]

    errors: List[str] = []
    for key in ROLE_ALIGNMENT_KEYS:
        if key not in role_alignment:
            errors.append(f"role_alignment missing field: {key}")
    for key in ("source_user_email", "source_role_name", "source_role_identifier", "captured_at"):
        if not _is_non_empty_string(role_alignment.get(key)):
            errors.append(f"role_alignment.{key} must be a non-empty string")

    must_cover = role_alignment.get("must_cover")
    if not isinstance(must_cover, list) or not must_cover:
        errors.append("role_alignment.must_cover must be a non-empty list")

    must_not_cover = role_alignment.get("must_not_cover")
    if not isinstance(must_not_cover, list):
        errors.append("role_alignment.must_not_cover must be a list")

    mappings = role_alignment.get("jd_to_signal_map")
    if not isinstance(mappings, list) or not mappings:
        errors.append("role_alignment.jd_to_signal_map must be a non-empty list")
        return errors

    covered_dimensions: set[str] = set()
    for idx, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            errors.append(f"role_alignment.jd_to_signal_map[{idx}] must be an object")
            continue
        for key in ("job_requirement", "task_artifact", "rubric_dimension"):
            if not _is_non_empty_string(mapping.get(key)):
                errors.append(f"role_alignment.jd_to_signal_map[{idx}].{key} must be a non-empty string")
        rubric_dimension = str(mapping.get("rubric_dimension") or "").strip()
        if rubric_dimension:
            covered_dimensions.add(rubric_dimension)

    missing_dimensions = rubric_dimensions - covered_dimensions
    if missing_dimensions:
        errors.append(
            "role_alignment.jd_to_signal_map must cover every rubric dimension; missing "
            + ", ".join(sorted(missing_dimensions))
        )

    return errors


def _validate_human_testing_checklist(spec: Dict[str, Any]) -> List[str]:
    checklist = spec.get("human_testing_checklist")
    if not isinstance(checklist, dict):
        return ["human_testing_checklist must be an object"]
    errors: List[str] = []
    for key in HUMAN_TESTING_KEYS:
        if key not in checklist:
            errors.append(f"human_testing_checklist missing field: {key}")
        elif not isinstance(checklist.get(key), bool):
            errors.append(f"human_testing_checklist.{key} must be a boolean")
    return errors


def validate_task_spec(spec: Dict[str, Any]) -> TaskSpecValidationResult:
    task_id = spec.get("task_id") or "unknown"
    errors: List[str] = []
    for req in REQUIRED_TASK_FIELDS:
        if req not in spec:
            errors.append(f"Missing required field: {req}")

    evaluation_rubric = spec.get("evaluation_rubric")
    if not isinstance(evaluation_rubric, dict):
        errors.append("evaluation_rubric must be an object")
        rubric_dimensions: set[str] = set()
    else:
        rubric_dimensions = set(evaluation_rubric.keys())
        if len(rubric_dimensions) != 5:
            errors.append(f"evaluation_rubric must define exactly 5 dimensions; got {len(rubric_dimensions)}")

    errors.extend(validate_rubric_weights(evaluation_rubric))
    errors.extend(_validate_repo_structure(spec))
    errors.extend(_validate_expected_candidate_journey(spec))
    errors.extend(_validate_interviewer_signals(spec))
    errors.extend(_validate_test_runner(spec))
    errors.extend(_validate_workspace_bootstrap(spec))
    errors.extend(_validate_role_alignment(spec, rubric_dimensions))
    errors.extend(_validate_human_testing_checklist(spec))

    repo_name = str(((spec.get("repo_structure") or {}).get("name")) or "").strip()
    bootstrap_working_dir = str(((spec.get("workspace_bootstrap") or {}).get("working_dir")) or "").strip()
    test_working_dir = str(((spec.get("test_runner") or {}).get("working_dir")) or "").strip()
    if repo_name:
        expected_suffix = f"/{repo_name}"
        if bootstrap_working_dir and not bootstrap_working_dir.endswith(expected_suffix):
            errors.append("workspace_bootstrap.working_dir must end with repo_structure.name")
        if test_working_dir and not test_working_dir.endswith(expected_suffix):
            errors.append("test_runner.working_dir must end with repo_structure.name")

    return TaskSpecValidationResult(task_id=task_id, valid=len(errors) == 0, errors=errors)


def load_task_specs(tasks_dir: str | Path) -> List[Dict[str, Any]]:
    root = Path(tasks_dir or canonical_task_catalog_dir())
    specs: List[Dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for path in sorted(root.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = _normalize_task_spec(json.load(f))
        result = validate_task_spec(data)
        if not result.valid:
            joined = "; ".join(result.errors)
            raise ValueError(f"Invalid task spec {path.name}: {joined}")
        if result.task_id in seen_task_ids:
            raise ValueError(f"Invalid task spec {path.name}: Duplicate task_id {result.task_id!r}")
        seen_task_ids.add(result.task_id)
        specs.append(data)
    return specs


def candidate_rubric_view(evaluation_rubric: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Candidate-safe rubric payload: category + weight only (no criteria leakage)."""
    safe: List[Dict[str, Any]] = []
    for category, details in (evaluation_rubric or {}).items():
        try:
            if isinstance(details, (int, float)):
                weight = float(details)
            else:
                weight = float((details or {}).get("weight", 0))
        except (TypeError, ValueError):
            weight = 0.0
        safe.append({"category": category, "weight": weight})
    return safe
