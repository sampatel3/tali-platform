"""Bounded, descriptor-relative cleanup for task repository remnants."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from .repository_path_safety import directory_open_flags


@dataclass
class _OpenedEntry:
    kind: str
    descriptor: int | None = None
    parent_descriptor: int | None = None
    name: str | None = None

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
        if self.parent_descriptor is not None:
            os.close(self.parent_descriptor)


def _identity(descriptor: int) -> tuple[int, int]:
    value = os.fstat(descriptor)
    return int(value.st_dev), int(value.st_ino)


def _promote_entry(parent_fd: int, name: str, preserve_parent_fd: int) -> None:
    preserved_name = f"quarantine-{secrets.token_hex(16)}"
    try:
        os.replace(
            name,
            preserved_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=preserve_parent_fd,
        )
    except OSError:
        pass


def _open_relative_entry(
    root_fd: int,
    parts: tuple[str, ...],
    protected_identities: frozenset[tuple[int, int]],
    preserve_parent_fd: int,
) -> _OpenedEntry:
    current_fd = os.dup(root_fd)
    for index, name in enumerate(parts):
        is_final = index == len(parts) - 1
        try:
            child_fd = os.open(
                name,
                directory_open_flags(),
                dir_fd=current_fd,
            )
        except FileNotFoundError:
            os.close(current_fd)
            return _OpenedEntry("missing")
        except OSError:
            if is_final:
                return _OpenedEntry(
                    "non-directory",
                    parent_descriptor=current_fd,
                    name=name,
                )
            os.close(current_fd)
            return _OpenedEntry("changed")

        if _identity(child_fd) in protected_identities:
            _promote_entry(current_fd, name, preserve_parent_fd)
            os.close(child_fd)
            os.close(current_fd)
            return _OpenedEntry("protected")
        if is_final:
            return _OpenedEntry(
                "directory",
                descriptor=child_fd,
                parent_descriptor=current_fd,
                name=name,
            )
        os.close(current_fd)
        current_fd = child_fd
    os.close(current_fd)
    return _OpenedEntry("missing")


def clear_directory_preserving_identities(
    directory_fd: int,
    protected_identities: frozenset[tuple[int, int]],
    preserve_parent_fd: int,
) -> bool:
    """Iteratively clear a pinned tree and promote protected directories.

    Paths are reopened one component at a time with ``O_NOFOLLOW``. This avoids
    Python recursion and pathname-length limits while keeping only a bounded
    number of descriptors open, so every accepted manifest depth remains
    cleanable.
    """

    observed_change = False
    for _attempt in range(3):
        stack: list[tuple[tuple[str, ...], bool]] = [
            ((name,), False) for name in reversed(os.listdir(directory_fd))
        ]
        while stack:
            parts, postorder = stack.pop()
            opened = _open_relative_entry(
                directory_fd,
                parts,
                protected_identities,
                preserve_parent_fd,
            )
            try:
                if opened.kind in {"protected", "changed"}:
                    observed_change = True
                    continue
                if opened.kind == "missing":
                    continue
                if opened.kind == "non-directory":
                    try:
                        os.unlink(
                            opened.name or "",
                            dir_fd=opened.parent_descriptor,
                        )
                    except FileNotFoundError:
                        pass
                    except OSError:
                        observed_change = True
                    continue
                if not postorder:
                    child_names = os.listdir(opened.descriptor)
                    stack.append((parts, True))
                    stack.extend(
                        (parts + (child_name,), False)
                        for child_name in reversed(child_names)
                    )
                    continue
                try:
                    os.rmdir(
                        opened.name or "",
                        dir_fd=opened.parent_descriptor,
                    )
                except FileNotFoundError:
                    pass
                except OSError:
                    observed_change = True
            finally:
                opened.close()
        if not os.listdir(directory_fd):
            return observed_change
    return True
