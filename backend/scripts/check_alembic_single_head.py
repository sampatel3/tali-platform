#!/usr/bin/env python3
"""CI gate: the alembic migration graph must reduce to exactly one head.

Two PRs that each add a migration branched from the same parent leave
``main`` with multiple heads. ``alembic upgrade head`` — run on boot by
``app/scripts/railway_start.py`` — then refuses to choose between them and
exits non-zero, so the web service restart-loops and never serves. GitHub
reports such a pair as a CLEAN merge because the conflict is semantic, not
textual, so nothing catches it before deploy.

This check runs in CI (the ``backend`` job, which does NOT ``pip install``),
so it is deliberately **stdlib-only** — it parses the migration files
directly instead of importing alembic. Exits 0 on a single head, 1 otherwise.

Fix when it fails: add an empty merge revision whose ``down_revision`` is a
tuple of the reported heads, e.g.::

    revision = "NNN_merge_heads"
    down_revision = ("<head_a>", "<head_b>")
    def upgrade() -> None: pass
    def downgrade() -> None: pass
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

_REVISION_RE = re.compile(r'^revision\s*=\s*[\'"]([^\'"]+)[\'"]', re.M)
# Capture the down_revision value (str, None, or a possibly multi-line tuple)
# up to the next top-level assignment (branch_labels / depends_on / etc.).
_DOWN_RE = re.compile(r'^down_revision\s*=\s*(.*?)(?=^\w+\s*=)', re.M | re.S)
_QUOTED_RE = re.compile(r'[\'"]([^\'"]+)[\'"]')


def compute_heads() -> tuple[list[str], dict[str, list[str]], list[str]]:
    revisions: set[str] = set()
    edges: dict[str, list[str]] = {}
    revision_paths: dict[str, Path] = {}
    errors: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8")
        rev_m = _REVISION_RE.search(src)
        if not rev_m:
            continue
        rev = rev_m.group(1)
        if rev in revision_paths:
            errors.append(
                f"duplicate revision {rev!r} in "
                f"{revision_paths[rev].name!r} and {path.name!r}"
            )
            continue
        revisions.add(rev)
        revision_paths[rev] = path
        down_m = _DOWN_RE.search(src)
        edges[rev] = _QUOTED_RE.findall(down_m.group(1)) if down_m else []

    referenced = {parent for parents in edges.values() for parent in parents}
    heads = sorted(revisions - referenced)
    dangling = sorted(
        {p for parents in edges.values() for p in parents if p not in revisions}
    )
    if dangling:
        errors.append(
            "migrations reference unknown down_revision(s): " f"{dangling}"
        )
    return heads, edges, errors


def main() -> int:
    if not VERSIONS_DIR.is_dir():
        print(f"ERROR: versions dir not found: {VERSIONS_DIR}", file=sys.stderr)
        return 1
    heads, _, errors = compute_heads()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if len(heads) == 1:
        print(f"OK: single alembic head ({heads[0]})")
        return 0
    print(
        f"ERROR: alembic must resolve to exactly one head; found "
        f"{len(heads)}: {heads}\n"
        "Add a merge revision with these as its `down_revision` tuple.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
