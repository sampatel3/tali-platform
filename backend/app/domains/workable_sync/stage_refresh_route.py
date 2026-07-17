"""Compatibility import for the canonical plural-named route module."""

from __future__ import annotations

from .stage_refresh_routes import (
    StageRefreshResult,
    refresh_role_workable_stages,
    router,
)


__all__ = ["StageRefreshResult", "refresh_role_workable_stages", "router"]
