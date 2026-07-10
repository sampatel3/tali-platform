"""Deterministic process features derived from the captured session.

The strongest external evidence on AI-assisted work says the discriminating
behaviours are verification (did they test before claiming done) and
discernment (did they challenge the agent's output) — see
docs/ASSESSMENT_E2E_DEEP_DIVE.md §3. Both are countable from telemetry we
already capture per turn (``assessments.ai_prompts``) and per event
(``assessments.timeline``); no new capture and no model calls.

``compute_process_features`` runs at submit time in ``submission_runtime``.
Its output is persisted under ``score_breakdown.process_features`` (recruiter
evidence) and rendered into the rubric grader's prompt via
``ScoringArtifacts.process_features_excerpt()`` — handing the LLM judge the
deterministic loop skeleton instead of making it infer counts from a
truncated transcript.

Everything here is a transparent count over server-written records. The
timestamps on ai_prompts records are written server-side at persist time, so
cadence features don't trust the client clock.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# Tool names that mutate the workspace (old executor + agent-SDK spellings).
_EDIT_TOOL_NAMES = frozenset({"write_file", "apply_edit", "write", "edit", "notebookedit", "str_replace_editor"})
# Tool names that execute commands (a pytest run through chat is verification).
_RUN_TOOL_NAMES = frozenset({"run_command", "bash"})
_TEST_COMMAND_RE = re.compile(r"\b(pytest|python -m pytest|npm test|vitest|unittest)\b", re.IGNORECASE)

# Explicit, auditable challenge markers — a candidate pushing back on or
# correcting the agent. Deliberately narrow: false positives here would
# reward noise. Reviewed alongside the discernment lens, not a score itself.
_CHALLENGE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bthat'?s (?:wrong|not right|not what)\b",
        r"\bnot what i asked\b",
        r"\bwhy did you\b",
        r"\binstead of\b",
        r"\brevert\b",
        r"\bundo that\b",
        r"\byou (?:missed|broke|skipped|ignored)\b",
        r"\bdon'?t (?:do|change|touch)\b",
        r"\bi disagree\b",
        r"\bshouldn'?t (?:have|be)\b",
        r"\bare you sure\b",
        r"\bdouble[- ]check\b",
    )
]

_QUICK_FOLLOW_UP_MAX_SECONDS = 120
_QUICK_FOLLOW_UP_MAX_WORDS = 25


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _tool_name(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("name") or "").strip().lower()
    return str(entry or "").strip().lower()


def _tool_input_text(entry: Any) -> str:
    if isinstance(entry, dict):
        try:
            return str(entry.get("input") or "")
        except Exception:
            return ""
    return ""


def _tool_is_error(entry: Any) -> bool:
    return bool(isinstance(entry, dict) and entry.get("is_error"))


def _candidate_turns(ai_prompts: Iterable[Any]) -> List[Dict[str, Any]]:
    """Records where the candidate actually said something (opener turns
    have an empty ``message`` and are Claude-only — not candidate activity)."""
    turns = []
    for record in ai_prompts or []:
        if isinstance(record, dict) and str(record.get("message") or "").strip():
            turns.append(record)
    return turns


def compute_process_features(
    ai_prompts: Optional[List[Any]],
    timeline: Optional[List[Any]],
) -> Dict[str, Any]:
    """Derive the deterministic process-feature set from captured telemetry.

    Pure function over persisted JSON; safe on legacy records (string tool
    calls, missing timestamps) — any unparseable slice degrades to 0/None
    for its feature rather than raising.
    """
    prompts = [r for r in (ai_prompts or []) if isinstance(r, dict)]
    events = [e for e in (timeline or []) if isinstance(e, dict)]
    turns = _candidate_turns(prompts)

    # --- Verification ------------------------------------------------------
    test_run_events = [
        e for e in events
        if e.get("event_type") == "code_execute" and e.get("tests_total") is not None
    ]
    run_events = [e for e in events if e.get("event_type") == "code_execute"]

    agent_test_runs = 0
    tool_errors = 0
    last_edit_ts: Optional[datetime] = None
    for record in prompts:
        record_ts = _parse_ts(record.get("timestamp"))
        for call in record.get("tool_calls_made") or []:
            name = _tool_name(call)
            if name in _RUN_TOOL_NAMES and _TEST_COMMAND_RE.search(_tool_input_text(call)):
                agent_test_runs += 1
            if name in _EDIT_TOOL_NAMES and record_ts is not None:
                last_edit_ts = record_ts if last_edit_ts is None else max(last_edit_ts, record_ts)
            if _tool_is_error(call):
                tool_errors += 1

    for e in events:
        if e.get("event_type") == "repo_file_save":
            ts = _parse_ts(e.get("timestamp"))
            if ts is not None:
                last_edit_ts = ts if last_edit_ts is None else max(last_edit_ts, ts)

    last_test_ts: Optional[datetime] = None
    for e in run_events:
        ts = _parse_ts(e.get("timestamp"))
        if ts is not None:
            last_test_ts = ts if last_test_ts is None else max(last_test_ts, ts)

    test_runs = len(test_run_events) + agent_test_runs
    edits_after_last_test: Optional[bool] = None
    if last_test_ts is not None and last_edit_ts is not None:
        edits_after_last_test = last_edit_ts > last_test_ts
    elif last_test_ts is not None:
        edits_after_last_test = False

    # --- Discernment proxies -----------------------------------------------
    challenge_marker_turns = sum(
        1 for t in turns
        if any(p.search(str(t.get("message") or "")) for p in _CHALLENGE_PATTERNS)
    )

    # --- Cadence (server-side timestamps only) -----------------------------
    stamps = [ts for ts in (_parse_ts(t.get("timestamp")) for t in turns) if ts is not None]
    gaps = [
        (b - a).total_seconds()
        for a, b in zip(stamps, stamps[1:])
        if (b - a).total_seconds() >= 0
    ]
    quick_follow_up_turns = 0
    for prev, cur in zip(turns, turns[1:]):
        a, b = _parse_ts(prev.get("timestamp")), _parse_ts(cur.get("timestamp"))
        if a is None or b is None:
            continue
        words = len(str(cur.get("message") or "").split())
        if 0 <= (b - a).total_seconds() <= _QUICK_FOLLOW_UP_MAX_SECONDS and words <= _QUICK_FOLLOW_UP_MAX_WORDS:
            quick_follow_up_turns += 1

    return {
        "candidate_turns": len(turns),
        "test_runs": test_runs,
        "test_runs_with_results": len(test_run_events),
        "agent_test_runs": agent_test_runs,
        "edits_after_last_test": edits_after_last_test,
        "tool_errors": tool_errors,
        "challenge_marker_turns": challenge_marker_turns,
        "quick_follow_up_turns": quick_follow_up_turns,
        "median_inter_turn_seconds": round(statistics.median(gaps), 1) if gaps else None,
        "max_idle_seconds": round(max(gaps), 1) if gaps else None,
        "single_mega_prompt": len(turns) == 1,
    }


def render_process_features(features: Optional[Dict[str, Any]]) -> str:
    """One compact block for the grader prompt. Empty string when absent."""
    if not isinstance(features, dict) or not features:
        return ""
    label_map = [
        ("candidate_turns", "candidate turns"),
        ("test_runs", "test/verification runs (editor + agent)"),
        ("edits_after_last_test", "edits made AFTER the last test run (shipped unverified)"),
        ("tool_errors", "agent tool errors hit"),
        ("challenge_marker_turns", "turns where the candidate pushed back on / corrected the agent"),
        ("quick_follow_up_turns", "quick short follow-ups (iterating, not re-rolling)"),
        ("median_inter_turn_seconds", "median seconds between turns"),
        ("max_idle_seconds", "longest idle gap in seconds"),
        ("single_mega_prompt", "single mega-prompt session (one turn total)"),
    ]
    lines = ["Deterministic process counts (derived from the captured session, not estimates):"]
    for key, label in label_map:
        value = features.get(key)
        if value is None:
            continue
        lines.append(f"- {label}: {value}")
    return "\n".join(lines)


__all__ = ["compute_process_features", "render_process_features"]
