import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.repository_path_safety as path_safety
import app.services.task_repo_publication as task_repo_publication
import app.services.task_repo_service as task_repo_service
import app.services.task_repo_transaction_state as task_repo_transaction_state
from app.services.task_repo_service import (
    UnsafeRepositoryPathError,
    recreate_task_main_repo,
    task_main_repo_path,
    write_repo_file,
)


def _task(repo_structure: dict, *, task_id: int = 71) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        organization_id=9,
        task_key="repo-path-contract",
        name="Repository Path Contract",
        repo_structure=repo_structure,
    )


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


def test_recreate_task_repo_preserves_normal_relative_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task(
        {
            "files": {
                "README.md": "# Safe task\n",
                "src/main.py": "print('ok')\n",
                "docs/design notes.md": "Useful detail\n",
                "windows\\style\\path.txt": "compatible\n",
                ".gitignore": ".venv/\n",
            }
        }
    )

    repo = Path(recreate_task_main_repo(task))

    assert (repo / "README.md").read_text(encoding="utf-8") == "# Safe task\n"
    assert (repo / "src/main.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (repo / "docs/design notes.md").read_text(
        encoding="utf-8"
    ) == "Useful detail\n"
    assert (repo / "windows/style/path.txt").read_text(
        encoding="utf-8"
    ) == "compatible\n"
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".venv/\n"
    assert (repo / ".git").is_dir()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.py",
        "/absolute/path.py",
        "src/../../outside.py",
        r"C:\Windows\outside.py",
        "src//empty-segment.py",
        ".git/config",
        "src/.GIT/hooks/pre-commit",
        "src/.git./config",
    ],
)
def test_recreate_task_repo_rejects_unsafe_manifest_atomically(
    monkeypatch,
    tmp_path,
    unsafe_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_git_config = (repo / ".git/config").read_bytes()

    task.repo_structure = {
        "files": {
            "would-be-partial.txt": "must not publish\n",
            unsafe_path: "unsafe\n",
        }
    }

    with pytest.raises(UnsafeRepositoryPathError, match="Unsafe repository file path"):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "would-be-partial.txt").exists()
    assert (repo / ".git/config").read_bytes() == original_git_config


def test_repo_writer_rejects_parent_symlink_without_touching_target(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "src").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeRepositoryPathError, match="filesystem target"):
        write_repo_file(repo, "src/escaped.txt", "must stay inside\n")

    assert not (outside / "escaped.txt").exists()


def test_recreate_task_repo_rejects_separator_aliases_before_publish(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    task.repo_structure = {
        "files": {
            "src/main.py": "first\n",
            "src\\main.py": "second\n",
        }
    }

    with pytest.raises(
        UnsafeRepositoryPathError, match="Duplicate repository file path"
    ):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "src").exists()


@pytest.mark.parametrize(
    "files",
    [
        {"src": "file\n", "src/main.py": "child\n"},
        {"src/main.py": "child\n", "src": "file\n"},
    ],
)
def test_recreate_task_repo_rejects_file_parent_conflict_before_any_write(
    monkeypatch,
    tmp_path,
    files,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))

    with pytest.raises(UnsafeRepositoryPathError, match="file/parent conflict"):
        recreate_task_main_repo(_task({"files": files}))

    assert not repos_root.exists()


def test_repo_writer_replaces_final_symlink_instead_of_following_it(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.txt"
    repo.mkdir()
    outside.write_text("outside stays unchanged\n", encoding="utf-8")
    (repo / "result.txt").symlink_to(outside)

    write_repo_file(repo, "result.txt", "safe replacement\n")

    assert outside.read_text(encoding="utf-8") == "outside stays unchanged\n"
    assert not (repo / "result.txt").is_symlink()
    assert (repo / "result.txt").read_text(encoding="utf-8") == "safe replacement\n"


def test_recreate_task_repo_replaces_root_symlink_without_following_it(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "fresh repo\n"}})
    repo = Path(task_main_repo_path(task))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not delete\n", encoding="utf-8")
    repo.symlink_to(outside, target_is_directory=True)

    recreated = Path(recreate_task_main_repo(task))

    assert recreated == repo
    assert not recreated.is_symlink()
    assert (recreated / "README.md").read_text(encoding="utf-8") == "fresh repo\n"
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"


