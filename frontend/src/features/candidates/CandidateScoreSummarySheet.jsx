import React, { useEffect, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';

import { Badge, Button, Panel, Select, Sheet, Spinner } from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import { CandidateSidebarHeader } from './CandidateSidebarHeader';
import { formatDateTime } from './candidatesUiUtils';

function CandidateInterviewKitSection({ kit }) {
  if (!kit) return null;
  const knockouts = Array.isArray(kit.knockout_checks) ? kit.knockout_checks : [];
  const probes = Array.isArray(kit.priority_probes) ? kit.priority_probes : [];
  if (knockouts.length === 0 && probes.length === 0) return null;

  return (
    <Panel className="space-y-3 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[var(--taali-text)]">Candidate-specific interview guidance</p>
          <p className="text-[11px] text-[var(--taali-muted)]">Derived from CV scoring evidence — no extra Claude call.</p>
        </div>
        <Badge variant="muted" className="font-mono text-[11px]">
          {kit.summary?.total_criteria ?? 0} criteria
        </Badge>
      </div>

      {knockouts.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--taali-danger)]">
            Knockout checks ({knockouts.length})
          </p>
          {knockouts.map((item) => (
            <KitItemCard key={`knockout-${item.criterion_id}`} item={item} tone="danger" />
          ))}
        </div>
      ) : null}

      {probes.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--taali-warning)]">
            Priority probes ({probes.length})
          </p>
          {probes.map((item) => (
            <KitItemCard key={`probe-${item.criterion_id}`} item={item} tone="warning" />
          ))}
        </div>
      ) : null}
    </Panel>
  );
}

function KitItemCard({ item, tone }) {
  const borderClass = tone === 'danger'
    ? 'border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)]'
    : 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)]';
  return (
    <div className={`rounded border ${borderClass} px-3 py-2`}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-[var(--taali-text)]">{item.criterion_text}</p>
        <Badge variant="muted" className="font-mono text-[10px]">
          {item.status}
          {typeof item.confidence === 'number' ? ` · ${Math.round(item.confidence * 100)}%` : ''}
        </Badge>
      </div>
      {item.interview_probe ? (
        <p className="mt-1 text-xs text-[var(--taali-text)]">
          <span className="font-semibold">Ask:</span> {item.interview_probe}
        </p>
      ) : null}
      {item.cv_quote ? (
        <p className="mt-1 text-[11px] italic text-[var(--taali-muted)]">
          “{item.cv_quote}”
        </p>
      ) : null}
    </div>
  );
}

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
  onRefreshInterviewGuidance,
  refreshingInterviewGuidance = false,
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
        {onRefreshInterviewGuidance ? (
          <Button
            type="button"
            variant="secondary"
            disabled={refreshingInterviewGuidance}
            onClick={() => onRefreshInterviewGuidance(application)}
            title="Re-derive interview kit + screening pack from current scoring data (no Claude call)"
          >
            {refreshingInterviewGuidance ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
            <span className="ml-1">{refreshingInterviewGuidance ? 'Refreshing' : 'Refresh interview guidance'}</span>
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

          <CandidateInterviewKitSection kit={application?.candidate_interview_kit} />

          <CandidateAssessmentSummaryView reportModel={reportModel} variant="sheet" />
        </div>
      )}
    </Sheet>
  );
}
