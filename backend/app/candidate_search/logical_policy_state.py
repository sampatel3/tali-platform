"""Canonical role-local candidate truth for deterministic policy learning.

Policy feedback, nightly fitting, and threshold calibration all learn from the
same logical candidate memberships exposed by candidate search.  A related
role's ``SisterRoleEvaluation`` owns its score, pipeline, and outcome; the
physical ``CandidateApplication`` remains evidence and optional ATS transport.
This module projects that boundary once so learning code cannot accidentally
train on the ATS owner's judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .logical_application_scope import (
    LogicalMembershipKey,
    resolve_logical_application_selection,
)
from .role_scope import (
    RelatedRoleSearchApplication,
    hydrate_logical_candidate_rows,
)


LogicalPolicyApplication = CandidateApplication | RelatedRoleSearchApplication
LogicalCandidateKey = tuple[int, int]


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class LogicalCandidatePolicyMetrics:
    """Role-local scores and state with no physical-row authority leakage."""

    role_id: int
    application_id: int
    candidate_id: int
    cv_match_score: float | None
    role_fit_score: float | None
    pre_screen_score: float | None
    assessment_score: float | None
    taali_score: float | None
    pipeline_stage: str
    application_outcome: str
    local_external_stage: str | None
    local_disqualified: bool
    prompt_version: str | None

    @property
    def key(self) -> LogicalMembershipKey:
        return (self.role_id, self.application_id)

    @property
    def candidate_key(self) -> LogicalCandidateKey:
        """Stable logical subject, independent of its current evidence row."""

        return (self.role_id, self.candidate_id)

    @property
    def decision_scores(self) -> dict[str, float]:
        """Score vector accepted by :class:`decision_policy.DecisionInputs`."""

        values = {
            "role_fit_score": self.role_fit_score,
            "pre_screen_score": self.pre_screen_score,
            "assessment_score": self.assessment_score,
            "taali_score": self.taali_score,
        }
        return {name: value for name, value in values.items() if value is not None}


@dataclass(frozen=True)
class LogicalCandidatePolicyState(LogicalCandidatePolicyMetrics):
    """Policy metrics plus the canonical hydrated logical application."""

    application: LogicalPolicyApplication


def project_logical_candidate_policy_state(
    application: LogicalPolicyApplication,
) -> LogicalCandidatePolicyState:
    """Project one already-authorized logical candidate row.

    Provider stage/disqualification is local decision evidence only for an
    ordinary role whose application is itself the membership.  A related-role
    projection deliberately exposes no such learning label: its linked ATS row
    can restrict a future write but cannot become this role's outcome.
    """

    is_related = isinstance(application, RelatedRoleSearchApplication)
    source = application.source_application if is_related else application
    details = getattr(application, "cv_match_details", None)
    prompt_version = (
        getattr(application.evaluation, "prompt_version", None)
        if is_related and application.evaluation is not None
        else None
    )
    if prompt_version is None and isinstance(details, dict):
        prompt_version = details.get("prompt_version")
    role_fit_score = _number(
        getattr(application, "role_fit_score_cache_100", None)
    )
    if role_fit_score is None and isinstance(details, dict):
        role_fit_score = _number(details.get("role_fit_score"))
    cv_match_score = _number(getattr(application, "cv_match_score", None))
    if role_fit_score is None:
        role_fit_score = cv_match_score
    return LogicalCandidatePolicyState(
        role_id=int(application.role_id),
        application_id=int(application.id),
        candidate_id=int(application.candidate_id),
        cv_match_score=cv_match_score,
        role_fit_score=role_fit_score,
        pre_screen_score=_number(
            getattr(application, "pre_screen_score_100", None)
        ),
        assessment_score=_number(
            getattr(application, "assessment_score_cache_100", None)
        ),
        taali_score=_number(getattr(application, "taali_score_cache_100", None)),
        pipeline_stage=str(
            getattr(application, "pipeline_stage", None) or "applied"
        ).strip().lower(),
        application_outcome=str(
            getattr(application, "application_outcome", None) or "open"
        ).strip().lower(),
        local_external_stage=(
            None
            if is_related
            else str(getattr(source, "workable_stage", None) or "").strip() or None
        ),
        local_disqualified=(
            False if is_related else bool(getattr(source, "workable_disqualified", False))
        ),
        prompt_version=(str(prompt_version) if prompt_version else None),
        application=application,
    )


def _policy_membership_query(
    db: Session,
    *,
    organization_id: int,
    role_ids: Iterable[int],
    membership_keys: Iterable[LogicalMembershipKey] | None,
    candidate_keys: Iterable[LogicalCandidateKey] | None,
):
    if membership_keys is not None and candidate_keys is not None:
        raise ValueError("membership_keys and candidate_keys are mutually exclusive")
    requested_keys = (
        tuple(
            dict.fromkeys(
                (int(role_id), int(application_id))
                for role_id, application_id in membership_keys
            )
        )
        if membership_keys is not None
        else None
    )
    requested_candidate_keys = (
        tuple(
            dict.fromkeys(
                (int(role_id), int(candidate_id))
                for role_id, candidate_id in candidate_keys
            )
        )
        if candidate_keys is not None
        else None
    )
    requested_role_ids = tuple(sorted({int(role_id) for role_id in role_ids}))
    if requested_keys is not None:
        if not requested_keys:
            return None, None, (), ()
        keyed_role_ids = {role_id for role_id, _ in requested_keys}
        requested_role_ids = tuple(sorted(set(requested_role_ids) | keyed_role_ids))
    if requested_candidate_keys is not None:
        if not requested_candidate_keys:
            return None, None, (), ()
        keyed_role_ids = {role_id for role_id, _ in requested_candidate_keys}
        requested_role_ids = tuple(sorted(set(requested_role_ids) | keyed_role_ids))

    selection = resolve_logical_application_selection(
        db,
        organization_id=int(organization_id),
        role_ids=requested_role_ids,
    )
    if not selection.active:
        return None, None, (), ()
    query = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == int(organization_id)
    )
    query = selection.apply_membership(query)
    if requested_keys is not None:
        query = query.filter(
            CandidateApplication.id.in_(
                sorted({application_id for _, application_id in requested_keys})
            )
        )
    if requested_candidate_keys is not None:
        query = query.filter(
            CandidateApplication.candidate_id.in_(
                sorted({candidate_id for _, candidate_id in requested_candidate_keys})
            )
        )
    return selection, query, requested_keys, requested_candidate_keys


def read_logical_candidate_policy_metrics(
    db: Session,
    *,
    organization_id: int,
    role_ids: Iterable[int] = (),
    membership_keys: Iterable[LogicalMembershipKey] | None = None,
    candidate_keys: Iterable[LogicalCandidateKey] | None = None,
) -> tuple[LogicalCandidatePolicyMetrics, ...]:
    """Read role-local policy fields in one SQL query without ORM hydration.

    This is the hot path used by runtime threshold calculation and nightly
    labels. It still uses the canonical membership union and score/state
    expressions, but avoids loading CV blobs, relationships, and per-role
    assessment maps when callers need only scalar calibration truth.
    ``candidate_keys`` addresses durable role/candidate subjects even when
    their current membership uses a different physical evidence row.
    """

    selection, query, requested_keys, requested_candidate_keys = (
        _policy_membership_query(
            db,
            organization_id=int(organization_id),
            role_ids=role_ids,
            membership_keys=membership_keys,
            candidate_keys=candidate_keys,
        )
    )
    if selection is None or query is None:
        return ()
    logical_role_id = selection.logical_role_id_expression()
    rows = query.with_entities(
        logical_role_id.label("logical_role_id"),
        CandidateApplication.id.label("application_id"),
        CandidateApplication.candidate_id.label("candidate_id"),
        selection.score_expression("cv_match_score").label("cv_match_score"),
        selection.score_expression("role_fit_score_cache_100").label(
            "role_fit_score"
        ),
        selection.score_expression("pre_screen_score_100").label(
            "pre_screen_score"
        ),
        selection.score_expression("assessment_score_cache_100").label(
            "assessment_score"
        ),
        selection.score_expression("taali_score_cache_100").label("taali_score"),
        selection.pipeline_stage_expression().label("pipeline_stage"),
        selection.application_outcome_expression().label("application_outcome"),
        CandidateApplication.workable_stage.label("source_external_stage"),
        CandidateApplication.workable_disqualified.label("source_disqualified"),
    ).all()
    requested_key_set = set(requested_keys) if requested_keys is not None else None
    requested_candidate_key_set = (
        set(requested_candidate_keys)
        if requested_candidate_keys is not None
        else None
    )
    related_role_ids = set(selection.related_role_ids)
    metrics: list[LogicalCandidatePolicyMetrics] = []
    for row in rows:
        role_id = int(row.logical_role_id)
        key = (role_id, int(row.application_id))
        if requested_key_set is not None and key not in requested_key_set:
            continue
        candidate_key = (role_id, int(row.candidate_id))
        if (
            requested_candidate_key_set is not None
            and candidate_key not in requested_candidate_key_set
        ):
            continue
        is_related = role_id in related_role_ids
        metrics.append(
            LogicalCandidatePolicyMetrics(
                role_id=role_id,
                application_id=int(row.application_id),
                candidate_id=int(row.candidate_id),
                cv_match_score=_number(row.cv_match_score),
                role_fit_score=_number(row.role_fit_score),
                pre_screen_score=_number(row.pre_screen_score),
                assessment_score=_number(row.assessment_score),
                taali_score=_number(row.taali_score),
                pipeline_stage=str(row.pipeline_stage or "applied").strip().lower(),
                application_outcome=str(
                    row.application_outcome or "open"
                ).strip().lower(),
                local_external_stage=(
                    None
                    if is_related
                    else str(row.source_external_stage or "").strip() or None
                ),
                local_disqualified=(
                    False if is_related else bool(row.source_disqualified)
                ),
                prompt_version=None,
            )
        )
    return tuple(sorted(metrics, key=lambda item: item.key))


def read_logical_candidate_policy_states(
    db: Session,
    *,
    organization_id: int,
    role_ids: Iterable[int] = (),
    membership_keys: Iterable[LogicalMembershipKey] | None = None,
    candidate_keys: Iterable[LogicalCandidateKey] | None = None,
) -> tuple[LogicalCandidatePolicyState, ...]:
    """Read active memberships through the canonical selection/projection path.

    ``membership_keys`` and ``candidate_keys`` are mutually exclusive optional
    exact subsets. Missing or deleted memberships are omitted rather than
    replaced with their physical ATS row. The returned order is deterministic
    by logical role then application.
    """

    selection, query, requested_keys, requested_candidate_keys = (
        _policy_membership_query(
            db,
            organization_id=int(organization_id),
            role_ids=role_ids,
            membership_keys=membership_keys,
            candidate_keys=candidate_keys,
        )
    )
    if selection is None or query is None:
        return ()
    logical_role_id = selection.logical_role_id_expression()
    rows = query.with_entities(
        CandidateApplication.id,
        CandidateApplication.candidate_id,
        logical_role_id,
    ).all()
    keys = sorted(
        {
            (int(row.logical_role_id), int(row.id))
            for row in rows
        }
    )
    if requested_keys is not None:
        requested_key_set = set(requested_keys)
        keys = [key for key in keys if key in requested_key_set]
    if requested_candidate_keys is not None:
        requested_candidate_key_set = set(requested_candidate_keys)
        allowed_membership_keys = {
            (int(row.logical_role_id), int(row.id))
            for row in rows
            if (int(row.logical_role_id), int(row.candidate_id))
            in requested_candidate_key_set
        }
        keys = [key for key in keys if key in allowed_membership_keys]
    if not keys:
        return ()

    applications = hydrate_logical_candidate_rows(
        db,
        selection=selection,
        keys=keys,
    )
    return tuple(project_logical_candidate_policy_state(row) for row in applications)


__all__ = [
    "LogicalCandidateKey",
    "LogicalCandidatePolicyMetrics",
    "LogicalCandidatePolicyState",
    "project_logical_candidate_policy_state",
    "read_logical_candidate_policy_metrics",
    "read_logical_candidate_policy_states",
]
