from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.components.assessments import service


def test_new_assessment_task_must_satisfy_artifact_first_contract(monkeypatch) -> None:
    task = SimpleNamespace(id=41)
    monkeypatch.setattr(service, "reconstruct_generated_task_spec", lambda _task: {})
    monkeypatch.setattr(
        service,
        "validate_task_spec",
        lambda _spec, **_kwargs: SimpleNamespace(
            errors=["deliverable.required must be true"]
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        service._enforce_artifact_first_task(task)

    assert exc_info.value.status_code == 503
    assert "contact the hiring team" in str(exc_info.value.detail).lower()


def test_artifact_first_task_is_admitted(monkeypatch) -> None:
    task = SimpleNamespace(id=42)
    monkeypatch.setattr(service, "reconstruct_generated_task_spec", lambda _task: {})
    monkeypatch.setattr(
        service,
        "validate_task_spec",
        lambda _spec, **_kwargs: SimpleNamespace(errors=[]),
    )

    service._enforce_artifact_first_task(task)
