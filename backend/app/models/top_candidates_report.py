"""Shareable snapshot of a "top N candidates with X and Y" search.

A ``find_top_candidates`` result is persisted with an unguessable token so a
recruiter can share a read-only, no-auth report — the same ranked + grounded
view shown in chat, as a link. Mirrors ``ShareLink`` (token / expiry / revoke /
view_count), but holds a multi-candidate snapshot rather than a single
application, so it is its own table rather than a reuse of ``share_links``.

The ``snapshot`` is the find_top_candidates payload with candidate PII not
needed for a shareable view (e.g. email) scrubbed at persist time.
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


class TopCandidatesReport(Base):
    __tablename__ = "top_candidates_reports"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    token = Column(String, nullable=False, unique=True, index=True)
    query = Column(String, nullable=True)
    # The find_top_candidates payload (scrubbed): spec, ranked candidates with
    # grounded evidence, counts, warnings.
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
