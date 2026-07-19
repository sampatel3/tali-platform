"""Assessment router assembly for the runtime domain."""

from fastapi import APIRouter

from ...platform.config import settings
from .candidate_chat_reconciliation_routes import (
    router as candidate_chat_reconciliation_router,
)
from .candidate_runtime_routes import router as candidate_runtime_router
from .recruiter_routes import router as recruiter_router
from .result_delivery_reconciliation_routes import (
    router as result_delivery_reconciliation_router,
)

router = APIRouter(prefix="/assessments", tags=["Assessments"])
router.include_router(recruiter_router)
router.include_router(candidate_runtime_router)
router.include_router(result_delivery_reconciliation_router)
router.include_router(candidate_chat_reconciliation_router)

# Compatibility export used by tests that patch runtime settings via this module.
__all__ = ["router", "settings"]
