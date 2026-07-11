"""Sourced prospect — an outreach lead that isn't yet a full Candidate.

A prospect is someone a recruiter sourced (CSV import, manual add, talent-pool
rediscovery) as a target for an outreach campaign. It's deliberately lighter
than ``Candidate``: no CV parsing, no scoring, no application. When a prospect
is matched to (or converted into) a real Candidate, ``candidate_id`` links them.

Attribution fields mirror the ats branch's application-attribution naming
(``source_strategy`` / ``source_name``) so a converted prospect can carry its
provenance straight onto the candidate_application it spawns:
``source_name`` = ``"csv:<filename>"`` | ``"manual"`` | ``"rediscovery"``.

This PR lands the model + CRUD + CSV import only. Campaign send machinery is
the next PR.
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..platform.database import Base


PROSPECT_STATUS_NEW = "new"
PROSPECT_STATUS_CONTACTED = "contacted"
PROSPECT_STATUS_INTERESTED = "interested"
PROSPECT_STATUS_CONVERTED = "converted"
PROSPECT_STATUS_ARCHIVED = "archived"

PROSPECT_STATUSES = (
    PROSPECT_STATUS_NEW,
    PROSPECT_STATUS_CONTACTED,
    PROSPECT_STATUS_INTERESTED,
    PROSPECT_STATUS_CONVERTED,
    PROSPECT_STATUS_ARCHIVED,
)


class Prospect(Base):
    __tablename__ = "prospects"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_prospect_org_email"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    # Set when the prospect is matched to / converted into a real candidate.
    candidate_id = Column(
        Integer, ForeignKey("candidates.id"), index=True, nullable=True
    )

    full_name = Column(String, nullable=False)
    # Stored normalized (lowercase/trimmed) — the (org, email) uniqueness key.
    email = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    position = Column(String, nullable=True)
    location = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # Attribution — aligned with candidate_applications.source_strategy/source_name.
    source_strategy = Column(String, nullable=True, default="sourced")
    source_name = Column(String, nullable=True)

    # One of PROSPECT_STATUSES.
    status = Column(String, nullable=False, default=PROSPECT_STATUS_NEW)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
