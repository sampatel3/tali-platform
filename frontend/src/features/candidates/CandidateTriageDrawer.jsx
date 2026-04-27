import React, { useEffect, useMemo, useState } from 'react';
import {
  ExternalLink,
  Loader2,
  Send,
  X,
} from 'lucide-react';

import { Button } from '../../shared/ui/TaaliPrimitives';
import {
  CandidateAvatar,
  WorkableScorePip,
} from '../../shared/ui/RecruiterDesignPrimitives';

export const TRIAGE_STAGE_OPTIONS = [
  { value: 'applied', label: 'Applied' },
  { value: 'invited', label: 'Invited' },
  { value: 'in_assessment', label: 'Assessment' },
  { value: 'review', label: 'Review' },
];

export const candidateReportHref = (application, fromRoleId = null) => {
  if (!application?.id) return '/candidates';
  const base = `/candidates/${encodeURIComponent(application.id)}`;
  if (Number.isFinite(Number(fromRoleId))) {
    return `${base}?from=jobs/${encodeURIComponent(fromRoleId)}`;
  }
  return base;
};

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolvePreScreenScore = (application) => (
  application?.pre_screen_score
  ?? application?.cv_match_score
  ?? application?.pre_screen_score_100
  ?? null
);

const resolveTaaliScore = (application) => (
  application?.taali_score
  ?? application?.score_summary?.taali_score
  ?? application?.assessment_score_cache_100
  ?? null
);

const formatScore = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return `${Math.round(numeric)}/100`;
};

const formatCandidateTitle = (application) => (
  application?.candidate_name
  || application?.candidate_email
  || `Candidate #${application?.candidate_id || application?.id || '—'}`
);

const stopPlainNavigation = (event) => (
  event.defaultPrevented
  || event.metaKey
  || event.ctrlKey
  || event.shiftKey
  || event.altKey
  || event.button !== 0
);

