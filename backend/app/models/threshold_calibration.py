"""Learned role_fit advance/reject threshold, calibrated from recruiter
terminal decisions.

The OBJECTIVE role_fit score stays raw — this table only holds the POLICY
boundary (the score → advance/reject cut) that the decision engine compares
that raw score against. A nightly job learns the cut that best reproduces
recruiters' actual advance/reject decisions (Youden's J over (score, label)
pairs), bias-gates it, and writes a ``proposed`` row. A recruiter (or, opt-in,
the bias-gated auto-apply path) activates it; ``resolve_role_fit_threshold``
then reads the single ``active`` row for the (org, role) and feeds it to the
engine via the existing effective-threshold path.

Why its own table rather than ``DecisionPolicy.policy_json``: the engine's
``apply_effective_threshold`` already OVERWRITES the policy's stored threshold
with ``resolve_role_fit_threshold``'s output at eval time, so the policy row is
not the source of truth for this boundary; and ``RubricRevision`` versioning is
deprecated + org-only, whereas this needs per-role-with-pooling plus learner
provenance (sample counts, metric, bias-gate state).
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from ..platform.database import Base

# Status lifecycle.
STATUS_PROPOSED = "proposed"      # written by the job; shadow — never read by the engine
STATUS_ACTIVE = "active"          # the one the engine reads (one per org+role_id)
STATUS_SUPERSEDED = "superseded"  # a previously-active row replaced by a newer activation
STATUS_DISCARDED = "discarded"    # a proposal the recruiter rejected


class ThresholdCalibration(Base):
    __tablename__ = "threshold_calibrations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    # NULL = org-wide pooled calibration; non-NULL = a per-role calibration.
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope = Column(String, nullable=False)  # "org" | "role"

    # The learned boundary on the raw role_fit (cv_match) score, 0..100.
    learned_threshold = Column(Float, nullable=False)
    metric_name = Column(String, nullable=False)   # e.g. "youden_j"
    metric_value = Column(Float, nullable=True)     # optimized objective at the chosen cut

    status = Column(String, nullable=False, default=STATUS_PROPOSED, index=True)

    # Sample provenance.
    n_positive = Column(Integer, nullable=False, default=0)
    n_negative = Column(Integer, nullable=False, default=0)
    # True when a sparse role fell back to (or was shrunk toward) the org pool.
    pooled_from_org = Column(Boolean, nullable=False, default=False)
    # The scoring-prompt cohort the labels were drawn from (provenance only).
    prompt_version = Column(String, nullable=True)

    # Bias gate.
    bias_gate_passed = Column(Boolean, nullable=True)
    bias_gate_cold_start = Column(Boolean, nullable=True)  # no protected holdout configured
    bias_gate_reason = Column(Text, nullable=True)

    # Full learner diagnostics (J curve, base rate, shrinkage weight, clamp flags).
    metrics_json = Column(JSON, nullable=True)

    training_window_start = Column(DateTime(timezone=True), nullable=True)
    training_window_end = Column(DateTime(timezone=True), nullable=True)

    proposed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
