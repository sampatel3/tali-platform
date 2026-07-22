"""Grounded per-criterion CV evidence via Anthropic Citations.

Given a candidate's CV text and a list of recruiter criteria, ask a routed model —
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

One logical route per candidate covers all criteria. The task profile owns the
eligible citation-capable deployments and cost/iteration ceilings; callers also
bound the candidate set to the ranked shortlist.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..components.ai_routing import (
    RoutingAttribution,
    TaskKey,
    estimate_anthropic_messages,
    plan_route,
    prepare_route,
    routed_messages_client,
)
from ..llm import MeteringContext, one_call
from ..services.pricing_service import Feature
from .metering import search_metering

logger = logging.getLogger("taali.candidate_search.grounded")

# Grounding judges every displayed criterion, so quote integrity matters. The
# selected deployment and its validated legacy override now belong to the
# universal routing policy rather than this feature module.
GROUNDING_MAX_TOKENS = 700
GROUNDING_TEMPERATURE = 0.0
# Per-request timeout for a single grounding attempt. It is generous enough for
# citation processing but bounded so a wedged call fails rather than hanging.
# A timeout is outcome-ambiguous and therefore is never automatically replayed.
GROUNDING_TIMEOUT_S = 45.0
# Cache version: bump to invalidate every cached verdict after a prompt/logic
# change. Part of the cache key alongside the evidence and route fingerprints.
GROUNDING_PROMPT_VERSION = "2"
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
    model_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "status": self.status,
            "grounded": self.grounded,
            "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
            "note": self.note,
            "model_id": self.model_id,
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
            model_id=(str(d["model_id"]) if d.get("model_id") else None),
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
    explicit_verdict_indexes: set[int] = set()
    for m in _VERDICT_RE.finditer(full_text):
        k = int(m.group(1))
        if not (1 <= k <= n):
            continue
        verdict_index = k - 1
        explicit_verdict_indexes.add(verdict_index)
        raw = m.group(2).upper().replace(" ", "_")
        if raw.startswith("NOT"):  # NOT_MET / NOTMET
            status = "not_met"
        elif raw == "MET":
            status = "met"
        elif raw == "MISSING":
            status = "missing"
        else:  # PARTIAL / PARTIALLY / PARTIALLY_MET
            status = "partially_met"
        verdicts[verdict_index].status = status
        line_start = m.end()
        nl = full_text.find("\n", line_start)
        note = full_text[line_start : (nl if nl != -1 else len(full_text))]
        verdicts[verdict_index].note = note.strip(" —-:.\t").strip()

    # Grounding enforcement: a verdict counts as grounded ONLY if a verbatim
    # quote was cited. A MET/PARTIAL with no citation keeps its status word
    # but is flagged ungrounded so the UI shows it as unverified and the
    # qualifying gate ignores it. A requested criterion the model omitted (or
    # emitted without one of the required verdict words) is a failed check, not
    # evidence of absence. Mark it `error` so it is never cached as `missing`.
    for index, v in enumerate(verdicts):
        if index not in explicit_verdict_indexes:
            v.status = "error"
            v.grounded = False
            v.source = "none"
            v.evidence = []
            v.note = "Evidence response omitted this criterion."
            continue
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
    """Stable hash of the exact evidence text the grounder reads (post-cap), so a
    changed CV or questionnaire answer misses the cache and re-grounds, while
    re-running the same query over an unchanged candidate hits it."""
    h = hashlib.sha256()
    h.update((cv_text or "").strip()[:CV_TEXT_CHAR_CAP].encode("utf-8"))
    h.update(b"\x00")
    h.update((notes_text or "").strip()[:NOTES_CHAR_CAP].encode("utf-8"))
    return h.hexdigest()[:32]


def _cache_key(
    organization_id: int,
    doc_hash: str,
    criterion: str,
    *,
    behavior_fingerprint: str,
) -> str:
    # Normalise the criterion so "Banking domain" and "banking  domain" share a
    # cache entry. The behavior fingerprint includes semantic/schema revisions,
    # policy, registry, and selected deployment, so routing changes cannot serve
    # a stale verdict.
    crit_norm = " ".join((criterion or "").lower().split())
    crit_hash = hashlib.sha256(crit_norm.encode("utf-8")).hexdigest()[:16]
    return (
        f"{_GROUNDING_CACHE_PREFIX}v{GROUNDING_PROMPT_VERSION}:"
        f"{behavior_fingerprint}:"
        f"{organization_id}:{doc_hash}:{crit_hash}"
    )


def _cache_get(
    r,
    organization_id: int,
    doc_hash: str,
    criterion: str,
    *,
    behavior_fingerprint: str,
) -> CriterionVerdict | None:
    if r is None:
        return None
    try:
        raw = r.get(
            _cache_key(
                organization_id,
                doc_hash,
                criterion,
                behavior_fingerprint=behavior_fingerprint,
            )
        )
        if not raw:
            return None
        return CriterionVerdict.from_dict(json.loads(raw))
    except Exception:  # pragma: no cover — never fail a query on a cache read
        logger.debug("grounding cache read failed", exc_info=True)
        return None


def _cache_set(
    r,
    organization_id: int,
    doc_hash: str,
    verdict: CriterionVerdict,
    *,
    behavior_fingerprint: str,
) -> None:
    # Only cache real judgements. An `error` verdict is a failed check, not a
    # result — caching it would freeze a transient blip into a permanent answer.
    if r is None or verdict is None or verdict.status == "error":
        return
    try:
        r.setex(
            _cache_key(
                organization_id,
                doc_hash,
                verdict.criterion,
                behavior_fingerprint=behavior_fingerprint,
            ),
            GROUNDING_CACHE_TTL_S,
            json.dumps(verdict.to_dict()),
        )
    except Exception:  # pragma: no cover — never fail a query on a cache write
        logger.debug("grounding cache write failed", exc_info=True)


def _grounding_request(
    client,
    *,
    execution,
    messages,
    organization_id,
    role_id: int | None,
    application_id,
    require_role_authority: bool = False,
):
    """Run one Citations call; the adapter owns all safe physical retries."""
    call_metering = search_metering(
        organization_id=int(organization_id),
        role_id=int(role_id) if role_id is not None else None,
        feature=Feature.CANDIDATE_GROUNDING,
        entity_id=f"application:{application_id}",
        sub_feature="candidate_search_grounding",
        trace_id=f"candidate-search:grounding:application:{application_id}",
        require_role_authority=bool(require_role_authority),
    )
    return one_call(
        client,
        model=execution.selected_model_id,
        max_tokens=GROUNDING_MAX_TOKENS,
        temperature=GROUNDING_TEMPERATURE,
        system=_SYSTEM_PROMPT,
        messages=messages,
        timeout=GROUNDING_TIMEOUT_S,
        metering=MeteringContext.from_dict(
            call_metering,
            default_feature=Feature.CANDIDATE_GROUNDING,
        ),
    )


def _error_verdict(criterion: str, note: str) -> CriterionVerdict:
    """A check that couldn't be completed — NOT a judgement. Distinct from
    `missing` so the UI shows 'couldn't verify' and the caller can retry."""
    return CriterionVerdict(
        criterion=criterion, status="error", grounded=False, note=note
    )


