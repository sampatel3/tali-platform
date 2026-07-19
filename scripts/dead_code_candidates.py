#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set


DEFAULT_RUNTIME_ROOTS = frozenset(
    {
        # ASGI/API process.
        "app.main",
        # Celery workers and Beat load the package, whose eager imports are the
        # canonical task-registration inventory.
        "app.tasks",
        # Alembic imports the model package to populate SQLAlchemy metadata.
        "app.models",
    }
)

# These commands intentionally live outside ``app.scripts`` for historical
# package/API reasons. Keep this list exact: treating every file with a
# ``__main__`` guard as a root lets dead libraries opt themselves out of CI.
APPROVED_NON_SCRIPT_CLI_ROOTS = frozenset(
    {
        "app.candidate_graph.backfill",
        "app.cv_matching.calibrators.recalibrate",
        "app.cv_matching.evals.run_evals",
        "app.decision_policy.evals.run_evals",
    }
)

# Alembic's ``env.py`` lives outside the scanned ``app`` package, so its app
# imports cannot appear as graph edges. Model only the reviewed module it loads;
# nearby platform modules must still prove ordinary runtime reachability.
ALEMBIC_TOOLING_ROOTS: dict[str, str] = {
    "app.platform.alembic_autogenerate_policy": (
        "imported by backend/alembic/env.py to enforce migration diff policy"
    ),
}

# Public Python import paths can outlive their runtime callers. Keep this
# inventory exact and reason-bearing: each entry is a deliberately retained
# compatibility API with focused fail-closed/alias tests. A prefix exemption
# (for example every ``app.services.*`` module) would let unrelated dead code
# disappear from the CI report, so only these fully-qualified modules are
# eligible.
COMPATIBILITY_IMPORT_ROOTS: dict[str, str] = {
    "app.capabilities.bias_monitor_continuous": (
        "fail-closed import compatibility for a registry-unavailable capability"
    ),
    "app.capabilities.capability_auditor": (
        "fail-closed import compatibility for a registry-unavailable capability"
    ),
    "app.capabilities.causal_mode": (
        "fail-closed import compatibility for a registry-unavailable capability"
    ),
    "app.capabilities.portfolio_agent": (
        "fail-closed import compatibility for a registry-unavailable capability"
    ),
    "app.components.scoring.schemas": (
        "typed compatibility view over the canonical dictionary scoring payload"
    ),
    "app.components.integrations.anthropic_admin.service": (
        "read-only compatibility API for operator-managed Anthropic workspaces"
    ),
    "app.candidate_graph.health_contracts": (
        "public compatibility re-export after health contracts moved to the "
        "provider-independent platform package"
    ),
    "app.services.credit_ledger_service": (
        "fail-closed facade for the retired unsafe generic ledger writer"
    ),
    "app.services.related_role_ats_transition": (
        "public compatibility helpers for pre-receipt related-role ATS callers; "
        "retained with fail-closed scope and idempotency regression coverage"
    ),
    "app.sub_agents.intent_parser": (
        "provider-free facade preserving schemas without registering a sixth agent"
    ),
}


@dataclass
class ModuleInfo:
    module: str
    path: Path
    imports: Set[str]
    is_entrypoint: bool


def module_name_from_path(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    package = root.name
    if not parts:
        return package
    return ".".join([package] + parts)


def all_python_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def resolve_relative_import(
    current_module: str,
    level: int,
    module: str | None,
    *,
    current_is_package: bool = False,
) -> str | None:
    if level <= 0:
        return module
    current_parts = current_module.split(".")
    # ``module_name_from_path`` maps ``pkg/__init__.py`` to ``pkg``.  For an
    # ordinary module the import package is its parent, but for ``__init__``
    # the current module *is* the package.  Treating both alike used to resolve
    # ``from .routes import router`` in ``app/domains/foo/__init__.py`` as
    # ``app.domains.routes`` and produced a large set of false dead-code
    # candidates.
    package_parts = current_parts if current_is_package else current_parts[:-1]
    up = max(level - 1, 0)
    if up > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - up]
    suffix = module.split(".") if module else []
    resolved = ".".join(base + suffix)
    return resolved or None


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    values = (test.left, test.comparators[0])
    return any(isinstance(value, ast.Name) and value.id == "__name__" for value in values) and any(
        isinstance(value, ast.Constant) and value.value == "__main__" for value in values
    )


def _is_type_checking_test(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TYPE_CHECKING"
    ) or (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == "TYPE_CHECKING"
    )


def _approved_cli_module(module: str) -> bool:
    return module.startswith("app.scripts.") or module in APPROVED_NON_SCRIPT_CLI_ROOTS


class _RuntimeImportVisitor(ast.NodeVisitor):
    """Collect imports that can execute from a reviewed runtime mode.

    ``ast.walk`` used to count imports inside ``if False`` and
    ``if TYPE_CHECKING`` as live edges, which lets dead modules hide behind a
    type hint or deliberately unreachable block. Main-guard bodies count only
    for the exact approved CLI inventory.
    """

    def __init__(
        self,
        *,
        current_module: str,
        current_is_package: bool,
        known_modules: Set[str],
    ) -> None:
        self.current_module = current_module
        self.current_is_package = current_is_package
        self.known_modules = known_modules
        self.imports: Set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "app" or alias.name.startswith("app."):
                self.imports.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        resolved = resolve_relative_import(
            self.current_module,
            node.level,
            node.module,
            current_is_package=self.current_is_package,
        )
        if not resolved:
            return
        if resolved == "app" or resolved.startswith("app."):
            self.imports.add(resolved)
        for alias in node.names:
            if alias.name == "*":
                continue
            candidate = f"{resolved}.{alias.name}"
            if candidate in self.known_modules:
                self.imports.add(candidate)

    def visit_If(self, node: ast.If) -> None:
        if _is_main_guard(node):
            if _approved_cli_module(self.current_module):
                for statement in node.body:
                    self.visit(statement)
            for statement in node.orelse:
                self.visit(statement)
            return

        if _is_type_checking_test(node.test):
            for statement in node.orelse:
                self.visit(statement)
            return
        if (
            isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and _is_type_checking_test(node.test.operand)
        ):
            for statement in node.body:
                self.visit(statement)
            return
        if isinstance(node.test, ast.Constant):
            branch = node.body if bool(node.test.value) else node.orelse
            for statement in branch:
                self.visit(statement)
            return
        self.generic_visit(node)


