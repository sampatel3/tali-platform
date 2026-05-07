"""HTTP surface for the autonomous agent.

See ``routes.py`` (decisions, runs, agent status) and ``usage_routes.py``
(per-role spend breakdown).
"""

from fastapi import APIRouter

from .routes import router as _routes_router
from .usage_routes import router as _usage_router

router = APIRouter()
router.include_router(_routes_router)
router.include_router(_usage_router)

__all__ = ["router"]
