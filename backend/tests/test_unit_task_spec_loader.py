from app.services.task_spec_loader import validate_task_spec, candidate_rubric_view


def test_task_spec_validation_rejects_weight_sum_mismatch():
    spec = {
        "task_id": "x",
        "name": "Task",
        "duration_minutes": 30,
        "scenario": "s",
        "repo_structure": {"files": {"a.py": "print(1)"}},
        "evaluation_rubric": {
            "a": {"weight": 0.4},
            "b": {"weight": 0.4},
        },
    }
    result = validate_task_spec(spec)
    assert result.valid is False
    assert any("sum to 1.0" in e for e in result.errors)


def test_candidate_rubric_view_excludes_criteria():
    safe = candidate_rubric_view({
        "exploration": {"weight": 0.25, "criteria": {"excellent": "secret"}},
    })
    assert safe == [{"category": "exploration", "weight": 0.25}]
