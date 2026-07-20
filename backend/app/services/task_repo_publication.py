"""Crash-safe transaction state for canonical task repository publication."""

from __future__ import annotations

import os
import secrets

from .repository_path_safety import (
    UnsafeRepositoryPathError,
    directory_open_flags,
    entry_exists_at,
    remove_entry_at,
)
from .task_repo_transaction_state import (
    FALLBACK_NAME,
    FALLBACK_OLD_NAME,
    PublicationState,
    SnapshotIdentity,
    _acquire_publication_lock as _acquire_publication_lock,
    _open_transaction_directory as _open_transaction_directory,
    _transaction_dir_name as _transaction_dir_name,
    cleanup_abandoned_staging as _cleanup_abandoned_staging,
    cleanup_state_temporaries as _cleanup_state_temporaries,
    descriptor_identity,
    discard_untrusted_entry,
    entry_identity,
    entry_matches_identity,
    find_identity_name,
    legacy_backup_identity_candidates,
    prepare_discard_directory,
    quarantine_entry,
    read_publication_state,
    snapshot_remnant_names,
    write_publication_state,
)


def _remove_if_present(parent_fd: int, name: str) -> None:
    if entry_exists_at(parent_fd, name):
        remove_entry_at(parent_fd, name)


def _find_identity_source(
    identity: SnapshotIdentity,
    source_parent_fds: tuple[int, ...],
    *,
    destination_parent_fd: int,
    destination_name: str,
) -> tuple[int, str] | None:
    for source_parent_fd in source_parent_fds:
        source_name = find_identity_name(
            source_parent_fd,
            identity,
            excluded_names=(
                frozenset({destination_name})
                if source_parent_fd == destination_parent_fd
                else frozenset()
            ),
        )
        if source_name is not None:
            return source_parent_fd, source_name
    return None


def _restore_identity(
    identity: SnapshotIdentity,
    source_parent_fds: tuple[int, ...],
    destination_parent_fd: int,
    destination_name: str,
    transaction_fd: int,
) -> bool:
    """Restore a journaled directory identity without trusting a mutable name.

    A substitute destination is atomically moved into quarantine, never
    recursively deleted. If another actor swaps either mutable name, locate the
    durable identity again for a bounded retry. The known-good inode is never a
    target of a destructive operation.
    """

    if entry_matches_identity(destination_parent_fd, destination_name, identity):
        return False
    observed_swap = False
    for _attempt in range(3):
        source = _find_identity_source(
            identity,
            source_parent_fds,
            destination_parent_fd=destination_parent_fd,
            destination_name=destination_name,
        )
        if source is None:
            raise UnsafeRepositoryPathError(
                "Journaled task repository snapshot is no longer recoverable"
            )
        quarantine_entry(
            destination_parent_fd,
            destination_name,
            transaction_fd,
        )
        source_parent_fd, source_name = source
        try:
            os.replace(
                source_name,
                destination_name,
                src_dir_fd=source_parent_fd,
                dst_dir_fd=destination_parent_fd,
            )
        except OSError:
            observed_swap = True
            continue
        if entry_matches_identity(
            destination_parent_fd,
            destination_name,
            identity,
        ):
            return observed_swap
        observed_swap = True
    raise UnsafeRepositoryPathError(
        "Journaled task repository snapshot changed repeatedly during recovery"
    )


def _normalize_fallback(
    transaction_fd: int,
    identity: SnapshotIdentity | None,
    protected_identities: frozenset[SnapshotIdentity],
) -> bool:
    observed_swap = prepare_discard_directory(
        transaction_fd,
        protected_identities,
    )
    if identity is not None:
        observed_swap |= _restore_identity(
            identity,
            (transaction_fd,),
            transaction_fd,
            FALLBACK_NAME,
            transaction_fd,
        )
    for name in snapshot_remnant_names(transaction_fd):
        if identity is not None and entry_matches_identity(
            transaction_fd,
            name,
            identity,
        ):
            continue
        quarantine_name = quarantine_entry(
            transaction_fd,
            name,
            transaction_fd,
        )
        if quarantine_name is None:
            continue
        quarantined_identity = entry_identity(transaction_fd, quarantine_name)
        if quarantined_identity in protected_identities:
            observed_swap = True
            continue
        observed_swap |= discard_untrusted_entry(
            transaction_fd,
            quarantine_name,
            protected_identities,
        )
    return observed_swap


