"""Cohort survey tools: state classifications and intent gaps."""

from __future__ import annotations

from app.agent_runtime.cohort_tools import (
    COHORT_STATES,
    find_apps_in_state,
    read_pending_recruiter_inputs,
    survey_role_state,
)

from .conftest import make_world


def test_survey_returns_zero_counts_on_empty_role(db):
    org, role, _, _ = make_world(db, cv_text=None, cv_file_url=None)
    out = survey_role_state(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    assert out["role_id"] == int(role.id)
    assert out["counts"]["needs_pre_screen"] == 0
    assert out["counts"]["needs_score"] == 0


def test_survey_classifies_apps_into_correct_states(db):
    org, role, _, app = make_world(db)
    out = survey_role_state(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    # cv_text + no pre_screen → needs_pre_screen = 1
    assert out["counts"]["needs_pre_screen"] == 1
    # No cv_file_url-only fetch needed
    assert out["counts"]["needs_cv_fetch"] == 0


def test_survey_flags_needs_score_when_pre_screen_passed(db):
    org, role, _, app = make_world(db, pre_screen=80.0)
    out = survey_role_state(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    assert out["counts"]["needs_score"] == 1
    assert out["counts"]["needs_pre_screen"] == 0


def test_survey_intent_gaps_lists_missing_config(db):
    org, role, _, _ = make_world(db)
    role.monthly_usd_budget_cents = None
    role.score_threshold = None
    role.additional_requirements = None
    db.flush()
    out = survey_role_state(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    gaps = out["intent_gaps"]
    assert any("monthly_usd_budget_cents" in g for g in gaps)
    assert any("score_threshold" in g for g in gaps)
    assert any("must-have" in g for g in gaps)


def test_find_apps_in_state_returns_ids(db):
    org, role, _, app = make_world(db)
    ids = find_apps_in_state(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        state="needs_pre_screen",
    )
    assert ids == [int(app.id)]


def test_find_apps_in_state_unknown_state_returns_empty(db):
    org, role, _, _ = make_world(db)
    ids = find_apps_in_state(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        state="totally_made_up_state",
    )
    assert ids == []


def test_states_constant_matches_dispatch_paths(db):
    """Source-grep guard: every state in COHORT_STATES has a query
    path. Adding a state name to the constant without a path would
    silently return 0 from survey_role_state.
    """
    import inspect

    from app.agent_runtime import cohort_tools

    source = inspect.getsource(cohort_tools._state_query)
    for state in COHORT_STATES:
        assert state in source, f"missing dispatch for state {state!r}"


def test_read_pending_recruiter_inputs_empty(db):
    org, role, _, _ = make_world(db)
    rows = read_pending_recruiter_inputs(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    assert rows == []
