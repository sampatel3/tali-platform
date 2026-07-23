"""Per-prospect share links for the sales deck.

The deck used to sit at ``/_deck/index.html`` as a static asset behind a
client-side token check. Vercel's ``handle: filesystem`` phase served that
path directly, so anyone who knew it got the whole deck *and* the build-inlined
gate token — the gate was cosmetic. The deck bundle now lives under
``backend/app/static/deck/`` and is served only through
``GET /deck/{token}/...`` after this table says the token is live.

One row per prospect. That gives a distinct URL to send, a per-link open
history, and revocation that affects one recipient without breaking anyone
else's link.

Deliberately a separate table from ``ShareLink``: that model requires an
``application_id`` and an ``organization_id`` (both NOT NULL, both FK), and a
deck link has neither. Relaxing those columns would weaken the invariant that
every candidate share link is application-bound in order to serve an unrelated
feature. ``SubmittalPack`` sets the precedent for composing the same machinery
into a new shape rather than overloading one table.
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


class DeckShareLink(Base):
    __tablename__ = "deck_share_links"

    id = Column(Integer, primary_key=True, index=True)
    # Who the link was minted for — shown in the admin list so an open can be
    # attributed. Free text; this is an internal sales tool, not a CRM.
    prospect_label = Column(String, nullable=False)
    note = Column(String, nullable=True)
    token = Column(String, nullable=False, unique=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Nullable unlike ShareLink.expires_at: a deck link is usually open-ended
    # and retired by revoking it, not by waiting. NULL means "no expiry".
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, nullable=False, default=0)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    views = relationship(
        "DeckShareView",
        back_populates="link",
        cascade="all, delete-orphan",
        lazy="select",
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class DeckShareView(Base):
    """One row per document open.

    ``view_count`` alone answers "how many times", not "when". Sam wants to see
    whether a prospect opened the deck before a call, so each open of the HTML
    entry point is recorded here. Subresource requests (css/js/img) are not
    recorded — they would multiply every open by nine.
    """

    __tablename__ = "deck_share_views"

    id = Column(Integer, primary_key=True, index=True)
    deck_share_link_id = Column(
        Integer,
        ForeignKey("deck_share_links.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    viewed_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Coarse attribution only. No IP is stored: the deck goes to prospects who
    # never agreed to anything, and a truncated UA is enough to tell a real
    # open from a link-preview bot.
    user_agent = Column(String, nullable=True)

    link = relationship("DeckShareLink", back_populates="views", lazy="select")
