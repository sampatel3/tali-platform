"""Canonical entity + edge vocabulary for the multi-agent system.

Graphiti extracts entities and relationships from episode text via an LLM
pass — there's no static schema enforced at the driver level. This
module is the single source of truth for the *names* we use in episode
bodies and direct-Cypher queries (sub_agent_graph_queries.md). Writers
import the constants here so episode text uses consistent strings;
readers (sub-agents querying Graphiti) reference the same constants.

The reason names live here and not in episode-writer modules is that
multiple writers and multiple readers need to agree. A typo on either
side would silently route past the LLM extractor and create orphan
nodes; centralising the strings turns that into a Python import error.
"""

from __future__ import annotations

from typing import Final


# ---------------------------------------------------------------------------
# Node / entity labels (used in Cypher MATCH ... :Label clauses)
# ---------------------------------------------------------------------------

NODE_CANDIDATE: Final[str] = "Candidate"
NODE_ROLE: Final[str] = "Role"
NODE_COMPANY: Final[str] = "Company"
NODE_SKILL: Final[str] = "Skill"
NODE_REFERRER: Final[str] = "Referrer"
NODE_RECRUITER: Final[str] = "Recruiter"

# Multi-agent additions (spec §5.2)
NODE_AGENT_SCORE_EVENT: Final[str] = "AgentScoreEvent"
NODE_DECISION: Final[str] = "DecisionEvent"
NODE_HIRING_OUTCOME: Final[str] = "HiringOutcome"
NODE_APPLICATION: Final[str] = "Application"

# Amendment A1 (recruiter intent)
NODE_ROLE_INTENT: Final[str] = "RoleIntent"

# Amendment A2 (task selection lifecycle)
NODE_TASK_TEMPLATE: Final[str] = "TaskTemplate"
NODE_TASK_INSTANCE: Final[str] = "TaskInstance"
NODE_TASK_SUBMISSION: Final[str] = "TaskSubmission"
NODE_DIMENSION: Final[str] = "Dimension"
NODE_ARTIFACT: Final[str] = "Artifact"

ALL_NODE_LABELS: Final[tuple[str, ...]] = (
    NODE_CANDIDATE,
    NODE_ROLE,
    NODE_COMPANY,
    NODE_SKILL,
    NODE_REFERRER,
    NODE_RECRUITER,
    NODE_AGENT_SCORE_EVENT,
    NODE_DECISION,
    NODE_HIRING_OUTCOME,
    NODE_APPLICATION,
    NODE_ROLE_INTENT,
    NODE_TASK_TEMPLATE,
    NODE_TASK_INSTANCE,
    NODE_TASK_SUBMISSION,
    NODE_DIMENSION,
    NODE_ARTIFACT,
)


# ---------------------------------------------------------------------------
# Edge / relationship types
# ---------------------------------------------------------------------------

EDGE_HAS_SKILL: Final[str] = "HAS_SKILL"
EDGE_WORKED_AT: Final[str] = "WORKED_AT"
EDGE_REFERRED_BY: Final[str] = "REFERRED_BY"
EDGE_APPLIED_FOR: Final[str] = "APPLIED_FOR"
EDGE_SIMILAR_TO: Final[str] = "SIMILAR_TO"
EDGE_REQUIRES: Final[str] = "REQUIRES"
EDGE_AT_COMPANY: Final[str] = "AT"
EDGE_RELATED_TO: Final[str] = "RELATED_TO"

# Multi-agent additions
EDGE_SCORED_BY: Final[str] = "SCORED_BY"
EDGE_FED_INTO: Final[str] = "FED_INTO"
EDGE_REVIEWED_BY: Final[str] = "REVIEWED_BY"
EDGE_RESULTED_IN: Final[str] = "RESULTED_IN"
EDGE_HIGH_YIELD: Final[str] = "HIGH_YIELD"
EDGE_LOW_YIELD: Final[str] = "LOW_YIELD"
EDGE_COMPANY_SIGNAL_BOOST: Final[str] = "COMPANY_SIGNAL_BOOST"

# Amendment A1 (recruiter intent edges)
EDGE_HAS_INTENT: Final[str] = "HAS_INTENT"
EDGE_AUTHORED_BY: Final[str] = "AUTHORED_BY"
EDGE_SUPERSEDED_BY: Final[str] = "SUPERSEDED_BY"

# Amendment A2 (task selection edges)
EDGE_ELIGIBLE_FOR_TEMPLATE: Final[str] = "ELIGIBLE_FOR_TEMPLATE"
EDGE_TESTS: Final[str] = "TESTS"
EDGE_FROM_TEMPLATE: Final[str] = "FROM_TEMPLATE"
EDGE_ASSIGNED_TO: Final[str] = "ASSIGNED_TO"
EDGE_YIELDED: Final[str] = "YIELDED"
EDGE_CALIBRATION_FOR: Final[str] = "CALIBRATION_FOR"
EDGE_HAS_ARTIFACT: Final[str] = "HAS_ARTIFACT"
EDGE_EVIDENCES: Final[str] = "EVIDENCES"

