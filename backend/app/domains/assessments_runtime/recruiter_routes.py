"""Recruiter-facing assessment router assembly."""

from fastapi import APIRouter

from .recruiter_management_routes import router as management_router
from .recruiter_reporting_routes import router as reporting_router

router = APIRouter()
router.include_router(management_router)
router.include_router(reporting_router)

__all__ = ["router"]
