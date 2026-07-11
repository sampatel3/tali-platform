"""Outreach — sourcing assist, suppression, prospects, public unsubscribe.

The candidate-outbound domain: search-assist artefacts today, plus the
legal + data layer beneath outreach campaigns (built next). See:
- ``sourcing_assist_routes`` — recruiter-auth X-ray strings + outreach drafts.
- ``prospect_routes`` — recruiter-auth prospect CRUD + CSV import (/api/v1).
- ``unsubscribe_routes`` — public, no-auth one-click unsubscribe.

Exports:
- ``router``                      — sourcing assist endpoints (mount /api/v1)
- ``prospects_router``            — auth-required prospect CRUD (mount /api/v1)
- ``unsubscribe_public_router``   — public ``/api/v1/public/unsubscribe/{token}``
"""

from .prospect_routes import router as prospects_router
from .sourcing_assist_routes import router
from .unsubscribe_routes import public_router as unsubscribe_public_router

__all__ = ["router", "prospects_router", "unsubscribe_public_router"]
