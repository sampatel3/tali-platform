"""Deterministic lower-bound checks for source-backed semantic evidence."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+", re.IGNORECASE)
_EXPERIENCE_DEMAND_RE = re.compile(
    r"\b(?:experience|background|expertise|hands[\s-]*on)\b",
    re.IGNORECASE,
)
_APPLIED_ACTION_RE = re.compile(
    r"\b(?:administered|architected|built|created|delivered|deployed|designed|"
    r"developed|engineered|implemented|integrated|led|maintained|managed|"
    r"operated|used|using|worked\s+(?:on|with))\b",
    re.IGNORECASE,
)
_DIRECT_EXPERIENCE_RE = re.compile(
    r"\b(?:experience|expertise|background)\b|\bhands[\s-]*on\b|"
    r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\+?"
    r"\s+years?\b",
    re.IGNORECASE,
)
_NON_APPLIED_RE = re.compile(
    r"\b(?:familiar\s+with|interested\s+in|learning|seeking\s+exposure\s+to)\b",
    re.IGNORECASE,
)
_NEGATED_CLAIM_RE = re.compile(
    r"\b(?:did|does|do|has|have|had|was|were|is|are|can|could)\s+not\b|"
    r"\b(?:didn't|doesn't|don't|hasn't|haven't|hadn't|wasn't|weren't|"
    r"isn't|aren't|can't|couldn't)\b|\bnever\b|"
    r"\b(?:no|without)\s+(?:direct\s+)?"
    r"(?:experience|expertise|background|hands[\s-]*on)\b",
    re.IGNORECASE,
)
_NO_ENTITY_EXPERIENCE_RE = re.compile(
    r"\b(?:no|without)\s+"
    r"(?P<subject>(?:(?:direct|prior|professional|practical|commercial|formal|"
    r"relevant|production|hands[\s-]*on)\s+){0,3}"
    r"(?:[a-z0-9+#.()&/-]+\s+){1,6})"
    r"(?:experience|expertise|background)\b",
    re.IGNORECASE,
)
_NO_EXPERIENCE_WITH_RE = re.compile(
    r"\b(?:no|without)\s+(?:direct\s+)?"
    r"(?:experience|expertise|background|hands[\s-]*on)\s+"
    r"(?:with|in|of|using|on)\s+"
    r"(?P<subject>(?:[a-z0-9+#.()&/-]+\s*){1,6})",
    re.IGNORECASE,
)
_NEGATED_ACTION_RE = re.compile(
    r"\b(?:never|not)\s+(?:directly\s+|personally\s+|ever\s+){0,2}"
    r"(?:administered|architected|built|created|delivered|deployed|designed|"
    r"developed|engineered|implemented|integrated|maintained|managed|operated|"
    r"used|worked|employed|studied|attended|joined|graduated)\b|"
    r"\b(?:did|does|do|has|have|had|was|were|is|are|can|could)\s+not\s+"
    r"(?:ever\s+)?(?:administer|architect|build|create|deliver|deploy|design|"
    r"develop|engineer|implement|integrate|maintain|manage|operate|use|work|"
    r"study|attend|join|graduate)\b|"
    r"\b(?:didn't|doesn't|don't|hasn't|haven't|hadn't|wasn't|weren't|"
    r"isn't|aren't|can't|couldn't)\s+(?:ever\s+)?"
    r"(?:administer|architect|build|create|deliver|deploy|design|develop|"
    r"engineer|implement|integrate|maintain|manage|operate|use|work|study|"
    r"attend|join|graduate)\b",
    re.IGNORECASE,
)
_INDIRECT_SUBJECT_RE = re.compile(
    r"\b(?:(?:[a-z0-9._-]+'s|the|their|his|her|our|my)\s+)?"
    r"(?:team|department|company|organization|colleagues?)\s+"
    r"(?:(?:has|have|had)\s+|that\s+)?"
    r"(?:administered|architected|built|created|delivered|deployed|designed|"
    r"developed|engineered|implemented|integrated|led|maintained|managed|"
    r"operated|used|uses|using|worked\s+(?:on|with))\b",
    re.IGNORECASE,
)
_SCAFFOLDING = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "background",
        "candidate",
        "candidates",
        "domain",
        "experience",
        "expertise",
        "hands",
        "has",
        "have",
        "having",
        "in",
        "of",
        "on",
        "or",
        "people",
        "person",
        "skill",
        "skills",
        "the",
        "with",
    }
)


def _phrase_present(content: str, phrase: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
            content,
            re.IGNORECASE,
        )
    )


def _normalized_token(token: str) -> str:
    # Keep meaningful internal/leading dots (Node.js, .NET) while excluding a
    # sentence-ending full stop captured by the permissive technology tokenizer.
    return token.casefold().rstrip(".")


def _meaningful_tokens(value: str) -> set[str]:
    return {
        _normalized_token(token)
        for token in _TOKEN_RE.findall(value)
        if _normalized_token(token) not in _SCAFFOLDING
    }


def _segments(content: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r"[.;\n]|,\s*|\bbut\b", content)
        if segment.strip()
    ]


def _segment_tokens(segment: str) -> set[str]:
    return {_normalized_token(token) for token in _TOKEN_RE.findall(segment)}


def _contains_required(segment: str, value: str, required: set[str]) -> bool:
    return _phrase_present(segment, value) or bool(required) and required <= _segment_tokens(
        segment
    )


def _subject_mentions_required(subject: str, required: set[str]) -> bool:
    subject_tokens = _meaningful_tokens(subject)
    return bool(required & subject_tokens)


def _is_explicitly_negated(segment: str, value: str, required: set[str]) -> bool:
    """Return true only when the segment negates this value's claim."""

    if not _contains_required(segment, value, required):
        return False
    for pattern in (_NO_ENTITY_EXPERIENCE_RE, _NO_EXPERIENCE_WITH_RE):
        if any(
            _subject_mentions_required(match.group("subject"), required)
            for match in pattern.finditer(segment)
        ):
            return True
    return bool(
        _NEGATED_CLAIM_RE.search(segment) or _NEGATED_ACTION_RE.search(segment)
    )


