"""
Re-format job_spec_text and description for roles that have workable_job_data.
Fixes raw dict repr (Location: {'country': ...}) and HTML in descriptions.

Usage:
  # Deploy: cd backend && railway up
  # Then fix existing job specs:
  railway run bash -c 'cd backend && .venv/bin/python -m app.scripts.reformat_job_specs --email sampatel@deeplight.ae'

  # Or with DATABASE_URL set: cd backend && python -m app.scripts.reformat_job_specs [--email ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

from app.platform.database import SessionLocal
from app.models.role import Role
from app.models.user import User
from app.models.organization import Organization
from app.components.integrations.workable.sync_service import _format_job_spec_from_api


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-format job specs from workable_job_data")
    parser.add_argument("--email", help="Limit to org of this user (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Don't commit changes")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Role).filter(Role.deleted_at.is_(None))
        if args.email:
            user = db.query(User).filter(User.email == args.email.strip().lower()).first()
            if not user:
                print(f"User not found: {args.email}", file=sys.stderr)
                return 2
            query = query.filter(Role.organization_id == user.organization_id)

        roles = query.all()
        updated = 0
        for role in roles:
            job_data = role.workable_job_data
            if not job_data or not isinstance(job_data, dict):
                continue
            formatted = _format_job_spec_from_api(job_data)
            if not formatted.strip():
                continue
            if formatted.strip() != (role.job_spec_text or "").strip():
                role.job_spec_text = formatted
                role.description = formatted
                updated += 1
                print(f"Updated role id={role.id} name={role.name!r}")

        if not args.dry_run and updated:
            db.commit()
            print(f"Committed {updated} role(s)")
        elif args.dry_run and updated:
            print(f"[DRY RUN] Would update {updated} role(s)")
        else:
            print("No roles needed updating")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
