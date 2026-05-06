"""Multi-link share contract for candidate reports.

HANDOFF v2 §3 — every "Download PDF" / "Export PDF" reference was
replaced with shareable links. Recruiters can mint multiple active
links per candidate report, each with its own mode (recruiter / client
/ single-view) and expiry. The legacy single-token field on
``CandidateApplication.report_share_token`` is retained for
back-compat with existing share URLs but new links live here.

Modes:
- ``recruiter``    — full report incl. timeline + AI usage
- ``client``       — score + summary, no prompts
- ``single-view``  — invalidates after the first GET ``/share/:token``

Expiry presets in the share modal (24h / 7d / 30d / single-view) are
purely a UI convenience; this model stores the absolute ``expires_at``
plus the original ``expiry_preset`` for audit / re-creation.
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


SHARE_LINK_MODE_RECRUITER = "recruiter"
SHARE_LINK_MODE_CLIENT = "client"
SHARE_LINK_MODE_SINGLE_VIEW = "single-view"

SHARE_LINK_MODES = frozenset({
    SHARE_LINK_MODE_RECRUITER,
    SHARE_LINK_MODE_CLIENT,
    SHARE_LINK_MODE_SINGLE_VIEW,
})


class ShareLink(Base):
    __tablename__ = "share_links"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    application_id = Column(
        Integer,
        ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    token = Column(String, nullable=False, unique=True, index=True)
    mode = Column(String, nullable=False)
    # Absolute expiry. Single-view links also set this (a month in the
    # future) so list views can sort by recency uniformly; the
    # short-circuit on first GET is enforced via ``view_count``.
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Preset key used when minting the link (24h / 7d / 30d /
    # single-view). Lets the UI re-render the original choice on the
    # active-links list without having to compute it back from
    # ``expires_at``.
    expiry_preset = Column(String, nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, nullable=False, default=0)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    application = relationship(
        "CandidateApplication",
        backref="share_links",
        lazy="select",
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_single_view_consumed(self) -> bool:
        return self.mode == SHARE_LINK_MODE_SINGLE_VIEW and self.view_count > 0
