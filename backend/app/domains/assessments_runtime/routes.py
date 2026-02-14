"""Assessment router assembly for the runtime domain."""

from fastapi import APIRouter

from ...platform.config import settings
from .candidate_runtime_routes import router as candidate_runtime_router
from .recruiter_routes import router as recruiter_router

router = APIRouter(prefix="/assessments", tags=["Assessments"])
router.include_router(recruiter_router)
router.include_router(candidate_runtime_router)

# Compatibility export used by tests that patch runtime settings via this module.
__all__ = ["router", "settings"]
