// PUBLIC, auth-free PREVIEW of the real candidate standing report with the
// Motion library applied. It is NOT a new design: it composes the EXACT
// production leaf components — DecisionRail, AssessmentScorecard and
// CandidateReportView — laid out the way CandidateStandingReportPage lays them
// (the dossier: sticky DecisionRail on the left, the report body on the right),
// fed by the SAME AI_SHOWCASE fixtures via the SAME real view-model builder
// (buildStandingCandidateReportModel). The API-coupled panels (ScorecardPanel,
// the Evaluate/Notes self-fetching panes) are intentionally skipped.
//
// Motion is the signature here: the DecisionRail slides in, the 5-Ds scorecard
// bars fill from 0 and its rows stagger in on mount (scoped CSS keyframes so the
// real AssessmentScorecard is untouched), and the evidence report reveals on
// scroll. Everything respects prefers-reduced-motion via
// the shared MotionSystemProvider + the reduced flag.

import React, { useMemo } from 'react';
import { MotionSystemProvider, Reveal, useReducedMotionSync } from '../../shared/motion';

import { BreadcrumbsRow } from '../../shared/ui/Breadcrumbs';
import { useToast } from '../../context/ToastContext';
import { DecisionRail } from './DecisionRail';
import { AssessmentScorecard } from './AssessmentScorecard';
import { CandidateReportView } from './CandidateReportView';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import {
  AI_SHOWCASE_APPLICATION,
  AI_SHOWCASE_AGENT_DECISION,
  AI_SHOWCASE_COMPLETED_ASSESSMENT,
} from '../demo/productWalkthroughModels';
import {
  PreviewSwitcher,
} from '../../shared/motion/previewMotion';
import './ReportMotionPreview.css';

export const ReportMotionPreview = () => {
  const reduced = useReducedMotionSync();
  const { showToast } = useToast() || { showToast: () => {} };

  const application = AI_SHOWCASE_APPLICATION;
  const completedAssessment = AI_SHOWCASE_COMPLETED_ASSESSMENT;
  const decision = AI_SHOWCASE_AGENT_DECISION;

  // The REAL report view-model builder — same call CandidateStandingReportPage
  // makes, so CandidateReportView renders identical structure/evidence.
  const reportModel = useMemo(() => buildStandingCandidateReportModel({
    application,
    completedAssessment,
    identity: {
      assessmentId: completedAssessment?.id,
      sectionLabel: 'Standing report',
      name: application.candidate_name,
      email: application.candidate_email,
      position: application.candidate_position,
      roleName: application.role_name,
      applicationStatus: application.application_outcome,
    },
  }), [application, completedAssessment]);

  const candidateLabel = application.candidate_name;
  const candidateInitials = candidateLabel.split(/\s+/).filter(Boolean).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  const metaParts = [application.candidate_email, application.candidate_location, application.role_name].filter(Boolean);
  const flagCount = (reportModel?.roleFitModel?.claimsToVerify?.length || 0)
    + (reportModel?.roleFitModel?.integrityFlags?.length || 0);

  // Preview decision handlers — surface a toast (no backend). The real page
  // opens OverrideModal / TeachModal here; the preview just acknowledges.
  const toast = (msg) => showToast(msg, 'info');

  return (
    <MotionSystemProvider>
        <div data-brand="taali" className="rmp-root">
          <BreadcrumbsRow items={[
            { label: 'Jobs' },
            { label: application.role_name },
            { label: candidateLabel },
          ]} />

          <div className="page">
            {/* .rmp-dossier scopes the rail slide-in keyframe to the real
                .dossier-rail aside (disabled under reduced motion in CSS). */}
            <div className="dossier rmp-dossier">
              <DecisionRail
                candidateName={candidateLabel}
                candidateInitials={candidateInitials}
                candidateMeta={metaParts}
                taaliScore={reportModel?.summaryModel?.taaliScore}
                roleFitScore={reportModel?.summaryModel?.roleFitScore}
                assessmentScore={reportModel?.summaryModel?.assessmentScore}
                reqMet={reportModel?.roleFitModel?.requirementsMet ?? 0}
                reqTotal={reportModel?.roleFitModel?.requirementsTotal ?? 0}
                experienceLabel={reportModel?.candidateSnapshot?.yearsLabel || ''}
                decision={decision}
                application={application}
                flagCount={flagCount}
                provenance={application?.score_summary?.score_provenance}
                canDecide
                onApprove={() => toast('Approved — in the live product this writes back to Workable.')}
                onAlternative={(d, alt) => toast(`Overridden — ${alt?.label || 'alternative'}. Your call becomes the agent's training signal.`)}
                onTeach={() => toast('Sent back with feedback — the agent re-evaluates with your correction.')}
                onSnooze={() => toast('Snoozed 1h — it drops back into your queue later.')}
                onReEvaluate={() => toast('Re-evaluating with fresh inputs…')}
              />

              <main className="dossier-main">
                {/* (1) The signature 5-Ds scorecard — the REAL AssessmentScorecard.
                    The .rmp-scorecard wrapper scopes the bar-fill + row-stagger
                    keyframes so the component itself stays untouched. */}
                <Reveal delay={0.08} className="rmp-scorecard" reduced={reduced}>
                  <AssessmentScorecard assessment={completedAssessment} />
                </Reveal>

                {/* (2) The evidence report — the REAL CandidateReportView on the
                    real view-model. Reveals on scroll. */}
                <Reveal reduced={reduced} className="rmp-report" y={24}>
                  <CandidateReportView
                    model={reportModel}
                    variant="page"
                    showRoleFitSection
                    showIntegritySection
                    showEvidenceSections
                  />
                </Reveal>
              </main>
            </div>
          </div>

          <PreviewSwitcher current="report" badge="PREVIEW · Report on Motion" />
        </div>
    </MotionSystemProvider>
  );
};

export default ReportMotionPreview;
