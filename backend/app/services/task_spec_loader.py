from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


RUBRIC_WEIGHT_TOLERANCE = 1e-3


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


def validate_task_spec(spec: Dict[str, Any]) -> TaskSpecValidationResult:
    task_id = spec.get("task_id") or "unknown"
    errors: List[str] = []
    for req in ("task_id", "name", "duration_minutes", "scenario", "repo_structure", "evaluation_rubric"):
        if req not in spec:
            errors.append(f"Missing required field: {req}")

    errors.extend(validate_rubric_weights(spec.get("evaluation_rubric")))
    return TaskSpecValidationResult(task_id=task_id, valid=len(errors) == 0, errors=errors)


def load_task_specs(tasks_dir: str | Path) -> List[Dict[str, Any]]:
    root = Path(tasks_dir)
    specs: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        result = validate_task_spec(data)
        if not result.valid:
            joined = "; ".join(result.errors)
            raise ValueError(f"Invalid task spec {path.name}: {joined}")
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
