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
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from ..llm import CallUsage, MeteringContext, one_call, strip_json_fences
from . import MODEL_VERSION
from .prompts_pre_screen import (
    PRE_SCREEN_PROMPT_VERSION,
    build_pre_screen_prompt,
    build_pre_screen_system,
    build_pre_screen_user_messages,
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
    # v2.2: gate flag — candidate leans on an extraordinary, CV-uncorroborated
    # claim (named hackathon win, award, publication). Drives a soft penalty
    # downstream; never a hard reject on its own.
    unverified_claim: bool = False
    # Token usage (populated by the runner, used by usage_metering_service).
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def compute_pre_screen_cache_key(
    *,
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None,
    workable_context: str | None = None,
) -> str:
    payload = {
        "cv": cv_text or "",
        "jd": jd_text or "",
        "must_haves": [
            r.requirement for r in (requirements or [])
            if getattr(r.priority, "value", str(r.priority or "")).lower() == "must_have"
        ],
        "workable_context": (workable_context or "").strip(),
        "prompt_version": PRE_SCREEN_PROMPT_VERSION,
        "model_version": MODEL_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _normalize_decision(value: str) -> PreScreenDecision:
    label = (value or "").strip().lower()
    if label in {"yes", "no", "maybe"}:
        return label  # type: ignore[return-value]
    return "error"


def _resolve_anthropic_client(*, organization_id: int | None = None):
    # Always return a ``MeteredAnthropicClient`` so the metering wrapper
    # is available — even when the caller passed nothing. Pre-screen calls
    # set ``metering={"skip": True}`` because cv_score_orchestrator records
    # the event post-call, but going through the wrapper means any caller
    # that *doesn't* set skip (e.g. a future direct-invocation path) is
    # auto-metered instead of going to /dev/null.
    #
    # ``organization_id`` flows to the gated resolver (per-org workspace-key
    # routing when ANTHROPIC_WORKSPACE_KEYS_ENABLED; shared key otherwise).
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client(organization_id=organization_id)


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
        unverified_claim = bool(result.get("unverified_extraordinary_claim") or False)
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
            unverified_claim=unverified_claim,
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
                "unverified_extraordinary_claim": result.unverified_claim,
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
    workable_context: str | None = None,
    metering_context: dict | None = None,
) -> PreScreenResult:
    """Run the pre-screen LLM call. Never raises.

    Returns a ``PreScreenResult`` with ``decision`` in
    {yes, no, maybe, error}. ``error`` means the call or parse failed —
    the orchestrator should treat it as ``maybe`` so v3 still runs.

    ``workable_context`` is an optional pre-rendered text block carrying
    every Workable surface for the candidate (questionnaire answers,
    recruiter comments, activity log, structured profile). It's threaded
    into the variable per-candidate prompt block so hard constraints
    expressed only in Workable get filtered at pre-screen.
    """
    trace_id = str(uuid.uuid4())
    cache_key = compute_pre_screen_cache_key(
        cv_text=cv_text,
        jd_text=jd_text,
        requirements=requirements,
        workable_context=workable_context,
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
            client = _resolve_anthropic_client(
                organization_id=(metering_context or {}).get("organization_id")
            )
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

    # Stable instructions + JD + must-haves live in a cached system block
    # (identical for every candidate in a role batch); the per-candidate
    # CV is the only uncached part. The old shape put the cacheable content
    # in a user-message block and produced zero cache hits in prod — the
    # system-param location is Anthropic's canonical, reliable spot.
    system_blocks = build_pre_screen_system(jd_text, requirements)
    messages = build_pre_screen_user_messages(cv_text, workable_context=workable_context)
    started = time.monotonic()
    # The wrapper writes the pre-screen usage_event per call (FK-linked to
    # claude_call_log) when a metering_context is threaded through — captures
    # every actual call. The orchestrator records ONLY cache hits (no
    # Anthropic call → no wrapper run), so no double-count. Absent a context
    # (direct/test calls with a bare client) we skip so the bare client
    # doesn't choke on the metering kwarg.
    if metering_context:
        pre_metering = MeteringContext(
            feature="prescreen",
            organization_id=metering_context.get("organization_id"),
            role_id=metering_context.get("role_id"),
            entity_id=metering_context.get("entity_id"),
            user_id=metering_context.get("user_id"),
        )
    else:
        pre_metering = MeteringContext.skipped(metered_by="direct_call_no_context")
    usage = CallUsage()
    try:
        response = one_call(
            client,
            model=MODEL_VERSION,
            system=system_blocks,
            messages=messages,
            max_tokens=256,
            temperature=0,
            metering=pre_metering,
            usage_sink=usage,
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

    in_tok = usage.input_tokens
    out_tok = usage.output_tokens
    cache_read_tok = usage.cache_read_tokens
    cache_creation_tok = usage.cache_creation_tokens

    text = strip_json_fences(raw)
    decision: PreScreenDecision = "error"
    reason = ""
    parsed_score: float | None = None
    parsed_unverified: bool = False
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
            parsed_unverified = bool(parsed.get("unverified_extraordinary_claim") or False)
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
        unverified_claim=parsed_unverified,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_read_tok,
        cache_creation_tokens=cache_creation_tok,
    )
    if not skip_cache and decision != "error":
        _cache_set(cache_key, result, score=parsed_score)
    return result
