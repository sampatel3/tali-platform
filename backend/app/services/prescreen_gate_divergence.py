"""Read-only monitoring for cheap-gate versus full-score divergence."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.prescreen_calibration_sample import PrescreenCalibrationSample
from ..models.role import Role
from ..platform.config import settings


def pre_screen_gate_divergence_report(
    db: Session,
    *,
    organization_id: int | None = None,
) -> dict:
    """Quantify disagreement between the cheap gate and authoritative score."""

    from .auto_threshold_service import compute_role_fit_send_threshold

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            CandidateApplication.cv_match_score.isnot(None),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    both = diverge = false_neg = false_pos = 0
    production_pairs = shadow_reject_pairs = 0
    send_thresholds: dict[int, float] = {}
    seen_application_ids: set[int] = set()

    def _count_pair(
        *,
        app: CandidateApplication,
        role: Role,
        pre_score: float,
        full_score: float,
        shadow: bool,
    ) -> None:
        nonlocal both, diverge, false_neg, false_pos
        nonlocal production_pairs, shadow_reject_pairs
        evidence = (
            app.pre_screen_evidence
            if isinstance(app.pre_screen_evidence, dict)
            else {}
        )
        try:
            gate_threshold = float(evidence.get("gate_threshold_enforced"))
        except (TypeError, ValueError):
            gate_threshold = float(settings.PRE_SCREEN_THRESHOLD)
        org_id = int(app.organization_id)
        if org_id not in send_thresholds:
            send_thresholds[org_id] = float(
                compute_role_fit_send_threshold(db, role=role).value
            )
        send_threshold = send_thresholds[org_id]
        both += 1
        shadow_reject_pairs += int(shadow)
        production_pairs += int(not shadow)
        if abs(pre_score - full_score) > 20:
            diverge += 1
        if pre_score < gate_threshold and full_score >= send_threshold:
            false_neg += 1
        if pre_score >= gate_threshold and full_score < send_threshold:
            false_pos += 1

    for app, role in q.all():
        evidence = (
            app.pre_screen_evidence
            if isinstance(app.pre_screen_evidence, dict)
            else {}
        )
        llm = evidence.get("llm_score_100")
        if llm is None:
            continue
        seen_application_ids.add(int(app.id))
        _count_pair(
            app=app,
            role=role,
            pre_score=float(llm),
            full_score=float(app.cv_match_score),
            shadow=False,
        )

    # Reject-inference pairs observe the region the live gate suppresses. The
    # shadow score never touches CandidateApplication.cv_match_score, so this
    # side table is the only unbiased source for autonomous false negatives.
    shadow_q = (
        db.query(PrescreenCalibrationSample, CandidateApplication, Role)
        .join(
            CandidateApplication,
            CandidateApplication.id == PrescreenCalibrationSample.application_id,
        )
        .join(Role, Role.id == PrescreenCalibrationSample.role_id)
        .filter(
            PrescreenCalibrationSample.scoring_status == "ok",
            PrescreenCalibrationSample.pre_screen_score.isnot(None),
            PrescreenCalibrationSample.full_cv_match_score.isnot(None),
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    )
    if organization_id is not None:
        shadow_q = shadow_q.filter(
            PrescreenCalibrationSample.organization_id == int(organization_id)
        )
    for sample, app, role in shadow_q.all():
        if int(app.id) in seen_application_ids:
            continue
        _count_pair(
            app=app,
            role=role,
            pre_score=float(sample.pre_screen_score),
            full_score=float(sample.full_cv_match_score),
            shadow=True,
        )
    return {
        "both_scored": both,
        "production_pairs": production_pairs,
        "shadow_reject_pairs": shadow_reject_pairs,
        "diverge_gt20": diverge,
        "gate_false_negatives": false_neg,
        "gate_false_positives": false_pos,
        "legacy_gate_threshold_fallback": int(settings.PRE_SCREEN_THRESHOLD),
        "send_thresholds_by_organization": {
            str(org_id): threshold
            for org_id, threshold in sorted(send_thresholds.items())
        },
    }


__all__ = ["pre_screen_gate_divergence_report"]
