"""Sourcing outreach assist — see ``sourcing_assist_routes.py``.

Exports:
- ``router`` — recruiter-auth sourcing search + outreach-draft endpoints
  (mounted under ``/api/v1``).
"""

from .sourcing_assist_routes import router

__all__ = ["router"]
