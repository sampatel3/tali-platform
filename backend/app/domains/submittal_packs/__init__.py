"""Curated multi-candidate client submittal packs (WS2) — see ``routes.py``.

Exports:
- ``router``         — auth-required submittal-pack CRUD (mounted under /api/v1)
- ``public_router``  — public ``/submittal/{token}`` view (mounted at app root)
"""

from .routes import public_router, router

__all__ = ["public_router", "router"]
