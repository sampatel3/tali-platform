from types import SimpleNamespace

from app.services.assessment_repository_service import AssessmentRepositoryService


def test_create_assessment_branch_with_collision_suffix(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(tmp_path))

    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    task = SimpleNamespace(task_key="data_eng_c_backfill_schema", repo_structure={"files": {"README.md": "hi"}})

    first = svc.create_assessment_branch(task, 12)
    second = svc.create_assessment_branch(task, 12)

    assert first.branch_name == "assessment/12"
    assert second.branch_name.startswith("assessment/12-")
    assert "--branch" in second.clone_command
