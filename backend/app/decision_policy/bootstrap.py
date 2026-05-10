"""Seed the org-default DecisionPolicy from existing implicit thresholds.

The current pre-policy agent code has hardcoded numbers scattered
across orchestrator + tool registry. Bootstrap rolls them up into one
``policy_json`` per org so day-one behavior matches what recruiters
already see, then the retune loop takes over from there.

Idempotent: running ``bootstrap_org`` twice produces the same row.
``bootstrap_all_orgs_via_connection`` is the migration-time variant
that runs against a raw alembic connection (the SQLAlchemy ORM session
isn't available inside ``op.get_bind()``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ..models.decision_policy import DecisionPolicy
from ..models.organization import Organization
from ..models.role import Role
from ..models.rubric_revision import RubricRevision
from .schema import PolicyJson


logger = logging.getLogger("taali.decision_policy.bootstrap")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


# Defaults are anchored to the implicit numbers the agent uses today:
#   - Pre-screen "yes" cutoff: score >= 50 (see runner_pre_screen.py).
#   - Score threshold: comes off ``Role.score_threshold``; org default
#     falls back to 65 when neither is set.
#   - Reject floor: scores in the bottom decile (≤30) with no graph
#     prior support.
# Phase 5 adjusts these from data; this is just the cold-start.
DEFAULT_ORG_FALLBACK_SCORE_THRESHOLD = 65.0


def _default_policy_json(*, role_fit_min: float) -> dict[str, Any]:
    """Build the cold-start policy_json for a given role_fit_min."""
    role_fit_min = float(max(0.0, min(100.0, role_fit_min)))
    # Reject ceiling sits well below the send threshold so a candidate
    # in the gap (e.g. 35–50) just doesn't get an agent verdict — it
    # falls to the recruiter naturally.
    role_fit_reject_ceiling = max(0.0, min(100.0, min(30.0, role_fit_min - 25.0)))
    return {
        "schema_version": "v1",
        "decision_points": {
            "send_assessment": {
                "thresholds": {
                    "role_fit_min": role_fit_min,
                    "pre_screen_min": 50.0,
                },
                "weights": {
                    "role_fit_score": 0.7,
                    "pre_screen_score": 0.3,
                },
                "rules": [
                    {
                        "if": "must_have_blocked",
                        "then": "auto_reject",
                        "priority": 100,
                        "reason_template": (
                            "Candidate fails a must-have requirement; auto-reject "
                            "without sending assessment."
                        ),
                    },
                    {
                        "if": "has_pending_assessment",
                        "then": "skip",
                        "priority": 90,
                        "reason_template": (
                            "Assessment already in flight for this candidate; not "
                            "sending another."
                        ),
                    },
                    {
                        "if": "assessment_completed",
                        "then": "skip",
                        "priority": 85,
                        "reason_template": (
                            "Candidate has already completed an assessment — "
                            "advance / reject decisions take it from here."
                        ),
                    },
                    {
                        "if": "role_fit_score >= role_fit_min AND pre_screen_score >= pre_screen_min",
                        "then": "queue_send_assessment",
                        "priority": 50,
                        "reason_template": (
                            "Role-fit and pre-screen both clear send-assessment "
                            "thresholds — queueing for recruiter approval."
                        ),
                    },
                ],
                "confidence_floor": 0.5,
            },
            "advance_to_interview": {
                "thresholds": {
                    "taali_score_min": max(60.0, role_fit_min - 5.0),
                    "assessment_score_min": 50.0,
                },
                "weights": {
                    "taali_score": 0.7,
                    "assessment_score": 0.3,
                },
                "rules": [
                    {
                        "if": "taali_score >= taali_score_min AND assessment_completed",
                        "then": "queue_advance_decision",
                        "priority": 50,
                        "reason_template": (
                            "TAALI score clears advance threshold and assessment "
                            "is complete — queueing advance for recruiter approval."
                        ),
                    },
                ],
                "confidence_floor": 0.6,
            },
            "reject": {
                "thresholds": {
                    "role_fit_max": role_fit_reject_ceiling,
                },
                "weights": {
                    "role_fit_score": 1.0,
                },
                "rules": [
                    {
                        # Pre-screen-stage auto-reject. The eligibility
                        # flag is computed by the caller (Celery
                        # auto-reject task or the wrapper) since it
                        # combines several preconditions — score below
                        # the role's per-role threshold, application
                        # outcome still open, Workable link present, no
                        # assessment in flight. Routing the verdict
                        # through the engine keeps the policy as the
                        # single source of truth even though the gate
                        # logic lives in Python.
                        "if": "pre_screen_auto_reject_eligible",
                        "then": "auto_reject",
                        "priority": 70,
                        "reason_template": (
                            "Pre-screen score below the configured threshold; "
                            "auto-rejecting at pre-screen stage."
                        ),
                    },
                    {
                        "if": "role_fit_score <= role_fit_max AND no_pending_assessment",
                        "then": "queue_reject_decision",
                        "priority": 50,
                        "reason_template": (
                            "Role-fit far below the send-assessment floor and no "
                            "assessment pending — queueing reject."
                        ),
                    },
                ],
                "confidence_floor": 0.6,
            },
        },
        "graph_prior_config": {
            "enabled": True,
            "neighbourhood_size": 20,
            "min_neighbours_for_prior": 5,
            "decay_days": 365,
        },
        "intent_overrides": {
            "honor_strictness_modifiers": True,
            "max_threshold_shift": 20.0,
        },
        "manual_action_window": {
            "lookback_hours": 72,
            "skip_decision_types_on_recent_manual": [
                "send_assessment",
                "advance_to_interview",
                "reject",
            ],
        },
        "metadata": {
            "trained_from_feedback_ids": [],
            "trained_from_manual_decision_count": 0,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "notes": "Bootstrapped from existing implicit thresholds.",
        },
    }


def _resolve_role_fit_min(db: Session, org: Organization) -> float:
    """Pick a role_fit_min for the org default policy.

    Heuristic: median of ``Role.score_threshold`` across the org's
    roles, falling back to ``Organization.default_score_threshold``,
    then to ``DEFAULT_ORG_FALLBACK_SCORE_THRESHOLD``. The aim is to
    have the cold-start policy roughly match what recruiters already
    see today rather than introduce a new number.
    """
    thresholds = (
        db.query(Role.score_threshold)
        .filter(
            Role.organization_id == org.id,
            Role.score_threshold.isnot(None),
            Role.deleted_at.is_(None),
        )
        .all()
    )
    values = [float(t[0]) for t in thresholds if t[0] is not None]
    if values:
        values.sort()
        mid = values[len(values) // 2]
        return mid
    if org.default_score_threshold is not None:
        return float(org.default_score_threshold)
    return DEFAULT_ORG_FALLBACK_SCORE_THRESHOLD


# ---------------------------------------------------------------------------
# ORM-flavoured bootstrap (used at runtime + tests)
# ---------------------------------------------------------------------------


def bootstrap_org(db: Session, *, organization_id: int) -> DecisionPolicy:
    """Idempotent: returns the existing org-default row if one exists.

    Otherwise creates a ``RubricRevision`` (cause='bootstrap') and a
    paired ``DecisionPolicy`` row with ``activated_at=now()``.
    """
    existing = (
        db.query(DecisionPolicy)
        .filter(
            DecisionPolicy.organization_id == organization_id,
            DecisionPolicy.role_id.is_(None),
            DecisionPolicy.deactivated_at.is_(None),
        )
        .order_by(DecisionPolicy.created_at.asc())
        .first()
    )
    if existing is not None:
        return existing

    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .one_or_none()
    )
    if org is None:
        raise LookupError(f"organization_id={organization_id} not found")

    role_fit_min = _resolve_role_fit_min(db, org)
    policy_dict = _default_policy_json(role_fit_min=role_fit_min)
    # Validate before write — fail loud if our defaults ever drift
    # out of schema.
    PolicyJson.model_validate(policy_dict)

    revision = RubricRevision(
        organization_id=organization_id,
        role_id=None,
        # ``cause`` is intentionally set to ``human_edit`` here because
        # the existing REVISION_CAUSES tuple doesn't include
        # 'bootstrap' — we'd need a migration to extend it. The notes
        # field carries the disambiguation. Phase 5 widens
        # REVISION_CAUSES if/when needed.
        cause="human_edit",
        feedback_ids=[],
        weights_diff=None,
        threshold_diff=None,
        notes="bootstrap: cold-start org-default policy",
    )
    db.add(revision)
    db.flush()  # populate revision.id

    now = datetime.now(timezone.utc)
    policy = DecisionPolicy(
        organization_id=organization_id,
        role_id=None,
        revision_id=int(revision.id),
        policy_json=policy_dict,
        activated_at=now,
        deactivated_at=None,
    )
    db.add(policy)
    db.flush()
    logger.info(
        "Bootstrapped org-default decision policy: org_id=%s revision_id=%s policy_id=%s",
        organization_id, int(revision.id), int(policy.id),
    )
    return policy


def bootstrap_all_orgs(db: Session) -> int:
    """Bootstrap every existing org. Returns count of newly-created rows."""
    org_ids = [int(row[0]) for row in db.query(Organization.id).all()]
    created = 0
    for org_id in org_ids:
        before = (
            db.query(DecisionPolicy)
            .filter(
                DecisionPolicy.organization_id == org_id,
                DecisionPolicy.role_id.is_(None),
            )
            .count()
        )
        bootstrap_org(db, organization_id=org_id)
        after = (
            db.query(DecisionPolicy)
            .filter(
                DecisionPolicy.organization_id == org_id,
                DecisionPolicy.role_id.is_(None),
            )
            .count()
        )
        if after > before:
            created += 1
    return created


# ---------------------------------------------------------------------------
# Connection-flavoured bootstrap (used inside the alembic migration)
# ---------------------------------------------------------------------------


def bootstrap_all_orgs_via_connection(connection: Connection) -> int:
    """Migration-time variant.

    Runs raw SQL against the bound alembic connection so we don't need
    a SQLAlchemy session. Same idempotency guarantees as the ORM
    variant.
    """
    from sqlalchemy import text

    org_rows = list(connection.execute(text("SELECT id FROM organizations")))
    if not org_rows:
        return 0

    created = 0
    now = datetime.now(timezone.utc)
    for (org_id,) in org_rows:
        existing = connection.execute(
            text(
                "SELECT id FROM decision_policies "
                "WHERE organization_id = :org_id AND role_id IS NULL"
            ),
            {"org_id": int(org_id)},
        ).first()
        if existing:
            continue

        # Lookup median role_fit threshold via raw SQL.
        thresholds = [
            float(row[0])
            for row in connection.execute(
                text(
                    "SELECT score_threshold FROM roles "
                    "WHERE organization_id = :org_id "
                    "AND score_threshold IS NOT NULL "
                    "AND deleted_at IS NULL"
                ),
                {"org_id": int(org_id)},
            )
            if row[0] is not None
        ]
        if thresholds:
            thresholds.sort()
            role_fit_min = thresholds[len(thresholds) // 2]
        else:
            default_threshold = connection.execute(
                text(
                    "SELECT default_score_threshold FROM organizations WHERE id = :org_id"
                ),
                {"org_id": int(org_id)},
            ).scalar()
            role_fit_min = (
                float(default_threshold)
                if default_threshold is not None
                else DEFAULT_ORG_FALLBACK_SCORE_THRESHOLD
            )

        policy_dict = _default_policy_json(role_fit_min=role_fit_min)
        PolicyJson.model_validate(policy_dict)

        revision_id = connection.execute(
            text(
                "INSERT INTO rubric_revisions "
                "(organization_id, role_id, parent_revision_id, cause, "
                "feedback_ids, weights_diff, threshold_diff, notes, created_at) "
                "VALUES (:org_id, NULL, NULL, 'human_edit', :feedback_ids, "
                "NULL, NULL, :notes, :created_at) "
                "RETURNING id"
            ),
            {
                "org_id": int(org_id),
                "feedback_ids": json.dumps([]),
                "notes": "bootstrap: cold-start org-default policy",
                "created_at": now,
            },
        ).scalar()

        if revision_id is None:
            # SQLite (used in tests) doesn't support RETURNING on older
            # versions — read it back via last_insert_rowid().
            revision_id = connection.execute(
                text("SELECT id FROM rubric_revisions ORDER BY id DESC LIMIT 1")
            ).scalar()

        connection.execute(
            text(
                "INSERT INTO decision_policies "
                "(organization_id, role_id, revision_id, policy_json, "
                "activated_at, deactivated_at, created_at) "
                "VALUES (:org_id, NULL, :revision_id, :policy_json, "
                ":activated_at, NULL, :created_at)"
            ),
            {
                "org_id": int(org_id),
                "revision_id": int(revision_id),
                "policy_json": json.dumps(policy_dict),
                "activated_at": now,
                "created_at": now,
            },
        )
        created += 1
    return created


__all__ = [
    "DEFAULT_ORG_FALLBACK_SCORE_THRESHOLD",
    "bootstrap_all_orgs",
    "bootstrap_all_orgs_via_connection",
    "bootstrap_org",
]
