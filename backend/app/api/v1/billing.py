"""Thin router wrapper for billing domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.billing_webhooks.billing_routes import router

__all__ = ["router"]
