"""Pre-screen sub-agent.

Wraps ``cv_matching/runner_pre_screen.run_pre_screen``. The runner has
its own SHA256-keyed cache (via ``cv_score_cache``) so calling it on
every cycle is cheap when nothing has changed.

Fast-path: if ``CandidateApplication.pre_screen_score_100`` is already
populated, return it directly without a Claude call. The orchestrator
can pass ``skip_cache=True`` to force a recompute.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..cv_matching.runner_pre_screen import run_pre_screen
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..services.fraud_detection import (
    apply_fraud_penalty,
    build_fraud_signals_payload,
    detect_cv_copy_paste,
)
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.pre_screen")


def _resolve_jd_text(role: Role) -> str:
    """Best-effort job description text — same fields the existing
    scoring orchestrator uses, in priority order."""
    return (
        (role.job_spec_text or "")
        or (role.description or "")
        or (role.additional_requirements or "")
        or ""
    )


def _build_db(injected: Session | None) -> tuple[Session, bool]:
    """Tests pass an injected session; production opens its own.
    Returns (session, owns_session) so we know whether to close.
    """
    if injected is not None:
        return injected, False
    return SessionLocal(), True


class PreScreenSubAgent:
    name = "pre_screen"

    def run(
        self, req: SubAgentRequest, *, db: Session | None = None
    ) -> SubAgentResult:
        session, owns = _build_db(db)
        try:
            return self._run(req, session)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("pre_screen sub-agent crashed")
            return SubAgentResult(
                sub_agent=self.name, ok=False, error=f"unexpected: {exc}"
            )
        finally:
            if owns:
                session.close()

    def _run(self, req: SubAgentRequest, db: Session) -> SubAgentResult:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == req.application_id,
                CandidateApplication.organization_id == req.organization_id,
            )
            .one_or_none()
        )
        if app is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"application {req.application_id} not found in org {req.organization_id}",
            )

        # Fast path: cached pre-screen score on the application.
        if not req.skip_cache and app.pre_screen_score_100 is not None:
            score = float(app.pre_screen_score_100)
            decision = (
                "yes" if score >= 50.0 else "no"
            )  # mirrors runner_pre_screen v2.0 cutoff
            return SubAgentResult(
                sub_agent=self.name,
                ok=True,
                output={
                    "score": score,
                    "decision": decision,
                    "reason": app.pre_screen_recommendation or "",
                },
                confidence=1.0,
                cache_hit=True,
            )

        role = (
            db.query(Role)
            .filter(Role.id == req.role_id, Role.organization_id == req.organization_id)
            .one_or_none()
        )
        if role is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"role {req.role_id} not found",
            )

        cv_text = (app.cv_text or "").strip()
        jd_text = _resolve_jd_text(role).strip()
        if not cv_text or not jd_text:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="missing cv_text or jd_text",
            )

        # The runner is internally cached (compute_pre_screen_cache_key)
        # so calling here is cheap on a hit. ``skip_cache=True``
        # invalidates that path explicitly.
        result = run_pre_screen(cv_text, jd_text, skip_cache=req.skip_cache)
        if result.decision == "error":
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=result.reason or "pre_screen runner failed",
                tokens_used=int((result.input_tokens or 0) + (result.output_tokens or 0)),
            )

        raw_score = (
            float(result.score)
            if result.score is not None
            else _decision_to_score(result.decision)
        )
        # Deterministic fraud check is part of the pre-screen agent — a CV
        # that copy-pasted the JD is capped below the gate so the downstream
        # decision policy filters it out without spending v3 tokens.
        fraud = detect_cv_copy_paste(
            cv_text,
            jd_text,
            threshold=settings.FRAUD_COPY_PASTE_THRESHOLD,
        )
        score, fraud_capped = apply_fraud_penalty(
            raw_score,
            fraud,
            cap_score=settings.FRAUD_PENALTY_CAP_SCORE,
        )
        if fraud_capped:
            decision = "no"
            reason = (
                f"CV contains {fraud.score:.0%} text copied verbatim from the "
                f"job description (threshold {fraud.threshold:.0%})."
            )
        else:
            decision = result.decision
            reason = result.reason
        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output={
                "score": score,
                "decision": decision,
                "reason": reason,
                "fraud_signals": build_fraud_signals_payload(fraud),
                "fraud_capped": fraud_capped,
                "llm_score_100": raw_score,
            },
            confidence=1.0 if result.cache_hit else 0.9,
            cache_hit=result.cache_hit,
            tokens_used=int(
                (result.input_tokens or 0) + (result.output_tokens or 0)
            ),
        )


def _decision_to_score(decision: str) -> float:
    return {"yes": 75.0, "maybe": 50.0, "no": 25.0}.get(decision, 50.0)


# Module-level singleton + registration on import.
PRE_SCREEN_SUB_AGENT: SubAgent = PreScreenSubAgent()
register_sub_agent(PRE_SCREEN_SUB_AGENT)


__all__ = ["PRE_SCREEN_SUB_AGENT", "PreScreenSubAgent"]
