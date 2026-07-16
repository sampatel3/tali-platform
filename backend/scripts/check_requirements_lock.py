#!/usr/bin/env python3
"""Fail when the hashed CI lock is stale relative to its direct inputs."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import sys


BACKEND_ROOT = Path(__file__).resolve().parent.parent
INPUTS = ("requirements.txt", "requirements-dev.txt")
LOCK_FILE = "requirements-lock.txt"
_DIGEST_RE = re.compile(r"^# input-sha256: ([0-9a-f]{64})$", re.MULTILINE)


def input_digest(root: Path = BACKEND_ROOT) -> str:
    digest = sha256()
    for name in INPUTS:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def find_failures(root: Path = BACKEND_ROOT) -> list[str]:
    lock_path = root / LOCK_FILE
    if not lock_path.is_file():
        return [f"missing {LOCK_FILE}"]
    lock = lock_path.read_text(encoding="utf-8")
    marker = _DIGEST_RE.search(lock)
    failures: list[str] = []
    if marker is None:
        failures.append(f"{LOCK_FILE} is missing its input-sha256 marker")
    elif marker.group(1) != input_digest(root):
        failures.append(
            f"{LOCK_FILE} is stale; regenerate it with the uv command in its header"
        )
    if "--hash=sha256:" not in lock:
        failures.append(f"{LOCK_FILE} is not hash-locked")
    return failures


def main() -> int:
    failures = find_failures()
    if failures:
        for failure in failures:
            print(f"requirements lock FAILED: {failure}")
        return 1
    print(f"requirements lock passed ({input_digest()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
