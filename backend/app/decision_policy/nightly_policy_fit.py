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
  3. Reuse an equivalent current fitted row, or write one new
     ``PolicyVersion(status='candidate')`` row and mark older pending
     candidates ``superseded``.
  4. Do NOT open a shadow run or auto-promote. The durable per-decision
     shadow lifecycle is not wired in production yet, so fitted models
     remain fail-closed safety inputs for the rule-policy retune gate.

Idempotency: a deterministic fingerprint covers the ordered training
examples and fit-affecting workspace settings. Re-running with the same
inputs reuses the current candidate before grid/agentic search, avoiding
duplicate rows and model spend. A changed input set produces one new
candidate and supersedes older pending candidates for that scope.
"""

from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.decision_policy import DecisionPolicy
from ..models.organization import Organization
from ..models.policy_version import PolicyVersion
from ..models.role import Role
from . import autoresearch
from .audit_examples import load_audit_examples
from .fitted_policy import TrainingExample, fit_model


logger = logging.getLogger("taali.decision_policy.nightly_policy_fit")


# Operator opt-in (per-org ``workspace_settings.decision_policy_autoresearch``):
#   absent / false  -> one-shot fit with the historical defaults (unchanged).
#   "grid"          -> deterministic hyperparameter search (no LLM cost).
#   "agentic" / true-> LLM-driven search (Claude proposes; metered tokens).
# Either search mode only ever *replaces* the fit when it finds a bias-clean
# config that beats the baseline; otherwise the one-shot fit is used. If the
# dormant fitted-policy rollout is explicitly activated in future, the Phase-5
# gate remains the authoritative bias/gold/shadow check.
_AUTORESEARCH_MODES = {"grid", "agentic"}

# Bump whenever feature extraction or fit semantics change in a way that must
# force a fresh candidate even if the underlying decision rows are unchanged.
FIT_CONTRACT_VERSION = "fitted-policy-input-v2"

# These are the server-owned flattened values persisted by both the agent
# runtime and deterministic bulk decision paths. Keep the names aligned with
# DecisionInputs/the rule policy so Postgres fallback rows and Graph episodes
# train on the same vocabulary.
_FLAT_FEATURE_KEYS = (
    "pre_screen_score",
    "role_fit_score",
    "taali_score",
    "assessment_score",
    "calibrated_p_advance",
    "graph_prior_p_advance",
    "graph_prior_p_hired",
)


@dataclass(frozen=True)
class CandidateFitResult:
    candidate: PolicyVersion | None
    created: bool
    reason: str | None = None


def _autoresearch_mode(org: Organization | None) -> str | None:
    if org is None:
        return None
    settings = org.workspace_settings if isinstance(org.workspace_settings, dict) else None
    raw = (settings or {}).get("decision_policy_autoresearch", False)
    if raw is True:
        return "agentic"
    if isinstance(raw, str) and raw.lower() in _AUTORESEARCH_MODES:
        return raw.lower()
    return None


def _json_fingerprint_value(value: Any) -> Any:
    """Return a deterministic JSON-safe representation for hashing only."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        # ``repr`` is stable and distinguishes values that JSON's default
        # encoder could otherwise round into the same textual form.
        return {"__float__": repr(value)}
    if isinstance(value, dict):
        return {
            str(key): _json_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_fingerprint_value(item) for item in value]
    return {"__type__": type(value).__name__, "__repr__": repr(value)}