def extract_module_metadata(
    path: Path,
    current_module: str,
    known_modules: Set[str],
) -> tuple[Set[str], bool]:
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content)
    visitor = _RuntimeImportVisitor(
        current_module=current_module,
        current_is_package=path.name == "__init__.py",
        known_modules=known_modules,
    )
    visitor.visit(tree)
    is_entrypoint = any(
        isinstance(node, ast.If) and _is_main_guard(node)
        for node in tree.body
    )
    return visitor.imports, is_entrypoint


def build_graph(app_root: Path) -> Dict[str, ModuleInfo]:
    files = list(all_python_files(app_root))
    known_modules = {module_name_from_path(app_root, p) for p in files}
    graph: Dict[str, ModuleInfo] = {}
    for path in files:
        module = module_name_from_path(app_root, path)
        imports, is_entrypoint = extract_module_metadata(path, module, known_modules)
        graph[module] = ModuleInfo(
            module=module,
            path=path,
            imports=imports,
            is_entrypoint=is_entrypoint,
        )
    return graph


def entrypoint_roots(graph: Dict[str, ModuleInfo]) -> Set[str]:
    roots = {module for module in DEFAULT_RUNTIME_ROOTS if module in graph}
    roots.update(module for module in ALEMBIC_TOOLING_ROOTS if module in graph)
    roots.update(
        module for module in COMPATIBILITY_IMPORT_ROOTS if module in graph
    )
    for module, info in graph.items():
        if not info.is_entrypoint:
            continue
        if module.startswith("app.scripts.") or module in APPROVED_NON_SCRIPT_CLI_ROOTS:
            roots.add(module)
    return roots


def compute_reachable_modules(
    graph: Dict[str, ModuleInfo],
    *,
    roots: Iterable[str] | None = None,
) -> Set[str]:
    """Return modules reachable from real process/command entry points.

    An in-degree-only scan misses dead cycles and unused packages whose
    ``__init__`` imports their own routes. Reachability also models Python's
    execution of parent package ``__init__`` modules before a child import.
    """

    pending = list(roots if roots is not None else entrypoint_roots(graph))
    reachable: Set[str] = set()
    while pending:
        module = pending.pop()
        if module in reachable or module not in graph:
            continue
        reachable.add(module)
        pending.extend(graph[module].imports)

        parts = module.split(".")
        for end in range(1, len(parts)):
            parent = ".".join(parts[:end])
            if parent in graph and parent not in reachable:
                pending.append(parent)
    return reachable


def compute_candidates(
    graph: Dict[str, ModuleInfo],
    *,
    roots: Iterable[str] | None = None,
) -> List[dict]:
    reverse_count: Dict[str, int] = {m: 0 for m in graph}
    for info in graph.values():
        for imported in info.imports:
            if imported in reverse_count:
                reverse_count[imported] += 1

    reachable = compute_reachable_modules(graph, roots=roots)
    candidates = []
    for module, info in sorted(graph.items()):
        if module in reachable:
            continue
        if info.path.name == "__init__.py" and not _package_init_has_runtime_content(
            info.path
        ):
            continue
        candidates.append(
            {
                "module": module,
                "path": str(info.path),
                "imported_by_count": reverse_count[module],
                "imports_count": len(info.imports),
            }
        )
    return candidates


def _package_init_has_runtime_content(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    statements = list(tree.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    return any(not isinstance(statement, ast.Pass) for statement in statements)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate dead-code candidates from backend import graph")
    parser.add_argument("--backend-root", default="backend/app", help="Path to backend app package root")
    parser.add_argument("--output", default="dead_code_candidates.json", help="Output JSON path")
    parser.add_argument(
        "--fail-on-candidates",
        action="store_true",
        help="Exit non-zero when the current import graph contains candidates.",
    )
    args = parser.parse_args()

    app_root = Path(args.backend_root).resolve()
    if not app_root.exists():
        raise SystemExit(f"backend root not found: {app_root}")

    graph = build_graph(app_root)
    roots = entrypoint_roots(graph)
    reachable = compute_reachable_modules(graph, roots=roots)
    candidates = compute_candidates(graph, roots=roots)
    payload = {
        "backend_root": str(app_root),
        "module_count": len(graph),
        "root_count": len(roots),
        "roots": sorted(roots),
        "reachable_module_count": len(reachable),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"wrote {out} | modules={len(graph)} roots={len(roots)} "
        f"reachable={len(reachable)} candidates={len(candidates)}"
    )

    preview = candidates[:20]
    if preview:
        print("Top candidates:")
        for row in preview:
            print(f"- {row['module']} ({row['path']})")
    return 1 if args.fail_on_candidates and candidates else 0


if __name__ == "__main__":
    raise SystemExit(main())
