"""Backfill: re-parse the active RoleIntent.free_text into role_criteria chips.

Why: before [PR follow-up], the chip parser was using a model setting that
resolved to a retired Anthropic alias, so every recruiter answer to
``intent_slot_missing`` / ``intent_clarification`` authored RoleIntent
free_text successfully but added zero chips. The fix lands forward; this
script backfills existing roles by re-running the (now-fixed) parser on
the free_text we already have on file.

Run from backend/:
  python scripts/backfill_intent_chips.py --role-id 31 --dry-run
  python scripts/backfill_intent_chips.py --role-id 31

Idempotent: the parser dedups against existing chip texts (case-insensitive),
so re-running on a role that already has chips just no-ops.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent_runtime.role_intent import fetch_active_intent
from app.models.org_criterion import BUCKET_MUST
from app.models.role import Role
from app.models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from app.platform.database import SessionLocal
from app.services.intent_chip_parser import parse_intent_text_to_chips


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role-id", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    with SessionLocal() as db:
        role = db.query(Role).filter(Role.id == args.role_id).one_or_none()
        if role is None:
            print(f"role {args.role_id} not found", file=sys.stderr)
            return 1
        intent = fetch_active_intent(db, role_id=int(role.id))
        if intent is None or not (intent.free_text or "").strip():
            print(f"role {args.role_id} has no active RoleIntent free_text — nothing to backfill")
            return 0

        existing_texts = [
            (c.text or "").strip()
            for c in (role.criteria or [])
            if c.deleted_at is None and (c.text or "").strip()
        ]
        print(f"role {role.id} ({role.name}): {len(existing_texts)} existing chips, "
              f"free_text {len(intent.free_text)} chars")
        chips = parse_intent_text_to_chips(
            db,
            organization_id=int(role.organization_id),
            role=role,
            answer_text=intent.free_text,
            agent_question=None,
            existing_chip_texts=existing_texts,
        )
        if not chips:
            print("parser returned 0 chips (likely all dedup'd or LLM call still failing)")
            return 0
        print(f"parser returned {len(chips)} chips:")
        for c in chips:
            print(f"  [{c.bucket}] {c.text}")
        if args.dry_run:
            print("--dry-run: not writing")
            return 0

        existing_ordering = [
            int(c.ordering)
            for c in (role.criteria or [])
            if c.deleted_at is None
        ]
        next_ordering = (max(existing_ordering) + 1) if existing_ordering else 0
        now = datetime.now(timezone.utc)
        for chip in chips:
            db.add(
                RoleCriterion(
                    role_id=int(role.id),
                    source=CRITERION_SOURCE_RECRUITER,
                    ordering=next_ordering,
                    weight=1.0,
                    must_have=(chip.bucket == BUCKET_MUST),
                    bucket=chip.bucket,
                    org_criterion_id=None,
                    customized_at=now,
                    text=chip.text,
                )
            )
            next_ordering += 1
        db.commit()
        print(f"wrote {len(chips)} chips to role {role.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
