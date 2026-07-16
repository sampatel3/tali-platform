from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from check_merge_safety import format_report, inspect_merge  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _write(repo: Path, relative_path: str, contents: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Merge Safety Test")
    _git(tmp_path, "config", "user.email", "merge-safety@example.test")
    _write(tmp_path, "shared.txt", "first\nsecond\nthird\n")
    _commit(tmp_path, "initial")
    return tmp_path


def test_passes_when_head_contains_current_base(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "feature")
    _write(repo, "feature.txt", "feature\n")
    head = _commit(repo, "feature")
    checked_out_before = _git(repo, "rev-parse", "HEAD")

    result = inspect_merge(repo, base, head)

    assert result.ok
    assert not result.stale
    assert not result.conflicts
    assert result.overlapping_paths == ()
    assert "RESULT: PASS" in format_report(result, base_label="main")
    assert _git(repo, "rev-parse", "HEAD") == checked_out_before
    assert _git(repo, "status", "--porcelain") == ""


def test_fails_a_stale_branch_even_without_overlap(repo: Path) -> None:
    fork_point = _git(repo, "rev-parse", "HEAD")
    _write(repo, "base-only.txt", "base\n")
    base = _commit(repo, "base advances")
    _git(repo, "switch", "-q", "-c", "feature", fork_point)
    _write(repo, "head-only.txt", "head\n")
    head = _commit(repo, "feature advances")

    result = inspect_merge(repo, base, head)

    assert not result.ok
    assert result.stale
    assert not result.conflicts
    assert result.overlapping_paths == ()
    report = format_report(result, base_label="main")
    assert "STALE BRANCH" in report
    assert "RESULT: FAIL (stale branch)" in report


def test_reports_textual_conflicts_and_same_file_overlap(repo: Path) -> None:
    fork_point = _git(repo, "rev-parse", "HEAD")
    _write(repo, "shared.txt", "base changed\nsecond\nthird\n")
    base = _commit(repo, "base changes shared line")
    _git(repo, "switch", "-q", "-c", "feature", fork_point)
    _write(repo, "shared.txt", "head changed\nsecond\nthird\n")
    head = _commit(repo, "head changes shared line")

    result = inspect_merge(repo, base, head)

    assert result.stale
    assert result.conflicts
    assert result.overlapping_paths == ("shared.txt",)
    report = format_report(result)
    assert "HIGH-RISK OVERLAP" in report
    assert "MERGE-TREE CONFLICT" in report
    assert "shared.txt" in report


def test_fails_clean_semantic_same_file_overlap(repo: Path) -> None:
    fork_point = _git(repo, "rev-parse", "HEAD")
    _write(repo, "shared.txt", "base changed\nsecond\nthird\n")
    base = _commit(repo, "base changes first line")
    _git(repo, "switch", "-q", "-c", "feature", fork_point)
    _write(repo, "shared.txt", "first\nsecond\nhead changed\n")
    head = _commit(repo, "head changes third line")

    result = inspect_merge(repo, base, head)

    assert result.stale
    assert not result.conflicts
    assert result.overlapping_paths == ("shared.txt",)
    assert not result.ok
    report = format_report(result)
    assert "MERGE-TREE: read-only simulation completed without textual conflicts." in report
    assert "high-risk same-file overlap" in report


def test_updating_with_current_base_clears_stale_overlap_gate(repo: Path) -> None:
    fork_point = _git(repo, "rev-parse", "HEAD")
    _write(repo, "shared.txt", "base changed\nsecond\nthird\n")
    base = _commit(repo, "base changes first line")
    _git(repo, "switch", "-q", "-c", "feature", fork_point)
    _write(repo, "shared.txt", "first\nsecond\nhead changed\n")
    _commit(repo, "head changes third line")

    _git(repo, "merge", "-q", "--no-edit", base)
    updated_head = _git(repo, "rev-parse", "HEAD")
    result = inspect_merge(repo, base, updated_head)

    assert result.ok
    assert not result.stale
    assert not result.conflicts
    assert result.overlapping_paths == ()
