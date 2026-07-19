"""Sensitivity classification — Phase 6 §5 of the writeback patterns.

Three buckets:
  low     auto-commit (HAS_SKILL, WORKED_AT, RELATED_TO, REQUIRES weight-only)
  medium  co-sign required (SIMILAR_TO, HIGH_YIELD, COMPANY_SIGNAL_BOOST)
  high    blocked (anything touching protected attributes)

The blocklist comes from ``config/blocked_edge_attributes.yaml``. Falls
back to the spec defaults when YAML / file is missing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ..candidate_graph import schema as graph_schema
from .contracts import GraphWriteHint, ValidationResult


logger = logging.getLogger("taali.graph_writeback.sensitivity")


CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "blocked_edge_attributes.yaml"


@dataclass
class Blocklist:
    blocked_node_labels: frozenset[str]
    blocked_edge_types: frozenset[str]
    blocked_properties: frozenset[str]


_DEFAULT_BLOCKLIST = Blocklist(
    blocked_node_labels=graph_schema.HIGH_RISK_NODE_LABELS,
    blocked_edge_types=frozenset(
        {"HAS_PROTECTED_ATTR", "HAS_GENDER", "HAS_RACE", "HAS_RELIGION"}
    ),
    blocked_properties=frozenset(
        {
            "gender",
            "race",
            "ethnicity",
            "age",
            "age_band",
            "nationality",
            "religion",
            "disability",
            "marital_status",
            "sexual_orientation",
            "pregnancy_status",
            "veteran_status",
        }
    ),
)


def load_blocklist(path: str | os.PathLike[str] | None = None) -> Blocklist:
    target = Path(path) if path else CONFIG_PATH
    if not target.exists():
        return _DEFAULT_BLOCKLIST
    try:
        import yaml  # type: ignore[import-not-found]

        with target.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning(
            "blocklist YAML parse failed; using defaults error_type=%s",
            type(exc).__name__,
        )
        return _DEFAULT_BLOCKLIST
    return Blocklist(
        blocked_node_labels=frozenset(raw.get("blocked_node_labels") or []),
        blocked_edge_types=frozenset(raw.get("blocked_edge_types") or []),
        blocked_properties=frozenset(raw.get("blocked_properties") or []),
    )


def _normalised_node_label(node_id: str | None) -> str | None:
    """Extract a label prefix from a node id like ``Gender:female``.

    Returns the part before the first ``:`` or ``-``; None when the
    id lacks a label tag. Callers should also check the YAML's
    blocklist directly for unprefixed node ids that happen to point
    at protected nodes.
    """
    if not node_id:
        return None
    for sep in (":", "/", "-"):
        if sep in node_id:
            return node_id.split(sep, 1)[0]
    return None


def classify_hint(
    hint: GraphWriteHint, *, blocklist: Blocklist | None = None
) -> ValidationResult:
    """Validate + classify sensitivity in one pass.

    Returns ``ValidationResult.accept(sensitivity=...)`` on accept,
    ``ValidationResult.reject(reason=...)`` on reject (schema /
    reference failures), or accept(sensitivity='high') for blocked
    (the caller treats high as a soft block — logged + reported, not
    a 500).
    """
    bl = blocklist or load_blocklist()

    # 1. Edge type allow-list. Anything not in our vocabulary is rejected.
    if hint.action in ("assert_edge", "invalidate_edge", "update_edge_property"):
        if not hint.edge_type:
            return ValidationResult.reject("missing_edge_type")
        if hint.edge_type in bl.blocked_edge_types:
            return ValidationResult.accept(sensitivity="high")
        if hint.edge_type not in graph_schema.ALL_EDGE_TYPES:
            return ValidationResult.reject(f"unknown_edge_type:{hint.edge_type}")

    # 2. Endpoint label check — anything touching a protected node is high.
    for node_id in (hint.from_node_id, hint.to_node_id):
        label = _normalised_node_label(node_id)
        if label and label in bl.blocked_node_labels:
            return ValidationResult.accept(sensitivity="high")

    # 3. Property bag check — blocks proxies via ``properties`` dict.
    for key in (hint.properties or {}).keys():
        if key.lower() in bl.blocked_properties:
            return ValidationResult.accept(sensitivity="high")

    # 4. Endpoint check for assert/invalidate edges.
    if hint.action == "assert_edge" and not (hint.from_node_id and hint.to_node_id):
        return ValidationResult.reject("missing_endpoints")
    if hint.action == "invalidate_edge" and not (hint.from_node_id and hint.to_node_id):
        return ValidationResult.reject("missing_endpoints")

    # 5. Sensitivity bucket lookup.
    if hint.action == "assert_node":
        # Only allowed for the standard semantic labels (we don't
        # accept arbitrary new node types from feedback).
        return ValidationResult.accept(sensitivity="medium")
    edge = hint.edge_type or ""
    if edge in graph_schema.LOW_RISK_EDGE_TYPES:
        return ValidationResult.accept(sensitivity="low")
    if edge in graph_schema.MEDIUM_RISK_EDGE_TYPES:
        return ValidationResult.accept(sensitivity="medium")
    # Everything else defaults to medium — never auto-commit something
    # the schema didn't bless explicitly.
    return ValidationResult.accept(sensitivity="medium")


__all__ = [
    "Blocklist",
    "classify_hint",
    "load_blocklist",
]