def _publish_pinned_staging(
    root_fd: int,
    transaction_fd: int,
    repo_name: str,
    staging_name: str,
    staging_fd: int,
) -> None:
    """Publish staging while retaining one journaled prior snapshot.

    The state update is the commit point.  The verified prior canonical remains
    as ``fallback`` after success, so a path swap after the final in-function
    check cannot cause this code to delete the last known-good snapshot.  An
    external actor can still mutate a pathname after this function returns;
    the next locked recovery detects that identity mismatch and rolls back.
    """

    staging_identity = descriptor_identity(staging_fd)
    state = read_publication_state(transaction_fd)
    canonical_identity = entry_identity(root_fd, repo_name)
    fallback_identity: SnapshotIdentity | None = None

    if canonical_identity is not None:
        if state is None or canonical_identity != state.canonical:
            raise UnsafeRepositoryPathError(
                "Canonical task repository does not match durable state"
            )
        current_fd = os.open(
            repo_name,
            directory_open_flags(),
            dir_fd=root_fd,
        )
        try:
            canonical_identity = descriptor_identity(current_fd)
            if canonical_identity != state.canonical:
                raise UnsafeRepositoryPathError(
                    "Canonical task repository changed before fallback rotation"
                )
            prior_protected = frozenset(
                value
                for value in (state.canonical, state.fallback)
                if value is not None
            )
            if _normalize_fallback(
                transaction_fd,
                state.fallback,
                prior_protected,
            ):
                raise UnsafeRepositoryPathError(
                    "Task repository fallback changed during rotation"
                )
            if state.fallback is not None:
                os.replace(
                    FALLBACK_NAME,
                    FALLBACK_OLD_NAME,
                    src_dir_fd=transaction_fd,
                    dst_dir_fd=transaction_fd,
                )
            new_fallback_name = f"fallback-new-{secrets.token_hex(16)}"
            os.replace(
                repo_name,
                new_fallback_name,
                src_dir_fd=root_fd,
                dst_dir_fd=transaction_fd,
            )
            if not entry_matches_identity(
                transaction_fd,
                new_fallback_name,
                canonical_identity,
            ):
                _restore_identity(
                    canonical_identity,
                    (root_fd, transaction_fd),
                    root_fd,
                    repo_name,
                    transaction_fd,
                )
                raise UnsafeRepositoryPathError(
                    "Canonical task repository changed during fallback rotation"
                )

            # The verified current canonical is now the newer fallback.  Keep
            # the older fallback until the new canonical and state commit; a
            # swap of either one path can therefore never make cleanup delete
            # the only known-good snapshot.
            _restore_identity(
                canonical_identity,
                (transaction_fd,),
                transaction_fd,
                FALLBACK_NAME,
                transaction_fd,
            )
            fallback_identity = canonical_identity
        finally:
            os.close(current_fd)
    elif state is not None:
        raise UnsafeRepositoryPathError(
            "Durable task repository state has no canonical snapshot"
        )

    published = False
    try:
        os.replace(
            staging_name,
            repo_name,
            src_dir_fd=transaction_fd,
            dst_dir_fd=root_fd,
        )
        if not entry_matches_identity(root_fd, repo_name, staging_identity):
            raise UnsafeRepositoryPathError(
                "Task repository staging path changed during publication"
            )
        published = True
        write_publication_state(
            transaction_fd,
            PublicationState(
                canonical=staging_identity,
                fallback=fallback_identity,
            ),
        )
        if _normalize_fallback(
            transaction_fd,
            fallback_identity,
            frozenset(
                value
                for value in (staging_identity, fallback_identity)
                if value is not None
            ),
        ) or not entry_matches_identity(root_fd, repo_name, staging_identity):
            raise UnsafeRepositoryPathError(
                "Task repository path changed during committed cleanup"
            )
    except BaseException:
        if fallback_identity is not None:
            _restore_identity(
                fallback_identity,
                (transaction_fd,),
                root_fd,
                repo_name,
                transaction_fd,
            )
            write_publication_state(
                transaction_fd,
                PublicationState(canonical=fallback_identity),
            )
        elif not published:
            _remove_if_present(root_fd, repo_name)
        raise


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


