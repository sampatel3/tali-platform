#!/usr/bin/env python3
"""Fail CI when a metered AI feature has no reviewed offline eval decision.

The gate is intentionally small and stdlib-only. It parses ``Feature`` from
``pricing_service.py`` rather than importing the application, then compares the
enum values with ``app/evals/registry.json``. A feature must declare risk,
coverage, and existing pytest targets, unless it is explicitly infrastructure
and records why semantic evaluation belongs elsewhere. ``grounded_truth`` is
not a label-only escape hatch: it requires an exact pytest node and a concise
statement of the truth boundary. Critical features without grounded truth must
carry an explicit reviewed semantic-gap rationale.

This gate never invokes a model or any external service.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICING_PATH = BACKEND_ROOT / "app/services/pricing_service.py"
DEFAULT_REGISTRY_PATH = BACKEND_ROOT / "app/evals/registry.json"
SCHEMA_VERSION = "feature-ai-evals/v4"
FEATURE_ENUM = "app.services.pricing_service.Feature"
ALLOWED_RISKS = {"critical", "high", "medium", "low"}
ALLOWED_COVERAGE = {"grounded_truth", "behavioral", "contract"}
ALLOWED_CAPABILITY_SURFACES = {
    "rest",
    "public_mcp",
    "taali_chat",
    "agent_chat",
    "autonomous_agent",
    "frontend",
}
REQUIRED_CAPABILITIES = {
    "candidate.pool_state": {
        "search_role_candidates",
        "get_role_candidate",
    },
    "candidate.action_history": {"list_candidate_actions"},
    "candidate.decision_history": {"list_recent_agent_decisions"},
}
_CANDIDATE_TOOL_SURFACES = {
    "public_mcp",
    "taali_chat",
    "agent_chat",
    "autonomous_agent",
}
EXPECTED_CAPABILITY_BINDINGS = {
    "candidate.pool_state": {
        "rest": {
            "GET /api/v1/roles/{role_id}/applications",
            "GET /api/v1/applications?role_ids={role_ids}",
            "GET /api/v1/applications/{application_id}?view_role_id={role_id}",
            "GET /api/v1/roles/{role_id}/pipeline",
            "GET /api/v1/analytics/reporting-summary?role_id={role_id}",
            "GET /api/v1/analytics/decisions-breakdown?role_id={role_id}",
        },
        **{
            surface: {"search_role_candidates", "get_role_candidate"}
            for surface in _CANDIDATE_TOOL_SURFACES
        },
        "frontend": {
            "src/shared/layout/GlobalSearch.test.jsx",
            "src/features/jobs/JobPipelinePage.test.jsx",
            "src/test/CandidateBackLink.test.jsx",
        },
    },
    "candidate.action_history": {
        "rest": {"GET /api/v1/applications/{application_id}/events"},
        **{
            surface: {"list_candidate_actions"}
            for surface in _CANDIDATE_TOOL_SURFACES
        },
        "frontend": {"src/test/CandidateBackLink.test.jsx"},
    },
    "candidate.decision_history": {
        "rest": {"GET /api/v1/agent-decisions?role_id={role_id}"},
        **{
            surface: {"list_recent_agent_decisions"}
            for surface in _CANDIDATE_TOOL_SURFACES
        },
        "frontend": {
            "src/test/CandidateBackLink.test.jsx",
            "src/features/candidates/DecisionRail.test.jsx",
        },
    },
}
CAPABILITY_REQUIRED_FEATURES = {
    "taali_chat",
    "agent_chat",
    "agent_autonomous",
}
CAPABILITY_KEYS = {
    "risk",
    "ground_truth_scope",
    "required_surfaces",
    "bindings",
    "ordinary_role_test_case",
    "related_role_test_case",
    "regression_test_cases",
}
CAPABILITY_REQUIRED_KEYS = set(CAPABILITY_KEYS)
BEHAVIORAL_KEYS = {"risk", "coverage", "test_paths", "required_capabilities"}
BEHAVIORAL_REQUIRED_KEYS = {"risk", "coverage", "test_paths"}
GROUNDED_KEYS = {
    "risk",
    "coverage",
    "ground_truth_scope",
    "test_cases",
    "required_capabilities",
}
GROUNDED_REQUIRED_KEYS = {
    "risk",
    "coverage",
    "ground_truth_scope",
    "test_cases",
}
EXEMPT_KEYS = {"risk", "infrastructure_exemption"}


class DuplicateKeyError(ValueError):
    """Raised when JSON would otherwise silently discard a duplicate key."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, json.JSONDecodeError, DuplicateKeyError) as exc:
        return None, [f"cannot read registry {path}: {exc}"]
    if not isinstance(value, dict):
        return None, [f"registry {path} must contain a JSON object"]
    return value, []


