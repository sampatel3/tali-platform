"""
Workable QA Diagnostic - Run with sampatel@deeplight.ae to inspect API responses and DB state.

Usage (in container or from backend/ with PYTHONPATH=backend):
  python -m app.scripts.workable_qa_diagnostic sampatel@deeplight.ae

Prints:
- Jobs list structure and count
- First job details structure
- First job candidates structure and count
- DB roles count and details
- DB applications per role
"""
from __future__ import annotations

import json
import sys

from app.platform.database import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.role import Role
from app.models.candidate_application import CandidateApplication
from app.components.integrations.workable.service import WorkableService


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.workable_qa_diagnostic <user_email>", file=sys.stderr)
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
            print(f"Workable not connected for org {org.name}", file=sys.stderr)
            sys.exit(4)

        client = WorkableService(
            access_token=org.workable_access_token,
            subdomain=org.workable_subdomain,
        )

        print("=" * 60)
        print("1. LIST JOBS (GET /jobs)")
        print("=" * 60)
        jobs = client.list_open_jobs()
        print(f"Jobs count: {len(jobs)}")
        if jobs:
            j0 = jobs[0]
            print(f"First job keys: {list(j0.keys())}")
            print(f"First job shortcode: {j0.get('shortcode')}, id: {j0.get('id')}, title: {j0.get('title')}")
            if "details" in j0:
                d = j0["details"]
                print(f"  details keys: {list(d.keys()) if isinstance(d, dict) else type(d)}")
            print()

        print("=" * 60)
        print("2. JOB DETAILS (GET /jobs/:shortcode)")
        print("=" * 60)
        if jobs:
            shortcode = jobs[0].get("shortcode") or jobs[0].get("id")
            details = client.get_job_details(str(shortcode)) if shortcode else {}
            print(f"Response type: {type(details)}")
            print(f"Top-level keys: {list(details.keys()) if isinstance(details, dict) else 'N/A'}")
            if isinstance(details, dict):
                job_wrapped = details.get("job")
                if job_wrapped:
                    print(f"  'job' wrapper keys: {list(job_wrapped.keys())[:20]}")
                    det = job_wrapped.get("details")
                    if isinstance(det, dict):
                        print(f"  job.details keys: {list(det.keys())}")
                        for k in ("description", "full_description", "requirements", "benefits"):
                            v = det.get(k)
                            preview = (str(v)[:80] + "...") if v and len(str(v)) > 80 else str(v)
                            print(f"    {k}: {preview}")
                    else:
                        det = details.get("details")
                        print(f"  No 'job' wrapper. details keys: {list(det.keys()) if isinstance(det, dict) else det}")
            print()

        print("=" * 60)
        print("3. JOB CANDIDATES (GET /jobs/:shortcode/candidates)")
        print("=" * 60)
        if jobs:
            shortcode = jobs[0].get("shortcode") or jobs[0].get("id")
            candidates = client.list_job_candidates(str(shortcode), paginate=True, max_pages=2) if shortcode else []
            print(f"Candidates count: {len(candidates)}")
            if candidates:
                c0 = candidates[0]
                print(f"First candidate keys: {list(c0.keys())}")
                print(f"  id: {c0.get('id')}, email: {c0.get('email')}, stage: {c0.get('stage')}")
                print(f"  stage_name: {c0.get('stage_name')}, stage_kind: {c0.get('stage_kind')}")
            print()

        print("=" * 60)
        print("4. DB ROLES (after sync)")
        print("=" * 60)
        roles = db.query(Role).filter(
            Role.organization_id == org.id,
            Role.deleted_at.is_(None),
        ).order_by(Role.created_at.desc()).all()
        print(f"Roles count: {len(roles)}")
        for r in roles[:10]:
            app_count = db.query(CandidateApplication).filter(
                CandidateApplication.role_id == r.id,
                CandidateApplication.deleted_at.is_(None),
            ).count()
            print(f"  id={r.id} name={r.name[:40]} workable_job_id={r.workable_job_id} apps={app_count} "
                  f"desc_len={len(r.description or '')} job_spec_text_len={len(r.job_spec_text or '')}")
        print()

        print("=" * 60)
        print("5. DB APPLICATIONS per role")
        print("=" * 60)
        for r in roles[:5]:
            apps = db.query(CandidateApplication).filter(
                CandidateApplication.role_id == r.id,
                CandidateApplication.deleted_at.is_(None),
            ).limit(5).all()
            print(f"Role {r.id} ({r.name[:30]}): {len(apps)} apps shown")
            for a in apps[:2]:
                print(f"    app {a.id} candidate_id={a.candidate_id} source={a.source} stage={a.workable_stage}")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
