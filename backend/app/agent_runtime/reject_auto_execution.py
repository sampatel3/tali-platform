"""Authority checks for deterministic reject auto-execution."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from ..services.agent_policy_settings import role_shares_ats_application


REJECT_DECISION_TYPES = frozenset({"reject", "skip_assessment_reject"})


def is_deterministic_full_score_reject(
    decision: Any,
    decision_type: str,
) -> bool:
    """Whether a decision is the exact scored-reject contract auto_reject grants."""

    if decision_type not in REJECT_DECISION_TYPES:
        return False
    evidence = getattr(decision, "evidence", None)
    evidence = evidence if isinstance(evidence, dict) else {}
    return bool(
        str(getattr(decision, "model_version", "") or "") == "bulk-deterministic"
        and evidence.get("decision_source") == "policy"
        and evidence.get("decision_stage") == "full_scoring"
        and evidence.get("source") in {"score_time_decision", "bulk_decision"}
    )


def reject_requires_human_confirmation(
    db: Session,
    *,
    role: Role,
    decision: Any,
    decision_type: str,
) -> bool:
    """Return whether a reject falls outside the role's narrow auto grant."""

    if decision_type not in REJECT_DECISION_TYPES:
        return False
    return bool(
        role_shares_ats_application(role, db=db)
        or not is_deterministic_full_score_reject(decision, decision_type)
    )


def assert_reject_auto_execution_allowed(
    db: Session,
    *,
    role: Role,
    decision: Any,
    decision_type: str,
) -> None:
    """Fail closed before an irreversible reject side effect is attempted."""

    if decision_type not in REJECT_DECISION_TYPES:
        return
    if role_shares_ats_application(role, db=db):
        raise ValueError(
            "refusing to auto-execute a rejection for a shared role family "
            "because the ATS application is shared; leave it pending for "
            "recruiter confirmation"
        )
    if not is_deterministic_full_score_reject(decision, decision_type):
        raise ValueError(
            f"refusing to auto-execute irreversible decision_type "
            f"'{decision_type}' — it requires explicit human confirmation "
            f"(TAA-11 / P1-TALI-03). Leave the decision pending for the "
            f"recruiter to approve."
        )


__all__ = [
    "REJECT_DECISION_TYPES",
    "assert_reject_auto_execution_allowed",
    "is_deterministic_full_score_reject",
    "reject_requires_human_confirmation",
]
