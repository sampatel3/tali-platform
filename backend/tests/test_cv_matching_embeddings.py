"""Tests for ``app.cv_matching.embeddings``.

Exercises the mock provider end-to-end (deterministic, no network).
"""

from __future__ import annotations

from app.cv_matching.embeddings import (
    clear_cache,
    cosine_similarity,
    embed_cv,
    embed_jd,
)
from app.cv_matching.schemas import Priority, RequirementInput


def setup_function(_):
    clear_cache()


def test_mock_embed_cv_is_deterministic_and_normalised():
    a = embed_cv("Python developer for 6 years")
    b = embed_cv("Python developer for 6 years")
    assert a == b  # cache hit returns identical vector
    # mock provider L2-normalises
    norm_sq = sum(x * x for x in a)
    assert abs(norm_sq - 1.0) < 1e-9


def test_mock_embed_distinguishes_cvs():
    a = embed_cv("Senior Python engineer at FinTechCo")
    b = embed_cv("Junior frontend developer with React")
    sim = cosine_similarity(a, b)
    # Two distinct strings should produce non-identical mock vectors.
    assert sim != 1.0


def test_cosine_self_similarity_is_one():
    a = embed_cv("Senior Python engineer")
    sim = cosine_similarity(a, a)
    assert abs(sim - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert cosine_similarity(a, b) == 0.0


def test_cosine_zero_vector_returns_zero():
    a = [0.0, 0.0, 0.0]
    b = [1.0, 1.0, 1.0]
    assert cosine_similarity(a, b) == 0.0


def test_cosine_dim_mismatch_raises():
    try:
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
    except ValueError as exc:
        assert "vector dims differ" in str(exc)
        return
    raise AssertionError("expected ValueError on dim mismatch")


def test_embed_jd_includes_requirements_in_signal():
    jd = "Senior Python role at a fintech."
    a = embed_jd(jd, requirements=[])
    b = embed_jd(
        jd,
        requirements=[
            RequirementInput(
                id="r1",
                requirement="5+ years FastAPI",
                priority=Priority.MUST_HAVE,
            )
        ],
    )
    # Different requirement context → different vector. Provides a sanity
    # check that requirements actually thread into the embed key/text.
    assert a != b


def test_lru_cache_returns_same_vector_for_same_text():
    a = embed_cv("Same text")
    b = embed_cv("Same text")
    assert a is not b  # we return a defensive copy
    assert a == b
