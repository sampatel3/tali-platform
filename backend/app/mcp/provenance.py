"""Deterministic grounding requirements for model-facing recruiting reads.

Prompts improve tool selection, but they cannot be the final trust boundary.
This module classifies requests whose answer necessarily depends on durable
candidate-action history.  Chat transports use the classification to withhold
an unsupported terminal answer unless a successful canonical read supplied the
required capability in the same turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .catalog import (
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
    CANDIDATE_POOL_EXHAUSTIVE,
    CANDIDATE_POOL_STATE,
    CANDIDATE_QUALITATIVE_EVIDENCE,
    CANDIDATE_QUALITATIVE_EXACT_EMPTY,
)


ACTION_HISTORY_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's confirmed candidate-action "
    "history, so I won't claim a result. Please try again."
)
POOL_STATE_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's canonical candidate pool and "
    "current state, so I won't claim a result. Please try again."
)
DECISION_HISTORY_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's agent-decision history, so I "
    "won't claim a result. Please try again."
)
GROUNDING_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's canonical candidate data, so I "
    "won't claim a result. Please try again."
)
QUALITATIVE_EVIDENCE_REQUIRED_MESSAGE = (
    "I couldn't verify that skill or experience from cited candidate evidence "
    "for this role, so I won't claim a match or a zero. Please try again."
)

_COMPLETED_ACTION_RE = re.compile(
    r"\b(?:advance(?:d)?|reject(?:ed)?|hire(?:d)?|withdrew|withdrawn|"
    r"move(?:d)?|sent|resent|invite(?:d)?)\b",
    re.IGNORECASE,
)
_HISTORY_MARKER_RE = re.compile(
    r"\b(?:did|have|has|had|was|were|when|history|historical|recently|"
    r"today|yesterday|ago|earlier|previously|before|after|between|"
    r"last\s+(?:day|week|month|quarter|year)|"
    r"this\s+(?:day|week|month|quarter|year)|"
    r"past\s+(?:day|week|month|quarter|year))\b",
    re.IGNORECASE,
)
_CANDIDATE_CONTEXT_RE = re.compile(
    r"\b(?:candidate|candidates|applicant|applicants|people|person|who|whom|"
    r"anyone|anybody|someone|somebody|nobody|engineer|engineers|developer|"
    r"developers|profile|profiles|match|matches)\b",
    re.IGNORECASE,
)
_EXPLICIT_HISTORY_RE = re.compile(
    r"\b(?:candidate|application)\s+(?:action|actions|movement|movements|history)\b",
    re.IGNORECASE,
)
_ACTOR_COMPLETED_ACTION_RE = re.compile(
    r"\b(?:i|we|you|the\s+agent|agent)\s+(?:have\s+|has\s+|had\s+)?"
    r"(?:advanced|rejected|hired|withdrew|moved|sent|resent|invited)\b",
    re.IGNORECASE,
)
_DECISION_HISTORY_RE = re.compile(
    r"\b(?:agent\s+)?(?:decision|decisions|recommendation|recommendations)\b|"
    r"\b(?:decide|decided|recommended|overrode|override|overridden|approve|"
    r"approved|resolved|discarded|expired)\b",
    re.IGNORECASE,
)
_DECISION_HISTORY_CONTEXT_RE = re.compile(
    r"\b(?:did|has|have|had|was|were|show|list|audit|history|historical|"
    r"pending|processing|approved|overridden|reverted|discarded|expired|"
    r"resolved|approve|approved|override|overrode|overridden|when|yesterday|"
    r"today|last|past|this)\b",
    re.IGNORECASE,
)
_POOL_REQUEST_RE = re.compile(
    r"\b(?:list|show|find|search|compare|rank|count|how\s+many|which|who|"
    r"are\s+there|do\s+(?:we|i|you)\s+have|should\s+(?:we|i))\b",
    re.IGNORECASE,
)
_POOL_ASSERTION_RE = re.compile(
    r"(?:\b(?:zero|no|none|any|all|every|entire|whole|exact|exhaustive|"
    r"exhaustively|hard\s+zero)\b.{0,80}\b(?:candidate|candidates|applicant|"
    r"applicants|pool)\b)|(?:\b(?:candidate|candidates|applicant|applicants|"
    r"pool)\b.{0,80}\b(?:zero|none|all|every|exact|exhaustive|empty)\b)",
    re.IGNORECASE,
)
_CURRENT_STATE_RE = re.compile(
    r"\b(?:current|currently|pool|pipeline|stage|status|score|fit|experience|"
    r"skill|skills|qualified|available|strongest|best|top|advance|advanced|"
    r"reject|rejected|withdrawn|hired|assessment|interview)\b",
    re.IGNORECASE,
)
_FUTURE_CANDIDATE_ACTION_RE = re.compile(
    r"\bshould\s+(?:i|we)\s+(?:advance|reject|hire|interview|assess)\b",
    re.IGNORECASE,
)
_HISTORY_ROWS_RE = re.compile(
    r"\b(?:who|which|what|when|list|show|give|names?|details?)\b",
    re.IGNORECASE,
)
_EXHAUSTIVE_POOL_RE = re.compile(
    r"\b(?:all|every|entire|whole|exhaustive|exhaustively|complete\s+list)\b",
    re.IGNORECASE,
)
_QUALITATIVE_MARKER_RE = re.compile(
    r"\b(?:experience|experienced|skill|skills|skilled|knows?|knowledge|"
    r"expertise|expert|background|familiar(?:ity)?|proficien(?:t|cy)|"
    r"hands?[ -]on|worked\s+with|working\s+with|certif(?:ied|ication)|"
    r"speciali[sz](?:e|ed|ation)|implemented|built|delivered)\b",
    re.IGNORECASE,
)
_QUALITATIVE_EMPTY_ASSERTION_RE = re.compile(
    r"\b(?:zero|no|none|nobody|not\s+a\s+single|hard\s+zero|empty)\b",
    re.IGNORECASE,
)
_QUALITATIVE_SUBJECT_ASSERTION_RE = re.compile(
    r"\b(?:has|have|had|demonstrates?|brings?|shows?|lacks?)\b.{0,100}"
    r"\b(?:experience|experienced|skill|skills|skilled|knowledge|expertise|"
    r"background|proficien(?:t|cy)|certif(?:ied|ication))\b",
    re.IGNORECASE,
)
_CANDIDATE_MODIFIER_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9.+#/_-]{1,50})\s+"
    r"(?:candidate|candidates|applicant|applicants|people|engineer|engineers|"
    r"developer|developers|profile|profiles|match|matches)\b",
    re.IGNORECASE,
)
_CANDIDATE_WITH_RE = re.compile(
    r"\b(?:candidate|candidates|applicant|applicants|people|anyone|anybody|"
    r"someone|somebody)\b"
    r".{0,40}\b(?:with|having)\s+([A-Za-z][A-Za-z0-9.+#/_-]{1,50})",
    re.IGNORECASE,
)
_SUBJECT_WITH_QUALITY_RE = re.compile(
    r"\b(?:who|anyone|anybody|someone|somebody|nobody)\s+"
    r"(?:has|have|knows?|with|experienced\s+in)\s+"
    r"([A-Za-z][A-Za-z0-9.+#/_-]{1,50})",
    re.IGNORECASE,
)
_MATCH_FOR_QUALITY_RE = re.compile(
    r"\b(?:match|matches)\s+(?:for|with)\s+"
    r"([A-Za-z][A-Za-z0-9.+#/_-]{1,50})",
    re.IGNORECASE,
)
_CANDIDATE_STAGE_MODIFIER_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9+/_-]{0,50}(?:[- ]interview))\s+"
    r"(?:candidate|candidates|applicant|applicants|people|profiles?)\b",
    re.IGNORECASE,
)
_NON_QUALITATIVE_MODIFIERS = frozenset(
    {
        "all",
        "any",
        "available",
        "best",
        "current",
        "every",
        "hired",
        "open",
        "pending",
        "rejected",
        "strongest",
        "top",
        "withdrawn",
        "advanced",
        "sourced",
        "applied",
        "invited",
        "assessment",
        "applicant",
        "applicants",
        "candidate",
        "candidates",
        "interview",
        "score",
        "scored",
        "status",
        "stage",
        "role",
        "the",
        "these",
        "those",
        "which",
        "who",
        "zero",
        "no",
        "none",
        "our",
        "my",
        "of",
        "list",
        "find",
        "show",
    }
)

_QUALITATIVE_TERM_STOP_WORDS = _NON_QUALITATIVE_MODIFIERS.union(
    {
        "a",
        "about",
        "an",
        "and",
        "anybody",
        "anyone",
        "are",
        "backed",
        "based",
        "been",
        "bring",
        "brings",
        "built",
        "but",
        "by",
        "can",
        "check",
        "checked",
        "checking",
        "cited",
        "currently",
        "cv",
        "cvs",
        "delivered",
        "demonstrate",
        "demonstrates",
        "did",
        "do",
        "does",
        "evidence",
        "everyone",
        "exhaustive",
        "exhaustively",
        "experience",
        "experienced",
        "expert",
        "expertise",
        "familiar",
        "familiarity",
        "found",
        "for",
        "grounded",
        "give",
        "has",
        "have",
        "had",
        "hands",
        "having",
        "implemented",
        "in",
        "is",
        "knowledge",
        "knows",
        "match",
        "matches",
        "me",
        "nobody",
        "not",
        "of",
        "on",
        "or",
        "our",
        "person",
        "people",
        "pool",
        "proficiency",
        "proficient",
        "please",
        "rank",
        "ranked",
        "ranking",
        "recommend",
        "recommended",
        "role",
        "search",
        "searched",
        "showing",
        "someone",
        "somebody",
        "supported",
        "tell",
        "their",
        "there",
        "they",
        "this",
        "to",
        "unverified",
        "verified",
        "was",
        "we",
        "week",
        "with",
        "worked",
        "working",
        "years",
        "you",
        "your",
    }
)
_QUALITATIVE_SUBJECT_RE = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})\s+"
    r"(?:has|have|had|demonstrates?|brings?|shows?|lacks?|knows?)\b",
)
_FACT_SUBJECT_RE = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})\s+"
    r"(?:is|are|was|were|has|have|had)\b"
)
_HISTORY_ACTION_SUBJECT_RE = re.compile(
    r"\b(?i:advance(?:d)?|reject(?:ed)?|hire(?:d)?|move(?:d)?|invite(?:d)?|"
    r"send|sent|resend|resent|withdrew|withdraw)\s+"
    r"(?:candidate\s+|applicant\s+)?"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})"
    r"(?=\s+(?i:to|into|from|for|after|before|during|last|past|this|today|"
    r"yesterday|an|a|the)\b|[?.!,;]|$)"
)
_HISTORY_DECISION_SUBJECT_RE = re.compile(
    r"\b(?i:decide(?:d)?|decision|recommend(?:ed)?|recommendation)\b"
    r".{0,40}?\b(?i:for|about|on)\s+(?:candidate\s+|applicant\s+)?"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})"
    r"(?=[?.!,;]|$)"
)
_BULLET_SUBJECT_RE = re.compile(
    r"(?m)^\s*(?:[-*]|\d+[.)])\s+"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})"
    r"(?=\s*(?:[-—:,(]|$))"
)
_NON_PERSON_SUBJECT_WORDS = frozenset(
    {
        "agent",
        "application",
        "candidate",
        "candidates",
        "current",
        "evidence",
        "final interview",
        "here",
        "none",
        "one",
        "recommendation",
        "technical interview",
        "the agent",
        "there",
        "this role",
        "total",
        "zero",
    }
)
_ZERO_COUNT_ASSERTION_RE = re.compile(
    r"\b(?:zero|no)\s+(?:verified\s+)?(?:candidate|candidates|applicant|"
    r"applicants|people|"
    r"matches|actions|decisions|recommendations)\b|"
    r"\bnone\s+of\s+(?:the\s+)?(?:candidate|candidates|applicant|applicants)\b",
    re.IGNORECASE,
)
_NUMERIC_COUNT_ASSERTION_RES = (
    re.compile(
        r"\bthere\s+(?:are|were|is|was)\s+(\d+)\s+"
        r"(?:candidate|candidates|applicant|applicants|people|matches|actions|"
        r"decisions|recommendations)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d+)\s+(?:candidate|candidates|applicant|applicants|people|"
        r"matches|actions|decisions|recommendations)\s+"
        r"(?:are|were|was|is|have|has|had|matched|qualified|advanced|rejected|"
        r"recommended|found)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:exact\s+)?(?:total|count)\s*(?:is|=|:)\s*(\d+)\b", re.I),
)
_ACTION_TARGET_STAGE_RE = re.compile(
    r"\b(?:to|into)\s+([A-Za-z][A-Za-z0-9 /&+_-]{1,80}?)"
    r"(?=\s+(?:in|during|over|within|last|past|this|today|yesterday|since|"
    r"before|after|between)\b|[?.!,;]|$)",
    re.IGNORECASE,
)
_ATS_STAGE_RE = re.compile(
    r"\b(?:currently\s+)?in\s+([A-Za-z][A-Za-z0-9 /&+_-]{1,80}?)"
    r"(?=\s+(?:right\s+now|currently|today|with|and)\b|[?.!,;]|$)",
    re.IGNORECASE,
)
_PIPELINE_STAGE_TERMS = (
    "sourced",
    "applied",
    "invited",
    "in_assessment",
    "review",
    "advanced",
)


@dataclass(frozen=True)
class GroundingClaim:
    """One normalized factual claim that needs a same-turn certificate."""

    capability: str
    filters: tuple[tuple[str, str], ...] = ()
    terms: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    expected_total: int | None = None
    subject_resolution_required: bool = False

    @property
    def filter_map(self) -> dict[str, str]:
        return dict(self.filters)


def normalize_claim_value(value: object) -> str:
    """Normalize an enum/free-text filter without erasing semantic words."""

    return " ".join(
        re.findall(r"[a-z0-9][a-z0-9.+#/_-]*", str(value or "").lower())
    ).replace("_", " ")


def meaningful_qualitative_terms(value: object) -> tuple[str, ...]:
    """Stable content terms used to bind evidence to the requested quality."""

    text = str(value or "")
    subject_tokens = {
        token.lower()
        for subject in _QUALITATIVE_SUBJECT_RE.findall(text)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#/_-]*", subject)
    }
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#/_-]*", text):
        normalized = token.lower().strip("./_-")
        if (
            len(normalized) > 1
            and normalized not in _QUALITATIVE_TERM_STOP_WORDS
            and normalized not in subject_tokens
        ):
            terms.add(normalized)
    return tuple(sorted(terms))


def _qualitative_subjects(text: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                normalize_claim_value(subject)
                for subject in _QUALITATIVE_SUBJECT_RE.findall(text)
                if normalize_claim_value(subject)
            }
        )
    )


def _factual_subjects(text: str) -> tuple[str, ...]:
    plain_text = re.sub(r"[*_`]", "", text)
    raw_subjects = [
        *_FACT_SUBJECT_RE.findall(plain_text),
        *_HISTORY_ACTION_SUBJECT_RE.findall(plain_text),
        *_HISTORY_DECISION_SUBJECT_RE.findall(plain_text),
        *_BULLET_SUBJECT_RE.findall(plain_text),
    ]
    return tuple(
        sorted(
            {
                normalized
                for subject in raw_subjects
                if (normalized := normalize_claim_value(subject))
                and normalized not in _NON_PERSON_SUBJECT_WORDS
                and not any(
                    token in normalized.split()
                    for token in ("candidate", "applicant", "recommendation")
                )
            }
        )
    )


def _asserted_total(text: str) -> int | None:
    if _ZERO_COUNT_ASSERTION_RE.search(text):
        return 0
    for pattern in _NUMERIC_COUNT_ASSERTION_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _time_filters(text: str, *, now: datetime) -> dict[str, str]:
    moment = now.astimezone(timezone.utc)
    lowered = text.lower()
    start: datetime | None = None
    end: datetime | None = None
    if re.search(r"\b(?:last|past)\s+(?:7\s+days|week)\b", lowered):
        start, end = moment - timedelta(days=7), moment
    elif "yesterday" in lowered:
        today = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = today - timedelta(days=1), today
    elif "today" in lowered:
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        end = moment
    elif "this week" in lowered:
        start = (moment - timedelta(days=moment.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = moment
    if start is None:
        return {}
    return {
        "time_after": start.isoformat(),
        "time_before": (end or moment).isoformat(),
    }


def _action_filter_claim(text: str, *, now: datetime) -> dict[str, str]:
    lowered = text.lower()
    filters: dict[str, str] = {}
    if re.search(r"\b(?:sent|send|invited?)\b.{0,35}\bassessment\b", lowered):
        filters["action"] = "assessment_sent"
    elif re.search(r"\bresent\b.{0,35}\bassessment\b", lowered):
        filters["action"] = "assessment_resent"
    elif re.search(r"\breject(?:ed)?\b", lowered):
        filters["action"] = "rejected"
    elif re.search(r"\bhir(?:e|ed)\b", lowered):
        filters["action"] = "hired"
    elif re.search(r"\b(?:withdrew|withdrawn)\b", lowered):
        filters["action"] = "withdrawn"
    elif re.search(r"\b(?:advance(?:d)?|move(?:d)?|invited?)\b", lowered):
        filters["action"] = "advanced"
    target = _ACTION_TARGET_STAGE_RE.search(text)
    if target:
        filters["target_stage"] = normalize_claim_value(target.group(1))
    if re.search(
        r"\b(?:did\s+)?(?:i|we)\s+(?:have\s+|has\s+|had\s+)?"
        r"(?:advance(?:d)?|reject(?:ed)?|hire(?:d)?|withdrew|withdraw|"
        r"move(?:d)?|send|sent|resend|resent|invite(?:d)?)\b",
        lowered,
    ):
        filters["actor_type"] = "recruiter"
        # The runtime replaces this identity marker with the authenticated
        # user's id before planning or certificate comparison.
        filters["actor_id"] = "current_user"
    elif re.search(
        r"\b(?:did\s+)?(?:the\s+agent|agent|you)\s+"
        r"(?:have\s+|has\s+|had\s+)?"
        r"(?:advance(?:d)?|reject(?:ed)?|hire(?:d)?|withdrew|withdraw|"
        r"move(?:d)?|send|sent|resend|resent|invite(?:d)?)\b",
        lowered,
    ):
        filters["actor_type"] = "agent"
    elif re.search(
        r"\b(?:did\s+)?recruiters?\s+(?:have\s+|has\s+|had\s+)?"
        r"(?:advance(?:d)?|reject(?:ed)?|hire(?:d)?|withdrew|withdraw|"
        r"move(?:d)?|send|sent|resend|resent|invite(?:d)?)\b",
        lowered,
    ):
        filters["actor_type"] = "recruiter"
    filters.update(_time_filters(text, now=now))
    return filters


def _decision_filter_claim(text: str, *, now: datetime) -> dict[str, str]:
    lowered = text.lower()
    filters: dict[str, str] = {}
    status_patterns = (
        ("pending", r"\bpending\b"),
        ("processing", r"\bprocessing\b"),
        ("approved", r"\bapprov(?:e|ed)\b"),
        ("overridden", r"\b(?:override|overrode|overridden)\b"),
        ("reverted_for_feedback", r"\breverted(?:\s+for\s+feedback)?\b"),
        ("discarded", r"\bdiscarded\b"),
        ("expired", r"\bexpired\b"),
    )
    for status, pattern in status_patterns:
        if re.search(pattern, lowered):
            filters["status"] = status
            break
    if re.search(r"\breject(?:ed|ion)?\b", lowered):
        filters["decision_type"] = "reject"
    elif re.search(r"\b(?:advance|advanced|interview)\b", lowered):
        filters["decision_type"] = "advance_to_interview"
    elif re.search(r"\b(?:send|sent)\b.{0,30}\bassessment\b", lowered):
        filters["decision_type"] = "send_assessment"
    time_filters = _time_filters(text, now=now)
    if time_filters:
        filters["time_axis"] = (
            "resolved"
            if filters.get("status")
            in {
                "approved",
                "overridden",
                "reverted_for_feedback",
                "discarded",
                "expired",
            }
            or re.search(r"\b(?:resolved|approv|override|overrode)\w*\b", lowered)
            else "created"
        )
        filters.update(time_filters)
    return filters


def _pool_filter_claim(text: str) -> dict[str, str]:
    lowered = text.lower()
    filters: dict[str, str] = {}
    if re.search(
        r"\bpending\s+(?:candidate|candidates|applicant|applicants)\b", lowered
    ):
        filters["has_pending_decision"] = "true"
    for outcome in ("rejected", "withdrawn", "hired"):
        if re.search(rf"\b{outcome}\b", lowered):
            filters["application_outcome"] = outcome
            break
    for stage in _PIPELINE_STAGE_TERMS:
        phrase = stage.replace("_", " ")
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            filters["pipeline_stage"] = stage.replace("_", " ")
            break
    candidate_stage = _CANDIDATE_STAGE_MODIFIER_RE.search(text)
    if candidate_stage:
        filters["ats_stage"] = normalize_claim_value(
            candidate_stage.group(1).replace("-", " ").replace("_", " ")
        )
    ats_stage = _ATS_STAGE_RE.search(text)
    if ats_stage and "ats_stage" not in filters:
        normalized = normalize_claim_value(ats_stage.group(1))
        if normalized and normalized not in {
            stage.replace("_", " ") for stage in _PIPELINE_STAGE_TERMS
        }:
            filters["ats_stage"] = normalized
    return filters


def grounding_claims_for_message(
    message: str | None,
    *,
    now: datetime | None = None,
    include_values: bool = True,
    include_subjects: bool | None = None,
) -> tuple[GroundingClaim, ...]:
    """Return claim-specific requirements, not replayable capability booleans."""

    text = str(message or "").strip()
    if not text:
        return ()
    moment = now or datetime.now(timezone.utc)
    subjects_enabled = include_values if include_subjects is None else include_subjects
    claims: list[GroundingClaim] = []
    for capability in sorted(required_capabilities_for_message(text)):
        filters: dict[str, str] = {}
        terms: tuple[str, ...] = ()
        subjects: tuple[str, ...] = _factual_subjects(text) if subjects_enabled else ()
        if capability in {
            CANDIDATE_ACTION_HISTORY,
            CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
        }:
            filters = _action_filter_claim(text, now=moment)
        elif capability in {
            CANDIDATE_DECISION_HISTORY,
            CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
        }:
            filters = _decision_filter_claim(text, now=moment)
        elif capability in {CANDIDATE_POOL_STATE, CANDIDATE_POOL_EXHAUSTIVE}:
            filters = _pool_filter_claim(text)
        elif capability in {
            CANDIDATE_QUALITATIVE_EVIDENCE,
            CANDIDATE_QUALITATIVE_EXACT_EMPTY,
        }:
            terms = meaningful_qualitative_terms(text)
            if subjects_enabled:
                subjects = tuple(
                    sorted(set(subjects).union(_qualitative_subjects(text)))
                )
        claims.append(
            GroundingClaim(
                capability=capability,
                filters=tuple(sorted(filters.items())),
                terms=terms,
                subjects=subjects,
                expected_total=_asserted_total(text) if include_values else None,
            )
        )
    return tuple(claims)


def _is_qualitative_candidate_message(text: str) -> bool:
    """Detect candidate qualities that need CV/source evidence, not identity SQL."""

    if _QUALITATIVE_MARKER_RE.search(text):
        return True
    for pattern in (
        _CANDIDATE_MODIFIER_RE,
        _CANDIDATE_WITH_RE,
        _SUBJECT_WITH_QUALITY_RE,
        _MATCH_FOR_QUALITY_RE,
    ):
        for match in pattern.finditer(text):
            modifier = match.group(1).lower()
            normalized_modifier = modifier.replace("_", " ").replace("-", " ")
            # Provider stage slugs such as ``final-interview`` are current
            # workflow state, not evidence about interview expertise. Treat
            # the slug the same as the human-written ``final interview`` form.
            if (
                modifier not in _NON_QUALITATIVE_MODIFIERS
                and normalized_modifier not in _NON_QUALITATIVE_MODIFIERS
                and normalized_modifier.split()[-1] not in _NON_QUALITATIVE_MODIFIERS
            ):
                return True
    return False


def _requires_decision_history(text: str) -> bool:
    """Distinguish completed recommendation audit from future advice."""

    if not _DECISION_HISTORY_RE.search(text):
        # Present-tense ``recommend`` is history only with an explicit past
        # auxiliary ("who did the agent recommend?").
        return bool(
            re.search(r"\b(?:did|has|have|had)\b.{0,50}\brecommend\b", text, re.I)
        )
    return bool(_DECISION_HISTORY_CONTEXT_RE.search(text))


def required_capabilities_for_message(message: str | None) -> frozenset[str]:
    """Return canonical reads a terminal answer must have used this turn."""

    text = str(message or "").strip()
    if not text:
        return frozenset()
    qualitative_message = _is_qualitative_candidate_message(text)
    candidate_context = bool(
        _CANDIDATE_CONTEXT_RE.search(text)
        or _factual_subjects(text)
        or _FUTURE_CANDIDATE_ACTION_RE.search(text)
        or (qualitative_message and _QUALITATIVE_SUBJECT_ASSERTION_RE.search(text))
    )
    requires_action_history = bool(_EXPLICIT_HISTORY_RE.search(text)) or bool(
        candidate_context
        and (
            _ACTOR_COMPLETED_ACTION_RE.search(text)
            or (_COMPLETED_ACTION_RE.search(text) and _HISTORY_MARKER_RE.search(text))
        )
    )
    required: set[str] = set()
    if requires_action_history:
        required.add(
            CANDIDATE_ACTION_HISTORY_EXHAUSTIVE
            if _HISTORY_ROWS_RE.search(text)
            else CANDIDATE_ACTION_HISTORY
        )

    requires_decision_history = _requires_decision_history(text)
    if requires_decision_history:
        required.add(
            CANDIDATE_DECISION_HISTORY_EXHAUSTIVE
            if _HISTORY_ROWS_RE.search(text)
            else CANDIDATE_DECISION_HISTORY
        )

    requires_qualitative_evidence = bool(candidate_context and qualitative_message)
    if requires_qualitative_evidence:
        if _QUALITATIVE_EMPTY_ASSERTION_RE.search(text):
            required.add(CANDIDATE_QUALITATIVE_EXACT_EMPTY)
        else:
            required.add(CANDIDATE_QUALITATIVE_EVIDENCE)

    # Current pool/state questions and exact/empty assertions require a
    # canonical role-scoped read. Historical action/decision tools already
    # return candidate identity and their own exact totals, so do not require a
    # redundant pool read unless the message independently asks about current
    # state too.
    asks_for_pool = bool(
        candidate_context
        and (
            _POOL_ASSERTION_RE.search(text)
            or (_FACT_SUBJECT_RE.search(text) and _CURRENT_STATE_RE.search(text))
            or (_asserted_total(text) is not None and _CURRENT_STATE_RE.search(text))
            or (_POOL_REQUEST_RE.search(text) and _CURRENT_STATE_RE.search(text))
        )
    )
    if asks_for_pool and not (
        requires_action_history
        or requires_decision_history
        or requires_qualitative_evidence
    ):
        required.add(
            CANDIDATE_POOL_EXHAUSTIVE
            if _EXHAUSTIVE_POOL_RE.search(text)
            else CANDIDATE_POOL_STATE
        )
    return frozenset(required)


def grounding_required_message(missing: Iterable[str]) -> str:
    """Return a capability-specific fail-closed response."""

    capabilities = frozenset(missing)
    if capabilities.intersection(
        {CANDIDATE_ACTION_HISTORY, CANDIDATE_ACTION_HISTORY_EXHAUSTIVE}
    ):
        return ACTION_HISTORY_REQUIRED_MESSAGE
    if capabilities.intersection(
        {CANDIDATE_DECISION_HISTORY, CANDIDATE_DECISION_HISTORY_EXHAUSTIVE}
    ):
        return DECISION_HISTORY_REQUIRED_MESSAGE
    if capabilities.intersection({CANDIDATE_POOL_STATE, CANDIDATE_POOL_EXHAUSTIVE}):
        return POOL_STATE_REQUIRED_MESSAGE
    if capabilities.intersection(
        {CANDIDATE_QUALITATIVE_EVIDENCE, CANDIDATE_QUALITATIVE_EXACT_EMPTY}
    ):
        return QUALITATIVE_EVIDENCE_REQUIRED_MESSAGE
    return GROUNDING_REQUIRED_MESSAGE


def latest_user_text(messages: Iterable[dict[str, Any]]) -> str:
    """Extract the newest ordinary user text from an Anthropic transcript."""

    for message in reversed(list(messages)):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if content.strip():
                return content.strip()
            continue
        if not isinstance(content, list):
            continue
        chunks = [
            str(block.get("text") or "").strip()
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(chunk for chunk in chunks if chunk).strip()
        if text:
            return text
    return ""


def missing_required_capabilities(
    required: Iterable[str],
    grounded: Iterable[str],
) -> frozenset[str]:
    return frozenset(required).difference(grounded)


__all__ = [
    "ACTION_HISTORY_REQUIRED_MESSAGE",
    "DECISION_HISTORY_REQUIRED_MESSAGE",
    "GROUNDING_REQUIRED_MESSAGE",
    "POOL_STATE_REQUIRED_MESSAGE",
    "QUALITATIVE_EVIDENCE_REQUIRED_MESSAGE",
    "GroundingClaim",
    "grounding_claims_for_message",
    "grounding_required_message",
    "latest_user_text",
    "meaningful_qualitative_terms",
    "missing_required_capabilities",
    "normalize_claim_value",
    "required_capabilities_for_message",
]
