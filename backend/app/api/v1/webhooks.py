"""Thin router wrapper for webhook domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.billing_webhooks.webhook_routes import router

__all__ = ["router"]
