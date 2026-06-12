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
# Per-request timeout for a single grounding call. Overrides the client's
# 120s/1-retry default — grounding is fast and parallel, so a stuck call must
# fail quickly rather than stall the chat turn.
GROUNDING_TIMEOUT_S = 20.0
# Cap CV text sent to bound cost; most CVs sit well under this. Citation
# char offsets are relative to this (possibly truncated) string.
CV_TEXT_CHAR_CAP = 16000
# Cap the recruiter-notes / Workable evidence corpus (profile, questionnaire
# answers, comments, activity log) sent alongside the CV.
NOTES_CHAR_CAP = 8000

_MARKER_RE = re.compile(r"\[\[\s*C(\d+)\s*\]\]", re.IGNORECASE)
_VERDICT_RE = re.compile(
    r"\[\[\s*C(\d+)\s*\]\]\s*[\-—:.\s]*"
    r"(NOT[ _]?MET|MET|PARTIAL(?:LY)?(?:[ _]MET)?|MISSING)",
    re.IGNORECASE,
)


@dataclass
class Evidence:
    """One verbatim quote backing a verdict. ``source`` says where it came
    from — the CV, the candidate's recruiter notes / stated details, or a
    reused role requirement assessment."""

    quote: str
    start_char: int = -1
    end_char: int = -1
    source: str = "cv"  # cv | notes | role_requirement

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote": self.quote,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "source": self.source,
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
    "You judge whether a candidate meets specific recruiter criteria, using the "
    "candidate's evidence documents (the CV, and usually a NOTES document of "
    "recruiter notes, questionnaire answers, and stated details). All documents "
    "are candidate evidence, equal weight.\n\n"
    "For EACH criterion output exactly one line:\n"
    "[[C<n>]] <MET|PARTIAL|NOT_MET|MISSING> — <one short sentence>\n\n"
    "Two kinds of criteria:\n"
    "1) CONSTRAINTS on a stated value (salary expectation, notice period, years "
    "of experience, location, work authorisation), usually in the NOTES — look "
    "there. If the candidate stated a value, judge it against the cap/threshold "
    "in the criterion:\n"
    "   • CAP ('<= / under / at most X', e.g. salary <= 30k): AT OR BELOW X -> "
    "MET. ABOVE X but no more than 1.25x X (a small, negotiable overage, e.g. "
    "35k vs a 30k cap) -> PARTIAL (note e.g. 'states 35,000 AED, ~17% above the "
    "30k cap — negotiable'). MORE than 1.25x X -> NOT_MET. A value ABOVE the "
    "cap is NEVER MET.\n"
    "   • MINIMUM ('>= / at least X', e.g. 5+ years): at or above -> MET; below "
    "-> NOT_MET.\n"
    "   Always cite the stated value. If nothing relevant is stated -> MISSING.\n"
    "2) QUALITATIVE criteria (experience, skills, company type, domain). Judge "
    "as a careful recruiter would: the cited evidence must GENUINELY satisfy "
    "the criterion, not merely be a related fact. Do NOT mark MET just because "
    "you found an employer / title / keyword — verify it actually matches. MET "
    "only when it clearly satisfies; PARTIAL when related but not clearly "
    "satisfying; MISSING when it does not. Example — 'Western company': the "
    "employer must be a Western-origin company (North America, Western Europe, "
    "UK, Australia, etc.). A UAE/Gulf, South-Asian, or other non-Western "
    "employer does NOT qualify even though it is an employer — e.g. Emirates "
    "NBD (a Dubai bank) is NOT Western, whereas McKinsey or BNP Paribas are. "
    "Quote the company and judge its origin; if you genuinely can't tell, use "
    "PARTIAL/MISSING rather than guessing MET.\n\n"
    "Rules:\n"
    "- For MET / PARTIAL / NOT_MET you MUST cite the single decisive line (a "
    "stated figure, employer, title, project, or exact phrase). Keep it tight — "
    "not contact details, full skills lists, or boilerplate.\n"
    "- For MISSING cite nothing.\n"
    "- NEVER invent evidence; if a document doesn't contain it, it is not "
    "evidence.\n"
    "- Output only the [[C<n>]] lines, one per criterion, in order. No preamble."
)


