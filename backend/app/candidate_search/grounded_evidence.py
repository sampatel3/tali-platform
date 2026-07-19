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

One bounded Anthropic call per candidate (covers all criteria). It defaults
to the codebase Sonnet model because citation judgement quality materially
affects displayed evidence. Callers cap the ranked shortlist, so worst-case
cost remains bounded by ``len(shortlist)`` calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..cv_matching.holistic_cache_policy import (
    ProtectedWorkableEvidenceOverflow,
    compact_workable_context,
)
from ..llm.models import SONNET_MODEL as _SONNET_MODEL
from ..platform.config import settings
from ..services.claude_model_pricing import require_priceable_claude_model
from ..services.pricing_service import Feature
from ..services.provider_error_evidence import safe_provider_error_code
from ..services.provider_retry_policy import PROVIDER_WIRE_ATTEMPT_LIMIT_KEY
from .metering import admitted_search_metering

logger = logging.getLogger("taali.candidate_search.grounded")

# Grounding judges every displayed criterion (Sam: "keep citation for ALL
# grounding"), so quote integrity matters. Default to Sonnet — Haiku citations
# under-performed on the judgement, and pinning it here means a worker missing
# the CLAUDE_GROUNDING_MODEL env var can't silently fall back to Haiku (the
# per-service drift that ran ~45% of prod grounding on Haiku). Same Sonnet the
# holistic scorer uses, so the two CV reads agree.
GROUNDING_MODEL = (settings.CLAUDE_GROUNDING_MODEL or "").strip() or _SONNET_MODEL
GROUNDING_MAX_TOKENS = 700
GROUNDING_TEMPERATURE = 0.0
# Per-request timeout for a single grounding attempt. Generous enough that a
# Sonnet citation call finishes within it (the old 20s killed slow calls and,
# with retries disabled, turned one transient blip into a blanked candidate),
# but bounded so a wedged call fails and is retried rather than hanging.
GROUNDING_TIMEOUT_S = 45.0
# Retry transient failures (timeout / 429 / 5xx / overloaded) before giving up.
# A failed call still bills tokens, so the goal is for it NOT to fail — we retry
# rather than degrade. On final exhaustion the criterion becomes an explicit
# `error` verdict (never a fake `missing`, never cached), so the UI can show
# "couldn't verify — retrying" instead of "no evidence".
GROUNDING_MAX_ATTEMPTS = 3
GROUNDING_BACKOFF_BASE_S = 0.5
GROUNDING_BACKOFF_MAX_S = 4.0
# Cache version: bump to invalidate every cached verdict after a prompt/logic
# change. Part of the cache key alongside the CV+notes hash and the model.
GROUNDING_PROMPT_VERSION = "1"
# Cached verdicts live this long; the CV+notes content hash in the key means a
# changed CV or questionnaire answer misses and re-grounds, so a long TTL is safe.
GROUNDING_CACHE_TTL_S = 90 * 24 * 3600
_GROUNDING_CACHE_PREFIX = "taali:grounding:"
# Cap CV text sent to bound cost; most CVs sit well under this. Citation
# char offsets are relative to this (possibly truncated) string.
CV_TEXT_CHAR_CAP = 16000
# Cap the recruiter-notes / Workable evidence corpus (profile, questionnaire
# answers, comments, activity log) sent alongside the CV.
NOTES_CHAR_CAP = 8000


class GroundingDeadlineExceeded(TimeoutError):
    """Raised before admission when the shared grounding deadline has passed."""

# Anthropic transient error types to retry. Imported defensively so a test that
# injects a fake client (or an environment without the SDK) still works — the
# tuple is just empty there and nothing is caught as "transient".
try:  # pragma: no cover - import shape, not logic
    import anthropic as _anthropic

    _TRANSIENT_ERRORS = tuple(
        e
        for e in (
            getattr(_anthropic, "APITimeoutError", None),
            getattr(_anthropic, "RateLimitError", None),
            getattr(_anthropic, "InternalServerError", None),
            getattr(_anthropic, "OverloadedError", None),
            getattr(_anthropic, "APIConnectionError", None),
        )
        if isinstance(e, type)
    )
except Exception:  # pragma: no cover
    _TRANSIENT_ERRORS = ()

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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        def _int(v: Any) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return -1

        return cls(
            quote=str(d.get("quote") or ""),
            start_char=_int(d.get("start_char", -1)),
            end_char=_int(d.get("end_char", -1)),
            source=str(d.get("source") or "cv"),
        )


