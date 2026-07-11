"""Outreach foundations — email suppression, prospects, public unsubscribe.

The legal + data layer beneath outreach campaigns (built next). See:
- ``prospect_routes`` — recruiter-auth prospect CRUD + CSV import (/api/v1).
- ``unsubscribe_routes`` — public, no-auth one-click unsubscribe.

Exports:
- ``prospects_router``            — auth-required prospect CRUD (mount /api/v1)
- ``unsubscribe_public_router``   — public ``/api/v1/public/unsubscribe/{token}``
"""

from .prospect_routes import router as prospects_router
from .unsubscribe_routes import public_router as unsubscribe_public_router

__all__ = ["prospects_router", "unsubscribe_public_router"]
