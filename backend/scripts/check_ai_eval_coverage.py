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
SCHEMA_VERSION = "feature-ai-evals/v2"
FEATURE_ENUM = "app.services.pricing_service.Feature"
ALLOWED_RISKS = {"critical", "high", "medium", "low"}
ALLOWED_COVERAGE = {"grounded_truth", "behavioral", "contract"}
BEHAVIORAL_KEYS = {"risk", "coverage", "test_paths"}
GROUNDED_KEYS = {"risk", "coverage", "ground_truth_scope", "test_cases"}
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

    allowed_top_level = {"schema_version", "feature_enum", "features"}
    unknown_top_level = sorted(set(registry) - allowed_top_level)
    if unknown_top_level:
        errors.append(
            "registry has unknown top-level keys: " + ", ".join(unknown_top_level)
        )
    if registry.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"registry schema_version must be {SCHEMA_VERSION!r}")
    if registry.get("feature_enum") != FEATURE_ENUM:
        errors.append(f"registry feature_enum must be {FEATURE_ENUM!r}")

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

    test_targets: list[str] = []
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
            allowed_keys = required_keys = GROUNDED_KEYS
        else:
            allowed_keys = BEHAVIORAL_KEYS | {"reviewed_semantic_exemption"}
            required_keys = BEHAVIORAL_KEYS
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