def _feature_values(path: Path) -> tuple[dict[str, str], list[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return {}, [f"cannot parse Feature enum from {path}: {exc}"]

    feature_class = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Feature"
        ),
        None,
    )
    if feature_class is None:
        return {}, [f"{path} does not define class Feature"]

    members: dict[str, str] = {}
    errors: list[str] = []
    for statement in feature_class.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id.startswith("_"):
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            errors.append(f"Feature.{target.id} must have a literal string value")
            continue
        members[target.id] = value.value

    if not members:
        errors.append(f"{path} has no literal string Feature members")
    duplicate_values = sorted(
        value
        for value in set(members.values())
        if list(members.values()).count(value) > 1
    )
    if duplicate_values:
        errors.append(f"Feature has duplicate values: {', '.join(duplicate_values)}")
    return members, errors


def _has_pytest_test(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def _has_pytest_case(path: Path, node_parts: list[str]) -> bool:
    """Return whether an exact, unparameterized pytest node exists."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False

    body: list[ast.stmt] = tree.body
    for index, name in enumerate(node_parts):
        matching = next(
            (
                node
                for node in body
                if isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                )
                and node.name == name
            ),
            None,
        )
        if matching is None:
            return False
        if index == len(node_parts) - 1:
            return isinstance(matching, (ast.FunctionDef, ast.AsyncFunctionDef))
        if not isinstance(matching, ast.ClassDef):
            return False
        body = matching.body
    return False


def _validate_test_path(raw: Any, backend_root: Path, feature: str) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return [f"{feature}: test_paths entries must be non-empty strings"]
    if raw != raw.strip() or "\\" in raw:
        return [f"{feature}: test path must be normalized POSIX text: {raw!r}"]

    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return [f"{feature}: test path must stay inside backend/: {raw!r}"]
    if not relative.parts or relative.parts[0] != "tests" or relative.suffix != ".py":
        return [f"{feature}: test path must be a Python file under tests/: {raw!r}"]

    path = backend_root.joinpath(*relative.parts)
    if not path.is_file():
        return [f"{feature}: registered test does not exist: {raw}"]
    if not _has_pytest_test(path):
        return [f"{feature}: registered file has no pytest test functions: {raw}"]
    return []


def _validate_test_case(raw: Any, backend_root: Path, feature: str) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return [f"{feature}: test_cases entries must be non-empty strings"]
    if raw != raw.strip() or raw.count("::") < 1:
        return [
            f"{feature}: grounded truth must name an exact pytest node: {raw!r}"
        ]

    path_text, *node_parts = raw.split("::")
    errors = _validate_test_path(path_text, backend_root, feature)
    if errors:
        return errors
    if (
        any(not part or "[" in part or "]" in part for part in node_parts)
        or not node_parts[-1].startswith("test_")
    ):
        return [
            f"{feature}: grounded truth must use an unparameterized pytest node: "
            f"{raw!r}"
        ]

    path = backend_root.joinpath(*PurePosixPath(path_text).parts)
    if not _has_pytest_case(path, node_parts):
        return [f"{feature}: registered pytest case does not exist: {raw}"]
    return []


def _validate_capabilities(
    raw: Any,
    *,
    backend_root: Path,
) -> tuple[list[str], list[str], set[str]]:
    """Validate cross-surface product capabilities and their truth matrices."""

    if not isinstance(raw, dict):
        return ["registry capabilities must be an object"], [], set()

    errors: list[str] = []
    test_targets: list[str] = []
    names = set(raw)
    missing = sorted(set(REQUIRED_CAPABILITIES) - names)
    if missing:
        errors.append(
            "required product capabilities missing from eval registry: "
            + ", ".join(missing)
        )

    for capability in sorted(names):
        entry = raw[capability]
        label = f"capability {capability}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: registry entry must be an object")
            continue

        unknown_keys = sorted(set(entry) - CAPABILITY_KEYS)
        missing_keys = sorted(CAPABILITY_REQUIRED_KEYS - set(entry))
        if unknown_keys:
            errors.append(f"{label}: unknown fields: {', '.join(unknown_keys)}")
        if missing_keys:
            errors.append(f"{label}: missing fields: {', '.join(missing_keys)}")

        if entry.get("risk") != "critical":
            errors.append(f"{label}: risk must be 'critical'")
        scope = entry.get("ground_truth_scope")
        if not isinstance(scope, str) or not scope.strip():
            errors.append(f"{label}: ground_truth_scope needs a boundary")

        surfaces = entry.get("required_surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            errors.append(f"{label}: required_surfaces must be a non-empty list")
            surface_names: set[str] = set()
        else:
            surface_names = {
                surface for surface in surfaces if isinstance(surface, str)
            }
            if len(surface_names) != len(surfaces):
                errors.append(
                    f"{label}: required_surfaces must contain unique strings"
                )
            missing_surfaces = sorted(ALLOWED_CAPABILITY_SURFACES - surface_names)
            unknown_surfaces = sorted(surface_names - ALLOWED_CAPABILITY_SURFACES)
            if missing_surfaces:
                errors.append(
                    f"{label}: required surfaces missing: "
                    + ", ".join(missing_surfaces)
                )
            if unknown_surfaces:
                errors.append(
                    f"{label}: unknown surfaces: " + ", ".join(unknown_surfaces)
                )

        bindings = entry.get("bindings")
        if not isinstance(bindings, dict):
            errors.append(f"{label}: bindings must be an object keyed by surface")
        else:
            binding_surfaces = set(bindings)
            missing_bindings = sorted(surface_names - binding_surfaces)
            unknown_bindings = sorted(binding_surfaces - surface_names)
            if missing_bindings:
                errors.append(
                    f"{label}: bindings missing required surfaces: "
                    + ", ".join(missing_bindings)
                )
            if unknown_bindings:
                errors.append(
                    f"{label}: bindings name undeclared surfaces: "
                    + ", ".join(unknown_bindings)
                )
            for surface in sorted(binding_surfaces & surface_names):
                values = bindings[surface]
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(values) != len(set(values))
                ):
                    errors.append(
                        f"{label}: {surface} bindings must be unique non-empty strings"
                    )

            expected_bindings = EXPECTED_CAPABILITY_BINDINGS.get(capability)
            if expected_bindings is not None:
                for surface in sorted(surface_names & set(expected_bindings)):
                    values = bindings.get(surface)
                    if not isinstance(values, list):
                        continue
                    actual_values = set(values)
                    expected_values = expected_bindings[surface]
                    missing_values = sorted(expected_values - actual_values)
                    unknown_values = sorted(actual_values - expected_values)
                    if missing_values:
                        errors.append(
                            f"{label}: {surface} is missing approved bindings: "
                            + ", ".join(missing_values)
                        )
                    if unknown_values:
                        errors.append(
                            f"{label}: {surface} has unapproved bindings: "
                            + ", ".join(unknown_values)
                        )

            expected_tools = REQUIRED_CAPABILITIES.get(capability, set())
            for surface in sorted(_CANDIDATE_TOOL_SURFACES):
                values = bindings.get(surface)
                if not isinstance(values, list):
                    continue
                missing_tools = sorted(expected_tools - set(values))
                if missing_tools:
                    errors.append(
                        f"{label}: {surface} is missing canonical tools: "
                        + ", ".join(missing_tools)
                    )

            frontend_values = bindings.get("frontend")
            if isinstance(frontend_values, list):
                repository_root = backend_root.parent
                workflow_path = repository_root / ".github/workflows/ci.yml"
                try:
                    workflow_text = workflow_path.read_text(encoding="utf-8")
                except OSError as exc:
                    errors.append(
                        f"{label}: cannot read frontend CI workflow "
                        f"{workflow_path}: {exc}"
                    )
                    workflow_text = ""
                for raw_path in frontend_values:
                    if not isinstance(raw_path, str):
                        continue
                    relative = PurePosixPath(raw_path)
                    frontend_path = repository_root / "frontend" / relative
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or not raw_path.startswith("src/")
                        or ".test." not in relative.name
                    ):
                        errors.append(
                            f"{label}: frontend binding must be a normalized "
                            f"test path under frontend/src/: {raw_path!r}"
                        )
                    elif not frontend_path.is_file():
                        errors.append(
                            f"{label}: frontend binding does not exist: {raw_path}"
                        )
                    if raw_path not in workflow_text:
                        errors.append(
                            f"{label}: frontend binding is not executed by CI: "
                            f"{raw_path}"
                        )

        ordinary = entry.get("ordinary_role_test_case")
        related = entry.get("related_role_test_case")
        if isinstance(ordinary, str) and ordinary == related:
            errors.append(
                f"{label}: ordinary and related roles require distinct truth cases"
            )
        for role_kind, case in (("ordinary", ordinary), ("related", related)):
            errors.extend(
                _validate_test_case(case, backend_root, f"{label} {role_kind}")
            )
            if isinstance(case, str):
                test_targets.append(case)

        regressions = entry.get("regression_test_cases")
        if not isinstance(regressions, list) or not regressions:
            errors.append(
                f"{label}: regression_test_cases must be a non-empty list"
            )
            continue
        if len(regressions) != len(
            set(case for case in regressions if isinstance(case, str))
        ):
            errors.append(
                f"{label}: regression_test_cases must not contain duplicates"
            )
        for case in regressions:
            errors.extend(
                _validate_test_case(case, backend_root, f"{label} regression")
            )
            if isinstance(case, str):
                test_targets.append(case)

    return errors, test_targets, names


def validate_registry(
    *,
    backend_root: Path = BACKEND_ROOT,
    pricing_path: Path = DEFAULT_PRICING_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> tuple[list[str], list[str]]:
    """Return ``(violations, unique_test_targets)`` without importing the app."""

    members, errors = _feature_values(pricing_path)
    registry, registry_errors = _load_json_object(registry_path)
    errors.extend(registry_errors)
    if registry is None:
        return errors, []

    allowed_top_level = {
        "schema_version",
        "feature_enum",
        "features",
        "capabilities",
    }
    unknown_top_level = sorted(set(registry) - allowed_top_level)
    if unknown_top_level:
        errors.append(
            "registry has unknown top-level keys: " + ", ".join(unknown_top_level)
        )
    if registry.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"registry schema_version must be {SCHEMA_VERSION!r}")
    if registry.get("feature_enum") != FEATURE_ENUM:
        errors.append(f"registry feature_enum must be {FEATURE_ENUM!r}")

    capability_errors, capability_targets, capability_names = _validate_capabilities(
        registry.get("capabilities"), backend_root=backend_root
    )
    errors.extend(capability_errors)

    entries = registry.get("features")
    if not isinstance(entries, dict):
        errors.append("registry features must be an object keyed by Feature values")
        return errors, []

    expected = {value for name, value in members.items() if name != "OTHER"}
    excluded = {value for name, value in members.items() if name == "OTHER"}
    actual = set(entries)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        errors.append(
            "Feature values missing from eval registry: " + ", ".join(missing)
        )
    if unknown:
        label = (
            "OTHER must not be registered"
            if set(unknown) <= excluded
            else "unknown registry features"
        )
        errors.append(f"{label}: {', '.join(unknown)}")

    test_targets: list[str] = list(capability_targets)
    for feature in sorted(actual & expected):
        entry = entries[feature]
        if not isinstance(entry, dict):
            errors.append(f"{feature}: registry entry must be an object")
            continue

        risk = entry.get("risk")
        if risk not in ALLOWED_RISKS:
            errors.append(
                f"{feature}: risk must be one of {', '.join(sorted(ALLOWED_RISKS))}"
            )

        exempt = "infrastructure_exemption" in entry
        coverage = entry.get("coverage")
        grounded = coverage == "grounded_truth"
        if exempt:
            allowed_keys = required_keys = EXEMPT_KEYS
        elif grounded:
            allowed_keys = GROUNDED_KEYS
            required_keys = GROUNDED_REQUIRED_KEYS
        else:
            allowed_keys = BEHAVIORAL_KEYS | {"reviewed_semantic_exemption"}
            required_keys = BEHAVIORAL_REQUIRED_KEYS
        unknown_keys = sorted(set(entry) - allowed_keys)
        missing_keys = sorted(required_keys - set(entry))
        if unknown_keys:
            errors.append(f"{feature}: unknown fields: {', '.join(unknown_keys)}")
        if missing_keys:
            errors.append(f"{feature}: missing fields: {', '.join(missing_keys)}")

        if exempt:
            reason = entry.get("infrastructure_exemption")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{feature}: infrastructure_exemption needs a reason")
            continue

        required_capabilities = entry.get("required_capabilities", [])
        if not isinstance(required_capabilities, list) or any(
            not isinstance(name, str) or not name.strip()
            for name in required_capabilities
        ):
            errors.append(
                f"{feature}: required_capabilities must be a list of names"
            )
            required_capability_names: set[str] = set()
        else:
            required_capability_names = set(required_capabilities)
            if len(required_capability_names) != len(required_capabilities):
                errors.append(
                    f"{feature}: required_capabilities must not contain duplicates"
                )
            unknown_capabilities = sorted(
                required_capability_names - capability_names
            )
            if unknown_capabilities:
                errors.append(
                    f"{feature}: unknown required capabilities: "
                    + ", ".join(unknown_capabilities)
                )
        if feature in CAPABILITY_REQUIRED_FEATURES:
            missing_capabilities = sorted(
                set(REQUIRED_CAPABILITIES) - required_capability_names
            )
            if missing_capabilities:
                errors.append(
                    f"{feature}: required capabilities missing: "
                    + ", ".join(missing_capabilities)
                )

        if coverage not in ALLOWED_COVERAGE:
            errors.append(
                f"{feature}: coverage must be one of "
                f"{', '.join(sorted(ALLOWED_COVERAGE))}"
            )
        if grounded:
            scope = entry.get("ground_truth_scope")
            if not isinstance(scope, str) or not scope.strip():
                errors.append(f"{feature}: ground_truth_scope needs a boundary")
            cases = entry.get("test_cases")
            if not isinstance(cases, list) or not cases:
                errors.append(f"{feature}: test_cases must be a non-empty list")
                continue
            if len(cases) != len(
                set(case for case in cases if isinstance(case, str))
            ):
                errors.append(f"{feature}: test_cases must not contain duplicates")
            for case in cases:
                errors.extend(_validate_test_case(case, backend_root, feature))
                if isinstance(case, str):
                    test_targets.append(case)
            continue

        semantic_exemption = entry.get("reviewed_semantic_exemption")
        if risk == "critical":
            if not isinstance(semantic_exemption, str) or not semantic_exemption.strip():
                errors.append(
                    f"{feature}: critical features require grounded_truth or a "
                    "reviewed_semantic_exemption"
                )
        elif semantic_exemption is not None:
            errors.append(
                f"{feature}: reviewed_semantic_exemption is only valid for "
                "critical features"
            )

        paths = entry.get("test_paths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"{feature}: test_paths must be a non-empty list")
            continue
        if len(paths) != len(set(path for path in paths if isinstance(path, str))):
            errors.append(f"{feature}: test_paths must not contain duplicates")
        for path in paths:
            errors.extend(_validate_test_path(path, backend_root, feature))
            if isinstance(path, str):
                test_targets.append(path)

    return errors, sorted(set(test_targets))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-tests",
        action="store_true",
        help="print unique registered offline pytest targets, one per line",
    )
    parser.add_argument("--backend-root", type=Path, default=BACKEND_ROOT)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    errors, tests = validate_registry(
        backend_root=args.backend_root.resolve(),
        pricing_path=args.pricing.resolve(),
        registry_path=args.registry.resolve(),
    )
    if errors:
        print("AI eval coverage gate failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    if args.list_tests:
        print("\n".join(tests))
    else:
        print(
            f"AI eval coverage gate passed: {len(tests)} "
            "offline pytest targets registered"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
