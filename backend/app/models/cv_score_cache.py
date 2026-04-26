from sqlalchemy import Column, DateTime, Float, Integer, JSON, String
from sqlalchemy.sql import func

from ..platform.database import Base


class CvScoreCache(Base):
    """Hash-keyed cache of cv_match results.

    The cache key is a sha256 over (cv_text, normalized_spec, criteria_json,
    prompt_version, model). A hit means we can short-circuit the Claude call:
    re-uploading the same CV against the same role + criteria + prompt
    version + model returns the prior result without spending tokens.

    Rows are immutable. Cache invalidation happens implicitly: when criteria
    or the spec change, the inputs hash to a different key, so the next
    score yields a cache miss and a fresh Claude call.
    """

    __tablename__ = "cv_score_cache"

    cache_key = Column(String, primary_key=True)
    prompt_version = Column(String, nullable=False)
    model = Column(String, nullable=False)
    score_100 = Column(Float, nullable=True)
    result = Column(JSON, nullable=False)
    hit_count = Column(Integer, default=1, nullable=False)
    last_hit_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
