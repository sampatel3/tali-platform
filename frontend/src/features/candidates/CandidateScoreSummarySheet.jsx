import React, { useEffect, useState } from 'react';

import { Button, Panel, Select, Sheet, Spinner } from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import { CandidateSidebarHeader } from './CandidateSidebarHeader';
import { formatDateTime } from './candidatesUiUtils';

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
  const hasCv = Boolean(application?.cv_filename || application?.cv_text);
  const reportModel = buildStandingCandidateReportModel({
    application,
    completedAssessment,
    identity: {
      assessmentId: completedAssessment?.id || scoreSummary.assessment_id || application?.valid_assessment_id || null,
      sectionLabel: 'Assessment results',
      name: application?.candidate_name || application?.candidate_email || 'Candidate',
      email: application?.candidate_email || '',
      position: application?.candidate_position || '',
      roleName: application?.role_name || '',
      applicationStatus: application?.status || '',
      taskName: completedAssessment?.task_name || completedAssessment?.task?.name || '',
      completedLabel: completedAssessment?.completed_at ? formatDateTime(completedAssessment.completed_at) : '',
    },
  });
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

          <CandidateAssessmentSummaryView reportModel={reportModel} variant="sheet" />
        </div>
      )}
    </Sheet>
  );
}
