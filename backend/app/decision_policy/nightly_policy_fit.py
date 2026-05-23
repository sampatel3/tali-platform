"""Nightly fit job — Phase 3 §7.4 of the architecture spec.

Once a day:
  1. Pull training data from Postgres for each (org, role) with enough
     volume to fit:
       weight 1.0 for AgentDecision rows that have RESULTED_IN a
              hired/rejected_late HiringOutcome via the realised-outcomes
              JSON on Role.agent_calibration (the legacy path) + linked
              graph outcomes once they're flowing.
       weight 0.8 for override decisions where the recruiter's action
              has subsequently been confirmed (status=approved later).
       weight 0.3 for raw approve decisions without realised outcomes.
  2. Fit a pooled logistic regression via ``fitted_policy.fit_model``.
  3. Write a ``PolicyVersion(status='candidate')`` row.
  4. Do NOT auto-promote — that's the Phase 5 promotion gate's job.

Idempotency: re-running the job for the same (org, role) produces
another candidate row. The promotion gate evaluates the newest one.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.decision_policy import DecisionPolicy
from ..models.policy_version import PolicyVersion
from ..models.role import Role
from .fitted_policy import TrainingExample, fit_model


logger = logging.getLogger("taali.decision_policy.nightly_policy_fit")


# Minimum training volume per (org, role) before we attempt a role-level
# fit. Below this the pooling mechanism inside ``fit_model`` keeps the
# role inheriting the org baseline.
ROLE_FIT_FLOOR = 30
# Same for org-level.
ORG_FIT_FLOOR = 50


def _label_for_decision(
    decision: AgentDecision, *, app: CandidateApplication | None
) -> tuple[float | None, float]:
    """Map a resolved decision to a (label, weight) pair.

    Returns ``(None, 0.0)`` when the decision isn't a usable training
    signal (still pending, expired, etc.).
    """
    status = (decision.status or "").lower()
    # Realised outcome takes priority (weight 1.0).
    outcome = (app.application_outcome if app else "") or ""
    outcome = outcome.lower()
    if outcome == "hired":
        return 1.0, 1.0
    if outcome == "rejected":
        # Realised "they really were a no", weight 1.0.
        return 0.0, 1.0
    # No realised outcome — fall back to recruiter labels.
    if status == "approved":
        # Recruiter said yes; outcome not yet observed. Weight 0.3.
        if (decision.recommendation or "").startswith("advance"):
            return 1.0, 0.3
        if (decision.recommendation or "").startswith("reject"):
            return 0.0, 0.3
    if status == "overridden":
        # Recruiter overrode the agent — the *manual* action they took
        # tells us what the right call was. Use it with weight 0.8.
        override = (decision.override_action or "").lower()
        if override.startswith("advance"):
            return 1.0, 0.8
        if override.startswith("reject"):
            return 0.0, 0.8
    return None, 0.0


def _features_for_decision(decision: AgentDecision) -> dict[str, float]:
    """Extract a feature vector from a decision's evidence blob.

    The orchestrator stores sub-agent outputs as
    ``decision.evidence["scores"]`` keyed by agent name. Pre-pilot rows
    may have a thinner shape — we pull whatever's there and let the
    fitter handle missing keys (treated as 0.0).
    """
    evidence = decision.evidence or {}
    feats: dict[str, float] = {}
    scores = evidence.get("scores") or {}
    if isinstance(scores, dict):
        for agent_name, blob in scores.items():
            if not isinstance(blob, dict):
                continue
            # Canonical: <agent>_score, <agent>_uncertainty.
            score = blob.get("score") or blob.get("confidence")
            if isinstance(score, (int, float)):
                feats[f"{agent_name}_score"] = float(score)
            unc = blob.get("uncertainty")
            if isinstance(unc, (int, float)):
                feats[f"{agent_name}_uncertainty"] = float(unc)
    # Aggregate confidence at decision time.
    if decision.confidence is not None:
        try:
            feats["decision_confidence"] = float(decision.confidence)
        except (TypeError, ValueError):
            pass
    return feats


def _collect_training_data(
    db: Session, *, organization_id: int, since: datetime
) -> list[TrainingExample]:
    """Pull (features, label, weight) examples from Graphiti where it
    has outcome edges, falling back to Postgres for the rest.

    Two-stage strategy:
      1. Query Graphiti for ``DecisionEvent → RESULTED_IN → HiringOutcome``
         paths in the time window. Each path yields a strong training
         example (label = 1 for hired, 0 for rejected_late; weight 1.0).
         The features come from the decision's evidence JSON which the
         orchestrator already mirrors when emitting the decision episode.
      2. Walk Postgres for any AgentDecision that doesn't have a
         matching graph outcome yet but DOES have a recruiter approve /
         override resolution — those get the weaker labels (0.3 / 0.8)
         per §6.

    Graphiti is the canonical substrate; Postgres covers the gap until
    every outcome has been mirrored into the graph.
    """
    rows: list[TrainingExample] = []
    graph_seen_decision_ids: set[int] = set()
    try:
        graph_examples = _collect_from_graphiti(
            organization_id=organization_id, since=since
        )
        for ex, decision_id in graph_examples:
            rows.append(ex)
            if decision_id is not None:
                graph_seen_decision_ids.add(int(decision_id))
    except Exception as exc:
        logger.warning(
            "graphiti training-data fetch failed; falling through to Postgres: %s",
            exc,
        )

    decisions = (
        db.query(AgentDecision, CandidateApplication)
        .outerjoin(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.created_at >= since,
        )
        .all()
    )
    for decision, app in decisions:
        # Don't double-count: if Graphiti already gave us a strong
        # outcome label for this decision, skip the weaker Postgres
        # label for the same decision_id.
        if int(decision.id) in graph_seen_decision_ids:
            continue
        label, weight = _label_for_decision(decision, app=app)
        if label is None or weight <= 0:
            continue
        feats = _features_for_decision(decision)
        if not feats:
            continue
        rows.append(
            TrainingExample(
                features=feats,
                label=float(label),
                weight=float(weight),
                role_id=int(decision.role_id) if decision.role_id else None,
            )
        )
    return rows


def _collect_from_graphiti(
    *, organization_id: int, since: datetime
) -> list[tuple[TrainingExample, int | None]]:
    """Query Graphiti for DecisionEvent → RESULTED_IN → HiringOutcome paths.

    Returns a list of ``(training_example, decision_id)`` tuples — the
    caller dedupes Postgres rows against these. Returns ``[]`` on any
    failure (graph unavailable, no matches, parse error).

    Pre-pilot graph state: most decisions don't yet have an outcome
    edge written, so this query often returns []. The Postgres
    fallback in the caller covers the gap.
    """
    out: list[tuple[TrainingExample, int | None]] = []
    try:
        from ..candidate_graph import client as graph_client
        from ..candidate_graph import graphrag_queries
    except Exception:
        return out
    if not graph_client.is_configured():
        return out
    group_id = graph_client.group_id_for_org(organization_id)
    # Cypher: pull every DecisionEvent linked to a HiringOutcome since
    # the training window opened. The orchestrator's decision episode
    # writer stamps decision_id + recommended_action + reasoning into
    # the episode body, which the extractor binds to DecisionEvent
    # properties. The training features come from the same evidence
    # blob the Postgres path reads — but here we get the outcome label
    # straight from the graph rather than inferring it.
    query = """
        MATCH (d:DecisionEvent {group_id: $group_id})
              -[:RESULTED_IN]->(o:HiringOutcome {group_id: $group_id})
        WHERE coalesce(d.created_at, $since) >= $since
        RETURN d.decision_id AS decision_id,
               d.role_id AS role_id,
               d.features_json AS features_json,
               o.outcome_type AS outcome_type,
               coalesce(o.quality_signal, 0.0) AS quality_signal
        LIMIT 5000
    """
    rows = graphrag_queries._execute(query, group_id=group_id, since=since)
    for r in rows or []:
        outcome = (r.get("outcome_type") or "").lower()
        if outcome == "hired":
            label, weight = 1.0, 1.0
        elif outcome in ("rejected_late", "rejected"):
            label, weight = 0.0, 1.0
        else:
            # Pending / withdrawn / interview-only — not a strong label.
            continue
        feats = r.get("features_json") or {}
        # ``features_json`` is serialised into the decision episode body
        # (see agent_episodes.build_decision_episode) and comes back off the
        # graph node as a JSON string — parse it before use so rows that
        # legitimately carry features aren't dropped.
        if isinstance(feats, str):
            try:
                feats = json.loads(feats)
            except (ValueError, TypeError):
                feats = {}
        if not isinstance(feats, dict) or not feats:
            # No features serialised on the graph node — caller's
            # Postgres pass will pick this decision up via its
            # evidence JSON.
            continue
        role_id = r.get("role_id")
        decision_id = r.get("decision_id")
        out.append((
            TrainingExample(
                features={k: float(v) for k, v in feats.items() if isinstance(v, (int, float))},
                label=label,
                weight=weight,
                role_id=int(role_id) if role_id is not None else None,
            ),
            int(decision_id) if decision_id is not None else None,
        ))
    return out


def fit_for_org(
    db: Session, *, organization_id: int, since: datetime, role_id: int | None
) -> PolicyVersion | None:
    """Fit one candidate ``PolicyVersion`` for (org, role).

    Returns the persisted row, or None when there isn't enough data.
    """
    examples = _collect_training_data(db, organization_id=organization_id, since=since)
    if role_id is None:
        if len(examples) < ORG_FIT_FLOOR:
            logger.info(
                "skipping org-level fit org=%s, n=%d below floor=%d",
                organization_id, len(examples), ORG_FIT_FLOOR,
            )
            return None
    else:
        role_n = sum(1 for ex in examples if ex.role_id == role_id)
        if role_n < ROLE_FIT_FLOOR:
            logger.info(
                "skipping role-level fit org=%s role=%s, n=%d below floor=%d",
                organization_id, role_id, role_n, ROLE_FIT_FLOOR,
            )
            return None
    # Last 20% of examples becomes the in-fitter gold set (for isotonic
    # calibration). The Phase 5 promotion gate uses its own held-out
    # gold set separately.
    cut = max(1, int(len(examples) * 0.8))
    train, gold = examples[:cut], examples[cut:]
    model, metrics = fit_model(train, role_id=role_id, gold_set=gold)

    row = PolicyVersion(
        organization_id=organization_id,
        role_id=role_id,
        model_kind="logistic_pooled",
        model_json=model.to_dict(),
        metrics_json=metrics,
        training_window_start=since,
        training_window_end=datetime.now(timezone.utc),
        status="candidate",
    )
    db.add(row)
    db.flush()
    return row


def run_nightly_fit(db: Session, *, since: datetime) -> dict:
    """Loop through every org + active role pair and try to fit.

    Returns a small summary dict so the Celery wrapper can log
    statistics.
    """
    summary = {"fitted": 0, "skipped": 0, "by_org": {}}
    for org_id, in db.query(DecisionPolicy.organization_id).distinct().all():
        # Org-level pass.
        try:
            row = fit_for_org(db, organization_id=int(org_id), since=since, role_id=None)
        except Exception:
            logger.exception("org-level fit failed for org=%s", org_id)
            row = None
        if row is not None:
            summary["fitted"] += 1
            summary["by_org"].setdefault(int(org_id), {"org_level": True, "roles": []})
        else:
            summary["skipped"] += 1
        # Per-role pass.
        role_ids = [
            r[0]
            for r in db.query(Role.id)
            .filter(Role.organization_id == int(org_id))
            .filter(Role.agentic_mode_enabled.is_(True))
            .all()
        ]
        for role_id in role_ids:
            try:
                role_row = fit_for_org(
                    db, organization_id=int(org_id), since=since, role_id=int(role_id)
                )
            except Exception:
                logger.exception("role-level fit failed for role=%s", role_id)
                role_row = None
            if role_row is not None:
                summary["fitted"] += 1
                summary["by_org"].setdefault(int(org_id), {"org_level": False, "roles": []})
                summary["by_org"][int(org_id)]["roles"].append(int(role_id))
            else:
                summary["skipped"] += 1
    db.commit()
    return summary


__all__ = [
    "ORG_FIT_FLOOR",
    "ROLE_FIT_FLOOR",
    "fit_for_org",
    "run_nightly_fit",
]
