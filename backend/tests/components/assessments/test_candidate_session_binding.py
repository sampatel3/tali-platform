from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.components.assessments.repository import (
    bind_candidate_session,
    validate_candidate_session,
)


def test_candidate_session_binds_once_and_resumes_with_same_key() -> None:
    assessment = SimpleNamespace(
        candidate_session_hash=None,
        candidate_session_bound_at=None,
    )
    key = "A" * 43

    assert bind_candidate_session(assessment, key) is True
    assert assessment.candidate_session_hash != key
    assert len(assessment.candidate_session_hash) == 64
    assert assessment.candidate_session_bound_at is not None
    assert bind_candidate_session(assessment, key) is False
    validate_candidate_session(assessment, key)


def test_candidate_session_rejects_a_second_browser_key() -> None:
    assessment = SimpleNamespace(
        candidate_session_hash=None,
        candidate_session_bound_at=None,
    )
    bind_candidate_session(assessment, "A" * 43)

    with pytest.raises(HTTPException) as error:
        bind_candidate_session(assessment, "B" * 43)

    assert error.value.status_code == 409


@pytest.mark.parametrize("key", ["short", "contains spaces" * 4, "!" * 43])
def test_candidate_session_rejects_invalid_keys(key: str) -> None:
    assessment = SimpleNamespace(
        candidate_session_hash=None,
        candidate_session_bound_at=None,
    )

    with pytest.raises(HTTPException) as error:
        bind_candidate_session(assessment, key)

    assert error.value.status_code == 400
