"""Unit tests for auto-provisioning an assessment task from a role's JD."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.task_provisioning_service import (
    _deliverable_kind_hint,
    _slugify,
    role_has_active_task,
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


class TestGenerateAndLink:
    def test_skips_when_role_has_active_task(self):
        role = _role(tasks=[SimpleNamespace(is_active=True)])
        out = generate_and_link_task_for_role(MagicMock(), role, api_key="sk-x", organization_id=2)
        assert out is None

    def test_skips_when_jd_too_thin(self):
        role = _role(job_spec_text="short", description="")
        out = generate_and_link_task_for_role(MagicMock(), role, api_key="sk-x", organization_id=2)
        assert out is None

    @patch("app.services.task_provisioning_service.generate_task_spec")
    def test_skips_when_generation_invalid(self, mock_gen):
        mock_gen.return_value = GeneratedSpecResult(spec=None, valid=False, errors=["bad"], attempts=3)
        out = generate_and_link_task_for_role(MagicMock(), _role(), api_key="sk-x", organization_id=2)
        assert out is None

    @patch("app.services.task_provisioning_service._link_role_task")
    @patch("app.services.task_provisioning_service._provision_repo_best_effort")
    @patch("app.services.task_provisioning_service._persist_generated_task")
    @patch("app.services.task_provisioning_service.generate_task_spec")
    def test_happy_path_persists_draft_and_links(self, mock_gen, mock_persist, mock_repo, mock_link):
        spec = {"task_id": "secops_x", "name": "X", "role": "security_engineer"}
        mock_gen.return_value = GeneratedSpecResult(spec=spec, valid=True, errors=[], attempts=1)
        fake_task = SimpleNamespace(id=99, task_key="secops_x")
        mock_persist.return_value = fake_task
        out = generate_and_link_task_for_role(MagicMock(), _role(), api_key="sk-x", organization_id=2)
        assert out is fake_task
        mock_repo.assert_called_once_with(fake_task)
        mock_link.assert_called_once()
        # The generator got the role's JD + a kind hint.
        kw = mock_gen.call_args.kwargs
        assert kw["organization_id"] == 2
        assert kw["role_slug"] == "security_engineer"

    @patch("app.services.task_provisioning_service.generate_task_spec")
    def test_generation_exception_is_swallowed(self, mock_gen):
        mock_gen.side_effect = RuntimeError("boom")
        out = generate_and_link_task_for_role(MagicMock(), _role(), api_key="sk-x", organization_id=2)
        assert out is None
