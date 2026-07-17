"""Episode writers for the multi-agent system events.

Phase 2 (§6.7 of the architecture spec): every cycle / score / decision /
recruiter action / hiring outcome gets written as a Graphiti episode so
the graph carries a reconstructable history. This module is the *narrow*
writeback API for those events — episode bodies follow stable templates
so Graphiti's extractor consistently produces the typed nodes/edges
defined in ``candidate_graph.schema``.

Why episodes and not direct Cypher: Graphiti's extractor merges entities
across episodes by name + group_id. Writing AgentScoreEvent /
DecisionEvent / HiringOutcome via episodes means the same entity-merge
machinery that handles candidate-profile ingestion also handles the
decision history — one fewer code path to maintain.

Failures here are logged and ignored. The Postgres tables (agent_decisions,
decision_feedback) are the source of truth; the graph is a derived index.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from . import client as graph_client
from . import schema
from .episodes import Episode, bounded_episode_body, dispatch


logger = logging.getLogger("taali.candidate_graph.agent_episodes")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Score events — one per (application, sub-agent) per cycle
# ---------------------------------------------------------------------------


def build_agent_score_episode(
    *,
    organization_id: int,
    candidate_full_name: str | None,
    candidate_taali_id: int,
    application_id: int,
    role_id: int,
    agent_name: str,
    score: float,
    uncertainty: float,
    structured_evidence_summary: str,
    model_version: str,
    scored_at: datetime,
) -> Episode | None:
    """Compose an episode capturing a single sub-agent score.

    The body uses ``schema.NODE_AGENT_SCORE_EVENT`` and
    ``schema.EDGE_SCORED_BY`` strings so Graphiti's LLM extractor
    consistently produces the same labels.
    """
    if organization_id <= 0:
        return None
    group_id = graph_client.group_id_for_org(organization_id)
    name = candidate_full_name or f"Candidate {candidate_taali_id}"

    body = bounded_episode_body("\n".join(
        [
            f"Subject candidate: {name} (taali_id={candidate_taali_id})",
            f"Application taali_id={application_id}, role taali_id={role_id}.",
            f"{schema.NODE_AGENT_SCORE_EVENT}: sub-agent {agent_name} produced "
            f"a score of {score:.3f} with uncertainty {uncertainty:.3f} "
            f"(model version {model_version}).",
            f"This application is {schema.EDGE_SCORED_BY} the "
            f"{schema.NODE_AGENT_SCORE_EVENT} above.",
            "",
            "Structured evidence summary:",
            structured_evidence_summary[:1500] if structured_evidence_summary else "(none)",
        ]
    ))
    return Episode(
        name=f"agent-score-app-{application_id}-{agent_name}-{int(scored_at.timestamp())}",
        body=body,
        source_description=schema.EPISODE_SOURCE_AGENT_SCORE,
        reference_time=scored_at,
        group_id=group_id,
    )


# ---------------------------------------------------------------------------
# Decision events — one per AgentDecision
# ---------------------------------------------------------------------------


def build_decision_episode(
    *,
    organization_id: int,
    candidate_full_name: str | None,
    candidate_taali_id: int,
    application_id: int,
    role_id: int,
    decision_id: int,
    recommended_action: str,
    confidence: float,
    policy_revision_id: int | None,
    reasoning: str,
    created_at: datetime,
    features_json: dict[str, Any] | None = None,
) -> Episode | None:
    if organization_id <= 0:
        return None
    group_id = graph_client.group_id_for_org(organization_id)
    name = candidate_full_name or f"Candidate {candidate_taali_id}"
    body_lines = [
        f"Subject candidate: {name} (taali_id={candidate_taali_id})",
        f"{schema.NODE_DECISION}: decision id D-{decision_id} on application "
        f"taali_id={application_id} for role taali_id={role_id}.",
        f"Recommended action: {recommended_action}.",
        f"Policy revision id: {policy_revision_id}.",
        f"Confidence: {confidence:.3f}.",
    ]
    # Serialise the decision's feature vector as a stable ``features_json``
    # property so the nightly fitter's Graphiti collector
    # (``_collect_from_graphiti``) can read it back off the DecisionEvent
    # node. Without this the graph outcomes are dropped and only the
    # Postgres fallback contributes training rows.
    if features_json:
        body_lines.append(
            f"features_json: {json.dumps(features_json, sort_keys=True)}"
        )
    body_lines.extend(
        [
            (
                f"All {schema.NODE_AGENT_SCORE_EVENT} entries for application "
                f"{application_id} {schema.EDGE_FED_INTO} this "
                f"{schema.NODE_DECISION}."
            ),
            "",
            "Reasoning:",
            (reasoning or "")[:1500],
        ]
    )
    body = bounded_episode_body("\n".join(body_lines))
    return Episode(
        name=f"agent-decision-{decision_id}",
        body=body,
        source_description=schema.EPISODE_SOURCE_DECISION,
        reference_time=created_at,
        group_id=group_id,
    )


# ---------------------------------------------------------------------------
# Recruiter action events (approve / override / teach)
# ---------------------------------------------------------------------------


def build_recruiter_action_episode(
    *,
    organization_id: int,
    decision_id: int,
    recruiter_id: int,
    action: str,
    reason: str | None,
    happened_at: datetime,
    role_id: int | None = None,
) -> Episode | None:
    if organization_id <= 0:
        return None
    group_id = graph_client.group_id_for_org(organization_id)
    body = bounded_episode_body("\n".join(
        [
            f"Recruiter id={recruiter_id} {action} decision D-{decision_id}.",
            (
                f"This {schema.NODE_DECISION} is "
                f"{schema.EDGE_REVIEWED_BY} the {schema.NODE_RECRUITER} above."
            ),
            f"Action timestamp: {happened_at.isoformat()}.",
            "Reason:",
            (reason or "(no reason recorded)")[:1500],
        ]
    ))
    return Episode(
        name=f"recruiter-action-{action}-{decision_id}",
        body=body,
        source_description=f"{schema.EPISODE_SOURCE_RECRUITER_ACTION}.{action}",
        reference_time=happened_at,
        group_id=group_id,
    )


# ---------------------------------------------------------------------------
# Hiring outcome events
# ---------------------------------------------------------------------------


def build_hiring_outcome_episode(
    *,
    organization_id: int,
    candidate_full_name: str | None,
    candidate_taali_id: int,
    decision_id: int,
    outcome_type: str,
    quality_signal: float | None,
    observed_at: datetime,
    role_id: int | None = None,
) -> Episode | None:
    if organization_id <= 0:
        return None
    group_id = graph_client.group_id_for_org(organization_id)
    name = candidate_full_name or f"Candidate {candidate_taali_id}"
    qsig = (
        f"quality signal {quality_signal:.2f}"
        if quality_signal is not None
        else "no quality signal recorded"
    )
    body = bounded_episode_body("\n".join(
        [
            f"Subject candidate: {name} (taali_id={candidate_taali_id})",
            (
                f"{schema.NODE_HIRING_OUTCOME}: outcome '{outcome_type}' for "
                f"decision D-{decision_id} ({qsig})."
            ),
            (
                f"Decision D-{decision_id} {schema.EDGE_RESULTED_IN} this "
                f"{schema.NODE_HIRING_OUTCOME}."
            ),
            # Direct Candidate→Outcome edge so the read queries can walk a
            # single canonical path. Lets graph_priors match outcomes via
            # the candidate without requiring a DecisionEvent middle hop.
            (
                f"This Candidate {schema.EDGE_RESULTED_IN} this "
                f"{schema.NODE_HIRING_OUTCOME}."
            ),
            f"Observed at: {observed_at.isoformat()}.",
        ]
    ))
    return Episode(
        name=f"hiring-outcome-{decision_id}-{outcome_type}",
        body=body,
        source_description=schema.EPISODE_SOURCE_OUTCOME,
        reference_time=observed_at,
        group_id=group_id,
    )


# ---------------------------------------------------------------------------
# Public dispatch helpers — fire-and-forget, swallow errors
# ---------------------------------------------------------------------------


def _dispatch_metered(episode: Episode, payload: dict[str, Any]) -> int:
    """Dispatch one derived graph episode without an unmetered provider path."""
    organization_id = int(payload["organization_id"])
    raw_role_id = payload.get("role_id")
    role_id = int(raw_role_id) if raw_role_id is not None else None
    raw_candidate_id = payload.get("candidate_taali_id")
    candidate_id = (
        int(raw_candidate_id) if raw_candidate_id is not None else None
    )
    raw_user_id = payload.get("recruiter_id") or payload.get("authored_by_user_id")
    user_id = int(raw_user_id) if raw_user_id else None
    return dispatch(
        [episode],
        bill_organization_id=organization_id,
        bill_role_id=role_id,
        bill_user_id=user_id,
        bill_candidate_id=candidate_id,
        bill_trace_id=f"graph-direct:{episode.name}",
        require_hard_admission=True,
        require_role_admission=role_id is not None,
        raise_on_error=True,
    )


def emit_score_event(**kwargs: Any) -> bool:
    """Write a single agent-score episode. Returns True if dispatched."""
    episode = build_agent_score_episode(**kwargs)
    if episode is None:
        return False
    try:
        sent = _dispatch_metered(episode, kwargs)
        return bool(sent)
    except Exception as exc:
        logger.warning("emit_score_event failed: %s", exc)
        return False


def emit_decision_event(**kwargs: Any) -> bool:
    episode = build_decision_episode(**kwargs)
    if episode is None:
        return False
    try:
        sent = _dispatch_metered(episode, kwargs)
        return bool(sent)
    except Exception as exc:
        logger.warning("emit_decision_event failed: %s", exc)
        return False


def emit_recruiter_action_event(**kwargs: Any) -> bool:
    episode = build_recruiter_action_episode(**kwargs)
    if episode is None:
        return False
    try:
        sent = _dispatch_metered(episode, kwargs)
        return bool(sent)
    except Exception as exc:
        logger.warning("emit_recruiter_action_event failed: %s", exc)
        return False


def emit_hiring_outcome_event(**kwargs: Any) -> bool:
    episode = build_hiring_outcome_episode(**kwargs)
    if episode is None:
        return False
    try:
        sent = _dispatch_metered(episode, kwargs)
        return bool(sent)
    except Exception as exc:
        logger.warning("emit_hiring_outcome_event failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# RoleIntent (Amendment A1) — graph mirror of the manually authored row
# ---------------------------------------------------------------------------


def build_role_intent_episode(
    *,
    organization_id: int,
    role_id: int,
    role_name: str | None,
    intent_version: int,
    structured_summary: str,
    free_text: str | None,
    authored_by_user_id: int | None,
    authored_at: datetime,
) -> Episode | None:
    """Compose an episode that mirrors a newly authored RoleIntent row.

    Graphiti's extractor picks up the structured fields as semantic
    edges between Role and RoleIntent (HAS_INTENT) and tracks the
    AUTHORED_BY edge to the Recruiter entity. Bi-temporal: the
    episode's reference_time is the authored_at moment.
    """
    if organization_id <= 0:
        return None
    group_id = graph_client.group_id_for_org(organization_id)
    role_label = role_name or f"Role {role_id}"
    body_lines = [
        f"Role: {role_label} (taali_id={role_id})",
        f"{schema.NODE_ROLE_INTENT} v{intent_version}: the recruiter "
        f"has authored a versioned role intent for this role.",
        (
            f"This Role {schema.EDGE_HAS_INTENT} the "
            f"{schema.NODE_ROLE_INTENT} above."
        ),
    ]
    if authored_by_user_id:
        body_lines.append(
            f"The {schema.NODE_ROLE_INTENT} {schema.EDGE_AUTHORED_BY} "
            f"recruiter id={authored_by_user_id}."
        )
    body_lines.append("")
    body_lines.append("Structured intent:")
    body_lines.append(structured_summary[:1500] if structured_summary else "(empty)")
    if free_text:
        body_lines.append("")
        body_lines.append("Free-text notes:")
        body_lines.append(free_text[:1200])
    return Episode(
        name=f"role-intent-{role_id}-v{intent_version}",
        body=bounded_episode_body("\n".join(body_lines)),
        source_description=f"recruiter.role_intent.v{intent_version}",
        reference_time=authored_at,
        group_id=group_id,
    )


def emit_role_intent_event(**kwargs: Any) -> bool:
    """Fire-and-forget emit of a RoleIntent episode. Never raises."""
    episode = build_role_intent_episode(**kwargs)
    if episode is None:
        return False
    try:
        sent = _dispatch_metered(episode, kwargs)
        return bool(sent)
    except Exception as exc:
        logger.warning("emit_role_intent_event failed: %s", exc)
        return False


__all__ = [
    "build_agent_score_episode",
    "build_decision_episode",
    "build_hiring_outcome_episode",
    "build_recruiter_action_episode",
    "build_role_intent_episode",
    "emit_decision_event",
    "emit_hiring_outcome_event",
    "emit_recruiter_action_event",
    "emit_role_intent_event",
    "emit_score_event",
]
