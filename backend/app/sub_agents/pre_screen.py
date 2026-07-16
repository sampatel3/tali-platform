"""Pre-screen sub-agent.

Wraps ``cv_matching/runner_pre_screen.run_pre_screen``. The runner has
its own SHA256-keyed cache (via ``cv_score_cache``) so calling it on
every cycle is cheap when nothing has changed.

Fast-path: if ``CandidateApplication.genuine_pre_screen_score_100`` is already
populated, return it directly without a Claude call. The legacy shared score
can contain a later full-score snapshot and is never used as cache authority.
The orchestrator can pass ``skip_cache=True`` to force a recompute.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..cv_matching.runner_pre_screen import run_pre_screen
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..services.fraud_detection import (
    apply_fraud_penalty,
    apply_unverified_claim_prescreen_penalty,
    build_fraud_signals_payload,
    detect_cv_copy_paste,
)
from ..services.role_requirement_service import (
    build_pre_screen_requirements,
    resolve_role_job_spec,
)
from ..services.workable_context_service import format_workable_context
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.pre_screen")


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
        except Exception:  # pragma: no cover — defensive
            logger.exception("pre_screen sub-agent crashed")
            return SubAgentResult(
                sub_agent=self.name, ok=False, error="pre_screen_failed"
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
                error="application_not_found",
            )

        # A6: resolved applications are frozen. Refuse to run a fresh
        # pre-screen on them — saves Anthropic spend on stragglers.
        from ..domains.assessments_runtime.role_support import is_resolved
        if is_resolved(app):
            logger.info(
                "resolved_app_skipped action=pre_screen application_id=%s "
                "pipeline_stage=%s application_outcome=%s",
                app.id, app.pipeline_stage, app.application_outcome,
            )
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="application_resolved",
            )

        # Fast path: only the durable genuine score is safe to reuse.  The
        # legacy shared column can contain a later full-score snapshot.
        genuine_score = getattr(app, "genuine_pre_screen_score_100", None)
        if not req.skip_cache and genuine_score is not None:
            score = float(genuine_score)
            evidence = (
                app.pre_screen_evidence
                if isinstance(app.pre_screen_evidence, dict)
                else {}
            )
            decision = str(evidence.get("decision") or "").strip().lower()
            if decision not in {"yes", "maybe", "no"}:
                # Legacy genuine rows may predate the decision field.  This is
                # display metadata only; the numeric gate uses ``score``.
                decision = "yes" if score >= float(settings.PRE_SCREEN_THRESHOLD) else "no"
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
                error="role_not_found",
            )

        cv_text = (app.cv_text or "").strip()
        extra = req.extra or {}
        jd_text = resolve_role_job_spec(
            role,
            db=db,
            agent_name="pre_screen",
            role_intent=extra.get("role_intent"),
            exemplars_text=extra.get("exemplars_text"),
        )
        if not cv_text or not jd_text:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="missing_cv_text_or_job_spec",
            )

        # Surface every Workable surface (questionnaire answers,
        # recruiter comments, activity log, structured profile) so hard
        # constraints stated only in Workable (e.g. salary from a
        # LinkedIn apply answer) influence the score. Empty when the
        # candidate has no Workable footprint.
        workable_context = ""
        try:
            workable_context = format_workable_context(
                candidate=getattr(app, "candidate", None),
                application=app,
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "format_workable_context failed for app=%s; proceeding without",
                app.id,
            )

        # The runner is internally cached (compute_pre_screen_cache_key)
        # so calling here is cheap on a hit. ``skip_cache=True``
        # invalidates that path explicitly. Workable context is part of
        # the cache key, so refreshed metadata correctly busts stale
        # scores.
        result = run_pre_screen(
            cv_text,
            jd_text,
            build_pre_screen_requirements(role),
            skip_cache=req.skip_cache,
            workable_context=workable_context or None,
            # Without this the runner falls back to metering={"skip": True}
            # and the agent's pre-screen Anthropic calls never write a
            # usage_event — they were ~$11/day of Haiku showing up only as
            # feature_hint="skip" in claude_call_log. cv_scoring already
            # threads this; pre_screen had silently dropped it.
            metering_context=req.metering_context,
        )
        if result.decision == "error":
            from ..services.cv_score_orchestrator import public_scoring_failure_code

            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=public_scoring_failure_code(result.reason),
                tokens_used=int((result.input_tokens or 0) + (result.output_tokens or 0)),
            )

        raw_score = (
            float(result.score)
            if result.score is not None
            else _decision_to_score(result.decision)
        )
        # Deterministic copy-paste detection is always persisted, but copying
        # role text is not by itself a hiring verdict. The safe default is
        # flag-only; score capping requires an explicit operator policy.
        copy_paste_action = settings.FRAUD_COPY_PASTE_ACTION
        fraud = detect_cv_copy_paste(
            cv_text,
            jd_text,
            threshold=settings.FRAUD_COPY_PASTE_THRESHOLD,
            min_block_words=settings.FRAUD_COPY_PASTE_MIN_BLOCK_WORDS,
        )
        if copy_paste_action == "cap":
            score, fraud_capped = apply_fraud_penalty(
                raw_score,
                fraud,
                cap_score=settings.FRAUD_PENALTY_CAP_SCORE,
            )
        else:
            score, fraud_capped = raw_score, False
        score, unverified_penalised = apply_unverified_claim_prescreen_penalty(
            score,
            bool(getattr(result, "unverified_claim", False)) and not fraud_capped,
            penalty=settings.FRAUD_PRESCREEN_UNVERIFIED_PENALTY,
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
                "fraud_signals": {
                    **build_fraud_signals_payload(
                        fraud,
                        action=copy_paste_action,
                    ),
                    "unverified_claim": {
                        "flagged": bool(getattr(result, "unverified_claim", False)),
                        "penalty_applied": unverified_penalised,
                    },
                },
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
