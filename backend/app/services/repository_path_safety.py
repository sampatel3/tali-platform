"""Shared lexical and filesystem safety for task repository writes."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


_REPOSITORY_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class UnsafeRepositoryPathError(ValueError):
    """A task repository path cannot be used safely."""


def is_safe_repository_segment(value: object) -> bool:
    """Return whether ``value`` is one inert filesystem/API path segment."""

    return bool(
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and value.casefold().rstrip(" .") != ".git"
        and _REPOSITORY_SEGMENT.fullmatch(value)
    )


def canonical_repo_file_path(value: Any) -> str:
    """Return one canonical repository-relative path or raise.

    Backslashes remain supported as a compatibility path separator. Absolute
    paths, parent/current-directory aliases, control characters, and Git's
    private metadata directory are never task content.
    """

    if not isinstance(value, str) or not value.strip():
        raise UnsafeRepositoryPathError(
            "Repository file path must be a non-empty string"
        )

    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    unsafe = (
        normalized.startswith("/")
        or bool(PureWindowsPath(normalized).drive)
        or any(ord(character) < 32 for character in normalized)
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold().rstrip(" .") == ".git" for part in parts)
    )
    candidate = PurePosixPath(normalized)
    if unsafe or candidate.is_absolute():
        raise UnsafeRepositoryPathError(f"Unsafe repository file path: {value!r}")
    return candidate.as_posix()


def validate_manifest_file_hierarchy(
    canonical_source_paths: Mapping[str, str],
) -> None:
    """Reject manifests where one file path is another file's parent.

    Every key must already have passed :func:`canonical_repo_file_path`.
    Validating the complete set before a writer opens its root makes the result
    independent of dictionary order and prevents a partial materialization such
    as ``{"src": "file", "src/main.py": "child"}``.
    """

    canonical_paths = set(canonical_source_paths)
    for canonical in canonical_paths:
        parts = PurePosixPath(canonical).parts
        for depth in range(1, len(parts)):
            parent = PurePosixPath(*parts[:depth]).as_posix()
            if parent in canonical_paths:
                raise UnsafeRepositoryPathError(
                    "Repository manifest file/parent conflict: "
                    f"{canonical_source_paths[parent]!r} is a file and cannot "
                    f"contain {canonical_source_paths[canonical]!r}"
                )


def directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def entry_exists_at(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def remove_entry_at(parent_fd: int, name: str) -> None:
    item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISDIR(item_stat.st_mode):
        shutil.rmtree(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


def clear_directory(
    directory_fd: int,
    *,
    preserve_directories: frozenset[str] = frozenset(),
) -> None:
    """Remove entries without following links; validate preserved directories."""

    for name in os.listdir(directory_fd):
        item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if name in preserve_directories:
            if not stat.S_ISDIR(item_stat.st_mode):
                raise UnsafeRepositoryPathError(
                    f"Preserved repository path is not a directory: {name!r}"
                )
            continue
        remove_entry_at(directory_fd, name)


def same_open_directory(path: Path, descriptor: int) -> bool:
    try:
        current_fd = os.open(path, directory_open_flags())
    except OSError:
        return False
    try:
        expected = os.fstat(descriptor)
        current = os.fstat(current_fd)
        return (expected.st_dev, expected.st_ino) == (current.st_dev, current.st_ino)
    finally:
        os.close(current_fd)


_PINNED_DIRECTORY_EXEC = (
    "import os,sys; "
    "descriptor=int(sys.argv[1]); command=sys.argv[2:]; "
    "os.fchdir(descriptor); os.execvp(command[0], command)"
)


def run_in_pinned_directory(
    args: Sequence[str],
    directory_fd: int,
    **run_kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the directory identified by an already-open fd.

    ``subprocess`` only accepts a pathname for ``cwd``. Rechecking that pathname
    before a call still leaves a race in which it can be replaced by a symlink
    before the child changes directory. A small isolated interpreter inherits
    only a duplicate of the pinned descriptor, changes directory with
    :func:`os.fchdir`, and then replaces itself with the requested command.
    Consequently the child never resolves the mutable repository pathname.
    """

    if not args:
        raise ValueError("Pinned-directory command must not be empty")
    if "cwd" in run_kwargs or "pass_fds" in run_kwargs:
        raise ValueError("Pinned-directory commands manage cwd and pass_fds")
    pinned_fd = os.dup(directory_fd)
    try:
        if not stat.S_ISDIR(os.fstat(pinned_fd).st_mode):
            raise UnsafeRepositoryPathError(
                "Pinned repository descriptor is not a directory"
            )
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _PINNED_DIRECTORY_EXEC,
                str(pinned_fd),
                *args,
            ],
            pass_fds=(pinned_fd,),
            **run_kwargs,
        )
    finally:
        os.close(pinned_fd)


