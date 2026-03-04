import React, { useEffect, useState } from 'react';

import { Badge, Button, Panel, Select, Sheet, Spinner } from '../../shared/ui/TaaliPrimitives';
import { formatScale100Score } from '../../lib/scoreDisplay';
import {
  buildStandingCandidateReportModel,
  COMPLETED_ASSESSMENT_STATUSES,
} from './assessmentViewModels';
import { CandidateSidebarHeader } from './CandidateSidebarHeader';
import { formatDateTime } from './candidatesUiUtils';
import { CandidateReportView } from './CandidateReportView';

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
  onViewFullPage,
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
  const hasCv = Boolean(application?.cv_filename || application?.cv_text);
  const reportModel = buildStandingCandidateReportModel({
    application,
    completedAssessment,
    identity: {
      assessmentId: completedAssessment?.id || scoreSummary.assessment_id || application?.valid_assessment_id || null,
      sectionLabel: 'Standing candidate report',
      name: application?.candidate_name || application?.candidate_email || 'Candidate',
      email: application?.candidate_email || '',
      position: application?.candidate_position || '',
      roleName: application?.role_name || '',
      applicationStatus: application?.status || '',
      taskName: completedAssessment?.task_name || completedAssessment?.task?.name || '',
      completedLabel: completedAssessment?.completed_at ? formatDateTime(completedAssessment.completed_at) : '',
    },
  });
  const hasCompletedAssessment = reportModel.hasCompletedAssessment;
  const resolvedAssessmentId = completedAssessment?.id || scoreSummary.assessment_id || application?.valid_assessment_id || null;
  const hasValidAssessment = Boolean(application?.valid_assessment_id || resolvedAssessmentId);

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
        <Button
          type="button"
          variant="secondary"
          onClick={() => onViewFullPage?.(application, resolvedAssessmentId)}
        >
          View full page
        </Button>
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
        <Panel className="p-4 text-sm text-[var(--taali-muted)]">
          Candidate summary unavailable.
        </Panel>
      ) : (
        <div className="space-y-4">
          {completedAssessmentLoading ? (
            <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
              <Spinner size={16} />
              Refreshing completed assessment detail...
            </div>
          ) : null}

          <CandidateReportView model={reportModel} variant="sheet" />

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
                    <div key={item.assessment_id} className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-3">
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
