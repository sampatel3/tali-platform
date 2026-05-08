"""Assessment-scoring sub-agent.

Read-side wrapper. Assessment scoring (taali_score / assessment_score)
is computed during the assessment-completion flow elsewhere in the
codebase and persisted onto ``CandidateApplication.taali_score_cache_100``
+ ``assessment_score_cache_100``. This sub-agent surfaces those values
to the orchestrator + policy in the uniform shape; it does not
recompute.

If neither cache is populated, returns ``ok=True`` with empty output and
``confidence=0`` so the policy degrades gracefully on cold candidates.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..platform.database import SessionLocal
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.assessment_scoring")


class AssessmentScoringSubAgent:
    name = "assessment_scoring"

    def run(
        self, req: SubAgentRequest, *, db: Session | None = None
    ) -> SubAgentResult:
        session = db or SessionLocal()
        owns = db is None
        try:
            return self._run(req, session)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("assessment_scoring sub-agent crashed")
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
                error=f"application {req.application_id} not found",
            )
        taali = app.taali_score_cache_100
        assessment = app.assessment_score_cache_100

        # ``assessment_completed`` is set when an assessment_score has
        # been computed for this application, regardless of whether the
        # composite TAALI score has been cached yet.
        completed = assessment is not None
        if not completed and taali is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=True,
                output={
                    "taali_score": None,
                    "assessment_score": None,
                    "assessment_completed": False,
                },
                confidence=0.0,
                cache_hit=True,
            )
        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output={
                "taali_score": float(taali) if taali is not None else None,
                "assessment_score": float(assessment) if assessment is not None else None,
                "assessment_completed": completed,
            },
            confidence=1.0,
            cache_hit=True,
        )


ASSESSMENT_SCORING_SUB_AGENT: SubAgent = AssessmentScoringSubAgent()
register_sub_agent(ASSESSMENT_SCORING_SUB_AGENT)


__all__ = ["ASSESSMENT_SCORING_SUB_AGENT", "AssessmentScoringSubAgent"]
