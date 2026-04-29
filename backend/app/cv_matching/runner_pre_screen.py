"""Pre-screen runner — single tight LLM call, cached, fast-fails to "maybe".

Cost discipline:
- Model: ``claude-haiku-4-5-20251001`` (same as v3 for consistency).
- Temperature 0, max_tokens 256.
- One call. No retry on JSON parse failure — return decision="error"
  (which the orchestrator treats like "maybe" so v3 still runs).
- Cache key separate from v3 cache: prompt_version is the discriminator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from . import MODEL_VERSION
from .prompts_pre_screen import (
    PRE_SCREEN_PROMPT_VERSION,
    build_pre_screen_messages,
    build_pre_screen_prompt,
)
from .schemas import RequirementInput

logger = logging.getLogger("taali.cv_match.pre_screen")


PreScreenDecision = Literal["yes", "no", "maybe", "error"]


@dataclass
class PreScreenResult:
    decision: PreScreenDecision
    reason: str
    prompt_version: str
    model_version: str
    trace_id: str
    cache_hit: bool
    score: float | None = None  # 0-100 numeric pre-screen score (v2.0+)


def compute_pre_screen_cache_key(
    *,
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None,
) -> str:
    payload = {
        "cv": cv_text or "",
        "jd": jd_text or "",
        "must_haves": [
            r.requirement for r in (requirements or [])
            if getattr(r.priority, "value", str(r.priority or "")).lower() == "must_have"
        ],
        "prompt_version": PRE_SCREEN_PROMPT_VERSION,
        "model_version": MODEL_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
    return text


def _normalize_decision(value: str) -> PreScreenDecision:
    label = (value or "").strip().lower()
    if label in {"yes", "no", "maybe"}:
        return label  # type: ignore[return-value]
    return "error"


def _resolve_anthropic_client():
    from anthropic import Anthropic

    from ..platform.config import settings

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return Anthropic(api_key=api_key)


def _cache_get(cache_key: str) -> PreScreenResult | None:
    """Reuse the existing cv_score_cache table (different prompt_version
    keeps it isolated from v3 rows)."""
    try:
        from datetime import datetime, timezone

        from ..models.cv_score_cache import CvScoreCache
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Pre-screen cache get skipped (no DB): %s", exc)
        return None

    session = SessionLocal()
    try:
        row = session.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
        if row is None:
            return None
        result = row.result if isinstance(row.result, dict) else {}
        decision = _normalize_decision(str(result.get("decision") or ""))
        reason = str(result.get("reason") or "")
        raw_score = result.get("score")
        try:
            cached_score: float | None = max(0.0, min(100.0, float(raw_score)))
        except (TypeError, ValueError):
            cached_score = None
        try:
            row.hit_count = (row.hit_count or 0) + 1
            row.last_hit_at = datetime.now(timezone.utc)
            session.commit()
        except Exception:  # pragma: no cover — defensive
            session.rollback()
        return PreScreenResult(
            decision=decision,
            reason=reason,
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=str(result.get("trace_id") or ""),
            cache_hit=True,
            score=cached_score,
        )
    finally:
        session.close()


def _cache_set(cache_key: str, result: PreScreenResult, score: float | None = None) -> None:
    if result.decision == "error":
        return  # don't poison the cache with parse failures
    try:
        from ..models.cv_score_cache import CvScoreCache
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Pre-screen cache set skipped (no DB): %s", exc)
        return

    session = SessionLocal()
    try:
        existing = session.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
        if existing is not None:
            return
        row = CvScoreCache(
            cache_key=cache_key,
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model=MODEL_VERSION,
            score_100=score,
            result={
                "decision": result.decision,
                "score": result.score,
                "reason": result.reason,
                "trace_id": result.trace_id,
            },
            hit_count=0,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.warning("Pre-screen cache write failed: %s", exc)
        session.rollback()
    finally:
        session.close()


def run_pre_screen(
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None = None,
    *,
    client=None,
    skip_cache: bool = False,
) -> PreScreenResult:
    """Run the pre-screen LLM call. Never raises.

    Returns a ``PreScreenResult`` with ``decision`` in
    {yes, no, maybe, error}. ``error`` means the call or parse failed —
    the orchestrator should treat it as ``maybe`` so v3 still runs.
    """
    trace_id = str(uuid.uuid4())
    cache_key = compute_pre_screen_cache_key(
        cv_text=cv_text, jd_text=jd_text, requirements=requirements,
    )

    if not skip_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    if not (cv_text or "").strip() or not (jd_text or "").strip():
        return PreScreenResult(
            decision="error",
            reason="missing CV or job spec text",
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=trace_id,
            cache_hit=False,
        )

    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            logger.warning("Pre-screen client init failed: %s", exc)
            return PreScreenResult(
                decision="error",
                reason=f"client_init_failed: {exc}",
                prompt_version=PRE_SCREEN_PROMPT_VERSION,
                model_version=MODEL_VERSION,
                trace_id=trace_id,
                cache_hit=False,
            )

    messages = build_pre_screen_messages(cv_text, jd_text, requirements)
    started = time.monotonic()
    try:
        response = client.messages.create(
            model=MODEL_VERSION,
            max_tokens=256,
            temperature=0,
            system="You are a fast hiring pre-screener. Respond ONLY with valid JSON.",
            messages=messages,
        )
    except Exception as exc:
        logger.warning("Pre-screen Claude call failed: %s", exc)
        return PreScreenResult(
            decision="error",
            reason=f"claude_call_failed: {exc}",
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=trace_id,
            cache_hit=False,
        )

    raw = ""
    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        raw = ""

    text = _strip_json_fences(raw)
    decision: PreScreenDecision = "error"
    reason = ""
    parsed_score: float | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # v2.0: numeric score; v1.0 compat: fall back to decision field
            raw_score = parsed.get("score")
            if raw_score is not None:
                try:
                    parsed_score = max(0.0, min(100.0, float(raw_score)))
                    decision = "yes" if parsed_score >= 50.0 else "no"
                except (TypeError, ValueError):
                    pass
            if parsed_score is None:
                # v1.0 cache hit or malformed v2 response — use decision field
                decision = _normalize_decision(str(parsed.get("decision") or ""))
            reason = str(parsed.get("reason") or "")[:240]
    except json.JSONDecodeError as exc:
        logger.warning("Pre-screen JSON parse failed: %s", exc)
        decision = "error"
        reason = f"json_parse_failed: {exc}"[:240]

    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Pre-screen verdict: score=%s decision=%s elapsed_ms=%d reason=%s",
        parsed_score, decision, elapsed_ms, reason[:120],
    )

    result = PreScreenResult(
        decision=decision,
        reason=reason,
        prompt_version=PRE_SCREEN_PROMPT_VERSION,
        model_version=MODEL_VERSION,
        trace_id=trace_id,
        cache_hit=False,
        score=parsed_score,
    )
    if not skip_cache and decision != "error":
        _cache_set(cache_key, result, score=parsed_score)
    return result
