"""SQL predicates mirroring related-role decision de-duplication semantics."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, Float, and_, case, cast, func, or_
from sqlalchemy.orm import Session, aliased

from ..cv_matching.holistic import HOLISTIC_ENGINE_VERSION
from ..models.agent_decision import AgentDecision
from ..models.assessment import AssessmentStatus
from ..models.role import Role
from ..models.role_feedback_note import RoleFeedbackNote
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .decision_staleness import SCORE_DRIFT_BAND


# Every floating-point value read through this helper is a score or threshold.
# Bound both halves of its decimal representation before casting: a regex that
# accepts arbitrary digit counts or exponents still lets values such as
# ``1e1000000`` reach PostgreSQL's double-precision cast and abort the query.
# Normal snapshots are emitted as plain JSON numbers, so rejecting exponent
# notation and values with more than 16 digits on either side is a conservative
# historic-data fallback rather than a loss of a supported representation.
_JSON_FLOAT_PATTERN = r"^[+-]?(?:[0-9]{1,16}(?:\.[0-9]{0,16})?|\.[0-9]{1,16})$"

# Related-role assessment/evaluation/note identifiers are PostgreSQL INTEGER
# columns. Ten magnitude digits cover that domain; casting the validated value
# to BIGINT first also keeps out-of-domain ten-digit values non-raising so their
# later comparison simply fails.
_JSON_INTEGER_PATTERN = r"^[+-]?[0-9]{1,10}$"


def _safe_json_number(value, *, integer: bool = False):
    """Cast a JSON scalar only when its text is a bounded finite number.

    PostgreSQL raises for malformed JSON strings such as ``"unknown"`` when
    an ``as_float``/``as_integer`` expression is evaluated.  These audit
    snapshots are deliberately tolerant at the Python boundary, so the
    role-wide selector must also treat malformed historic values as absent
    instead of failing the whole batch.  SQLAlchemy's regex operator compiles
    to PostgreSQL ``~`` and SQLite ``REGEXP``.
    """

    text = func.trim(value.as_string())
    pattern = _JSON_INTEGER_PATTERN if integer else _JSON_FLOAT_PATTERN
    numeric_type = BigInteger if integer else Float
    return case(
        (text.regexp_match(pattern), cast(text, numeric_type)),
        else_=None,
    )


def _text_fingerprint(value: object) -> str | None:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def _optional_snapshot_matches(stored, current):
    """Match the staleness service's legacy-friendly optional comparison."""

    return or_(
        func.coalesce(stored, "") == "",
        func.coalesce(current, "") == "",
        stored == current,
    )


def _score_drift_is_below_band(stored, current):
    return or_(
        stored.is_(None),
        current.is_(None),
        func.abs(stored - current) < float(SCORE_DRIFT_BAND),
    )


def _engine_is_current_or_unknown():
    details = SisterRoleEvaluation.details
    engine = details["engine_version"].as_string()
    prompt = func.coalesce(details["prompt_version"].as_string(), "")
    return or_(
        engine == HOLISTIC_ENGINE_VERSION,
        and_(
            func.coalesce(engine, "") == "",
            prompt != "holistic_v1",
            ~prompt.like("cv_match_v%"),
        ),
    )


