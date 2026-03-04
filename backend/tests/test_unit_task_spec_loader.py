import json

from app.services.task_catalog import canonical_task_catalog_dir
from app.services.task_spec_loader import candidate_rubric_view, load_task_specs, validate_task_spec


def _valid_spec(**overrides):
    spec = {
        "task_id": "sample_task",
        "name": "Sample Task",
        "role": "ai_engineer",
        "duration_minutes": 30,
        "calibration_prompt": "Prompt",
        "scenario": "Scenario",
        "repo_structure": {
            "name": "sample-repo",
            "files": {
                "README.md": "# Sample",
                "docs/diagnostics.md": "notes",
                "src/main.py": "def run():\n    return 1\n",
                "tests/test_main.py": "def test_ok():\n    assert True\n",
                "requirements.txt": "pytest\n",
            },
        },
        "evaluation_rubric": {
            "risk_assessment": {"weight": 0.2, "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "guardrails": {"weight": 0.2, "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "production": {"weight": 0.2, "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "judgment": {"weight": 0.2, "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "communication": {"weight": 0.2, "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        },
        "expected_candidate_journey": {
            "phase_one": ["Read docs"],
            "phase_two": ["Run tests"],
            "phase_three": ["Summarize"],
        },
        "interviewer_signals": {
            "strong_positive": ["Reads docs first"],
            "red_flags": ["Skips diagnostics"],
        },
        "scoring_hints": {"min_reading_time_seconds": 300},
        "test_runner": {
            "command": "./.venv/bin/python -m pytest -q --tb=no",
            "working_dir": "/workspace/sample-repo",
            "parse_pattern": "(?P<passed>\\d+)\\s+passed",
            "timeout_seconds": 120,
        },
        "workspace_bootstrap": {
            "commands": ["python3 -m venv .venv", "./.venv/bin/python -m pip install -r requirements.txt"],
            "working_dir": "/workspace/sample-repo",
            "timeout_seconds": 240,
            "must_succeed": True,
        },
        "role_alignment": {
            "source_user_email": "sampatel@deeplight.ae",
            "source_role_name": "Deeplight GenAI / AI Engineer",
            "source_role_identifier": "deeplight-genai-ai-engineer",
            "captured_at": "2026-03-03T00:00:00Z",
            "must_cover": ["guardrails"],
            "must_not_cover": ["gpu clusters"],
            "jd_to_signal_map": [
                {"job_requirement": "risk", "task_artifact": "RISKS.md", "rubric_dimension": "risk_assessment"},
                {"job_requirement": "guardrails", "task_artifact": "safety.py", "rubric_dimension": "guardrails"},
                {"job_requirement": "production", "task_artifact": "tests", "rubric_dimension": "production"},
                {"job_requirement": "judgment", "task_artifact": "scenario", "rubric_dimension": "judgment"},
                {"job_requirement": "communication", "task_artifact": "summary", "rubric_dimension": "communication"},
            ],
        },
        "human_testing_checklist": {
            "candidate_clarity": False,
            "repo_boot_ok": False,
            "tests_collect_ok": False,
            "baseline_failures_meaningful": False,
            "rubric_matches_role": False,
            "timebox_realistic": False,
        },
    }
    spec.update(overrides)
    return spec


def test_task_spec_validation_rejects_weight_sum_mismatch():
    spec = _valid_spec()
    spec["evaluation_rubric"]["communication"]["weight"] = 0.1
    result = validate_task_spec(spec)
    assert result.valid is False
    assert any("sum to 1.0" in e for e in result.errors)


def test_task_spec_validation_requires_workspace_bootstrap():
    spec = _valid_spec()
    spec.pop("workspace_bootstrap")
    result = validate_task_spec(spec)
    assert result.valid is False
    assert any("Missing required field: workspace_bootstrap" in e for e in result.errors)


def test_task_spec_validation_requires_test_runner():
    spec = _valid_spec()
    spec.pop("test_runner")
    result = validate_task_spec(spec)
    assert result.valid is False
    assert any("Missing required field: test_runner" in e for e in result.errors)


def test_load_task_specs_rejects_duplicate_task_ids(tmp_path):
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(json.dumps(_valid_spec(task_id="duplicate-task")), encoding="utf-8")
    second.write_text(json.dumps(_valid_spec(task_id="duplicate-task", name="Duplicate Task 2")), encoding="utf-8")

    try:
        load_task_specs(tmp_path)
        assert False, "expected duplicate task_id to fail validation"
    except ValueError as exc:
        assert "Duplicate task_id" in str(exc)


def test_load_task_specs_normalizes_escaped_repo_file_content(tmp_path):
    path = tmp_path / "task.json"
    spec = _valid_spec()
    spec["repo_structure"]["files"]["README.md"] = "# Sample\\n\\nLine two\\n"
    spec["repo_structure"]["files"]["src/main.py"] = "def run():\\n    return 2\\n"
    path.write_text(json.dumps(spec), encoding="utf-8")

    loaded = load_task_specs(tmp_path)

    assert loaded[0]["repo_structure"]["files"]["README.md"] == "# Sample\n\nLine two\n"
    assert loaded[0]["repo_structure"]["files"]["src/main.py"] == "def run():\n    return 2\n"


def test_canonical_task_catalog_dir_points_to_backend_tasks():
    assert canonical_task_catalog_dir().as_posix().endswith("/backend/tasks")


def test_canonical_task_catalog_contains_two_specs():
    specs = load_task_specs(canonical_task_catalog_dir())
    assert len(specs) == 2


def test_candidate_rubric_view_excludes_criteria():
    safe = candidate_rubric_view({
        "exploration": {"weight": 0.25, "criteria": {"excellent": "secret"}},
    })
    assert safe == [{"category": "exploration", "weight": 0.25}]
