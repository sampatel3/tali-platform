from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

# Hiring-team membership roles on a specific job (role). Distinct from the
# org-wide RBAC role on ``users.role`` — this is per-job: who owns the req, who
# interviews, who coordinates. Drives per-job authorization (P0.5) and, later,
# scorecard ownership + interview scheduling (P3).
TEAM_ROLE_HIRING_MANAGER = "hiring_manager"
TEAM_ROLE_RECRUITER = "recruiter"
TEAM_ROLE_INTERVIEWER = "interviewer"
TEAM_ROLE_COORDINATOR = "coordinator"
TEAM_ROLES = (
    TEAM_ROLE_HIRING_MANAGER,
    TEAM_ROLE_RECRUITER,
    TEAM_ROLE_INTERVIEWER,
    TEAM_ROLE_COORDINATOR,
)


class JobHiringTeam(Base):
    """A user's membership on a specific job's hiring team, with a per-job role.

    Org-scoped (``organization_id`` mirrors the role's org for cheap tenant
    filtering). One row per (role, user); ``team_role`` says what they do on this
    job. Additive for P0.5 — per-job authz + scorecards consume it later.
    """

    __tablename__ = "job_hiring_team"
    __table_args__ = (
        UniqueConstraint("role_id", "user_id", name="uq_job_hiring_team_role_user"),
        Index("ix_job_hiring_team_role", "role_id"),
        Index("ix_job_hiring_team_user", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    team_role = Column(
        String, nullable=False, server_default=TEAM_ROLE_INTERVIEWER
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    role = relationship("Role")
    user = relationship("User")
