"""Public, no-auth CLIENT INTAKE share link — see ``routes.py``.

Exports:
- ``public_router`` — public ``/api/v1/public/intake/{token}`` view + chat +
  submit (mounted at app root)
"""

from .routes import public_router

__all__ = ["public_router"]
