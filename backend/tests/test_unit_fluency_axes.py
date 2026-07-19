"""Fluency-axis map + coverage validation.

The bug these guard against: a rubric dimension whose lens has no explicit axis
entry falls through to the back-compat delegation axis, so a task can look like
it grades Delegation universally while three axes are never graded at all.
"""

import re
from pathlib import Path

import pytest

from app.components.assessments.fluency_axes import (
    _GRADER_AXES,
    _LENS_AXES,
    FLUENCY_AXES,
    axes_covered_by_rubric,
    fluency_axis_for_dimension,
    missing_fluency_axes,
    unmapped_lenses,
    validate_fluency_coverage,
)
from app.services.task_spec_loader import _SUPPORTED_LENSES


def _rubric(*dims):
    return {f"d{i}": dim for i, dim in enumerate(dims)}


class TestLensVocabulary:
    def test_every_supported_lens_has_an_explicit_axis(self):
        """A lens the spec validator accepts must map to an axis deliberately.

        Adding a lens to _SUPPORTED_LENSES without an axis entry would silently
        route it to delegation — the original defect.
        """
        assert unmapped_lenses(set(_SUPPORTED_LENSES)) == []

    @pytest.mark.parametrize(
        "lens,expected",
        [
            ("decision", "delegation"),   # decision-ownership, not a fallthrough
            ("practice", "description"),  # observed AI-native practice
            ("discernment", "discernment"),
            ("diligence", "diligence"),
            ("deliverable", "deliverable"),
        ],
    )
    def test_lens_maps_to_expected_axis(self, lens, expected):
        assert fluency_axis_for_dimension({"lens": lens}) == expected

    def test_explicit_fluency_field_wins_over_grader_and_lens(self):
        spec = {"fluency": "diligence", "grader": "interrogation_outcome", "lens": "deliverable"}
        assert fluency_axis_for_dimension(spec) == "diligence"

    def test_grader_wins_over_lens(self):
        assert fluency_axis_for_dimension({"grader": "practice_outcome"}) == "description"
        assert (
            fluency_axis_for_dimension({"grader": "interrogation_outcome"}) == "delegation"
        )

    def test_unset_dimension_keeps_back_compat_delegation(self):
        assert fluency_axis_for_dimension({}) == "delegation"
        assert fluency_axis_for_dimension(None) == "delegation"


class TestFrontendMirrorStaysInSync:
    """frontend/src/shared/assessment/fluency4d.js re-implements this map.

    If the two drift, the report groups a criterion under a different axis than
    the stored fluency_4d rolled it up into — a silent, per-candidate wrong
    answer. Parsing the JS object literals is crude but catches the drift; the
    alternative is discovering it in a customer's report.
    """

    JS = Path(__file__).resolve().parents[2] / "frontend/src/shared/assessment/fluency4d.js"

    def _parse_js_map(self, name):
        source = self.JS.read_text()
        match = re.search(rf"const {name} = \{{(.*?)\n\}};", source, re.DOTALL)
        assert match, f"could not find `const {name}` in {self.JS.name}"
        return dict(re.findall(r"(\w+):\s*'([\w]+)'", match.group(1)))

    def test_lens_axis_map_matches(self):
        assert self._parse_js_map("LENS_AXES") == dict(_LENS_AXES)

    def test_grader_axis_map_matches(self):
        assert self._parse_js_map("GRADER_AXES") == dict(_GRADER_AXES)


class TestCoverage:
    def test_flagship_shape_covers_all_five_axes(self):
        rubric = _rubric(
            {"lens": "decision"},
            {"grader": "interrogation_outcome"},
            {"lens": "discernment"},
            {"lens": "diligence"},
            {"grader": "practice_outcome", "fluency": "description"},
            {"lens": "deliverable"},
        )
        assert axes_covered_by_rubric(rubric) == set(FLUENCY_AXES)
        assert validate_fluency_coverage(rubric) == []

    def test_delegation_and_deliverable_only_is_rejected(self):
        rubric = _rubric({"lens": "decision"}, {"lens": "deliverable"})
        assert missing_fluency_axes(rubric) == ["description", "discernment", "diligence"]
        errors = validate_fluency_coverage(rubric)
        assert len(errors) == 1
        assert "description, discernment, diligence" in errors[0]


class TestExemption:
    RUBRIC = _rubric({"lens": "decision"}, {"lens": "deliverable"})
    REASON = "This task ships a written artifact only; there is no agent output to scrutinise."

    def _exemption(self, **overrides):
        base = {
            "axes": ["description", "discernment", "diligence"],
            "reason": self.REASON,
            "reviewed_by": "sam@taali.ai",
            "reviewed_on": "2026-07-19",
        }
        base.update(overrides)
        return base

    def test_complete_exemption_passes(self):
        assert validate_fluency_coverage(self.RUBRIC, self._exemption()) == []

    def test_partial_exemption_still_reports_uncovered_axes(self):
        errors = validate_fluency_coverage(self.RUBRIC, self._exemption(axes=["discernment"]))
        assert any("description, diligence" in e for e in errors)

    def test_thin_reason_is_rejected(self):
        errors = validate_fluency_coverage(self.RUBRIC, self._exemption(reason="n/a"))
        assert any("at least" in e for e in errors)

    def test_missing_review_metadata_is_rejected(self):
        exemption = self._exemption()
        del exemption["reviewed_by"]
        errors = validate_fluency_coverage(self.RUBRIC, exemption)
        assert any("reviewed_by is required" in e for e in errors)

    def test_unknown_axis_name_is_rejected(self):
        errors = validate_fluency_coverage(self.RUBRIC, self._exemption(axes=["delegation!"]))
        assert any("must be one of" in e for e in errors)

    def test_stale_exemption_is_rejected(self):
        """An exemption for an axis the rubric now grades hides that the gap closed."""
        rubric = _rubric(
            {"lens": "decision"},
            {"lens": "deliverable"},
            {"lens": "discernment"},
            {"lens": "diligence"},
            {"grader": "practice_outcome", "fluency": "description"},
        )
        errors = validate_fluency_coverage(rubric, self._exemption())
        assert any("drop the stale exemption" in e for e in errors)
