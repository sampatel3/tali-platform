from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.sql import func

from ..platform.database import Base

# Lifecycle for a consultancy client account.
CLIENT_STATUS_ACTIVE = "active"
CLIENT_STATUS_ARCHIVED = "archived"
CLIENT_STATUSES = (CLIENT_STATUS_ACTIVE, CLIENT_STATUS_ARCHIVED)


class Client(Base):
    """A consultancy CLIENT — the company a recruiter is filling roles for.

    In the consultancy model, requisitions / roles are opened on behalf of a
    client and billed at a client rate (per-requisition economics live on the
    RoleBrief: ``client_id`` + ``client_rate``). Org-scoped so a recruiting
    consultancy's client book never leaks across organizations.
    """

    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    status = Column(String, nullable=False, server_default=CLIENT_STATUS_ACTIVE)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
