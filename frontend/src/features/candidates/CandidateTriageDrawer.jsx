import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';

import '../../styles/08-candidate-detail.css';

import { Button, Spinner, TabBar } from '../../shared/ui/TaaliPrimitives';
import {
  MotionDisclosure,
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
import { formatRoleFamilyReferences } from '../../shared/decisions/decisionActions';
import { isPostHandoverWorkableStage } from '../../shared/metrics';
import { formatStatusLabel } from './candidatesUiUtils';

// Pipeline stages — exported because tests and parents still import the
// list. The drawer itself no longer renders a segmented control for
// these (stage transitions happen automatically via Send assessment /
// Reject / hand-back to the owning ATS), but keeping the export avoids ripple-out
// breakage in callers that read it.
export const TRIAGE_STAGE_OPTIONS = [
  { value: 'applied', label: 'Applied' },
  { value: 'invited', label: 'Invited' },
  { value: 'in_assessment', label: 'Assessment' },
  { value: 'review', label: 'Review' },
];

const formatAtsStageOption = (stage) => {
  // Workable's /stages payloads come in two shapes: ``{slug, kind, name}``
  // from the integration sync and ``{id, name}`` from a plain list call.
  // Previously this only checked ``slug || kind``, so the second shape
  // produced ``value: ""`` and the "Send to Workable" select couldn't
  // resolve a non-empty selection — the hand-back stayed disabled.
  // Fall through to ``id`` so either shape yields a stable identifier.
  const value = stage?.slug || stage?.kind || stage?.id || '';
  const name = stage?.name || stage?.kind || stage?.slug || (stage?.id != null ? String(stage.id) : 'Stage');
  return {
    value: String(value),
    label: String(name),
    kind: String(stage?.kind || stage?.slug || value),
  };
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
const EMPTY_LIST = Object.freeze([]);

export function CandidateTriageDrawer({
  application,
  roleId = null,
  isRelatedRole = false,
  hasRelatedRoles = false,
  roleFamily = null,
  roleTasks = EMPTY_LIST,
  mode = 'inline',
  activityLabel = '',
  loadingActivity = false,
  // eslint-disable-next-line no-unused-vars -- kept for API parity; the
  // segmented control was retired in favour of automatic stage
  // transitions driven by the action buttons.
  stageBusy = false,
  assessmentBusy = false,
  rejectBusy = false,
  atsProvider = null,
  atsStages = null,
  loadingAtsStages = null,
  atsMoveBusy = null,
  // Legacy aliases retained for direct callers while the role workspace uses
  // the provider-neutral props above.
  workableStages = EMPTY_LIST,
  loadingWorkableStages = false,
  workableMoveBusy = false,
  onClose = null,
  // eslint-disable-next-line no-unused-vars -- kept for API parity.
  onMoveStage,
  onSendAssessment,
  onViewFullReport,
  onReject,
  onMoveToAtsStage,
  onMoveToWorkableStage,
  // True when the agent is actively running this role. Sending an assessment
  // is then a redundant mirror of what the agent does automatically, so the
  // Send control is demoted to a quiet manual override. Every decisive HITL
  // control (move forward, reject, ATS hand-back) stays as-is.
  agentRunning = false,
}) {
  // Default to the "move forward" tab — recruiters open the drawer on a
  // candidate who already has a score most of the time, so picking the
  // next pipeline step is the dominant action.
  const [activeTab, setActiveTab] = useState('move');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  // ``selectedMoveAction`` is either an external ATS stage/status or
  // ``REJECT_VALUE``. One picker, one confirm button.
  const [selectedMoveAction, setSelectedMoveAction] = useState('');
  const [showDetails, setShowDetails] = useState(false);
  const containerRef = useRef(null);
  const actionTabsId = useId().replace(/:/g, '');

  const applicationId = application?.id || null;
  const assessmentId = useMemo(() => resolveAssessmentId(application), [application]);
  const candidateName = formatCandidateTitle(application);
  const roleLabel = application?.role_name || application?.candidate_position || 'Role';
  const currentStage = String(application?.pipeline_stage || 'applied').toLowerCase();
  const applicationSource = String(application?.source || '').toLowerCase();
  const hasWorkableLink = Boolean(application?.workable_candidate_id);
  const hasBullhornLink = Boolean(
    application?.bullhorn_job_submission_id
    || application?.external_refs?.bullhorn_job_submission_id,
  );
  const applicationAtsProvider = hasBullhornLink || applicationSource === 'bullhorn'
    ? 'bullhorn'
    : hasWorkableLink || application?.workable_sourced || applicationSource === 'workable'
      ? 'workable'
      : null;
  const resolvedAtsProvider = atsProvider
    || applicationAtsProvider;
  const providerLabel = resolvedAtsProvider === 'bullhorn' ? 'Bullhorn' : 'Workable';
  const linkedRoleReferences = formatRoleFamilyReferences(roleFamily);
  const sourceLabel = applicationAtsProvider
    ? `Imported from ${applicationAtsProvider === 'bullhorn' ? 'Bullhorn' : 'Workable'}`
    : 'Added in Taali';
  const canAct = application?.application_outcome === 'open';
  const hasAtsLink = resolvedAtsProvider === 'workable'
    ? hasWorkableLink
    : resolvedAtsProvider === 'bullhorn'
      ? hasBullhornLink
      : false;
  const moveToAtsStage = onMoveToAtsStage || onMoveToWorkableStage;
  const showMoveToAts = hasAtsLink && Boolean(moveToAtsStage);
  const resolvedAtsStages = Array.isArray(atsStages) ? atsStages : workableStages;
  const atsStageOptions = useMemo(
    () => (Array.isArray(resolvedAtsStages) ? resolvedAtsStages.map(formatAtsStageOption) : []),
    [resolvedAtsStages],
  );
  const currentAtsStage = String(
    resolvedAtsProvider === 'bullhorn'
      ? (application?.external_stage_raw || application?.bullhorn_status || application?.external_stage_normalized || '')
      : (application?.workable_stage || application?.external_stage_raw || ''),
  ).trim();
  const currentAtsStageKey = currentAtsStage.toLowerCase();
  const currentNormalizedAtsStageKey = String(application?.external_stage_normalized || '')
    .trim()
    .toLowerCase();
  const outcomeWriteback = application?.integration_sync_state?.outcome_writeback;
  const outcomeWritebackStatus = String(outcomeWriteback?.status || '').trim().toLowerCase();
  const outcomeWritebackTarget = String(outcomeWriteback?.target_outcome || '').trim().toLowerCase();
  const outcomeWritebackProvider = String(outcomeWriteback?.provider || '').trim().toLowerCase();
  const closedOutcomeAtsCopy = (() => {
    const outcome = String(application?.application_outcome || '').trim().toLowerCase();
    if (!hasAtsLink || outcome !== 'rejected') return null;
    const receiptMatches = outcomeWritebackTarget === outcome
      && (!outcomeWritebackProvider || outcomeWritebackProvider === resolvedAtsProvider);

    // Workable outcome writes are synchronous: the route persists a confirmed
    // receipt with the local close. Older rows become provable after read-sync
    // sets workable_disqualified. Never infer hired/withdrawn provider actions;
    // those outcomes are local-only today.
    if (resolvedAtsProvider === 'workable') {
      return (receiptMatches && outcomeWritebackStatus === 'confirmed')
        || application?.workable_disqualified === true
        ? 'rejected in Workable'
        : null;
    }

    if (resolvedAtsProvider !== 'bullhorn') return null;
    if ((receiptMatches && outcomeWritebackStatus === 'confirmed')
      || currentNormalizedAtsStageKey === 'rejected') {
      return 'rejected in Bullhorn';
    }
    if (receiptMatches && outcomeWritebackStatus === 'queued') {
      return 'Bullhorn rejection queued';
    }
    if (receiptMatches && outcomeWritebackStatus === 'failed') {
      return 'Bullhorn rejection sync failed';
    }
    return null;
  })();
  const isPostHandoverAtsStage = resolvedAtsProvider === 'bullhorn'
    ? currentNormalizedAtsStageKey === 'advanced' || currentStage === 'advanced'
    : isPostHandoverWorkableStage(currentAtsStage);
  const atsStagesLoading = loadingAtsStages == null ? loadingWorkableStages : loadingAtsStages;
  const atsMovementBusy = atsMoveBusy == null ? workableMoveBusy : atsMoveBusy;

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
  // but happens after the provider stage catalogue is fetched).
  useEffect(() => {
    setSelectedMoveAction((current) => {
      if (!current || current === REJECT_VALUE) return current;
      return atsStageOptions.some((stage) => stage.value === current) ? current : '';
    });
  }, [applicationId, atsStageOptions]);

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
  const selectedAtsStage = isRejectSelected
    ? null
    : atsStageOptions.find((stage) => stage.value === selectedMoveAction);
  const isSharedMoveSelected = Boolean(
    selectedAtsStage && (isRelatedRole || hasRelatedRoles),
  );
  const moveBusy = isRejectSelected ? rejectBusy : atsMovementBusy;

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
    moveToAtsStage?.(application, selectedMoveAction, selectedAtsStage?.label || null);
  };

  const moveButtonLabel = (() => {
    if (moveBusy) return isRejectSelected ? 'Rejecting…' : 'Sending…';
    if (isRejectSelected) return 'Reject candidate';
    if (selectedAtsStage) {
      return `Send to ${providerLabel}: ${selectedAtsStage.label}`;
    }
    if (selectedMoveAction) {
      return `Send to ${providerLabel}`;
    }
    return 'Pick an option';
  })();
  const actionTabs = [
    {
      id: 'move',
      label: 'Move forward',
      tabId: `${actionTabsId}-candidate-action-tab-move`,
      panelId: `${actionTabsId}-candidate-action-panel-move`,
    },
    {
      id: 'send',
      label: 'Send assessment',
      tabId: `${actionTabsId}-candidate-action-tab-send`,
      panelId: `${actionTabsId}-candidate-action-panel-send`,
    },
  ];

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
            {currentAtsStage ? (
              <>
                <span className="ctc-meta-dot" />
                <span className="ctc-meta-workable">
                  {providerLabel} <span className={`ctc-stage-chip ${resolvedAtsProvider === 'workable' ? 'ctc-stage-chip-workable' : ''}`.trim()}>
                    {formatStatusLabel(currentAtsStage)}
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
            {resolvedAtsProvider === 'workable' ? (
              <span>
                Workable{' '}
                {application.workable_score_raw != null ? (
                  <WorkableScorePip value={application.workable_score_raw} />
                ) : (
                  <strong>—</strong>
                )}
              </span>
            ) : null}
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
            {closedOutcomeAtsCopy ? ` · ${closedOutcomeAtsCopy}` : null}
            . No further actions can be taken.
          </span>
        </div>
      ) : null}

      <TabBar
        tabs={actionTabs}
        activeTab={activeTab}
        onChange={setActiveTab}
        className="ctc-tabs"
        ariaLabel="Candidate actions"
        density="compact"
      />

      <PresenceSwap presenceKey={activeTab}>
      {activeTab === 'send' ? (
        <div
          id={`${actionTabsId}-candidate-action-panel-send`}
          className="ctc-tab-pane"
          role="tabpanel"
          aria-labelledby={`${actionTabsId}-candidate-action-tab-send`}
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
          id={`${actionTabsId}-candidate-action-panel-move`}
          className="ctc-tab-pane"
          role="tabpanel"
          aria-labelledby={`${actionTabsId}-candidate-action-tab-move`}
        >
          <div className="ctc-cards">
            {showMoveToAts ? (
              atsStagesLoading ? (
                <div className="ctc-empty">Loading {providerLabel} stages…</div>
              ) : atsStageOptions.length === 0 ? (
                <div className="ctc-empty">No mapped {providerLabel} stages found for this role.</div>
              ) : (
                atsStageOptions.map((stage) => {
                  const isCurrent = [stage.value, stage.label, stage.kind]
                    .map((value) => String(value || '').trim().toLowerCase())
                    .some((value) => value && (
                      value === currentAtsStageKey
                      || value === currentNormalizedAtsStageKey
                    ));
                  const isOn = selectedMoveAction === stage.value;
                  return (
                    <button
                      key={stage.value}
                      type="button"
                      className={`ctc-card ${isOn ? 'on' : ''}`}
                      disabled={!canAct || isCurrent}
                      aria-pressed={isOn}
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
                always appears, even for native candidates. Visually
                differentiated by a deeper plum tint per the platform's
                "purple variations, not red/amber/green" convention. */}
            <button
              type="button"
              className={`ctc-card ctc-card-reject ${selectedMoveAction === REJECT_VALUE ? 'on' : ''}`}
              disabled={!canAct}
              aria-pressed={selectedMoveAction === REJECT_VALUE}
              onClick={() => setSelectedMoveAction(REJECT_VALUE)}
            >
              <div className="ctc-card-title">Reject</div>
              <div className="ctc-card-sub">Closes the application</div>
            </button>
          </div>
          {/* Rejection changes the one canonical ATS application, so it is
              global across the original and every related-role funnel. */}
          {isRejectSelected ? (
            <div className="ctc-reject-warning" role="alert">
              {isRelatedRole || hasRelatedRoles ? (
                <>
                  {linkedRoleReferences ? (
                    <>
                      <strong>Reject everywhere —</strong> rejecting here affects the shared {providerLabel}{' '}
                      application across all linked roles: {linkedRoleReferences}.
                    </>
                  ) : (
                    <>
                      <strong>Reject everywhere —</strong> this is one shared {providerLabel} application.
                      Rejecting here disqualifies the candidate in the original role and every related role.
                    </>
                  )}
                  {isPostHandoverAtsStage ? (
                    <> They are currently in <strong>{formatStatusLabel(currentAtsStage)}</strong> in {providerLabel}.</>
                  ) : null}
                </>
              ) : (
                <>
                  <strong>Heads up —</strong> this candidate is in{' '}
                  <strong>{formatStatusLabel(currentAtsStage)}</strong> in {providerLabel}, so rejecting will update them there.
                  You can still reject — just make sure that&apos;s intended.
                </>
              )}
            </div>
          ) : null}
          {isSharedMoveSelected ? (
            <div className="ctc-reject-warning" role="alert">
              {linkedRoleReferences ? (
                <>
                  <strong>Shared ATS move —</strong> moving the shared {providerLabel} application to{' '}
                  <strong>{selectedAtsStage.label}</strong> updates all linked roles: {linkedRoleReferences}.
                </>
              ) : (
                <>
                  <strong>Shared ATS move —</strong> moving this shared {providerLabel} application to{' '}
                  <strong>{selectedAtsStage.label}</strong> updates the original role and every related role.
                </>
              )}
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
