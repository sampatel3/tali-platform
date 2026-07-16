"""Roles router assembly for the assessments runtime domain."""

from fastapi import APIRouter

from ...platform.config import settings
from .applications_routes import router as applications_router
from .interview_feedback_routes import router as interview_feedback_router
from .process_routes import router as process_router
from .related_role_capability_routes import router as related_role_capability_router
from .roles_management_routes import router as roles_management_router
from .sister_role_routes import router as sister_role_router

router = APIRouter(tags=["Roles"])
router.include_router(roles_management_router)
router.include_router(applications_router)
router.include_router(process_router)
router.include_router(interview_feedback_router)
router.include_router(sister_role_router)
router.include_router(related_role_capability_router)

# Compatibility export used by tests that patch runtime settings via this module.
__all__ = ["router", "settings"]