def related_role_decision_is_suppressed(
    db: Session,
    *,
    role: Role,
    threshold: float,
    criteria_fingerprint: str | None,
    expected_decision_type,
    current_decision_score,
    current_assessment,
    current_assessment_score,
):
    """Return an EXISTS predicate equivalent to ``queue_decision`` guards.

    Approved decisions use the seven-day, five-point-bucket de-duplication
    window. Discarded/overridden decisions suppress only an explicit human
    resolution whose related-role inputs are not stale. System discards are
    deliberately never suppressing.
    """

    approved_floor = datetime.now(timezone.utc) - timedelta(days=7)
    current_bucket = func.floor(current_decision_score / 5.0)
    approved_threshold = _safe_json_number(
        AgentDecision.evidence["effective_threshold"]
    )
    approved_score = _safe_json_number(AgentDecision.evidence["taali_score"])
    approved = (
        db.query(AgentDecision.id)
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == SisterRoleEvaluation.source_application_id,
            AgentDecision.status == "approved",
            AgentDecision.resolved_at.is_not(None),
            AgentDecision.resolved_at >= approved_floor,
            AgentDecision.decision_type == expected_decision_type,
            AgentDecision.decision_dedup_key.is_not(None),
            func.coalesce(AgentDecision.criteria_fingerprint, "")
            == str(criteria_fingerprint or ""),
            func.coalesce(
                AgentDecision.evidence["evaluation_cv_fingerprint"].as_string(),
                "",
            )
            == func.coalesce(SisterRoleEvaluation.cv_fingerprint, ""),
            approved_threshold == float(threshold),
            func.floor(approved_score / 5.0) == current_bucket,
        )
        .exists()
    )

    human = aliased(AgentDecision, name="related_runtime_human_decision")
    latest_human = aliased(AgentDecision, name="related_runtime_latest_human_decision")
    latest_human_id = (
        db.query(latest_human.id)
        .filter(
            latest_human.organization_id == int(role.organization_id),
            latest_human.role_id == int(role.id),
            latest_human.application_id == SisterRoleEvaluation.source_application_id,
            latest_human.status.in_(("discarded", "overridden")),
            latest_human.resolved_by_user_id.is_not(None),
            latest_human.decision_type == expected_decision_type,
        )
        .order_by(
            latest_human.resolved_at.desc().nullslast(),
            latest_human.id.desc(),
        )
        .limit(1)
        .correlate(SisterRoleEvaluation, current_assessment)
        .scalar_subquery()
    )
    latest_note_id = (
        db.query(func.max(RoleFeedbackNote.id))
        .filter(
            RoleFeedbackNote.organization_id == int(role.organization_id),
            RoleFeedbackNote.role_id == int(role.id),
        )
        .scalar_subquery()
    )
    stored_note_id = _safe_json_number(
        human.input_fingerprint["last_recruiter_note_id"], integer=True
    )
    stored_assessment_id = _safe_json_number(
        human.evidence["assessment_id"], integer=True
    )
    stored_assessment_score = _safe_json_number(human.evidence["assessment_score"])
    stored_evaluation_id_text = human.evidence[
        "sister_evaluation_id"
    ].as_string()
    stored_evaluation_id = _safe_json_number(
        human.evidence["sister_evaluation_id"], integer=True
    )
    fingerprint_score_text = human.input_fingerprint[
        "cv_match_score_at_emit"
    ].as_string()
    stored_threshold = _safe_json_number(human.evidence["effective_threshold"])
    stored_role_fit_score = _safe_json_number(human.evidence["role_fit_score"])
    same_assessment = or_(
        and_(
            stored_assessment_id.is_(None),
            current_assessment.id.is_(None),
        ),
        and_(
            stored_assessment_id == current_assessment.id,
            current_assessment.status.in_(
                (
                    AssessmentStatus.COMPLETED,
                    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                )
            ),
            _score_drift_is_below_band(
                stored_assessment_score,
                current_assessment_score,
            ),
        ),
    )
    current_role_spec_fingerprint = _text_fingerprint(role.job_spec_text)
    fingerprinted_human_is_current = and_(
        fingerprint_score_text.is_not(None),
        or_(
            stored_evaluation_id_text.is_(None),
            stored_evaluation_id == SisterRoleEvaluation.id,
        ),
        _optional_snapshot_matches(
            human.evidence["evaluation_spec_fingerprint"].as_string(),
            SisterRoleEvaluation.spec_fingerprint,
        ),
        _optional_snapshot_matches(
            human.evidence["evaluation_spec_fingerprint"].as_string(),
            current_role_spec_fingerprint,
        ),
        _optional_snapshot_matches(
            human.evidence["evaluation_cv_fingerprint"].as_string(),
            SisterRoleEvaluation.cv_fingerprint,
        ),
        func.coalesce(human.criteria_fingerprint, "")
        == str(criteria_fingerprint or ""),
        or_(
            latest_note_id.is_(None),
            and_(stored_note_id.is_not(None), stored_note_id >= latest_note_id),
        ),
        or_(
            stored_threshold.is_(None),
            stored_threshold == float(threshold),
        ),
        _score_drift_is_below_band(
            stored_role_fit_score,
            SisterRoleEvaluation.role_fit_score,
        ),
        same_assessment,
        _engine_is_current_or_unknown(),
    )
    legacy_floor = datetime.now(timezone.utc) - timedelta(minutes=10)
    recent_legacy_human = and_(
        fingerprint_score_text.is_(None),
        human.resolved_at.is_not(None),
        human.resolved_at >= legacy_floor,
    )
    human_suppressed = (
        db.query(human.id)
        .filter(
            human.id == latest_human_id,
            or_(fingerprinted_human_is_current, recent_legacy_human),
        )
        .exists()
    )
    return or_(approved, human_suppressed)


__all__ = ["related_role_decision_is_suppressed"]
