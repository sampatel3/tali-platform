#!/usr/bin/env python3
"""Reject PR heads that can silently replace work from the base branch.

The check is intentionally read-only: ``git merge-tree`` computes a merge in
Git's object database without checking files out or changing the index.  A PR
fails when its head does not contain the current base SHA, when the simulated
merge has textual conflicts, or when both sides changed the same path.  The
last condition treats a clean same-file merge as high risk because Git cannot
detect semantic overwrites (CSS cascade, route ordering, async control flow,
and similar behavior).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class GitError(RuntimeError):
    """Raised when repository state cannot be inspected safely."""


@dataclass(frozen=True)
class MergeSafetyResult:
    base_sha: str
    head_sha: str
    merge_base_sha: str
    stale: bool
    conflicts: bool
    overlapping_paths: tuple[str, ...]
    merge_tree_output: str

    @property
    def ok(self) -> bool:
        return not (self.stale or self.conflicts or self.overlapping_paths)


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and process.returncode != 0:
        command = "git " + " ".join(args)
        raise GitError(f"{command} failed ({process.returncode}):\n{process.stdout.strip()}")
    return process


def _resolve_commit(repo: Path, revision: str) -> str:
    process = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    return process.stdout.strip()


def _changed_paths(repo: Path, start: str, end: str) -> set[str]:
    process = _git(
        repo,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        "-z",
        start,
        end,
    )
    return {path for path in process.stdout.split("\0") if path}


def inspect_merge(repo: Path, base: str, head: str) -> MergeSafetyResult:
    """Inspect ``base`` and ``head`` without modifying the worktree or index."""

    repo = repo.resolve()
    base_sha = _resolve_commit(repo, base)
    head_sha = _resolve_commit(repo, head)
    merge_base_sha = _git(repo, "merge-base", base_sha, head_sha).stdout.strip()

    ancestor = _git(repo, "merge-base", "--is-ancestor", base_sha, head_sha, check=False)
    if ancestor.returncode not in (0, 1):
        raise GitError(
            "git merge-base --is-ancestor failed "
            f"({ancestor.returncode}):\n{ancestor.stdout.strip()}"
        )
    stale = ancestor.returncode == 1

    base_paths = _changed_paths(repo, merge_base_sha, base_sha)
    head_paths = _changed_paths(repo, merge_base_sha, head_sha)
    overlapping_paths = tuple(sorted(base_paths & head_paths))

    # --write-tree performs Git's real recursive merge without touching the
    # worktree or index. Exit 1 means conflicts; values above 1 are fatal.
    merge_tree = _git(
        repo,
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--messages",
        base_sha,
        head_sha,
        check=False,
    )
    if merge_tree.returncode not in (0, 1):
        raise GitError(
            f"git merge-tree failed ({merge_tree.returncode}):\n"
            f"{merge_tree.stdout.strip()}"
        )

    return MergeSafetyResult(
        base_sha=base_sha,
        head_sha=head_sha,
        merge_base_sha=merge_base_sha,
        stale=stale,
        conflicts=merge_tree.returncode == 1,
        overlapping_paths=overlapping_paths,
        merge_tree_output=merge_tree.stdout.strip(),
    )


def format_report(result: MergeSafetyResult, *, base_label: str = "base branch") -> str:
    lines = [
        "Merge safety check",
        f"  current base: {result.base_sha}",
        f"  PR head:      {result.head_sha}",
        f"  merge base:   {result.merge_base_sha}",
    ]

    if result.stale:
        lines.extend(
            [
                "",
                f"STALE BRANCH: PR head does not contain the current {base_label} SHA.",
                f"Update the branch with {base_label}, resolve it intentionally, and push again.",
            ]
        )
    else:
        lines.extend(["", f"UP TO DATE: PR head contains the current {base_label} SHA."])

    if result.overlapping_paths:
        lines.extend(
            [
                "",
                "HIGH-RISK OVERLAP: both sides changed the same path(s).",
                "A clean textual merge can still overwrite behavior; "
                "review each path after updating:",
            ]
        )
        lines.extend(f"  - {path}" for path in result.overlapping_paths)
    else:
        lines.extend(["", "OVERLAP: no paths were changed independently on both sides."])

    if result.conflicts:
        lines.extend(
            [
                "",
                "MERGE-TREE CONFLICT: Git's read-only merge simulation found conflicts:",
                result.merge_tree_output or "  (Git returned no conflict details)",
            ]
        )
    else:
        lines.extend(["", "MERGE-TREE: read-only simulation completed without textual conflicts."])

    reasons: list[str] = []
    if result.stale:
        reasons.append("stale branch")
    if result.conflicts:
        reasons.append("merge conflict")
    if result.overlapping_paths:
        reasons.append("high-risk same-file overlap")

    if reasons:
        lines.extend(["", f"RESULT: FAIL ({', '.join(reasons)})"])
    else:
        lines.extend(["", "RESULT: PASS"])
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Current base-branch commit SHA")
    parser.add_argument("--head", required=True, help="Pull-request head commit SHA")
    parser.add_argument(
        "--base-label",
        default="base branch",
        help="Human-readable base name used in failure guidance",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Git repository to inspect (default: current directory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = inspect_merge(args.repo, args.base, args.head)
    except GitError as exc:
        print(f"Merge safety check could not run:\n{exc}", file=sys.stderr)
        return 2

    print(format_report(result, base_label=args.base_label))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
