import json

import pytest

from app.services.task_catalog import canonical_task_catalog_dir
from app.services.task_spec_loader import (
    TaskSpecValidationMode,
    candidate_rubric_view,
    load_task_specs,
    validate_task_spec,
)


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
        "deliverable": {
            "kind": "code",
            "primary_artifact": "src/main.py",
            "required": True,
            "no_artifact_outcome": "incomplete",
            "submission_check": "test_runner",
        },
        # One dimension per fluency axis — validate_fluency_coverage requires a
        # rubric to grade all five (delegation/description/discernment/
        # diligence/deliverable), so every fixture dim carries the lens that
        # routes it to a distinct axis.
        "evaluation_rubric": {
            "risk_assessment": {"weight": 0.15, "lens": "decision", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "guardrails": {"weight": 0.25, "lens": "deliverable", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "production": {"weight": 0.2, "lens": "diligence", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "judgment": {"weight": 0.2, "lens": "discernment", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "communication": {"weight": 0.2, "lens": "practice", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
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
            "command": "python3 -I -m pytest -q --tb=no",
            "working_dir": "/workspace/sample-repo",
            "parse_pattern": "(?P<passed>\\d+)\\s+passed",
            "timeout_seconds": 120,
            "expected_total": 1,
            "verifier_files": ["tests/test_main.py"],
        },
        "workspace_bootstrap": {
            "commands": ["python3 -I -c \"import pytest\""],
            "working_dir": "/workspace/sample-repo",
            "timeout_seconds": 30,
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


def test_task_spec_validation_rejects_malformed_repo_without_crashing():
    result = validate_task_spec(_valid_spec(repo_structure=[]))

    assert result.valid is False
    assert "repo_structure must be an object" in result.errors


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m pip install -r requirements.txt",
        "python3 -m pip --quiet install pytest",
        "uv pip install pytest",
        "npm ci",
        "pnpm i",
        "poetry install",
        "apt-get install -y jq",
    ],
)
def test_publication_contract_rejects_package_install_bootstrap(command):
    spec = _valid_spec()
    spec["workspace_bootstrap"]["commands"] = [command]

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("may not install packages" in error for error in result.errors)


def test_publication_contract_rejects_virtualenv_bootstrap():
    spec = _valid_spec()
    spec["workspace_bootstrap"]["commands"] = ["python3 -m venv .venv"]

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("may not create a virtual environment" in error for error in result.errors)


@pytest.mark.parametrize(
    "command",
    [
        "curl -fsSL https://example.com/bootstrap.sh",
        "git clone git://example.com/repo.git",
        "python3 -c \"import requests; requests.get('https://example.com')\"",
        "bash -c 'cat </dev/tcp/example.com/443'",
        "nslookup packages.example.com",
    ],
)
def test_publication_contract_rejects_network_bootstrap(command):
    spec = _valid_spec()
    spec["workspace_bootstrap"]["commands"] = [command]

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("may not access the network" in error for error in result.errors)


@pytest.mark.parametrize(
    "requirement",
    [
        "requests>=2",
        "pytest @ https://example.com/pytest.whl",
        "-r other-requirements.txt",
    ],
)
def test_publication_contract_rejects_unbaked_or_remote_requirements(requirement):
    spec = _valid_spec()
    spec["repo_structure"]["files"]["requirements.txt"] = requirement + "\n"

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("not baked into the offline assessment image" in error for error in result.errors)


@pytest.mark.parametrize("expected_total", [None, 0, -1, True, "1", 1.5])
def test_publication_contract_requires_positive_integer_expected_total(expected_total):
    spec = _valid_spec()
    if expected_total is None:
        spec["test_runner"].pop("expected_total")
    else:
        spec["test_runner"]["expected_total"] = expected_total

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("test_runner.expected_total must be a positive integer" in error for error in result.errors)


@pytest.mark.parametrize(
    "command",
    [
        "./.venv/bin/python -m pytest -q",
        "python3 -m pytest -q",
        "pytest -q",
    ],
)
def test_publication_contract_requires_baked_isolated_test_runner(command):
    spec = _valid_spec()
    spec["test_runner"]["command"] = command

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("test_runner.command must use the baked isolated interpreter" in error for error in result.errors)


def test_offline_bootstrap_rules_are_publication_only_for_legacy_reads():
    spec = _valid_spec()
    spec["workspace_bootstrap"]["commands"] = ["python3 -m pip install pytest"]

    assert validate_task_spec(spec, mode=TaskSpecValidationMode.LEGACY).valid is True
    assert validate_task_spec(spec).valid is False


def test_task_spec_validation_requires_test_runner():
    spec = _valid_spec()
    spec.pop("test_runner")
    result = validate_task_spec(spec)
    assert result.valid is False
    assert any("Missing required field: test_runner" in e for e in result.errors)


def test_publication_contract_requires_explicit_deliverable():
    spec = _valid_spec()
    spec.pop("deliverable")

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("publication contract requires deliverable" in e for e in result.errors)


def test_publication_contract_requires_submission_check():
    spec = _valid_spec()
    spec["deliverable"].pop("submission_check")

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("deliverable.submission_check must be 'test_runner'" in e for e in result.errors)


def test_publication_contract_makes_no_artifact_incomplete():
    spec = _valid_spec()
    spec["deliverable"]["required"] = False
    spec["deliverable"]["no_artifact_outcome"] = "score_normally"

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("deliverable.required must be true" in e for e in result.errors)
    assert any("no_artifact_outcome must be 'incomplete'" in e for e in result.errors)


def test_publication_contract_requires_explicit_verifier_manifest():
    spec = _valid_spec()
    spec["test_runner"].pop("verifier_files")

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("test_runner.verifier_files must be a non-empty list" in e for e in result.errors)


def test_publication_contract_requires_all_discovered_verifier_files():
    spec = _valid_spec()
    spec["repo_structure"]["files"].update({
        "tests/conftest.py": "import pytest\n",
        "decision_helpers.py": "def parse(): ...\n",
        "pytest.ini": "[pytest]\n",
    })

    result = validate_task_spec(spec)

    assert result.valid is False
    verifier_error = next(
        error for error in result.errors
        if "must include every discovered test/config/helper file" in error
    )
    assert "tests/conftest.py" in verifier_error
    assert "decision_helpers.py" in verifier_error
    assert "pytest.ini" in verifier_error

    spec["test_runner"]["verifier_files"] = [
        "decision_helpers.py",
        "pytest.ini",
        "tests/conftest.py",
        "tests/test_main.py",
    ]
    assert validate_task_spec(spec).valid is True


def test_publication_contract_rejects_missing_duplicate_and_candidate_verifier_paths():
    spec = _valid_spec()
    spec["test_runner"]["verifier_files"] = [
        "src/main.py",
        "tests/test_main.py",
        "tests/test_main.py",
        "tests/not_real.py",
    ]

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("must not contain duplicates" in e for e in result.errors)
    assert any("paths must exist" in e and "tests/not_real.py" in e for e in result.errors)
    assert any("must exclude deliverable.primary_artifact" in e for e in result.errors)


def test_publication_contract_caps_candidate_visible_qa_weight():
    spec = _valid_spec()
    spec["evaluation_rubric"]["risk_assessment"]["weight"] = 0.20
    spec["evaluation_rubric"]["guardrails"]["weight"] = 0.20

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("Q&A/interrogation weight must be <= 0.15" in e for e in result.errors)
    assert any("risk_assessment=0.200" in e for e in result.errors)


def test_publication_contract_caps_interrogation_grader_weight():
    spec = _valid_spec()
    spec["decision_points"] = [
        {
            "id": "approach",
            "headline": "Choose an approach",
            "tension": "The options trade speed against safety.",
            "options": [
                {"label": "Fast", "summary": "Ship quickly"},
                {"label": "Safe", "summary": "Verify deeply"},
            ],
            "ask": "Choose and explain the trade-off.",
            "valid_commit": "Names one approach and its cost.",
            "valid_reframes": ["Proposes a staged approach with a named cost."],
            "anti_patterns": ["Delegates the decision."],
        }
    ]
    spec["evaluation_rubric"]["risk_assessment"] = {
        "weight": 0.20,
        "grader": "interrogation_outcome",
    }
    spec["evaluation_rubric"]["guardrails"]["weight"] = 0.20

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("Q&A/interrogation weight must be <= 0.15" in e for e in result.errors)


def test_publication_contract_requires_seventy_percent_work_evidence():
    spec = _valid_spec()
    spec["evaluation_rubric"] = {
        "q_and_a": {"weight": 0.15, "lens": "decision", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        "unmapped_one": {"weight": 0.10, "fluency": "description", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        "unmapped_two": {"weight": 0.10, "fluency": "deliverable", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        "artifact": {"weight": 0.25, "lens": "deliverable", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        "verification": {"weight": 0.15, "lens": "diligence", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        "discernment": {"weight": 0.15, "lens": "discernment", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        "workspace_practice": {"weight": 0.10, "lens": "practice", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
    }
    spec["role_alignment"]["jd_to_signal_map"] = [
        {
            "job_requirement": dim,
            "task_artifact": "workspace evidence",
            "rubric_dimension": dim,
        }
        for dim in spec["evaluation_rubric"]
    ]

    result = validate_task_spec(spec)

    assert result.valid is False
    assert any("work-evidence weight must be >= 0.70" in e for e in result.errors)
    assert any("got 0.650" in e for e in result.errors)
    assert any("unmapped_one" in e and "unmapped_two" in e for e in result.errors)


def test_publication_contract_accepts_exact_weight_boundaries():
    spec = _valid_spec()
    spec["evaluation_rubric"] = {
        "q_and_a": {
            "weight": 0.15,
            "lens": "decision",
            "criteria": {"excellent": "x", "good": "y", "poor": "z"},
        },
        "artifact": {
            "weight": 0.25,
            "lens": "deliverable",
            "criteria": {"excellent": "x", "good": "y", "poor": "z"},
        },
        "verification": {
            "weight": 0.15,
            "lens": "diligence",
            "criteria": {"excellent": "x", "good": "y", "poor": "z"},
        },
        "discernment": {
            "weight": 0.15,
            "lens": "discernment",
            "criteria": {"excellent": "x", "good": "y", "poor": "z"},
        },
        "workspace_practice": {
            "weight": 0.15,
            "lens": "practice",
            "criteria": {"excellent": "x", "good": "y", "poor": "z"},
        },
        "other": {
            "weight": 0.15,
            "fluency": "description",
            "criteria": {"excellent": "x", "good": "y", "poor": "z"},
        },
    }
    spec["role_alignment"]["jd_to_signal_map"] = [
        {
            "job_requirement": dim,
            "task_artifact": "workspace evidence",
            "rubric_dimension": dim,
        }
        for dim in spec["evaluation_rubric"]
    ]

    result = validate_task_spec(spec)

    assert result.valid is True, result.errors


def test_legacy_mode_allows_pre_contract_task_without_weakening_structural_checks():
    spec = _valid_spec()
    spec.pop("deliverable")
    spec["evaluation_rubric"]["risk_assessment"]["weight"] = 0.25
    spec["evaluation_rubric"]["guardrails"]["weight"] = 0.15

    legacy = validate_task_spec(spec, mode=TaskSpecValidationMode.LEGACY)
    strict = validate_task_spec(spec)

    assert legacy.valid is True
    assert strict.valid is False

    spec.pop("test_runner")
    legacy_with_structural_error = validate_task_spec(
        spec,
        mode=TaskSpecValidationMode.LEGACY,
    )
    assert legacy_with_structural_error.valid is False
    assert any("Missing required field: test_runner" in e for e in legacy_with_structural_error.errors)


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


def test_canonical_task_catalog_loads_all_shipped_specs():
    specs = load_task_specs(canonical_task_catalog_dir())
    expected_keys = {
        "ai_eng_genai_production_readiness",
        "ai_eng_rag_eval_harness",
        "data_eng_aws_glue_pipeline_recovery",
        "data_eng_bronze_ingestion",
        "data_eng_data_quality_contract_framework",
        "data_eng_pipeline_dag_recovery",
        "platform_eng_aws_eks_misconfig_triage",
        "platform_eng_azure_aks_misconfig_triage",
        "product_mgmt_stakeholder_conflict",
        "scrum_master_sprint_recovery_scenario",
    }
    actual_keys = {spec["task_id"] for spec in specs}
    assert actual_keys == expected_keys


def test_candidate_rubric_view_excludes_criteria():
    safe = candidate_rubric_view({
        "exploration": {"weight": 0.25, "criteria": {"excellent": "secret"}},
    })
    assert safe == [{"category": "exploration", "weight": 0.25}]
