#!/usr/bin/env python3
"""Validate model.yaml integrity and ADR linkage.

Checks:
  - required top-level keys are present
  - ids are unique within repos / containers / components
  - every reference resolves: container.repo, component.container,
    implementation.repo, relationship source/destination
  - every ADR id referenced from the model resolves to a file in
    architecture/decisions/
  - every ADR file is listed in the ADR index (decisions/README.md)

Exit 0 = clean, 1 = problems found. Requires PyYAML.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("error: PyYAML required. Install with: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "architecture" / "model" / "model.yaml"
DECISIONS = ROOT / "architecture" / "decisions"

REQUIRED_TOP = ["metadata", "repos", "context", "containers", "components",
                "relationships", "boundaries", "invariants"]


def adr_files() -> dict[str, Path]:
    """Map 'ADR-0003' -> path for each NNNN-*.md in decisions/."""
    out: dict[str, Path] = {}
    for p in DECISIONS.glob("*.md"):
        m = re.match(r"(\d{4})-", p.name)
        if m:
            out[f"ADR-{m.group(1)}"] = p
    return out


def main() -> int:
    if not MODEL.exists():
        print(f"error: {MODEL} not found")
        return 1
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    errors: list[str] = []

    for key in REQUIRED_TOP:
        if key not in model:
            errors.append(f"missing top-level key: {key}")
    if errors:  # structure is too broken to continue meaningfully
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    repo_ids = {r["id"] for r in model["repos"]}
    container_ids = {c["id"] for c in model["containers"]}
    # nodes that relationships may reference
    people_ids = {p["id"] for p in model["context"].get("people", [])}
    ext_ids = {e["id"] for e in model["context"].get("externalSystems", [])}
    node_ids = container_ids | people_ids | ext_ids | {model["context"]["system"]["id"]}

    def check_unique(items, label):
        seen = set()
        for it in items:
            i = it["id"]
            if i in seen:
                errors.append(f"duplicate {label} id: {i}")
            seen.add(i)

    check_unique(model["repos"], "repo")
    check_unique(model["containers"], "container")
    check_unique(model["components"], "component")

    for c in model["containers"]:
        if c.get("repo") not in repo_ids:
            errors.append(f"container {c['id']}: unknown repo '{c.get('repo')}'")

    for c in model["components"]:
        if c.get("container") not in container_ids:
            errors.append(f"component {c['id']}: unknown container '{c.get('container')}'")

    for item in model["containers"] + model["components"]:
        impl = item.get("implementation")
        if impl and impl.get("repo") not in repo_ids:
            errors.append(f"{item['id']}: implementation.repo '{impl.get('repo')}' is not a known repo")

    for r in model["relationships"]:
        for end in ("source", "destination"):
            if r.get(end) not in node_ids:
                errors.append(f"relationship {r}: unknown {end} '{r.get(end)}'")

    # ADR linkage
    adrs = adr_files()
    referenced: set[str] = set()
    for c in model["containers"] + model["components"]:
        if c.get("invariant"):
            referenced.add(c["invariant"])
    for b in model["boundaries"] + model["invariants"]:
        if b.get("adr"):
            referenced.add(b["adr"])
    for ref in sorted(referenced):
        if ref not in adrs:
            errors.append(f"model references {ref} but no decisions/{ref[4:]}-*.md exists")

    # ADR index freshness
    index = (DECISIONS / "README.md")
    if index.exists():
        index_text = index.read_text(encoding="utf-8")
        for ref, path in sorted(adrs.items()):
            num = ref[4:]
            if num not in index_text:
                errors.append(f"ADR {path.name} is not listed in the index (decisions/README.md)")

    if errors:
        print(f"model validation FAILED ({len(errors)} problem(s)):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print(f"model OK: {len(model['containers'])} containers, "
          f"{len(model['components'])} components, {len(adrs)} ADRs, all references resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
