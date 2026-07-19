"""Durable idempotency receipts for confirmed chat mutations.

The transcript's ``_confirmation_consumed`` marker is the human/audit view of
the command.  This row is the execution-side receipt: it is keyed from the
opaque server confirmation token, stores no command arguments, and lets a
recovered chat turn reuse the original downstream dispatch key/result instead
of repeating a side effect.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.sql import func

from ..platform.database import Base


CHAT_COMMAND_PENDING = "pending"
CHAT_COMMAND_COMPLETED = "completed"
CHAT_COMMAND_STATUSES = (CHAT_COMMAND_PENDING, CHAT_COMMAND_COMPLETED)


class ChatCommandReceipt(Base):
    __tablename__ = "chat_command_receipts"

    id = Column(Integer, primary_key=True, index=True)
    command_key = Column(String(96), nullable=False, unique=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)
    requested_by_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # Agent Chat and Taali Chat have separate conversation tables.  A typed
    # discriminator avoids a polymorphic foreign key while retaining the exact
    # scope used to validate a replay.
    conversation_kind = Column(String(24), nullable=False)
    conversation_id = Column(Integer, nullable=False)
    operation = Column(String(100), nullable=False)
    arguments_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default=CHAT_COMMAND_PENDING)
    result = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "CHAT_COMMAND_COMPLETED",
    "CHAT_COMMAND_PENDING",
    "CHAT_COMMAND_STATUSES",
    "ChatCommandReceipt",
]
