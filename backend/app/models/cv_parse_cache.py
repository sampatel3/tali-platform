"""Content-hash cache for parsed CV sections (cv_parsing module)."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, JSON, String
from sqlalchemy.sql import func

from ..platform.database import Base


class CvParseCache(Base):
    __tablename__ = "cv_parse_cache"

    cache_key = Column(String, primary_key=True)
    prompt_version = Column(String, nullable=False)
    model = Column(String, nullable=False)
    result = Column(JSON, nullable=False)
    hit_count = Column(Integer, default=1, nullable=False)
    last_hit_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