def _recover_interrupted_publication(
    root_fd: int,
    transaction_fd: int,
    repo_name: str,
) -> None:
    """Reconcile the canonical path against the durable identity journal."""

    _cleanup_state_temporaries(transaction_fd)
    state = read_publication_state(transaction_fd)
    canonical_identity = entry_identity(root_fd, repo_name)
    observed_recovery_swap = False

    if state is None:
        legacy_candidates = legacy_backup_identity_candidates(transaction_fd)
        if len(legacy_candidates) > 1:
            raise UnsafeRepositoryPathError(
                f"Ambiguous task repository backups for {repo_name!r}"
            )
        if legacy_candidates:
            # Without a journal, a surviving backup is the only explicit
            # known-good handoff.  Never prefer an arbitrary canonical directory.
            canonical_identity = legacy_candidates[0]
            observed_recovery_swap = _restore_identity(
                canonical_identity,
                (transaction_fd,),
                root_fd,
                repo_name,
                transaction_fd,
            )
        elif canonical_identity is None:
            if entry_exists_at(root_fd, repo_name):
                # A non-directory/symlink is not an adoptable snapshot.  Remove
                # only the entry itself (never a symlink target) so a fresh
                # first publication can proceed.
                remove_entry_at(root_fd, repo_name)
            _cleanup_abandoned_staging(transaction_fd)
            return
        state = PublicationState(canonical=canonical_identity)
        write_publication_state(transaction_fd, state)

    if not entry_matches_identity(root_fd, repo_name, state.canonical):
        expected_source = _find_identity_source(
            state.canonical,
            (root_fd, transaction_fd),
            destination_parent_fd=root_fd,
            destination_name=repo_name,
        )
        if expected_source is not None:
            recovered_identity = state.canonical
            recovered_fallback = state.fallback
        elif state.fallback is not None and (
            entry_matches_identity(root_fd, repo_name, state.fallback)
            or _find_identity_source(
                state.fallback,
                (transaction_fd, root_fd),
                destination_parent_fd=root_fd,
                destination_name=repo_name,
            )
            is not None
        ):
            recovered_identity = state.fallback
            recovered_fallback = None
        else:
            raise UnsafeRepositoryPathError(
                "No journaled task repository snapshot remains"
            )

        observed_recovery_swap = _restore_identity(
            recovered_identity,
            (root_fd, transaction_fd),
            root_fd,
            repo_name,
            transaction_fd,
        )
        state = PublicationState(
            canonical=recovered_identity,
            fallback=recovered_fallback,
        )
        write_publication_state(transaction_fd, state)

    recorded_fallback = state.fallback
    fallback_identity = recorded_fallback
    if (
        fallback_identity is not None
        and find_identity_name(
            transaction_fd,
            fallback_identity,
        )
        is None
    ):
        fallback_identity = None

    protected = frozenset(
        value for value in (state.canonical, recorded_fallback) if value is not None
    )
    normalization_swap = _normalize_fallback(
        transaction_fd,
        fallback_identity,
        protected,
    )
    if normalization_swap:
        raise UnsafeRepositoryPathError(
            "Recovered task repository path changed during publication"
        )
    final_state = PublicationState(
        canonical=state.canonical,
        fallback=fallback_identity,
    )
    observed_recovery_swap |= _cleanup_abandoned_staging(transaction_fd)
    if (
        observed_recovery_swap
        or not entry_matches_identity(root_fd, repo_name, state.canonical)
        or (
            fallback_identity is not None
            and not entry_matches_identity(
                transaction_fd,
                FALLBACK_NAME,
                fallback_identity,
            )
        )
        or (
            recorded_fallback is not None
            and fallback_identity is None
            and find_identity_name(transaction_fd, recorded_fallback) is not None
        )
    ):
        raise UnsafeRepositoryPathError(
            "Recovered task repository path changed during publication"
        )
    if final_state != state:
        write_publication_state(transaction_fd, final_state)
