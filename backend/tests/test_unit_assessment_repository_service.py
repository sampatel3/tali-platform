import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.assessment_repository_service import (
    AssessmentRepositoryService,
    sanitize_candidate_workspace_files,
)
from app.services.assessment_repository_types import AssessmentRepositoryError


def _initialize_outside_git_repo(path: Path) -> str:
    path.mkdir()
    (path / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "outside"], cwd=path, check=True)
    subprocess.run(["git", "add", "baseline.txt"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Outside",
            "-c",
            "user.email=outside@example.com",
            "commit",
            "-m",
            "Outside baseline",
        ],
        cwd=path,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def test_authenticated_repo_url_injects_token(monkeypatch):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    secured = svc.authenticated_repo_url("https://github.com/test-org/sample.git")
    assert secured.startswith("https://x-access-token:abc123@github.com/")


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.py",
        "/absolute/path.py",
        "src/../../outside.py",
        "src/.git/config",
        "src/.GiT./config",
        "src\\..\\outside.py",
        r"C:\Windows\outside.py",
        "src//duplicate-separator.py",
    ],
)
def test_candidate_workspace_files_reject_unsafe_paths(unsafe_path):
    with pytest.raises(AssessmentRepositoryError, match="Unsafe candidate workspace path"):
        sanitize_candidate_workspace_files({"files": {unsafe_path: "content"}})


def test_candidate_workspace_files_normalize_safe_paths_and_reject_aliases():
    assert sanitize_candidate_workspace_files(
        {"files": {"src\\main.py": "print('safe')", ".gitignore": ".venv/"}}
    ) == {
        "src/main.py": "print('safe')",
        ".gitignore": ".venv/",
    }

    with pytest.raises(AssessmentRepositoryError, match="duplicate path"):
        sanitize_candidate_workspace_files(
            {"files": {"src/main.py": "one", "src\\main.py": "two"}}
        )


@pytest.mark.parametrize(
    "files",
    [
        {"src": "file", "src/main.py": "child"},
        {"src/main.py": "child", "src": "file"},
    ],
)
def test_candidate_workspace_files_reject_file_parent_conflicts(files):
    with pytest.raises(AssessmentRepositoryError, match="file/parent conflict"):
        sanitize_candidate_workspace_files({"files": files})


def test_mock_repository_rejects_file_parent_conflict_before_any_write(
    monkeypatch,
    tmp_path,
):
    mock_root = tmp_path / "mock-root"
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")

    with pytest.raises(AssessmentRepositoryError, match="file/parent conflict"):
        svc._ensure_mock_repo(
            "safe-repo",
            {"src": "file", "src/main.py": "child"},
        )

    assert not mock_root.exists()


def test_local_template_writer_does_not_follow_parent_symlink(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "src").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AssessmentRepositoryError, match="Unsafe local repository target"):
        svc._write_repo_files(repo, {"src/escaped.py": "print('unsafe')\n"})

    assert not (outside / "escaped.py").exists()


@pytest.mark.parametrize("task_key", [".", "..", ".git", ".GIT."])
def test_local_repository_name_rejects_reserved_segments(monkeypatch, task_key):
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")

    with pytest.raises(AssessmentRepositoryError, match="repository name is unsafe"):
        svc._repo_name(SimpleNamespace(task_key=task_key))


