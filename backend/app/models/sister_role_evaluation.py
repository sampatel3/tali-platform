"""Persistent alternate-role scores over canonical ATS applications."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

SISTER_EVAL_PENDING = "pending"
SISTER_EVAL_RUNNING = "running"
SISTER_EVAL_RETRY_WAIT = "retry_wait"
SISTER_EVAL_STALE_HELD = "stale_held"
SISTER_EVAL_DONE = "done"
SISTER_EVAL_ERROR = "error"
SISTER_EVAL_UNSCORABLE = "unscorable"
SISTER_EVAL_EXCLUDED = "excluded"
SISTER_EVAL_STATUSES = {
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RUNNING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_STALE_HELD,
    SISTER_EVAL_DONE,
    SISTER_EVAL_ERROR,
    SISTER_EVAL_UNSCORABLE,
    SISTER_EVAL_EXCLUDED,
}


class SisterRoleEvaluation(Base):
    """Explicit related-role candidate membership and role-owned state.

    Row existence means the candidate belongs to this related role.  The
    source application supplies candidate evidence; ``ats_application_id``
    optionally points at the shared external ATS application.  Pipeline and
    outcome fields are local to this role.  Shared ATS state may restrict an
    action, but must never rewrite this membership or its local state.
    """

    __tablename__ = "sister_role_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "source_application_id",
            name="uq_sister_evaluations_role_application",
        ),
        # Historical dual-source rows remain as soft-deleted audit shadows,
        # while exactly one live row is the canonical role/candidate pool
        # membership. Migration 187 builds the equivalent PostgreSQL index
        # concurrently after migration 185 has collapsed legacy duplicates.
        Index(
            "uq_sister_evaluations_live_role_candidate",
            "role_id",
            "candidate_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_sister_evaluations_role_status", "role_id", "status"),
        Index(
            "ix_sister_evaluations_role_pipeline_stage", "role_id", "pipeline_stage"
        ),
        Index("ix_sister_evaluations_recovery", "status", "next_attempt_at"),
        Index(
            "ix_sister_evaluations_role_membership_state",
            "role_id",
            "deleted_at",
            "application_outcome",
            "pipeline_stage",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Migration 185 installs a database trigger that derives this value from
    # source_application_id for pre-185 workers, then enforces NOT NULL. New
    # writers still set it explicitly so identity is visible in application
    # code and the database verifies both values describe the same candidate.
    candidate_id = Column(
        Integer, ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_application_id = Column(
        Integer, ForeignKey("candidate_applications.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    ats_application_id = Column(
        Integer,
        ForeignKey("candidate_applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(String(length=16), nullable=False, default=SISTER_EVAL_PENDING)
    pipeline_stage = Column(
        String(length=32), nullable=False, default="applied", server_default="applied"
    )
    pipeline_stage_updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    pipeline_stage_source = Column(
        String(length=16), nullable=False, default="system", server_default="system"
    )
    application_outcome = Column(
        String(length=32), nullable=False, default="open", server_default="open"
    )
    application_outcome_updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    application_outcome_source = Column(
        String(length=16), nullable=False, default="system", server_default="system"
    )
    version = Column(Integer, nullable=False, default=1, server_default="1")
    membership_source = Column(
        String(length=32),
        nullable=False,
        default="initial_snapshot",
        server_default="initial_snapshot",
    )
    spec_fingerprint = Column(String(length=64), nullable=False)
    cv_fingerprint = Column(String(length=64), nullable=True)
    role_fit_score = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)
    # Recruiter-owned decision for this exact logical role membership.  The
    # physical source application may simultaneously belong to an ATS owner,
    # whose manual decision must remain independent.
    manual_decision = Column(JSON, nullable=True)
    # Compact audit trail of superseded results. The current score stays in the
    # first-class columns for fast ranking; prior scores, summaries, and
    # spec/CV fingerprints remain inspectable without cloning applications.
    history = Column(JSON, nullable=True)
    model_version = Column(String(length=100), nullable=True)
    prompt_version = Column(String(length=100), nullable=True)
    trace_id = Column(String(length=100), nullable=True)
    cache_hit = Column(Boolean, nullable=False, default=False, server_default="false")
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_attempted_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(length=100), nullable=True)
    queued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    role = relationship("Role")
    candidate = relationship("Candidate")
    source_application = relationship(
        "CandidateApplication", foreign_keys=[source_application_id]
    )
    _ats_application_record = relationship(
        "CandidateApplication", foreign_keys=[ats_application_id]
    )

    @property
    def ats_application(self):
        """Return the ATS transport only when its complete identity is valid.

        The database trigger owns this invariant in production.  Keeping the
        object-level relationship fail-closed as well prevents a corrupt legacy
        row, a fixture built without migrations, or a partially rolled deploy
        from exposing another candidate's/provider role's state through a
        canonical read.
        """

        application = self._ats_application_record
        role = self.role
        if application is None or role is None:
            return None
        owner_role_id = getattr(role, "ats_owner_role_id", None)
        if owner_role_id is None:
            return None
        owner_role = getattr(role, "ats_owner_role", None)
        if (
            owner_role is None
            or int(owner_role.organization_id) != int(self.organization_id)
            or getattr(owner_role, "deleted_at", None) is not None
        ):
            return None
        if getattr(application, "deleted_at", None) is not None:
            return None
        if int(application.organization_id) != int(self.organization_id):
            return None
        if int(application.candidate_id) != int(self.candidate_id):
            return None
        if int(application.role_id) != int(owner_role_id):
            return None
        return application


@event.listens_for(SisterRoleEvaluation, "before_insert")
@event.listens_for(SisterRoleEvaluation, "before_update")
def _resolve_candidate_identity(_mapper, connection, target) -> None:
    """Mirror migration 185's compatibility trigger for ORM-created schemas.

    Production PostgreSQL owns the invariant in a trigger so pre-185 workers
    remain compatible. Tests and disposable databases are commonly created
    directly from ORM metadata, so this listener supplies the same value there
    without weakening the model's NOT NULL contract.
    """

    source_application_id = getattr(target, "source_application_id", None)
    if source_application_id is None:
        return
    from .candidate_application import CandidateApplication

    source_identity = connection.execute(
        select(
            CandidateApplication.candidate_id,
            CandidateApplication.organization_id,
        ).where(
            CandidateApplication.id == int(source_application_id)
        )
    ).one_or_none()
    if source_identity is None:
        return
    resolved_candidate_id, source_organization_id = source_identity
    if target.candidate_id is None:
        target.candidate_id = int(resolved_candidate_id)
    elif int(target.candidate_id) != int(resolved_candidate_id):
        raise ValueError(
            "candidate_id does not own SisterRoleEvaluation.source_application_id"
        )
    if int(target.organization_id) != int(source_organization_id):
        raise ValueError(
            "organization_id does not own "
            "SisterRoleEvaluation.source_application_id"
        )

    from .role import Role

    role_identity = connection.execute(
        select(Role.organization_id, Role.ats_owner_role_id).where(
            Role.id == int(target.role_id)
        )
    ).one_or_none()
    if role_identity is None:
        raise ValueError("SisterRoleEvaluation.role_id does not exist")
    role_organization_id, ats_owner_role_id = role_identity
    if int(target.organization_id) != int(role_organization_id):
        raise ValueError(
            "organization_id does not own SisterRoleEvaluation.role_id"
        )

    ats_application_id = getattr(target, "ats_application_id", None)
    if ats_application_id is None:
        return
    if ats_owner_role_id is None:
        raise ValueError(
            "SisterRoleEvaluation ATS owner must belong to its organization"
        )
    ats_owner_organization_id = connection.scalar(
        select(Role.organization_id).where(Role.id == int(ats_owner_role_id))
    )
    if ats_owner_organization_id is None or int(
        ats_owner_organization_id
    ) != int(target.organization_id):
        raise ValueError(
            "SisterRoleEvaluation ATS owner must belong to its organization"
        )
    ats_identity = connection.execute(
        select(
            CandidateApplication.organization_id,
            CandidateApplication.candidate_id,
            CandidateApplication.role_id,
        ).where(CandidateApplication.id == int(ats_application_id))
    ).one_or_none()
    if ats_identity is None:
        raise ValueError("SisterRoleEvaluation.ats_application_id does not exist")
    ats_organization_id, ats_candidate_id, ats_role_id = ats_identity
    if (
        ats_owner_role_id is None
        or int(ats_organization_id) != int(target.organization_id)
        or int(ats_candidate_id) != int(target.candidate_id)
        or int(ats_role_id) != int(ats_owner_role_id)
    ):
        raise ValueError(
            "ats_application_id must belong to the membership organization, "
            "candidate, and role's declared ATS owner"
        )
