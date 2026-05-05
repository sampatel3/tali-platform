from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# Roles match Anthropic's Messages API: user, assistant. Tool calls/results
# are stored as content blocks inside the assistant/user message bodies
# rather than separate rows — that way replaying ``messages`` straight back
# to the API works without reshuffling.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
TAALI_CHAT_ROLES = (ROLE_USER, ROLE_ASSISTANT)


class TaaliChatMessage(Base):
    """One message in a Taali Chat conversation.

    ``content`` is a list of Anthropic-shaped content blocks (text,
    tool_use, tool_result) so we can hand it back to ``messages.create``
    on follow-up turns without re-shaping. ``token_usage`` carries the
    per-message Anthropic usage report — bookkeeping for the metering
    layer that records UsageEvents for billing.
    """

    __tablename__ = "taali_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("taali_chat_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    role = Column(String, nullable=False)  # one of TAALI_CHAT_ROLES
    content = Column(JSON, nullable=False)
    model = Column(String, nullable=True)
    stop_reason = Column(String, nullable=True)
    token_usage = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation = relationship("TaaliChatConversation", back_populates="messages")
