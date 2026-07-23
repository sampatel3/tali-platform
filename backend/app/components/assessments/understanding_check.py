"""Post-submit understanding check — does the candidate understand what the
agent built for them?

The runtime already interrogates the candidate BEFORE code exists
(``interrogation.py``): commit to the load-bearing decisions, or Claude won't
write anything substantive. This module is the mirror image on the other side
of submit — the work is frozen, and now the candidate has to show they can read
it.

Why it exists: an Anthropic randomized controlled trial found AI-assisted
participants finished in about the same time as controls but scored ~17 points
lower on a follow-up comprehension quiz (50% vs 67%), with the steepest decline
in debugging. Working code plus no comprehension is exactly the failure mode a
recruiter cannot see from a test-pass count, and it is what this measures.

Shape, and why:

- **Multiple choice only.** Free-text explanation is the stronger signal, but
  it needs an LLM judge at grade time and carries grading variance. MCQ grades
  deterministically in pure Python — zero metering rows at grade time, exactly
  reproducible on re-score, same property that makes the interrogation grader
  trustworthy. The generator earns the signal back by building distractors out
  of the candidate's OWN code, so a question cannot be answered by someone who
  has not read the diff.
- **Grounded in the frozen artifact.** Questions cite real files, functions and
  line ranges from ``artifact_delta``/``git_evidence`` — not the task brief.
  A model that has never seen this repo cannot answer them from the text alone.
- **Never blocks.** A bad score is evidence on the report under Discernment; it
  never rejects a candidate. Consistent with the platform rule that the agent
  warns and the recruiter decides.

Honest limitation: this is not cheat-proof. A candidate can paste a question
into another assistant in a second tab. What the design does is raise the cost
(per-question deadline, one question at a time, no back-navigation, questions
that need the candidate's own repo) and record tab-switch telemetry during the
check so a recruiter can see the attempt. It measures comprehension under time
pressure, not comprehension in a sealed room.

Metering: generation flows through ``MeteredAnthropicClient`` with
``sub_feature="understanding_check_generator"`` (platform invariant — every
Anthropic call writes a UsageEvent).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from anthropic import Anthropic

from ...platform.database import SessionLocal
from ...services.metered_anthropic_client import MeteredAnthropicClient
from ...services.pricing_service import Feature
from ...services.usage_credit_reservations import CreditReservation, reserve_credits

logger = logging.getLogger("taali.assessments.understanding_check")


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

# Five questions at 75 seconds each is a ~6 minute tail on a 30-90 minute
# assessment. Long enough to sample more than one part of the diff, short
# enough that it never reads as a second exam.
QUESTION_COUNT = 5
PER_QUESTION_SECONDS = 75

# Hard ceiling on the whole window, independent of how many questions remain.
# A candidate who walks away is expired by the sweep and graded on what they
# answered; grading never waits on a browser that is never coming back.
WINDOW_MINUTES = 15

# Status values for ``Assessment.understanding_check_status``. NULL means the
# run predates the feature and is graded as not-assessed, never as zero.
# Window is open but the questions do not exist yet. Submit sets this and
# returns immediately: generating five questions from a ~25k-token context is a
# 20-30s Sonnet call, and putting that inside the submit request would make the
# candidate watch a spinner on the one action that must feel instant — and risk
# the request timing out on the single call that freezes their work. Generation
# happens on the first check fetch instead, behind the loading state the check
# screen already shows.
STATUS_GENERATING = "generating"
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_EXPIRED = "expired"
STATUS_SKIPPED = "skipped"

TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_EXPIRED, STATUS_SKIPPED})
# Both states hold grading back — a window waiting on generation is still a
# window the candidate may answer in.
OPEN_STATUSES = frozenset({STATUS_GENERATING, STATUS_PENDING})

OPTION_COUNT = 4

_GENERATOR_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS_GENERATOR = 4000

# Bounds on what we hand the generator. The diff is the load-bearing input, so
# it gets the largest share.
_MAX_DIFF_CHARS = 18000
_MAX_FILE_CHARS = 4000
_MAX_FILES = 8
_MAX_TRANSCRIPT_TURNS = 12
_MAX_TURN_CHARS = 700


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Question schema
# ---------------------------------------------------------------------------

# One question:
#   id            stable handle, "q1".."q5"
#   prompt        the question text; must name a real file/function/symbol
#   options       exactly OPTION_COUNT strings
#   correct_index int, 0-based — SERVER ONLY, never serialized to a candidate
#   probe         what the question tests (see PROBES)
#   evidence      file path (and optional line range) the answer lives in
#   rationale     one sentence on why the correct option is correct; shown to
#                 the recruiter as evidence, never to the candidate
QUESTION_REQUIRED = ("id", "prompt", "options", "correct_index", "probe", "evidence")

# The four things worth asking about work an agent produced. Deliberately not
# "what does this line do" trivia — each probe targets a comprehension failure
# that costs something in production.
PROBES = {
    # Can they locate where a behaviour actually lives in their own diff?
    "locate",
    # Do they know what breaks if a specific piece of their code changes?
    "consequence",
    # Can they name the tradeoff the agent's chosen approach made?
    "tradeoff",
    # Do they know what their solution does NOT handle?
    "limitation",
}


def validate_questions(questions: Any) -> List[str]:
    """Validate a generated question set. Empty list = valid.

    Applied to the generator's output before anything is persisted, so a
    malformed model response degrades to "no check for this run" rather than
    to a broken candidate surface.
    """
    errors: List[str] = []
    if not isinstance(questions, list):
        return ["questions must be a list"]
    if not questions:
        return ["questions must be non-empty"]
    seen_ids: set[str] = set()
    for idx, question in enumerate(questions):
        if not isinstance(question, dict):
            errors.append(f"questions[{idx}] must be an object")
            continue
        for field in QUESTION_REQUIRED:
            if field not in question:
                errors.append(f"questions[{idx}].{field} is required")
        qid = question.get("id")
        if not isinstance(qid, str) or not qid.strip():
            errors.append(f"questions[{idx}].id must be a non-empty string")
        elif qid in seen_ids:
            errors.append(f"questions[{idx}].id '{qid}' is duplicated")
        else:
            seen_ids.add(qid)
        prompt = question.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"questions[{idx}].prompt must be a non-empty string")
        options = question.get("options")
        if not isinstance(options, list) or len(options) != OPTION_COUNT:
            errors.append(
                f"questions[{idx}].options must be a list of exactly {OPTION_COUNT}"
            )
        elif any(not isinstance(opt, str) or not opt.strip() for opt in options):
            errors.append(f"questions[{idx}].options must all be non-empty strings")
        elif len({opt.strip() for opt in options}) != OPTION_COUNT:
            errors.append(f"questions[{idx}].options must be distinct")
        correct = question.get("correct_index")
        if not isinstance(correct, int) or isinstance(correct, bool):
            errors.append(f"questions[{idx}].correct_index must be an integer")
        elif not 0 <= correct < OPTION_COUNT:
            errors.append(
                f"questions[{idx}].correct_index must be in 0..{OPTION_COUNT - 1}"
            )
        probe = question.get("probe")
        if not isinstance(probe, str) or probe.strip().lower() not in PROBES:
            errors.append(
                f"questions[{idx}].probe must be one of {sorted(PROBES)}"
            )
        evidence = question.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            errors.append(f"questions[{idx}].evidence must be a non-empty string")
    return errors


def candidate_question_view(question: Dict[str, Any], *, index: int, total: int) -> Dict[str, Any]:
    """The ONLY shape a candidate surface may see.

    Strips ``correct_index``, ``rationale`` and ``evidence`` — evidence names
    the file the answer lives in, which would hand over half the question.
    Every candidate-facing route serializes through this function; nothing
    returns a raw stored question.
    """
    return {
        "id": str(question.get("id") or ""),
        "prompt": str(question.get("prompt") or ""),
        "options": [str(opt) for opt in (question.get("options") or [])],
        "index": index,
        "total": total,
        "seconds_allowed": PER_QUESTION_SECONDS,
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_GENERATOR_SYSTEM_PROMPT = (
    "You write comprehension questions that test whether an engineer "
    "understands code an AI agent just produced for them.\n"
    "\n"
    "You are given the candidate's frozen diff, the final state of the files "
    "they changed, the design decisions they committed to, and their "
    "conversation with the agent. Write exactly {count} multiple-choice "
    "questions about THIS specific submission.\n"
    "\n"
    "The single most important rule: a competent engineer who has NOT read "
    "this diff must be unable to answer, and one who HAS read it must find it "
    "straightforward. Every question names a real file, function or symbol "
    "from the diff. Never ask general programming trivia, never ask about the "
    "task brief, never ask anything answerable from the file names alone.\n"
    "\n"
    "Distractors must come from the candidate's own code — a different "
    "function in the same file, the behaviour before the change, a related "
    "branch that does something similar. A distractor that is obviously wrong "
    "on sight makes the question free. All four options must be the same "
    "length and register; do not make the correct option the longest or the "
    "most detailed.\n"
    "\n"
    "Spread the questions across these probes, at most two of any one:\n"
    "- locate: where in the diff does a named behaviour actually live?\n"
    "- consequence: what breaks if a specific line/value in their code "
    "changes?\n"
    "- tradeoff: what did the chosen approach give up relative to a named "
    "alternative?\n"
    "- limitation: what input or case does the submitted solution NOT "
    "handle?\n"
    "\n"
    "Vary which index is correct across the set; do not put the answer at the "
    "same position every time.\n"
    "\n"
    "Respond ONLY with valid JSON, no markdown, matching:\n"
    '{"questions": [{"id": "q1", "prompt": "...", "options": ["a","b","c","d"], '
    '"correct_index": 0, "probe": "locate", "evidence": "path/to/file.py:42-58", '
    '"rationale": "one sentence on why that option is correct"}]}'
)


@dataclass(frozen=True)
class GenerationOutcome:
    """Result of one generation attempt.

    ``questions`` is empty when generation failed for any reason. Callers treat
    that as "this run gets no check" and let grading proceed — a generator
    outage must never strand a submitted assessment.
    """

    questions: List[Dict[str, Any]]
    model_used: str
    error: Optional[str] = None


def _render_diff(git_evidence: Optional[Dict[str, Any]]) -> str:
    evidence = git_evidence if isinstance(git_evidence, dict) else {}
    diff = str(evidence.get("diff_main") or evidence.get("diff") or "").strip()
    if not diff:
        return "(no diff captured)"
    if len(diff) > _MAX_DIFF_CHARS:
        return diff[:_MAX_DIFF_CHARS] + "\n... (diff truncated)"
    return diff


def _render_changed_files(
    repo_files: Optional[Dict[str, str]],
    git_evidence: Optional[Dict[str, Any]],
) -> str:
    """Final contents of the files the candidate actually touched.

    The diff alone shows changed hunks without their surroundings, which is not
    enough to build a distractor out of "a different function in the same
    file". Restricting to changed files keeps the prompt bounded and keeps the
    generator away from untouched scaffolding.
    """
    files = repo_files if isinstance(repo_files, dict) else {}
    if not files:
        return "(no repo files captured)"
    evidence = git_evidence if isinstance(git_evidence, dict) else {}
    delta = evidence.get("artifact_delta")
    changed: List[str] = []
    if isinstance(delta, dict):
        raw_changed = delta.get("changed_files") or delta.get("changed_paths") or []
        if isinstance(raw_changed, list):
            changed = [str(path) for path in raw_changed if isinstance(path, str)]
    ordered = [path for path in changed if path in files] or sorted(files)
    parts: List[str] = []
    for path in ordered[:_MAX_FILES]:
        body = str(files.get(path) or "")
        if len(body) > _MAX_FILE_CHARS:
            body = body[:_MAX_FILE_CHARS] + "\n... (truncated)"
        parts.append(f"--- {path} ---\n{body}")
    if len(ordered) > _MAX_FILES:
        parts.append(f"... ({len(ordered) - _MAX_FILES} more changed file(s) omitted)")
    return "\n\n".join(parts) if parts else "(no repo files captured)"


def _render_transcript(prompt_transcript: Optional[List[Dict[str, Any]]]) -> str:
    """Recent candidate/agent turns.

    Gives the generator the reasoning the agent narrated, so a question can ask
    about something the candidate was TOLD and may have waved through — the
    most productive place to find a comprehension gap.
    """
    turns = prompt_transcript if isinstance(prompt_transcript, list) else []
    if not turns:
        return "(no transcript)"
    lines: List[str] = []
    for i, turn in enumerate(turns[-_MAX_TRANSCRIPT_TURNS:], 1):
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("message") or "")[:_MAX_TURN_CHARS]
        asst = str(turn.get("response") or "")[:_MAX_TURN_CHARS]
        lines.append(f"### Turn {i}\n[Candidate]: {user}\n[Claude]: {asst}")
    return "\n\n".join(lines) if lines else "(no transcript)"


def _parse_generator_json(raw: str) -> Dict[str, Any]:
    """Parse the generator response, tolerant of stray markdown fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        stripped = text.split("\n", 1)[-1] if "\n" in text else text
        text = stripped.rsplit("```", 1)[0].strip()
    return json.loads(text)


