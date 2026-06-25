from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_users.db import SQLAlchemyBaseUserTable

from ..platform.database import Base

# RBAC roles (P0.5). Default 'admin' preserves pre-RBAC behavior (every existing
# user was effectively an admin). Hiring-team scoping + broad write-route gating
# build on this.
ROLE_ADMIN = "admin"
ROLE_RECRUITER = "recruiter"
ROLE_HIRING_MANAGER = "hiring_manager"
ROLE_INTERVIEWER = "interviewer"
ROLE_VIEWER = "viewer"
USER_ROLES = (
    ROLE_ADMIN,
    ROLE_RECRUITER,
    ROLE_HIRING_MANAGER,
    ROLE_INTERVIEWER,
    ROLE_VIEWER,
)


class User(SQLAlchemyBaseUserTable[int], Base):
    """User model extending FastAPI-Users base with TAALI-specific fields."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    full_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default=ROLE_ADMIN)
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    organization = relationship("Organization", back_populates="users")