def test_mock_repository_rejects_symlinked_root(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    mock_root = tmp_path / "mock-root"
    mock_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")

    with pytest.raises(AssessmentRepositoryError, match="path is unsafe"):
        svc._ensure_mock_repo("safe-repo", {"README.md": "unsafe\n"})

    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.parametrize("symlink_segment", ["organization", "repository"])
def test_mock_repository_replaces_child_symlink_without_touching_target(
    monkeypatch,
    tmp_path,
    symlink_segment,
):
    mock_root = tmp_path / "mock-root"
    mock_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    organization = mock_root / "test-org"
    if symlink_segment == "organization":
        organization.symlink_to(outside, target_is_directory=True)
    else:
        organization.mkdir()
        (organization / "safe-repo").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")

    repo = svc._ensure_mock_repo("safe-repo", {"README.md": "safe\n"})

    assert repo == mock_root / "test-org" / "safe-repo"
    assert not organization.is_symlink()
    assert not repo.is_symlink()
    assert (repo / "README.md").read_text(encoding="utf-8") == "safe\n"
    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert list(outside.iterdir()) == [sentinel]


def test_mock_repository_detects_path_swap_after_git_init(monkeypatch, tmp_path):
    mock_root = tmp_path / "mock-root"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    repo = mock_root / "test-org" / "safe-repo"
    real_run = svc._run_pinned
    commands = []

    def swap_path_after_git_init(args, repo_fd):
        commands.append(args)
        result = real_run(args, repo_fd)
        if args[:2] == ["git", "init"]:
            repo.rename(repo.with_name(f"{repo.name}-displaced"))
            repo.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(svc, "_run_pinned", swap_path_after_git_init)

    with pytest.raises(AssessmentRepositoryError, match="path changed"):
        svc._ensure_mock_repo("safe-repo", {"README.md": "unsafe\n"})

    assert commands == [["git", "init", "-b", "main"]]
    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert not (outside / "README.md").exists()


def test_mock_git_add_cannot_be_redirected_by_repository_path_swap(
    monkeypatch,
    tmp_path,
):
    mock_root = tmp_path / "mock-root"
    outside = tmp_path / "outside"
    outside_head = _initialize_outside_git_repo(outside)
    (outside / "outside-untracked.txt").write_text("do not stage\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    repo = mock_root / "test-org" / "safe-repo"
    real_run = svc._run_pinned
    swapped = False

    def swap_before_git_add(args, repo_fd):
        nonlocal swapped
        if not swapped and args == ["git", "add", "-A"]:
            repo.rename(repo.with_name(f"{repo.name}-displaced"))
            repo.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_run(args, repo_fd)

    monkeypatch.setattr(svc, "_run_pinned", swap_before_git_add)

    with pytest.raises(AssessmentRepositoryError, match="path changed"):
        svc._ensure_mock_repo("safe-repo", {"README.md": "safe\n"})

    outside_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    outside_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert swapped is True
    assert outside_after == outside_head
    assert outside_status == "?? outside-untracked.txt"


def test_assessment_branch_git_cannot_escape_pinned_repository_context(
    monkeypatch,
    tmp_path,
):
    mock_root = tmp_path / "mock-root"
    outside = tmp_path / "outside"
    outside_head = _initialize_outside_git_repo(outside)
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    svc = AssessmentRepositoryService(github_org="test-org", github_token="x")
    task = SimpleNamespace(
        task_key="safe-repo",
        repo_structure={"files": {"README.md": "safe\n"}},
    )
    repo = mock_root / "test-org" / "safe-repo"
    real_run = svc._run_pinned
    swapped = False

    def swap_before_branch_checkout(args, repo_fd):
        nonlocal swapped
        if not swapped and args == ["git", "checkout", "main"]:
            repo.rename(repo.with_name(f"{repo.name}-displaced"))
            repo.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_run(args, repo_fd)

    monkeypatch.setattr(svc, "_run_pinned", swap_before_branch_checkout)

    with pytest.raises(AssessmentRepositoryError, match="path changed"):
        svc.create_assessment_branch(task, 12)

    outside_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    outside_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert swapped is True
    assert outside_after == outside_head
    assert outside_branch == "outside"


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
        lambda *a, **k: _Resp(200, {
            "description": f"{svc._TEMPLATE_HASH_PREFIX}{digest}",
            "private": True,
        }),
    )
    assert svc._template_is_current("sample_task", files) is True

    # Different content -> stale.
    assert svc._template_is_current("sample_task", {"README.md": "changed"}) is False

    # No description / missing repo -> stale (force a correct full sync).
    monkeypatch.setattr(svc, "_request", lambda *a, **k: _Resp(200, {"description": None, "private": True}))
    assert svc._template_is_current("sample_task", files) is False
    monkeypatch.setattr(svc, "_request", lambda *a, **k: _Resp(404, {}))
    assert svc._template_is_current("sample_task", files) is False


def test_template_is_never_current_when_repository_is_public(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"x"

        def json(self):
            return {
                "description": "taali-template-sha1:anything",
                "private": False,
            }

    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    monkeypatch.setattr(svc, "_request", lambda *a, **k: _Resp())

    assert svc._template_is_current("sample_task", {"README.md": "hi"}) is False


def test_existing_public_template_repository_is_hardened_to_private(monkeypatch):
    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self.content = b"x"
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    svc = AssessmentRepositoryService(github_org="test-org", github_token="abc123")
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_payload")))
        if method == "GET":
            return _Resp({"private": False})
        if method == "PATCH":
            return _Resp({"private": True})
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(svc, "_request", fake_request)

    svc._ensure_repo_exists("sample-task")

    assert calls == [
        ("GET", "/repos/test-org/sample-task", None),
        (
            "PATCH",
            "/repos/test-org/sample-task",
            {"private": True, "visibility": "private"},
        ),
    ]


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
