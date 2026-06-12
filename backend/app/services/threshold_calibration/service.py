"""Orchestrate the nightly role_fit threshold calibration.

For each org: learn the org-wide threshold from terminal-outcome labels, then a
per-role threshold (shrunk toward the org anchor) for agentic roles that clear
the sample floor. Each is bias-gated and written as a ``proposed`` (shadow) row
— never read by the engine until activated. Activation is the recruiter's call
in the Decision Hub, or — opt-in per org and only when the bias gate passes on a
configured protected-attribute holdout — automatic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from vendor.mainspring_bias.seam import SegmentMetrics, pairwise_fairness_verdict

from ...decision_policy.audit_examples import load_audit_examples
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.threshold_calibration import (
    STATUS_ACTIVE,
    STATUS_PROPOSED,
    STATUS_SUPERSEDED,
    ThresholdCalibration,
)
from .label_builder import LabelledSet, build_labelled_pairs
from .learner import (
    MIN_CHANGE,
    ThresholdFit,
    clamp_absolute,
    learn_threshold,
    shrink_and_clamp_to_org,
)

logger = logging.getLogger("taali.threshold_calibration")

# Protected attributes we bucket the holdout by (mirror the bias seam).
_PROTECTED = ("gender", "race", "age_band", "nationality", "disability_status", "religion")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _auto_apply_enabled(org: Organization) -> bool:
    settings = org.workspace_settings if isinstance(org.workspace_settings, dict) else None
    return bool((settings or {}).get("decision_policy_auto_apply", False))


def _bias_gate(org: Organization, threshold: float) -> tuple[bool | None, bool, str]:
    """Evaluate the threshold DECISION for disparate impact on the curated
    protected-attribute holdout.

    Returns ``(passed, cold_start, reason)``:
      * cold_start=True (no holdout configured) → ``passed`` is None; the
        proposal can NEVER auto-apply (shadow-only). This is the safe default.
      * holdout present → apply ``advance iff role_fit >= threshold`` per example,
        bucket by segment, run the EEOC 4/5ths + parity verdict (the same
        compliance-owned rule the fitted-model audit uses).
    """
    try:
        examples = list(load_audit_examples(org))
    except Exception as exc:  # pragma: no cover — never break the job
        return None, True, f"audit holdout unavailable ({exc}); shadow-only"
    if not examples:
        return None, True, "no protected-attribute holdout configured (cold start); shadow-only"

    # buckets[attr][segment] = [selected_count, n]
    buckets: dict[str, dict[str, list[int]]] = {a: {} for a in _PROTECTED}
    for ex in examples:
        feats = getattr(ex, "features", {}) or {}
        rf = feats.get("role_fit")
        if rf is None:
            continue
        # holdout role_fit may be on a 0..1 scale; normalise to 0..100 like cv_match.
        rf = float(rf)
        if rf <= 1.0:
            rf *= 100.0
        selected = 1 if rf >= threshold else 0
        segs = getattr(ex, "segments", {}) or {}
        for attr in _PROTECTED:
            seg = segs.get(attr)
            if not seg:
                continue
            b = buckets[attr].setdefault(str(seg), [0, 0])
            b[0] += selected
            b[1] += 1

    metrics_by_attr = {
        attr: [
            SegmentMetrics(segment=seg, n=n, selection_rate=(sel / n if n else 0.0))
            for seg, (sel, n) in segs.items()
        ]
        for attr, segs in buckets.items()
        if segs
    }
    _, violations = pairwise_fairness_verdict(metrics_by_attr=metrics_by_attr)
    if violations:
        kinds = ", ".join(f"{v['attr']}:{v['kind']}" for v in violations[:5])
        return False, False, f"{len(violations)} fairness violation(s): {kinds}"
    return True, False, "passed EEOC 4/5ths + parity"


def _active_threshold(db: Session, *, org_id: int, role_id: int | None) -> float | None:
    q = db.query(ThresholdCalibration).filter(
        ThresholdCalibration.organization_id == org_id,
        ThresholdCalibration.status == STATUS_ACTIVE,
    )
    q = q.filter(ThresholdCalibration.role_id.is_(None) if role_id is None
                 else ThresholdCalibration.role_id == role_id)
    row = q.order_by(ThresholdCalibration.activated_at.desc()).first()
    return float(row.learned_threshold) if row else None


def _scope_filter(query, role_id: int | None):
    return query.filter(ThresholdCalibration.role_id.is_(None) if role_id is None
                        else ThresholdCalibration.role_id == role_id)


def activate(db: Session, row: ThresholdCalibration) -> None:
    """Flip a proposal to active, superseding the prior active row for the same
    (org, role) in the same transaction."""
    now = _now()
    prior = _scope_filter(
        db.query(ThresholdCalibration).filter(
            ThresholdCalibration.organization_id == row.organization_id,
            ThresholdCalibration.status == STATUS_ACTIVE,
            ThresholdCalibration.id != row.id,
        ),
        row.role_id,
    ).all()
    for p in prior:
        p.status = STATUS_SUPERSEDED
        p.superseded_at = now
    row.status = STATUS_ACTIVE
    row.activated_at = now


def _propose(
    db: Session,
    *,
    org: Organization,
    role_id: int | None,
    scope: str,
    threshold: float,
    fit: ThresholdFit,
    labels: LabelledSet,
    window_start,
    auto_apply: bool,
    pooled: bool,
    shrink_w: float | None,
) -> bool:
    """Write a proposal (and maybe activate). Returns True if one was written."""
    # Min-change guard: don't churn when we already have a near-identical active value.
    current = _active_threshold(db, org_id=int(org.id), role_id=role_id)
    if current is not None and abs(threshold - current) < MIN_CHANGE:
        return False

    now = _now()
    # Supersede any prior un-acted proposal for the same (org, role).
    _scope_filter(
        db.query(ThresholdCalibration).filter(
            ThresholdCalibration.organization_id == int(org.id),
            ThresholdCalibration.status == STATUS_PROPOSED,
        ),
        role_id,
    ).update({"status": STATUS_SUPERSEDED, "superseded_at": now}, synchronize_session=False)

    passed, cold_start, reason = _bias_gate(org, threshold)
    row = ThresholdCalibration(
        organization_id=int(org.id),
        role_id=role_id,
        scope=scope,
        learned_threshold=float(round(threshold, 2)),
        metric_name="youden_j",
        metric_value=float(fit.youden_j),
        status=STATUS_PROPOSED,
        n_positive=int(fit.n_positive),
        n_negative=int(fit.n_negative),
        pooled_from_org=bool(pooled),
        prompt_version=labels.prompt_version,
        bias_gate_passed=passed,
        bias_gate_cold_start=cold_start,
        bias_gate_reason=reason,
        metrics_json={
            "balanced_accuracy": round(fit.balanced_accuracy, 3),
            "base_rate": round(fit.base_rate, 4),
            "raw_threshold": fit.threshold,
            "shrink_weight": shrink_w,
            "current_active": current,
            "curve": fit.curve,
        },
        training_window_start=window_start,
        training_window_end=now,
        proposed_at=now,
    )
    db.add(row)
    db.flush()
    if auto_apply and passed is True and not cold_start:
        activate(db, row)
    return True


def run_for_org(db: Session, *, organization_id: int, window_start=None) -> dict:
    org = db.query(Organization).filter(Organization.id == organization_id).one_or_none()
    if org is None:
        return {"org": organization_id, "skipped": "org not found"}
    auto_apply = _auto_apply_enabled(org)
    summary: dict = {"org": organization_id, "org_proposed": False, "roles_proposed": 0, "skipped": []}

    org_labels = build_labelled_pairs(db, organization_id=organization_id)
    org_fit = learn_threshold(org_labels.pairs)
    if org_fit is None:
        summary["skipped"].append(
            f"org below floor (pos={org_labels.n_positive} neg={org_labels.n_negative})"
        )
        return summary

    t_org = clamp_absolute(org_fit.threshold)
    if _propose(db, org=org, role_id=None, scope="org", threshold=t_org, fit=org_fit,
                labels=org_labels, window_start=window_start, auto_apply=auto_apply,
                pooled=False, shrink_w=None):
        summary["org_proposed"] = True

    # Per-role only for agentic roles that clear their own floor; shrink to org.
    roles = (
        db.query(Role)
        .filter(Role.organization_id == organization_id, Role.agentic_mode_enabled.is_(True))
        .all()
    )
    for role in roles:
        rl = build_labelled_pairs(db, organization_id=organization_id, role_id=int(role.id))
        rfit = learn_threshold(rl.pairs)
        if rfit is None:
            continue
        t_final, w = shrink_and_clamp_to_org(rfit.threshold, rl.n_total, t_org)
        if _propose(db, org=org, role_id=int(role.id), scope="role", threshold=t_final,
                    fit=rfit, labels=rl, window_start=window_start, auto_apply=auto_apply,
                    pooled=True, shrink_w=w):
            summary["roles_proposed"] += 1
    return summary


def run_for_all_orgs(db: Session, *, window_start=None) -> dict:
    summary = {"orgs": 0, "org_proposed": 0, "roles_proposed": 0, "errors": 0}
    org_ids = [
        r[0]
        for r in db.query(CandidateApplication.organization_id).distinct().all()
        if r[0] is not None
    ]
    for oid in org_ids:
        try:
            s = run_for_org(db, organization_id=int(oid), window_start=window_start)
            db.commit()
            summary["orgs"] += 1
            if s.get("org_proposed"):
                summary["org_proposed"] += 1
            summary["roles_proposed"] += int(s.get("roles_proposed", 0))
        except Exception:
            db.rollback()
            summary["errors"] += 1
            logger.exception("threshold calibration failed for org=%s", oid)
    return summary
