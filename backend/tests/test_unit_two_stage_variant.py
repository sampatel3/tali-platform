"""build_two_stage_variant derives the announced A/B arm from a base task."""

from types import SimpleNamespace

import pytest

from scripts.seed_two_stage_ab import build_two_stage_variant


def _base_task(rubric=None):
    return SimpleNamespace(
        id=36,
        task_key="data_eng_bronze_ingestion",
        name="Source-to-Bronze Ingestion",
        description="desc",
        task_type="data_engineer",
        difficulty="medium",
        duration_minutes=30,
        calibration_prompt="warm up",
        role="data_engineer",
        scenario="scenario",
        repo_structure={"files": {"README.md": "hi"}},
        evaluation_rubric=rubric
        or {
            "design_decisions_articulated": {"weight": 0.3, "grader": "interrogation_outcome"},
            "ai_native_practice": {"weight": 0.1, "grader": "practice_outcome", "part": "applied"},
            "ingestion_correctness": {"weight": 0.6, "lens": "deliverable", "criteria": {}},
        },
        extra_data={"decision_points": [{"id": "a"}]},
    )


def test_variant_retags_practice_to_part_one_and_adds_stage_config():
    base = _base_task()
    payload = build_two_stage_variant(base, organization_id=2)

    assert payload["task_key"] == "data_eng_bronze_ingestion_two_stage"
    assert payload["organization_id"] == 2
    assert payload["is_template"] is False

    rubric = payload["evaluation_rubric"]
    assert rubric["ai_native_practice"]["part"] == "practice"
    # Non-practice dims untouched.
    assert "part" not in rubric["ingestion_correctness"]

    extra = payload["extra_data"]
    assert extra["part_weights"] == {"practice": 0.3, "applied": 0.7}
    assert len(extra["two_stage"]["parts"]) == 2
    assert extra["two_stage_variant_of"] == "data_eng_bronze_ingestion"

    # The BASE task's rubric must not be mutated by derivation.
    assert base.evaluation_rubric["ai_native_practice"]["part"] == "applied"
    assert "two_stage" not in base.extra_data


def test_variant_requires_a_practice_dim():
    base = _base_task(
        rubric={
            "design_decisions_articulated": {"weight": 0.4, "grader": "interrogation_outcome"},
            "ingestion_correctness": {"weight": 0.6, "lens": "deliverable", "criteria": {}},
        }
    )
    with pytest.raises(ValueError, match="no practice dim"):
        build_two_stage_variant(base, organization_id=2)
