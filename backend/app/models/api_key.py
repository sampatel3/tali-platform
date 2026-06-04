"""Per-organization API keys for the public API + machine-to-machine access.

An API key is the *second* way (besides a FastAPI-Users JWT) to resolve a
request's ``organization_id``. Every business query already filters by org, so
a key that resolves to an org inherits tenant isolation for free — see
``docs/PUBLIC_API_BUILD_PLAN.md``.

Only the SHA-256 hash of the secret is stored; the plaintext is shown once at
creation and never again. Keys carry a recognisable ``tali_live_`` /
``tali_test_`` prefix (handy in logs + secret-scanners); ``prefix`` stores the
non-secret display head (e.g. ``tali_live_a1b2c3``) while ``hashed_secret`` is
the lookup key.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# ---- Scope vocabulary (least-privilege grants on a key) -------------------
SCOPE_ROLES_READ = "roles:read"
SCOPE_APPLICATIONS_READ = "applications:read"
SCOPE_ASSESSMENTS_READ = "assessments:read"
SCOPE_ASSESSMENTS_WRITE = "assessments:write"
SCOPE_SHARE_LINKS_WRITE = "share-links:write"

API_KEY_SCOPES = frozenset({
    SCOPE_ROLES_READ,
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
    SCOPE_ASSESSMENTS_WRITE,
    SCOPE_SHARE_LINKS_WRITE,
})

# Granted when a key is minted without an explicit scope list: read-only.
DEFAULT_API_KEY_SCOPES = (
    SCOPE_ROLES_READ,
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
)

KEY_PREFIX_LIVE = "tali_live_"
KEY_PREFIX_TEST = "tali_test_"


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String, nullable=False)
    # Non-secret display head, e.g. ``tali_live_a1b2c3`` — shown in the
    # Developers UI + request logs so a key is identifiable without the secret.
    prefix = Column(String, nullable=False, index=True)
    is_test = Column(Boolean, nullable=False, default=False)
    # SHA-256 hex of the full token. The plaintext is shown once at creation
    # and never stored; lookups hash the presented token and match here.
    hashed_secret = Column(String, nullable=False, unique=True, index=True)
    scopes = Column(JSON, nullable=False, default=list)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization = relationship("Organization")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
