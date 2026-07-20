"""Crash-safe transaction state for canonical task repository publication."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
from pathlib import Path

from .repository_path_safety import (
    UnsafeRepositoryPathError,
    directory_open_flags,
    entry_exists_at,
    remove_entry_at,
    same_open_directory,
)


def _transaction_remnants(directory_fd: int, prefix: str) -> list[str]:
    return sorted(
        name for name in os.listdir(directory_fd) if name.startswith(prefix)
    )


def _legacy_transaction_remnants(
    root_fd: int,
    repo_name: str,
    kind: str,
) -> list[tuple[str, str]]:
    """Return only the exact tokenized names emitted by the prior publisher."""

    prefix = f".{repo_name}-{kind}-"
    remnants: list[tuple[str, str]] = []
    for name in os.listdir(root_fd):
        if not name.startswith(prefix):
            continue
        token = name[len(prefix) :]
        if len(token) == 32 and all(
            character in "0123456789abcdef" for character in token
        ):
            remnants.append((name, token))
    return sorted(remnants)


def _transaction_dir_name(repo_name: str) -> str:
    digest = hashlib.sha256(repo_name.encode("utf-8")).hexdigest()
    return f".taali-transactions-{digest}"


def _migrate_legacy_transaction_remnants(
    root_fd: int,
    transaction_fd: int,
    repo_name: str,
) -> None:
    """Move exact pre-namespace remnants under the locked transaction root."""

    for kind in ("staging", "backup"):
        for legacy_name, token in _legacy_transaction_remnants(
            root_fd,
            repo_name,
            kind,
        ):
            os.replace(
                legacy_name,
                f"{kind}-legacy-{token}",
                src_dir_fd=root_fd,
                dst_dir_fd=transaction_fd,
            )


def _open_transaction_directory(
    root_fd: int,
    repo_root: Path,
    repo_name: str,
) -> tuple[Path, int]:
    transaction_name = _transaction_dir_name(repo_name)
    try:
        os.mkdir(transaction_name, mode=0o700, dir_fd=root_fd)
    except FileExistsError:
        pass
    try:
        transaction_fd = os.open(
            transaction_name,
            directory_open_flags(),
            dir_fd=root_fd,
        )
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            "Task repository transaction namespace is unsafe"
        ) from exc
    transaction_dir = repo_root / transaction_name
    if not same_open_directory(transaction_dir, transaction_fd):
        os.close(transaction_fd)
        raise UnsafeRepositoryPathError(
            "Task repository transaction namespace changed"
        )
    return transaction_dir, transaction_fd


def _acquire_publication_lock(transaction_fd: int) -> int:
    lock_flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        lock_fd = os.open(
            "publication.lock",
            lock_flags,
            0o600,
            dir_fd=transaction_fd,
        )
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            "Task repository publication lock is unsafe"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise UnsafeRepositoryPathError(
                "Task repository publication lock is not a regular file"
            )
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return lock_fd
    except BaseException:
        os.close(lock_fd)
        raise


def _recover_interrupted_publication(
    root_fd: int,
    transaction_fd: int,
    repo_root: Path,
    repo_name: str,
) -> None:
    """Restore a known prior snapshot and clear abandoned transaction state."""

    for staging_name in _transaction_remnants(transaction_fd, "staging-"):
        remove_entry_at(transaction_fd, staging_name)

    backup_names = _transaction_remnants(transaction_fd, "backup-")
    canonical_exists = entry_exists_at(root_fd, repo_name)
    canonical_is_safe = False
    if canonical_exists:
        try:
            canonical_fd = os.open(
                repo_name,
                directory_open_flags(),
                dir_fd=root_fd,
            )
        except OSError:
            canonical_fd = None
        else:
            canonical_is_safe = True
            os.close(canonical_fd)

    if canonical_is_safe:
        for backup_name in backup_names:
            remove_entry_at(transaction_fd, backup_name)
        return
    if not backup_names:
        if canonical_exists:
            remove_entry_at(root_fd, repo_name)
        return
    if len(backup_names) != 1:
        raise UnsafeRepositoryPathError(
            f"Ambiguous task repository backups for {repo_name!r}"
        )

    backup_name = backup_names[0]
    try:
        backup_fd = os.open(
            backup_name,
            directory_open_flags(),
            dir_fd=transaction_fd,
        )
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            f"Task repository backup is not a safe directory: {backup_name!r}"
        ) from exc
    try:
        if canonical_exists:
            remove_entry_at(root_fd, repo_name)
        os.replace(
            backup_name,
            repo_name,
            src_dir_fd=transaction_fd,
            dst_dir_fd=root_fd,
        )
        if not same_open_directory(repo_root / repo_name, backup_fd):
            raise UnsafeRepositoryPathError(
                "Recovered task repository path changed during publication"
            )
    finally:
        os.close(backup_fd)
