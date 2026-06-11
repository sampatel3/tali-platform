"""Re-derive ``derived_from_spec`` role criteria with the hardened spec deriver.

One-off backfill to purge the junk the old line-splitter emitted — markdown
lead-ins ("**You will have experience in:**"), bare connectives ("and"),
boilerplate prose — and to retire the years-of-experience auto-must promotion.
It re-runs the SAME deriver the live Workable sync uses, so the backfilled
criteria match exactly what future syncs will produce.

Safe by default: dry-run unless ``--apply``. It NEVER wipes a role's derived
criteria when the spec would now yield none (guards a blank/odd job_spec_text).
Recruiter-added criteria (source != derived_from_spec) are never touched.

Usage (on the prod box, after the deriver fix has deployed):
    # dry-run — report what would change, write nothing:
    railway ssh resourceful-adaptation \
      "cd /app && PYTHONPATH=/app /opt/venv/bin/python scripts/rederive_role_criteria.py --org 2 --verbose"
    # apply:
    railway ssh resourceful-adaptation \
      "cd /app && PYTHONPATH=/app /opt/venv/bin/python scripts/rederive_role_criteria.py --org 2 --apply"
"""

from __future__ import annotations

import argparse
import os
import sys


def _resolve_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.normpath(os.path.join(here, "..", "backend"))
    if os.path.isdir(backend_dir) and backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-derive derived_from_spec role criteria with the hardened deriver."
    )
    parser.add_argument("--org", type=int, default=None, help="Limit to one organization id.")
    parser.add_argument(
        "--role-id", type=int, action="append", default=None,
        help="Limit to specific role id(s); repeatable.",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    parser.add_argument(
        "--verbose", action="store_true", help="List dropped/added criteria per role."
    )
    args = parser.parse_args()

    _resolve_paths()

    from app.models.role import Role
    from app.platform.database import SessionLocal
    from app.services.role_criteria_service import (
        CRITERION_SOURCE_DERIVED,
        sync_derived_criteria,
    )
    from app.services.spec_normalizer import derive_criteria, normalize_spec

    db = SessionLocal()
    roles_changed = 0
    roles_skipped_empty = 0
    total_before = 0
    total_after = 0
    total_dropped = 0
    must_before = 0
    must_after = 0
    try:
        q = db.query(Role)
        if args.org is not None:
            q = q.filter(Role.organization_id == args.org)
        if args.role_id:
            q = q.filter(Role.id.in_(args.role_id))
        roles = q.order_by(Role.id).all()

        for role in roles:
            existing = [
                c for c in (role.criteria or [])
                if c.source == CRITERION_SOURCE_DERIVED and c.deleted_at is None
            ]
            if not existing:
                continue  # no derived criteria to refresh

            spec = normalize_spec(role.job_spec_text)
            new_items = derive_criteria(spec.requirements)

            # Guard: never wipe a role's derived set on a blank/odd spec.
            if not new_items:
                roles_skipped_empty += 1
                print(
                    f"[SKIP] role {role.id} (org {role.organization_id}): spec now yields 0 "
                    f"criteria but {len(existing)} exist — not wiping "
                    f"(job_spec_text len={len(role.job_spec_text or '')})"
                )
                continue

            old_texts = [c.text for c in existing]
            new_texts = [i.text for i in new_items]
            old_set = {t.lower() for t in old_texts}
            new_set = {t.lower() for t in new_texts}
            dropped = [t for t in old_texts if t.lower() not in new_set]
            added = [t for t in new_texts if t.lower() not in old_set]
            old_must = sum(1 for c in existing if c.must_have)
            new_must = sum(1 for i in new_items if i.must_have)

            changed = bool(dropped or added or old_must != new_must)
            total_before += len(existing)
            total_after += len(new_items)
            total_dropped += len(dropped)
            must_before += old_must
            must_after += new_must

            if changed:
                roles_changed += 1
                tag = "APPLY" if args.apply else " DRY "
                title = (getattr(role, "title", None) or getattr(role, "name", "") or "")[:42]
                print(
                    f"[{tag}] role {role.id} (org {role.organization_id}) '{title}': "
                    f"{len(existing)} -> {len(new_items)} criteria "
                    f"(dropped {len(dropped)}, added {len(added)}, must {old_must} -> {new_must})"
                )
                if args.verbose:
                    for t in dropped:
                        print(f"         - DROP: {t[:90]}")
                    for t in added:
                        print(f"         + ADD : {t[:90]}")

            if args.apply and changed:
                sync_derived_criteria(db, role)
                db.commit()

        print("\n=== summary ===")
        print(f"roles changed:           {roles_changed}")
        print(f"roles skipped (no spec): {roles_skipped_empty}")
        print(f"criteria before -> after: {total_before} -> {total_after} (dropped {total_dropped})")
        print(f"must-haves before -> after: {must_before} -> {must_after}")
        print(f"mode: {'APPLIED (written)' if args.apply else 'DRY-RUN (no writes)'}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
