"""Schema-driven interrogation engine.

The candidate assessment runtime opens each session with a fixed list of
**design decisions** the candidate must commit to before Claude will
write substantive code. Phase 1 of this feature (#422) used hand-authored
opener strings stored per task — fine for the 4 pilot tasks, but doesn't
scale: every new task = me writing prose; the response classifier was
prose-only too, so it mis-classified senior-engineer *reframes* (e.g.
"the underlying question is malformed — talk to finance first") as
*dodges*.

This module replaces that with a fully metadata-driven design:

- **Schema**: each task spec carries a top-level ``decision_points``
  array. One entry per load-bearing decision. Declarative fields only —
  no prose the runtime has to parse.
- **Renderer**: a pure template that turns ``decision_points`` into the
  opener message Claude posts as ai_prompts[0]. Replaces the hand-written
  ``task_opener`` string on every task.
- **Classifier**: one Haiku call per candidate turn that judges each
  open decision against its declared ``valid_commit`` / ``valid_reframes``
  / ``anti_patterns`` criteria and returns ``commit | reframe | dodge |
  vague | unaddressed``. Reframes are first-class — no more D2 bug.
- **State derivation**: walks the ai_prompts transcript and returns
  the latest known status per decision point. Used by the chat route
  before classifying (so it carries forward) and by the rubric grader
  at submit time.
- **Directive builder**: produces the system-prompt block describing
  current decision state + how Claude should handle each open one.
  Replaces the hand-written ``INTERROGATIVE MODE`` prose in
  ``candidate_claude_chat_routes._build_agentic_system_prompt``.

Why one module: keeping schema + renderer + classifier + state + grader
co-located is what makes the autogen target tractable. An LLM authoring
a new task only needs to emit a ``decision_points`` block — everything
downstream (opener, interrogation prompt, grading) operates on the same
data with no per-task code or prose anywhere else in the codebase.

Metering: classifier calls flow through ``MeteredAnthropicClient`` with
``sub_feature="interrogation_classifier"`` (platform invariant).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...platform.database import SessionLocal
from ...services.claude_client_resolver import get_metered_interrogation_client
from ...services.pricing_service import Feature
from ...services.provider_request_identity import provider_request_sha256
from ...services.usage_credit_reservations import (
    CreditReservation,
    reserve_credits,
)

logger = logging.getLogger("taali.assessments.interrogation")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# A decision point is a structured load-bearing design decision the
# candidate must commit to (or reframe). Fields are declarative so an
# autogen pipeline can emit them with no per-task code anywhere else.
#
# Required:
#   id              stable handle, snake_case, unique within the task
#   headline        short title rendered in the opener
#   tension         one-sentence framing of why this is hard to call
#   options         list of {label, summary} — the named alternatives
#   ask             the question Claude poses to force a commit
#   valid_commit    description of what a substantive commit looks like
#   valid_reframes  list of reframes that count as substantive (e.g.
#                   challenging the question premise). May be empty.
#   anti_patterns   list of dodge patterns to push back on. May be empty.
#
# Optional:
#   weight          relative weight inside the decisions dimension; defaults
#                   to equal across decision_points. Reserved for future use.

DECISION_POINT_REQUIRED = (
    "id",
    "headline",
    "tension",
    "options",
    "ask",
    "valid_commit",
)
DECISION_POINT_OPTIONAL = (
    "valid_reframes",
    "anti_patterns",
    "weight",
)

DECISION_STATUSES = {"commit", "reframe", "vague", "dodge", "unaddressed"}
# Order from most-engaged to least-engaged. Used both for "do not
# downgrade" semantics in the classifier carry-forward AND for the
# grading aggregation buckets.
STATUS_RANK = {
    "commit": 4,
    "reframe": 3,
    "vague": 2,
    "dodge": 1,
    "unaddressed": 0,
}
# Statuses that count as "decision resolved" — Claude should not push
# back on these. Reframes are first-class, by design.
RESOLVED_STATUSES = frozenset({"commit", "reframe"})


def validate_decision_points(decision_points: Any) -> List[str]:
    """Return a list of validation errors. Empty list = valid.

    Called from task_spec_loader on every spec load. Cheap, pure, no
    network. Schema errors here are deployment-fatal — we want them caught
    at boot, not at the candidate's first chat turn.
    """
    errors: List[str] = []
    if decision_points is None:
        return errors  # optional field; a task with no decision_points just skips interrogation
    if not isinstance(decision_points, list):
        return ["decision_points must be a list"]
    if not decision_points:
        return ["decision_points must be non-empty when present (drop the field instead)"]
    seen_ids: set[str] = set()
    for idx, dp in enumerate(decision_points):
        if not isinstance(dp, dict):
            errors.append(f"decision_points[{idx}] must be an object")
            continue
        for field in DECISION_POINT_REQUIRED:
            value = dp.get(field)
            if field == "options":
                if not isinstance(value, list) or len(value) < 2:
                    errors.append(f"decision_points[{idx}].options must be a list of ≥2 entries")
                else:
                    for j, opt in enumerate(value):
                        if not isinstance(opt, dict):
                            errors.append(f"decision_points[{idx}].options[{j}] must be an object")
                            continue
                        for opt_field in ("label", "summary"):
                            if not isinstance(opt.get(opt_field), str) or not opt.get(opt_field).strip():
                                errors.append(
                                    f"decision_points[{idx}].options[{j}].{opt_field} must be a non-empty string"
                                )
            else:
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"decision_points[{idx}].{field} must be a non-empty string")
        for field in ("valid_reframes", "anti_patterns"):
            value = dp.get(field)
            if value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                errors.append(f"decision_points[{idx}].{field} must be a list of non-empty strings")
        dp_id = dp.get("id")
        if isinstance(dp_id, str) and dp_id.strip():
            if dp_id in seen_ids:
                errors.append(f"decision_points[{idx}].id={dp_id!r} duplicates an earlier entry")
            seen_ids.add(dp_id)
    return errors


TRAP_REQUIRED = ("id", "planted", "tell")


def validate_traps(traps: Any) -> List[str]:
    """Validate the optional ``traps`` block. Empty list = valid.

    A trap is a planted, wrong-but-plausible path (a shortcut the agent might
    propose, a latent bug, a contradiction it might paper over). The grader's
    DISCERNMENT lens uses them to check whether the candidate CAUGHT and
    rejected the trap — the hardest-to-game appropriate-reliance signal. Each
    trap: ``{id, planted, tell, where?}``. Optional field overall; absent =
    the task simply has no planted-trap aid. Deployment-fatal on schema error
    (caught at boot, not at grade time), mirroring validate_decision_points.
    """
    errors: List[str] = []
    if traps is None:
        return errors
    if not isinstance(traps, list):
        return ["traps must be a list"]
    if not traps:
        return ["traps must be non-empty when present (drop the field instead)"]
    seen_ids: set[str] = set()
    for idx, trap in enumerate(traps):
        if not isinstance(trap, dict):
            errors.append(f"traps[{idx}] must be an object")
            continue
        for field in TRAP_REQUIRED:
            value = trap.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"traps[{idx}].{field} must be a non-empty string")
        where = trap.get("where")
        if where is not None and not (isinstance(where, str) and where.strip()):
            errors.append(f"traps[{idx}].where must be a non-empty string when present")
        tid = trap.get("id")
        if isinstance(tid, str) and tid.strip():
            if tid in seen_ids:
                errors.append(f"traps[{idx}].id={tid!r} duplicates an earlier entry")
            seen_ids.add(tid)
    return errors


# ---------------------------------------------------------------------------
# Opener renderer
# ---------------------------------------------------------------------------

# The greeting is the candidate's first impression of the whole runtime —
# half of assessment dropout happens in the first minutes, so it offers a
# zero-stakes first move before the decision contract below.
_OPENER_GREETING = (
    "Hi — I'm Claude, and I'll be pairing with you on this. I already have "
    "the repo open and I've read the brief, so there's nothing to set up. "
    "If you'd like to see where things stand first, just ask me to run the "
    "tests and I'll walk you through what's failing."
)
_OPENER_PREAMBLE = (
    "Before we start, {n} {decision_word} that need to come from you — "
    "not from me. These shape everything else; I'm not going to do the "
    "substantive work until you've made a call on each."
)
_OPENER_CLOSING = (
    "Once I have your answers I'll write the implementation that matches your "
    "choices. If you tell me \"whatever you think\" I'll push back — these need "
    "to come from you. You're assessed on the steering and design thinking you "
    "show here, not on whether we reach working code — I can write the code; "
    "the judgment is the part that's yours."
)


def render_opener(decision_points: List[Dict[str, Any]]) -> str:
    """Render the opener message from a structured decision_points list.

    Output is the assistant turn persisted as ai_prompts[0] when the
    candidate clicks "Start assessment". Pure function; no Anthropic
    call. Used by ``service.start_or_resume_assessment``.
    """
    if not decision_points:
        return ""
    n = len(decision_points)
    decision_word = "decision" if n == 1 else "decisions"
    sections: List[str] = [
        _OPENER_GREETING,
        "",
        _OPENER_PREAMBLE.format(n=n, decision_word=decision_word),
    ]
    for idx, dp in enumerate(decision_points, start=1):
        headline = str(dp.get("headline") or "").strip()
        tension = str(dp.get("tension") or "").strip()
        ask = str(dp.get("ask") or "").strip()
        options = dp.get("options") or []
        sections.append("")  # blank line between decisions
        sections.append(f"**{idx}. {headline}** {tension}")
        sections.append("")
        for opt in options:
            label = str(opt.get("label") or "").strip()
            summary = str(opt.get("summary") or "").strip()
            sections.append(f"- **{label}**: {summary}")
        sections.append("")
        sections.append(ask)
    sections.append("")
    sections.append(_OPENER_CLOSING)
    return "\n".join(sections).strip()


# ---------------------------------------------------------------------------
# State derivation — walk ai_prompts to find current per-dp status
# ---------------------------------------------------------------------------


def derive_interrogation_state(
    decision_points: List[Dict[str, Any]] | None,
    ai_prompts: Iterable[Dict[str, Any]] | None,
) -> Dict[str, str]:
    """Walk the persisted ai_prompts transcript and return the latest known
    status per decision point.

    Each candidate turn writes ``interrogation_state`` into its ai_prompts
    record (see ``candidate_claude_chat_routes``). We walk forward,
    upgrading status by ``STATUS_RANK`` so a one-time substantive answer
    can't be retroactively downgraded by a subsequent unaddressed turn.

    Returns ``{dp_id: status}`` defaulting to ``"unaddressed"`` for any
    decision_point id not yet seen.
    """
    state: Dict[str, str] = {}
    if not decision_points:
        return state
    for dp in decision_points:
        dp_id = dp.get("id")
        if isinstance(dp_id, str) and dp_id:
            state[dp_id] = "unaddressed"
    if not ai_prompts:
        return state
    for record in ai_prompts:
        if not isinstance(record, dict):
            continue
        per_dp = record.get("interrogation_state") or {}
        if not isinstance(per_dp, dict):
            continue
        for dp_id, payload in per_dp.items():
            if dp_id not in state:
                continue
            status = ""
            if isinstance(payload, dict):
                status = str(payload.get("status") or "").strip().lower()
            elif isinstance(payload, str):
                status = payload.strip().lower()
            if status not in DECISION_STATUSES:
                continue
            if STATUS_RANK[status] > STATUS_RANK[state[dp_id]]:
                state[dp_id] = status
    return state


def all_resolved(state: Dict[str, str]) -> bool:
    """True if every decision point is in a resolved status (commit or
    reframe). Drives interrogation-mode exit."""
    if not state:
        return True  # no decisions = nothing to interrogate
    return all(v in RESOLVED_STATUSES for v in state.values())


# ---------------------------------------------------------------------------
# Directive builder — task-agnostic system-prompt block
# ---------------------------------------------------------------------------

_DIRECTIVE_RULES = (
    "INTERROGATION RULES (apply to each decision listed above):\n"
    "- status=commit OR status=reframe: do NOT re-raise the decision. Treat it as resolved.\n"
    "- status=vague: politely push for the specific missing substance. Quote the decision's `ask` verbatim. ONE sentence.\n"
    "- status=dodge: push firmly. Name the anti-pattern (e.g. 'asking me to decide', 'listing options without picking'). ONE sentence.\n"
    "- status=unaddressed: answer the candidate's actual question first; then in a short postscript, list the still-open decisions by headline only.\n"
    "- If ALL decisions are commit/reframe: switch to pair-programmer mode. Do not re-raise decisions. Write the code that matches the candidate's choices.\n"
    "- NEVER make a decision for the candidate to keep the conversation moving. The friction IS the assessment.\n"
)


def build_interrogation_directive(
    decision_points: List[Dict[str, Any]] | None,
    state: Dict[str, str],
) -> str:
    """Return a system-prompt snippet describing current per-decision state
    and how Claude should respond. Empty string if all decisions resolved
    (caller skips the block entirely).

    The output is task-AGNOSTIC code; only the data inside the block
    varies. Adding a new task = JSON only.
    """
    if not decision_points or all_resolved(state):
        return ""
    lines: List[str] = ["INTERROGATION STATE (computed from the candidate's latest message):"]
    for dp in decision_points:
        dp_id = dp.get("id")
        if not isinstance(dp_id, str) or not dp_id:
            continue
        status = state.get(dp_id, "unaddressed")
        headline = str(dp.get("headline") or "").strip()
        ask = str(dp.get("ask") or "").strip()
        lines.append(f"- decision={dp_id} | status={status} | headline={headline} | ask={ask}")
    lines.append("")
    lines.append(_DIRECTIVE_RULES)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

_DEFAULT_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS_CLASSIFIER = 800

_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a strict classifier judging a candidate's chat response against a "
    "list of declared decision criteria.\n"
    "\n"
    "For EACH decision point, output one of these statuses:\n"
    "- commit: response matches the decision's `valid_commit` criterion — a "
    "specific choice named AND substantive justification.\n"
    "- reframe: response matches one of the decision's `valid_reframes` — a "
    "substantive challenge to the decision's premise (e.g. naming it as a "
    "requirements-gathering issue first). Reframes are FIRST-CLASS valid "
    "engagement, not evasions.\n"
    "- vague: response engages with the decision but lacks the specific "
    "substance `valid_commit` requires (e.g. listed options without picking; "
    "named a choice without justifying it).\n"
    "- dodge: response matches one of the decision's `anti_patterns` (e.g. "
    "'whatever you think', delegating back to Claude, pasting the brief).\n"
    "- unaddressed: response doesn't engage with this decision at all (it may "
    "be answering a different question — that's fine, mark unaddressed).\n"
    "\n"
    "Do NOT confuse 'reframe' with 'dodge'. A reframe is engagement, not "
    "evasion. Examples of reframes: 'the real question is X', 'we should ask "
    "the stakeholder Y before picking a label', 'this is a requirements "
    "problem, not a design problem'. These are SENIOR-engineer signals.\n"
    "\n"
    "Respond ONLY with valid JSON, no markdown, matching:\n"
    '{"by_dp": {"<dp_id>": {"status": "<commit|reframe|vague|dodge|unaddressed>", '
    '"rationale": "<10 words max>"}}}'
)


@dataclass(frozen=True)
class ClassificationOutcome:
    """Per-turn classifier result.

    ``by_dp`` is ``{dp_id: {"status": str, "rationale": str}}`` — the
    classifier's judgement for each decision point on the candidate's
    most recent message. ``error`` is set on failure; callers fall back
    to leaving prior state untouched (i.e. treat as ``unaddressed`` for
    this turn).
    """

    by_dp: Dict[str, Dict[str, str]]
    model_used: str
    error: Optional[str] = None


def _decision_points_for_classifier(decision_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trim decision_points to the fields the classifier needs. Keeps the
    prompt bounded and avoids leaking authoring metadata."""
    trimmed: List[Dict[str, Any]] = []
    for dp in decision_points:
        trimmed.append(
            {
                "id": dp.get("id"),
                "ask": dp.get("ask"),
                "valid_commit": dp.get("valid_commit"),
                "valid_reframes": list(dp.get("valid_reframes") or []),
                "anti_patterns": list(dp.get("anti_patterns") or []),
            }
        )
    return trimmed


