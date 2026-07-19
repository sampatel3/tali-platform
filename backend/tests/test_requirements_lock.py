from __future__ import annotations

from pathlib import Path

from scripts.check_requirements_lock import (
    BACKEND_ROOT,
    RUNTIME_FORBIDDEN_PACKAGES,
    compile_locks,
    find_failures,
    input_digest,
    runtime_input_digest,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _write_inputs(root: Path) -> None:
    (root / "requirements.txt").write_text("runtime==1.0\n", encoding="utf-8")
    (root / "runtime.txt").write_text("python-3.11.9\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text(
        "-r requirements.txt\npytest==9.0.3\n",
        encoding="utf-8",
    )


def _write_locks(root: Path) -> None:
    (root / "requirements-lock.txt").write_text(
        f"# input-sha256: {input_digest(root)}\n"
        f"pytest==9.0.3 \\\n    --hash=sha256:{HASH_A}\n",
        encoding="utf-8",
    )
    (root / "requirements-runtime-lock.txt").write_text(
        f"# input-sha256: {runtime_input_digest(root)}\n"
        f"runtime==1.0 \\\n    --hash=sha256:{HASH_B}\n",
        encoding="utf-8",
    )


def test_fresh_hashed_locks_pass(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)

    assert find_failures(tmp_path) == []


def test_changed_runtime_requirement_invalidates_both_locks(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)
    (tmp_path / "requirements.txt").write_text("runtime==2.0\n", encoding="utf-8")

    stale = [failure for failure in find_failures(tmp_path) if "stale" in failure]

    assert len(stale) == 2
    assert any("requirements-lock.txt" in failure for failure in stale)
    assert any("requirements-runtime-lock.txt" in failure for failure in stale)


def test_changed_dev_requirement_only_invalidates_ci_lock(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)
    (tmp_path / "requirements-dev.txt").write_text(
        "-r requirements.txt\npytest==9.0.4\n",
        encoding="utf-8",
    )

    failures = find_failures(tmp_path)

    assert any("requirements-lock.txt is stale" in failure for failure in failures)
    assert not any(
        "requirements-runtime-lock.txt is stale" in failure for failure in failures
    )


def test_changed_python_runtime_only_invalidates_runtime_lock(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)
    (tmp_path / "runtime.txt").write_text("python-3.12.4\n", encoding="utf-8")

    failures = find_failures(tmp_path)

    assert any(
        "requirements-runtime-lock.txt is stale" in failure for failure in failures
    )
    assert not any("requirements-lock.txt is stale" in failure for failure in failures)


def test_malformed_python_runtime_fails_closed(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)
    (tmp_path / "runtime.txt").write_text("3.11\n", encoding="utf-8")

    assert "runtime.txt must contain exactly python-X.Y.Z" in find_failures(
        tmp_path, runtime_only=True
    )


def test_lock_rejects_unpinned_or_malformed_hash_entries(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)
    (tmp_path / "requirements-runtime-lock.txt").write_text(
        f"# input-sha256: {runtime_input_digest(tmp_path)}\n"
        "runtime>=1.0\n"
        "other==2.0 \\\n    --hash=sha256:not-a-real-hash\n",
        encoding="utf-8",
    )

    failures = find_failures(tmp_path, runtime_only=True)

    assert any("not an exact name==version pin" in failure for failure in failures)
    assert any("not an exact sha256 hash" in failure for failure in failures)
    assert any("leaves other unhashed" in failure for failure in failures)


def test_runtime_lock_rejects_dev_only_packages(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    _write_locks(tmp_path)
    (tmp_path / "requirements-runtime-lock.txt").write_text(
        f"# input-sha256: {runtime_input_digest(tmp_path)}\n"
        f"pytest==9.0.3 \\\n    --hash=sha256:{HASH_B}\n",
        encoding="utf-8",
    )

    assert any(
        "contains dev-only packages: pytest" in failure
        for failure in find_failures(tmp_path, runtime_only=True)
    )


def test_repository_locks_are_current_and_runtime_excludes_dev_tools() -> None:
    assert find_failures(BACKEND_ROOT) == []
    runtime_lock = (BACKEND_ROOT / "requirements-runtime-lock.txt").read_text()

    for package in RUNTIME_FORBIDDEN_PACKAGES:
        assert f"{package}==" not in runtime_lock


def test_repository_replaces_the_python_images_bundled_setuptools() -> None:
    runtime_requirements = (BACKEND_ROOT / "requirements.txt").read_text().splitlines()
    setuptools_pin = next(
        (line for line in runtime_requirements if line.startswith("setuptools==")),
        None,
    )

    assert setuptools_pin is not None
    for lock_name in ("requirements-lock.txt", "requirements-runtime-lock.txt"):
        lock = (BACKEND_ROOT / lock_name).read_text()
        assert f"{setuptools_pin} \\" in lock


def test_runtime_compiler_targets_supported_linux_and_embeds_digest(
    tmp_path: Path, monkeypatch
) -> None:
    _write_inputs(tmp_path)
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.setattr(
        "scripts.check_requirements_lock.shutil.which", lambda command: "/uv"
    )

    def fake_run(command, *, cwd, check):
        assert check is True
        calls.append((command, cwd))

    monkeypatch.setattr("scripts.check_requirements_lock.subprocess.run", fake_run)

    compile_locks(tmp_path, runtime_only=True)

    assert len(calls) == 1
    command, cwd = calls[0]
    assert cwd == tmp_path
    assert command[:4] == ["/uv", "pip", "compile", "requirements.txt"]
    assert command[command.index("--python-version") + 1] == "3.11.9"
    assert command[command.index("--python-platform") + 1] == (
        "x86_64-unknown-linux-gnu"
    )
    assert "--generate-hashes" in command
    assert "runtime.txt" not in command
    header = command[command.index("--custom-compile-command") + 1]
    assert f"# input-sha256: {runtime_input_digest(tmp_path)}" in header
