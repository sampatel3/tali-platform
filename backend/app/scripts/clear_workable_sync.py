"""
Clear Workable sync state for a user's org (stuck sync).

Usage (from backend/ with DATABASE_URL set, or via Railway):
  python -m app.scripts.clear_workable_sync sampatel@deeplight.ae
"""
from __future__ import annotations

import sys
from app.platform.database import SessionLocal
from app.models.user import User
from app.models.organization import Organization


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.clear_workable_sync <user_email>", file=sys.stderr)
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"User not found: {email}", file=sys.stderr)
            sys.exit(2)
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if not org:
            print(f"Organization not found for user {email}", file=sys.stderr)
            sys.exit(3)
        if org.workable_sync_started_at is None and org.workable_sync_progress is None:
            print(f"No sync in progress for {email} (org_id={org.id}). Already clear.")
            return
        db.query(Organization).filter(Organization.id == org.id).update(
            {
                Organization.workable_sync_started_at: None,
                Organization.workable_sync_progress: None,
                Organization.workable_sync_cancel_requested_at: None,
            },
            synchronize_session=False,
        )
        db.commit()
        print(f"Cleared Workable sync state for {email} (org_id={org.id}). They can start a new sync.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