def _reserve_generator_call(
    *,
    organization_id: int,
    assessment_id: Optional[int],
    role_id: int,
    trace_id: Optional[str],
) -> CreditReservation:
    """Hold one generation call against both org credits and the role cap."""
    logical_trace = (
        str(trace_id).strip()
        if trace_id is not None and str(trace_id).strip()
        else f"assessment:{assessment_id or 'unknown'}:understanding-check"
    )
    with SessionLocal() as meter_db:
        reservation = reserve_credits(
            meter_db,
            organization_id=int(organization_id),
            feature=Feature.ASSESSMENT,
            external_ref=f"usage-hold:{logical_trace}:{uuid.uuid4().hex}",
            metadata={
                "sub_feature": "understanding_check_generator",
                "assessment_id": assessment_id,
                "trace_id": logical_trace,
            },
            role_id=int(role_id),
            enforce_role_budget=True,
        )
        meter_db.commit()
        return reservation


def generate_questions(
    *,
    api_key: str,
    organization_id: int,
    git_evidence: Optional[Dict[str, Any]] = None,
    repo_files: Optional[Dict[str, str]] = None,
    prompt_transcript: Optional[List[Dict[str, Any]]] = None,
    decision_points: Optional[List[Dict[str, Any]]] = None,
    task_scenario: str = "",
    candidate_role: str = "",
    assessment_id: Optional[int] = None,
    role_id: Optional[int] = None,
    trace_id: Optional[str] = None,
    model: Optional[str] = None,
    count: int = QUESTION_COUNT,
) -> GenerationOutcome:
    """Generate the MCQ set from the candidate's frozen submission.

    Resilience: never raises. On API error, malformed JSON or a question set
    that fails ``validate_questions``, returns an outcome with empty
    ``questions`` and ``error`` populated. The caller marks the check skipped
    and releases grading — a generator failure costs the signal for one run,
    never the run itself.
    """
    chosen_model = (model or "").strip() or _GENERATOR_MODEL
    if not api_key:
        return GenerationOutcome(questions=[], model_used=chosen_model, error="missing api_key")

    diff = _render_diff(git_evidence)
    if diff == "(no diff captured)" and not repo_files:
        # Nothing to ask about. A run with no captured work is already flagged
        # as incomplete elsewhere; don't spend a call to ask about nothing.
        return GenerationOutcome(
            questions=[], model_used=chosen_model, error="no_submission_evidence",
        )

    client = MeteredAnthropicClient(
        inner=Anthropic(api_key=api_key),
        organization_id=int(organization_id),
    )
    # Everything except feature/entity_id/user_id/role_id/metadata has to ride
    # inside ``metadata`` or it never lands on the UsageEvent row and the cost
    # reconciler buckets the call as "other".
    generator_meta: Dict[str, Any] = {"sub_feature": "understanding_check_generator"}
    if assessment_id is not None:
        generator_meta["assessment_id"] = str(assessment_id)
    if trace_id:
        generator_meta["trace_id"] = str(trace_id)
    metering: Dict[str, Any] = {
        "feature": "assessment",
        "organization_id": int(organization_id),
        "metadata": generator_meta,
    }
    if assessment_id is not None:
        metering["entity_id"] = f"assessment:{assessment_id}"
    if role_id is not None:
        metering["role_id"] = int(role_id)
    if trace_id:
        metering["trace_id"] = str(trace_id)

    try:
        if role_id is not None:
            reservation = _reserve_generator_call(
                organization_id=int(organization_id),
                assessment_id=assessment_id,
                role_id=int(role_id),
                trace_id=trace_id,
            )
            metering["credit_reservation"] = reservation.as_metering_payload()
    except Exception as exc:  # noqa: BLE001 — fail closed before provider
        logger.info(
            "understanding check admission blocked assessment=%s role=%s err=%s",
            assessment_id,
            role_id,
            exc,
        )
        return GenerationOutcome(
            questions=[], model_used=chosen_model, error=f"budget_admission_failed: {exc}",
        )

    committed_decisions = [
        {"headline": dp.get("headline"), "ask": dp.get("ask")}
        for dp in (decision_points or [])
        if isinstance(dp, dict)
    ]
    user_prompt = "\n\n".join(
        [
            f"## Role\n{candidate_role or '(unspecified)'}",
            f"## Task brief (context only — never ask about this)\n{(task_scenario or '')[:2000]}",
            f"## Design decisions the candidate committed to\n{json.dumps(committed_decisions, indent=2)}",
            f"## The candidate's frozen diff\n{diff}",
            f"## Final contents of the changed files\n{_render_changed_files(repo_files, git_evidence)}",
            f"## Their conversation with the agent\n{_render_transcript(prompt_transcript)}",
            f"Write exactly {count} questions. Return JSON only.",
        ]
    )

    try:
        response = client.messages.create(
            model=chosen_model,
            max_tokens=_MAX_TOKENS_GENERATOR,
            system=_GENERATOR_SYSTEM_PROMPT.format(count=count),
            messages=[{"role": "user", "content": user_prompt}],
            metering=metering,
        )
        raw_text = response.content[0].text if response.content else ""
        payload = _parse_generator_json(raw_text)
    except Exception as exc:  # noqa: BLE001 — resilience boundary
        logger.warning(
            "understanding check generation failed assessment=%s err=%s",
            assessment_id,
            exc,
        )
        return GenerationOutcome(questions=[], model_used=chosen_model, error=str(exc))

    questions = payload.get("questions") if isinstance(payload, dict) else None
    if isinstance(questions, list):
        questions = questions[:count]
    errors = validate_questions(questions)
    if errors:
        logger.warning(
            "understanding check generation invalid assessment=%s errors=%s",
            assessment_id,
            errors[:5],
        )
        return GenerationOutcome(
            questions=[], model_used=chosen_model, error=f"invalid_questions: {errors[:3]}",
        )
    normalized = [
        {
            "id": str(question["id"]).strip(),
            "prompt": str(question["prompt"]).strip(),
            "options": [str(opt).strip() for opt in question["options"]],
            "correct_index": int(question["correct_index"]),
            "probe": str(question["probe"]).strip().lower(),
            "evidence": str(question["evidence"]).strip(),
            "rationale": str(question.get("rationale") or "").strip()[:400],
        }
        for question in questions
    ]
    return GenerationOutcome(questions=normalized, model_used=chosen_model)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def is_window_open(assessment: Any, *, now: Optional[datetime] = None) -> bool:
    """True while the candidate can still answer.

    Grading waits on exactly this predicate, so it is deliberately strict: an
    absent expiry reads as closed rather than as open forever.
    """
    if getattr(assessment, "understanding_check_status", None) not in OPEN_STATUSES:
        return False
    expires_at = getattr(assessment, "understanding_check_expires_at", None)
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (now or utcnow()) < expires_at


