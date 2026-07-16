"""Bypass regressions for the backend file-size ratchet."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_gate():
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_file_sizes.py"
    spec = importlib.util.spec_from_file_location("check_file_sizes_regression", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_general_ceiling_catches_large_module_with_non_service_name(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gate = _load_gate()
    app = tmp_path / "app"
    app.mkdir()
    oversized = app / "renamed_hotspot.py"
    oversized.write_text("VALUE = 1\n" * (gate.GENERAL_SIZE_LIMIT + 1), encoding="utf-8")
    monkeypatch.setattr(gate, "BACKEND_ROOT", tmp_path)

    violations = gate.find_violations()

    assert any("app/renamed_hotspot.py" in violation for violation in violations)


def test_nonstandard_route_registration_gets_strict_limit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gate = _load_gate()
    app = tmp_path / "app"
    app.mkdir()
    route = app / "custom.py"
    route.write_text(
        "router.add_api_route('/write', endpoint, methods=['POST'])\n"
        + "VALUE = 1\n" * gate.SIZE_LIMIT,
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", tmp_path)

    violations = gate.find_violations()

    assert any(
        "app/custom.py" in violation and "route/service limit" in violation
        for violation in violations
    )
