#!/usr/bin/env python3
"""Drift detection: does the model still match reality?

For every container/component in model.yaml that declares an `implementation` block,
verify the mapped code paths actually exist in the right repo. This is what makes the
North Star self-maintaining rather than rot-prone (see DESIGN.md §4).

Repo locations are resolved via each repo's `localPathEnv` env var. `tali-platform`
defaults to the current directory so this checkout is verifiable out of the box.
Repos with no resolvable checkout are reported "unverifiable" (skipped), NOT failed —
so the model can describe repos this checkout can't see.

Exit 0 = no drift (in verifiable repos), 1 = drift found. Requires PyYAML.

Flags:
    --strict   treat unverifiable repos as failures (use when all repos are checked out)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("error: PyYAML required. Install with: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "architecture" / "model" / "model.yaml"

# Fallback local paths when a repo's localPathEnv is unset. While this package is
# still embedded in tali-platform, default that repo to the dir containing north-star
# (i.e. the tali-platform root) so drift is verifiable regardless of the cwd. Once
# extracted to its own repo, set TALI_PLATFORM_PATH explicitly instead.
DEFAULT_PATHS = {"tali-platform": str(ROOT.parent)}


def resolve_repo_paths(repos: list[dict]) -> dict[str, Path | None]:
    out: dict[str, Path | None] = {}
    for r in repos:
        rid = r["id"]
        env = r.get("localPathEnv")
        val = os.environ.get(env) if env else None
        if not val:
            val = DEFAULT_PATHS.get(rid)
        out[rid] = Path(val).resolve() if val else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="treat unverifiable repos as failures")
    args = ap.parse_args()

    if not MODEL.exists():
        print(f"error: {MODEL} not found")
        return 1
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    repo_paths = resolve_repo_paths(model["repos"])

    drift: list[str] = []
    unverifiable: list[str] = []
    verified = 0

    for item in model.get("containers", []) + model.get("components", []):
        impl = item.get("implementation")
        if not impl:
            continue
        repo = impl["repo"]
        base = repo_paths.get(repo)
        for rel in impl.get("paths", []):
            ref = f"{item['id']} -> {repo}:{rel}"
            if base is None:
                unverifiable.append(ref)
                continue
            if (base / rel).exists():
                verified += 1
                print(f"  ok          {ref}")
            else:
                drift.append(ref)
                print(f"  DRIFT       {ref}  (not found under {base})")

    if unverifiable:
        print("\nunverifiable (repo not checked out; set its localPathEnv to verify):")
        for u in unverifiable:
            print(f"  - {u}")

    failed = bool(drift) or (args.strict and bool(unverifiable))
    print(f"\nsummary: {verified} verified, {len(drift)} drifted, "
          f"{len(unverifiable)} unverifiable.")
    if drift:
        print("DRIFT DETECTED: the model claims code that does not exist. "
              "Update model.yaml or fix the code, in the same PR.")
    if args.strict and unverifiable:
        print("STRICT: unverifiable mappings are treated as failures.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
