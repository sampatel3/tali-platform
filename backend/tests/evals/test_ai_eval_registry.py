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
    capabilities: dict[str, object] | None = None,
) -> tuple[Path, Path, Path]:
    backend_root = tmp_path / "backend"
    pricing_path = backend_root / "app/services/pricing_service.py"
    registry_path = backend_root / "app/evals/registry.json"
    test_path = backend_root / "tests/test_feature.py"
    workflow_path = backend_root.parent / ".github/workflows/ci.yml"
    pricing_path.parent.mkdir(parents=True)
    registry_path.parent.mkdir(parents=True)
    test_path.parent.mkdir(parents=True)
    workflow_path.parent.mkdir(parents=True)

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
                "capabilities": (
                    _capabilities() if capabilities is None else capabilities
                ),
            }
        ),
        encoding="utf-8",
    )
    test_path.write_text(
        "def test_feature():\n    assert True\n\n"
        "def test_related():\n    assert True\n",
        encoding="utf-8",
    )
    frontend_bindings = sorted(
        {
            binding
            for capability in checker.EXPECTED_CAPABILITY_BINDINGS.values()
            for binding in capability["frontend"]
        }
    )
    for binding in frontend_bindings:
        frontend_test = backend_root.parent / "frontend" / binding
        frontend_test.parent.mkdir(parents=True, exist_ok=True)
        frontend_test.write_text(
            "import { it, expect } from 'vitest';\n"
            "it('covers the registered surface', () => expect(true).toBe(true));\n",
            encoding="utf-8",
        )
    workflow_path.write_text(
        "name: fixture-ci\n# " + "\n# ".join(frontend_bindings) + "\n",
        encoding="utf-8",
    )
    return backend_root, pricing_path, registry_path


def _capability_entry(*, capability: str) -> dict[str, object]:
    surfaces = sorted(checker.ALLOWED_CAPABILITY_SURFACES)
    expected = checker.EXPECTED_CAPABILITY_BINDINGS[capability]
    bindings = {
        surface: sorted(expected[surface])
        for surface in surfaces
    }
    return {
        "risk": "critical",
        "ground_truth_scope": "A deterministic ordinary and related role oracle.",
        "required_surfaces": surfaces,
        "bindings": bindings,
        "ordinary_role_test_case": "tests/test_feature.py::test_feature",
        "related_role_test_case": "tests/test_feature.py::test_related",
        "regression_test_cases": ["tests/test_feature.py::test_feature"],
    }


def _capabilities() -> dict[str, object]:
    return {
        name: _capability_entry(capability=name)
        for name in checker.REQUIRED_CAPABILITIES
    }


CAPABILITY_TARGETS = [
    "tests/test_feature.py::test_feature",
    "tests/test_feature.py::test_related",
]


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
    assert test_paths == CAPABILITY_TARGETS


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
    assert test_targets == ["tests/test_feature.py", *CAPABILITY_TARGETS]


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
    assert test_targets == CAPABILITY_TARGETS


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


def test_required_capability_needs_every_surface_and_distinct_role_truth_cases(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities()
    current_state = capabilities["candidate.pool_state"]
    assert isinstance(current_state, dict)
    current_state["required_surfaces"] = ["rest", "public_mcp"]
    current_state["bindings"] = {
        "rest": ["GET /api/v1/roles/{role_id}/applications"],
        "public_mcp": ["search_role_candidates", "get_role_candidate"],
    }
    current_state["related_role_test_case"] = current_state[
        "ordinary_role_test_case"
    ]
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
        capabilities=capabilities,
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert any("required surfaces missing" in error for error in errors)
    assert any("require distinct truth cases" in error for error in errors)


def test_registry_cannot_drop_a_required_product_capability(tmp_path: Path) -> None:
    capabilities = _capabilities()
    capabilities.pop("candidate.decision_history")
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
        capabilities=capabilities,
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert (
        "required product capabilities missing from eval registry: "
        "candidate.decision_history"
    ) in errors


def test_capability_binding_requires_canonical_tool_name(tmp_path: Path) -> None:
    capabilities = _capabilities()
    actions = capabilities["candidate.action_history"]
    assert isinstance(actions, dict)
    bindings = actions["bindings"]
    assert isinstance(bindings, dict)
    bindings["agent_chat"] = ["search_applications"]
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
        capabilities=capabilities,
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert (
        "capability candidate.action_history: agent_chat is missing "
        "canonical tools: list_candidate_actions"
    ) in errors


def test_capability_binding_rejects_unapproved_endpoint_or_label(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities()
    pool_state = capabilities["candidate.pool_state"]
    assert isinstance(pool_state, dict)
    bindings = pool_state["bindings"]
    assert isinstance(bindings, dict)
    rest = bindings["rest"]
    assert isinstance(rest, list)
    rest.append("GET /api/v1/looks-grounded-but-is-not-tested")
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
        capabilities=capabilities,
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert any(
        "rest has unapproved bindings" in error
        and "looks-grounded-but-is-not-tested" in error
        for error in errors
    )


def test_frontend_capability_binding_must_exist_and_run_in_ci(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
    )
    frontend_test = (
        backend_root.parent
        / "frontend/src/shared/layout/GlobalSearch.test.jsx"
    )
    frontend_test.unlink()

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert any(
        "frontend binding does not exist: "
        "src/shared/layout/GlobalSearch.test.jsx" in error
        for error in errors
    )


def test_agent_features_must_reference_all_candidate_capabilities(
    tmp_path: Path,
) -> None:
    entry = _grounded_entry()
    entry["required_capabilities"] = ["candidate.pool_state"]
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"AGENT_CHAT": "agent_chat", "OTHER": "other"},
        entries={"agent_chat": entry},
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert any("agent_chat: required capabilities missing" in error for error in errors)


def test_feature_cannot_reference_an_unknown_capability(tmp_path: Path) -> None:
    entry = _covered_entry()
    entry["required_capabilities"] = ["candidate.imaginary_history"]
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": entry},
    )

    errors, _ = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert (
        "alpha: unknown required capabilities: candidate.imaginary_history"
    ) in errors


def test_capability_truth_cases_are_registered_as_exact_ci_targets(
    tmp_path: Path,
) -> None:
    backend_root, pricing_path, registry_path = _fixture(
        tmp_path,
        enum_members={"ALPHA": "alpha", "OTHER": "other"},
        entries={"alpha": _covered_entry()},
    )

    errors, targets = checker.validate_registry(
        backend_root=backend_root,
        pricing_path=pricing_path,
        registry_path=registry_path,
    )

    assert errors == []
    assert targets == ["tests/test_feature.py", *CAPABILITY_TARGETS]
