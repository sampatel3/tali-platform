from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..platform.database import Base

# Event types (single source of truth — reuse these, don't inline strings)
AUTH_EVENT_LOGIN_SUCCESS = "login_success"
AUTH_EVENT_LOGIN_FAILED = "login_failed"
AUTH_EVENT_ACCOUNT_LOCKED = "account_locked"
AUTH_EVENT_PASSWORD_RESET_REQUESTED = "password_reset_requested"
AUTH_EVENT_PASSWORD_RESET_COMPLETED = "password_reset_completed"
AUTH_EVENT_MEMBER_INVITED = "member_invited"
# Covers both invite revocation (pending target) and member removal — see
# the ``was_pending_invite`` metadata flag.
AUTH_EVENT_MEMBER_REMOVED = "member_removed"

AUTH_EVENT_TYPES = (
    AUTH_EVENT_LOGIN_SUCCESS,
    AUTH_EVENT_LOGIN_FAILED,
    AUTH_EVENT_ACCOUNT_LOCKED,
    AUTH_EVENT_PASSWORD_RESET_REQUESTED,
    AUTH_EVENT_PASSWORD_RESET_COMPLETED,
    AUTH_EVENT_MEMBER_INVITED,
    AUTH_EVENT_MEMBER_REMOVED,
)


class AuthEvent(Base):
    """Append-only audit trail for auth and team-management events.

    Rows are written best-effort (a failed audit write never blocks auth).
    `email` is captured as typed so failed logins for unknown/deleted users
    still leave a trace; `user_id` is SET NULL on user deletion for the same
    reason.
    """

    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Who performed the action when it targets someone else (e.g. the inviter).
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    event_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
