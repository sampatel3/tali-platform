"""Tests for the archetype rubric system (RALPH 2.5 / 2.6)."""

from __future__ import annotations

from app.cv_matching.rubrics import (
    ArchetypeRubric,
    list_rubrics,
    load_rubric,
)


def test_aws_glue_archetype_loads_and_validates():
    rubric = load_rubric("aws_glue_data_engineer")
    assert isinstance(rubric, ArchetypeRubric)
    assert rubric.archetype_id == "aws_glue_data_engineer"
    assert rubric.jd_centroid_text  # non-empty
    assert any(c.cluster == "managed_spark_etl" for c in rubric.must_have_archetypes)


def test_aws_glue_dimension_weights_sum_close_to_one():
    rubric = load_rubric("aws_glue_data_engineer")
    total = sum(rubric.dimension_weights.values())
    assert abs(total - 1.0) < 1e-6


def test_normalised_dimension_weights_fills_defaults_and_sums_to_one():
    rubric = ArchetypeRubric(
        archetype_id="x",
        description="d",
        jd_centroid_text="j",
        must_have_archetypes=[],
        dimension_weights={"skills_coverage": 0.5},  # only one dim set
    )
    norm = rubric.normalised_dimension_weights()
    assert abs(sum(norm.values()) - 1.0) < 1e-9
    # All six dimensions present after normalisation.
    assert set(norm) == {
        "skills_coverage",
        "skills_depth",
        "title_trajectory",
        "seniority_alignment",
        "industry_match",
        "tenure_pattern",
    }


def test_seniority_anchors_have_concrete_band_descriptions():
    rubric = load_rubric("aws_glue_data_engineer")
    anchors = rubric.seniority_anchors
    # All five bands populated. Each ≥ 30 chars (the test for "concrete
    # candidate profile, not abstract quality language").
    for band in (
        anchors.band_100,
        anchors.band_75,
        anchors.band_50,
        anchors.band_25,
        anchors.band_0,
    ):
        assert len(band) > 30, f"anchor too short: {band!r}"


def test_list_rubrics_skips_underscore_files():
    rubrics = list(list_rubrics())
    archetype_ids = {r.archetype_id for r in rubrics}
    assert "aws_glue_data_engineer" in archetype_ids
    # Generation tooling files start with _ and must be skipped.
    for r in rubrics:
        assert not r.archetype_id.startswith("_")


def test_load_rubric_missing_raises():
    try:
        load_rubric("not_a_real_archetype_xyz")
    except FileNotFoundError as exc:
        assert "not_a_real_archetype_xyz" in str(exc)
        return
    raise AssertionError("expected FileNotFoundError for missing archetype")


def test_must_have_archetypes_have_substitution_lists():
    rubric = load_rubric("aws_glue_data_engineer")
    # The "managed_spark_etl" cluster is the headline must-have. It must
    # carry exact + strong + weak + unrelated lists with at least one
    # entry each — this is what the v4.2 prompt depends on for tier
    # classification.
    cluster = next(
        c for c in rubric.must_have_archetypes if c.cluster == "managed_spark_etl"
    )
    assert cluster.exact_matches
    assert cluster.strong_substitutes
    assert cluster.weak_substitutes
    assert cluster.unrelated
