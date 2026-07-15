"""Outreach — campaigns, suppression, and public response links.

The candidate-outbound domain. Manual Prospect ingestion and maintenance APIs
are retired in favor of the role agent's governed candidate/application flow.
The legacy database rows remain available internally for old campaign and
application-audit compatibility, but are no longer an operator workflow.
- ``unsubscribe_routes`` — public, no-auth one-click unsubscribe.

Exports:
- ``campaigns_router``            — auth-required outreach campaigns (mount /api/v1)
- ``unsubscribe_public_router``   — public ``/api/v1/public/unsubscribe/{token}``
- ``interest_public_router``      — public ``/api/v1/public/outreach/interest/{token}``
"""

from .campaign_routes import router as campaigns_router
from .interest_routes import public_router as interest_public_router
from .unsubscribe_routes import public_router as unsubscribe_public_router

__all__ = [
    "campaigns_router",
    "unsubscribe_public_router",
    "interest_public_router",
]
