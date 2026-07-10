"""Curated multi-candidate client submittal pack (WS2).

An agency recruiter shares a *curated set* of candidates for one role as a
single client-facing link — the submittal. Taali already had per-candidate
client-safe share links (``ShareLink``) and a frozen multi-candidate search
snapshot (``TopCandidatesReport``); this composes the same machinery into the
agency submittal shape: a role-scoped, ordered shortlist frozen at mint time
and served read-only at ``GET /submittal/{token}``.

Mirrors ``TopCandidatesReport`` (token / expiry / revoke / view_count + a
frozen JSON ``snapshot``) plus ``ShareLink`` (org + created_by + expiry preset
semantics). The snapshot is built from ``application_detail_payload(app,
client_safe=True)`` at mint time — nothing live is read on the public view.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class SubmittalPack(Base):
    __tablename__ = "submittal_packs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    role_id = Column(
        Integer, ForeignKey("roles.id"), index=True, nullable=False
    )
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    token = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=True)
    # Frozen at mint: {role: {title}, organization: {name}, candidates: [...]}.
    # Each candidate entry is derived from the client-safe application payload
    # plus the optional recruiter note. Read-only on the public view.
    snapshot = Column(JSON, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, nullable=False, default=0)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