def _has_applied_experience(content: str, required: set[str]) -> bool:
    """Require an applied claim, not interest or familiarity, near the skill."""

    if not required:
        return False
    for segment in _segments(content):
        available = _segment_tokens(segment)
        if not required <= available:
            continue
        # A lexical action is not direct applied experience when the source
        # explicitly negates it or attributes it to somebody else's team.
        if _is_explicitly_negated(segment, " ".join(sorted(required)), required):
            continue
        if _INDIRECT_SUBJECT_RE.search(segment):
            continue
        has_action = bool(_APPLIED_ACTION_RE.search(segment))
        if has_action:
            return True
        if _NON_APPLIED_RE.search(segment):
            continue
        if _DIRECT_EXPERIENCE_RE.search(segment):
            return True
    return False


def _relationship_present(content: str, value: str, predicate: str) -> bool:
    """Require an affirmative relationship phrase bound to the entity value."""

    entity = rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])"
    if predicate == "worked_at":
        patterns = (
            rf"\b(?:worked|works|working)\s+(?:at|for)\s+{entity}",
            rf"\bemployed\s+(?:at|by)\s+{entity}",
            rf"\b(?:employee|contractor)\s+(?:at|of|for)\s+{entity}",
            rf"\b(?:employment|role|position)\s+(?:at|with)\s+{entity}",
            rf"\bjoined\s+{entity}",
            rf"\b(?:administrator|analyst|architect|consultant|designer|developer|"
            rf"director|engineer|founder|head|lead|manager|officer|president|"
            rf"recruiter|scientist|specialist)\b(?:\s+[a-z0-9&/+.-]+){{0,3}}"
            rf"\s+at\s+{entity}",
        )
    else:
        patterns = (
            rf"\b(?:studied|educated|enrolled)\s+at\s+{entity}",
            rf"\battended\s+{entity}",
            rf"\bgraduated\s+from\s+{entity}",
            rf"\bdegree\s+from\s+{entity}",
            rf"\b(?:alumnus|alumna|graduate)\s+of\s+{entity}",
            rf"\beducation\s+at\s+{entity}",
        )
    required = _meaningful_tokens(value)
    for segment in _segments(content):
        if not _contains_required(segment, value, required):
            continue
        if _is_explicitly_negated(segment, value, required):
            continue
        if any(re.search(pattern, segment, re.IGNORECASE) for pattern in patterns):
            return True
    return False


def contains_grounding_value(
    content: str,
    value: str,
    *,
    predicate: str | None = None,
) -> bool:
    """Require every meaningful criterion token in the original source.

    Exact phrases pass immediately.  Otherwise common query scaffolding is
    ignored, so a hands-on product-experience criterion can be supported by a
    CV sentence that directly names the product without repeating the
    recruiter’s phrasing.  This is intentionally a conservative lower bound,
    not semantic inference: unsupported synonyms require the separate
    verification layer.
    """

    normalized_content = " ".join(content.casefold().split())
    normalized_predicate = str(predicate or "").strip().casefold()
    # A source-text mention cannot prove an exact path or shared-employer
    # relationship. Those predicates stay fail-closed until a parameterized
    # graph traversal adapter can return path evidence.
    if normalized_predicate in {"colleague_of", "n_hop_from"}:
        return False
    normalized_value = " ".join(value.casefold().split())
    if not normalized_value:
        return False
    required = _meaningful_tokens(normalized_value)
    if normalized_predicate in {"worked_at", "studied_at"}:
        return _relationship_present(
            normalized_content,
            normalized_value,
            normalized_predicate,
        )
    if _EXPERIENCE_DEMAND_RE.search(normalized_value) and not _has_applied_experience(
        normalized_content,
        required,
    ):
        return False
    return any(
        _contains_required(segment, normalized_value, required)
        and not _is_explicitly_negated(segment, normalized_value, required)
        for segment in _segments(normalized_content)
    )


__all__ = ["contains_grounding_value"]
