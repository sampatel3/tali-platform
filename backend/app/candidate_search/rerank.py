"""Claude rerank for soft NL-search criteria.

When a query carries qualitative phrases ("large enterprise", "in
production") that the SQL layer can't decide, this module asks Claude
which of the SQL-passing candidates actually match. Each candidate is
sent with a compact summary plus their graph neighbourhood (top
companies, schools, skills, colleagues) so the model can reason about
employer scale, peer cohort, etc.

Single Claude call PER CANDIDATE in v1. Caller bounds the input set
(``RERANK_TOP_N`` in runner.py). Each call is ~$0.001 at Haiku rates;
for a 50-candidate rerank, that's ~$0.05 worst-case per query.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from . import MODEL_VERSION
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication

logger = logging.getLogger("taali.candidate_search.rerank")

RERANK_MAX_TOKENS = 256
RERANK_TEMPERATURE = 0.0


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = obj_match.group(0)
    return text


def _resolve_anthropic_client(*, organization_id: int | None = None):
    """Build a metered Anthropic client. ``organization_id`` is bound at
    construction so every rerank call records to the right org without
    each call repeating it."""
    from ..services.claude_client_resolver import get_shared_client

    return get_shared_client(organization_id=organization_id)


def _build_candidate_summary(candidate: Candidate, application: CandidateApplication) -> dict:
    """Compact summary used as Claude prompt context. Token-bounded by truncation."""
    cv_sections = candidate.cv_sections or {}
    skills_top = (cv_sections.get("skills") or candidate.skills or [])[:30]
    experience = (cv_sections.get("experience") or [])[:6]
    return {
        "headline": (candidate.headline or "")[:160],
        "summary": (cv_sections.get("summary") or candidate.summary or "")[:600],
        "skills_top": [str(s)[:60] for s in skills_top],
        "experience_top": [
            {
                "company": str(e.get("company", ""))[:80],
                "title": str(e.get("title", ""))[:80],
                "start": str(e.get("start") or e.get("start_date", ""))[:16],
                "end": str(e.get("end") or e.get("end_date", ""))[:16],
            }
            for e in experience
            if isinstance(e, dict)
        ],
        "current_country": (candidate.location_country or "")[:80],
        "cv_match_score": application.cv_match_score,
    }


def _build_graph_context(
    *, organization_id: int, candidate_id: int
) -> dict | None:
    """Pull a compact graph neighbourhood from Neo4j (or None when unavailable)."""
    try:
        from ..candidate_graph import client as graph_client

        if not graph_client.is_configured():
            return None
        from ..candidate_graph import search as graph_search

        return graph_search.colleague_neighbourhood(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )
    except Exception as exc:
        logger.debug("Graph context unavailable for candidate=%s: %s", candidate_id, exc)
        return None


_SYSTEM_PROMPT = (
    "You are a recruiter's qualitative filter. Given soft criteria and one "
    "candidate's profile (with optional graph neighbourhood), decide if the "
    "candidate clearly matches ALL the criteria.\n\n"
    "Respond with ONLY this JSON: {\"match\": true|false, \"reason\": \"<short>\"}.\n"
    "- 'match' is true ONLY when every soft criterion has supporting evidence.\n"
    "- 'reason' is one sentence, ≤25 words, citing the strongest evidence (or absence).\n"
    "- Do not invent facts. If evidence is absent, set match=false.\n"
)


def _shared_prefix_text(soft_criteria: list[str]) -> str:
    """Build the per-query shared block: soft criteria + decision rules.

    Identical for every candidate in a rerank query, so we mark it
    ``cache_control=ephemeral`` so Anthropic can serve it at the cheap
    cache-read rate on candidates 2..N.

    Note: the cache only materializes when the cached prefix exceeds the
    Haiku 4.5 minimum (~4096 tokens). Short queries (1-2 brief criteria)
    won't actually cache — but the structural cost is zero, so we mark it
    unconditionally and let Anthropic decide.
    """
    rendered_criteria = "\n".join(f"- {c}" for c in soft_criteria) or "(none)"
    return (
        "=== SOFT CRITERIA (shared across candidates in this query) ===\n"
        f"{rendered_criteria}\n\n"
        "=== DECISION RULES ===\n"
        "Match a soft criterion only when the candidate's profile or graph\n"
        "neighbourhood provides positive supporting evidence. Absence of\n"
        "evidence is NOT evidence of absence — but it IS sufficient grounds\n"
        "for match=false (we are filtering for confident matches, not\n"
        "borderline cases). Cite the strongest evidence (or absence) in\n"
        "the reason. Keep reason under 25 words.\n"
    )


def _candidate_block_text(summary: dict, graph: dict | None) -> str:
    """Per-candidate user block — the only thing that changes per call."""
    payload: dict = {"candidate": summary}
    if graph is not None:
        payload["graph_neighbourhood"] = graph
    return (
        "=== CANDIDATE TO EVALUATE ===\n"
        f"{json.dumps(payload, separators=(',', ':'))[:6000]}\n\n"
        "Return only the JSON match decision."
    )


def _evaluate_one(
    *,
    soft_criteria: list[str],
    summary: dict,
    graph: dict | None,
    client,
    metrics: dict | None = None,
    metering: dict | None = None,
) -> bool:
    """Return True iff Claude marks the candidate as a match. Defaults to False on error.

    ``metrics`` (optional) is mutated in place to accumulate
    ``input_tokens`` / ``output_tokens`` / ``cache_read_tokens`` /
    ``cache_creation_tokens`` across all candidates in a rerank batch,
    so the caller can observe whether prompt caching is actually
    materialising for this query.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _shared_prefix_text(soft_criteria),
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": _candidate_block_text(summary, graph),
                },
            ],
        }
    ]
    try:
        response = client.messages.create(
            model=MODEL_VERSION,
            max_tokens=RERANK_MAX_TOKENS,
            temperature=RERANK_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=messages,
            metering=metering or {"feature": "cv_rerank"},
        )
        if metrics is not None:
            usage = getattr(response, "usage", None)
            if usage is not None:
                metrics["input_tokens"] = metrics.get("input_tokens", 0) + int(
                    getattr(usage, "input_tokens", 0) or 0
                )
                metrics["output_tokens"] = metrics.get("output_tokens", 0) + int(
                    getattr(usage, "output_tokens", 0) or 0
                )
                metrics["cache_read_tokens"] = metrics.get("cache_read_tokens", 0) + int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0
                )
                metrics["cache_creation_tokens"] = metrics.get(
                    "cache_creation_tokens", 0
                ) + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        raw = ""
        try:
            raw = response.content[0].text  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            raw = ""
        text = _strip_json_fences(raw)
        decision = json.loads(text)
    except Exception as exc:
        logger.debug("Rerank call failed: %s", exc)
        return False
    return bool(decision.get("match", False))