export function CandidateTriageDrawer({
  application,
  roleTasks = [],
  mode = 'inline',
  activityLabel = '',
  loadingActivity = false,
  stageBusy = false,
  assessmentBusy = false,
  rejectBusy = false,
  onClose = null,
  onMoveStage,
  onSendAssessment,
  onViewFullReport,
  onReject,
}) {
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [confirmReject, setConfirmReject] = useState(false);

  const applicationId = application?.id || null;
  const assessmentId = useMemo(() => resolveAssessmentId(application), [application]);
  const currentStage = String(application?.pipeline_stage || 'applied').toLowerCase();
  const candidateName = formatCandidateTitle(application);
  const roleLabel = application?.role_name || application?.candidate_position || 'Role';
  const roleMeta = [
    application?.candidate_location,
    application?.candidate_headline,
  ].filter(Boolean).join(' · ');
  const sourceLabel = application?.workable_sourced || application?.workable_candidate_id
    ? 'Imported from Workable'
    : 'Added in Taali';
  const canAct = application?.application_outcome === 'open';

  useEffect(() => {
    setConfirmReject(false);
    if (roleTasks.length === 1) {
      setSelectedTaskId(String(roleTasks[0].id));
      return;
    }
    setSelectedTaskId((current) => (
      roleTasks.some((task) => String(task.id) === String(current)) ? current : ''
    ));
  }, [applicationId, roleTasks]);

  if (!application) return null;

  const reportHref = candidateReportHref(application);
  const sendLabel = assessmentId ? 'Send retake' : 'Send invite';

  const handleReportClick = (event) => {
    if (stopPlainNavigation(event)) return;
    event.preventDefault();
    onViewFullReport?.(application);
  };

  const handleRejectClick = () => {
    if (!confirmReject) {
      setConfirmReject(true);
      return;
    }
    onReject?.(application);
  };

  return (
    <div className={`candidate-triage candidate-triage-${mode}`}>
      <div className="candidate-triage-grid">
        <section className="candidate-triage-identity" aria-label={`${candidateName} triage summary`}>
          <div className="candidate-triage-person">
            <CandidateAvatar
              name={candidateName}
              imageUrl={application.candidate_image_url}
              size={44}
            />
            <div className="min-w-0">
              <div className="candidate-triage-name">
                {candidateName}
              </div>
              <div className="candidate-triage-role">
                {roleLabel}{roleMeta ? ` · ${roleMeta}` : ''}
              </div>
              <div className="candidate-triage-email">
                {application?.candidate_email || 'No email captured'}
              </div>
            </div>
          </div>

          <div className="candidate-triage-scores">
            <div className="candidate-triage-score-card">
              <div className="k">Pre-screen</div>
              <div className="v">{formatScore(resolvePreScreenScore(application))}</div>
            </div>
            <div className="candidate-triage-score-card">
              <div className="k">Taali</div>
              <div className="v">{formatScore(resolveTaaliScore(application))}</div>
            </div>
            <div className="candidate-triage-score-card">
              <div className="k">Workable</div>
              <div className="v">
                {application.workable_score_raw != null ? (
                  <WorkableScorePip value={application.workable_score_raw} />
                ) : (
                  '—'
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="candidate-triage-actions" aria-label={`${candidateName} triage actions`}>
          <div className="candidate-triage-section">
            <div className="candidate-triage-label">Stage</div>
            <div className="candidate-triage-stage-seg" role="group" aria-label="Candidate stage">
              {TRIAGE_STAGE_OPTIONS.map((stage) => (
                <button
                  key={stage.value}
                  type="button"
                  className={stage.value === currentStage ? 'on' : ''}
                  disabled={!canAct || stageBusy || stage.value === currentStage}
                  onClick={() => onMoveStage?.(application, stage.value)}
                >
                  {stage.label}
                </button>
              ))}
            </div>
          </div>

          <div className="candidate-triage-section">
            <div className="candidate-triage-label">Send Taali assessment</div>
            <div className="candidate-triage-row">
              <select
                className="candidate-triage-task-select"
                value={selectedTaskId}
                onChange={(event) => setSelectedTaskId(event.target.value)}
                aria-label="Assessment task"
              >
                <option value="">Select a task...</option>
                {roleTasks.map((task) => (
                  <option key={task.id} value={task.id}>
                    {task.name}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                variant="primary"
                size="sm"
                className="candidate-triage-send"
                disabled={!selectedTaskId || assessmentBusy}
                onClick={() => onSendAssessment?.(application, selectedTaskId)}
              >
                {assessmentBusy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                {assessmentBusy ? 'Sending...' : sendLabel}
              </Button>
            </div>
          </div>

          <div className="candidate-triage-section">
            <div className="candidate-triage-label">Other actions</div>
            <div className="candidate-triage-row">
              <a
                className="btn btn-outline btn-sm candidate-triage-report-link"
                href={reportHref}
                onClick={handleReportClick}
              >
                <ExternalLink size={13} />
                View full report
              </a>
              <span className="candidate-triage-action-spacer" />
              <button
                type="button"
                className={`btn btn-outline btn-sm candidate-triage-reject ${confirmReject ? 'confirm' : ''}`}
                disabled={!canAct || rejectBusy}
                onClick={handleRejectClick}
              >
                {rejectBusy ? 'Rejecting...' : confirmReject ? 'Confirm reject' : 'Reject'}
              </button>
            </div>
          </div>
        </section>

        {onClose ? (
          <button
            type="button"
            className="candidate-triage-close"
            onClick={onClose}
            aria-label="Close candidate drawer"
          >
            <X size={16} />
          </button>
        ) : null}

        <div className="candidate-triage-foot">
          <span>{loadingActivity ? 'Loading activity...' : (activityLabel || 'Last activity not captured')}</span>
          <span className="dot" />
          <span>{sourceLabel}</span>
          <span className="grow" />
          <span>Esc closes</span>
        </div>
      </div>
    </div>
  );
}

export default CandidateTriageDrawer;