def answered_ids(assessment: Any) -> List[str]:
    answers = getattr(assessment, "understanding_check_answers", None)
    if not isinstance(answers, list):
        return []
    return [
        str(answer.get("question_id"))
        for answer in answers
        if isinstance(answer, dict) and answer.get("question_id")
    ]


def next_question(assessment: Any) -> Optional[Dict[str, Any]]:
    """The next unanswered question in candidate-safe form, or None.

    Serving strictly one at a time is what makes the per-question deadline mean
    anything — a candidate who can see all five up front can screenshot the set
    and spend the whole window on them together.
    """
    questions = getattr(assessment, "understanding_check_questions", None)
    if not isinstance(questions, list) or not questions:
        return None
    done = set(answered_ids(assessment))
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        if str(question.get("id") or "") in done:
            continue
        return candidate_question_view(question, index=index, total=len(questions))
    return None


# Clock skew, request latency and render time all sit between the server
# stamping served_at and the candidate seeing the question. Anything inside the
# grace period counts as on-time.
_SERVED_GRACE_SECONDS = 10


def mark_served(
    assessment: Any,
    question_id: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Stamp when a question was first handed to the candidate, and return it.

    This is what makes the per-question deadline real rather than advisory: the
    browser's self-reported ``elapsed_ms`` is candidate-controlled, so the
    authoritative clock has to start on the server. Idempotent — a re-fetch
    (refresh, flaky connection) returns the ORIGINAL stamp rather than
    restarting the timer, so reloading the page is never a way to buy time.
    """
    questions = getattr(assessment, "understanding_check_questions", None)
    if not isinstance(questions, list):
        return None
    stamped = now or utcnow()
    updated: List[Dict[str, Any]] = []
    served_at: Optional[datetime] = None
    changed = False
    for question in questions:
        if not isinstance(question, dict) or str(question.get("id") or "") != question_id:
            updated.append(question)
            continue
        existing = _parse_iso(question.get("served_at"))
        if existing is not None:
            served_at = existing
            updated.append(question)
            continue
        served_at = stamped
        updated.append({**question, "served_at": stamped.isoformat()})
        changed = True
    if changed:
        # Reassign so SQLAlchemy sees the JSON column as dirty.
        assessment.understanding_check_questions = updated
    return served_at


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_answer(
    assessment: Any,
    *,
    question_id: str,
    selected_index: Optional[int],
    elapsed_ms: Optional[int] = None,
    tab_switches: int = 0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Append one graded answer. Returns the stored record.

    Grading is deterministic and happens here, at answer time, so the stored
    record is self-describing and a re-score never re-runs a model. Raises
    ``ValueError`` for an unknown question or a repeat answer; the route maps
    those to 4xx.

    ``selected_index=None`` records a deliberate skip (the browser's
    per-question deadline elapsed). An answer that arrives after the SERVER's
    deadline is forced to the same outcome no matter what the client sent —
    otherwise the deadline would only bind candidates who chose to honour it.
    """
    questions = getattr(assessment, "understanding_check_questions", None)
    if not isinstance(questions, list):
        raise ValueError("no_questions")
    match = next(
        (
            question
            for question in questions
            if isinstance(question, dict) and str(question.get("id") or "") == question_id
        ),
        None,
    )
    if match is None:
        raise ValueError("unknown_question")
    if question_id in set(answered_ids(assessment)):
        raise ValueError("already_answered")
    if selected_index is not None and not 0 <= int(selected_index) < len(match.get("options") or []):
        raise ValueError("invalid_option")

    answered_at = now or utcnow()
    served_at = _parse_iso(match.get("served_at"))
    server_elapsed_ms: Optional[int] = None
    late = False
    if served_at is not None:
        server_elapsed_ms = max(0, int((answered_at - served_at).total_seconds() * 1000))
        late = server_elapsed_ms > (PER_QUESTION_SECONDS + _SERVED_GRACE_SECONDS) * 1000

    correct_index = int(match.get("correct_index", -1))
    is_correct = (
        not late
        and selected_index is not None
        and int(selected_index) == correct_index
    )
    record = {
        "question_id": question_id,
        "selected_index": None if selected_index is None else int(selected_index),
        "correct_index": correct_index,
        "is_correct": bool(is_correct),
        "timed_out": selected_index is None or late,
        "probe": str(match.get("probe") or ""),
        # Authoritative server-measured duration; None only for a question
        # answered without ever having been served (not reachable via the API).
        "elapsed_ms": server_elapsed_ms,
        # What the browser claimed. Kept alongside so a large divergence from
        # elapsed_ms is visible rather than silently discarded.
        "client_elapsed_ms": int(elapsed_ms) if isinstance(elapsed_ms, int) else None,
        "tab_switches": max(0, int(tab_switches or 0)),
        "answered_at": answered_at.isoformat(),
    }
    existing = list(getattr(assessment, "understanding_check_answers", None) or [])
    assessment.understanding_check_answers = existing + [record]
    return record


def score_answers(assessment: Any) -> Optional[float]:
    """Percentage of the GENERATED questions answered correctly, 0-100.

    The denominator is the questions asked, not the questions answered, so
    abandoning the check partway scores what it should rather than rewarding a
    candidate for stopping after their last correct answer. Returns None when
    no questions were ever generated — not-assessed, not zero.
    """
    questions = getattr(assessment, "understanding_check_questions", None)
    if not isinstance(questions, list) or not questions:
        return None
    answers = getattr(assessment, "understanding_check_answers", None) or []
    correct = sum(
        1
        for answer in answers
        if isinstance(answer, dict) and answer.get("is_correct")
    )
    return round(100.0 * correct / len(questions), 1)


def reserve_window(assessment: Any, *, now: Optional[datetime] = None) -> None:
    """Open the window at submit time, before any question exists.

    Pure field assignment — no model call — so submit stays as fast as it is
    today. The expiry clock starts here rather than at generation so a
    candidate who never opens the check still expires on schedule instead of
    parking grading indefinitely.
    """
    started = now or utcnow()
    assessment.understanding_check_questions = []
    assessment.understanding_check_answers = []
    assessment.understanding_check_status = STATUS_GENERATING
    assessment.understanding_check_started_at = started
    assessment.understanding_check_expires_at = started + timedelta(minutes=WINDOW_MINUTES)
    assessment.understanding_check_completed_at = None
    assessment.understanding_check_score = None


def open_window(
    assessment: Any,
    questions: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> None:
    """Attach generated questions and make the window answerable.

    Safe to call on a reserved window or directly on a fresh row; the expiry is
    only set when it isn't already, so filling in questions never extends a
    clock that has been running since submit.
    """
    started = now or utcnow()
    assessment.understanding_check_questions = questions
    assessment.understanding_check_answers = []
    assessment.understanding_check_status = STATUS_PENDING
    if not isinstance(getattr(assessment, "understanding_check_started_at", None), datetime):
        assessment.understanding_check_started_at = started
    if not isinstance(getattr(assessment, "understanding_check_expires_at", None), datetime):
        assessment.understanding_check_expires_at = started + timedelta(minutes=WINDOW_MINUTES)
    assessment.understanding_check_completed_at = None
    assessment.understanding_check_score = None


def close_window(
    assessment: Any,
    *,
    status: str,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Finalize the check and return the score. Idempotent.

    Called from three places — the last answer, the expiry sweep, and the skip
    path when generation produced nothing. Re-closing an already-closed check
    leaves the recorded status alone so a late sweep tick cannot rewrite a
    completed run as expired.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"invalid terminal status: {status}")
    current = getattr(assessment, "understanding_check_status", None)
    if current in TERMINAL_STATUSES:
        return getattr(assessment, "understanding_check_score", None)
    assessment.understanding_check_status = status
    assessment.understanding_check_completed_at = now or utcnow()
    score = score_answers(assessment)
    assessment.understanding_check_score = score
    return score


def skip_window(assessment: Any, *, reason: str, now: Optional[datetime] = None) -> None:
    """Mark a run as having no check at all (generation failed, no work found).

    Distinct from ``expired``: nothing was ever asked, so the recruiter report
    shows "not assessed" rather than "did not complete".
    """
    assessment.understanding_check_questions = []
    assessment.understanding_check_answers = []
    assessment.understanding_check_status = STATUS_SKIPPED
    assessment.understanding_check_started_at = now or utcnow()
    assessment.understanding_check_expires_at = None
    assessment.understanding_check_completed_at = now or utcnow()
    assessment.understanding_check_score = None
    logger.info(
        "understanding check skipped assessment=%s reason=%s",
        getattr(assessment, "id", None),
        reason,
    )


def summarize(assessment: Any) -> Dict[str, Any]:
    """Recruiter-facing summary. Safe to serialize — no correct answers leak
    before the candidate has answered, because a closed check is the only one
    that carries per-question detail."""
    status = getattr(assessment, "understanding_check_status", None)
    questions = getattr(assessment, "understanding_check_questions", None) or []
    answers = getattr(assessment, "understanding_check_answers", None) or []
    total = len(questions) if isinstance(questions, list) else 0
    correct = sum(
        1 for answer in answers if isinstance(answer, dict) and answer.get("is_correct")
    )
    detail: List[Dict[str, Any]] = []
    if status in TERMINAL_STATUSES and isinstance(questions, list):
        by_id = {
            str(answer.get("question_id")): answer
            for answer in answers
            if isinstance(answer, dict)
        }
        for question in questions:
            if not isinstance(question, dict):
                continue
            answer = by_id.get(str(question.get("id") or "")) or {}
            detail.append(
                {
                    "id": question.get("id"),
                    "prompt": question.get("prompt"),
                    "probe": question.get("probe"),
                    "evidence": question.get("evidence"),
                    "rationale": question.get("rationale"),
                    "options": question.get("options"),
                    "correct_index": question.get("correct_index"),
                    "selected_index": answer.get("selected_index"),
                    "is_correct": bool(answer.get("is_correct")),
                    "timed_out": bool(answer.get("timed_out")),
                    "elapsed_ms": answer.get("elapsed_ms"),
                    "tab_switches": answer.get("tab_switches"),
                    "answered": bool(answer),
                }
            )
    return {
        "status": status,
        "score": getattr(assessment, "understanding_check_score", None),
        "questions_total": total,
        "questions_answered": len(answers),
        "questions_correct": correct,
        # Tab switches DURING the check. Not proof of anything on its own —
        # surfaced so a recruiter reading a strong score can see how it was
        # earned.
        "tab_switches_during_check": sum(
            int(answer.get("tab_switches") or 0)
            for answer in answers
            if isinstance(answer, dict)
        ),
        "started_at": _iso(getattr(assessment, "understanding_check_started_at", None)),
        "completed_at": _iso(getattr(assessment, "understanding_check_completed_at", None)),
        "questions": detail,
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


__all__ = [
    "OPTION_COUNT",
    "PER_QUESTION_SECONDS",
    "PROBES",
    "QUESTION_COUNT",
    "STATUS_COMPLETED",
    "STATUS_EXPIRED",
    "STATUS_PENDING",
    "STATUS_SKIPPED",
    "TERMINAL_STATUSES",
    "WINDOW_MINUTES",
    "OPEN_STATUSES",
    "STATUS_GENERATING",
    "GenerationOutcome",
    "answered_ids",
    "reserve_window",
    "candidate_question_view",
    "close_window",
    "generate_questions",
    "is_window_open",
    "mark_served",
    "next_question",
    "open_window",
    "record_answer",
    "score_answers",
    "skip_window",
    "summarize",
    "validate_questions",
]
