"""Best-effort recorder for the auth_events audit trail.

Audit writes must never break the auth flow they observe: every recorder
swallows and logs its own failures. Client IP / user agent come from the
request-context vars set by RequestLoggingMiddleware, so callers deep inside
FastAPI-Users (which never see the Request) can still attribute events.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...models.auth_event import AuthEvent
from ...platform.request_context import get_client_meta

logger = logging.getLogger("taali.auth")


def _build_event(
    event_type: str,
    *,
    user_id: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    email: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> AuthEvent:
    client = get_client_meta()
    return AuthEvent(
        event_type=event_type,
        user_id=user_id,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        email=(email or None) and email.strip().lower()[:320],
        ip_address=(client.get("ip") or None) and str(client.get("ip"))[:64],
        user_agent=(client.get("user_agent") or None) and str(client.get("user_agent"))[:512],
        event_metadata=metadata or None,
    )


def record_auth_event(session: Session, event_type: str, **kwargs) -> None:
    """Sync variant (routes using get_db)."""
    try:
        session.add(_build_event(event_type, **kwargs))
        session.commit()
    except Exception:
        logger.exception("Failed to record auth event %s", event_type)
        try:
            session.rollback()
        except Exception:
            pass


async def record_auth_event_async(session: AsyncSession, event_type: str, **kwargs) -> None:
    """Async variant (FastAPI-Users user manager, get_async_db)."""
    try:
        session.add(_build_event(event_type, **kwargs))
        await session.commit()
    except Exception:
        logger.exception("Failed to record auth event %s", event_type)
        try:
            await session.rollback()
        except Exception:
            pass
