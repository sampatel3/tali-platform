"""HTTP surface for the autonomous agent.

Split across multiple route modules to keep file sizes within the
project's LOC gates:
- ``routes.py``: decisions, runs, agent status, run-now
- ``usage_routes.py``: per-role spend breakdown
- ``cohort_signals_routes.py``: GET /roles/{id}/agent/cohort-signals

A single ``router`` is exported (via APIRouter().include_router(...)) so
``main.py`` mounts everything in one shot.
"""

from fastapi import APIRouter

from .cohort_signals_routes import router as _cohort_signals_router
from .hub_feedback_routes import router as _hub_feedback_router
from .hub_routes import router as _hub_router
from .routes import router as _routes_router
from .usage_routes import router as _usage_router

router = APIRouter()
router.include_router(_routes_router)
router.include_router(_usage_router)
router.include_router(_cohort_signals_router)
router.include_router(_hub_router)
router.include_router(_hub_feedback_router)

__all__ = ["router"]
