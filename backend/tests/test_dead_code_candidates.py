"""Regression tests for the repository dead-code candidate graph."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dead_code_candidates.py"
_SPEC = importlib.util.spec_from_file_location("dead_code_candidates", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
dead_code_candidates = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = dead_code_candidates
_SPEC.loader.exec_module(dead_code_candidates)


def test_package_init_relative_import_resolves_inside_package(tmp_path: Path) -> None:
    app = tmp_path / "app"
    feature = app / "domains" / "feature"
    feature.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "domains" / "__init__.py").write_text("", encoding="utf-8")
    (feature / "__init__.py").write_text(
        "from .routes import router\n",
        encoding="utf-8",
    )
    (feature / "routes.py").write_text("router = object()\n", encoding="utf-8")
    (app / "main.py").write_text(
        "from app.domains import feature\n",
        encoding="utf-8",
    )

    graph = dead_code_candidates.build_graph(app)

    assert "app.domains.feature.routes" in graph["app.domains.feature"].imports
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}
    assert "app.domains.feature.routes" not in candidates


def test_nested_package_parent_import_resolves_one_level_up(tmp_path: Path) -> None:
    app = tmp_path / "app"
    feature = app / "domains" / "feature"
    feature.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "domains" / "__init__.py").write_text("", encoding="utf-8")
    (app / "domains" / "shared.py").write_text("value = 1\n", encoding="utf-8")
    (feature / "__init__.py").write_text(
        "from ..shared import value\n",
        encoding="utf-8",
    )

    graph = dead_code_candidates.build_graph(app)

    assert "app.domains.shared" in graph["app.domains.feature"].imports


def test_cli_entrypoints_are_not_reported_as_dead_modules(tmp_path: Path) -> None:
    app = tmp_path / "app"
    scripts = app / "scripts"
    scripts.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "maintenance.py").write_text(
        "def main():\n    return 0\n\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    (app / "unused_library.py").write_text("VALUE = 1\n", encoding="utf-8")

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert graph["app.scripts.maintenance"].is_entrypoint is True
    assert "app.scripts.maintenance" not in candidates
    assert "app.unused_library" in candidates


def test_arbitrary_main_guard_does_not_exempt_dead_library(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "unused.py").write_text(
        "if __name__ == '__main__':\n    raise SystemExit(0)\n",
        encoding="utf-8",
    )

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert graph["app.unused"].is_entrypoint is True
    assert "app.unused" in candidates


def test_dead_import_cycle_is_reported(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (app / "cycle_a.py").write_text("import app.cycle_b\n", encoding="utf-8")
    (app / "cycle_b.py").write_text("import app.cycle_a\n", encoding="utf-8")

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert {"app.cycle_a", "app.cycle_b"} <= candidates


def test_unused_package_cannot_keep_its_own_routes_alive(tmp_path: Path) -> None:
    app = tmp_path / "app"
    feature = app / "domains" / "unused"
    feature.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "domains" / "__init__.py").write_text("", encoding="utf-8")
    (feature / "__init__.py").write_text("from .routes import router\n", encoding="utf-8")
    (feature / "routes.py").write_text("router = object()\n", encoding="utf-8")
    (app / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert "app.domains.unused.routes" in candidates


def test_excluded_prefix_lookalikes_are_not_exempt(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (app / "main_old.py").write_text("VALUE = 1\n", encoding="utf-8")
    (app / "platform_dead.py").write_text("VALUE = 1\n", encoding="utf-8")

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert {"app.main_old", "app.platform_dead"} <= candidates


def test_type_only_and_provably_dead_imports_do_not_create_live_edges(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "main.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import app.type_only\n"
        "if False:\n    import app.impossible\n",
        encoding="utf-8",
    )
    (app / "type_only.py").write_text("VALUE = 1\n", encoding="utf-8")
    (app / "impossible.py").write_text("VALUE = 1\n", encoding="utf-8")

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert "app.type_only" not in graph["app.main"].imports
    assert "app.impossible" not in graph["app.main"].imports
    assert {"app.type_only", "app.impossible"} <= candidates


def test_nonempty_unreachable_package_initializer_is_reported(tmp_path: Path) -> None:
    app = tmp_path / "app"
    feature = app / "unused"
    feature.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (feature / "__init__.py").write_text("REGISTERED = True\n", encoding="utf-8")

    graph = dead_code_candidates.build_graph(app)
    candidates = {row["module"] for row in dead_code_candidates.compute_candidates(graph)}

    assert "app.unused" in candidates


def test_cli_can_fail_ci_when_candidates_exist(
    monkeypatch, tmp_path: Path
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "unused.py").write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "candidates.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dead_code_candidates.py",
            "--backend-root",
            str(app),
            "--output",
            str(output),
            "--fail-on-candidates",
        ],
    )

    assert dead_code_candidates.main() == 1
