"""Fluency-axis map — the single source of truth for how one graded rubric
dimension rolls up to a scorecard axis.

Deliberately stdlib-only so the task-spec validator (and the CI gate that runs
it) can import this without pulling in the Anthropic client, the DB session, or
metering — all of which ``rubric_scoring`` needs but a spec validator does not.

The mapping mirrors ``frontend/src/shared/assessment/fluency4d.js``; the two
must agree or the UI grouping will disagree with the stored ``fluency_4d``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

# Anthropic's AI-Fluency "4 Ds" plus a Deliverable/outcome axis.
FLUENCY_AXES = ("delegation", "description", "discernment", "diligence", "deliverable")

# Grader -> axis, for dimensions that carry a grader instead of a lens.
# interrogation_outcome is inherently decision-lens (the loader rejects a dim
# that declares both), and practice_outcome grades observed AI-native practice,
# which is a Description signal.
_GRADER_AXES: Dict[str, str] = {
    "interrogation_outcome": "delegation",
    "practice_outcome": "description",
    # The post-submit understanding check asks whether the candidate can read
    # back what the agent produced for them. That is Discernment — critically
    # evaluating the agent's output — not Diligence, which is about verifying
    # before claiming done.
    "comprehension_outcome": "discernment",
}

# Lens -> axis. Every lens in ``task_spec_loader._SUPPORTED_LENSES`` must have
# an entry here; ``unmapped_lenses()`` is asserted empty by the loader's tests
# so adding a lens without deciding its axis fails CI rather than silently
# landing on the back-compat default.
_LENS_AXES: Dict[str, str] = {
    "decision": "delegation",
    "delegation": "delegation",
    "description": "description",
    "discernment": "discernment",
    "diligence": "diligence",
    "deliverable": "deliverable",
    "practice": "description",
}

# A dimension with neither an explicit axis, a known grader, nor a lens is a
# pre-lens-model spec. Those are all decision-ownership dims in practice, so
# they roll up to delegation. New specs should never rely on this.
_BACK_COMPAT_AXIS = "delegation"


def fluency_axis_for_dimension(spec: Dict[str, Any]) -> str:
    """Map one rubric-dimension spec to its fluency axis.

    Precedence: explicit ``fluency`` > ``grader`` > ``lens`` > back-compat
    default. This order is load-bearing — changing it re-buckets historical
    grades.
    """
    if not isinstance(spec, dict):
        return _BACK_COMPAT_AXIS
    explicit = str(spec.get("fluency") or "").strip().lower()
    if explicit in FLUENCY_AXES:
        return explicit
    grader = str(spec.get("grader") or "").strip().lower()
    if grader in _GRADER_AXES:
        return _GRADER_AXES[grader]
    lens = str(spec.get("lens") or "").strip().lower()
    if lens in _LENS_AXES:
        return _LENS_AXES[lens]
    return _BACK_COMPAT_AXIS


def unmapped_lenses(supported_lenses: Set[str]) -> List[str]:
    """Lenses that are accepted by the spec validator but have no axis entry.

    Any lens here would silently fall through to the back-compat axis, which is
    how 8 of 10 production tasks came to report a Delegation-only rubric.
    """
    return sorted(lens for lens in supported_lenses if lens not in _LENS_AXES)


def axes_covered_by_rubric(evaluation_rubric: Optional[Dict[str, Any]]) -> Set[str]:
    """The set of fluency axes at least one graded dimension rolls up to."""
    covered: Set[str] = set()
    for spec in (evaluation_rubric or {}).values():
        if isinstance(spec, dict):
            covered.add(fluency_axis_for_dimension(spec))
    return covered


def missing_fluency_axes(evaluation_rubric: Optional[Dict[str, Any]]) -> List[str]:
    """Axes with no contributing dimension, in canonical order."""
    covered = axes_covered_by_rubric(evaluation_rubric)
    return [axis for axis in FLUENCY_AXES if axis not in covered]


_EXEMPTION_KEYS = ("axes", "reason", "reviewed_by", "reviewed_on")
_MIN_EXEMPTION_REASON_CHARS = 40


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_fluency_coverage(
    evaluation_rubric: Optional[Dict[str, Any]],
    exemption: Any = None,
) -> List[str]:
    """Every task must grade all five fluency axes, or declare why it doesn't.

    We publish a five-axis scorecard per candidate. A task whose rubric only
    reaches two axes leaves three ungraded, and the report used to backfill
    those from behavioural heuristics — which is not a grade. Catch that here,
    at spec-load and in CI, rather than in a customer's report.

    A task may opt out per-axis via ``fluency_coverage_exemption``, but the
    exemption must name the axes, carry a substantive reason, and record who
    reviewed it and when — so an exemption is a visible decision, not a
    default.

    Lives here rather than in ``task_spec_loader`` so the CI gate can import it
    without the loader's transitive FastAPI/SQLAlchemy/Anthropic dependencies.
    """
    errors: List[str] = []
    if not isinstance(evaluation_rubric, dict):
        return errors

    missing = missing_fluency_axes(evaluation_rubric)

    if exemption is None:
        if missing:
            errors.append(
                "evaluation_rubric grades no dimension for fluency axis/axes "
                + ", ".join(missing)
                + ". Add a dimension for each (see the output_scrutiny / "
                "verification_before_done / ai_native_practice pattern in "
                "data_eng_bronze_ingestion.json), or declare a reviewed "
                "fluency_coverage_exemption."
            )
        return errors

    if not isinstance(exemption, dict):
        errors.append("fluency_coverage_exemption must be an object")
        return errors

    for key in _EXEMPTION_KEYS:
        if key not in exemption:
            errors.append(f"fluency_coverage_exemption.{key} is required")

    axes = exemption.get("axes")
    exempt: Set[str] = set()
    if not isinstance(axes, list) or not axes:
        errors.append("fluency_coverage_exemption.axes must be a non-empty list of axis names")
    else:
        for axis in axes:
            if not isinstance(axis, str) or axis not in FLUENCY_AXES:
                errors.append(
                    f"fluency_coverage_exemption.axes entry {axis!r} must be one of {list(FLUENCY_AXES)}"
                )
            else:
                exempt.add(axis)

    reason = exemption.get("reason")
    if not _is_non_empty_string(reason):
        errors.append("fluency_coverage_exemption.reason is required")
    elif len(str(reason).strip()) < _MIN_EXEMPTION_REASON_CHARS:
        errors.append(
            "fluency_coverage_exemption.reason must explain why the axis cannot be "
            f"graded (at least {_MIN_EXEMPTION_REASON_CHARS} characters)"
        )
    for key in ("reviewed_by", "reviewed_on"):
        if key in exemption and not _is_non_empty_string(exemption.get(key)):
            errors.append(f"fluency_coverage_exemption.{key} must be a non-empty string")

    uncovered = [axis for axis in missing if axis not in exempt]
    if uncovered:
        errors.append(
            "evaluation_rubric grades no dimension for fluency axis/axes "
            + ", ".join(uncovered)
            + ", and they are not covered by fluency_coverage_exemption.axes"
        )

    # A stale exemption is worse than none — it hides that the gap was closed.
    stale = sorted(axis for axis in exempt if axis not in missing)
    if stale:
        errors.append(
            "fluency_coverage_exemption.axes lists "
            + ", ".join(stale)
            + " but the rubric now grades that axis; drop the stale exemption"
        )
    return errors
