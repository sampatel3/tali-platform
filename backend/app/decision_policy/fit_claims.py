"""Durable nightly-fit claims and fitted-candidate reuse helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.policy_version import PolicyVersion

# Bump whenever extraction or fit semantics change enough to require a refit.
FIT_CONTRACT_VERSION = "fitted-policy-input-v2"

_UNRESOLVED_FIT_CLAIM_STATES = {
    "fit_started",
    "agentic_provider_call_started",
    "agentic_provider_outcome_unknown",
}


def _policy_version_scope_query(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
):
    query = db.query(PolicyVersion).filter(
        PolicyVersion.organization_id == int(organization_id)
    )
    if role_id is None:
        return query.filter(PolicyVersion.role_id.is_(None))
    return query.filter(PolicyVersion.role_id == int(role_id))


def _equivalent_current_candidate(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    fingerprint: str,
) -> PolicyVersion | None:
    rows = (
        _policy_version_scope_query(
            db,
            organization_id=organization_id,
            role_id=role_id,
        )
        .filter(PolicyVersion.status.in_(("candidate", "shadow", "live")))
        .order_by(PolicyVersion.trained_at.desc(), PolicyVersion.id.desc())
        .all()
    )
    for row in rows:
        metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
        if (
            metrics.get("fit_contract_version") == FIT_CONTRACT_VERSION
            and metrics.get("training_fingerprint") == fingerprint
        ):
            return row
    return None


def _unresolved_fit_claim(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    fingerprint: str,
) -> PolicyVersion | None:
    rows = (
        _policy_version_scope_query(
            db,
            organization_id=organization_id,
            role_id=role_id,
        )
        .filter(PolicyVersion.status == "superseded")
        .order_by(PolicyVersion.trained_at.desc(), PolicyVersion.id.desc())
        .all()
    )
    for row in rows:
        metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
        if (
            metrics.get("fit_contract_version") == FIT_CONTRACT_VERSION
            and metrics.get("training_fingerprint") == fingerprint
            and metrics.get("fit_claim_state") in _UNRESOLVED_FIT_CLAIM_STATES
        ):
            return row
    return None


def _new_fit_claim(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    since: datetime,
    fingerprint: str,
    example_count: int,
    mode: str | None,
) -> PolicyVersion:
    started_at = datetime.now(timezone.utc)
    row = PolicyVersion(
        organization_id=organization_id,
        role_id=role_id,
        model_kind="logistic_pooled",
        model_json={"fit_claim": True},
        metrics_json={
            "fit_contract_version": FIT_CONTRACT_VERSION,
            "training_fingerprint": fingerprint,
            "training_example_count": example_count,
            "activation_status": "dormant_fail_closed",
            "fit_claim_state": (
                "agentic_provider_call_started" if mode == "agentic" else "fit_started"
            ),
            "autoresearch_mode": mode,
        },
        training_window_start=since,
        training_window_end=started_at,
        # This status is excluded from promotion/read paths. The same row is
        # promoted to candidate only after exact post-work finalization.
        status="superseded",
    )
    db.add(row)
    db.flush()
    return row


def _mark_fit_claim(
    db: Session,
    *,
    claim_id: int,
    fingerprint: str,
    state: str,
) -> None:
    db.rollback()
    row = (
        db.query(PolicyVersion)
        .filter(
            PolicyVersion.id == int(claim_id),
            PolicyVersion.status == "superseded",
        )
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        db.rollback()
        return
    metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
    if metrics.get("training_fingerprint") != fingerprint:
        db.rollback()
        return
    row.metrics_json = {**metrics, "fit_claim_state": state}
    db.commit()


def _supersede_pending_candidates(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    keep_id: int,
    superseded_at: datetime,
) -> int:
    """Bound pending nightly output without touching live/manual shadow rows."""
    rows = (
        _policy_version_scope_query(
            db,
            organization_id=organization_id,
            role_id=role_id,
        )
        .filter(
            PolicyVersion.status == "candidate",
            PolicyVersion.id != int(keep_id),
        )
        .all()
    )
    for row in rows:
        row.status = "superseded"
        row.archived_at = superseded_at
    return len(rows)


__all__ = ["FIT_CONTRACT_VERSION"]
