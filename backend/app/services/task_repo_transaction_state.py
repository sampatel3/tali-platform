"""Durable identity journal for canonical task repository publication."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from .repository_path_safety import (
    UnsafeRepositoryPathError,
    directory_open_flags,
    same_open_directory,
)
from .task_repo_discard import clear_directory_preserving_identities


STATE_NAME = "publication-state.json"
STATE_TEMP_PREFIX = ".publication-state-"
FALLBACK_NAME = "fallback"
FALLBACK_OLD_NAME = "fallback-old"
QUARANTINE_PREFIX = "quarantine-"
DISCARD_NAME = "discard"
STATE_VERSION = 1
MAX_STATE_BYTES = 4096


@dataclass(frozen=True)
class SnapshotIdentity:
    device: int
    inode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> SnapshotIdentity:
        return cls(device=int(value.st_dev), inode=int(value.st_ino))

    @classmethod
    def from_payload(cls, value: object) -> SnapshotIdentity:
        if not isinstance(value, dict):
            raise ValueError("snapshot identity must be an object")
        device = value.get("device")
        inode = value.get("inode")
        if not isinstance(device, int) or not isinstance(inode, int):
            raise ValueError("snapshot identity fields must be integers")
        if device < 0 or inode <= 0:
            raise ValueError("snapshot identity fields are invalid")
        return cls(device=device, inode=inode)

    def payload(self) -> dict[str, int]:
        return {"device": self.device, "inode": self.inode}


@dataclass(frozen=True)
class PublicationState:
    canonical: SnapshotIdentity
    fallback: SnapshotIdentity | None = None


def descriptor_identity(descriptor: int) -> SnapshotIdentity:
    value = os.fstat(descriptor)
    if not stat.S_ISDIR(value.st_mode):
        raise UnsafeRepositoryPathError("Task repository snapshot is not a directory")
    return SnapshotIdentity.from_stat(value)


def entry_identity(parent_fd: int, name: str) -> SnapshotIdentity | None:
    try:
        value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISDIR(value.st_mode):
        return None
    return SnapshotIdentity.from_stat(value)


def entry_matches_identity(
    parent_fd: int,
    name: str,
    identity: SnapshotIdentity,
) -> bool:
    return entry_identity(parent_fd, name) == identity


def find_identity_name(
    parent_fd: int,
    identity: SnapshotIdentity,
    *,
    excluded_names: frozenset[str] = frozenset(),
) -> str | None:
    for name in os.listdir(parent_fd):
        if name in excluded_names:
            continue
        if entry_matches_identity(parent_fd, name, identity):
            return name
    return None


def snapshot_remnant_names(transaction_fd: int) -> list[str]:
    """Return names owned by current and legacy publication transactions."""

    return sorted(
        name
        for name in os.listdir(transaction_fd)
        if name in {FALLBACK_NAME, FALLBACK_OLD_NAME}
        or name.startswith(("fallback-new-", "backup-", QUARANTINE_PREFIX))
    )


def legacy_backup_identity_candidates(transaction_fd: int) -> list[SnapshotIdentity]:
    """Return only snapshots emitted by the exact pre-journal backup protocol."""

    identities: list[SnapshotIdentity] = []
    for name in os.listdir(transaction_fd):
        token = ""
        for prefix in ("backup-legacy-", "backup-"):
            candidate = name[len(prefix) :] if name.startswith(prefix) else ""
            if len(candidate) == 32 and all(
                character in "0123456789abcdef" for character in candidate
            ):
                token = candidate
                break
        if not token:
            continue
        identity = entry_identity(transaction_fd, name)
        if identity is not None and identity not in identities:
            identities.append(identity)
    return identities


def quarantine_entry(
    source_parent_fd: int,
    source_name: str,
    transaction_fd: int,
) -> str | None:
    """Atomically preserve whichever entry currently occupies a mutable name."""

    quarantine_name = f"{QUARANTINE_PREFIX}{secrets.token_hex(16)}"
    try:
        os.replace(
            source_name,
            quarantine_name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=transaction_fd,
        )
    except FileNotFoundError:
        return None
    return quarantine_name


def prepare_discard_directory(
    transaction_fd: int,
    protected_identities: frozenset[SnapshotIdentity],
) -> bool:
    """Empty the fixed discard inode through a pinned descriptor.

    The discard directory itself is retained and reused, bounding cleanup
    remnants without recursively deleting a mutable snapshot pathname.
    """

    try:
        discard_fd = os.open(
            DISCARD_NAME,
            directory_open_flags(),
            dir_fd=transaction_fd,
        )
    except FileNotFoundError:
        try:
            os.mkdir(DISCARD_NAME, mode=0o700, dir_fd=transaction_fd)
        except FileExistsError:
            return True
        return False
    except OSError:
        try:
            os.unlink(DISCARD_NAME, dir_fd=transaction_fd)
            os.mkdir(DISCARD_NAME, mode=0o700, dir_fd=transaction_fd)
        except OSError:
            return True
        return False
    try:
        identity = descriptor_identity(discard_fd)
        if identity in protected_identities:
            return True
        observed_change = clear_directory_preserving_identities(
            discard_fd,
            frozenset(
                (protected.device, protected.inode)
                for protected in protected_identities
            ),
            transaction_fd,
        )
        return observed_change or not entry_matches_identity(
            transaction_fd,
            DISCARD_NAME,
            identity,
        )
    finally:
        os.close(discard_fd)


def discard_untrusted_entry(
    transaction_fd: int,
    source_name: str,
    protected_identities: frozenset[SnapshotIdentity],
) -> bool:
    """Move an untrusted remnant into pinned, fixed-size discard storage."""

    if prepare_discard_directory(transaction_fd, protected_identities):
        return True
    try:
        os.replace(
            source_name,
            DISCARD_NAME,
            src_dir_fd=transaction_fd,
            dst_dir_fd=transaction_fd,
        )
    except FileNotFoundError:
        return False
    except OSError:
        # Files and symlinks cannot replace the discard directory. Unlinking
        # cannot remove a directory if a protected snapshot races into place.
        try:
            os.unlink(source_name, dir_fd=transaction_fd)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return False
    return prepare_discard_directory(transaction_fd, protected_identities)


def _transaction_dir_name(repo_name: str) -> str:
    digest = hashlib.sha256(repo_name.encode("utf-8")).hexdigest()
    return f".taali-transactions-{digest}"


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
        raise UnsafeRepositoryPathError("Task repository transaction namespace changed")
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


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024, MAX_STATE_BYTES + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_STATE_BYTES:
            raise UnsafeRepositoryPathError("Task repository state file is too large")


def read_publication_state(transaction_fd: int) -> PublicationState | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(STATE_NAME, flags, dir_fd=transaction_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            "Task repository publication state is unsafe"
        ) from exc
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_size > MAX_STATE_BYTES:
            raise UnsafeRepositoryPathError(
                "Task repository publication state is invalid"
            )
        payload = json.loads(_read_all(descriptor).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
            raise ValueError("unsupported publication state")
        canonical = SnapshotIdentity.from_payload(payload.get("canonical"))
        raw_fallback = payload.get("fallback")
        fallback = (
            None
            if raw_fallback is None
            else SnapshotIdentity.from_payload(raw_fallback)
        )
        return PublicationState(canonical=canonical, fallback=fallback)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise UnsafeRepositoryPathError(
            "Task repository publication state is invalid"
        ) from exc
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short publication-state write")
        offset += written


def write_publication_state(
    transaction_fd: int,
    state: PublicationState,
) -> None:
    payload = json.dumps(
        {
            "version": STATE_VERSION,
            "canonical": state.canonical.payload(),
            "fallback": state.fallback.payload() if state.fallback else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_name = f"{STATE_TEMP_PREFIX}{secrets.token_hex(16)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=transaction_fd,
        )
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            STATE_NAME,
            src_dir_fd=transaction_fd,
            dst_dir_fd=transaction_fd,
        )
        os.fsync(transaction_fd)
        temporary_name = ""
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            "Task repository publication state could not be persisted"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=transaction_fd)
            except OSError:
                pass


def cleanup_state_temporaries(transaction_fd: int) -> None:
    for name in os.listdir(transaction_fd):
        if name.startswith(STATE_TEMP_PREFIX):
            try:
                os.unlink(name, dir_fd=transaction_fd)
            except OSError:
                pass


def cleanup_abandoned_staging(transaction_fd: int) -> bool:
    """Remove staging remnants without deleting a journaled snapshot inode."""

    try:
        state = read_publication_state(transaction_fd)
    except UnsafeRepositoryPathError:
        return True
    preserved = frozenset(
        value
        for value in (
            state.canonical if state is not None else None,
            state.fallback if state is not None else None,
        )
        if value is not None
    )
    observed_swap = prepare_discard_directory(transaction_fd, preserved)
    for name in os.listdir(transaction_fd):
        if not name.startswith("staging-"):
            continue
        quarantine_name = quarantine_entry(transaction_fd, name, transaction_fd)
        if quarantine_name is None:
            continue
        identity = entry_identity(transaction_fd, quarantine_name)
        if identity is not None and identity in preserved:
            observed_swap = True
            continue
        observed_swap |= discard_untrusted_entry(
            transaction_fd,
            quarantine_name,
            preserved,
        )
    return observed_swap
