"""Pure-Python verdict engine.

``evaluate(inputs, *, db) -> PolicyDecision``

Order of operations inside ``evaluate``:

  1. Load active policy (org default + role override merged).
  2. Apply intent overrides as an ephemeral overlay.
  3. Evaluate decision points in fixed priority order:
       send_assessment > advance_to_interview > reject
     The first one that produces a non-skip verdict wins. (A skip means
     "this decision is not for the agent right now"; the engine keeps
     looking.)
  4. Inside a decision point:
       - Rules are walked in priority-descending order. First match
         wins.
       - If no rule fires, fall back to threshold/weight evaluation:
         compute weighted score from inputs.scores + graph_priors, hold
         it against ``thresholds``, queue when above.
  5. Build a recruiter-readable ``rule_path`` and ``reasoning`` trace.

No DB writes. No LLM. No exceptions raised to the caller — failures
collapse into ``decision_type='no_action'`` with the reason populated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models.decision_policy import DecisionPolicy as DecisionPolicyRow
from .intent import apply_intent_overrides
from .schema import DecisionPoint, PolicyJson, Rule


logger = logging.getLogger("taali.decision_policy.engine")


# Decision-point evaluation order. The engine returns the first
# non-skip, non-no_action verdict from this sequence so a stale
# manual action on a higher-priority point doesn't prevent a lower
# one from queueing.
DECISION_POINT_ORDER = ("send_assessment", "advance_to_interview", "reject")


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass
class ManualAction:
    """Recent recruiter action on the application.

    Mirrors the shape produced by
    ``agent_runtime.manual_action_reader.read_recent_manual_actions``.
    Using a plain dataclass (not a SQLAlchemy row) keeps the engine
    pure-Python — unit tests build these by hand.
    """

    kind: str  # 'sent_assessment' | 'rejected' | 'advanced' | 'advanced_outcome'
    timestamp_iso: str
    actor_id: int | None = None
    reason: str | None = None


@dataclass
class DecisionInputs:
    """Everything the engine needs to render a verdict.

    The orchestrator composes this from sub-agent outputs + the manual
    action reader before calling ``evaluate``. The engine never looks
    outside this struct (plus the loaded policy row).
    """

    application_id: int
    role_id: int
    organization_id: int

    # Score-like signals from sub-agents. Keys the engine recognizes:
    #   role_fit_score, pre_screen_score, taali_score, assessment_score,
    #   calibrated_p_advance.
    # Values are floats in [0, 100] for scores, [0, 1] for probabilities.
    scores: dict[str, float] = field(default_factory=dict)

    # Graph priors (Phase 4 fills these). Keys: p_advance, p_hired,
    # neighbour_count, confidence. Confidence == 0 means "treat as
    # absent" — the engine collapses the prior's weight to 0 when so.
    graph_priors: dict[str, float] = field(default_factory=dict)

    # Parsed recruiter intent (Phase 2 sub-agent fills this). Keys:
    #   strictness_modifier (-1..+1), must_skills (list[str]),
    #   disqualifying_signals (list[str]), soft_signals (list[str]),
    #   constraints_parsed (list[dict]).
    intent: dict[str, Any] = field(default_factory=dict)

    # Boolean flags the engine inspects directly from rule conditions.
    # Examples set by the orchestrator:
    #   must_have_blocked, no_pending_assessment, assessment_completed,
    #   has_pending_assessment.
    flags: dict[str, bool] = field(default_factory=dict)

    # Recent manual recruiter actions on the application within the
    # policy's lookback window (already filtered by the orchestrator).
    manual_actions: list[ManualAction] = field(default_factory=list)

    # The role's *effective* role-fit threshold (recruiter-set
    # ``score_threshold`` in manual mode, or the agent-calibrated value in
    # auto mode), resolved by the caller. When set, it collapses the
    # reject ceiling and send-assessment floor onto this single boundary
    # at eval time (see ``apply_effective_threshold``) — so the recruiter's
    # one knob drives the agent live, and there's no "gap" band where a
    # candidate gets no verdict. ``None`` => fall back to the policy_json
    # thresholds unchanged (e.g. threshold genuinely unset).
    effective_role_fit_threshold: float | None = None


@dataclass
class PolicyDecision:
    """Verdict + reasoning trace.

    ``decision_type`` is one of:
      queue_send_assessment | queue_advance_decision |
      queue_reject_decision | queue_skip_assessment_reject_decision |
      auto_reject | skip | no_action

    The ``skip`` case means "no agent action this cycle"; the orchestrator
    just doesn't queue. ``no_action`` means "policy declined to fire" —
    same outward effect, but recorded distinctly so audits can tell the
    difference between "recruiter already handled" and "agent decided
    not to recommend".
    """

    decision_type: str
    confidence: float = 0.0
    reasoning: str = ""
    rule_path: list[str] = field(default_factory=list)
    policy_revision_id: int | None = None
    decision_point: str | None = None
    intent_overrode: bool = False
    skipped_due_to_manual: bool = False


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_active_policy(
    db: Session, *, organization_id: int, role_id: int | None
) -> DecisionPolicyRow:
    """Return the active policy row for (org, role).

    Lookup order:
      1. Role-specific row (role_id matches), activated, not deactivated.
      2. Org-default row (role_id IS NULL), activated, not deactivated.

    Raises ``LookupError`` if neither exists — that means bootstrap
    didn't run for this org and the orchestrator should bail loudly
    rather than silently fall through.
    """
    role_row: DecisionPolicyRow | None = None
    if role_id is not None:
        role_row = (
            db.query(DecisionPolicyRow)
            .filter(
                DecisionPolicyRow.organization_id == organization_id,
                DecisionPolicyRow.role_id == role_id,
                DecisionPolicyRow.activated_at.isnot(None),
                DecisionPolicyRow.deactivated_at.is_(None),
            )
            .order_by(DecisionPolicyRow.activated_at.desc())
            .first()
        )
    if role_row is not None:
        return role_row

    default_row = (
        db.query(DecisionPolicyRow)
        .filter(
            DecisionPolicyRow.organization_id == organization_id,
            DecisionPolicyRow.role_id.is_(None),
            DecisionPolicyRow.activated_at.isnot(None),
            DecisionPolicyRow.deactivated_at.is_(None),
        )
        .order_by(DecisionPolicyRow.activated_at.desc())
        .first()
    )
    if default_row is None:
        raise LookupError(
            f"no active decision policy for organization_id={organization_id}; "
            "bootstrap_org probably never ran for this org"
        )
    return default_row


def apply_effective_threshold(policy: "PolicyJson", value: float | None) -> "PolicyJson":
    """Collapse the reject ceiling and send-assessment floor onto a single
    boundary = ``value`` (the role's effective threshold).

    Rewrites ``role_fit_min`` and ``role_fit_max`` wherever they already
    appear in a decision point's thresholds (send_assessment carries
    ``role_fit_min``; reject carries ``role_fit_max``) to the same value.
    The result: ``role_fit < value`` -> reject, ``role_fit >= value`` ->
    send_assessment (send is evaluated before reject so the boundary
    candidate goes to send). This both (a) makes the recruiter's single
    threshold drive the agent live and (b) closes the old "gap" band
    (reject_ceiling .. send_floor) where a candidate received no verdict.

    Works on already-persisted policies without a migration: it only
    touches keys that exist, so the frozen org policy (role_fit_min=65,
    role_fit_max=30) is corrected purely at eval time. ``None`` is a
    no-op (fall back to the stored thresholds — e.g. threshold unset)."""
    if value is None:
        return policy
    value = float(max(0.0, min(100.0, value)))
    new_points: dict[str, DecisionPoint] = {}
    changed = False
    for point_name, point in policy.decision_points.items():
        thresholds = dict(point.thresholds)
        touched = False
        for key in ("role_fit_min", "role_fit_max"):
            if key in thresholds and thresholds[key] != value:
                thresholds[key] = value
                touched = True
        if touched:
            new_points[point_name] = point.model_copy(update={"thresholds": thresholds})
            changed = True
        else:
            new_points[point_name] = point
    if not changed:
        return policy
    return policy.model_copy(update={"decision_points": new_points})


_FAILED_ASSESSMENT_CONDITION = (
    "assessment_completed AND assessment_score < assessment_score_min"
)


def apply_completed_assessment_outcome_rules(policy: PolicyJson) -> PolicyJson:
    """Keep a completed failed assessment out of the passive cascade gap.

    Older persisted v1 policies predate the explicit failed-assessment branch:
    their advance rule only checked ``taali_score`` and their reject point only
    checked role fit.  A strong CV could therefore mask a low assessment and
    either advance the candidate or fall all the way through to ``no_action``.

    Treat the policy's existing ``assessment_score_min`` as the pass/fail
    boundary.  A highest-priority passive rule prevents advance below that
    floor, then a matching reject rule queues the existing HITL reject decision.
    This is an ephemeral compatibility overlay, so already-activated policies
    receive the invariant without mutating their frozen audit revision.  New
    policies persist the same rules directly in ``bootstrap._default_policy_json``.
    """
    advance = policy.decision_points.get("advance_to_interview")
    reject = policy.decision_points.get("reject")
    if advance is None or reject is None:
        return policy
    raw_floor = advance.thresholds.get("assessment_score_min")
    if raw_floor is None:
        return policy
    try:
        assessment_floor = float(raw_floor)
    except (TypeError, ValueError):  # pragma: no cover - schema already numeric
        return policy

    # Remove older/lower-priority copies before prepending the invariant.  The
    # schema permits recruiter-authored priorities up to 10_000, so insertion
    # order plus the maximum priority guarantees this guard wins ties.
    advance_rules = [
        rule
        for rule in advance.rules
        if not (
            rule.if_ == _FAILED_ASSESSMENT_CONDITION and rule.then == "skip"
        )
    ]
    advance_rules.insert(
        0,
        Rule.model_validate(
            {
                "if": _FAILED_ASSESSMENT_CONDITION,
                "then": "skip",
                "priority": 10_000,
                "reason_template": (
                    "Completed assessment is below the policy pass floor; "
                    "advance is blocked and the reject decision point must decide."
                ),
            }
        ),
    )

    reject_thresholds = dict(reject.thresholds)
    reject_thresholds["assessment_score_min"] = assessment_floor
    reject_rules = [
        rule
        for rule in reject.rules
        if not (
            rule.if_ == _FAILED_ASSESSMENT_CONDITION
            and rule.then == "queue_reject_decision"
        )
    ]
    reject_rules.insert(
        0,
        Rule.model_validate(
            {
                "if": _FAILED_ASSESSMENT_CONDITION,
                "then": "queue_reject_decision",
                "priority": 10_000,
                "reason_template": (
                    "Completed assessment is below the policy pass floor; "
                    "queueing reject for recruiter approval."
                ),
            }
        ),
    )

    new_points = dict(policy.decision_points)
    new_points["advance_to_interview"] = advance.model_copy(
        update={"rules": advance_rules}
    )
    new_points["reject"] = reject.model_copy(
        update={"thresholds": reject_thresholds, "rules": reject_rules}
    )
    return policy.model_copy(update={"decision_points": new_points})


def merge_role_into_default(
    default_json: dict, role_json: dict | None
) -> dict:
    """Shallow-merge a role-specific policy on top of the org default.

    Only keys present on ``role_json`` override defaults. The merge is
    one level deep on top-level keys *except* ``decision_points``, which
    merges per decision-point so an override of just ``send_assessment``
    leaves the other points untouched.
    """
    if not role_json:
        return default_json
    out: dict = {**default_json}
    for key, value in role_json.items():
        if key == "decision_points" and isinstance(value, dict):
            merged_points = {**(default_json.get("decision_points") or {})}
            for point_name, point_body in value.items():
                merged_points[point_name] = point_body
            out["decision_points"] = merged_points
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Tiny rule-condition evaluator
# ---------------------------------------------------------------------------


def _resolve_value(token: str, ctx: dict[str, Any]) -> Any:
    """Resolve a rule token to a runtime value.

    Tokens look like:
      - bare identifier      → ctx[identifier]   (None if absent)
      - dotted path          → ctx['a']['b']     (None on any miss)
      - numeric literal      → float
      - quoted string        → strip quotes
      - true / false / null  → Python equivalents
    """
    token = token.strip()
    if not token:
        return None
    if token.lower() in {"true", "false"}:
        return token.lower() == "true"
    if token.lower() == "null" or token.lower() == "none":
        return None
    if (token.startswith("'") and token.endswith("'")) or (
        token.startswith('"') and token.endswith('"')
    ):
        return token[1:-1]
    try:
        if "." in token and not token.replace(".", "", 1).isdigit():
            # Dotted access: a.b.c
            parts = token.split(".")
            cur: Any = ctx
            for p in parts:
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:
                    return None
            return cur
        return float(token)
    except ValueError:
        return ctx.get(token)


_OPS = (
    (">=", lambda a, b: _num(a) >= _num(b)),
    ("<=", lambda a, b: _num(a) <= _num(b)),
    ("==", lambda a, b: _eq(a, b)),
    ("!=", lambda a, b: not _eq(a, b)),
    (">", lambda a, b: _num(a) > _num(b)),
    ("<", lambda a, b: _num(a) < _num(b)),
)


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _eq(a: Any, b: Any) -> bool:
    # Trim quotes off string literals before comparing; numbers compare
    # via float coercion for consistency.
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        return _num(a) == _num(b)
    return str(a) == str(b)


def _eval_atom(expr: str, ctx: dict[str, Any]) -> bool:
    """Evaluate a single comparison or bare boolean."""
    expr = expr.strip()
    if not expr:
        return False
    # Negation: "NOT foo" / "!foo"
    if expr.lower().startswith("not "):
        return not _eval_atom(expr[4:], ctx)
    if expr.startswith("!"):
        return not _eval_atom(expr[1:], ctx)
    for sym, fn in _OPS:
        if sym in expr:
            left, right = expr.split(sym, 1)
            return fn(_resolve_value(left, ctx), _resolve_value(right, ctx))
    # Bare identifier — truthy check.
    val = _resolve_value(expr, ctx)
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.lower() not in {"", "false", "0", "no"}
    return bool(val)


def _eval_condition(expr: str, ctx: dict[str, Any]) -> bool:
    """Boolean expression with ``AND`` / ``OR`` (case-insensitive).

    No precedence beyond left-to-right within a connector — chains of
    ``AND`` evaluate AND-style, chains of ``OR`` OR-style. Mixed
    expressions group at the first OR (so ``a AND b OR c`` is ``(a AND
    b) OR c``). Recruiters writing rules through the Hub should be
    nudged toward parenthesis-free conjunctions; the engine is
    deliberately small.
    """
    expr = expr.strip()
    if not expr:
        return True
    # Split on OR first (lower precedence), then AND.
    or_parts = _split_outside_quotes(expr, " OR ")
    if len(or_parts) > 1:
        return any(_eval_condition(p, ctx) for p in or_parts)
    and_parts = _split_outside_quotes(expr, " AND ")
    if len(and_parts) > 1:
        return all(_eval_condition(p, ctx) for p in and_parts)
    return _eval_atom(expr, ctx)


def _split_outside_quotes(expr: str, sep: str) -> list[str]:
    """Split ``expr`` on case-insensitive ``sep`` outside quoted spans.

    Plain ``str.split`` would break on ``role_fit_score >= 'and 5'`` —
    unlikely in practice, but cheap to be correct.
    """
    out: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    sep_lower = sep.lower()
    i = 0
    while i < len(expr):
        ch = expr[i]
        if in_quote:
            buf.append(ch)
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            in_quote = ch
            buf.append(ch)
            i += 1
            continue
        if expr[i : i + len(sep)].lower() == sep_lower:
            out.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


# ---------------------------------------------------------------------------
# Manual-action skip
# ---------------------------------------------------------------------------


# Map manual action ``kind`` to the decision points the engine should
# skip when an action of that kind happened recently. A recruiter
# rejecting on their own makes both "advance" and "reject" agent
# verdicts redundant; sending an assessment makes "send_assessment"
# redundant, etc.
_MANUAL_SKIP_MAP: dict[str, set[str]] = {
    "sent_assessment": {"send_assessment"},
    "rejected": {"send_assessment", "advance_to_interview", "reject"},
    "advanced": {"advance_to_interview", "reject"},
    "advanced_outcome": {"advance_to_interview", "reject"},
}


def _decision_points_to_skip(actions: list[ManualAction]) -> set[str]:
    skip: set[str] = set()
    for action in actions:
        skip.update(_MANUAL_SKIP_MAP.get(action.kind, set()))
    return skip


# ---------------------------------------------------------------------------
# Per-decision-point evaluation
# ---------------------------------------------------------------------------


def _build_rule_context(
    inputs: DecisionInputs, point_name: str, point: DecisionPoint
) -> dict[str, Any]:
    """Flatten everything a rule's ``if`` may reference.

    Naming convention is verbatim — a rule that says
    ``role_fit_score >= role_fit_min`` looks up ``role_fit_score`` from
    inputs.scores and ``role_fit_min`` from the point's thresholds.
    """
    ctx: dict[str, Any] = {}
    ctx.update(inputs.scores)
    ctx.update(point.thresholds)
    # Graph priors are namespaced (``graph_prior_p_advance``) so they
    # don't collide with any score named "p_advance".
    for key, value in inputs.graph_priors.items():
        ctx[f"graph_prior_{key}"] = value
    ctx.update({k: bool(v) for k, v in inputs.flags.items()})
    # Intent fields surface under their keys for direct rule reference.
    for key, value in (inputs.intent or {}).items():
        ctx[f"intent_{key}"] = value
    return ctx


def _weighted_score(
    inputs: DecisionInputs, point: DecisionPoint
) -> tuple[float, dict[str, float]]:
    """Compute the weighted decision score from configured weights.

    Missing inputs contribute 0 (so a graph prior with confidence=0,
    which the orchestrator zeroes out, doesn't perturb the total).
    Returns (weighted_total, per_component_contribution).
    """
    contributions: dict[str, float] = {}
    total = 0.0
    for key, weight in point.weights.items():
        raw: float
        if key.startswith("graph_prior_"):
            tail = key[len("graph_prior_") :]
            raw = float(inputs.graph_priors.get(tail, 0.0) or 0.0)
            # Probability inputs are 0..1; lift to a 0..100 scale so they
            # combine sanibly with the score-shaped weights.
            if 0.0 <= raw <= 1.0:
                raw = raw * 100.0
        else:
            raw = float(inputs.scores.get(key, 0.0) or 0.0)
        contribution = raw * float(weight)
        contributions[key] = contribution
        total += contribution
    return total, contributions


def _confidence_from_inputs(
    inputs: DecisionInputs, point: DecisionPoint
) -> float:
    """Confidence = fraction of this point's weighted signals that are present.

    A decision point declares ``weights`` over the signals it cares
    about. If all of them are populated, confidence is 1.0; if half
    are, it's 0.5. Graph priors only count when the prior's own
    confidence is non-zero (cold start collapses cleanly).

    This is intentionally a *signal-density* measure, not an
    *agreement* one — a reject of a candidate scoring 5/100 is just as
    confident as an advance of a 95/100, provided we have the signal.
    """
    if not point.weights:
        return 0.0
    present = 0
    for key in point.weights.keys():
        if key.startswith("graph_prior_"):
            tail = key[len("graph_prior_") :]
            prior_conf = float(inputs.graph_priors.get("confidence", 0.0) or 0.0)
            if (
                inputs.graph_priors.get(tail) is not None
                and prior_conf > 0.0
            ):
                present += 1
        else:
            if inputs.scores.get(key) is not None:
                present += 1
    return present / len(point.weights)


def _evaluate_decision_point(
    inputs: DecisionInputs,
    *,
    point_name: str,
    point: DecisionPoint,
    skipped: set[str],
) -> PolicyDecision:
    """Evaluate one decision point. Returns a ``PolicyDecision``.

    A returned ``decision_type='skip'`` means "this point doesn't apply
    here, look at the next one". Anything else is final.
    """
    rule_path: list[str] = [f"point:{point_name}"]
    confidence = _confidence_from_inputs(inputs, point)

    # Manual-action skip is the highest-priority short-circuit.
    if point_name in skipped:
        return PolicyDecision(
            decision_type="skip",
            confidence=confidence,
            reasoning=(
                f"Recruiter recently took a manual action that supersedes "
                f"the agent's {point_name} verdict — skipping."
            ),
            rule_path=rule_path + ["manual_action_skip"],
            decision_point=point_name,
            skipped_due_to_manual=True,
        )

    ctx = _build_rule_context(inputs, point_name, point)
    weighted_total, contributions = _weighted_score(inputs, point)
    ctx["weighted_score"] = weighted_total

    # Rules in priority-descending order. First match wins.
    for rule in sorted(point.rules, key=lambda r: -r.priority):
        try:
            fired = _eval_condition(rule.if_, ctx)
        except Exception as exc:  # pragma: no cover — never fail evaluation
            logger.warning(
                "Rule eval crashed (%s): point=%s rule=%r — treating as no-match",
                exc, point_name, rule.if_,
            )
            fired = False
        rule_path.append(f"rule:{'fired' if fired else 'skipped'}:{rule.if_}")
        if not fired:
            continue
        action = rule.then
        if action == "skip":
            return PolicyDecision(
                decision_type="skip",
                confidence=confidence,
                reasoning=rule.reason_template
                or f"Rule fired in {point_name}: {rule.if_!r} -> skip",
                rule_path=rule_path,
                decision_point=point_name,
            )
        if action == "no_action":
            return PolicyDecision(
                decision_type="no_action",
                confidence=confidence,
                reasoning=rule.reason_template
                or f"Rule fired in {point_name}: {rule.if_!r} -> no_action",
                rule_path=rule_path,
                decision_point=point_name,
            )
        # Confidence floor gates queueing actions. ``auto_reject`` is a
        # hard rule — if a recruiter-authored rule fires it explicitly,
        # the verdict is already certain and shouldn't be diluted by the
        # absence of other signals (e.g. pre-screen-stage rejects fire
        # before role_fit_score is computed).
        if action != "auto_reject" and confidence < point.confidence_floor:
            return PolicyDecision(
                decision_type="no_action",
                confidence=confidence,
                reasoning=(
                    f"Rule fired in {point_name} ({rule.if_!r} -> {action}) "
                    f"but confidence {confidence:.2f} below floor "
                    f"{point.confidence_floor:.2f}; not queueing."
                ),
                rule_path=rule_path + [f"confidence_floor_blocked:{confidence:.2f}"],
                decision_point=point_name,
            )
        return PolicyDecision(
            decision_type=action,
            confidence=confidence,
            reasoning=rule.reason_template
            or _explain_match(point_name, rule, contributions, ctx),
            rule_path=rule_path,
            decision_point=point_name,
        )

    # No rule fired — fall through to no_action so the orchestrator
    # leaves the decision alone. We deliberately don't synthesise a
    # threshold-only verdict here: rules are the recruiter-facing
    # explanation surface, and silently queueing without one is harder
    # to audit.
    rule_path.append(f"no_rule_matched:weighted={weighted_total:.2f}")
    return PolicyDecision(
        decision_type="no_action",
        confidence=confidence,
        reasoning=(
            f"No rule matched in {point_name}. Weighted score "
            f"{weighted_total:.2f}; thresholds={dict(point.thresholds)}."
        ),
        rule_path=rule_path,
        decision_point=point_name,
    )


def _explain_match(
    point_name: str,
    rule: Any,
    contributions: dict[str, float],
    ctx: dict[str, Any],
) -> str:
    contrib_summary = ", ".join(
        f"{k}={v:.1f}" for k, v in sorted(contributions.items()) if v
    ) or "no weighted contributions"
    return (
        f"{point_name}: rule {rule.if_!r} fired -> {rule.then}. "
        f"Weighted contributions: {contrib_summary}."
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def evaluate(inputs: DecisionInputs, *, db: Session) -> PolicyDecision:
    """Pure-Python verdict for ``inputs``.

    Never raises to the caller. On any unexpected failure the verdict
    collapses to ``decision_type='no_action'`` with a populated
    ``reasoning`` so the orchestrator can record the failure in the
    AgentDecision audit trail without crashing the cycle.
    """
    try:
        row = load_active_policy(
            db,
            organization_id=inputs.organization_id,
            role_id=inputs.role_id,
        )
    except LookupError as exc:
        logger.info(
            "Decision policy unavailable organization_id=%s role_id=%s: %s",
            inputs.organization_id,
            inputs.role_id,
            exc,
        )
        return PolicyDecision(
            decision_type="no_action",
            reasoning="policy_not_configured",
            rule_path=["no_active_policy"],
        )

    raw_default_json = row.policy_json or {}
    role_json = None
    if row.role_id is not None:
        # When ``load_active_policy`` returned a role-specific row, that
        # row already represents the merged shape — don't double-merge.
        merged_json: dict = dict(raw_default_json)
    else:
        merged_json = merge_role_into_default(raw_default_json, role_json)

    try:
        policy = PolicyJson.model_validate(merged_json)
    except Exception:
        logger.exception("policy_json failed schema validation")
        return PolicyDecision(
            decision_type="no_action",
            reasoning="policy_configuration_invalid",
            rule_path=["policy_validation_failed"],
            policy_revision_id=int(row.revision_id),
        )

    # Collapse the reject/send boundary onto the role's effective
    # threshold FIRST (so it's the base), then let recruiter-intent
    # strictness nudge it.
    policy = apply_effective_threshold(policy, inputs.effective_role_fit_threshold)
    policy = apply_completed_assessment_outcome_rules(policy)

    intent_payload = inputs.intent or {}
    overlaid, intent_overrode = apply_intent_overrides(policy, intent_payload)

    skipped = _decision_points_to_skip(inputs.manual_actions)

    # ADR-0010 cutover: the verdict cascade is now PRODUCED by mainspring's
    # vendored deterministic engine (``PolicyEngine.evaluate_decision_points``),
    # not hand-rolled here. The translation closes each mainspring rule over
    # tali's own per-point context + ``_eval_condition`` (so the rule language
    # stays tali's), and mainspring's weighted-score/confidence math is
    # arithmetically identical to ``_weighted_score`` / ``_confidence_from_inputs``
    # — net-zero behaviour change on ``decision_type`` (proved by the parity
    # corpus in tests/decision_policy/test_engine_mainspring_parity.py). tali's
    # pure evaluator above stays as the audit oracle the parity test asserts
    # against. The abstention overlay is NOT applied (``evaluate_decision_points``
    # has no escalation path); tali's abstention lives downstream in
    # ``policy_evaluator`` and runs after this verdict.
    final = _verdict_via_mainspring(
        inputs=inputs,
        overlaid=overlaid,
        skipped=skipped,
        revision_id=int(row.revision_id),
        intent_overrode=intent_overrode,
    )
    return final


def _verdict_via_mainspring(
    *,
    inputs: DecisionInputs,
    overlaid: PolicyJson,
    skipped: set[str],
    revision_id: int,
    intent_overrode: bool,
) -> PolicyDecision:
    """Produce the verdict through mainspring's vendored cascade, returning the
    canonical tali ``PolicyDecision`` for the point mainspring selected.

    Division of labour (this is why every ``PolicyDecision`` field is identical
    to the old hand-rolled cascade, not just ``decision_type``):

    * mainspring's ``evaluate_decision_points`` is the CASCADE DRIVER — it walks
      the points in order, applies the confidence floor + manual-skip + passive
      fall-through, and selects the *winning* point + verdict. Its math/control
      flow is proved equal to the legacy walk by the parity corpus.
    * tali's KEPT pure ``_evaluate_decision_point`` then RENDERS the canonical
      verdict object for that selected point — reproducing the exact legacy
      ``reasoning`` + ``rule_path`` (the ``rule:fired:…`` / ``no_rule_matched``
      audit trace) byte-for-byte. We never re-derive the verdict, only re-render
      the trace for the point mainspring already chose, so the two can't diverge.

    Fall-through (no point queues) is reconstructed exactly like the legacy
    ``last_no_action`` / ``any_manual_skip`` propagation.
    """
    # Lazily imported to avoid an import cycle (the bridge imports engine helpers).
    from .mainspring_engine import derive_verdict

    present_points = [
        p for p in DECISION_POINT_ORDER if overlaid.decision_points.get(p) is not None
    ]
    if not present_points:
        return PolicyDecision(
            decision_type="no_action",
            reasoning="No decision points configured for this policy.",
            rule_path=["empty_policy"],
            policy_revision_id=revision_id,
            intent_overrode=intent_overrode,
        )

    ms_verdict = derive_verdict(inputs, overlaid, skip_points=skipped)

    # The point mainspring's cascade settled on (its rule_path leads with
    # ``point:<name>``). For a queueing verdict this is the winning point; for a
    # fall-through it's the LAST passive point — which matches the legacy
    # ``last_no_action`` (the last point the walk recorded before giving up).
    selected_point: str | None = None
    ms_path = list(ms_verdict.rule_path or [])
    if ms_path and isinstance(ms_path[0], str) and ms_path[0].startswith("point:"):
        selected_point = ms_path[0].split(":", 1)[1]

    is_queueing = ms_verdict.decision_type not in ("skip", "no_action")

    # Re-render the canonical tali verdict for the selected point via the kept
    # pure evaluator — this yields the legacy reasoning + rule_path verbatim.
    point_obj = overlaid.decision_points.get(selected_point) if selected_point else None
    if point_obj is not None:
        final = _evaluate_decision_point(
            inputs, point_name=selected_point, point=point_obj, skipped=skipped
        )
    else:  # pragma: no cover — defensive; cascade always names a present point
        final = PolicyDecision(
            decision_type=ms_verdict.decision_type,
            confidence=float(ms_verdict.confidence),
            reasoning=ms_verdict.reasoning,
            rule_path=ms_path,
            decision_point=selected_point,
        )

    # Legacy ``any_manual_skip`` propagation: on a NON-queueing final, surface
    # that a recruiter manually handled the candidate even if the walk moved on.
    if not is_queueing and any(p in skipped for p in present_points):
        final.skipped_due_to_manual = True

    final.policy_revision_id = revision_id
    final.intent_overrode = intent_overrode
    return final


__all__ = [
    "DECISION_POINT_ORDER",
    "DecisionInputs",
    "ManualAction",
    "PolicyDecision",
    "apply_completed_assessment_outcome_rules",
    "evaluate",
    "load_active_policy",
    "merge_role_into_default",
]
