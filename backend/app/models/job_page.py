from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

# Lifecycle for a published job page.
JOB_PAGE_STATUS_OPEN = "open"
JOB_PAGE_STATUS_CLOSED = "closed"
JOB_PAGE_STATUSES = (JOB_PAGE_STATUS_OPEN, JOB_PAGE_STATUS_CLOSED)


class JobPage(Base):
    """A shareable PUBLIC job page, created when a requisition is published.

    Publishing a requisition snapshots its PUBLIC-safe fields (title, location,
    comp band, the FE-rendered JD) onto a JobPage addressed by an unguessable
    ``token``; the page is served, with NO auth, at ``/api/v1/public/job/{token}``
    so the URL the recruiter shares works in any browser. Org-scoped (the org is
    the poster / consultancy whose name is shown) and deliberately carries NO
    client / rate / margin — none of the consultancy economics that live on the
    RoleBrief ever leak to the public.

    One page per ``brief_id``: re-publishing the same requisition refreshes the
    existing page (same token) rather than minting a new URL.
    """

    __tablename__ = "job_pages"
    __table_args__ = (
        Index("ix_job_pages_brief_id", "brief_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    # The requisition this page was published from (one page per brief).
    brief_id = Column(Integer, ForeignKey("role_briefs.id"), nullable=True)
    # Unguessable public address; minted once on first publish.
    token = Column(String, nullable=False, unique=True, index=True)

    # --- Public-safe snapshot (NO client / rate / margin) ---
    title = Column(String, nullable=True)
    jd_markdown = Column(Text, nullable=True)  # the FE-rendered JD body
    location = Column(String, nullable=True)  # "City, Country" (joined)
    workplace_type = Column(String, nullable=True)  # onsite | remote | hybrid
    employment_type = Column(String, nullable=True)
    seniority = Column(String, nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    salary_currency = Column(String, nullable=True)

    status = Column(String, nullable=False, server_default=JOB_PAGE_STATUS_OPEN)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization")
    brief = relationship("RoleBrief")