ALL_EDGE_TYPES: Final[tuple[str, ...]] = (
    EDGE_HAS_SKILL,
    EDGE_WORKED_AT,
    EDGE_REFERRED_BY,
    EDGE_APPLIED_FOR,
    EDGE_SIMILAR_TO,
    EDGE_REQUIRES,
    EDGE_AT_COMPANY,
    EDGE_RELATED_TO,
    EDGE_SCORED_BY,
    EDGE_FED_INTO,
    EDGE_REVIEWED_BY,
    EDGE_RESULTED_IN,
    EDGE_HIGH_YIELD,
    EDGE_LOW_YIELD,
    EDGE_COMPANY_SIGNAL_BOOST,
    EDGE_HAS_INTENT,
    EDGE_AUTHORED_BY,
    EDGE_SUPERSEDED_BY,
    EDGE_ELIGIBLE_FOR_TEMPLATE,
    EDGE_TESTS,
    EDGE_FROM_TEMPLATE,
    EDGE_ASSIGNED_TO,
    EDGE_YIELDED,
    EDGE_CALIBRATION_FOR,
    EDGE_HAS_ARTIFACT,
    EDGE_EVIDENCES,
)


# ---------------------------------------------------------------------------
# Sensitivity buckets for graph writeback (spec §5 of writeback patterns)
#
# Mirrored from config/blocked_edge_attributes.yaml at apply time. This
# module is the *type-level* taxonomy — the YAML controls the runtime
# enforcement and is the file compliance signs off on.
# ---------------------------------------------------------------------------

LOW_RISK_EDGE_TYPES: Final[frozenset[str]] = frozenset(
    {
        EDGE_HAS_SKILL,
        EDGE_WORKED_AT,
        EDGE_RELATED_TO,
        EDGE_REQUIRES,  # weight updates only
    }
)

MEDIUM_RISK_EDGE_TYPES: Final[frozenset[str]] = frozenset(
    {
        EDGE_SIMILAR_TO,
        EDGE_HIGH_YIELD,
        EDGE_LOW_YIELD,
        EDGE_COMPANY_SIGNAL_BOOST,
    }
)

# Anything not listed in low or medium is treated as high-risk and
# blocked. The blocklist is YAML-driven for the deploy-time gate.
HIGH_RISK_NODE_LABELS: Final[frozenset[str]] = frozenset(
    {
        "Gender",
        "Race",
        "AgeBand",
        "Nationality",
        "Disability",
        "Religion",
        "MaritalStatus",
        "SexualOrientation",
        "PregnancyStatus",
        "VeteranStatus",
    }
)


# ---------------------------------------------------------------------------
# Episode source descriptors — used by the orchestrator's episode writer.
# Keep them short; Graphiti's extraction key-by-source helps debugging.
# ---------------------------------------------------------------------------

EPISODE_SOURCE_AGENT_SCORE: Final[str] = "agent.score"
EPISODE_SOURCE_DECISION: Final[str] = "agent.decision"
EPISODE_SOURCE_RECRUITER_ACTION: Final[str] = "recruiter.action"
EPISODE_SOURCE_OUTCOME: Final[str] = "outcome.hiring"
EPISODE_SOURCE_FEEDBACK: Final[str] = "recruiter.feedback"


__all__ = [
    "ALL_EDGE_TYPES",
    "ALL_NODE_LABELS",
    "EDGE_APPLIED_FOR",
    "EDGE_AT_COMPANY",
    "EDGE_COMPANY_SIGNAL_BOOST",
    "EDGE_FED_INTO",
    "EDGE_HAS_SKILL",
    "EDGE_HIGH_YIELD",
    "EDGE_LOW_YIELD",
    "EDGE_REFERRED_BY",
    "EDGE_RELATED_TO",
    "EDGE_REQUIRES",
    "EDGE_RESULTED_IN",
    "EDGE_REVIEWED_BY",
    "EDGE_SCORED_BY",
    "EDGE_SIMILAR_TO",
    "EDGE_WORKED_AT",
    "EPISODE_SOURCE_AGENT_SCORE",
    "EPISODE_SOURCE_DECISION",
    "EPISODE_SOURCE_FEEDBACK",
    "EPISODE_SOURCE_OUTCOME",
    "EPISODE_SOURCE_RECRUITER_ACTION",
    "HIGH_RISK_NODE_LABELS",
    "LOW_RISK_EDGE_TYPES",
    "MEDIUM_RISK_EDGE_TYPES",
    "NODE_AGENT_SCORE_EVENT",
    "NODE_APPLICATION",
    "NODE_CANDIDATE",
    "NODE_COMPANY",
    "NODE_DECISION",
    "NODE_HIRING_OUTCOME",
    "NODE_RECRUITER",
    "NODE_REFERRER",
    "NODE_ROLE",
    "NODE_SKILL",
]
