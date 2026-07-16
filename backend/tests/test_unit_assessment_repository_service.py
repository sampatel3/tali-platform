import subprocess
from types import SimpleNamespace

from app.services.assessment_repository_service import AssessmentRepositoryService


def test_create_assessment_branch_with_collision_suffix(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(tmp_path))

    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    task = SimpleNamespace(task_key="data_eng_super_platform_crisis", repo_structure={"files": {"README.md": "hi"}})

    first = svc.create_assessment_branch(task, 12)
    second = svc.create_assessment_branch(task, 12)

    assert first.branch_name == "assessment/12"
    assert second.branch_name.startswith("assessment/12-")
    assert "--branch" in second.clone_command


def test_mock_branch_collision_enumerates_refs_once(monkeypatch, tmp_path):
    """A large collision set must not spawn one Git process per suffix."""
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    task = SimpleNamespace(task_key="collision-heavy", repo_structure={})
    monkeypatch.setattr(svc, "_ensure_mock_repo", lambda *_args: tmp_path)

    enumeration_calls = []
    run_calls = []
    occupied = ["main", "assessment/12"] + [
        f"assessment/12-{suffix}" for suffix in range(1, 501)
    ]

    def fake_run_strict(args, cwd, context):
        enumeration_calls.append(tuple(args))
        assert cwd == tmp_path
        assert context == "list mock branches"
        return subprocess.CompletedProcess(args, 0, stdout="\n".join(occupied), stderr="")

    def fake_run(args, cwd):
        run_calls.append(tuple(args))
        assert cwd == tmp_path
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(svc, "_run_strict", fake_run_strict)
    monkeypatch.setattr(svc, "_run", fake_run)

    result = svc.create_assessment_branch(task, 12)

    assert result.branch_name == "assessment/12-501"
    assert enumeration_calls == [
        (
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        )
    ]
    assert run_calls == [
        ("git", "checkout", "main"),
        ("git", "checkout", "-b", "assessment/12-501"),
    ]


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

    # main is stale / repo is new -> a full sync is required.
    monkeypatch.setattr(svc, "_template_is_current", lambda _repo, _files: False)
    monkeypatch.setattr(svc, "_ensure_repo_exists", fake_ensure)
    monkeypatch.setattr(svc, "_sync_repo_main_branch", fake_sync)
    monkeypatch.setattr(svc, "_stamp_template_hash", lambda repo_name, files: calls.append(("stamp", repo_name)))

    repo_url = svc.create_template_repo(task)
    assert repo_url == "https://github.com/test-org/sample_task.git"
    assert calls[0] == ("ensure", "sample_task")
    assert calls[1] == ("sync", "sample_task", "hi")
    # The synced digest is stamped so the *next* send can skip the clone+push.
    assert ("stamp", "sample_task") in calls


def test_create_template_repo_skips_sync_when_current(monkeypatch):
    """Fast path: when GitHub main already holds these files (digest stamped in
    the repo description), the expensive clone+rewrite+push is skipped."""
    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    task = SimpleNamespace(task_key="sample_task", repo_structure={"files": {"README.md": "hi"}})

    monkeypatch.setattr(svc, "_template_is_current", lambda _repo, _files: True)

    def boom(*_args, **_kwargs):
        raise AssertionError("should not sync when the template is already current")

    monkeypatch.setattr(svc, "_ensure_repo_exists", boom)
    monkeypatch.setattr(svc, "_sync_repo_main_branch", boom)
    monkeypatch.setattr(svc, "_stamp_template_hash", boom)

    repo_url = svc.create_template_repo(task)
    assert repo_url == "https://github.com/test-org/sample_task.git"

    # ...unless forced (admin/template resync), which always re-pushes.
    calls = []
    monkeypatch.setattr(svc, "_ensure_repo_exists", lambda r: calls.append(("ensure", r)))
    monkeypatch.setattr(svc, "_sync_repo_main_branch", lambda r, f: calls.append(("sync", r)))
    monkeypatch.setattr(svc, "_stamp_template_hash", lambda r, f: calls.append(("stamp", r)))
    svc.create_template_repo(task, force=True)
    assert ("sync", "sample_task") in calls


def test_template_is_current_compares_description_digest(monkeypatch):
    """_template_is_current returns True only when the repo description stamps
    the exact digest of the files we're about to push."""
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
    files = {"README.md": "hi", "src/main.py": "print(1)"}
    digest = svc._files_digest(files)

    # Matching stamp -> current.
    monkeypatch.setattr(
        svc, "_request",
        lambda *a, **k: _Resp(200, {"description": f"{svc._TEMPLATE_HASH_PREFIX}{digest}"}),
    )
    assert svc._template_is_current("sample_task", files) is True

    # Different content -> stale.
    assert svc._template_is_current("sample_task", {"README.md": "changed"}) is False

    # No description / missing repo -> stale (force a correct full sync).
    monkeypatch.setattr(svc, "_request", lambda *a, **k: _Resp(200, {"description": None}))
    assert svc._template_is_current("sample_task", files) is False
    monkeypatch.setattr(svc, "_request", lambda *a, **k: _Resp(404, {}))
    assert svc._template_is_current("sample_task", files) is False


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
