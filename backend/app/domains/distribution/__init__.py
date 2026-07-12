"""Role distribution domain — copy-paste artefacts + a public job-board feed.

- ``router`` (recruiter-auth, mounted under ``/api/v1``): the per-role
  distribution artefacts (LinkedIn post, share URLs, feed URL).
- ``public_router`` (no auth, same visibility as the careers board): the org
  careers-board ``JobPosting`` XML feed the boards pull.
"""
from .routes import public_router, router

__all__ = ["router", "public_router"]
