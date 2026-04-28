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


def _resolve_anthropic_client():
    from anthropic import Anthropic

    from ..platform.config import settings

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return Anthropic(api_key=api_key)


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


def _user_prompt(soft_criteria: list[str], summary: dict, graph: dict | None) -> str:
    payload = {
        "soft_criteria": soft_criteria,
        "candidate": summary,
    }
    if graph is not None:
        payload["graph_neighbourhood"] = graph
    return (
        "Decide whether the candidate matches every soft criterion.\n\n"
        "INPUT JSON:\n"
        f"{json.dumps(payload, separators=(',', ':'))[:6000]}\n\n"
        "Return only the JSON match decision."
    )


def _evaluate_one(
    *,
    soft_criteria: list[str],
    summary: dict,
    graph: dict | None,
    client,
) -> bool:
    """Return True iff Claude marks the candidate as a match. Defaults to False on error."""
    user_prompt = _user_prompt(soft_criteria, summary, graph)
    try:
        response = client.messages.create(
            model=MODEL_VERSION,
            max_tokens=RERANK_MAX_TOKENS,
            temperature=RERANK_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
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
            client = _resolve_anthropic_client()
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
        ):
            kept.append(int(app_id))
    return kept