@dataclass
class CriterionVerdict:
    """Per-criterion judgement. ``grounded`` is true only when at least one
    verbatim quote backs the verdict — that is the anti-hallucination gate."""

    criterion: str
    # met | partially_met | not_met | missing | error. `error` means the check
    # could not be completed (transient failure after retries / timeout) — it is
    # NOT a judgement and must never be shown as "no evidence" or cached.
    status: str = "missing"
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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CriterionVerdict":
        return cls(
            criterion=str(d.get("criterion") or ""),
            status=str(d.get("status") or "missing"),
            grounded=bool(d.get("grounded", False)),
            source=str(d.get("source") or "none"),
            evidence=[
                Evidence.from_dict(e)
                for e in (d.get("evidence") or [])
                if isinstance(e, dict)
            ],
            note=str(d.get("note") or ""),
        )


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
    "- The one short sentence is the REASON for the verdict — it must explain "
    "WHY, in plain words a recruiter can read at a glance. For PARTIAL say what "
    "IS satisfied and what is NOT (e.g. 'Worked at Werkdata OÜ in Estonia (EU/"
    "Western), but his other employers — Emirates NBD, CiSS Egypt — are not "
    "Western'). For NOT_MET say what disqualifies it. Don't just restate the "
    "criterion.\n"
    "- For MET / PARTIAL / NOT_MET you MUST cite the single decisive line (a "
    "stated figure, employer, title, project, or exact phrase). Keep it tight — "
    "not contact details, full skills lists, or boilerplate. When several items "
    "matter (e.g. a mix of Western and non-Western employers), cite the few that "
    "decide the verdict, not every one.\n"
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


def _chunk_workable_evidence(text: str) -> list[str]:
    """Split bounded Workable evidence without dropping or rewriting bytes.

    CV chunking deliberately favours tight citations and caps the number of
    short semantic fragments.  Reusing that strategy for protected Workable
    evidence is unsafe: a valid corpus containing many short questionnaire or
    activity rows can exceed ``MAX_CV_CHUNKS`` while remaining below the single
    32,000-character rail, silently hiding the late rows.  Fixed-width blocks
    retain the complete provider-visible corpus and still need at most 146
    blocks at the current 220-character granularity.
    """

    return [
        text[offset : offset + CV_CHUNK_MAX_LEN]
        for offset in range(0, len(text), CV_CHUNK_MAX_LEN)
    ]


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


def _redis():
    """Best-effort Redis handle for the grounding cache (shared infra with the
    holistic scorer's caches). Returns None when Redis is unavailable, which
    disables caching cleanly — grounding still works, just without reuse."""
    try:
        import redis

        from ..platform.config import settings

        return redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
    except Exception:  # pragma: no cover — cache is best-effort
        return None


def _doc_hash(cv_text: str | None, notes_text: str | None) -> str:
    """Stable hash of the exact evidence text the grounder reads (post-rail), so a
    changed CV or questionnaire answer misses the cache and re-grounds, while
    re-running the same query over an unchanged candidate hits it."""
    h = hashlib.sha256()
    h.update((cv_text or "").strip()[:CV_TEXT_CHAR_CAP].encode("utf-8"))
    h.update(b"\x00")
    h.update((notes_text or "").strip().encode("utf-8"))
    return h.hexdigest()[:32]


def _cache_key(organization_id: int, doc_hash: str, criterion: str) -> str:
    # Normalise the criterion so "Banking domain" and "banking  domain" share a
    # cache entry; version + model are in the key so a prompt/model change can't
    # serve a stale verdict.
    crit_norm = " ".join((criterion or "").lower().split())
    crit_hash = hashlib.sha256(crit_norm.encode("utf-8")).hexdigest()[:16]
    return (
        f"{_GROUNDING_CACHE_PREFIX}v{GROUNDING_PROMPT_VERSION}:{GROUNDING_MODEL}:"
        f"{organization_id}:{doc_hash}:{crit_hash}"
    )


def _cache_get(
    r, organization_id: int, doc_hash: str, criterion: str
) -> CriterionVerdict | None:
    if r is None:
        return None
    try:
        raw = r.get(_cache_key(organization_id, doc_hash, criterion))
        if not raw:
            return None
        return CriterionVerdict.from_dict(json.loads(raw))
    except Exception as exc:  # pragma: no cover — cache is best-effort
        logger.debug(
            "grounding cache read failed error_code=%s",
            safe_provider_error_code(exc, operation="grounding_cache_read"),
        )
        return None


