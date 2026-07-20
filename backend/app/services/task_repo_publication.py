"""Crash-safe transaction state for canonical task repository publication."""

from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
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


def _entry_matches_open_directory(
    parent_fd: int,
    name: str,
    descriptor: int,
) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        expected = os.fstat(descriptor)
    except OSError:
        return False
    return (
        stat.S_ISDIR(current.st_mode)
        and (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)
    )


def _find_open_directory_name(parent_fd: int, descriptor: int) -> str | None:
    """Find the current name for a pinned directory without following links."""

    for name in os.listdir(parent_fd):
        if _entry_matches_open_directory(parent_fd, name, descriptor):
            return name
    return None


def _remove_if_present(parent_fd: int, name: str) -> None:
    if entry_exists_at(parent_fd, name):
        remove_entry_at(parent_fd, name)


def _restore_open_directory(
    source_parent_fd: int,
    destination_parent_fd: int,
    destination_path: Path,
    destination_name: str,
    descriptor: int,
) -> None:
    """Restore the directory pinned by ``descriptor`` after a name swap.

    A rename still consumes a mutable directory entry.  Verify the destination
    inode and, if a concurrent swap substituted another entry, remove only that
    substitute and locate the still-open source inode for a bounded retry.
    """

    if same_open_directory(destination_path, descriptor):
        return
    for _attempt in range(3):
        _remove_if_present(destination_parent_fd, destination_name)
        source_name = _find_open_directory_name(source_parent_fd, descriptor)
        if source_name is None:
            raise UnsafeRepositoryPathError(
                "Pinned task repository snapshot is no longer recoverable"
            )
        os.replace(
            source_name,
            destination_name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=destination_parent_fd,
        )
        if same_open_directory(destination_path, descriptor):
            return
    _remove_if_present(destination_parent_fd, destination_name)
    raise UnsafeRepositoryPathError(
        "Pinned task repository snapshot changed repeatedly during recovery"
    )


def _publish_pinned_staging(
    root_fd: int,
    transaction_fd: int,
    repo_root: Path,
    repo_name: str,
    staging_name: str,
    staging_fd: int,
) -> None:
    """Publish exactly the open staging inode or restore the prior snapshot."""

    repo_dir = repo_root / repo_name
    backup_name: str | None = None
    backup_fd: int | None = None
    if entry_exists_at(root_fd, repo_name):
        try:
            backup_fd = os.open(
                repo_name,
                directory_open_flags(),
                dir_fd=root_fd,
            )
        except OSError as exc:
            raise UnsafeRepositoryPathError(
                "Existing task repository changed before publication"
            ) from exc
        backup_name = f"backup-{secrets.token_hex(16)}"
        try:
            os.replace(
                repo_name,
                backup_name,
                src_dir_fd=root_fd,
                dst_dir_fd=transaction_fd,
            )
            if not _entry_matches_open_directory(
                transaction_fd,
                backup_name,
                backup_fd,
            ):
                _remove_if_present(transaction_fd, backup_name)
                _restore_open_directory(
                    root_fd,
                    root_fd,
                    repo_dir,
                    repo_name,
                    backup_fd,
                )
                raise UnsafeRepositoryPathError(
                    "Existing task repository changed during backup"
                )
        except BaseException:
            os.close(backup_fd)
            raise

    try:
        os.replace(
            staging_name,
            repo_name,
            src_dir_fd=transaction_fd,
            dst_dir_fd=root_fd,
        )
        if not same_open_directory(repo_dir, staging_fd):
            raise UnsafeRepositoryPathError(
                "Task repository staging path changed during publication"
            )

        # This check is deliberately adjacent to backup deletion.  The prior
        # snapshot remains available until the canonical name is proven to be
        # the exact staging inode that passed validation and Git initialization.
        if backup_fd is not None and not same_open_directory(repo_dir, staging_fd):
            raise UnsafeRepositoryPathError(
                "Task repository canonical path changed before backup cleanup"
            )
    except BaseException:
        if backup_fd is None:
            _remove_if_present(root_fd, repo_name)
        else:
            try:
                _restore_open_directory(
                    transaction_fd,
                    root_fd,
                    repo_dir,
                    repo_name,
                    backup_fd,
                )
            except BaseException as restore_exc:
                raise UnsafeRepositoryPathError(
                    "Task repository publication failed and the prior snapshot "
                    "could not be restored"
                ) from restore_exc
        raise
    else:
        if backup_fd is not None:
            current_backup_name = _find_open_directory_name(
                transaction_fd,
                backup_fd,
            )
            if current_backup_name is not None:
                try:
                    remove_entry_at(transaction_fd, current_backup_name)
                except OSError:
                    # The canonical staging inode is already verified.  Leave
                    # an undeletable prior snapshot for locked crash recovery
                    # instead of reporting a false publication failure.
                    pass
    finally:
        if backup_fd is not None:
            os.close(backup_fd)


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
            _restore_open_directory(
                transaction_fd,
                root_fd,
                repo_root / repo_name,
                repo_name,
                backup_fd,
            )
            raise UnsafeRepositoryPathError(
                "Recovered task repository path changed during publication"
            )
    finally:
        os.close(backup_fd)
