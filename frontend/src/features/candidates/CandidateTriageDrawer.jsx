import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { Button } from '../../shared/ui/TaaliPrimitives';
import { CandidateAuditTimeline } from './CandidateAuditTimeline';
import {
  CandidateAvatar,
  WorkableScorePip,
} from '../../shared/ui/RecruiterDesignPrimitives';

// Pipeline stages — exported because tests and parents still import the
// list. The drawer itself no longer renders a segmented control for
// these (stage transitions happen automatically via Send assessment /
// Reject / Move-to-Workable), but keeping the export avoids ripple-out
// breakage in callers that read it.
export const TRIAGE_STAGE_OPTIONS = [
  { value: 'applied', label: 'Applied' },
  { value: 'invited', label: 'Invited' },
  { value: 'in_assessment', label: 'Assessment' },
  { value: 'review', label: 'Review' },
];

const formatWorkableStageOption = (stage) => {
  // Workable's /stages payloads come in two shapes: ``{slug, kind, name}``
  // from the integration sync and ``{id, name}`` from a plain list call.
  // Previously this only checked ``slug || kind``, so the second shape
  // produced ``value: ""`` and the "Send to Workable" select couldn't
  // resolve a non-empty selection — the hand-back stayed disabled.
  // Fall through to ``id`` so either shape yields a stable identifier.
  const value = stage?.slug || stage?.kind || stage?.id || '';
  const name = stage?.name || stage?.kind || stage?.slug || (stage?.id != null ? String(stage.id) : 'Stage');
  return { value: String(value), label: String(name) };
};

