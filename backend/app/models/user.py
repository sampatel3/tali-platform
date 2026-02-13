from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_users.db import SQLAlchemyBaseUserTable

from ..platform.database import Base


class User(SQLAlchemyBaseUserTable[int], Base):
    """User model extending FastAPI-Users base with TAALI-specific fields."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    full_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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