def _criteria_block(criteria: list[str]) -> str:
    lines = "\n".join(f"[[C{i + 1}]] {c}" for i, c in enumerate(criteria))
    return (
        "Assess the candidate against each criterion below, using ALL the "
        "documents (CV and notes). Cite the decisive line for every MET, "
        "PARTIAL, or NOT_MET.\n\n" + lines
    )


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off an SDK block object or a plain dict (for tests)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def parse_citation_response(
    content_blocks: list[Any],
    criteria: list[str],
    doc_sources: list[str] | None = None,
) -> list[CriterionVerdict]:
    """Parse the interleaved text+citation response into per-criterion verdicts.

    Pure function (no I/O) so it is unit-testable with synthetic blocks.
    Walks blocks in order, tracking the most recent ``[[C<n>]]`` marker as
    the "current" criterion, and attaches each block's citations to it.
    Verdict words are read from the concatenated text. ``doc_sources`` maps a
    citation's ``document_index`` to a source label (e.g. ["cv", "notes"]) so
    each quote is tagged with where it came from.
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
                try:
                    doc_idx = int(_attr(c, "document_index", 0))
                except (TypeError, ValueError):
                    doc_idx = 0
                src = "cv"
                if doc_sources and 0 <= doc_idx < len(doc_sources):
                    src = doc_sources[doc_idx]
                verdicts[current_idx].evidence.append(
                    Evidence(quote=quote, start_char=start, end_char=end, source=src)
                )

    full_text = "".join(full_text_parts)
    for m in _VERDICT_RE.finditer(full_text):
        k = int(m.group(1))
        if not (1 <= k <= n):
            continue
        raw = m.group(2).upper().replace(" ", "_")
        if raw.startswith("NOT"):  # NOT_MET / NOTMET
            status = "not_met"
        elif raw == "MET":
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
        v.source = v.evidence[0].source if v.grounded else "none"

    return verdicts


# Citation granularity. Plain-text documents are auto-chunked "by sentence",
# which yields a single giant block for separator-laden CV headers (no real
# sentence boundaries) — so the model can only cite the whole blob. We instead
# send the CV as a CUSTOM-CONTENT document of small pre-split blocks, so a
# citation lands on one tight, relevant line.
CV_CHUNK_MAX_LEN = 220
# Cap citable blocks per document. Fewer blocks = faster citation processing;
# 200 small blocks already covers a full CV well past the char cap.
MAX_CV_CHUNKS = 200
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


def _content_document(chunks: list[str], title: str) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": ch} for ch in chunks],
        },
        "title": title,
        "citations": {"enabled": True},
    }


def extract_cv_evidence(
    *,
    cv_text: str | None,
    criteria: list[str],
    client,
    organization_id: int,
    application_id: int,
    notes_text: str | None = None,
) -> list[CriterionVerdict]:
    """Run one citations call over the candidate's evidence (CV + recruiter
    notes / stated details) and return per-criterion verdicts.

    ``notes_text`` is the candidate's Workable evidence corpus (profile,
    questionnaire answers, recruiter comments, activity log) — where hard
    constraints like salary expectation, notice period, and location are
    usually stated rather than in the CV. Each verdict's quotes are tagged
    with their source (``cv`` / ``notes``).

    Never raises: on any failure every criterion degrades to ``missing``
    with an explanatory note, so the caller can still render the candidate.
    """
    criteria = [c.strip() for c in (criteria or []) if c and c.strip()]
    if not criteria:
        return []

    documents: list[dict[str, Any]] = []
    doc_sources: list[str] = []

    cv_chunks = _chunk_cv((cv_text or "").strip()[:CV_TEXT_CHAR_CAP])
    if cv_chunks:
        documents.append(_content_document(cv_chunks, "Candidate CV"))
        doc_sources.append("cv")

    notes_chunks = _chunk_cv((notes_text or "").strip()[:NOTES_CHAR_CAP])
    if notes_chunks:
        documents.append(
            _content_document(
                notes_chunks,
                "Candidate notes & stated details (recruiter notes, "
                "questionnaire answers, activity log)",
            )
        )
        doc_sources.append("notes")

    if not documents:
        return [
            CriterionVerdict(
                criterion=c, status="missing", note="No CV or notes available."
            )
            for c in criteria
        ]

    messages = [
        {
            "role": "user",
            "content": [*documents, {"type": "text", "text": _criteria_block(criteria)}],
        }
    ]

    try:
        resp = client.messages.create(
            model=GROUNDING_MODEL,
            max_tokens=GROUNDING_MAX_TOKENS,
            temperature=GROUNDING_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=messages,
            # Short per-request timeout: grounding is a fast Haiku call and runs
            # in a parallel batch behind a chat turn — the default 120s/1-retry
            # (240s) would let one stuck call hang the whole response.
            timeout=GROUNDING_TIMEOUT_S,
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
    return parse_citation_response(content, criteria, doc_sources)
