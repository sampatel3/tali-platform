from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.sql import func

from ..platform.database import Base


class CvEmbedding(Base):
    """Hash-keyed cache of embedding vectors.

    The cache key is sha256 over (text, provider, model). The same text
    embedded under a different provider/model produces a different hash,
    so rows never collide across providers. Vectors are stored as a JSON
    array of floats — provider-dependent dimensionality is fine because
    the column is schemaless. Persistence is bounded by row count rather
    than byte size; a future cleanup job evicts rows whose
    ``last_hit_at`` exceeds the LRU window.

    Lives separately from ``cv_score_cache`` because embeddings have a
    different lifecycle: a single CV may be scored against many JDs but
    is embedded exactly once per provider/model. Co-locating would force
    every score-cache write to also touch the embedding row.
    """

    __tablename__ = "cv_embeddings"

    content_hash = Column(String, primary_key=True)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    embedding = Column(JSON, nullable=False)
    last_hit_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
