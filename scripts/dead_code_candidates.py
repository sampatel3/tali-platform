#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set


@dataclass
class ModuleInfo:
    module: str
    path: Path
    imports: Set[str]


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


def resolve_relative_import(current_module: str, level: int, module: str | None) -> str | None:
    if level <= 0:
        return module
    current_parts = current_module.split(".")
    package_parts = current_parts[:-1]
    up = max(level - 1, 0)
    if up > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - up]
    suffix = module.split(".") if module else []
    resolved = ".".join(base + suffix)
    return resolved or None


def extract_imports(path: Path, current_module: str, known_modules: Set[str]) -> Set[str]:
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content)
    imports: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app"):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = resolve_relative_import(current_module, node.level, node.module)
            if not resolved:
                continue
            if resolved.startswith("app"):
                imports.add(resolved)
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{resolved}.{alias.name}"
                if candidate in known_modules:
                    imports.add(candidate)

    return imports


def build_graph(app_root: Path) -> Dict[str, ModuleInfo]:
    files = list(all_python_files(app_root))
    known_modules = {module_name_from_path(app_root, p) for p in files}
    graph: Dict[str, ModuleInfo] = {}
    for path in files:
        module = module_name_from_path(app_root, path)
        graph[module] = ModuleInfo(
            module=module,
            path=path,
            imports=extract_imports(path, module, known_modules),
        )
    return graph


def is_candidate(module: str) -> bool:
    excluded_prefixes = (
        "app.main",
        "app.deps",
        "app.platform",
        "app.models",
        "app.schemas",
        "app.migrations",
        "app.tasks",
    )
    if module == "app":
        return False
    return not module.startswith(excluded_prefixes)


def compute_candidates(graph: Dict[str, ModuleInfo]) -> List[dict]:
    reverse_count: Dict[str, int] = {m: 0 for m in graph}
    for module, info in graph.items():
        for imported in info.imports:
            if imported in reverse_count:
                reverse_count[imported] += 1

    candidates = []
    for module, info in sorted(graph.items()):
        if reverse_count[module] != 0:
            continue
        if info.path.name == "__init__.py":
            continue
        if not is_candidate(module):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate dead-code candidates from backend import graph")
    parser.add_argument("--backend-root", default="backend/app", help="Path to backend app package root")
    parser.add_argument("--output", default="dead_code_candidates.json", help="Output JSON path")
    args = parser.parse_args()

    app_root = Path(args.backend_root).resolve()
    if not app_root.exists():
        raise SystemExit(f"backend root not found: {app_root}")

    graph = build_graph(app_root)
    candidates = compute_candidates(graph)
    payload = {
        "backend_root": str(app_root),
        "module_count": len(graph),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {out} | modules={len(graph)} candidates={len(candidates)}")

    preview = candidates[:20]
    if preview:
        print("Top candidates:")
        for row in preview:
            print(f"- {row['module']} ({row['path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
