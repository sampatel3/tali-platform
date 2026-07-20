"""Small publishable assessment task definitions for cross-domain tests."""

from __future__ import annotations

import re


def valid_task_definition(
    *,
    task_key: str,
    name: str = "Coding task",
    duration_minutes: int = 30,
) -> dict:
    """Return a minimal work-first, offline-verifiable Python task."""
    repo_name = re.sub(r"[^A-Za-z0-9._-]+", "-", task_key).strip("-").lower()
    repo_name = repo_name or "test-assessment-task"
    scenario = "Implement and verify the requested repository change."
    starter_code = "def transform(value):\n    return value\n"
    test_code = "from src.main import transform\n\ndef test_transform():\n    assert transform(2) == 4\n"
    rubric = {
        "implementation": {
            "weight": 0.40,
            "lens": "deliverable",
            "criteria": "The submitted artifact implements the requested change.",
        },
        "decision_ownership": {
            "weight": 0.15,
            "lens": "decision",
            "criteria": "The candidate owns one material implementation decision.",
        },
        "ai_native_practice": {
            "weight": 0.15,
            "grader": "practice_outcome",
            "part": "applied",
            "fluency": "description",
        },
        "output_scrutiny": {
            "weight": 0.15,
            "lens": "discernment",
            "criteria": "The candidate reviews and corrects tool output.",
        },
        "verification_before_done": {
            "weight": 0.15,
            "lens": "diligence",
            "criteria": "The candidate runs the verifier before submitting.",
        },
    }
    return {
        "name": name,
        "description": "A work-first test assessment task.",
        "task_type": "python",
        "difficulty": "medium",
        "duration_minutes": duration_minutes,
        "starter_code": starter_code,
        "test_code": test_code,
        "task_key": task_key,
        "role": "software_engineer",
        "scenario": scenario,
        "repo_structure": {
            "name": repo_name,
            "files": {
                "README.md": "# Test assessment repository\n",
                "SCENARIO.md": f"# Scenario\n\n{scenario}\n",
                "requirements.txt": "pytest>=8.0.0\n",
                "src/main.py": starter_code,
                "tests/test_main.py": test_code,
            },
        },
        "evaluation_rubric": rubric,
        "extra_data": {
            "deliverable": {
                "kind": "code",
                "primary_artifact": "src/main.py",
                "required": True,
                "no_artifact_outcome": "incomplete",
                "submission_check": "test_runner",
            },
            "expected_candidate_journey": {
                "orient": ["Read the local scenario and starter implementation."],
                "implement": ["Make a substantive change in src/main.py."],
                "verify": ["Run and inspect the frozen verifier before submission."],
            },
            "interviewer_signals": {
                "strong_positive": ["Ships and verifies a working artifact."],
                "red_flags": ["Submits without changing the primary artifact."],
            },
            "scoring_hints": {"min_reading_time_seconds": 1},
            "test_runner": {
                "command": "python3 -I -m pytest -q --tb=short",
                "working_dir": f"/workspace/{repo_name}",
                "parse_pattern": r"(?P<passed>\d+)\s+passed(?:,\s+(?P<failed>\d+)\s+failed)?",
                "timeout_seconds": 60,
                "expected_total": 1,
                "verifier_files": ["tests/test_main.py"],
            },
            "workspace_bootstrap": {
                "commands": ["python3 -I -c \"import pytest\""],
                "working_dir": f"/workspace/{repo_name}",
                "timeout_seconds": 30,
                "must_succeed": True,
            },
            "role_alignment": {
                "source_user_email": "test-author@example.com",
                "source_role_name": "Software Engineer",
                "source_role_identifier": "test:software-engineer",
                "captured_at": "2026-01-01T00:00:00Z",
                "must_cover": ["Implement and verify a repository change."],
                "must_not_cover": [],
                "jd_to_signal_map": [
                    {
                        "job_requirement": "Implement and verify production work.",
                        "task_artifact": "Repository artifact and process evidence.",
                        "rubric_dimension": dimension,
                    }
                    for dimension in rubric
                ],
            },
            "human_testing_checklist": {
                "candidate_clarity": True,
                "repo_boot_ok": True,
                "tests_collect_ok": True,
                "baseline_failures_meaningful": True,
                "rubric_matches_role": True,
                "timebox_realistic": True,
            },
        },
    }
