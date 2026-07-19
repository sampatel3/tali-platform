"""Provider-specific serialization namespaces for shared ATS operations."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def op_mutex_namespaces(
    organization_id: int,
    payload: dict | None = None,
) -> tuple[str, ...]:
    """Resolve every provider lock needed by an application/decision payload."""

    from ..tasks.assessment_tasks import _WORKABLE_ORG_MUTEX_KEY_PREFIX

    explicit_provider = str((payload or {}).get("provider") or "").strip().lower()
    if frozenset(payload or {}) == frozenset({"application_id", "user_id", "body"}):
        # Rolling-deploy payloads from the old producer are always Workable,
        # even when the now-dual-linked application resolves Bullhorn first.
        explicit_provider = "workable"
    if explicit_provider == "workable":
        return (_WORKABLE_ORG_MUTEX_KEY_PREFIX,)
    if explicit_provider == "bullhorn":
        from ..components.integrations.bullhorn.sync_runner import (
            BULLHORN_ORG_MUTEX_NAMESPACE,
        )

        return (BULLHORN_ORG_MUTEX_NAMESPACE,)

    try:
        from ..components.integrations.bullhorn.provider import BullhornProvider
        from ..components.integrations.bullhorn.sync_runner import (
            BULLHORN_ORG_MUTEX_NAMESPACE,
        )
        from ..components.integrations.resolver import (
            resolve_application_ats_provider,
            resolve_ats_provider,
        )
        from ..models.agent_decision import AgentDecision
        from ..models.candidate_application import CandidateApplication
        from ..models.organization import Organization
        from ..platform.database import SessionLocal

        db = SessionLocal()
        try:
            org = (
                db.query(Organization)
                .filter(Organization.id == int(organization_id))
                .first()
            )
            application_ids: set[int] = set()
            if (payload or {}).get("application_id") is not None:
                application_ids.add(int(payload["application_id"]))
            application_ids.update(
                map(int, (payload or {}).get("application_ids") or [])
            )
            decision_ids = list((payload or {}).get("decision_ids") or [])
            if (payload or {}).get("decision_id") is not None:
                decision_ids.append(int(payload["decision_id"]))
            if decision_ids:
                application_ids.update(
                    int(row[0])
                    for row in db.query(AgentDecision.application_id)
                    .filter(
                        AgentDecision.organization_id == int(organization_id),
                        AgentDecision.id.in_([int(value) for value in decision_ids]),
                    )
                    .all()
                )
            namespaces: set[str] = set()
            applications = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.organization_id == int(organization_id),
                    CandidateApplication.id.in_(application_ids),
                )
                .all()
                if application_ids
                else []
            )
            for app in applications:
                provider = resolve_application_ats_provider(org, db, app)
                if isinstance(provider, BullhornProvider) or (
                    app.bullhorn_job_submission_id and not app.workable_candidate_id
                ):
                    namespaces.add(BULLHORN_ORG_MUTEX_NAMESPACE)
                else:
                    namespaces.add(_WORKABLE_ORG_MUTEX_KEY_PREFIX)
            if not namespaces:
                provider = resolve_ats_provider(org, db)
                namespaces.add(
                    BULLHORN_ORG_MUTEX_NAMESPACE
                    if isinstance(provider, BullhornProvider)
                    else _WORKABLE_ORG_MUTEX_KEY_PREFIX
                )
            return tuple(sorted(namespaces))
        finally:
            db.close()
    except Exception:  # pragma: no cover - safe dual-lock fallback
        logger.exception(
            "ATS mutex-namespace resolution failed org_id=%s",
            organization_id,
        )
    try:
        from ..components.integrations.bullhorn.sync_runner import (
            BULLHORN_ORG_MUTEX_NAMESPACE,
        )

        return tuple(
            sorted({_WORKABLE_ORG_MUTEX_KEY_PREFIX, BULLHORN_ORG_MUTEX_NAMESPACE})
        )
    except Exception:
        return (_WORKABLE_ORG_MUTEX_KEY_PREFIX,)


def op_mutex_namespace(organization_id: int, payload: dict | None = None) -> str:
    """Backward-compatible single-namespace view."""

    return op_mutex_namespaces(organization_id, payload)[0]


__all__ = ["op_mutex_namespace", "op_mutex_namespaces"]
