"""Grounded per-criterion CV evidence via Anthropic Citations.

Given a candidate's CV text and a list of recruiter criteria, ask Claude —
with the CV supplied as a *citations-enabled document* — to judge each
criterion and quote the supporting CV text. The Citations API guarantees
``cited_text`` is verbatim from the document (it is parsed out of the
source, not generated), so a "met" verdict that carries a quote is
grounded by construction. A verdict with no quote is treated as
*ungrounded* and never counts toward a candidate qualifying — absence of
a citation is absence of evidence.

Citations is incompatible with Structured Outputs (the two together 400),
so the model emits a small marker-tagged text format::

    [[C1]] MET — Senior Data Engineer at JPMorgan Chase (2019–2023) ...
    [[C2]] MISSING — no evidence of Kafka in the CV.

which we parse, pairing each criterion with the citation blocks that
follow its marker in the interleaved response.

One Anthropic call per candidate (covers all criteria). Defaults to the
codebase ``FAST_MODEL`` (Haiku 4.5 — cheap, and supports citations).
Callers bound the candidate set to the ranked shortlist, so worst-case
cost is ``len(shortlist)`` single Haiku calls.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from . import MODEL_VERSION as _FAST_MODEL

logger = logging.getLogger("taali.candidate_search.grounded")

# Citations works on all active models except Haiku 3. FAST_MODEL (Haiku
# 4.5) qualifies and keeps the per-candidate cost to fractions of a cent.
GROUNDING_MODEL = os.getenv("CLAUDE_GROUNDING_MODEL") or _FAST_MODEL
GROUNDING_MAX_TOKENS = 700
GROUNDING_TEMPERATURE = 0.0
# Cap CV text sent to bound cost; most CVs sit well under this. Citation
# char offsets are relative to this (possibly truncated) string.
CV_TEXT_CHAR_CAP = 16000

_MARKER_RE = re.compile(r"\[\[\s*C(\d+)\s*\]\]", re.IGNORECASE)
_VERDICT_RE = re.compile(
    r"\[\[\s*C(\d+)\s*\]\]\s*[\-—:.\s]*"
    r"(MET|PARTIAL(?:LY)?(?:[ _]MET)?|MISSING)",
    re.IGNORECASE,
)


@dataclass
class Evidence:
    """One verbatim CV quote backing a verdict, with its char span."""

    quote: str
    start_char: int = -1
    end_char: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote": self.quote,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }


@dataclass
class CriterionVerdict:
    """Per-criterion judgement. ``grounded`` is true only when at least one
    verbatim quote backs the verdict — that is the anti-hallucination gate."""

    criterion: str
    status: str = "missing"  # met | partially_met | missing
    grounded: bool = False
    source: str = "none"  # cv_citation | role_requirement | none
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "status": self.status,
            "grounded": self.grounded,
            "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
            "note": self.note,
        }


_SYSTEM_PROMPT = (
    "You verify whether a candidate's CV contains evidence for specific "
    "recruiter criteria. You are given the CV as a document (citations "
    "enabled) and a numbered list of criteria.\n\n"
    "For EACH criterion, output exactly one line in this format:\n"
    "[[C<n>]] <MET|PARTIAL|MISSING> — <one short sentence>\n\n"
    "Rules:\n"
    "- MET: the CV clearly satisfies the criterion. PARTIAL: related but "
    "incomplete evidence. MISSING: no supporting evidence.\n"
    "- For MET or PARTIAL, restate the SINGLE most specific CV line or phrase "
    "(employer, title, project, dates, or the exact phrase) that proves it, so "
    "it is cited. Keep it tight — cite the one decisive line, NOT contact "
    "details, full skills lists, or surrounding boilerplate.\n"
    "- For MISSING, say no evidence was found and cite nothing.\n"
    "- NEVER claim evidence that is not in the document. Absence of evidence "
    "is MISSING — never inferred from adjacent or similar facts.\n"
    "- Output only the [[C<n>]] lines, one per criterion, in order. No "
    "preamble, no summary."
)


def _criteria_block(criteria: list[str]) -> str:
    lines = "\n".join(f"[[C{i + 1}]] {c}" for i, c in enumerate(criteria))
    return (
        "Assess the candidate against each criterion below. Quote the CV "
        "for every MET or PARTIAL.\n\n" + lines
    )


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off an SDK block object or a plain dict (for tests)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def parse_citation_response(
    content_blocks: list[Any], criteria: list[str]
) -> list[CriterionVerdict]:
    """Parse the interleaved text+citation response into per-criterion verdicts.

    Pure function (no I/O) so it is unit-testable with synthetic blocks.
    Walks blocks in order, tracking the most recent ``[[C<n>]]`` marker as
    the "current" criterion, and attaches each block's citations to it.
    Verdict words are read from the concatenated text.
    """
    n = len(criteria)
    verdicts = [CriterionVerdict(criterion=c) for c in criteria]

    full_text_parts: list[str] = []
    current_idx: int | None = None

    for block in content_blocks or []:
        if _attr(block, "type") != "text":
            continue
        text = _attr(block, "text", "") or ""
        full_text_parts.append(text)

        markers = list(_MARKER_RE.finditer(text))
        if markers:
            k = int(markers[-1].group(1))
            if 1 <= k <= n:
                current_idx = k - 1

        citations = _attr(block, "citations", None) or []
        if citations and current_idx is not None:
            for c in citations:
                quote = (_attr(c, "cited_text", "") or "").strip()
                if not quote:
                    continue
                try:
                    start = int(_attr(c, "start_char_index", -1))
                except (TypeError, ValueError):
                    start = -1
                try:
                    end = int(_attr(c, "end_char_index", -1))
                except (TypeError, ValueError):
                    end = -1
                verdicts[current_idx].evidence.append(
                    Evidence(quote=quote, start_char=start, end_char=end)
                )

    full_text = "".join(full_text_parts)
    for m in _VERDICT_RE.finditer(full_text):
        k = int(m.group(1))
        if not (1 <= k <= n):
            continue
        raw = m.group(2).upper().replace(" ", "_")
        if raw == "MET":
            status = "met"
        elif raw == "MISSING":
            status = "missing"
        else:  # PARTIAL / PARTIALLY / PARTIALLY_MET
            status = "partially_met"
        verdicts[k - 1].status = status
        line_start = m.end()
        nl = full_text.find("\n", line_start)
        note = full_text[line_start : (nl if nl != -1 else len(full_text))]
        verdicts[k - 1].note = note.strip(" —-:.\t").strip()

    # Grounding enforcement: a verdict counts as grounded ONLY if a verbatim
    # quote was cited. A MET/PARTIAL with no citation keeps its status word
    # but is flagged ungrounded so the UI shows it as unverified and the
    # qualifying gate ignores it.
    for v in verdicts:
        v.grounded = len(v.evidence) > 0
        v.source = "cv_citation" if v.grounded else "none"

    return verdicts


# Citation granularity. Plain-text documents are auto-chunked "by sentence",
# which yields a single giant block for separator-laden CV headers (no real
# sentence boundaries) — so the model can only cite the whole blob. We instead
# send the CV as a CUSTOM-CONTENT document of small pre-split blocks, so a
# citation lands on one tight, relevant line.
CV_CHUNK_MAX_LEN = 220
MAX_CV_CHUNKS = 400
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SEPARATOR_SPLIT_RE = re.compile(r"\s*[·•|;]\s*")


def _chunk_cv(text: str) -> list[str]:
    """Split a CV into small citable blocks: by line, then sentence, then
    separators (· | ;), with a hard length cap as the last resort."""
    chunks: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= CV_CHUNK_MAX_LEN:
                chunks.append(sentence)
            else:
                for seg in _SEPARATOR_SPLIT_RE.split(sentence):
                    seg = seg.strip()
                    while len(seg) > CV_CHUNK_MAX_LEN:
                        chunks.append(seg[:CV_CHUNK_MAX_LEN].strip())
                        seg = seg[CV_CHUNK_MAX_LEN:].strip()
                    if seg:
                        chunks.append(seg)
            if len(chunks) >= MAX_CV_CHUNKS:
                return chunks[:MAX_CV_CHUNKS]
    return chunks[:MAX_CV_CHUNKS]


def extract_cv_evidence(
    *,
    cv_text: str | None,
    criteria: list[str],
    client,
    organization_id: int,
    application_id: int,
) -> list[CriterionVerdict]:
    """Run one citations call over the CV and return per-criterion verdicts.

    Never raises: on any failure every criterion degrades to ``missing``
    with an explanatory note, so the caller can still render the candidate.
    """
    criteria = [c.strip() for c in (criteria or []) if c and c.strip()]
    if not criteria:
        return []

    cv = (cv_text or "").strip()
    if not cv:
        return [
            CriterionVerdict(criterion=c, status="missing", note="CV text unavailable.")
            for c in criteria
        ]
    cv = cv[:CV_TEXT_CHAR_CAP]
    chunks = _chunk_cv(cv)
    if not chunks:
        return [
            CriterionVerdict(criterion=c, status="missing", note="CV text unavailable.")
            for c in criteria
        ]

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "content",
                        "content": [{"type": "text", "text": ch} for ch in chunks],
                    },
                    "title": "Candidate CV",
                    "citations": {"enabled": True},
                },
                {"type": "text", "text": _criteria_block(criteria)},
            ],
        }
    ]

    try:
        resp = client.messages.create(
            model=GROUNDING_MODEL,
            max_tokens=GROUNDING_MAX_TOKENS,
            temperature=GROUNDING_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=messages,
            metering={
                "feature": "candidate_grounding",
                "organization_id": organization_id,
                "entity_id": f"application:{application_id}",
            },
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the turn
        logger.warning(
            "grounded evidence call failed app=%s: %s", application_id, exc
        )
        return [
            CriterionVerdict(
                criterion=c, status="missing", note="Evidence check unavailable."
            )
            for c in criteria
        ]

    content = getattr(resp, "content", None) or []
    return parse_citation_response(content, criteria)