def rerank_application_ids(
    *,
    db: Session,
    organization_id: int,
    application_ids: list[int],
    soft_criteria: list[str],
    client=None,
) -> list[int]:
    """Filter ``application_ids`` to those matching every soft criterion.

    Order is preserved. On any unexpected failure the candidate is dropped
    (conservative — recruiters would rather see a tighter, smaller list
    than false positives from a broken rerank step).
    """
    if not application_ids or not soft_criteria:
        return application_ids

    if client is None:
        try:
            client = _resolve_anthropic_client(organization_id=organization_id)
        except Exception as exc:
            logger.warning("Rerank client init failed; dropping rerank: %s", exc)
            # Fall back to keeping the input list — better to over-include
            # than silently drop everyone when Claude is unreachable.
            return application_ids

    apps = (
        db.query(CandidateApplication)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.id.in_(application_ids),
        )
        .all()
    )
    by_id = {int(a.id): a for a in apps}

    metrics: dict = {}
    kept: list[int] = []
    for app_id in application_ids:
        application = by_id.get(int(app_id))
        if application is None or application.candidate is None:
            continue
        candidate = application.candidate
        summary = _build_candidate_summary(candidate, application)
        graph = _build_graph_context(
            organization_id=organization_id, candidate_id=int(candidate.id)
        )
        if _evaluate_one(
            soft_criteria=soft_criteria,
            summary=summary,
            graph=graph,
            client=client,
            metrics=metrics,
            metering={
                "feature": "cv_rerank",
                "organization_id": organization_id,
                "entity_id": f"application:{app_id}",
                "db": db,
            },
        ):
            kept.append(int(app_id))

    if metrics:
        cached = metrics.get("cache_read_tokens", 0)
        written = metrics.get("cache_creation_tokens", 0)
        billed = metrics.get("input_tokens", 0)
        total_input = cached + written + billed
        cache_hit_pct = (cached / total_input * 100.0) if total_input else 0.0
        logger.info(
            "Rerank usage: candidates=%d input=%d cache_read=%d cache_write=%d hit_pct=%.1f",
            len(application_ids),
            billed,
            cached,
            written,
            cache_hit_pct,
        )
    return kept
