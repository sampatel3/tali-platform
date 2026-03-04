"""Export role/job specs for a user's organization without mutating any state.

Usage:
  PYTHONPATH=backend python -m app.scripts.export_role_specs sampatel@deeplight.ae
  PYTHONPATH=backend python -m app.scripts.export_role_specs sampatel@deeplight.ae --match genai --match glue
"""

from __future__ import annotations

import argparse
import json
import sys

from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.platform.database import SessionLocal


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export role specs for a user's organization")
    parser.add_argument("user_email", help="User email used to resolve the organization")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="Case-insensitive substring filter for role names. Repeat to match multiple roles.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    email = args.user_email.strip().lower()
    match_terms = [term.strip().lower() for term in args.match if term and term.strip()]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(json.dumps({"error": f"user_not_found:{email}"}))
            sys.exit(2)

        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if not org:
            print(json.dumps({"error": f"organization_not_found:{email}"}))
            sys.exit(3)

        query = db.query(Role).filter(Role.organization_id == org.id, Role.deleted_at.is_(None)).order_by(Role.id.asc())
        roles = query.all()
        if match_terms:
            roles = [
                role
                for role in roles
                if any(term in (role.name or "").lower() for term in match_terms)
            ]

        payload = {
            "user_email": email,
            "organization_id": org.id,
            "organization_name": org.name,
            "role_count": len(roles),
            "roles": [
                {
                    "id": role.id,
                    "name": role.name,
                    "source": role.source,
                    "workable_job_id": role.workable_job_id,
                    "job_spec_filename": role.job_spec_filename,
                    "job_spec_uploaded_at": role.job_spec_uploaded_at.isoformat() if role.job_spec_uploaded_at else None,
                    "job_spec_text": role.job_spec_text,
                }
                for role in roles
            ],
        }
        print(json.dumps(payload, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