@contextmanager
def pinned_subdirectory(
    root: Path,
    segments: Sequence[str],
) -> Iterator[tuple[Path, int]]:
    """Create/open safe child segments and keep their directory identity pinned."""

    if not all(is_safe_repository_segment(segment) for segment in segments):
        raise UnsafeRepositoryPathError("Repository path segment is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root_fd = os.open(root, directory_open_flags())
    except OSError as exc:
        raise UnsafeRepositoryPathError("Repository root is not safe") from exc
    opened_fds = [root_fd]
    current_fd = root_fd
    try:
        for segment in segments:
            try:
                os.mkdir(segment, mode=0o700, dir_fd=current_fd)
            except FileExistsError:
                item_stat = os.stat(
                    segment,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(item_stat.st_mode):
                    os.unlink(segment, dir_fd=current_fd)
                    os.mkdir(segment, mode=0o700, dir_fd=current_fd)
                elif not stat.S_ISDIR(item_stat.st_mode):
                    raise UnsafeRepositoryPathError(
                        f"Repository path is not a directory: {segment!r}"
                    )
            current_fd = os.open(
                segment,
                directory_open_flags(),
                dir_fd=current_fd,
            )
            opened_fds.append(current_fd)
        path = root.joinpath(*segments)
        if not same_open_directory(path, current_fd):
            raise UnsafeRepositoryPathError("Repository path changed")
        yield path, current_fd
    except OSError as exc:
        raise UnsafeRepositoryPathError("Repository path is not safe") from exc
    finally:
        for descriptor in reversed(opened_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass


def write_repo_file(
    repo_dir: Path,
    rel_path: str,
    content: str,
    *,
    repo_fd: int | None = None,
) -> None:
    """Atomically write below ``repo_dir`` without following symbolic links."""

    canonical = canonical_repo_file_path(rel_path)
    parts = PurePosixPath(canonical).parts
    opened_fds: list[int] = []
    parent_fd: int | None = None
    temporary_name: str | None = None
    try:
        root_fd = (
            os.open(repo_dir, directory_open_flags())
            if repo_fd is None
            else os.dup(repo_fd)
        )
        opened_fds.append(root_fd)
        parent_fd = root_fd
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise UnsafeRepositoryPathError("Repository root is not a directory")

        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o755, dir_fd=parent_fd)
            except FileExistsError:
                pass
            child_fd = os.open(part, directory_open_flags(), dir_fd=parent_fd)
            opened_fds.append(child_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                raise UnsafeRepositoryPathError(
                    f"Repository path parent is not a directory: {canonical!r}"
                )
            parent_fd = child_fd

        temporary_name = f".taali-write-{secrets.token_hex(16)}"
        write_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        temporary_fd = os.open(
            temporary_name,
            write_flags,
            0o666,
            dir_fd=parent_fd,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(
            temporary_name,
            parts[-1],
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = None
    except UnsafeRepositoryPathError:
        raise
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            f"Unsafe repository filesystem target: {canonical!r}"
        ) from exc
    finally:
        try:
            if temporary_name is not None and parent_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except OSError:
                    pass
        finally:
            for descriptor in reversed(opened_fds):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
