from __future__ import annotations

from typing import TYPE_CHECKING

from ...platform.config import settings
from .base import ATSProvider
from .workable.provider import WorkableProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...models.organization import Organization
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


def resolve_application_ats_provider(
    org: Organization | None,
    db: Session | None,
    application: object | None,
) -> ATSProvider | None:
    """Resolve the writable ATS for one application.

    Org-level precedence remains Workable-first for unscoped operations, but a
    dual-connected workspace can legitimately contain a Workable role and a
    Bullhorn role at the same time.  Application-scoped writes must follow the
    application's durable remote linkage; otherwise a Bullhorn JobSubmission
    can be rejected or silently skipped merely because Workable is also
    connected for another role.

    When both application links are present, Workable retains the established
    migration-edge precedence.  A linked but unavailable provider returns
    ``None`` rather than falling through to the other ATS.
    """
    if org is None:
        return None

    workable_linked = bool(
        str(getattr(application, "workable_candidate_id", None) or "").strip()
    )
    bullhorn_linked = bool(
        str(
            getattr(application, "bullhorn_job_submission_id", None) or ""
        ).strip()
    )

    if workable_linked:
        if (
            org.workable_connected
            and org.workable_access_token
            and org.workable_subdomain
        ):
            return WorkableProvider(org)
        return None

    if bullhorn_linked:
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

    return resolve_ats_provider(org, db)
