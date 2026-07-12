"""P2 core analytics endpoints: native pipeline funnel + time-to-fill.

Read-only; scoped to the caller's org. Kept in its own thin router (the main
``analytics_routes`` is already large) but mounted under the same ``/analytics``
prefix so it reads as one API surface.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from .pipeline_analytics_service import pipeline_funnel, time_to_fill

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/pipeline-funnel")
def get_pipeline_funnel(
    role_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Current headcount per configured pipeline stage (funnel order) + outcome mix."""
    return pipeline_funnel(db, current_user.organization_id, role_id=role_id)


@router.get("/time-to-fill")
def get_time_to_fill(
    role_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Days from application to hired — overall summary + per-role breakdown."""
    return time_to_fill(db, current_user.organization_id, role_id=role_id)
