"""Focused tests for the stdlib-only AI feature eval coverage gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts/check_ai_eval_coverage.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_ai_eval_coverage", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _fixture(
    tmp_path: Path,
    *,
    enum_members: dict[str, str],
    entries: dict[str, object],
) -> tuple[Path, Path, Path]:
    backend_root = tmp_path / "backend"
    pricing_path = backend_root / "app/services/pricing_service.py"
    registry_path = backend_root / "app/evals/registry.json"
    test_path = backend_root / "tests/test_feature.py"
    pricing_path.parent.mkdir(parents=True)
    registry_path.parent.mkdir(parents=True)
    test_path.parent.mkdir(parents=True)

    member_source = "\n".join(
        f"    {name} = {value!r}" for name, value in enum_members.items()
    )
    pricing_path.write_text(
        "from enum import Enum\n\nclass Feature(str, Enum):\n"
        f"{member_source}\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": checker.SCHEMA_VERSION,
                "feature_enum": checker.FEATURE_ENUM,
                "features": entries,
            }
        ),
        encoding="utf-8",
    )
    test_path.write_text("def test_feature():\n    assert True\n", encoding="utf-8")
    return backend_root, pricing_path, registry_path


def _covered_entry() -> dict[str, object]:
    return {
        "risk": "high",
        "coverage": "behavioral",
        "test_paths": ["tests/test_feature.py"],
    }


def _grounded_entry(*, case: str = "tests/test_feature.py::test_feature") -> dict[str, object]:
    return {
        "risk": "critical",
        "coverage": "grounded_truth",
        "ground_truth_scope": "A deterministic input/output truth boundary.",
        "test_cases": [case],
    }


def test_repository_registry_covers_every_non_other_feature() -> None:
    errors, test_paths = checker.validate_registry()

    assert errors == []
    assert test_paths
    assert all(path.startswith("tests/") for path in test_paths)


def test_new_feature_enum_member_fails_until_it_is_registered(tmp_path: Path) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "NEW_AI": "new_ai", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert "Feature values missing from eval registry: new_ai" in errors


def test_registered_feature_requires_risk_coverage_and_real_tests(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={
            "alpha": {
                "risk": "urgent",
                "coverage": "claimed",
                "test_paths": ["tests/missing.py"],
            }
        },
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert any("risk must be one of" in error for error in errors)
    assert any("coverage must be one of" in error for error in errors)
    assert "alpha: registered test does not exist: tests/missing.py" in errors


def test_infrastructure_exemption_is_explicit_and_needs_no_test_path(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"TRANSPORT": "transport", "OTHER": "other"},
        entries={
            "transport": {
                "risk": "low",
                "infrastructure_exemption": (
                    "Transport only; semantic truth is evaluated by calling features."
                ),
            }
        },
    )

    errors, test_paths = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert errors == []
    assert test_paths == []


def test_critical_feature_requires_grounded_truth_or_reviewed_gap(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"AGENT": "agent", "OTHER": "other"},
        entries={
            "agent": {
                "risk": "critical",
                "coverage": "behavioral",
                "test_paths": ["tests/test_feature.py"],
            }
        },
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert (
        "agent: critical features require grounded_truth or a "
        "reviewed_semantic_exemption"
    ) in errors


def test_reviewed_semantic_gap_is_explicit_and_auditable(tmp_path: Path) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"AGENT": "agent", "OTHER": "other"},
        entries={
            "agent": {
                "risk": "critical",
                "coverage": "contract",
                "test_paths": ["tests/test_feature.py"],
                "reviewed_semantic_exemption": (
                    "No independent oracle yet; prompt changes require review."
                ),
            }
        },
    )

    errors, test_targets = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert errors == []
    assert test_targets == ["tests/test_feature.py"]


def test_grounded_truth_requires_a_named_scope_and_exact_existing_case(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"AGENT": "agent", "OTHER": "other"},
        entries={
            "agent": {
                "risk": "critical",
                "coverage": "grounded_truth",
                "ground_truth_scope": "",
                "test_cases": ["tests/test_feature.py::test_missing"],
            }
        },
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert "agent: ground_truth_scope needs a boundary" in errors
    assert (
        "agent: registered pytest case does not exist: "
        "tests/test_feature.py::test_missing"
    ) in errors


def test_grounded_truth_returns_exact_case_instead_of_whole_file(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"AGENT": "agent", "OTHER": "other"},
        entries={"agent": _grounded_entry()},
    )

    errors, test_targets = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert errors == []
    assert test_targets == ["tests/test_feature.py::test_feature"]


def test_other_is_deliberately_excluded_from_registry(tmp_path: Path) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry(), "other": _covered_entry()},
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert "OTHER must not be registered: other" in errors
