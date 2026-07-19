"""Unit tests for auto-provisioning an assessment task from a role's JD."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.organization import Organization
from app.services.task_provisioning_service import (
    PROVISIONING_PENDING,
    TaskProvisioningBlockedError,
    TaskProvisioningRetryableError,
    _deliverable_kind_hint,
    _persist_generated_task,
    _slugify,
    provisioning_state_is_due,
    request_assessment_task_provisioning,
    role_has_active_task,
    role_has_linked_task,
    generate_and_link_task_for_role,
)
from app.services.task_spec_generator import GeneratedSpecResult


def _role(**kw):
    base = dict(id=7, organization_id=2, name="Security Engineer",
               job_spec_text="x" * 300, description="", tasks=[])
    base.update(kw)
    return SimpleNamespace(**base)


class TestHeuristics:
    def test_slugify(self):
        assert _slugify("Specialist - Vulnerability Management") == "specialist_vulnerability_management"

    def test_kind_hint_code(self):
        assert _deliverable_kind_hint("Backend Engineer", "build python services") == "code"

    def test_kind_hint_doc(self):
        assert _deliverable_kind_hint("Product Manager", "own the roadmap and prioritise") == "doc"

    def test_kind_hint_ambiguous_is_none(self):
        # "engineer" (code) + "manager" (doc) both present → defer to generator.
        assert _deliverable_kind_hint("Engineering Manager", "lead a team of engineers") is None


class TestRoleHasActiveTask:
    def test_no_tasks(self):
        assert role_has_active_task(MagicMock(), _role(tasks=[])) is False

    def test_inactive_task(self):
        assert role_has_active_task(MagicMock(), _role(tasks=[SimpleNamespace(is_active=False)])) is False

    def test_active_task(self):
        assert role_has_active_task(MagicMock(), _role(tasks=[SimpleNamespace(is_active=True)])) is True

    def test_any_linked_task_includes_inactive_draft(self):
        assert role_has_linked_task(_role(tasks=[SimpleNamespace(is_active=False)])) is True


class TestGenerateAndLink:
    def test_new_generated_task_does_not_persist_superseded_warmup(self, db):
        org = Organization(name="Generated Task Org", slug="generated-task-org")
        db.add(org)
        db.flush()
        task = _persist_generated_task(
            db,
            {
                "task_id": "generated_no_warmup",
                "name": "Generated without warmup",
                "role": "security_engineer",
                "calibration_prompt": "This legacy model output must be ignored",
                "scenario": "A production incident.",
                "repo_structure": {"name": "generated", "files": {"README.md": "x"}},
                "evaluation_rubric": {},
            },
            organization_id=org.id,
        )

        assert task.calibration_prompt is None
        assert "calibration_prompt" not in (task.extra_data or {})

    def test_skips_when_role_has_active_task(self):
        role = _role(tasks=[SimpleNamespace(is_active=True)])
        out = generate_and_link_task_for_role(MagicMock(), role, api_key="sk-x", organization_id=2)
        assert out is None

    def test_skips_when_role_has_inactive_draft(self):
        role = _role(tasks=[SimpleNamespace(is_active=False)])
        out = generate_and_link_task_for_role(
            MagicMock(), role, api_key="sk-x", organization_id=2
        )
        assert out is None

    def test_skips_when_jd_too_thin(self):
        role = _role(job_spec_text="short", description="")
        with pytest.raises(TaskProvisioningBlockedError, match="too thin"):
            generate_and_link_task_for_role(
                MagicMock(), role, api_key="sk-x", organization_id=2
            )

    @patch("app.services.task_provisioning_service.generate_task_spec")
    def test_skips_when_generation_invalid(self, mock_gen):
        mock_gen.return_value = GeneratedSpecResult(spec=None, valid=False, errors=["bad"], attempts=3)
        with pytest.raises(TaskProvisioningRetryableError, match="remained invalid"):
            generate_and_link_task_for_role(
                MagicMock(), _role(), api_key="sk-x", organization_id=2
            )

    @patch("app.services.task_provisioning_service._link_role_task")
    @patch("app.services.task_provisioning_service._provision_repo_best_effort")
    @patch("app.services.task_provisioning_service._persist_generated_task")
    @patch("app.services.task_provisioning_service.generate_task_spec")
    def test_happy_path_persists_draft_and_links(self, mock_gen, mock_persist, mock_repo, mock_link):
        spec = {"task_id": "secops_x", "name": "X", "role": "security_engineer"}
        mock_gen.return_value = GeneratedSpecResult(spec=spec, valid=True, errors=[], attempts=1)
        fake_task = SimpleNamespace(id=99, task_key="secops_x")
        mock_persist.return_value = fake_task
        db = MagicMock()
        out = generate_and_link_task_for_role(
            db, _role(), api_key="sk-x", organization_id=2
        )
        assert out is fake_task
        mock_repo.assert_called_once_with(db, fake_task)
        mock_link.assert_called_once()
        # The generator got the role's JD + a kind hint.
        kw = mock_gen.call_args.kwargs
        assert kw["organization_id"] == 2
        assert kw["role_slug"] == "security_engineer"
        assert kw["role_id"] == 7

    @patch("app.services.task_provisioning_service.generate_task_spec")
    def test_generation_exception_is_retryable(self, mock_gen):
        mock_gen.side_effect = RuntimeError("boom")
        with pytest.raises(TaskProvisioningRetryableError, match="boom"):
            generate_and_link_task_for_role(
                MagicMock(), _role(), api_key="sk-x", organization_id=2
            )


class TestDurableProvisioningIntent:
    def test_request_stamps_recoverable_state(self):
        role = _role()

        requested = request_assessment_task_provisioning(
            role, reason="requisition_publish"
        )

        assert requested is True
        assert role.assessment_task_provisioning["status"] == PROVISIONING_PENDING
        assert role.assessment_task_provisioning["reason"] == "requisition_publish"
        assert role.assessment_task_provisioning["request_id"]
        assert provisioning_state_is_due(role.assessment_task_provisioning) is True

    def test_existing_link_is_terminal_not_re_requested(self):
        role = _role(tasks=[SimpleNamespace(id=91, is_active=False)])

        requested = request_assessment_task_provisioning(
            role, reason="requisition_publish"
        )

        assert requested is False
        assert role.assessment_task_provisioning["status"] == "succeeded"
        assert role.assessment_task_provisioning["task_id"] == 91

    def test_jd_change_supersedes_only_inactive_generated_review_draft(self):
        draft = SimpleNamespace(
            id=92,
            is_active=False,
            extra_data={
                "generated": True,
                "needs_review": True,
                "battle_test_provisioning": {
                    "status": "running",
                    "claim_token": "old-worker",
                },
            },
        )
        role = _role(tasks=[draft])

        requested = request_assessment_task_provisioning(
            role,
            reason="requisition_publish",
            supersede_generated_drafts=True,
        )

        assert requested is True
        assert role.tasks == []
        assert role.assessment_task_provisioning["status"] == PROVISIONING_PENDING
        assert role.assessment_task_provisioning["superseded_task_ids"] == [92]
        assert draft.extra_data["superseded"] is True
        assert draft.extra_data["needs_review"] is False
        assert draft.extra_data["battle_test_provisioning"]["status"] == "superseded"
        assert draft.extra_data["battle_test_provisioning"]["claim_token"] is None

    @pytest.mark.parametrize(
        "linked",
        [
            SimpleNamespace(
                id=93,
                is_active=True,
                extra_data={"generated": True, "needs_review": False},
            ),
            SimpleNamespace(id=94, is_active=False, extra_data={"generated": False}),
        ],
    )
    def test_jd_change_preserves_active_and_manual_tasks(self, linked):
        role = _role(tasks=[linked])

        requested = request_assessment_task_provisioning(
            role,
            reason="requisition_publish",
            supersede_generated_drafts=True,
        )

        assert requested is False
        assert role.tasks == [linked]
        assert role.assessment_task_provisioning["status"] == "succeeded"
        assert role.assessment_task_provisioning["task_id"] == linked.id
