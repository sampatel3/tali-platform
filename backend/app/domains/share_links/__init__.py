"""Multi-link candidate report share contract — see ``routes.py``.

Exports:
- ``router``         — auth-required share-link CRUD (mounted under /api/v1)
- ``public_router``  — public ``/share/{token}`` view (mounted at app root)
"""

from .routes import public_router, router

__all__ = ["public_router", "router"]