export const candidateReportHref = (application, fromRoleId = null) => {
  if (!application?.id) return '/jobs';
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

const REJECT_VALUE = '__reject__';

export function CandidateTriageDrawer({
  application,
  roleTasks = [],
  mode = 'inline',
  activityLabel = '',
  loadingActivity = false,
  // eslint-disable-next-line no-unused-vars -- kept for API parity; the
  // segmented control was retired in favour of automatic stage
  // transitions driven by the action buttons.
  stageBusy = false,
  assessmentBusy = false,
  rejectBusy = false,
  workableStages = [],
  loadingWorkableStages = false,
  workableMoveBusy = false,
  onClose = null,
  // eslint-disable-next-line no-unused-vars -- kept for API parity.
  onMoveStage,
  onSendAssessment,
  onViewFullReport,
  onReject,
  onMoveToWorkableStage,
}) {
  // Default to the "move forward" tab — recruiters open the drawer on a
  // candidate who already has a score most of the time, so picking the
  // next pipeline step is the dominant action.
  const [activeTab, setActiveTab] = useState('move');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  // ``selectedMoveAction`` is either a Workable stage slug or
  // ``REJECT_VALUE``. One picker, one confirm button.
  const [selectedMoveAction, setSelectedMoveAction] = useState('');
  const [showDetails, setShowDetails] = useState(false);
  const containerRef = useRef(null);

  const applicationId = application?.id || null;
  const assessmentId = useMemo(() => resolveAssessmentId(application), [application]);
  const candidateName = formatCandidateTitle(application);
  const roleLabel = application?.role_name || application?.candidate_position || 'Role';
  const currentStage = String(application?.pipeline_stage || 'applied').toLowerCase();
  const sourceLabel = application?.workable_sourced || application?.workable_candidate_id
    ? 'Imported from Workable'
    : 'Added in Taali';
  const canAct = application?.application_outcome === 'open';
  const hasWorkableLink = Boolean(application?.workable_candidate_id);
  const showMoveToWorkable = hasWorkableLink && Boolean(onMoveToWorkableStage);
  const workableStageOptions = useMemo(
    () => (Array.isArray(workableStages) ? workableStages.map(formatWorkableStageOption) : []),
    [workableStages],
  );
  const currentWorkableStage = String(application?.workable_stage || '').toLowerCase();

  // Reset selections whenever a different candidate's drawer opens.
  useEffect(() => {
    setActiveTab('move');
    setSelectedMoveAction('');
    setShowDetails(false);
    if (roleTasks.length === 1) {
      setSelectedTaskId(String(roleTasks[0].id));
    } else {
      setSelectedTaskId((current) => (
        roleTasks.some((task) => String(task.id) === String(current)) ? current : ''
      ));
    }
  }, [applicationId, roleTasks]);

  // Drop the stage selection if the underlying stage list changes (rare,
  // but happens after the role's Workable shortcode is fetched).
  useEffect(() => {
    setSelectedMoveAction((current) => {
      if (!current || current === REJECT_VALUE) return current;
      return workableStageOptions.some((stage) => stage.value === current) ? current : '';
    });
  }, [applicationId, workableStageOptions]);

  // Bring the drawer into view whenever the open application changes.
  // ``block: 'nearest'`` only scrolls if the drawer is currently off-
  // screen — if it's already visible (e.g. clicking a row that's mid-
  // page), no scroll fires, so the candidate's own row stays where
  // it was. ``scrollIntoView`` is not implemented in jsdom; guard for
  // tests.
  useEffect(() => {
    if (!applicationId || !containerRef.current) return;
    if (typeof containerRef.current.scrollIntoView !== 'function') return;
    containerRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [applicationId]);

  if (!application) return null;

  const reportHref = candidateReportHref(application);
  const sendLabel = assessmentId ? 'Send retake' : 'Send invite';
  const isRejectSelected = selectedMoveAction === REJECT_VALUE;
  const moveBusy = isRejectSelected ? rejectBusy : workableMoveBusy;

  const handleReportClick = (event) => {
    if (stopPlainNavigation(event)) return;
    event.preventDefault();
    onViewFullReport?.(application);
  };

  const handleConfirmMove = () => {
    if (!selectedMoveAction || moveBusy) return;
    if (isRejectSelected) {
      onReject?.(application);
      return;
    }
    onMoveToWorkableStage?.(application, selectedMoveAction);
  };

  const moveButtonLabel = (() => {
    if (moveBusy) return isRejectSelected ? 'Rejecting…' : 'Sending…';
    if (isRejectSelected) return 'Reject candidate';
    if (selectedMoveAction) {
      const picked = workableStageOptions.find((s) => s.value === selectedMoveAction);
      return picked ? `Send to Workable: ${picked.label}` : 'Send to Workable';
    }
    return 'Pick an option';
  })();

  return (
    <div ref={containerRef} className={`candidate-triage candidate-triage-${mode} ctc`}>
      {onClose ? (
        <button
          type="button"
          className="ctc-close"
          onClick={onClose}
          aria-label="Close candidate drawer"
        >
          <X size={16} />
        </button>
      ) : null}

      <div className="ctc-head">
        <CandidateAvatar
          name={candidateName}
          imageUrl={application.candidate_image_url}
          size={32}
        />
        <div className="ctc-head-text">
          <div className="ctc-name">{candidateName}</div>
          <div className="ctc-meta">
            <span className="ctc-meta-role">{roleLabel}</span>
            <span className="ctc-meta-dot" />
            <span className="ctc-meta-stage">
              Stage <span className="ctc-stage-chip">{currentStage}</span>
            </span>
            {currentWorkableStage ? (
              <>
                <span className="ctc-meta-dot" />
                <span className="ctc-meta-workable">
                  Workable <span className="ctc-stage-chip ctc-stage-chip-workable">
                    {currentWorkableStage}
                  </span>
                </span>
              </>
            ) : null}
          </div>
        </div>
        <button
          type="button"
          className="ctc-toggle-link"
          onClick={() => setShowDetails((prev) => !prev)}
        >
          {showDetails ? 'Hide details' : 'Show details'}
        </button>
      </div>

      {showDetails ? (
        <div className="ctc-details">
          <div className="ctc-scores">
            <span>Pre-screen <strong>{formatScore(resolvePreScreenScore(application))}</strong></span>
            <span>Taali <strong>{formatScore(resolveTaaliScore(application))}</strong></span>
            <span>
              Workable{' '}
              {application.workable_score_raw != null ? (
                <WorkableScorePip value={application.workable_score_raw} />
              ) : (
                <strong>—</strong>
              )}
            </span>
            <span className="ctc-grow" />
            <span className="ctc-meta-faint">{application?.candidate_email || 'No email captured'}</span>
          </div>
          {applicationId ? (
            <div className="ctc-timeline">
              <CandidateAuditTimeline applicationId={applicationId} />
            </div>
          ) : null}
        </div>
      ) : null}

      {!canAct ? (
        <div className="ctc-closed-banner">
          <span>
            Application <strong>{application?.application_outcome || 'closed'}</strong>
            {(() => {
              // Workable status copy must match the actual outcome — a
              // hired or withdrawn candidate isn't "disqualified in
              // Workable", they're moved to a hired stage / dropped.
              if (!application?.workable_candidate_id) return null;
              const outcome = application?.application_outcome;
              if (outcome === 'rejected') return ' · disqualified in Workable';
              if (outcome === 'hired') return ' · moved to hired in Workable';
              if (outcome === 'withdrawn') return ' · withdrawn in Workable';
              return null;
            })()}
            . No further actions can be taken.
          </span>
        </div>
      ) : null}

      <div className="ctc-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'move'}
          className={activeTab === 'move' ? 'on' : ''}
          onClick={() => setActiveTab('move')}
        >
          Move forward
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'send'}
          className={activeTab === 'send' ? 'on' : ''}
          onClick={() => setActiveTab('send')}
        >
          Send assessment
        </button>
      </div>

      {activeTab === 'send' ? (
        <div className="ctc-tab-pane" role="tabpanel">
          <div className="ctc-cards">
            {roleTasks.length === 0 ? (
              <div className="ctc-empty">No tasks linked to this role yet.</div>
            ) : (
              roleTasks.map((task) => {
                const isOn = String(selectedTaskId) === String(task.id);
                return (
                  <button
                    key={task.id}
                    type="button"
                    className={`ctc-card ${isOn ? 'on' : ''}`}
                    disabled={!canAct}
                    onClick={() => setSelectedTaskId(String(task.id))}
                  >
                    <div className="ctc-card-title">{task.name}</div>
                    <div className="ctc-card-sub">~60 min · in-browser IDE</div>
                  </button>
                );
              })
            )}
          </div>
          <div className="ctc-action-row">
            <a
              className="ctc-link"
              href={reportHref}
              onClick={handleReportClick}
            >
              View full report →
            </a>
            <span className="ctc-grow" />
            <Button
              type="button"
              variant="primary"
              size="sm"
              disabled={!canAct || !selectedTaskId || assessmentBusy}
              onClick={() => onSendAssessment?.(application, selectedTaskId)}
            >
              {assessmentBusy ? <Loader2 size={14} className="animate-spin" /> : null}
              {assessmentBusy ? 'Sending…' : sendLabel}
            </Button>
          </div>
        </div>
      ) : (
        <div className="ctc-tab-pane" role="tabpanel">
          <div className="ctc-cards">
            {showMoveToWorkable ? (
              loadingWorkableStages ? (
                <div className="ctc-empty">Loading Workable stages…</div>
              ) : workableStageOptions.length === 0 ? (
                <div className="ctc-empty">No Workable stages found for this role.</div>
              ) : (
                workableStageOptions.map((stage) => {
                  const isCurrent = stage.value === currentWorkableStage;
                  const isOn = selectedMoveAction === stage.value;
                  return (
                    <button
                      key={stage.value}
                      type="button"
                      className={`ctc-card ${isOn ? 'on' : ''}`}
                      disabled={!canAct || isCurrent}
                      onClick={() => setSelectedMoveAction(stage.value)}
                    >
                      <div className="ctc-card-title">{stage.label}</div>
                      {isCurrent ? <div className="ctc-card-sub">Current stage</div> : null}
                    </button>
                  );
                })
              )
            ) : null}
            {/* Reject is the only "move out of pipeline" option that
                always appears, even for non-Workable candidates. Visually
                differentiated by a deeper plum tint per the platform's
                "purple variations, not red/amber/green" convention. */}
            <button
              type="button"
              className={`ctc-card ctc-card-reject ${selectedMoveAction === REJECT_VALUE ? 'on' : ''}`}
              disabled={!canAct}
              onClick={() => setSelectedMoveAction(REJECT_VALUE)}
            >
              <div className="ctc-card-title">Reject</div>
              <div className="ctc-card-sub">Closes the application</div>
            </button>
          </div>
          <div className="ctc-action-row">
            <a
              className="ctc-link"
              href={reportHref}
              onClick={handleReportClick}
            >
              View full report →
            </a>
            <span className="ctc-grow" />
            <Button
              type="button"
              variant="primary"
              size="sm"
              className={isRejectSelected ? 'ctc-confirm-reject' : ''}
              disabled={!canAct || !selectedMoveAction || moveBusy}
              onClick={handleConfirmMove}
            >
              {moveBusy ? <Loader2 size={14} className="animate-spin" /> : null}
              {moveButtonLabel}
            </Button>
          </div>
        </div>
      )}

      <div className="ctc-foot">
        <span>{loadingActivity ? 'Loading activity…' : (activityLabel || sourceLabel)}</span>
        <span className="ctc-grow" />
        <span>Esc closes</span>
      </div>
    </div>
  );
}

export default CandidateTriageDrawer;
