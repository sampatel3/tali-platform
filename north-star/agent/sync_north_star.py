#!/usr/bin/env python3
"""Sync the North Star managed block into each target repo's CLAUDE.md.

The North Star reaches every repo as a delimited, generated block inside that repo's
CLAUDE.md (see ADR-0007). The block is ROLE-AWARE: the substrate, a brand, and the
legacy repo have different obligations at the architectural boundary, so each gets a
tailored section appended to a shared core (see ADR-0008).

Usage:
    python agent/sync_north_star.py             # write/update blocks in all targets
    python agent/sync_north_star.py --check      # exit 1 if any block is missing/stale
    python agent/sync_north_star.py --print [role]  # print a role's block, write nothing
                                                 # role in {substrate,brand,legacy}; default substrate

Dependency-free (stdlib only) so it runs anywhere, including CI, with no install.
Config: agent/sync.config.json (targets [{repo, role, claudeMd}] + northStarRef).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BEGIN = "<!-- NORTH-STAR:BEGIN -->"
END = "<!-- NORTH-STAR:END -->"

ROOT = Path(__file__).resolve().parent.parent  # north-star repo root
CONFIG = ROOT / "agent" / "sync.config.json"
TEMPLATE = ROOT / "agent" / "NORTH_STAR.block.md"
BLOCKS_DIR = ROOT / "agent" / "blocks"

VALID_ROLES = ("substrate", "brand", "legacy")

# One-line role descriptor shown near the top of the block.
ROLE_LINES = {
    "substrate": "You own the shared, cross-cutting capabilities; keep yourself brand-agnostic.",
    "brand": "You own your surface and data; build on the substrate, never fork it.",
    "legacy": "You are being drained into the substrate/brand model; don't grow new architecture here.",
}

# Source files whose content defines the North Star. The digest over these tells a
# consuming repo whether its block is stale relative to the source of truth.
DIGEST_SOURCES = [
    ROOT / "NORTH_STAR.md",
    ROOT / "architecture" / "model" / "model.yaml",
    TEMPLATE,
    *sorted(BLOCKS_DIR.glob("*.md")),
    *sorted((ROOT / "architecture" / "decisions").glob("*.md")),
]


def compute_digest() -> str:
    h = hashlib.sha256()
    for p in DIGEST_SOURCES:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def role_section(role: str) -> str:
    block_file = BLOCKS_DIR / f"{role}.md"
    if not block_file.exists():
        sys.exit(f"error: no role block for '{role}' at {block_file}")
    return block_file.read_text(encoding="utf-8").rstrip("\n")


def render_block(role: str, north_star_ref: str) -> str:
    if role not in VALID_ROLES:
        sys.exit(f"error: role '{role}' not in {VALID_ROLES}")
    body = TEMPLATE.read_text(encoding="utf-8").rstrip("\n")
    body = body.replace("{{NORTH_STAR_REF}}", north_star_ref)
    body = body.replace("{{ROLE}}", role)
    body = body.replace("{{ROLE_LINE}}", ROLE_LINES[role])
    body = body.replace("{{ROLE_SECTION}}", role_section(role))
    body = body.replace("{{DIGEST}}", compute_digest())
    return f"{BEGIN}\n{body}\n{END}\n"


def splice(existing: str, block: str) -> str:
    """Return CLAUDE.md content with the managed block inserted or replaced."""
    if BEGIN in existing and END in existing:
        pre = existing[: existing.index(BEGIN)]
        post = existing[existing.index(END) + len(END):]
        return f"{pre.rstrip()}\n\n{block}{post.lstrip(chr(10))}" if pre.strip() else f"{block}{post.lstrip(chr(10))}"
    # No block yet: append to the end, preserving the repo's own content.
    sep = "" if existing == "" else existing.rstrip("\n") + "\n\n"
    return f"{sep}{block}"


def current_block(text: str) -> str | None:
    if BEGIN in text and END in text:
        return text[text.index(BEGIN): text.index(END) + len(END)] + "\n"
    return None


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit(f"error: config not found at {CONFIG}")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (ROOT / p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync North Star block into target CLAUDE.md files.")
    ap.add_argument("--check", action="store_true", help="verify blocks are present and current; do not write")
    ap.add_argument("--print", dest="do_print", nargs="?", const="substrate", default=None,
                    metavar="ROLE", help="print a role's block and exit (default: substrate)")
    args = ap.parse_args()

    cfg = load_config()
    ref = cfg.get("northStarRef", "the north-star repo")

    if args.do_print is not None:
        print(render_block(args.do_print, ref), end="")
        return 0

    targets = cfg.get("targets", [])
    if not targets:
        print("no targets configured in sync.config.json")
        return 0

    stale, written, missing = [], [], []
    for t in targets:
        role = t.get("role", "brand")
        block = render_block(role, ref)
        claude_md = resolve(t["claudeMd"])
        label = f"{t.get('repo', '?')} [{role}] ({t['claudeMd']})"

        if not claude_md.parent.exists():
            missing.append(label)
            continue

        existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
        if current_block(existing) == block:
            print(f"  ok    {label}")
            continue

        if args.check:
            stale.append(label)
            print(f"  STALE {label}")
            continue

        claude_md.write_text(splice(existing, block), encoding="utf-8")
        written.append(label)
        print(f"  wrote {label}")

    if missing:
        print("\nrepos not found locally (skipped):")
        for m in missing:
            print(f"  - {m}")

    if args.check and stale:
        print(f"\n{len(stale)} block(s) missing or stale. Run without --check to update.")
        return 1
    if written:
        print(f"\nupdated {len(written)} block(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
