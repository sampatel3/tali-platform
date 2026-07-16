"""Live authority and attempt fencing for related-role scoring workers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session


class _RelatedRosterRevoked(RuntimeError):
    """The evaluation no longer belongs to the related role's live roster."""

    def __init__(self, *, code: str, message: str, phase: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.phase = str(phase)


class _RelatedAuthorityRevoked(RuntimeError):
    """The related role no longer authorizes autonomous provider work."""

    def __init__(self, *, phase: str, message: str) -> None:
        super().__init__(message)
        self.phase = str(phase)
        self.message = str(message)


class _RelatedInputsChanged(RuntimeError):
    """The CV or role specification changed during one scoring attempt."""

    def __init__(self, *, phase: str) -> None:
        super().__init__("related-role scoring inputs changed")
        self.phase = str(phase)


class _RelatedAttemptRevoked(RuntimeError):
    """Another worker already replaced or completed this scoring attempt."""


class _RelatedRoleAdmittedMessages:
    """Reserve each related-role provider attempt with live role authority."""

    def __init__(
        self,
        *,
        inner: Any,
        organization_id: int,
        role_id: int,
        evaluation_id: int,
        authority_state: dict[str, bool],
    ) -> None:
        self._inner = inner
        self._organization_id = int(organization_id)
        self._role_id = int(role_id)
        self._evaluation_id = int(evaluation_id)
        self._authority_state = authority_state

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def create(self, **kwargs: Any) -> Any:
        from ..services.pricing_service import Feature
        from ..services.provider_usage_admission import (
            AutomaticProviderAuthorityError,
            release_provider_usage,
            reserve_provider_usage,
            with_credit_reservation,
        )

        metering = dict(kwargs.get("metering") or {})
        feature = metering.get("feature") or Feature.SCORE
        trace_id = str(
            metering.get("trace_id")
            or f"sister-evaluation:{self._evaluation_id}:provider"
        )
        entity_id = str(
            metering.get("entity_id")
            or f"sister_evaluation:{self._evaluation_id}"
        )
        try:
            reservation = reserve_provider_usage(
                organization_id=self._organization_id,
                role_id=self._role_id,
                feature=feature,
                trace_id=trace_id,
                entity_id=entity_id,
                sub_feature="related_role_scoring",
                metadata={
                    **dict(metering.get("metadata") or {}),
                    "admission_source": "related_role_scoring",
                    "sister_evaluation_id": self._evaluation_id,
                },
                require_role_authority=True,
            )
        except AutomaticProviderAuthorityError as exc:
            self._authority_state["revoked"] = True
            raise _RelatedAuthorityRevoked(
                phase="metered_provider_admission",
                message="related role authority changed before provider admission",
            ) from exc

        metering.update(
            {
                "organization_id": self._organization_id,
                "role_id": self._role_id,
                "entity_id": entity_id,
                "trace_id": trace_id,
            }
        )
        kwargs["metering"] = with_credit_reservation(metering, reservation)
        try:
            return self._inner.create(**kwargs)
        except Exception:
            # The real metered wrapper owns ambiguous provider outcomes. This
            # releases only a hold that never reached its started marker.
            release_provider_usage(
                reservation,
                reason="related_role_provider_call_failed",
            )
            raise


class _RelatedRoleAdmittedClient:
    """Narrow client proxy used only by the related-role scoring pipeline."""

    def __init__(
        self,
        *,
        inner: Any,
        organization_id: int,
        role_id: int,
        evaluation_id: int,
    ) -> None:
        self._inner = inner
        self._organization_id = int(organization_id)
        self._role_id = int(role_id)
        self._evaluation_id = int(evaluation_id)
        self._authority_state = {"revoked": False}

    @property
    def messages(self) -> _RelatedRoleAdmittedMessages:
        return _RelatedRoleAdmittedMessages(
            inner=self._inner.messages,
            organization_id=self._organization_id,
            role_id=self._role_id,
            evaluation_id=self._evaluation_id,
            authority_state=self._authority_state,
        )

    def require_unrevoked_admission(self, *, phase: str) -> None:
        if self._authority_state["revoked"]:
            raise _RelatedAuthorityRevoked(
                phase=phase,
                message="related role authority changed before provider admission",
            )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)


