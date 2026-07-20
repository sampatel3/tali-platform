from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.components.assessments.task_snapshot import (
    TASK_SNAPSHOT_FIELDS,
    freeze_assessment_task,
    task_view_for_assessment,
)


def _task() -> SimpleNamespace:
    values = {field: None for field in TASK_SNAPSHOT_FIELDS}
    values.update(
        {
            "id": 7,
            "task_key": "frozen-task",
            "name": "Frozen task",
            "scenario": "Original scenario",
            "repo_structure": {"files": {"answer.py": "# starter\n"}},
            "evaluation_rubric": {"deliverable": {"weight": 1.0}},
            "extra_data": {
                "deliverable": {"primary_artifact": "answer.py"},
                "test_runner": {"expected_total": 3},
            },
        }
    )
    return SimpleNamespace(**values)


def test_frozen_task_view_does_not_follow_catalog_mutation() -> None:
    assessment = SimpleNamespace(
        task_spec_snapshot=None,
        task_spec_snapshot_sha256=None,
    )
    live_task = _task()

    assert freeze_assessment_task(assessment, live_task) is True
    live_task.scenario = "Changed after invitation"
    live_task.repo_structure["files"]["answer.py"] = "changed starter\n"
    live_task.evaluation_rubric = {"replacement": {"weight": 1.0}}

    frozen = task_view_for_assessment(assessment, live_task)

    assert frozen.scenario == "Original scenario"
    assert frozen.repo_structure == {"files": {"answer.py": "# starter\n"}}
    assert frozen.evaluation_rubric == {"deliverable": {"weight": 1.0}}


def test_frozen_task_snapshot_tampering_fails_closed() -> None:
    assessment = SimpleNamespace(
        task_spec_snapshot=None,
        task_spec_snapshot_sha256=None,
    )
    live_task = _task()
    freeze_assessment_task(assessment, live_task)
    assessment.task_spec_snapshot["scenario"] = "tampered"

    with pytest.raises(RuntimeError, match="digest verification failed"):
        task_view_for_assessment(assessment, live_task)


@pytest.mark.parametrize(
    ("snapshot", "digest"),
    [
        ({"version": 1}, None),
        (None, "0" * 64),
    ],
)
def test_partial_task_snapshot_metadata_fails_closed(snapshot, digest) -> None:
    assessment = SimpleNamespace(
        task_spec_snapshot=snapshot,
        task_spec_snapshot_sha256=digest,
    )

    with pytest.raises(RuntimeError, match="metadata is incomplete"):
        freeze_assessment_task(assessment, _task())


def test_frozen_task_view_returns_defensive_copies() -> None:
    assessment = SimpleNamespace(
        task_spec_snapshot=None,
        task_spec_snapshot_sha256=None,
    )
    live_task = _task()
    frozen = task_view_for_assessment(assessment, live_task)

    repo = frozen.repo_structure
    repo["files"]["answer.py"] = "caller mutation"

    assert frozen.repo_structure["files"]["answer.py"] == "# starter\n"
    with pytest.raises(AttributeError, match="read-only"):
        frozen.scenario = "cannot assign"
