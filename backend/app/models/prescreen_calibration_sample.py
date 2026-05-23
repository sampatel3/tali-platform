from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.sql import func

from ..platform.database import Base


class PrescreenCalibrationSample(Base):
    """Training data for the pre-screen score calibrator (reject inference).

    A backend-only job periodically takes a RANDOM sample of pre-screen
    *rejects*, runs full cv_match on them in **shadow mode** — the result is
    recorded here and NEVER written to ``candidate_applications`` or shown to
    a recruiter — and stores the ``(cheap pre-screen score → full score)``
    pair. The calibrator fits on these pairs to learn where the cheap gate
    systematically mis-predicts, which is the only unbiased way to calibrate
    the *filter* decision (we otherwise never observe full scores below the
    gate). Purely a training dataset; no recruiter-facing view reads it.
    """

    __tablename__ = "prescreen_calibration_samples"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    application_id = Column(
        Integer, ForeignKey("candidate_applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Feature: the cheap pre-screen LLM score (evidence.llm_score_100).
    pre_screen_score = Column(Float, nullable=True)
    # Label: the authoritative full cv_match role_fit_score from shadow scoring.
    full_cv_match_score = Column(Float, nullable=True)
    full_recommendation = Column(String, nullable=True)
    scoring_status = Column(String, nullable=True)  # "ok" / "failed"
    sampled_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
