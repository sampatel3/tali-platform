import React from 'react';
import { ArrowLeft } from 'lucide-react';

import { PageContainer, Button, Panel, TabBar, Badge } from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';

const PREVIEW_CANDIDATE = {
  name: 'Maya Chen',
  email: 'maya.chen@example.com',
  position: 'Senior Data Engineer',
  task: 'Revenue Recovery Incident',
  time: '42 min',
  completedDate: 'Mar 3, 2026',
  _raw: {
    id: 1042,
    status: 'completed',
    role_name: 'Senior Data Engineer',
    application_status: 'Shortlisted',
    taali_score: 84.3,
    assessment_score: 88.9,
    final_score: 88.9,
    role_fit_score: 77.4,
    cv_job_match_score: 82.0,
    total_duration_seconds: 42 * 60,
    tests_passed: 12,
    tests_total: 12,
    started_at: '2026-03-03T10:00:00.000Z',
    completed_at: '2026-03-03T10:42:00.000Z',
    score_breakdown: {
      heuristic_summary:
        'TAALI pulls the strongest candidate to the top quickly: assessment execution is strong, role fit is credible, and the follow-up risk is explicit rather than hidden in the notes.',
      category_scores: {
        task_completion: 8.9,
        prompt_clarity: 9.1,
        context_provision: 8.7,
        independence_efficiency: 8.6,
        response_utilization: 8.8,
        debugging_design: 8.4,
        written_communication: 8.9,
        role_fit: 7.7,
      },
      score_components: {
        taali_score: 84.3,
        assessment_score: 88.9,
        role_fit_score: 77.4,
        cv_fit_score: 82.0,
        requirements_fit_score: 74.3,
      },
    },
    cv_job_match_details: {
      score_scale: '0-100',
      role_fit_score_100: 77.4,
      summary: 'Strong fit for a senior data-platform incident role with enough evidence to defend a move-forward recommendation.',
      score_rationale_bullets: [
        'Role fit is credible for senior data-platform incident ownership.',
        'Assessment signal is stronger than fit signal, which improves shortlist confidence.',
        'Probe stakeholder communication around rollback and finance-close risk.',
      ],
      requirements_match_score_100: 74.3,
      requirements_coverage: {
        total: 4,
        met: 3,
        partially_met: 1,
        missing: 0,
      },
      requirements_assessment: [
        {
          requirement: 'Communicate residual operational risk clearly',
          priority: 'must_have',
          status: 'partially_met',
          evidence: 'Verification was strong, but rollback framing can be sharper.',
          impact: 'Good candidate, but this is the main interview probe area.',
        },
      ],
      matching_skills: [
        'AWS Glue incident response',
        'Batch pipeline debugging',
        'Data quality validation',
        'Production handoff judgment',
      ],
      missing_skills: ['Rollback communication could be crisper'],
      experience_highlights: [
        'Clear production debugging approach under time pressure',
      ],
      concerns: ['Probe how the candidate communicates unresolved operational risk to finance stakeholders'],
    },
    prompt_fraud_flags: [],
  },
};

export const CandidateResultsPreviewView = ({
  className = '',
  maxHeightClass = 'max-h-[27rem]',
  scaleClassName = 'scale-[0.8]',
  scaledWidth = '125%',
  showBackButton = false,
  lightMode = false,
}) => {
  const reportModel = buildStandingCandidateReportModel({
    application: null,
    completedAssessment: PREVIEW_CANDIDATE._raw,
    identity: {
      assessmentId: PREVIEW_CANDIDATE._raw.id,
      sectionLabel: 'Assessment results',
      name: PREVIEW_CANDIDATE.name,
      email: PREVIEW_CANDIDATE.email,
      position: PREVIEW_CANDIDATE.position,
      taskName: PREVIEW_CANDIDATE.task,
      roleName: PREVIEW_CANDIDATE._raw.role_name,
      applicationStatus: PREVIEW_CANDIDATE._raw.application_status,
      durationLabel: PREVIEW_CANDIDATE.time,
      completedLabel: PREVIEW_CANDIDATE.completedDate,
    },
  });
  const topTabs = [
    { id: 'summary', label: 'SUMMARY', panelId: 'preview-summary' },
    { id: 'assessment-results', label: 'ASSESSMENT RESULTS', panelId: 'preview-assessment-results' },
    { id: 'role-fit', label: 'ROLE FIT', panelId: 'preview-role-fit' },
    { id: 'interview-guidance', label: 'INTERVIEW GUIDANCE', panelId: 'preview-interview-guidance' },
  ];
  const previewThemeClass = lightMode ? 'taali-preview-scope-light' : 'taali-preview-scope-dark';

  return (
    <div
      className={`overflow-hidden bg-[var(--taali-bg)] ${previewThemeClass} ${className}`}
      style={{ colorScheme: lightMode ? 'light' : 'dark' }}
    >
      <div className={`${maxHeightClass} overflow-hidden`}>
        <div className={`origin-top-left ${scaleClassName}`} style={{ width: scaledWidth }}>
          <div className="min-h-full bg-[var(--taali-bg)] text-[var(--taali-text)]">
            <PageContainer density="compact" width="wide" className="!px-4 !pb-0 !pt-3 md:!px-5">
              {showBackButton ? (
                <Button variant="ghost" size="xs" className="mb-4 font-mono" disabled>
                  <ArrowLeft size={16} /> Back to Assessments
                </Button>
              ) : null}
              <Panel className="mb-4 p-3">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                  <TabBar tabs={topTabs} activeTab="summary" onChange={() => {}} density="compact" />
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={reportModel.source?.badgeVariant || 'muted'} className="font-mono text-[11px]">
                      {reportModel.source?.label || 'Completed assessment'}
                    </Badge>
                    <Badge variant={reportModel.recommendation?.variant || 'muted'} className="font-mono text-[11px]">
                      {reportModel.recommendation?.label || 'Pending review'}
                    </Badge>
                  </div>
                </div>
              </Panel>
              <CandidateAssessmentSummaryView
                reportModel={reportModel}
                variant="preview"
                onOpenInterviewGuidance={() => {}}
                showInterviewGuidanceAction
              />
            </PageContainer>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CandidateResultsPreviewView;
