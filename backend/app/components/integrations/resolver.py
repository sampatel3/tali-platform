from __future__ import annotations

from typing import TYPE_CHECKING

from ...platform.config import settings
from .workable.provider import WorkableProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...models.organization import Organization
    from .base import ATSProvider


def resolve_ats_provider(
    org: Organization | None, db: Session | None = None
) -> ATSProvider | None:
    """Resolve the connected ATS provider for an org, or ``None`` if unconnected.

    Import this lazily at call sites (matching the codebase's lazy-import
    convention) — the resolver must NOT be imported by the ``Organization``
    model, or ``workable_actions_service``'s ``import Organization`` would cycle.

    Precedence rule (deterministic, simplest possible): **Workable wins when an
    org is connected to both.** Workable is the established, live integration; a
    dual-connected org is a migration edge, and preferring the incumbent means an
    in-flight Workable org never silently reroutes its writes when Bullhorn is
    attached. A Bullhorn-only org resolves to :class:`BullhornProvider`.

    The Workable arm mirrors ``_validate_writeable_org``'s connection check; the
    Bullhorn arm mirrors ``sync_runner._org_connected`` and is gated behind
    ``BULLHORN_ENABLED`` (flag off → Bullhorn is never resolved, so every
    write/read hook is a no-op for a Bullhorn org). ``db`` is required for the
    Bullhorn arm (the provider's reverse stage-map + local-write stamp need a
    session); Workable ignores it.
    """
    if org is None:
        return None
    if org.workable_connected and org.workable_access_token and org.workable_subdomain:
        return WorkableProvider(org)
    if (
        settings.BULLHORN_ENABLED
        and db is not None
        and getattr(org, "bullhorn_connected", False)
        and getattr(org, "bullhorn_client_id", None)
        and getattr(org, "bullhorn_refresh_token", None)
        and getattr(org, "bullhorn_username", None)
    ):
        from .bullhorn.provider import BullhornProvider

        return BullhornProvider(org, db)
    return None
