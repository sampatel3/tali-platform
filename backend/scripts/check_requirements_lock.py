#!/usr/bin/env python3
"""Fail when either hashed dependency lock is stale or structurally unsafe."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import shutil
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DIGEST_RE = re.compile(r"^# input-sha256: ([0-9a-f]{64})$", re.MULTILINE)
_PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^]]+\])?==[^\s\\]+ \\$")
_HASH_RE = re.compile(r"^    --hash=sha256:[0-9a-f]{64}(?: \\)?$")


@dataclass(frozen=True)
class LockSpec:
    filename: str
    inputs: tuple[str, ...]
    compile_input: str
    python_version: str | None = None


CI_LOCK = LockSpec(
    filename="requirements-lock.txt",
    inputs=("requirements.txt", "requirements-dev.txt"),
    compile_input="requirements-dev.txt",
    python_version="3.11",
)
RUNTIME_LOCK = LockSpec(
    filename="requirements-runtime-lock.txt",
    inputs=("requirements.txt", "runtime.txt"),
    compile_input="requirements.txt",
)
LOCK_SPECS = (CI_LOCK, RUNTIME_LOCK)
RUNTIME_FORBIDDEN_PACKAGES = frozenset(
    {
        "aiosqlite",
        "httpx2",
        "pip-audit",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "ruff",
    }
)


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_python_version(root: Path) -> str:
    runtime_path = root / "runtime.txt"
    if not runtime_path.is_file():
        raise RuntimeError("missing runtime.txt")
    value = runtime_path.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"python-(\d+\.\d+\.\d+)", value)
    if match is None:
        raise RuntimeError("runtime.txt must contain exactly python-X.Y.Z")
    return match.group(1)


def _python_version(spec: LockSpec, root: Path) -> str:
    if spec.python_version is not None:
        return spec.python_version
    return _runtime_python_version(root)


def _regenerate_command(spec: LockSpec, root: Path) -> str:
    return (
        f"uv pip compile {spec.compile_input} "
        f"--python-version {_python_version(spec, root)} "
        "--python-platform x86_64-unknown-linux-gnu --generate-hashes "
        f"--output-file {spec.filename}"
    )


def _input_digest(inputs: tuple[str, ...], root: Path) -> str:
    digest = sha256()
    for name in inputs:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def input_digest(root: Path = BACKEND_ROOT) -> str:
    """Return the backward-compatible digest for the dev-inclusive CI lock."""

    return _input_digest(CI_LOCK.inputs, root)


def runtime_input_digest(root: Path = BACKEND_ROOT) -> str:
    return _input_digest(RUNTIME_LOCK.inputs, root)


def _structure_failures(lock: str, filename: str) -> tuple[list[str], set[str]]:
    failures: list[str] = []
    packages: set[str] = set()
    lines = lock.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[0].isspace():
            failures.append(f"{filename}:{index + 1} has a stray continuation")
            index += 1
            continue

        pin = _PIN_RE.fullmatch(line)
        if pin is None:
            failures.append(
                f"{filename}:{index + 1} is not an exact name==version pin"
            )
            index += 1
            continue

        package = _canonical_name(pin.group("name"))
        if package in packages:
            failures.append(f"{filename}:{index + 1} duplicates {package}")
        packages.add(package)
        index += 1
        hashes = 0
        while index < len(lines) and (
            not lines[index] or lines[index][0].isspace()
        ):
            continuation = lines[index]
            if not continuation or continuation.lstrip().startswith("#"):
                index += 1
                continue
            if _HASH_RE.fullmatch(continuation) is None:
                failures.append(
                    f"{filename}:{index + 1} is not an exact sha256 hash"
                )
            else:
                hashes += 1
            index += 1
        if hashes == 0:
            failures.append(f"{filename} leaves {package} unhashed")

    if not packages:
        failures.append(f"{filename} has no pinned packages")
    return failures, packages


def _lock_failures(spec: LockSpec, root: Path) -> tuple[list[str], set[str]]:
    missing_inputs = [name for name in spec.inputs if not (root / name).is_file()]
    if missing_inputs:
        return [f"missing {name}" for name in missing_inputs], set()
    try:
        _python_version(spec, root)
    except RuntimeError as exc:
        return [str(exc)], set()
    lock_path = root / spec.filename
    if not lock_path.is_file():
        return [f"missing {spec.filename}"], set()
    lock = lock_path.read_text(encoding="utf-8")
    marker = _DIGEST_RE.search(lock)
    failures: list[str] = []
    if marker is None:
        failures.append(f"{spec.filename} is missing its input-sha256 marker")
    elif marker.group(1) != _input_digest(spec.inputs, root):
        failures.append(
            f"{spec.filename} is stale; regenerate it with: "
            f"{_regenerate_command(spec, root)}"
        )
    structure_failures, packages = _structure_failures(lock, spec.filename)
    failures.extend(structure_failures)
    return failures, packages


def find_failures(
    root: Path = BACKEND_ROOT, *, runtime_only: bool = False
) -> list[str]:
    specs = (RUNTIME_LOCK,) if runtime_only else LOCK_SPECS
    failures: list[str] = []
    runtime_packages: set[str] = set()
    for spec in specs:
        lock_failures, packages = _lock_failures(spec, root)
        failures.extend(lock_failures)
        if spec == RUNTIME_LOCK:
            runtime_packages = packages
    forbidden = sorted(runtime_packages & RUNTIME_FORBIDDEN_PACKAGES)
    if forbidden:
        failures.append(
            "requirements-runtime-lock.txt contains dev-only packages: "
            + ", ".join(forbidden)
        )
    return failures


def compile_locks(
    root: Path = BACKEND_ROOT, *, runtime_only: bool = False
) -> None:
    """Regenerate selected locks and bind each header to its exact input bytes."""

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to regenerate dependency locks")
    specs = (RUNTIME_LOCK,) if runtime_only else LOCK_SPECS
    for spec in specs:
        digest = _input_digest(spec.inputs, root)
        python_version = _python_version(spec, root)
        header = f"{_regenerate_command(spec, root)}\n# input-sha256: {digest}"
        subprocess.run(
            [
                uv,
                "pip",
                "compile",
                spec.compile_input,
                "--python-version",
                python_version,
                "--python-platform",
                "x86_64-unknown-linux-gnu",
                "--generate-hashes",
                "--custom-compile-command",
                header,
                "--output-file",
                spec.filename,
                "--quiet",
            ],
            cwd=root,
            check=True,
        )
        if _input_digest(spec.inputs, root) != digest:
            raise RuntimeError(f"{', '.join(spec.inputs)} changed during lock generation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="validate only the production runtime lock",
    )
    parser.add_argument(
        "--compile",
        action="store_true",
        help="regenerate selected locks with uv before validating them",
    )
    args = parser.parse_args()
    if args.compile:
        try:
            compile_locks(runtime_only=args.runtime_only)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"requirements lock compilation FAILED: {exc}")
            return 1
    failures = find_failures(runtime_only=args.runtime_only)
    if failures:
        for failure in failures:
            print(f"requirements lock FAILED: {failure}")
        return 1
    if args.runtime_only:
        print(f"runtime requirements lock passed ({runtime_input_digest()})")
    else:
        print(
            "requirements locks passed "
            f"(ci={input_digest()}, runtime={runtime_input_digest()})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
