"""CV-scoring sub-agent.

Wraps ``cv_matching/runner.run_cv_match``. Fast-path reads the
already-stored ``cv_match_details`` blob off the application; cold-path
calls the runner (which itself caches by SHA256(cv+jd+req+versions)).

Output exposes: role_fit_score, dimension_scores,
requirements_assessment, calibrated_p_advance — the keys the engine
weights and rules reference.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..cv_matching.runner import run_cv_match
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.database import SessionLocal
from ..services.role_requirement_service import (
    build_scoring_requirements,
    resolve_role_job_spec,
)
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.cv_scoring")


def _from_cached_details(app: CandidateApplication) -> dict[str, Any] | None:
    details = app.cv_match_details if isinstance(app.cv_match_details, dict) else None
    if not details:
        return None
    return {
        "role_fit_score": float(
            details.get("role_fit_score") or app.role_fit_score_cache_100 or 0.0
        ),
        "dimension_scores": details.get("dimension_scores") or {},
        "requirements_assessment": details.get("requirements_assessment") or [],
        "calibrated_p_advance": details.get("calibrated_p_advance"),
        "summary": details.get("summary") or "",
    }


class CvScoringSubAgent:
    name = "cv_scoring"

    def run(
        self, req: SubAgentRequest, *, db: Session | None = None
    ) -> SubAgentResult:
        session = db or SessionLocal()
        owns = db is None
        try:
            return self._run(req, session)
        except Exception:  # pragma: no cover — defensive
            logger.exception("cv_scoring sub-agent crashed")
            return SubAgentResult(
                sub_agent=self.name, ok=False, error="cv_scoring_failed"
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

        # A6: resolved applications are frozen. The cached score (if any)
        # from when they were open is still served — that's the snapshot.
        # But we never spend on a fresh score for them.
        from ..domains.assessments_runtime.role_support import is_resolved
        if is_resolved(app):
            cached = _from_cached_details(app) if not req.skip_cache else None
            if cached is not None:
                return SubAgentResult(
                    sub_agent=self.name,
                    ok=True,
                    output=cached,
                    confidence=1.0,
                    cache_hit=True,
                )
            logger.info(
                "resolved_app_skipped action=cv_scoring application_id=%s "
                "pipeline_stage=%s application_outcome=%s",
                app.id, app.pipeline_stage, app.application_outcome,
            )
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="application_resolved",
            )

        if not req.skip_cache:
            cached = _from_cached_details(app)
            if cached is not None:
                return SubAgentResult(
                    sub_agent=self.name,
                    ok=True,
                    output=cached,
                    confidence=1.0,
                    cache_hit=True,
                )

        role = (
            db.query(Role)
            .filter(
                Role.id == req.role_id,
                Role.organization_id == req.organization_id,
            )
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
            agent_name="cv_scoring",
            role_intent=extra.get("role_intent"),
            exemplars_text=extra.get("exemplars_text"),
        )
        if not cv_text or not jd_text:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="missing_cv_text_or_job_spec",
            )

        # Feed the candidate's Workable metadata (questionnaire answers,
        # recruiter comments, activity log) so hard constraints answered
        # outside the CV (e.g. salary expectation on a LinkedIn apply) are
        # scored, not left "unknown" — matching the orchestrator path.
        workable_context = ""
        try:
            from ..services.workable_context_service import format_workable_context

            workable_context = format_workable_context(
                candidate=getattr(app, "candidate", None),
                application=app,
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "format_workable_context failed for application=%s; scoring without it",
                app.id,
            )

        result = run_cv_match(
            cv_text,
            jd_text,
            build_scoring_requirements(role),
            skip_cache=req.skip_cache,
            metering_context=req.metering_context,
            workable_context=workable_context or None,
        )
        if str(result.scoring_status) not in {"OK", "ScoringStatus.OK", "ok"}:
            from ..services.cv_score_orchestrator import public_scoring_failure_code

            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=public_scoring_failure_code(result.error_reason),
                tokens_used=int(
                    (result.input_tokens or 0) + (result.output_tokens or 0)
                ),
            )

        # Project the runner result onto the engine-friendly key set.
        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output={
                "role_fit_score": float(result.role_fit_score or 0.0),
                "dimension_scores": (
                    result.dimension_scores.model_dump()
                    if result.dimension_scores is not None
                    else {}
                ),
                "requirements_assessment": [
                    r.model_dump() for r in (result.requirements_assessment or [])
                ],
                "calibrated_p_advance": result.calibrated_p_advance,
                "summary": result.summary or "",
            },
            confidence=1.0 if result.cache_hit else 0.9,
            cache_hit=bool(result.cache_hit),
            tokens_used=int(
                (result.input_tokens or 0) + (result.output_tokens or 0)
            ),
        )


CV_SCORING_SUB_AGENT: SubAgent = CvScoringSubAgent()
register_sub_agent(CV_SCORING_SUB_AGENT)


__all__ = ["CV_SCORING_SUB_AGENT", "CvScoringSubAgent"]
