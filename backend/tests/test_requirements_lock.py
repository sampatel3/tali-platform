from __future__ import annotations

from pathlib import Path

from scripts.check_requirements_lock import find_failures, input_digest


def _write_inputs(root: Path) -> None:
    (root / "requirements.txt").write_text("runtime==1.0\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text(
        "-r requirements.txt\npytest==9.0.3\n",
        encoding="utf-8",
    )


def test_fresh_hashed_lock_passes(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    digest = input_digest(tmp_path)
    (tmp_path / "requirements-lock.txt").write_text(
        f"# input-sha256: {digest}\n"
        "runtime==1.0 \\\n    --hash=sha256:abc\n",
        encoding="utf-8",
    )

    assert find_failures(tmp_path) == []


def test_changed_direct_requirement_invalidates_lock(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    digest = input_digest(tmp_path)
    (tmp_path / "requirements-lock.txt").write_text(
        f"# input-sha256: {digest}\n"
        "runtime==1.0 \\\n    --hash=sha256:abc\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("runtime==2.0\n", encoding="utf-8")

    assert any("stale" in failure for failure in find_failures(tmp_path))