def extract_cv_evidence(
    *,
    cv_text: str | None,
    criteria: list[str],
    route_client_factory=None,
    organization_id: int,
    role_id: int | None = None,
    application_id: int,
    notes_text: str | None = None,
    require_role_authority: bool = False,
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

    Failure is explicit, not faked. Only an explicit non-billable provider
    rejection may be retried; ambiguous transport failures are never replayed.
    A criterion that cannot complete comes back as ``status="error"`` (never a
    fabricated ``missing``, never cached) so the caller can retry intentionally.
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
                criterion=criterion,
                status="missing",
                note="No CV or notes available.",
            )
            for criterion in criteria
        ]

    full_messages = [
        {
            "role": "user",
            "content": [
                *documents,
                {"type": "text", "text": _criteria_block(criteria)},
            ],
        }
    ]
    try:
        execution = prepare_route(
            TaskKey.SEARCH_GROUNDING,
            request_estimate=estimate_anthropic_messages(
                system=_SYSTEM_PROMPT,
                messages=full_messages,
                max_tokens=GROUNDING_MAX_TOKENS,
            ),
            attribution=RoutingAttribution(
                organization_id=int(organization_id),
                role_id=int(role_id) if role_id is not None else None,
                entity_id=f"application:{application_id}",
            ),
            operation="candidate_search.ground_evidence",
            require_role_authority=bool(require_role_authority),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("grounded evidence routing failed app=%s: %s", application_id, exc)
        return [
            _error_verdict(criterion, "Evidence check failed — will retry.")
            for criterion in criteria
        ]

    behavior_fingerprint = execution.decision.behavior_fingerprint
    r = _redis()
    doc_hash = _doc_hash(cv_text, notes_text)
    verdicts: dict[str, CriterionVerdict] = {}
    misses: list[str] = []
    workflow_succeeded = False
    try:
        for criterion in criteria:
            cached = _cache_get(
                r,
                organization_id,
                doc_hash,
                criterion,
                behavior_fingerprint=behavior_fingerprint,
            )
            if cached is not None:
                verdicts[criterion] = cached
            else:
                misses.append(criterion)

        if misses:
            messages = [
                {
                    "role": "user",
                    "content": [
                        *documents,
                        {"type": "text", "text": _criteria_block(misses)},
                    ],
                }
            ]
            selected_model_id = execution.selected_model_id
            try:
                resp = _grounding_request(
                    (route_client_factory or routed_messages_client)(execution),
                    execution=execution,
                    messages=messages,
                    organization_id=organization_id,
                    role_id=role_id,
                    application_id=application_id,
                    require_role_authority=bool(require_role_authority),
                )
                selected_model_id = execution.selected_model_id
            except Exception as exc:  # noqa: BLE001
                selected_model_id = (
                    execution.last_attempt_model_id or execution.selected_model_id
                )
                logger.warning(
                    "grounded evidence call failed app=%s: %s",
                    application_id,
                    exc,
                )
                for criterion in misses:
                    verdict = _error_verdict(
                        criterion,
                        "Evidence check failed — will retry.",
                    )
                    verdict.model_id = selected_model_id
                    verdicts[criterion] = verdict
            else:
                fresh = parse_citation_response(
                    getattr(resp, "content", None) or [],
                    misses,
                    doc_sources,
                )
                for verdict in fresh:
                    verdict.model_id = selected_model_id
                    _cache_set(
                        r,
                        organization_id,
                        doc_hash,
                        verdict,
                        behavior_fingerprint=behavior_fingerprint,
                    )
                    verdicts[verdict.criterion] = verdict

        workflow_succeeded = all(
            verdict.status != "error" for verdict in verdicts.values()
        ) and len(verdicts) == len(criteria)
        return [
            verdicts.get(criterion)
            or _error_verdict(criterion, "No verdict returned.")
            for criterion in criteria
        ]
    finally:
        execution.finish_workflow(succeeded=workflow_succeeded)


def grounding_model_id() -> str:
    """Expose the centrally selected model for the existing API response field."""

    return plan_route(TaskKey.SEARCH_GROUNDING).selected_model_id
