"""Per-role conversational-agent thread + messages + read state.

This is the data layer for "chat to the role's agent" — the recruiter
opens a conversation scoped to one role and talks to that role's agent in
natural language ("re-screen this role at a 25k salary cap", "what happens
if I drop the threshold to 65?"). The agent reads role state, runs impact
analysis, and edits constraints through the ``agent_chat`` engine's tools.

Three tables:

* ``AgentConversation`` — exactly one per (organization, role). The thread
  is *shared* across the org's recruiters because it's the role's agent,
  not a private chat: the role's open questions (``agent_needs_input``) and
  pending decisions (``agent_decisions``) belong to the same surface and
  are merged into the timeline alongside the chat messages.
* ``AgentConversationMessage`` — one row per Anthropic message in the
  thread. ``content`` keeps the raw block list (text / tool_use /
  tool_result) so a follow-up turn can replay full context exactly like
  ``taali_chat``; ``text`` + ``actions`` are the render-friendly
  projections the timeline serves.
* ``AgentConversationRead`` — per-user last-read marker, so the sidebar can
  show an unread badge ("the agent sent you a message") without a private
  copy of the thread per user.

Integer PKs (not BigInteger) deliberately — matches ``taali_chat_*`` and
sidesteps the SQLite BigInteger autoincrement hack the test harness needs
for the few BigInteger tables.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# ``author_role`` — the Anthropic message role, used verbatim when the
# engine replays history into ``messages.create``.
AUTHOR_ROLE_USER = "user"
AUTHOR_ROLE_ASSISTANT = "assistant"

# ``kind`` — render discriminator for the timeline. ``tool`` rows are the
# synthetic ``user`` turns carrying ``tool_result`` blocks; they're kept for
# replay fidelity but hidden from the rendered timeline. ``action`` marks an
# assistant turn that carried out a constraint/threshold change (it has a
# populated ``actions`` payload the UI renders as an impact card).
MESSAGE_KIND_CHAT = "chat"
MESSAGE_KIND_ACTION = "action"
MESSAGE_KIND_TOOL = "tool"
MESSAGE_KIND_SYSTEM = "system"
# Deterministic, agent-initiated helper message. It is visible and replayed as
# assistant context, but it is not an interactive-turn reply. Keeping a
# separate kind prevents a proactive nudge from falsely closing an in-flight
# recruiter turn in ``conversation_agent_working``.
MESSAGE_KIND_PROACTIVE = "proactive"
# Durable notification emitted by background work (for example, a failed or
# budget-paused autonomous cycle). Event rows are visible in the transcript but
# are deliberately excluded from model history: they may arrive while an
# interactive turn is running and must never disturb the user/assistant tool
# sequence replayed to the model.
MESSAGE_KIND_EVENT = "event"


class AgentConversation(Base):
    """One conversation between the org's recruiters and a role's agent.

    Unique on (organization_id, role_id): the role *is* the agent, so every
    recruiter on the role shares the same thread. Soft-deletes are not
    modelled — a role's conversation lives as long as the role does and is
    cascade-deleted with it.
    """

    __tablename__ = "agent_conversations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "role_id", name="uq_agent_conversations_org_role"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    role_id = Column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title = Column(String, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # Bumped whenever a message is appended (by either party). Drives the
    # sidebar sort + "last activity" preview without a per-list COUNT/MAX.
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    # Durable interactive-turn dispatch. The user message and this pending
    # receipt commit atomically; a bounded worker lease prevents concurrent
    # duplicate paid turns and lets Beat recover a lost broker publish/worker.
    turn_message_id = Column(Integer, nullable=True, index=True)
    turn_status = Column(String(24), nullable=True, index=True)
    turn_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    turn_next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    turn_lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    turn_error = Column(String(500), nullable=True)

    messages = relationship(
        "AgentConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentConversationMessage.created_at",
    )
    reads = relationship(
        "AgentConversationRead",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class AgentConversationMessage(Base):
    """One message in a role-agent conversation.

    ``content`` is the Anthropic block list (``[{type: text|tool_use|
    tool_result, ...}]``) persisted verbatim so the engine can rebuild the
    exact message history on the next turn. ``text`` is the flattened
    human-readable text (for previews + the rendered timeline). ``actions``
    is the structured impact-card payload an assistant turn produced when it
    changed a constraint or threshold — see ``agent_chat.tools`` for the
    card shapes.
    """

    __tablename__ = "agent_conversation_messages"
    __table_args__ = (
        # Background event publication is retryable and may race across Celery
        # redeliveries. NULL for ordinary dialogue; event rows use a stable
        # source key so the database, not a best-effort pre-query, guarantees
        # exactly one transcript notification per role/source event.
        UniqueConstraint(
            "organization_id",
            "role_id",
            "source_key",
            name="uq_agent_conversation_messages_event_source",
        ),
        # The common read: "this conversation's messages, oldest first."
        # created_at + id keeps a stable order even at equal timestamps.
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Denormalized for cheap org-scoped filtering + the metering entity link.
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)

    author_role = Column(String, nullable=False)  # user | assistant
    # Set for user-authored messages; null for assistant + synthetic tool
    # turns. Lets the UI attribute "Sam asked…" in a multi-recruiter thread.
    author_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    kind = Column(String, nullable=False, default=MESSAGE_KIND_CHAT)

    content = Column(JSON, nullable=False)  # Anthropic block list
    text = Column(Text, nullable=True)  # flattened text projection
    actions = Column(JSON, nullable=True)  # impact-card payloads

    model = Column(String, nullable=True)
    stop_reason = Column(String, nullable=True)
    source_key = Column(String(255), nullable=True)
    token_usage = Column(JSON, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation = relationship("AgentConversation", back_populates="messages")
    author = relationship("User", foreign_keys=[author_user_id])


class AgentConversationRead(Base):
    """Per-user last-read marker for unread badges.

    One row per (conversation, user). ``last_read_at`` is compared against
    assistant ``created_at`` to count "messages the agent sent you since you
    last looked" — the notification number on the sidebar agent.
    """

    __tablename__ = "agent_conversation_reads"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "user_id", name="uq_agent_conversation_reads_convo_user"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    last_read_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation = relationship("AgentConversation", back_populates="reads")
