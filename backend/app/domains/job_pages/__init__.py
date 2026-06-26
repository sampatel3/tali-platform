"""Public job-page view — see ``routes.py``.

Exports:
- ``public_router`` — public ``/api/v1/public/job/{token}`` view (mounted at app root)
"""

from .routes import public_router

__all__ = ["public_router"]
