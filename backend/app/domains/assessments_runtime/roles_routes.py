"""Roles router assembly for the assessments runtime domain."""

from fastapi import APIRouter

from ...platform.config import settings
from .applications_routes import router as applications_router
from .roles_management_routes import router as roles_management_router

router = APIRouter(tags=["Roles"])
router.include_router(roles_management_router)
router.include_router(applications_router)

# Compatibility export used by tests that patch runtime settings via this module.
__all__ = ["router", "settings"]