def _cache_set(
    r, organization_id: int, doc_hash: str, verdict: CriterionVerdict
) -> None:
    # Only cache real judgements. An `error` verdict is a failed check, not a
    # result — caching it would freeze a transient blip into a permanent answer.
    if r is None or verdict is None or verdict.status == "error":
        return
    try:
        r.setex(
            _cache_key(organization_id, doc_hash, verdict.criterion),
            GROUNDING_CACHE_TTL_S,
            json.dumps(verdict.to_dict()),
        )
    except Exception as exc:  # pragma: no cover — cache is best-effort
        logger.debug(
            "grounding cache write failed error_code=%s",
            safe_provider_error_code(exc, operation="grounding_cache_write"),
        )


def _is_transient(exc: Exception) -> bool:
    """Worth retrying? Timeouts, connection drops, rate limits, and any 5xx/529
    (overload) are transient. Anything with a 4xx status (400 malformed doc, 401,
    403, 404, 413) is permanent — retrying only burns money. The status-code
    check covers 529, which this SDK version raises as a bare APIStatusError
    rather than a dedicated OverloadedError class."""
    if _TRANSIENT_ERRORS and isinstance(exc, _TRANSIENT_ERRORS):
        return True
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and (code == 429 or code >= 500)


def _grounding_request(
    client,
    *,
    messages,
    organization_id,
    role_id: int | None,
    application_id,
    deadline_monotonic: float | None = None,
):
    """One Citations call, retried with exponential backoff on TRANSIENT errors
    (timeout / 429 / 5xx / overloaded). Non-transient errors (e.g. a 400 from a
    malformed document) raise immediately. If every attempt fails the last
    exception is raised for the caller to surface."""
    require_priceable_claude_model(GROUNDING_MODEL)
    last_exc: Exception | None = None
    grounding_trace_id = (
        f"candidate-search:grounding:application:{application_id}:"
        f"{uuid.uuid4().hex}"
    )
    for attempt in range(GROUNDING_MAX_ATTEMPTS):
        try:
            timeout = GROUNDING_TIMEOUT_S
            if deadline_monotonic is not None:
                timeout = min(timeout, deadline_monotonic - time.monotonic())
                if timeout <= 0:
                    raise GroundingDeadlineExceeded("grounding deadline exceeded")
            provider_request = {
                "model": GROUNDING_MODEL,
                "max_tokens": GROUNDING_MAX_TOKENS,
                "temperature": GROUNDING_TEMPERATURE,
                "system": _SYSTEM_PROMPT,
                "messages": messages,
                "timeout": timeout,
            }
            option_builder = getattr(type(client), "with_options", None)
            attempt_client = (
                option_builder(client, timeout=timeout, max_retries=0)
                if callable(option_builder)
                else client
            )
            call_metering = admitted_search_metering(
                organization_id=int(organization_id),
                role_id=int(role_id) if role_id is not None else None,
                feature=Feature.CANDIDATE_GROUNDING,
                entity_id=f"application:{application_id}",
                sub_feature="candidate_search_grounding",
                trace_id=grounding_trace_id,
                metadata={"retry_attempt": int(attempt)},
                base_metering={
                    PROVIDER_WIRE_ATTEMPT_LIMIT_KEY: 1,
                    "retry_attempt": int(attempt),
                },
                provider_request=provider_request,
            )
            return attempt_client.messages.create(
                **provider_request, metering=call_metering
            )
        except Exception as exc:  # noqa: BLE001 — re-raise non-transient below
            if isinstance(exc, GroundingDeadlineExceeded):
                raise
            if not _is_transient(exc):
                raise
            last_exc = exc
            if attempt + 1 >= GROUNDING_MAX_ATTEMPTS:
                break
            delay = min(
                GROUNDING_BACKOFF_BASE_S * (2 ** attempt), GROUNDING_BACKOFF_MAX_S
            )
            if deadline_monotonic is not None:
                remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    raise GroundingDeadlineExceeded(
                        "grounding deadline exceeded"
                    ) from None
                delay = min(delay, remaining)
            logger.info(
                "grounding transient error app=%s attempt=%d/%d delay=%.1fs error_code=%s",
                application_id,
                attempt + 1,
                GROUNDING_MAX_ATTEMPTS,
                delay,
                safe_provider_error_code(exc, operation="grounding_retry"),
            )
            time.sleep(delay)
    raise last_exc if last_exc is not None else RuntimeError("grounding failed")


