"""Backfill CLI for the candidate knowledge graph.

Usage:

    python -m app.candidate_graph.backfill --org 42
    python -m app.candidate_graph.backfill --all-orgs

Idempotent. Safe to re-run after schema bumps. Skips candidates with no
experience/education/skills (they produce no graph). Aborts cleanly when
``NEO4J_URI`` is unset.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import sync as sync_module
from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.backfill")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--org", type=int, help="Organization id to backfill")
    group.add_argument(
        "--all-orgs",
        action="store_true",
        help="Backfill every organization with at least one candidate",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not graph_client.is_configured():
        logger.error("Neo4j is not configured (NEO4J_URI is empty); aborting.")
        return 2

    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        if args.all_orgs:
            result = sync_module.sync_all_organizations(db)
        else:
            result = sync_module.sync_organization(db, int(args.org))
    finally:
        db.close()

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
