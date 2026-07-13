import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';

import '../../styles/08-candidate-detail.css';

import { Button, Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  MotionDisclosure,
  MotionTab,
  MotionTabs,
  PresenceSwap,
  motionSafeScrollBehavior,
} from '../../shared/motion';
import { CandidateAuditTimeline } from './CandidateAuditTimeline';
import { AssessmentInviteChip } from './CandidateStatusChips';
import { ScoreProvenance } from './ScoreProvenance';

const _fmtTrackTs = (ts) => {
  if (!ts) return null;
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return null;
  }
};
import {
  CandidateAvatar,
  WorkableScorePip,
} from '../../shared/ui/RecruiterDesignPrimitives';
import { isPostHandoverWorkableStage } from '../../shared/metrics';
import { formatStatusLabel } from './candidatesUiUtils';

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
  // Guard against null/undefined explicitly: Number(null) === 0 is finite,
  // so the old check happily produced "?from=jobs/null", which the report's
  // back-link parser then rejected and fell back to "Back to home".
  if (fromRoleId != null && Number.isFinite(Number(fromRoleId))) {
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
  roleId = null,
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
  // True when the agent is actively running this role. Sending an assessment
  // is then a redundant mirror of what the agent does automatically, so the
  // Send control is demoted to a quiet manual override. Every decisive HITL
  // control (move forward, reject, move-to-Workable) stays as-is.
  agentRunning = false,
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
    } else if (roleTasks.length > 1) {
      // Multiple linked tasks ⇒ an A/B is in play. Default to Auto so an
      // active experiment assigns the arm (50/50, stable per candidate)
      // instead of silently forcing whichever task happens to be first —
      // that default is exactly why role 26's A/B never split.
      setSelectedTaskId('auto');
    } else {
      setSelectedTaskId('');
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
    containerRef.current.scrollIntoView({
      behavior: motionSafeScrollBehavior('smooth'),
      block: 'nearest',
    });
  }, [applicationId]);

  if (!application) return null;

  const reportHref = candidateReportHref(application, roleId);
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
          className="taali-icon-btn taali-icon-btn-ghost taali-icon-btn-sm ctc-close"
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
              Stage <span className="ctc-stage-chip">{formatStatusLabel(currentStage)}</span>
            </span>
            {currentWorkableStage ? (
              <>
                <span className="ctc-meta-dot" />
                <span className="ctc-meta-workable">
                  Workable <span className="ctc-stage-chip ctc-stage-chip-workable">
                    {formatStatusLabel(currentWorkableStage)}
                  </span>
                </span>
              </>
            ) : null}
          </div>
        </div>
        <button
          type="button"
          className="taali-text-btn ctc-toggle-link"
          aria-expanded={showDetails}
          aria-controls="candidate-triage-details"
          onClick={() => setShowDetails((prev) => !prev)}
        >
          {showDetails ? 'Hide details' : 'Show details'}
        </button>
      </div>

      <MotionDisclosure open={showDetails} id="candidate-triage-details">
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
          <ScoreProvenance
            provenance={application?.score_summary?.score_provenance}
            density="compact"
          />
          {applicationId ? (
            <div className="ctc-timeline">
              <CandidateAuditTimeline applicationId={applicationId} />
            </div>
          ) : null}
        </div>
      </MotionDisclosure>

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

      <MotionTabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="ctc-tabs"
        aria-label="Candidate actions"
      >
        <MotionTab
          value="move"
          id="candidate-action-tab-move"
          aria-controls="candidate-action-panel-move"
          className={activeTab === 'move' ? 'on' : ''}
          indicatorClassName="ctc-tab-motion-indicator"
        >
          <span>Move forward</span>
        </MotionTab>
        <MotionTab
          value="send"
          id="candidate-action-tab-send"
          aria-controls="candidate-action-panel-send"
          className={activeTab === 'send' ? 'on' : ''}
          indicatorClassName="ctc-tab-motion-indicator"
        >
          <span>Send assessment</span>
        </MotionTab>
      </MotionTabs>

      <PresenceSwap presenceKey={activeTab}>
      {activeTab === 'send' ? (
        <div
          id="candidate-action-panel-send"
          className="ctc-tab-pane"
          role="tabpanel"
          aria-labelledby="candidate-action-tab-send"
        >
          {application?.score_summary?.invite_tracking?.invite_sent_at ? (
            <div className="ctc-invite-track">
              <div className="ctc-invite-track-head">
                <span className="ctc-invite-track-title">Invite tracking</span>
                <AssessmentInviteChip
                  status={application?.score_summary?.assessment_status}
                  tracking={application?.score_summary?.invite_tracking}
                />
              </div>
              <ul className="ctc-invite-track-list">
                {(() => {
                  const t = application.score_summary.invite_tracking;
                  const rows = [
                    ['Invited', t.invite_sent_at, false],
                    ['Delivered', t.delivered_at, false],
                    ['Email opened', t.opened_at, false],
                    ['Bounced', t.bounced_at, true],
                    ['Started', t.started_at, false],
                    ['Expires', t.expires_at, false],
                  ];
                  return rows
                    .filter(([, ts]) => ts)
                    .map(([label, ts, danger]) => (
                      <li key={label} className={danger ? 'is-danger' : ''}>
                        <span>{label}</span>
                        <span>{_fmtTrackTs(ts)}</span>
                      </li>
                    ));
                })()}
              </ul>
              {(() => {
                const es = (application.score_summary.invite_tracking.email_status || '').toLowerCase();
                if (es === 'failed') {
                  return (
                    <div className="ctc-invite-track-note is-danger">
                      Invite could not be sent — resend it so the candidate receives the assessment.
                    </div>
                  );
                }
                if (!es) {
                  return (
                    <div className="ctc-invite-track-note">
                      No delivery or open events recorded for this invite yet.
                    </div>
                  );
                }
                return null;
              })()}
            </div>
          ) : null}
          <div className="ctc-cards">
            {roleTasks.length === 0 ? (
              <div className="ctc-empty">No tasks linked to this role yet.</div>
            ) : (
              <>
                {roleTasks.length > 1 ? (
                  <button
                    type="button"
                    className={`ctc-card ${selectedTaskId === 'auto' ? 'on' : ''}`}
                    disabled={!canAct}
                    onClick={() => setSelectedTaskId('auto')}
                  >
                    <div className="ctc-card-title">Auto · A/B split</div>
                    <div className="ctc-card-sub">Experiment assigns the task — 50/50, stable per candidate</div>
                  </button>
                ) : null}
                {roleTasks.map((task) => {
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
                })}
              </>
            )}
          </div>
          {agentRunning ? (
            <div className="ctc-agent-note">
              The agent sends assessments automatically for this role. Sending here is a manual override.
            </div>
          ) : null}
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
              variant={agentRunning ? 'secondary' : 'primary'}
              size="sm"
              disabled={!canAct || !selectedTaskId || assessmentBusy}
              onClick={() => onSendAssessment?.(application, selectedTaskId)}
            >
              {assessmentBusy ? <Spinner size={14} className="!text-current" /> : null}
              {assessmentBusy ? 'Sending…' : sendLabel}
            </Button>
          </div>
        </div>
      ) : (
        <div
          id="candidate-action-panel-move"
          className="ctc-tab-pane"
          role="tabpanel"
          aria-labelledby="candidate-action-tab-move"
        >
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
          {/* Reject is always allowed — even for a candidate the recruiter has
              advanced in Workable — but a later-stage reject disqualifies them
              in Workable, so warn clearly first. Advice, not a block. */}
          {isRejectSelected && isPostHandoverWorkableStage(application?.workable_stage) ? (
            <div className="ctc-reject-warning" role="alert">
              <strong>Heads up —</strong> this candidate is in{' '}
              <strong>{formatStatusLabel(application?.workable_stage)}</strong> in Workable
              {application?.workable_candidate_id ? ', so rejecting will disqualify them there' : ''}.
              You can still reject — just make sure that&apos;s intended.
            </div>
          ) : null}
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
              {moveBusy ? <Spinner size={14} className="!text-current" /> : null}
              {moveButtonLabel}
            </Button>
          </div>
        </div>
      )}
      </PresenceSwap>

      <div className="ctc-foot">
        <span>{loadingActivity ? 'Loading activity…' : (activityLabel || sourceLabel)}</span>
        <span className="ctc-grow" />
        <span>Esc closes</span>
      </div>
    </div>
  );
}

export default CandidateTriageDrawer;