def test_recreate_task_repo_restores_prior_snapshot_when_publish_fails(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    original_git_config = (repo / ".git/config").read_bytes()
    original_head = (repo / ".git/HEAD").read_bytes()
    task.repo_structure = {"files": {"README.md": "replacement\n"}}

    real_replace = os.replace
    publish_failed = False

    def fail_staging_publish(src, dst, *args, **kwargs):
        nonlocal publish_failed
        if not publish_failed and str(src).startswith("staging-") and dst == repo.name:
            publish_failed = True
            raise OSError("injected publish failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(task_repo_service.os, "replace", fail_staging_publish)

    with pytest.raises(OSError, match="injected publish failure"):
        recreate_task_main_repo(task)

    assert publish_failed is True
    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert (repo / ".git/config").read_bytes() == original_git_config
    assert (repo / ".git/HEAD").read_bytes() == original_head
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    assert not list(transaction_dir.glob("backup-*"))
    assert not list(transaction_dir.glob("staging-*"))


def test_publish_rejects_last_moment_staging_swap_and_restores_prior_snapshot(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    task.repo_structure = {"files": {"README.md": "replacement\n"}}
    real_replace = task_repo_publication.os.replace
    swapped = False

    def swap_staging_at_publish(src, dst, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(src).startswith("staging-") and dst == repo.name:
            staging = transaction_dir / str(src)
            staging.rename(staging.with_name(f"{staging.name}-displaced"))
            staging.mkdir()
            (staging / "README.md").write_text("substitute\n", encoding="utf-8")
            swapped = True
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(
        task_repo_publication.os,
        "replace",
        swap_staging_at_publish,
    )

    with pytest.raises(
        UnsafeRepositoryPathError,
        match="staging path changed during publication",
    ):
        recreate_task_main_repo(task)

    assert swapped is True
    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not list(transaction_dir.glob("backup-*"))
    assert not list(transaction_dir.glob("staging-*"))


@pytest.mark.parametrize("unsafe_canonical", [None, "symlink", "file"])
def test_recreate_task_repo_recovers_crash_gap_before_starting_new_git_work(
    monkeypatch,
    tmp_path,
    unsafe_canonical,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    backup = transaction_dir / "backup-crash"
    stale_staging = transaction_dir / "staging-crash"
    repo.rename(backup)
    stale_staging.mkdir()
    (stale_staging / "partial.txt").write_text("partial\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    if unsafe_canonical == "symlink":
        repo.symlink_to(outside, target_is_directory=True)
    elif unsafe_canonical == "file":
        repo.write_text("unsafe replacement\n", encoding="utf-8")
    task.repo_structure = {"files": {"README.md": "replacement\n"}}

    def stop_before_new_git_work(*_args, **_kwargs):
        raise RuntimeError("injected stop after recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_before_new_git_work,
    )

    with pytest.raises(RuntimeError, match="injected stop after recovery"):
        recreate_task_main_repo(task)

    assert repo.is_dir()
    assert not repo.is_symlink()
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert not list(transaction_dir.glob("backup-*"))
    assert not list(transaction_dir.glob("staging-*"))


def test_recovery_rejects_backup_name_swap_and_keeps_snapshot_on_second_run(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    backup = transaction_dir / "backup-crash"
    repo.rename(backup)
    task.repo_structure = {"files": {"README.md": "replacement\n"}}
    real_replace = task_repo_publication.os.replace
    swapped = False

    def swap_backup_at_restore(src, dst, *args, **kwargs):
        nonlocal swapped
        if not swapped and src == backup.name and dst == repo.name:
            backup.rename(backup.with_name("backup-preserved"))
            backup.mkdir()
            (backup / "README.md").write_text("substitute\n", encoding="utf-8")
            swapped = True
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(
        task_repo_publication.os,
        "replace",
        swap_backup_at_restore,
    )

    with pytest.raises(
        UnsafeRepositoryPathError,
        match="Recovered task repository path changed",
    ):
        recreate_task_main_repo(task)

    assert swapped is True
    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not list(transaction_dir.glob("backup-*"))

    def stop_after_second_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after second recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_second_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after second recovery"):
        recreate_task_main_repo(task)

    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not list(transaction_dir.glob("backup-*"))
    assert not list(transaction_dir.glob("staging-*"))


def test_post_commit_canonical_swap_retains_fallback_for_recovery(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    task.repo_structure = {"files": {"README.md": "replacement\n"}}
    displaced = tmp_path / "externally-displaced-canonical"
    real_matches = task_repo_publication.entry_matches_identity
    swapped = False
    published_match_count = 0

    def swap_after_final_check(parent_fd, name, identity):
        nonlocal published_match_count, swapped
        result = real_matches(parent_fd, name, identity)
        if result and name == repo.name and identity.inode != original_inode:
            published_match_count += 1
        if not swapped and published_match_count == 2:
            repo.rename(displaced)
            repo.mkdir()
            (repo / "README.md").write_text("substitute\n", encoding="utf-8")
            swapped = True
        return result

    monkeypatch.setattr(
        task_repo_publication,
        "entry_matches_identity",
        swap_after_final_check,
    )

    assert Path(recreate_task_main_repo(task)) == repo
    assert swapped is True
    assert (repo / "README.md").read_text(encoding="utf-8") == "substitute\n"
    fallback = transaction_dir / task_repo_publication.FALLBACK_NAME
    assert fallback.stat().st_ino == original_inode
    assert (fallback / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (transaction_dir / task_repo_publication.FALLBACK_OLD_NAME).exists()

    # Simulate the external actor removing the newly committed inode entirely.
    # Recovery must reject the substitute canonical and restore the retained
    # known-good fallback, rather than trusting any directory at the path.
    shutil.rmtree(displaced)

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after journal recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after journal recovery"):
        recreate_task_main_repo(task)

    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not fallback.exists()
    assert not list(transaction_dir.glob("staging-*"))


def test_publish_rejects_canonical_swap_between_state_check_and_open(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    displaced = repo.with_name(f"{repo.name}-externally-displaced")
    task.repo_structure = {"files": {"README.md": "replacement\n"}}
    real_open = task_repo_publication.os.open
    swapped = False

    def swap_before_canonical_open(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and path == repo.name and kwargs.get("dir_fd") is not None:
            repo.rename(displaced)
            repo.mkdir()
            (repo / "README.md").write_text("substitute\n", encoding="utf-8")
            swapped = True
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(task_repo_publication.os, "open", swap_before_canonical_open)

    with pytest.raises(
        UnsafeRepositoryPathError,
        match="changed before fallback rotation",
    ):
        recreate_task_main_repo(task)

    assert swapped is True
    assert displaced.stat().st_ino == original_inode
    assert (displaced / "README.md").read_text(encoding="utf-8") == "original\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "substitute\n"

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after displaced canonical recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after displaced canonical recovery"):
        recreate_task_main_repo(task)

    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not displaced.exists()


def test_legacy_recovery_prefers_exact_backup_over_substitute_canonical(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "replacement\n"}})
    repo = Path(task_main_repo_path(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    repo.mkdir()
    (repo / "README.md").write_text("substitute\n", encoding="utf-8")
    backup = repos_root / f".{repo.name}-backup-{'a' * 32}"
    backup.mkdir()
    (backup / "README.md").write_text("known-good\n", encoding="utf-8")

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after legacy recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after legacy recovery"):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "known-good\n"
    assert not backup.exists()
    assert (transaction_dir / "publication-state.json").is_file()
    assert not list(transaction_dir.glob("staging-*"))


def test_upgrade_recovery_prefers_prejournal_transaction_backup(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "replacement\n"}})
    repo = Path(task_main_repo_path(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    transaction_dir.mkdir()
    repo.mkdir()
    (repo / "README.md").write_text("substitute\n", encoding="utf-8")
    backup = transaction_dir / f"backup-{'b' * 32}"
    backup.mkdir()
    (backup / "README.md").write_text("known-good\n", encoding="utf-8")

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after upgrade recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after upgrade recovery"):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "known-good\n"
    assert not backup.exists()
    assert (transaction_dir / "publication-state.json").is_file()


def test_unjournaled_arbitrary_backup_is_not_adopted(monkeypatch, tmp_path):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "fresh\n"}})
    repo = Path(task_main_repo_path(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    transaction_dir.mkdir()
    untrusted = transaction_dir / "backup-untrusted"
    untrusted.mkdir()
    (untrusted / "README.md").write_text("substitute\n", encoding="utf-8")

    recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "fresh\n"
    assert not untrusted.exists()


def test_recovery_adopts_fallback_already_restored_to_canonical(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    task.repo_structure = {"files": {"README.md": "version-1\n"}}
    recreate_task_main_repo(task)
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    fallback = transaction_dir / task_repo_publication.FALLBACK_NAME

    # Crash image: recovery restored fallback to canonical, but died before
    # rewriting the old journal whose canonical still points at version-1.
    shutil.rmtree(repo)
    fallback.rename(repo)

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after restored fallback adoption")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after restored fallback adoption"):
        recreate_task_main_repo(task)

    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "version-0\n"
    assert not fallback.exists()
    assert (transaction_dir / "publication-state.json").is_file()


def test_recovery_never_journals_substitute_for_missing_fallback(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))
    task.repo_structure = {"files": {"README.md": "version-1\n"}}
    recreate_task_main_repo(task)
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    fallback = transaction_dir / task_repo_publication.FALLBACK_NAME

    shutil.rmtree(fallback)
    fallback.mkdir()
    (fallback / "README.md").write_text("substitute\n", encoding="utf-8")

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after missing fallback recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after missing fallback recovery"):
        recreate_task_main_repo(task)

    state = json.loads(
        (transaction_dir / "publication-state.json").read_text(encoding="utf-8")
    )
    assert state["fallback"] is None
    assert not fallback.exists()

    shutil.rmtree(repo)
    with pytest.raises(
        UnsafeRepositoryPathError,
        match="No journaled task repository snapshot remains",
    ):
        recreate_task_main_repo(task)


def test_restore_quarantines_destination_swap_without_losing_known_good(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    task.repo_structure = {"files": {"README.md": "version-1\n"}}
    recreate_task_main_repo(task)
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    fallback = transaction_dir / task_repo_publication.FALLBACK_NAME
    shutil.rmtree(repo)
    repo.mkdir()
    (repo / "README.md").write_text("substitute\n", encoding="utf-8")

    real_replace = task_repo_transaction_state.os.replace
    swapped = False

    def swap_before_destination_quarantine(src, dst, *args, **kwargs):
        nonlocal swapped
        if not swapped and src == repo.name and str(dst).startswith("quarantine-"):
            good = transaction_dir / "race-good"
            fallback.rename(good)
            repo.rename(fallback)
            good.rename(repo)
            swapped = True
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(
        task_repo_transaction_state.os,
        "replace",
        swap_before_destination_quarantine,
    )

    with pytest.raises(
        UnsafeRepositoryPathError,
        match="Recovered task repository path changed",
    ):
        recreate_task_main_repo(task)

    assert swapped is True
    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "version-0\n"


def test_cleanup_quarantines_fallback_swap_before_removal(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    task.repo_structure = {"files": {"README.md": "version-1\n"}}
    recreate_task_main_repo(task)
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    fallback = transaction_dir / task_repo_publication.FALLBACK_NAME
    fallback_old = transaction_dir / task_repo_publication.FALLBACK_OLD_NAME
    fallback_old.mkdir()
    (fallback_old / "README.md").write_text("stale\n", encoding="utf-8")

    real_replace = task_repo_transaction_state.os.replace
    swapped = False

    def swap_before_cleanup_quarantine(src, dst, *args, **kwargs):
        nonlocal swapped
        if (
            not swapped
            and src == task_repo_publication.FALLBACK_OLD_NAME
            and str(dst).startswith("quarantine-")
        ):
            good = transaction_dir / "race-good"
            fallback.rename(good)
            fallback_old.rename(fallback)
            good.rename(fallback_old)
            swapped = True
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(
        task_repo_transaction_state.os,
        "replace",
        swap_before_cleanup_quarantine,
    )

    with pytest.raises(
        UnsafeRepositoryPathError,
        match="Recovered task repository path changed",
    ):
        recreate_task_main_repo(task)

    assert swapped is True
    assert any(
        path.stat().st_ino == original_inode
        for path in transaction_dir.iterdir()
        if path.is_dir()
    )

    def stop_after_recovery(*_args, **_kwargs):
        raise RuntimeError("stop after quarantined fallback recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_after_recovery,
    )
    with pytest.raises(RuntimeError, match="stop after quarantined fallback recovery"):
        recreate_task_main_repo(task)

    assert fallback.stat().st_ino == original_inode
    assert not list(transaction_dir.glob("quarantine-*"))


def test_discard_promotes_nested_known_good_before_clearing(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    task.repo_structure = {"files": {"README.md": "version-1\n"}}
    recreate_task_main_repo(task)
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    fallback = transaction_dir / task_repo_publication.FALLBACK_NAME
    discard = transaction_dir / task_repo_transaction_state.DISCARD_NAME
    real_clear = task_repo_transaction_state.clear_directory_preserving_identities
    moved = False

    def move_fallback_into_pinned_discard(*args, **kwargs):
        nonlocal moved
        if not moved:
            fallback.rename(discard / "nested-known-good")
            moved = True
        return real_clear(*args, **kwargs)

    monkeypatch.setattr(
        task_repo_transaction_state,
        "clear_directory_preserving_identities",
        move_fallback_into_pinned_discard,
    )

    with pytest.raises(
        UnsafeRepositoryPathError,
        match="Recovered task repository path changed",
    ):
        recreate_task_main_repo(task)

    assert moved is True
    assert fallback.stat().st_ino == original_inode
    assert (fallback / "README.md").read_text(encoding="utf-8") == "version-0\n"
    assert not list(discard.iterdir())


def test_successive_publications_keep_one_bounded_fallback(monkeypatch, tmp_path):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )

    for version in range(1, 5):
        task.repo_structure = {
            "files": {"README.md": f"version-{version}\n"},
        }
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "version-4\n"
    assert (transaction_dir / task_repo_publication.FALLBACK_NAME).is_dir()
    assert not (transaction_dir / task_repo_publication.FALLBACK_OLD_NAME).exists()
    assert not list(transaction_dir.glob("fallback-new-*"))
    assert not list(transaction_dir.glob("backup-*"))
    assert not list(transaction_dir.glob("staging-*"))
    discard = transaction_dir / task_repo_transaction_state.DISCARD_NAME
    assert discard.is_dir()
    assert not list(discard.iterdir())
    assert not list(transaction_dir.glob("quarantine-*"))


def test_successive_publications_clean_paths_deeper_than_64_levels(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    deep_path = "/".join(["nested"] * 65 + ["value.txt"])
    task = _task({"files": {deep_path: "version-0\n"}})
    repo = Path(recreate_task_main_repo(task))

    for version in range(1, 3):
        task.repo_structure = {"files": {deep_path: f"version-{version}\n"}}
        recreate_task_main_repo(task)

    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    assert (repo / deep_path).read_text(encoding="utf-8") == "version-2\n"
    assert not list(transaction_dir.glob("quarantine-*"))
    discard = transaction_dir / task_repo_transaction_state.DISCARD_NAME
    assert discard.is_dir()
    assert not list(discard.iterdir())


def test_recreate_task_repo_cleans_stale_remnants_when_canonical_exists(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    stale_backup = transaction_dir / "backup-stale"
    stale_staging = transaction_dir / "staging-stale"
    stale_backup.mkdir()
    stale_staging.mkdir()
    task.repo_structure = {"files": {"README.md": "replacement\n"}}

    recreated = Path(recreate_task_main_repo(task))

    assert recreated == repo
    assert (repo / "README.md").read_text(encoding="utf-8") == "replacement\n"
    assert not stale_backup.exists()
    assert not stale_staging.exists()


def test_recreate_task_repo_recovers_exact_legacy_crash_gap(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    legacy_backup = repos_root / f".{repo.name}-backup-{'a' * 32}"
    legacy_staging = repos_root / f".{repo.name}-staging-{'b' * 32}"
    repo.rename(legacy_backup)
    legacy_staging.mkdir()
    (legacy_staging / "partial.txt").write_text("partial\n", encoding="utf-8")
    task.repo_structure = {"files": {"README.md": "replacement\n"}}

    def stop_before_new_git_work(*_args, **_kwargs):
        raise RuntimeError("injected stop after legacy recovery")

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        stop_before_new_git_work,
    )

    with pytest.raises(RuntimeError, match="injected stop after legacy recovery"):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not legacy_backup.exists()
    assert not legacy_staging.exists()


def test_failed_lock_waiter_never_cleans_live_publisher_staging(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo.name
    )
    live_staging = transaction_dir / "staging-live-publisher"
    live_staging.mkdir()
    marker = live_staging / "keep.txt"
    marker.write_text("live\n", encoding="utf-8")
    task.repo_structure = {"files": {"README.md": "replacement\n"}}

    def fail_lock(_transaction_fd):
        raise RuntimeError("injected lock interruption")

    monkeypatch.setattr(
        task_repo_service,
        "_acquire_publication_lock",
        fail_lock,
    )

    with pytest.raises(RuntimeError, match="injected lock interruption"):
        recreate_task_main_repo(task)

    assert marker.read_text(encoding="utf-8") == "live\n"


def test_long_valid_repo_name_uses_fixed_size_transaction_namespace(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "long name\n"}})
    task.task_key = "k" * 120
    task.name = "n" * 120

    repo = Path(recreate_task_main_repo(task))
    transaction_dir = repo.parent / task_repo_publication._transaction_dir_name(
        repo.name
    )

    assert 235 < len(repo.name) <= 255
    assert len(transaction_dir.name) < 100
    assert (repo / "README.md").read_text(encoding="utf-8") == "long name\n"


def test_task_git_commands_cannot_be_redirected_by_staging_path_swap(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    outside = tmp_path / "outside"
    outside_head = _initialize_outside_git_repo(outside)
    (outside / "outside-untracked.txt").write_text("do not stage\n", encoding="utf-8")
    task = _task({"files": {"README.md": "safe\n"}})
    repo_name = Path(task_main_repo_path(task)).name
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo_name
    )
    real_run = task_repo_service.run_in_pinned_directory
    swapped = False

    def swap_before_git_add(args, directory_fd, **kwargs):
        nonlocal swapped
        if not swapped and list(args) == ["git", "add", "."]:
            staging = next(transaction_dir.glob("staging-*"))
            staging.rename(staging.with_name(f"{staging.name}-displaced"))
            staging.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_run(args, directory_fd, **kwargs)

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        swap_before_git_add,
    )

    with pytest.raises(UnsafeRepositoryPathError, match="staging path changed"):
        recreate_task_main_repo(task)

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
    assert not list(transaction_dir.glob("staging-*"))


def test_pinned_git_ignores_host_git_environment_config_and_path(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    outside = tmp_path / "outside"
    outside_head = _initialize_outside_git_repo(outside)
    outside_untracked = outside / "outside-untracked.txt"
    outside_untracked.write_text("do not stage\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git_marker = tmp_path / "fake-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\n/usr/bin/touch {fake_git_marker}\nexit 97\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    hooks = tmp_path / "host-hooks"
    hooks.mkdir()
    hook_marker = tmp_path / "host-hook-ran"
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text(
        f"#!/bin/sh\n/usr/bin/touch {hook_marker}\n",
        encoding="utf-8",
    )
    pre_commit.chmod(0o755)
    global_config = tmp_path / "host-gitconfig"
    global_config.write_text(
        f"[core]\n\thooksPath = {hooks}\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("GIT_DIR", str(outside / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outside))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(global_config))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(hooks))

    repo = Path(recreate_task_main_repo(_task({"files": {"README.md": "safe\n"}})))

    outside_after = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    ).stdout.strip()
    outside_status = subprocess.run(
        ["/usr/bin/git", "status", "--porcelain"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    ).stdout.strip()
    assert outside_after == outside_head
    assert outside_status == "?? outside-untracked.txt"
    assert (repo / ".git").is_dir()
    assert not fake_git_marker.exists()
    assert not hook_marker.exists()


def test_task_publications_for_same_repo_are_serialized(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    first_task = _task({"files": {"README.md": "first\n"}})
    second_task = _task({"files": {"README.md": "second\n"}})
    first_entered_git = threading.Event()
    release_first = threading.Event()
    second_entered_git = threading.Event()
    real_run = task_repo_service.run_in_pinned_directory
    errors: list[BaseException] = []

    def gate_first_publisher(args, directory_fd, **kwargs):
        if list(args[:2]) == ["git", "init"]:
            if threading.current_thread().name == "publisher-one":
                first_entered_git.set()
                if not release_first.wait(timeout=5):
                    raise RuntimeError("publisher barrier timed out")
            elif threading.current_thread().name == "publisher-two":
                second_entered_git.set()
        return real_run(args, directory_fd, **kwargs)

    def publish(task):
        try:
            recreate_task_main_repo(task)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    monkeypatch.setattr(
        task_repo_service,
        "run_in_pinned_directory",
        gate_first_publisher,
    )
    first = threading.Thread(
        target=publish,
        args=(first_task,),
        name="publisher-one",
    )
    second = threading.Thread(
        target=publish,
        args=(second_task,),
        name="publisher-two",
    )

    first.start()
    assert first_entered_git.wait(timeout=5)
    second.start()
    assert not second_entered_git.wait(timeout=0.25)
    repo_name = Path(task_main_repo_path(first_task)).name
    transaction_dir = repos_root / task_repo_publication._transaction_dir_name(
        repo_name
    )
    assert len(list(transaction_dir.glob("staging-*"))) == 1
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert second_entered_git.is_set()
    repo = Path(task_main_repo_path(second_task))
    assert (repo / "README.md").read_text(encoding="utf-8") == "second\n"
    assert not list(transaction_dir.glob("staging-*"))
    assert not list(transaction_dir.glob("backup-*"))


def test_recovery_does_not_touch_another_repository_transaction_namespace(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "first\n"}})
    repo = Path(recreate_task_main_repo(task))
    colliding_namespace = repos_root / (
        f".{repo.name}-staging-other.taali-transactions"
    )
    colliding_namespace.mkdir()
    marker = colliding_namespace / "staging-live"
    marker.mkdir()
    (marker / "keep.txt").write_text("live\n", encoding="utf-8")
    task.repo_structure = {"files": {"README.md": "second\n"}}

    recreate_task_main_repo(task)

    assert (marker / "keep.txt").read_text(encoding="utf-8") == "live\n"


def test_recreate_task_repo_rejects_symlinked_task_repositories_root(
    monkeypatch,
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    root = tmp_path / "repos-link"
    root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("TASK_REPOS_ROOT", str(root))

    with pytest.raises(UnsafeRepositoryPathError, match="root is not a safe"):
        recreate_task_main_repo(_task({"files": {"README.md": "unsafe\n"}}))

    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert list(outside.iterdir()) == [sentinel]


def test_repo_writer_cleanup_failure_preserves_error_and_closes_directories(
    monkeypatch,
    tmp_path,
):
    repo = tmp_path / "repo"
    (repo / "nested").mkdir(parents=True)
    real_close = os.close
    closed_descriptors = []

    def fail_replace(*_args, **_kwargs):
        raise OSError("primary replace failure")

    def fail_cleanup_unlink(*_args, **_kwargs):
        raise OSError("cleanup unlink failure")

    def track_close(descriptor):
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(path_safety.os, "replace", fail_replace)
    monkeypatch.setattr(path_safety.os, "unlink", fail_cleanup_unlink)
    monkeypatch.setattr(path_safety.os, "close", track_close)

    with pytest.raises(UnsafeRepositoryPathError) as error:
        path_safety.write_repo_file(repo, "nested/result.txt", "content\n")

    assert str(error.value.__cause__) == "primary replace failure"
    assert len(closed_descriptors) == 2
    for descriptor in closed_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