def _training_fingerprint(
    examples: list[TrainingExample],
    *,
    role_id: int | None,
    organization: Organization | None,
) -> str:
    """Hash every fit-affecting input available before model/search spend.

    The example order is intentionally retained because it determines the
    train/holdout split. Full workspace settings are included conservatively:
    they contain the autoresearch mode and curated audit holdout, and an
    unrelated setting change merely causes one extra safe refit rather than an
    unsafe false reuse.
    """
    payload = {
        "contract": FIT_CONTRACT_VERSION,
        "role_id": role_id,
        "workspace_settings": (
            organization.workspace_settings
            if organization is not None
            and isinstance(organization.workspace_settings, dict)
            else {}
        ),
        "examples": [
            {
                "features": example.features,
                "label": float(example.label),
                "weight": float(example.weight),
                "role_id": example.role_id,
            }
            for example in examples
        ],
    }
    encoded = json.dumps(
        _json_fingerprint_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _policy_version_scope_query(
    db: Session, *, organization_id: int, role_id: int | None
):
    query = db.query(PolicyVersion).filter(
        PolicyVersion.organization_id == int(organization_id)
    )
    if role_id is None:
        return query.filter(PolicyVersion.role_id.is_(None))
    return query.filter(PolicyVersion.role_id == int(role_id))


def _lock_organization_for_fit(
    db: Session, *, organization_id: int
) -> Organization | None:
    """Serialize fitted-policy work for one organization.

    PostgreSQL holds the row lock until the surrounding nightly transaction
    commits, so overlapping Beat/retry workers cannot both pass the equivalent
    lookup and spend on the same fit. SQLite intentionally ignores
    ``FOR UPDATE`` while preserving identical single-worker test behavior.
    """
    return (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )


def _equivalent_current_candidate(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    fingerprint: str,
) -> PolicyVersion | None:
    rows = (
        _policy_version_scope_query(
            db, organization_id=organization_id, role_id=role_id
        )
        .filter(PolicyVersion.status.in_(("candidate", "shadow", "live")))
        .order_by(PolicyVersion.trained_at.desc(), PolicyVersion.id.desc())
        .all()
    )
    for row in rows:
        metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
        if (
            metrics.get("fit_contract_version") == FIT_CONTRACT_VERSION
            and metrics.get("training_fingerprint") == fingerprint
        ):
            return row
    return None


def _supersede_pending_candidates(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    keep_id: int,
    superseded_at: datetime,
) -> int:
    """Bound pending nightly output without touching live/manual shadow rows."""
    rows = (
        _policy_version_scope_query(
            db, organization_id=organization_id, role_id=role_id
        )
        .filter(
            PolicyVersion.status == "candidate",
            PolicyVersion.id != int(keep_id),
        )
        .all()
    )
    for row in rows:
        row.status = "superseded"
        row.archived_at = superseded_at
    return len(rows)


def _fit_candidate_model(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    train: list[TrainingExample],
    gold: list[TrainingExample],
) -> tuple[object, dict]:
    """Produce the candidate model + metrics, optionally via the autoresearch loop.

    Falls back to the one-shot ``fit_model`` whenever autoresearch is disabled,
    can't build its proposer, or finds no bias-clean improvement — so this is
    strictly non-regressive versus the historical fitter.
    """
    org = db.query(Organization).filter(Organization.id == organization_id).one_or_none()
    mode = _autoresearch_mode(org)
    if mode is None:
        return fit_model(train, role_id=role_id, gold_set=gold)

    audit_examples = load_audit_examples(org) if org is not None else []
    proposer = None
    if mode == "agentic":
        try:
            proposer = autoresearch.make_llm_proposer(org, role_id=role_id)
        except Exception:
            logger.exception(
                "autoresearch: LLM proposer build failed org=%s; falling back to grid",
                organization_id,
            )
            mode = "grid"

    try:
        result = autoresearch.search(
            train_examples=train,
            gold_set=gold,
            audit_examples=audit_examples,
            role_id=role_id,
            proposer=proposer,
        )
    except Exception:
        logger.exception(
            "autoresearch: search crashed org=%s role=%s; using one-shot fit",
            organization_id, role_id,
        )
        model, metrics = fit_model(train, role_id=role_id, gold_set=gold)
        metrics["autoresearch"] = {"mode": mode, "accepted": False, "error": True}
        return model, metrics

    if result.accepted and result.best_model is not None:
        metrics = dict(result.best_metrics or {})
        metrics["autoresearch"] = autoresearch.summarize(result, mode=mode)
        return result.best_model, metrics

    # No bias-clean improvement — keep the baseline candidate the gate expects.
    model, metrics = fit_model(train, role_id=role_id, gold_set=gold)
    metrics["autoresearch"] = autoresearch.summarize(result, mode=mode)
    return model, metrics


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

    Current production writers persist server-owned flattened policy inputs
    (``role_fit_score``, ``pre_screen_score``, ``taali_score``). Older rows may
    instead carry ``evidence["scores"]`` keyed by sub-agent. Normalize both
    shapes onto the production vocabulary; unknown legacy agents keep their
    historical ``<agent>_score`` feature. Missing keys remain absent and are
    treated as 0.0 by the fitter.
    """
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    feats: dict[str, float] = {}

    def put(name: str, value: Any) -> None:
        # bool is an int subclass but is never a meaningful fitted score.
        if (
            name not in feats
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            feats[name] = float(value)

    for key in _FLAT_FEATURE_KEYS:
        put(key, evidence.get(key))

    scores = evidence.get("scores") or {}
    if isinstance(scores, dict):
        for agent_name, blob in scores.items():
            if not isinstance(blob, dict):
                continue
            # Some legacy snapshots wrapped each agent's result in ``output``;
            # others stored the output mapping directly.
            output = blob.get("output")
            values = output if isinstance(output, dict) else blob
            semantic_keys: dict[str, str] = {}
            fallback_name = f"{agent_name}_score"
            if agent_name == "pre_screen":
                semantic_keys = {"score": "pre_screen_score"}
                fallback_name = "pre_screen_score"
            elif agent_name == "cv_scoring":
                semantic_keys = {
                    "role_fit_score": "role_fit_score",
                    "calibrated_p_advance": "calibrated_p_advance",
                    "score": "role_fit_score",
                }
                fallback_name = "role_fit_score"
            elif agent_name == "assessment_scoring":
                semantic_keys = {
                    "taali_score": "taali_score",
                    "assessment_score": "assessment_score",
                    "score": "taali_score",
                }
                fallback_name = "taali_score"
            elif agent_name == "graph_priors":
                semantic_keys = {
                    "p_advance": "graph_prior_p_advance",
                    "p_hired": "graph_prior_p_hired",
                }

            for source_key, feature_name in semantic_keys.items():
                put(feature_name, values.get(source_key))

            score = values.get("score")
            if score is None:
                score = blob.get("confidence")
            put(fallback_name, score)
            put(f"{agent_name}_uncertainty", values.get("uncertainty"))

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


def _fit_for_org(
    db: Session, *, organization_id: int, since: datetime, role_id: int | None
) -> CandidateFitResult:
    """Fit or safely reuse one ``PolicyVersion`` for an org/role scope."""

    examples = _collect_training_data(db, organization_id=organization_id, since=since)
    if role_id is None:
        if len(examples) < ORG_FIT_FLOOR:
            logger.info(
                "skipping org-level fit org=%s, n=%d below floor=%d",
                organization_id, len(examples), ORG_FIT_FLOOR,
            )
            return CandidateFitResult(None, created=False, reason="below_org_floor")
    else:
        role_n = sum(1 for ex in examples if ex.role_id == role_id)
        if role_n < ROLE_FIT_FLOOR:
            logger.info(
                "skipping role-level fit org=%s role=%s, n=%d below floor=%d",
                organization_id, role_id, role_n, ROLE_FIT_FLOOR,
            )
            return CandidateFitResult(None, created=False, reason="below_role_floor")

    # Acquire before fingerprint lookup *and* grid/agentic search. Without the
    # lock, overlapping task deliveries can both observe no current row,
    # duplicate model spend, and insert competing candidates.
    organization = _lock_organization_for_fit(
        db, organization_id=organization_id
    )
    if organization is None:
        logger.warning("skipping fit for missing organization_id=%s", organization_id)
        return CandidateFitResult(
            None, created=False, reason="organization_not_found"
        )
    fingerprint = _training_fingerprint(
        examples,
        role_id=role_id,
        organization=organization,
    )
    equivalent = _equivalent_current_candidate(
        db,
        organization_id=organization_id,
        role_id=role_id,
        fingerprint=fingerprint,
    )
    if equivalent is not None:
        if equivalent.status == "candidate":
            cleaned = _supersede_pending_candidates(
                db,
                organization_id=organization_id,
                role_id=role_id,
                keep_id=int(equivalent.id),
                superseded_at=datetime.now(timezone.utc),
            )
            if cleaned:
                db.flush()
        logger.info(
            "reusing equivalent fitted policy org=%s role=%s policy_version=%s",
            organization_id,
            role_id,
            equivalent.id,
        )
        return CandidateFitResult(
            equivalent,
            created=False,
            reason="equivalent_current_candidate",
        )

    # Last 20% of examples becomes the in-fitter gold set (for isotonic
    # calibration). The Phase 5 promotion gate uses its own held-out
    # gold set separately.
    cut = max(1, int(len(examples) * 0.8))
    train, gold = examples[:cut], examples[cut:]
    model, metrics = _fit_candidate_model(
        db,
        organization_id=organization_id,
        role_id=role_id,
        train=train,
        gold=gold,
    )
    metrics = dict(metrics or {})
    metrics.update(
        {
            "fit_contract_version": FIT_CONTRACT_VERSION,
            "training_fingerprint": fingerprint,
            "training_example_count": len(examples),
            "activation_status": "dormant_fail_closed",
        }
    )

    fitted_at = datetime.now(timezone.utc)
    row = PolicyVersion(
        organization_id=organization_id,
        role_id=role_id,
        model_kind="logistic_pooled",
        model_json=model.to_dict(),
        metrics_json=metrics,
        training_window_start=since,
        training_window_end=fitted_at,
        status="candidate",
    )
    db.add(row)
    db.flush()
    superseded = _supersede_pending_candidates(
        db,
        organization_id=organization_id,
        role_id=role_id,
        keep_id=int(row.id),
        superseded_at=fitted_at,
    )
    if superseded:
        logger.info(
            "superseded pending fitted policies org=%s role=%s count=%s",
            organization_id,
            role_id,
            superseded,
        )
    db.flush()
    return CandidateFitResult(row, created=True)


def fit_for_org(
    db: Session, *, organization_id: int, since: datetime, role_id: int | None
) -> PolicyVersion | None:
    """Return the newly fitted or equivalent current row, if data is sufficient.

    This compatibility wrapper preserves the original return shape. The nightly
    sweep uses :func:`_fit_for_org` to distinguish a new fit from a no-cost
    equivalent reuse in its summary.
    """
    return _fit_for_org(
        db,
        organization_id=organization_id,
        since=since,
        role_id=role_id,
    ).candidate


def run_nightly_fit(db: Session, *, since: datetime) -> dict:
    """Loop through every org + active role pair and try to fit.

    Returns a small summary dict so the Celery wrapper can log
    statistics.
    """
    summary = {"fitted": 0, "reused": 0, "skipped": 0, "by_org": {}}
    for org_id, in (
        db.query(DecisionPolicy.organization_id)
        .distinct()
        .order_by(DecisionPolicy.organization_id.asc())
        .all()
    ):
        # Org-level pass.
        try:
            result = _fit_for_org(
                db, organization_id=int(org_id), since=since, role_id=None
            )
        except Exception:
            logger.exception("org-level fit failed for org=%s", org_id)
            result = CandidateFitResult(None, created=False, reason="fit_failed")
        if result.created:
            summary["fitted"] += 1
            summary["by_org"].setdefault(int(org_id), {"org_level": True, "roles": []})
        elif result.candidate is not None:
            summary["reused"] += 1
        else:
            summary["skipped"] += 1
        # Per-role pass.
        role_ids = [
            r[0]
            for r in db.query(Role.id)
            .filter(Role.organization_id == int(org_id))
            .filter(Role.agentic_mode_enabled.is_(True))
            .order_by(Role.id.asc())
            .all()
        ]
        for role_id in role_ids:
            try:
                role_result = _fit_for_org(
                    db, organization_id=int(org_id), since=since, role_id=int(role_id)
                )
            except Exception:
                logger.exception("role-level fit failed for role=%s", role_id)
                role_result = CandidateFitResult(
                    None, created=False, reason="fit_failed"
                )
            if role_result.created:
                summary["fitted"] += 1
                summary["by_org"].setdefault(int(org_id), {"org_level": False, "roles": []})
                summary["by_org"][int(org_id)]["roles"].append(int(role_id))
            elif role_result.candidate is not None:
                summary["reused"] += 1
            else:
                summary["skipped"] += 1
    db.commit()
    return summary


__all__ = [
    "FIT_CONTRACT_VERSION",
    "ORG_FIT_FLOOR",
    "ROLE_FIT_FLOOR",
    "fit_for_org",
    "run_nightly_fit",
]
