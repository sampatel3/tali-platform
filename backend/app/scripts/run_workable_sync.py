"""
Run a Workable sync for a given user email (foreground, for testing/debugging).

Usage (from backend/):
  python -m app.scripts.run_workable_sync sampatel@deeplight.ae

Uses the same sync logic as POST /workable/sync but runs in the current process
so you see all logs and the final summary. Does not set workable_sync_started_at
(no conflict with in-progress UI).
"""
from __future__ import annotations

import sys
from app.platform.database import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.run_workable_sync <user_email>", file=sys.stderr)
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
        if not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
            print(f"Workable not connected for org {org.name} (user {email})", file=sys.stderr)
            sys.exit(4)
        service = WorkableSyncService(
            WorkableService(
                access_token=org.workable_access_token,
                subdomain=org.workable_subdomain,
            )
        )
        print(f"Syncing Workable for org_id={org.id} ({org.name}), user {email}...")
        summary = service.sync_org(db, org)
        print("Summary:", summary)
    finally:
        db.close()


if __name__ == "__main__":
    main()
