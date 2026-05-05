from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.user import User


ACTOR_RECRUITER = "recruiter"
ACTOR_AGENT = "agent"
ACTOR_SYSTEM = "system"


@dataclass(frozen=True)
class Actor:
    """Who is invoking an action.

    Mirrors ``CandidateApplicationEvent.actor_type`` plus the appropriate
    id field. ``user_id`` is set for recruiter / system; ``agent_run_id``
    is set for agent. Use the classmethods to construct rather than the
    raw dataclass to avoid setting both ids.
    """

    type: str  # 'recruiter' | 'agent' | 'system'
    user_id: Optional[int] = None
    agent_run_id: Optional[int] = None

    @classmethod
    def recruiter(cls, user: "User") -> "Actor":
        return cls(type=ACTOR_RECRUITER, user_id=int(user.id))

    @classmethod
    def agent(cls, agent_run_id: int) -> "Actor":
        return cls(type=ACTOR_AGENT, agent_run_id=int(agent_run_id))

    @classmethod
    def system(cls, user_id: Optional[int] = None) -> "Actor":
        return cls(type=ACTOR_SYSTEM, user_id=user_id)

    @property
    def event_actor_id(self) -> Optional[int]:
        """Returns the id to record on ``CandidateApplicationEvent.actor_id``."""
        if self.type == ACTOR_AGENT:
            return self.agent_run_id
        return self.user_id


@dataclass(frozen=True)
class ActionResult:
    """Generic action result wrapper.

    Most actions return the affected entity directly; this is for the
    cases (queue_decision, send_assessment) where multiple entities are
    affected and we want a structured payload.
    """

    ok: bool
    entity: Optional[Any] = None
    detail: Optional[str] = None
    extra: Optional[dict] = None
