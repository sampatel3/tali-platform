import React, { useEffect, useMemo, useState } from 'react';

import { Badge, Button, Panel, Select, Sheet, Spinner } from '../../shared/ui/TaaliPrimitives';
import { formatScale100Score } from '../../lib/scoreDisplay';
import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import {
  buildAssessmentSummaryModel,
  buildRoleFitEvidenceModel,
} from './assessmentViewModels';
import { CandidateSidebarHeader } from './CandidateSidebarHeader';
import { CandidateSidebarScoreHero } from './CandidateSidebarScoreHero';
import { CandidateStatusSnapshot } from './CandidateStatusSnapshot';
import { RoleFitEvidenceSections } from './RoleFitEvidenceSections';
import { formatDateTime } from './candidatesUiUtils';

const COMPLETED_ASSESSMENT_STATUSES = new Set(['completed', 'completed_due_to_timeout']);

const modeMeta = (mode) => {
  if (mode === 'assessment_plus_cv') return { label: 'Assessment + CV', variant: 'purple' };
  if (mode === 'assessment_only_fallback') return { label: 'Assessment only', variant: 'warning' };
  if (mode === 'pending') return { label: 'Pending', variant: 'muted' };
  return { label: 'CV fit only', variant: 'muted' };
};

const InfoCard = ({ label, value }) => (
  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] px-3 py-3">
    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</p>
    <p className="mt-2 text-sm font-semibold text-[var(--taali-text)]">{value}</p>
  </div>
);

const toAssessmentStatusText = (status) => {
  const cleaned = String(status || '').trim();
  if (!cleaned) return 'not started';
  return cleaned.replace(/_/g, ' ');
};