def _error_verdict(criterion: str, note: str) -> CriterionVerdict:
    """A check that couldn't be completed — NOT a judgement. Distinct from
    `missing` so the UI shows 'couldn't verify' and the caller can retry."""
    return CriterionVerdict(criterion=criterion, status="error", grounded=False, note=note)


def extract_cv_evidence(
    *,
    cv_text: str | None,
    criteria: list[str],
    client,
    organization_id: int,
    role_id: int | None = None,
    application_id: int,
    notes_text: str | None = None,
    deadline_monotonic: float | None = None,
) -> list[CriterionVerdict]:
    """Per-criterion grounded verdicts over the candidate's evidence (CV +
    recruiter notes / stated details), backed by a persistent cache.

    Each (CV+notes content, criterion) pair is grounded at most once: cached
    verdicts are reused and only the cache-miss criteria are sent — in a single
    Citations call regardless of how many miss, since the CV dominates the cost.
    ``notes_text`` is the candidate's Workable evidence corpus (profile,
    questionnaire answers, recruiter comments, activity log), where constraints
    like salary expectation and notice period are usually stated rather than in
    the CV. Each verdict's quotes are tagged with their source (``cv`` /
    ``notes``).

    Failure is explicit, not faked: a transient call failure is retried, and if
    it still can't complete the criterion comes back as ``status="error"`` (never
    a fabricated ``missing``, never cached) so the caller can retry and the UI can
    show "couldn't verify" instead of "no evidence".
    """
    criteria = [c.strip() for c in (criteria or []) if c and c.strip()]
    if not criteria:
        return []

    try:
        prompt_notes = compact_workable_context(
            notes_text,
            max_chars=NOTES_CHAR_CAP,
        )
    except ProtectedWorkableEvidenceOverflow:
        # Late questionnaire/comment constraints must not disappear behind the
        # old notes prefix. An explicit error is safer than a grounded-looking
        # judgement over an incomplete evidence corpus, and incurs no LLM cost.
        return [
            _error_verdict(
                criterion,
                "Protected Workable evidence exceeds the grounding safety ceiling.",
            )
            for criterion in criteria
        ]

    r = _redis()
    doc_hash = _doc_hash(cv_text, prompt_notes)
    verdicts: dict[str, CriterionVerdict] = {}
    misses: list[str] = []
    for c in criteria:
        cached = _cache_get(r, organization_id, doc_hash, c)
        if cached is not None:
            verdicts[c] = cached
        else:
            misses.append(c)

    if misses:
        documents: list[dict[str, Any]] = []
        doc_sources: list[str] = []

        cv_chunks = _chunk_cv((cv_text or "").strip()[:CV_TEXT_CHAR_CAP])
        if cv_chunks:
            documents.append(_content_document(cv_chunks, "Candidate CV"))
            doc_sources.append("cv")

        notes_chunks = _chunk_workable_evidence(prompt_notes)
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
            # Genuinely nothing to read — an honest "missing", and deterministic
            # for this (empty) CV+notes hash, so it's safe to cache.
            for c in misses:
                v = CriterionVerdict(
                    criterion=c, status="missing", note="No CV or notes available."
                )
                _cache_set(r, organization_id, doc_hash, v)
                verdicts[c] = v
        else:
            messages = [
                {
                    "role": "user",
                    "content": [
                        *documents,
                        {"type": "text", "text": _criteria_block(misses)},
                    ],
                }
            ]
            try:
                resp = _grounding_request(
                    client,
                    messages=messages,
                    organization_id=organization_id,
                    role_id=role_id,
                    application_id=application_id,
                    deadline_monotonic=deadline_monotonic,
                )
            except Exception as exc:  # noqa: BLE001 — surface as error, don't crash
                logger.warning(
                    "grounded evidence call failed app=%s error_code=%s",
                    application_id,
                    safe_provider_error_code(exc, operation="grounding_evidence"),
                )
                failure_note = (
                    "Evidence check didn't finish — retrying."
                    if isinstance(exc, GroundingDeadlineExceeded)
                    else "Evidence check failed — will retry."
                )
                for c in misses:
                    verdicts[c] = _error_verdict(c, failure_note)
            else:
                content = getattr(resp, "content", None) or []
                fresh = parse_citation_response(content, misses, doc_sources)
                for v in fresh:
                    _cache_set(r, organization_id, doc_hash, v)
                    verdicts[v.criterion] = v

    # Return in the caller's criterion order; anything still unresolved (a miss
    # the model omitted from its reply) is an explicit error, never a silent
    # "missing".
    return [
        verdicts.get(c) or _error_verdict(c, "No verdict returned.")
        for c in criteria
    ]
