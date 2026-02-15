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


def test_authenticated_repo_url_injects_token(monkeypatch):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    secured = svc.authenticated_repo_url("https://github.com/test-org/sample.git")
    assert secured.startswith("https://x-access-token:abc123@github.com/")


def test_create_template_repo_prod_syncs_main(monkeypatch):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    task = SimpleNamespace(task_key="sample_task", repo_structure={"files": {"README.md": "hi"}})
    calls = []

    def fake_ensure(repo_name):
        calls.append(("ensure", repo_name))

    def fake_sync(repo_name, files):
        calls.append(("sync", repo_name, files.get("README.md")))

    monkeypatch.setattr(svc, "_ensure_repo_exists", fake_ensure)
    monkeypatch.setattr(svc, "_sync_repo_main_branch", fake_sync)

    repo_url = svc.create_template_repo(task)
    assert repo_url == "https://github.com/test-org/sample_task.git"
    assert calls[0] == ("ensure", "sample_task")
    assert calls[1] == ("sync", "sample_task", "hi")


def test_create_assessment_branch_prod_handles_collision(monkeypatch):
    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)
            self.content = b"x"

        def json(self):
            return self._payload

    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    task = SimpleNamespace(task_key="sample_task", repo_structure={"files": {"README.md": "hi"}})
    refs_attempted = []

    monkeypatch.setattr(svc, "create_template_repo", lambda _task: "https://github.com/test-org/sample_task.git")
    monkeypatch.setattr(svc, "_main_head_sha", lambda _repo_name: "deadbeef")

    def fake_request(method, path, **kwargs):
        if method == "POST" and path.endswith("/git/refs"):
            ref = (kwargs.get("json_payload") or {}).get("ref")
            refs_attempted.append(ref)
            if ref == "refs/heads/assessment/99":
                return _Resp(422, {"message": "Reference already exists"})
            return _Resp(201, {"ref": ref})
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(svc, "_request", fake_request)

    ctx = svc.create_assessment_branch(task, 99)
    assert ctx.branch_name == "assessment/99-1"
    assert ctx.repo_url == "https://github.com/test-org/sample_task.git"
    assert refs_attempted == ["refs/heads/assessment/99", "refs/heads/assessment/99-1"]