export function CandidateScoreSummarySheet({
  open,
  loading,
  application,
  completedAssessment,
  completedAssessmentLoading = false,
  roleTasks,
  creatingAssessmentId,
  onClose,
  onLaunchAssessment,
  onOpenRetakeDialog,
  onOpenCvSidebar,
  onViewResults,
}) {
  const [selectedTask, setSelectedTask] = useState('');

  useEffect(() => {
    if (!open) return;
    if (roleTasks.length === 1) {
      setSelectedTask(String(roleTasks[0].id));
    } else {
      setSelectedTask('');
    }
  }, [open, roleTasks]);

  const scoreSummary = application?.score_summary || {};
  const assessmentHistory = Array.isArray(application?.assessment_history) ? application.assessment_history : [];
  const mode = modeMeta(scoreSummary.mode);
  const hasCompletedAssessment = COMPLETED_ASSESSMENT_STATUSES.has(String(scoreSummary.assessment_status || '').toLowerCase())
    && Boolean(scoreSummary.assessment_id);
  const hasValidAssessment = Boolean(application?.valid_assessment_id);
  const hasCv = Boolean(application?.cv_filename || application?.cv_text);
  const summaryModel = buildAssessmentSummaryModel({ application, completedAssessment });
  const roleFitModel = buildRoleFitEvidenceModel({ application, completedAssessment });

  const currentAssessmentPreview = useMemo(() => {
    if (!scoreSummary.assessment_id || !Object.keys(summaryModel.categoryScores || {}).length) return [];
    return [
      {
        id: scoreSummary.assessment_id,
        name: application?.candidate_name || 'Current assessment',
        _raw: {
          score_breakdown: {
            category_scores: summaryModel.categoryScores || {},
          },
        },
      },
    ];
  }, [application?.candidate_name, scoreSummary.assessment_id, summaryModel.categoryScores]);

  const assessmentValue = hasCompletedAssessment
    ? formatScale100Score(summaryModel.assessmentScore, '0-100')
    : (hasValidAssessment ? 'In progress' : 'Not started');

  const footer = loading ? (
    <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
      <Spinner size={16} />
      Loading candidate summary...
    </div>
  ) : application ? (
    <div className="space-y-3">
      {roleTasks.length > 0 ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={selectedTask}
              onChange={(event) => setSelectedTask(event.target.value)}
              className="min-w-[240px]"
            >
              <option value="">Select task...</option>
              {roleTasks.map((task) => (
                <option key={task.id} value={task.id}>{task.name}</option>
              ))}
            </Select>
            <Button
              type="button"
              variant="primary"
              disabled={!selectedTask || creatingAssessmentId === application.id}
              onClick={() => {
                if (hasValidAssessment) {
                  onOpenRetakeDialog?.(application, selectedTask);
                  return;
                }
                onLaunchAssessment?.(application, selectedTask);
              }}
            >
              {creatingAssessmentId === application.id
                ? (hasValidAssessment ? 'Creating retake...' : 'Sending...')
                : (hasValidAssessment ? 'Retake assessment' : 'Send assessment')}
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-amber-700">Link a task to this role before sending an assessment.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {hasCompletedAssessment ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => onViewResults?.(scoreSummary.assessment_id, application)}
          >
            View full assessment results
          </Button>
        ) : null}
        {hasCv ? (
          <Button type="button" variant="secondary" onClick={() => onOpenCvSidebar?.(application)}>
            View CV
          </Button>
        ) : null}
      </div>
    </div>
  ) : (
    <div className="text-sm text-[var(--taali-muted)]">No candidate selected.</div>
  );

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={application?.candidate_name || application?.candidate_email || 'Candidate summary'}
      description={application?.role_name || application?.candidate_position || 'Role scoring summary'}
      headerContent={<CandidateSidebarHeader application={application} />}
      footer={footer}
    >
      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center">
          <Spinner size={22} />
        </div>
      ) : !application ? (
        <Panel className="p-3.5 text-sm text-[var(--taali-muted)]">
          Candidate summary unavailable.
        </Panel>
      ) : (
        <div className="space-y-4">
          <CandidateSidebarScoreHero
            application={application}
            score={summaryModel.taaliScore}
            scoreDetails={{ score_scale: '0-100' }}
            mode={mode}
            sourceMeta={summaryModel.source}
            caption={summaryModel.source.formulaLabel}
          />

          <CandidateStatusSnapshot application={application} />

          <Panel className="overflow-hidden p-0">
            <div className="border-b border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] px-4 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Assessment score</p>
                  <p className="mt-2 taali-display text-3xl font-semibold text-[var(--taali-text)]">{assessmentValue}</p>
                </div>
                <Badge variant={hasCompletedAssessment ? 'purple' : (hasValidAssessment ? 'warning' : 'muted')}>
                  {hasCompletedAssessment ? 'Completed' : (hasValidAssessment ? 'Active attempt' : 'Awaiting assessment')}
                </Badge>
              </div>
            </div>

            <div className="space-y-4 px-4 py-4">
              {completedAssessmentLoading ? (
                <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
                  <Spinner size={16} />
                  Refreshing completed assessment detail...
                </div>
              ) : null}

              <p className="text-sm leading-6 text-[var(--taali-text)]">{summaryModel.heuristicSummary}</p>

              {hasCompletedAssessment ? (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
                  <div className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <InfoCard label="Strongest dimension" value={summaryModel.strongestLabel} />
                      <InfoCard label="Weakest dimension" value={summaryModel.weakestLabel} />
                    </div>
                    {scoreSummary.mode === 'assessment_only_fallback' ? (
                      <p className="text-sm text-amber-700">
                        CV fit was unavailable on the completed attempt, so TAALI score reflects assessment evidence only.
                      </p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => onViewResults?.(scoreSummary.assessment_id, application)}
                      >
                        View full assessment results
                      </Button>
                    </div>
                  </div>
                  {currentAssessmentPreview.length > 0 ? (
                    <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] p-3">
                      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Assessment chart</p>
                      <ComparisonRadar
                        assessments={currentAssessmentPreview}
                        highlightAssessmentId={scoreSummary.assessment_id}
                        showLegend={false}
                        height={220}
                        className="-mx-1"
                      />
                    </div>
                  ) : null}
                </div>
              ) : (
                <p className="text-sm text-[var(--taali-muted)]">
                  {hasValidAssessment
                    ? `Current assessment is ${toAssessmentStatusText(scoreSummary.assessment_status)}. Until it completes, TAALI continues to reflect application CV fit.`
                    : 'No completed assessment exists yet for this role. TAALI currently reflects application CV fit and recruiter requirements evidence.'}
                </p>
              )}
            </div>
          </Panel>

          <RoleFitEvidenceSections
            model={roleFitModel}
            variant="compact"
            emptyMessage="This candidate does not have role-fit evidence yet."
          />

          <Panel className="p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Assessment history</p>
              {scoreSummary.has_voided_attempts ? <Badge variant="warning">Includes voided attempts</Badge> : null}
            </div>
            {assessmentHistory.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">No assessment attempts yet for this role.</p>
            ) : (
              <div className="space-y-3">
                {assessmentHistory.map((item) => {
                  const canViewItem = Boolean(item.assessment_id) && (
                    COMPLETED_ASSESSMENT_STATUSES.has(String(item.status || '').toLowerCase()) || Boolean(item.is_voided)
                  );
                  return (
                    <div key={item.assessment_id} className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-2.5">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-semibold text-[var(--taali-text)]">{item.task_name || `Assessment #${item.assessment_id}`}</p>
                            {item.is_voided ? <Badge variant="warning">Voided</Badge> : <Badge variant="muted">Current</Badge>}
                          </div>
                          <p className="mt-1 text-sm text-[var(--taali-muted)]">
                            Status: {toAssessmentStatusText(item.status)}
                            {item.completed_at ? ` • Completed ${formatDateTime(item.completed_at)}` : ''}
                            {!item.completed_at && item.created_at ? ` • Created ${formatDateTime(item.created_at)}` : ''}
                          </p>
                          {item.void_reason ? (
                            <p className="mt-1 text-sm text-amber-700">Void reason: {item.void_reason}</p>
                          ) : null}
                        </div>
                        <div className="space-y-2 text-right">
                          <p className="font-mono text-sm text-[var(--taali-text)]">TAALI {formatScale100Score(item.taali_score, '0-100')}</p>
                          {canViewItem ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => onViewResults?.(item.assessment_id, application)}
                            >
                              View
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Panel>
        </div>
      )}
    </Sheet>
  );
}
