"""Per-prospect share links for the sales deck — see ``routes.py``.

Exports:
- ``router``         — owner-gated mint/list/revoke (mounted under /api/v1)
- ``public_router``  — public ``/deck/{token}`` serving (mounted at app root)
"""

from .routes import public_router, router

__all__ = ["public_router", "router"]
