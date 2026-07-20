"""Transaction ownership for role-level provider artifact refreshes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.organization import Organization
from app.models.role import Role
from app.services import role_provider_artifact_lifecycle as lifecycle
from app.services.role_provider_generation import capture_role_provider_generation
from tests.conftest import auth_headers


def _seed_role(client, db, *, label: str) -> int:
    headers, _ = auth_headers(
        client,
        organization_name=f"Lifecycle {label}",
    )
    created = client.post(
        "/api/v1/roles",
        json={"name": f"Lifecycle {label}"},
        headers=headers,
    ).json()
    role = db.get(Role, int(created["id"]))
    role.job_spec_text = f"Original provider inputs for {label}"
    role.interview_focus = {"questions": [{"question": "Old focus"}]}
    role.screening_pack_template = {
        "stage": "screening",
        "questions": [{"question": "Old screening"}],
    }
    role.tech_interview_pack_template = {
        "stage": "tech_stage_2",
        "questions": [{"question": "Old technical pack"}],
    }
    role.tech_questions_signature = f"old-generation-{label}"
    db.commit()
    return int(role.id)


def _stage_change(db, *, role_id: int, suffix: str) -> None:
    role = db.get(Role, int(role_id))
    previous = capture_role_provider_generation(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    role.job_spec_text = f"Revised provider inputs for {suffix}"
    assert lifecycle.invalidate_role_provider_artifacts_if_changed(
        db,
        role=role,
        previous=previous,
    )


def test_refresh_dispatches_only_after_root_commit(client, db):
    role_id = _seed_role(client, db, label="commit")

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        _stage_change(db, role_id=role_id, suffix="commit")
        assert lifecycle._SESSION_PENDING_KEY in db.info
        focus_dispatch.assert_not_called()
        tech_dispatch.assert_not_called()

        db.commit()

    focus_dispatch.assert_called_once_with(role_id, requires_running_agent=False)
    tech_dispatch.assert_called_once_with(role_id)
    assert lifecycle._SESSION_PENDING_KEY not in db.info


def test_rollback_discards_refresh_and_restores_artifacts(client, db):
    role_id = _seed_role(client, db, label="rollback")

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        _stage_change(db, role_id=role_id, suffix="rollback")
        db.rollback()

    focus_dispatch.assert_not_called()
    tech_dispatch.assert_not_called()
    assert lifecycle._SESSION_PENDING_KEY not in db.info
    db.expire_all()
    role = db.get(Role, role_id)
    assert role.interview_focus == {"questions": [{"question": "Old focus"}]}
    assert role.tech_questions_signature == "old-generation-rollback"


@pytest.mark.parametrize("reset_method", ["close", "reset"])
def test_implicit_session_reset_cannot_release_stale_refresh_on_reuse(
    client, db, reset_method
):
    role_id = _seed_role(client, db, label=reset_method)
    role = db.get(Role, role_id)
    organization_id = int(role.organization_id)

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        _stage_change(db, role_id=role_id, suffix=f"abandoned-{reset_method}")
        assert lifecycle._SESSION_PENDING_KEY in db.info

        getattr(db, reset_method)()
        assert lifecycle._SESSION_PENDING_KEY not in db.info

        organization = db.get(Organization, organization_id)
        organization.name = f"Unrelated commit after {reset_method}"
        db.commit()
        focus_dispatch.assert_not_called()
        tech_dispatch.assert_not_called()

        # The per-Session listeners remain installed after reset and correctly
        # serve a new relevant transaction on the reused Session.
        _stage_change(db, role_id=role_id, suffix=f"fresh-{reset_method}")
        db.commit()

    focus_dispatch.assert_called_once_with(role_id, requires_running_agent=False)
    tech_dispatch.assert_called_once_with(role_id)


def test_nested_commit_defers_refresh_until_root_commit(client, db):
    role_id = _seed_role(client, db, label="nested-commit")

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        with db.begin_nested():
            _stage_change(db, role_id=role_id, suffix="nested-commit")
            focus_dispatch.assert_not_called()
            tech_dispatch.assert_not_called()

        focus_dispatch.assert_not_called()
        tech_dispatch.assert_not_called()
        pending = db.info[lifecycle._SESSION_PENDING_KEY]
        assert set(pending) == {db.get_transaction()}
        db.commit()

    focus_dispatch.assert_called_once_with(role_id, requires_running_agent=False)
    tech_dispatch.assert_called_once_with(role_id)


def test_nested_rollback_discards_child_refresh_but_keeps_outer_refresh(client, db):
    outer_role_id = _seed_role(client, db, label="nested-outer")
    child_role_id = _seed_role(client, db, label="nested-child")

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        _stage_change(db, role_id=outer_role_id, suffix="outer")
        savepoint = db.begin_nested()
        _stage_change(db, role_id=child_role_id, suffix="child")
        savepoint.rollback()
        db.commit()

    focus_dispatch.assert_called_once_with(
        outer_role_id, requires_running_agent=False
    )
    tech_dispatch.assert_called_once_with(outer_role_id)