def _attempt_timestamp(value: datetime | None) -> datetime | None:
    """Normalize SQLite-naive and PostgreSQL-aware attempt timestamps."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _matches_attempt(
    evaluation,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    expected_spec_fingerprint: str,
    expected_cv_fingerprint: str,
    expected_started_at: datetime,
) -> bool:
    from ..models.sister_role_evaluation import SISTER_EVAL_RUNNING

    return bool(
        evaluation.status == SISTER_EVAL_RUNNING
        and int(evaluation.organization_id) == int(organization_id)
        and int(evaluation.role_id) == int(role_id)
        and int(evaluation.source_application_id) == int(application_id)
        and str(evaluation.spec_fingerprint or "")
        == str(expected_spec_fingerprint)
        and str(evaluation.cv_fingerprint or "") == str(expected_cv_fingerprint)
        and _attempt_timestamp(evaluation.started_at)
        == _attempt_timestamp(expected_started_at)
    )


def _lock_matching_related_scoring_attempt(
    db: Session,
    *,
    evaluation_id: int,
    organization_id: int,
    role_id: int,
    application_id: int,
    expected_spec_fingerprint: str,
    expected_cv_fingerprint: str,
    expected_started_at: datetime,
):
    """Lock one row and return it only when it is still this worker's attempt."""

    from ..models.sister_role_evaluation import SisterRoleEvaluation

    db.expire_all()
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.id == int(evaluation_id))
        .with_for_update(of=SisterRoleEvaluation)
        .populate_existing()
        .one_or_none()
    )
    current_status = str(evaluation.status) if evaluation is not None else "missing"
    if evaluation is None or not _matches_attempt(
        evaluation,
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        expected_spec_fingerprint=expected_spec_fingerprint,
        expected_cv_fingerprint=expected_cv_fingerprint,
        expected_started_at=expected_started_at,
    ):
        return None, current_status
    return evaluation, current_status


def _require_live_related_scoring_scope(
    db: Session,
    *,
    evaluation_id: int,
    organization_id: int,
    role_id: int,
    application_id: int,
    expected_spec_fingerprint: str,
    expected_cv_fingerprint: str,
    expected_started_at: datetime,
    phase: str,
    lock_for_update: bool = False,
):
    """Re-read every revocable scope immediately before paid work or save."""

    from ..models.sister_role_evaluation import SisterRoleEvaluation
    from ..services.job_page_lifecycle import role_allows_new_paid_ats_work
    from ..services.related_role_roster import (
        RELATED_ROSTER_EXCLUSION_CODE,
        related_source_application_is_live,
    )
    from ..services.sister_role_service import (
        application_cv_text,
        source_application_is_globally_closed,
        text_fingerprint,
    )

    if db.in_transaction():
        db.rollback()
    db.expire_all()
    evaluation_query = db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.id == int(evaluation_id)
    )
    if lock_for_update:
        evaluation_query = evaluation_query.with_for_update(
            of=SisterRoleEvaluation
        )
    evaluation = evaluation_query.populate_existing().one_or_none()
    if evaluation is None:
        raise _RelatedRosterRevoked(
            code=RELATED_ROSTER_EXCLUSION_CODE,
            message="Related-role evaluation is no longer available",
            phase=phase,
        )
    if not _matches_attempt(
        evaluation,
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        expected_spec_fingerprint=expected_spec_fingerprint,
        expected_cv_fingerprint=expected_cv_fingerprint,
        expected_started_at=expected_started_at,
    ):
        raise _RelatedAttemptRevoked()

    role = evaluation.role
    application = evaluation.source_application
    if (
        role is None
        or int(role.organization_id) != int(organization_id)
        or not related_source_application_is_live(role, application)
    ):
        raise _RelatedRosterRevoked(
            code=RELATED_ROSTER_EXCLUSION_CODE,
            message="Source application left the owner roster",
            phase=phase,
        )
    if source_application_is_globally_closed(application):
        raise _RelatedRosterRevoked(
            code="shared_application_closed",
            message="Shared ATS application is disqualified or closed",
            phase=phase,
        )
    if not role_allows_new_paid_ats_work(role, db=db):
        raise _RelatedAuthorityRevoked(
            phase=phase,
            message="related role no longer authorizes paid ATS work",
        )

    current_cv_text = application_cv_text(application)
    current_job_spec = (role.job_spec_text or "").strip()
    if text_fingerprint(current_job_spec) != str(
        expected_spec_fingerprint
    ) or text_fingerprint(current_cv_text) != str(expected_cv_fingerprint):
        raise _RelatedInputsChanged(phase=phase)
    return evaluation, role, application, current_cv_text, current_job_spec


__all__ = [
    "_RelatedAttemptRevoked",
    "_RelatedAuthorityRevoked",
    "_RelatedInputsChanged",
    "_RelatedRoleAdmittedClient",
    "_RelatedRoleAdmittedMessages",
    "_RelatedRosterRevoked",
    "_lock_matching_related_scoring_attempt",
    "_require_live_related_scoring_scope",
]
