"""Central task-design contract — enforced.

Every task in the catalog MUST satisfy the same structural contract
(``task_spec_loader.validate_task_spec``): required fields, rubric weights
summing to 1.0, exactly the agreed dimensions with an ``interrogation_outcome``
design dimension backed by ``decision_points``, a real repo (README + scenario
doc + tests + source + requirements), a test_runner, workspace_bootstrap,
scoring_hints, and a jd_to_signal_map covering every rubric dimension.

This test is the alignment guarantee: a new or edited task that drifts from the
shared design fails CI here rather than reaching candidates.
"""
from __future__ import annotations

import glob
import json
import os

import pytest

from app.services.task_catalog import canonical_task_catalog_dir
from app.services.task_spec_loader import validate_task_spec

_CATALOG = str(canonical_task_catalog_dir())
_SPEC_FILES = sorted(glob.glob(os.path.join(_CATALOG, "*.json")))


def test_catalog_is_non_empty():
    assert _SPEC_FILES, f"no task specs found in {_CATALOG}"


@pytest.mark.parametrize("spec_path", _SPEC_FILES, ids=[os.path.basename(p) for p in _SPEC_FILES])
def test_task_spec_conforms_to_central_contract(spec_path):
    spec = json.load(open(spec_path))
    result = validate_task_spec(spec)
    assert result.valid, (
        f"{os.path.basename(spec_path)} violates the central task-design contract:\n  - "
        + "\n  - ".join(result.errors)
    )


@pytest.mark.parametrize("spec_path", _SPEC_FILES, ids=[os.path.basename(p) for p in _SPEC_FILES])
def test_catalog_practice_dims_stay_flat_scored(spec_path):
    """Catalog practice dims must declare an explicit part.

    ``part_for_dimension`` maps a ``practice_outcome`` grader (or ``practice``
    lens) to Part 1 by default, which activates the two-stage 30/70 blend as
    the authoritative score. Catalog tasks score practice OBSERVED inside the
    flat rubric (part: "applied"); the announced two-stage variant exists only
    as an A/B arm (scripts/seed_two_stage_ab.py). An implicit-part practice
    dim here would silently flip a live task to two-stage scoring.
    """
    spec = json.load(open(spec_path))
    for dim_id, details in (spec.get("evaluation_rubric") or {}).items():
        if not isinstance(details, dict):
            continue
        is_practice = (
            str(details.get("grader") or "").strip() == "practice_outcome"
            or str(details.get("lens") or "").strip() == "practice"
        )
        if is_practice:
            assert str(details.get("part") or "").strip() in ("practice", "applied"), (
                f"{os.path.basename(spec_path)}: {dim_id} is a practice dim with no explicit "
                "part — it would silently activate the two-stage blend"
            )
