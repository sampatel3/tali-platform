from __future__ import annotations

from typing import TYPE_CHECKING

from .workable.provider import WorkableProvider

if TYPE_CHECKING:
    from ...models.organization import Organization
    from .base import ATSProvider


def resolve_ats_provider(org: Organization | None) -> ATSProvider | None:
    """Resolve the connected ATS provider for an org, or ``None`` if unconnected.

    Import this lazily at call sites (matching the codebase's lazy-import
    convention) — the resolver must NOT be imported by the ``Organization``
    model, or ``workable_actions_service``'s ``import Organization`` would cycle.

    The Workable arm mirrors ``_validate_writeable_org``'s connection check
    exactly. A Bullhorn arm is added later with one ``elif`` branch.
    """
    if org is None:
        return None
    if org.workable_connected and org.workable_access_token and org.workable_subdomain:
        return WorkableProvider(org)
    return None
