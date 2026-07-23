"""scripts/create_org.py — the sales-led org onboarding path.

Covers the core (create_org): fresh org + free-tier grant + owner user,
duplicate-email refusal, slug de-duplication, and workspace-key attachment.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from app.models.organization import Organization
from app.models.usage_grant import UsageGrant
from app.models.user import User
from app.platform.config import settings
from app.platform.secrets import decrypt_text
from app.platform.security import verify_password

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_org.py"
_spec = importlib.util.spec_from_file_location("create_org", _SCRIPT)
create_org_mod = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass introspection can resolve the module.
sys.modules[_spec.name] = create_org_mod
_spec.loader.exec_module(create_org_mod)
create_org = create_org_mod.create_org
CreateOrgError = create_org_mod.CreateOrgError


def test_creates_org_owner_and_grant(db):
    result = create_org(
        db, name="Venquis Ltd", owner_email="Sam@Venquis.com", owner_name="Sam"
    )
    db.commit()

    org = db.query(Organization).filter_by(id=result.org.id).one()
    assert org.name == "Venquis Ltd"
    assert org.slug == "venquis-ltd"
    assert org.plan == "pay_per_use"
    assert org.credits_balance > 0

    owner = db.query(User).filter_by(organization_id=org.id).one()
    assert owner.email == "sam@venquis.com"  # normalized
    assert owner.role == "owner"
    assert owner.is_active and owner.is_verified and not owner.is_superuser

    grant = db.query(UsageGrant).filter_by(organization_id=org.id).one()
    assert grant.credits_granted == org.credits_balance

    # A temp password was generated and it actually verifies against the hash.
    assert result.temp_password
    assert verify_password(result.temp_password, owner.hashed_password)


def test_explicit_password_is_not_echoed(db):
    result = create_org(
        db, name="Acme", owner_email="a@acme.com", password="a-real-strong-pass"
    )
    assert result.temp_password is None
    assert verify_password("a-real-strong-pass", result.owner.hashed_password)


def test_duplicate_owner_email_refused(db):
    create_org(db, name="Org One", owner_email="dup@x.com")
    db.commit()
    with pytest.raises(CreateOrgError, match="already exists"):
        create_org(db, name="Org Two", owner_email="dup@x.com")


def test_slug_is_deduplicated(db):
    a = create_org(db, name="Globex", owner_email="one@globex.com")
    db.commit()
    b = create_org(db, name="Globex", owner_email="two@globex.com")
    db.commit()
    assert a.org.slug == "globex"
    assert b.org.slug == "globex-2"


def test_explicit_taken_slug_refused(db):
    create_org(db, name="First", owner_email="one@f.com", slug="shared")
    db.commit()
    with pytest.raises(CreateOrgError, match="already taken"):
        create_org(db, name="Second", owner_email="two@f.com", slug="shared")


def test_workspace_key_stored_encrypted(db):
    result = create_org(
        db,
        name="Enterprise Co",
        owner_email="o@ent.com",
        workspace_id="wrkspc_abc123",
        workspace_key="sk-ant-secret-value",
    )
    db.commit()
    org = db.query(Organization).filter_by(id=result.org.id).one()
    assert org.anthropic_workspace_id == "wrkspc_abc123"
    # Stored ciphertext, not the raw key; decrypts back to the original.
    assert org.anthropic_workspace_key_encrypted != "sk-ant-secret-value"
    assert (
        decrypt_text(org.anthropic_workspace_key_encrypted, settings.SECRET_KEY)
        == "sk-ant-secret-value"
    )


def test_workspace_id_and_key_must_be_paired(db):
    with pytest.raises(CreateOrgError, match="together"):
        create_org(
            db, name="Half", owner_email="h@x.com", workspace_key="sk-ant-only"
        )