def _parse_classifier_json(raw: str) -> Dict[str, Any]:
    """Parse the classifier response, tolerant of stray markdown fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        stripped = text.split("\n", 1)[-1] if "\n" in text else text
        text = stripped.rsplit("```", 1)[0].strip()
    return json.loads(text)


def _reserve_classifier_call(
    *,
    organization_id: int,
    assessment_id: Optional[int],
    role_id: int,
    trace_id: Optional[str],
    model: str,
    provider_request: Dict[str, Any],
) -> CreditReservation:
    """Hold one classifier call against both org credits and the role cap."""
    logical_trace = (
        str(trace_id).strip()
        if trace_id is not None and str(trace_id).strip()
        else f"assessment:{assessment_id or 'unknown'}:classifier"
    )
    with SessionLocal() as meter_db:
        reservation = reserve_credits(
            meter_db,
            organization_id=int(organization_id),
            feature=Feature.ASSESSMENT,
            external_ref=(
                f"usage-hold:{logical_trace}:{uuid.uuid4().hex}"
            ),
            metadata={
                "sub_feature": "interrogation_classifier",
                "assessment_id": assessment_id,
                "trace_id": logical_trace,
            },
            role_id=int(role_id),
            entity_id=(
                f"assessment:{int(assessment_id)}"
                if assessment_id is not None
                else None
            ),
            provider="anthropic",
            model=model,
            request_sha256=provider_request_sha256(provider_request),
            enforce_role_budget=True,
        )
        meter_db.commit()
        return reservation


def classify_response(
    *,
    decision_points: List[Dict[str, Any]],
    candidate_message: str,
    prior_state: Dict[str, str],
    api_key: str,
    organization_id: int,
    assessment_id: Optional[int] = None,
    role_id: Optional[int] = None,
    trace_id: Optional[str] = None,
    model: Optional[str] = None,
) -> ClassificationOutcome:
    """Run the classifier against ``candidate_message`` for every open
    decision point.

    Resilience: never raises. On API/JSON error returns an outcome with
    ``by_dp={}`` and ``error`` populated; the chat route then leaves the
    prior state untouched for this turn (semantically equivalent to
    "unaddressed for every dp this turn").

    Carry-forward: the caller is expected to merge this outcome's by_dp
    into the prior_state by STATUS_RANK upgrade — see
    ``derive_interrogation_state`` for the merge semantics.

    Metering: routes through ``MeteredAnthropicClient`` with
    ``sub_feature=interrogation_classifier``.
    """
    if not decision_points:
        return ClassificationOutcome(by_dp={}, model_used=model or _DEFAULT_CLASSIFIER_MODEL)
    if not api_key:
        return ClassificationOutcome(
            by_dp={}, model_used=model or _DEFAULT_CLASSIFIER_MODEL,
            error="interrogation_classifier_unconfigured",
        )

    chosen_model = (model or "").strip() or _DEFAULT_CLASSIFIER_MODEL
    client = get_metered_interrogation_client(
        api_key=api_key,
        organization_id=int(organization_id),
    )
    # MeteredAnthropicClient extracts ``feature`` / ``entity_id`` /
    # ``user_id`` / ``role_id`` / ``metadata`` from this dict; everything
    # else has to ride inside ``metadata`` to land on the UsageEvent row
    # (otherwise it silently disappears — the pre-2026-06-01 attribution
    # gap that left every classifier row with ``metadata=null`` and made
    # the cost reconciler bucket them as "other").
    classifier_meta: Dict[str, Any] = {
        "sub_feature": "interrogation_classifier",
    }
    if assessment_id is not None:
        classifier_meta["assessment_id"] = str(assessment_id)
    if trace_id:
        classifier_meta["trace_id"] = str(trace_id)
    metering: Dict[str, Any] = {
        "feature": "assessment",
        "organization_id": int(organization_id),
        "metadata": classifier_meta,
    }
    if assessment_id is not None:
        metering["entity_id"] = f"assessment:{assessment_id}"
    if role_id is not None:
        metering["role_id"] = int(role_id)
    if trace_id:
        # Top-level drives ClaudeCallLog retry/reconciliation grouping; the
        # nested copy above persists on UsageEvent metadata.
        metering["trace_id"] = str(trace_id)

    user_payload = {
        "decision_points": _decision_points_for_classifier(decision_points),
        "prior_state": dict(prior_state or {}),
        "candidate_message": (candidate_message or "").strip()[:4000],
    }
    user_prompt = (
        "Classify the candidate's latest message against EACH decision point. "
        "Return JSON only.\n\n"
        + json.dumps(user_payload, indent=2)
    )
    provider_request = {
        "model": chosen_model,
        "max_tokens": _MAX_TOKENS_CLASSIFIER,
        "system": _CLASSIFIER_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    # A candidate turn can race other applications and assessment calls on
    # the same role.  Check and hold at the provider boundary so a stale
    # route-level `spent < cap` read cannot overrun the universal role cap.
    # Legacy calls without role context retain their existing org-only path.
    try:
        if role_id is not None:
            reservation = _reserve_classifier_call(
                organization_id=int(organization_id),
                assessment_id=assessment_id,
                role_id=int(role_id),
                trace_id=trace_id,
                model=chosen_model,
                provider_request=provider_request,
            )
            metering["credit_reservation"] = reservation.as_metering_payload()
    except Exception as exc:  # noqa: BLE001 — fail closed before provider
        logger.info(
            "interrogation classifier admission blocked assessment=%s role=%s error_type=%s",
            assessment_id,
            role_id,
            type(exc).__name__,
        )
        return ClassificationOutcome(
            by_dp={},
            model_used=chosen_model,
            error="interrogation_classifier_budget_blocked",
        )

    try:
        response = client.messages.create(**provider_request, metering=metering)
        raw_text = response.content[0].text if response.content else ""
        payload = _parse_classifier_json(raw_text)
    except Exception as exc:  # noqa: BLE001 — resilience boundary
        logger.error(
            "interrogation classifier failed assessment=%s error_type=%s",
            assessment_id,
            type(exc).__name__,
        )
        return ClassificationOutcome(
            by_dp={},
            model_used=chosen_model,
            error="interrogation_classifier_failed",
        )

    raw_by_dp = payload.get("by_dp") if isinstance(payload, dict) else None
    if not isinstance(raw_by_dp, dict):
        return ClassificationOutcome(
            by_dp={},
            model_used=chosen_model,
            error="interrogation_classifier_output_invalid",
        )

    valid_dp_ids = {dp.get("id") for dp in decision_points if isinstance(dp.get("id"), str)}
    cleaned: Dict[str, Dict[str, str]] = {}
    for dp_id, entry in raw_by_dp.items():
        if dp_id not in valid_dp_ids:
            continue
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status not in DECISION_STATUSES:
            continue
        rationale = str(entry.get("rationale") or "").strip()[:200]
        cleaned[dp_id] = {"status": status, "rationale": rationale}

    return ClassificationOutcome(by_dp=cleaned, model_used=chosen_model)


def merge_state(
    prior_state: Dict[str, str],
    new_by_dp: Dict[str, Dict[str, str]],
) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    """Apply a fresh classifier outcome to the prior state with the
    "do not downgrade" rule.

    Returns ``(merged_state, persist_payload)``:
    - ``merged_state``: the new ``{dp_id: status}`` reflecting carry-forward
      semantics — once a candidate has committed/reframed, a subsequent
      tangential turn doesn't roll it back to vague.
    - ``persist_payload``: the per-dp dict to write into the ai_prompts
      record's ``interrogation_state`` field. Reflects the classifier's
      raw judgment for *this* turn (so the audit trail of how a candidate
      moved through the decisions is preserved), with the status
      upgraded to the merged value so the grader can read either the
      record-level or replay-derived state and get the same answer.
    """
    merged = dict(prior_state or {})
    persist: Dict[str, Dict[str, str]] = {}
    for dp_id, entry in (new_by_dp or {}).items():
        new_status = entry.get("status", "unaddressed")
        if new_status not in DECISION_STATUSES:
            new_status = "unaddressed"
        prior = merged.get(dp_id, "unaddressed")
        if STATUS_RANK[new_status] > STATUS_RANK[prior]:
            merged[dp_id] = new_status
            effective_status = new_status
        else:
            effective_status = prior
        persist[dp_id] = {
            "status": effective_status,
            "raw_status": new_status,
            "rationale": entry.get("rationale", ""),
        }
    # Ensure every known dp is represented in the persisted payload so
    # replay-from-transcript is faithful even if the classifier omitted
    # one for this turn.
    for dp_id, status in merged.items():
        if dp_id not in persist:
            persist[dp_id] = {"status": status, "raw_status": "unaddressed", "rationale": ""}
    return merged, persist


__all__ = [
    "DECISION_POINT_REQUIRED",
    "DECISION_POINT_OPTIONAL",
    "DECISION_STATUSES",
    "STATUS_RANK",
    "RESOLVED_STATUSES",
    "ClassificationOutcome",
    "all_resolved",
    "build_interrogation_directive",
    "classify_response",
    "derive_interrogation_state",
    "merge_state",
    "render_opener",
    "validate_decision_points",
]
