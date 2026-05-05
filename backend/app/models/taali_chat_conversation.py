from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class TaaliChatConversation(Base):
    """One conversation between a recruiter and Taali Chat.

    Org-scoped (every conversation belongs to one organisation; we never
    surface another org's conversations even to admins). Soft-delete via
    ``archived_at``. Drives the sidebar conversation list in the chat UI.
    """

    __tablename__ = "taali_chat_conversations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    title = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    archived_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship(
        "TaaliChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="TaaliChatMessage.created_at",
    )
