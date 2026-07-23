"""Operator-driven org onboarding: create an organization + its first owner.

Onboarding is sales-led — public self-serve signup is off
(``ALLOW_PUBLIC_REGISTRATION=False``), so this script is the supported way a
new org comes into being. It mirrors what the old signup flow did (fresh org
with a unique slug + free-tier credit grant), plus:

  * creates the owner user directly (role=owner, pre-verified — no email
    verification loop, since an operator vouches for them), and
  * optionally attaches a hand-minted Anthropic workspace key.

On the workspace key: the Admin API has no create-key endpoint, so a per-org
key can only be minted by hand in the Anthropic Console. Pass BOTH the
workspace id (``wrkspc_...``) and the key (``sk-ant-...``) to attach one. The
key routes that org's billable calls; the workspace id is what lets nightly
reconciliation attribute Anthropic's usage report back to this org. Omit both
to run the org on the shared Taali key (billing still meters per-org from the
internal usage ledger — you just don't get independent per-org reconciliation).

Idempotent-ish: refuses if the owner email already exists or the resolved slug
is taken, so a re-run can't silently double-create.

Run (dry-run prints what it would do; ``--execute`` writes):

    railway run --service resourceful-adaptation \
        python scripts/create_org.py --name "Venquis" --owner-email sam@venquis.com
    railway run --service resourceful-adaptation \
        python scripts/create_org.py --name "Venquis" --owner-email sam@venquis.com \
            --workspace-id wrkspc_xxx --workspace-key sk-ant-xxx --execute
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from dataclasses import dataclass
from typing import Optional

from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.usage_grant import GRANT_FREE_TIER, UsageGrant
from app.models.user import User
from app.platform.config import settings
from app.platform.database import SessionLocal
from app.platform.secrets import encrypt_text
from app.platform.security import get_password_hash
from app.services.pricing_service import FREE_TIER


class CreateOrgError(Exception):
    """A precondition failed (duplicate email or slug)."""


@dataclass
class CreatedOrg:
    org: Organization
    owner: User
    temp_password: Optional[str]  # set only when we generated one


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "organization"


def _unique_slug(db, base: str) -> str:
    slug, suffix = base, 2
    while db.query(Organization.id).filter(Organization.slug == slug).first() is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def create_org(
    db,
    *,
    name: str,
    owner_email: str,
    owner_name: Optional[str] = None,
    slug: Optional[str] = None,
    password: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_key: Optional[str] = None,
) -> CreatedOrg:
    """Create an org + free-tier grant + owner user. Flushes but does NOT commit
    (the caller owns the transaction). Raises CreateOrgError on a duplicate
    owner email or an explicitly-requested slug that is taken.
    """
    email = owner_email.strip().lower()
    ws_id = (workspace_id or "").strip() or None
    ws_key = (workspace_key or "").strip() or None
    if bool(ws_id) != bool(ws_key):
        raise CreateOrgError(
            "workspace_id and workspace_key must be given together"
        )

    if db.query(User.id).filter(User.email == email).first() is not None:
        raise CreateOrgError(f"a user with email {email!r} already exists")

    base_slug = _slugify(slug or name)
    if slug is not None:
        if db.query(Organization.id).filter(
            Organization.slug == base_slug
        ).first() is not None:
            raise CreateOrgError(f"slug {base_slug!r} is already taken")
        resolved_slug = base_slug
    else:
        resolved_slug = _unique_slug(db, base_slug)

    generated = password is None
    pw = password or secrets.token_urlsafe(16)

    org = Organization(name=name, slug=resolved_slug, plan="pay_per_use")
    org.credits_balance = FREE_TIER.credits
    if ws_key:
        org.anthropic_workspace_id = ws_id
        org.anthropic_workspace_key_encrypted = encrypt_text(
            ws_key, settings.SECRET_KEY
        )
    db.add(org)
    db.flush()

    external_ref = f"free_tier:{org.id}"
    db.add(
        UsageGrant(
            organization_id=org.id,
            grant_type=GRANT_FREE_TIER,
            credits_granted=FREE_TIER.credits,
            external_ref=external_ref,
        )
    )
    db.add(
        BillingCreditLedger(
            organization_id=org.id,
            delta=FREE_TIER.credits,
            balance_after=FREE_TIER.credits,
            reason=f"grant:{GRANT_FREE_TIER}",
            external_ref=external_ref,
        )
    )

    owner = User(
        email=email,
        full_name=owner_name,
        hashed_password=get_password_hash(pw),
        organization_id=org.id,
        role="owner",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(owner)
    db.flush()

    return CreatedOrg(org=org, owner=owner, temp_password=pw if generated else None)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="organization display name")
    ap.add_argument("--owner-email", required=True, help="first owner's email")
    ap.add_argument("--owner-name", default=None, help="first owner's full name")
    ap.add_argument(
        "--slug",
        default=None,
        help="explicit slug (default: derived from --name, de-duped)",
    )
    ap.add_argument(
        "--password",
        default=None,
        help="owner password (default: a strong random one, printed once)",
    )
    ap.add_argument("--workspace-id", default=None, help="Anthropic wrkspc_... id")
    ap.add_argument("--workspace-key", default=None, help="Anthropic sk-ant-... key")
    ap.add_argument("--execute", action="store_true", help="write (default: dry-run)")
    args = ap.parse_args()

    ws_key = (args.workspace_key or "").strip() or None
    if not args.execute:
        print("DRY-RUN — no writes. Re-run with --execute to apply.")
        print(f"  org      name={args.name!r}  plan=pay_per_use")
        print(f"  owner    email={args.owner_email.strip().lower()!r}  role=owner")
        print(f"  credits  free-tier grant of {FREE_TIER.credits}")
        print(f"  workspace key attached: {'yes' if ws_key else 'no (shared key)'}")
        return

    db = SessionLocal()
    try:
        try:
            result = create_org(
                db,
                name=args.name,
                owner_email=args.owner_email,
                owner_name=args.owner_name,
                slug=args.slug,
                password=args.password,
                workspace_id=args.workspace_id,
                workspace_key=args.workspace_key,
            )
        except CreateOrgError as exc:
            print(f"REFUSING: {exc}.")
            sys.exit(1)
        db.commit()

        print(f"Done. org_id={result.org.id}  slug={result.org.slug}")
        if result.temp_password is not None:
            print(
                "Temporary owner password (share securely, ask them to reset): "
                f"{result.temp_password}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
