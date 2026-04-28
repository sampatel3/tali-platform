"""Backfill CLI for the candidate knowledge graph (Graphiti).

Usage:

    python -m app.candidate_graph.backfill --org 42
    python -m app.candidate_graph.backfill --all-orgs

Idempotent. Each candidate produces N episodes (profile + skills/edu +
one per experience entry, capped by GRAPHITI_MAX_EPISODES_PER_CANDIDATE)
plus a CV-text episode. Each interview produces 1-2 episodes (transcript
plus structured summary). Each non-trivial pipeline event produces 1.

LLM cost budget at the defaults:
- ~$0.005 per profile episode (Anthropic Haiku 4.5 extraction)
- ~$0.0001 per Voyage embedding call (1024-dim, voyage-3)
- Typical org of 200 candidates with 1 interview each: ~$3-8 total.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import client as graph_client
from . import sync as sync_module

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
        logger.error(
            "Graphiti not configured (need NEO4J_URI and VOYAGE_API_KEY); aborting."
        )
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
        graph_client.close()

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
