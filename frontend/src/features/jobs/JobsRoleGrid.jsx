import { forwardRef } from 'react';
import {
  Building2,
  Globe,
  Inbox,
  Pause,
  RefreshCw,
  Sparkles,
  Star,
  Zap,
} from 'lucide-react';

import {
  PIPELINE_FUNNEL_STAGES,
  invitedStageValue,
  funnelStageTone,
  formatCount,
} from '../../shared/metrics';
import {
  AnimatePresence,
  AgentLoop,
  LayoutGroup,
  MotionNumber,
  cappedStaggerDelay,
  fadeVariants,
  m,
  motionTransition,
  reducedFadeVariants,
} from '../../shared/motion';
import { formatRelativeDateTime } from '../../shared/ui/RecruiterDesignPrimitives';
import {
  atsProviderLabel,
  AtsTypeTag,
  effectiveNativeJobStatus,
  roleAtsProvider,
  roleExternalJobLive,
  roleExternalJobState,
} from './atsType';

const STAGES = PIPELINE_FUNNEL_STAGES;
const StageCount = ({ value }) => <MotionNumber value={value} format={formatCount} />;

// Posting lifecycle controls visual dimming independently of agent state.
const ROLE_CARD_DIMMED_OPACITY = 0.55;
const LIVE_EXTERNAL_STATES = new Set(['published', 'open', 'accepting candidates', 'accepting_candidates']);
const NON_LIVE_EXTERNAL_STATES = new Set(['draft', 'archived', 'closed', 'filled', 'cancelled', 'inactive']);
const roleCardFadeVariants = Object.freeze({
  hidden: fadeVariants.hidden,
  visible: ({ index = 0, stagger = false } = {}) => ({
    opacity: 1,
    transition: {
      ...motionTransition.reveal,
      delay: stagger ? cappedStaggerDelay(index, 'dense') : 0,
    },
  }),
  dimmed: ({ index = 0, stagger = false } = {}) => ({
    opacity: ROLE_CARD_DIMMED_OPACITY,
    transition: {
      ...motionTransition.reveal,
      delay: stagger ? cappedStaggerDelay(index, 'dense') : 0,
    },
  }),
  exit: fadeVariants.exit,
});
const reducedRoleCardFadeVariants = Object.freeze({
  ...reducedFadeVariants,
  dimmed: Object.freeze({ opacity: ROLE_CARD_DIMMED_OPACITY, transition: motionTransition.instant }),
});

const JOB_STATUS_META = {
  draft: { label: 'Draft', tone: 'draft' },
  open: { label: 'Open', tone: 'open' },
  filled: { label: 'Filled', tone: 'filled' },
  filled_external: { label: 'Filled · external', tone: 'ext' },
  cancelled: { label: 'Archived', tone: 'cancelled' },
};

const externalRoleStatus = (role) => {
  const state = roleExternalJobState(role);
  const live = roleExternalJobLive(role);
  const formattedState = String(state || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
  return {
    label: {
      archived: 'Archived',
      cancelled: 'Cancelled',
      closed: 'Closed',
      draft: 'Draft',
      filled: 'Filled',
      inactive: 'Inactive',
    }[state] || formattedState || (live === true ? 'Open' : 'Status pending sync'),
    tone: live === true || LIVE_EXTERNAL_STATES.has(state) ? 'open'
      : state === 'draft' ? 'draft'
        : state === 'filled' ? 'filled' : 'cancelled',
  };
};

export const isRoleDraft = (role) => {
  if (roleAtsProvider(role)) return roleExternalJobState(role) === 'draft';
  return effectiveNativeJobStatus(role) === 'draft';
};

export const isRoleLive = (role) => {
  const provider = roleAtsProvider(role);
  if (provider) {
    const live = roleExternalJobLive(role);
    if (live != null) return live;
    return LIVE_EXTERNAL_STATES.has(roleExternalJobState(role));
  }
  if (effectiveNativeJobStatus(role) !== 'open') return false;
  return role?.is_published == null ? true : role.is_published === true;
};

export const isRoleDimmed = (role) => {
  const provider = roleAtsProvider(role);
  if (provider) {
    const live = roleExternalJobLive(role);
    if (live != null) return !live;
    return NON_LIVE_EXTERNAL_STATES.has(roleExternalJobState(role));
  }
  return effectiveNativeJobStatus(role) !== 'open';
};

const JobsRoleCard = forwardRef(function JobsRoleCard({
  activeAts,
  activeAtsLastSyncAt,
  agentLive,
  gridStaggerDone,
  onNavigate,
  onToggleStar,
  reduced,
  role,
  roleCount,
  roleFamily,
  roleIndex,
  workspacePaused,
}, ref) {
  const stageCounts = role?.stage_counts || {};
  const roleProvider = roleAtsProvider(role);
  const roleProviderLabel = atsProviderLabel(roleProvider);
  const workableRole = roleProvider === 'workable';
  const roleLive = isRoleLive(role);
  const lifecycleDimmed = isRoleDimmed(role);
  const roleLifecycleMeta = roleProvider
    ? externalRoleStatus(role)
    : JOB_STATUS_META[effectiveNativeJobStatus(role)];
  const lastRoleActivity = role?.last_candidate_activity_at
    || role?.updated_at
    || (roleProvider === activeAts ? activeAtsLastSyncAt : null)
    || null;
  const agentEnabled = Boolean(role?.agentic_mode_enabled);
  const agentPaused = agentEnabled && Boolean(role?.agent_paused_at);
  const agentHeld = agentEnabled && !agentPaused && workspacePaused;
  const agentActive = agentEnabled && !agentPaused && !workspacePaused;
  const activationIntent = role?.assessment_task_provisioning?.activation_intent;
  const activationStatus = String(activationIntent?.status || '');
  const activationQueued = !agentEnabled && ['pending', 'retry_wait'].includes(activationStatus);
  const activationBlocked = !agentEnabled && activationStatus === 'blocked';
  const agentBudget = Number(
    agentLive?.monthly_budget_cents
    ?? role?.monthly_usd_budget_cents
    ?? 0,
  ) / 100;
  const agentSpent = agentLive ? Number(agentLive.monthly_spent_cents || 0) / 100 : null;
  const pendingCount = Number(agentLive?.pending_decisions || 0);
  const roleLoc = String(role?.location || role?.workable_location || '').trim();
  const roleDept = String(role?.department || role?.workable_department || '').trim();
  const roleMeta = [
    roleDept || null,
    roleLoc || null,
    lastRoleActivity ? `updated ${formatRelativeDateTime(lastRoleActivity)}` : null,
  ].filter(Boolean).join(' · ') || 'No details yet';

  return (
    <m.div
      ref={ref}
      layout={reduced || roleCount > 40 ? false : 'position'}
      custom={{ index: roleIndex, stagger: !gridStaggerDone }}
      variants={reduced ? reducedRoleCardFadeVariants : roleCardFadeVariants}
      initial={reduced ? false : 'hidden'}
      animate={lifecycleDimmed ? 'dimmed' : 'visible'}
      exit="exit"
      transition={{ layout: reduced ? motionTransition.instant : motionTransition.layout }}
      data-motion-index={roleIndex}
      data-role-family={roleFamily?.isLinked ? roleFamily?.owner?.id : undefined}
      className={`job-card is-active ${workableRole ? 'from-wk' : ''} ${agentActive ? 'agent-on' : ''} ${lifecycleDimmed ? 'not-live' : ''}`}
      onClick={() => onNavigate('job-pipeline', { roleId: role.id })}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onNavigate('job-pipeline', { roleId: role.id });
        }
      }}
      style={{ cursor: 'pointer' }}
    >
      <div className="job-head">
        {roleLive ? (
          <span
            className="job-star is-locked"
            aria-label={roleProvider ? `Live ${roleProviderLabel} role · always in continuous sync` : 'Live native role · monitored continuously'}
            title={roleProvider ? `Live ${roleProviderLabel} role · always in continuous sync (auto-starred)` : 'Live native role · monitored continuously (auto-starred)'}
            style={{
              padding: 2,
              marginTop: 2,
              flexShrink: 0,
              color: 'var(--purple)',
              cursor: 'default',
              display: 'inline-flex',
            }}
          >
            <Star size={16} strokeWidth={1.5} fill="currentColor" />
          </span>
        ) : (
          <button
            type="button"
            className="job-star"
            onClick={(event) => {
              event.stopPropagation();
              void onToggleStar(role);
            }}
            aria-label={role.starred_for_auto_sync ? 'Unstar role (stop auto-sync)' : 'Star role to enable auto-sync and real-time scoring'}
            aria-pressed={Boolean(role.starred_for_auto_sync)}
            title={role.starred_for_auto_sync ? 'Auto-sync enabled · click to disable' : `Star to auto-sync${roleProvider ? ` from ${roleProviderLabel}` : ''} and score in real-time`}
            style={{
              background: 'transparent',
              border: 'none',
              padding: 2,
              marginTop: 2,
              cursor: 'pointer',
              flexShrink: 0,
              color: role.starred_for_auto_sync ? 'var(--purple)' : 'var(--ink-soft)',
            }}
          >
            <Star
              size={16}
              strokeWidth={1.5}
              fill={role.starred_for_auto_sync ? 'currentColor' : 'none'}
            />
          </button>
        )}
        <div className="job-card-title">
          <div className="job-card-title-line">
            <h3 className="role-name" title={role.name}>{role.name}</h3>
            <span className="job-card-role-id">#{role.id}</span>
          </div>
          <div className="role-meta" title={roleMeta}>{roleMeta}</div>
        </div>
        <span className="job-card-agent-slot">
          {agentPaused ? (
            <span className="job-agent-pill is-paused" title={agentBudget > 0 ? `Agent paused · cap $${Math.round(agentBudget)}` : 'Agent paused'}>
              <span className="d"><Pause size={10} strokeWidth={2.4} fill="currentColor" /></span>
              PAUSED
            </span>
          ) : agentHeld ? (
            <span className="job-agent-pill is-held" title="Workspace agent paused · this role remains on and will resume automatically">
              <span className="d"><Pause size={10} strokeWidth={2.4} fill="currentColor" /></span>
              ON · HELD
            </span>
          ) : agentEnabled ? (
            <AgentLoop kind="flow" className="job-agent-pill is-on" title="Agent on for this role">
              <span className="d"><Sparkles size={11} strokeWidth={2.2} /></span>
              {agentSpent != null && agentBudget > 0
                ? `ON · $${Math.round(agentSpent)}/$${Math.round(agentBudget)}`
                : agentBudget > 0
                  ? `ON · cap $${Math.round(agentBudget)}`
                  : 'ON'}
            </AgentLoop>
          ) : activationQueued ? (
            <span className="job-agent-pill is-queued" title="Turn on is saved; the backend is validating and preparing this role">
              <span className="d"><RefreshCw size={10} strokeWidth={2.3} /></span>
              TURN-ON QUEUED
            </span>
          ) : activationBlocked ? (
            <span className="job-agent-pill is-needs-input" title={activationIntent?.last_error || 'Turn on needs recruiter input'}>
              NEEDS INPUT
            </span>
          ) : (
            <span className="job-agent-pill is-off" title="Agent off">OFF</span>
          )}
        </span>
      </div>

      <div className="job-stats">
        {STAGES.map((stage) => {
          const value = stage.key === 'invited'
            ? invitedStageValue(stageCounts)
            : Number(stageCounts?.[stage.key] || 0);
          const tone = funnelStageTone(stage.key, value);
          return (
            <div key={stage.key} className={`js-cell${tone === 'term' ? ' is-term' : ''}`}>
              <div className="k">{stage.label}</div>
              <div className="v" style={tone === 'term' ? { color: 'var(--mute)' } : undefined}>
                <StageCount value={value} />
              </div>
            </div>
          );
        })}
      </div>

      <div className="job-card-bottom">
        <div className="job-card-pill-row">
          <div className="job-card-pill-list">
            <AtsTypeTag role={role} size="sm" className="ats-tag" />
            {roleLifecycleMeta ? (
              <span className={`job-status-badge is-${roleLifecycleMeta.tone}`}>
                {roleLifecycleMeta.label}
              </span>
            ) : null}
            {role?.is_published && roleLive ? (
              <span className="job-live-badge" title="Public job page is live — candidates can apply">
                <Globe size={10} strokeWidth={2} /> Live
              </span>
            ) : null}
            {role?.client_name ? (
              <span className="job-client-chip" title={`Client · ${role.client_name}`}>
                <Building2 size={10} strokeWidth={2} /> {role.client_name}
              </span>
            ) : null}
          </div>
        </div>
        <div className="job-foot">
          {pendingCount > 0 ? (
            <span className="job-foot-pending"><Inbox size={13} aria-hidden="true" /> {pendingCount} awaiting you</span>
          ) : agentPaused ? (
            <span className="job-foot-hint job-foot-paused"><Pause size={13} aria-hidden="true" /> Agent paused</span>
          ) : !agentEnabled ? (
            <span className="job-foot-hint"><Zap size={13} aria-hidden="true" /> Turn on agent mode to start screening</span>
          ) : (
            <span />
          )}
          <span className="job-foot-open">Open pipeline →</span>
        </div>
      </div>
    </m.div>
  );
});

export function JobsRoleGrid({
  activeAts,
  activeAtsLastSyncAt,
  agentSpendByRole,
  gridStaggerDone,
  onNavigate,
  onToggleStar,
  reduced,
  roleGroups,
  roles,
  workspacePaused,
}) {
  return (
    <LayoutGroup id="jobs-role-grid">
      <div
        className="jobs-grid"
        data-motion-stagger={gridStaggerDone ? 'settled' : 'entering'}
        style={{ position: 'relative' }}
      >
        <AnimatePresence initial={false} mode={reduced ? 'sync' : 'popLayout'}>
          {(roleGroups || []).map((familyGroup) => {
            const roleCards = familyGroup.visibleRoles.map((role) => (
              <JobsRoleCard
                key={role.id}
                activeAts={activeAts}
                activeAtsLastSyncAt={activeAtsLastSyncAt}
                agentLive={agentSpendByRole?.[role.id] || null}
                gridStaggerDone={gridStaggerDone}
                onNavigate={onNavigate}
                onToggleStar={onToggleStar}
                reduced={reduced}
                role={role}
                roleCount={roles.length}
                roleFamily={familyGroup.context}
                roleIndex={roles.findIndex((item) => Number(item?.id) === Number(role?.id))}
                workspacePaused={workspacePaused}
              />
            ));
            if (!familyGroup.context?.isLinked) return roleCards[0] || null;
            const familyGridSize = Math.min(Math.max(roleCards.length, 1), 3);
            return (
              <m.section
                key={familyGroup.key}
                layout={reduced ? false : 'position'}
                className={`job-family-group is-size-${familyGridSize}`}
                data-role-family={familyGroup.ownerId || undefined}
                data-family-size={familyGridSize}
                aria-label="Shared candidate pool"
              >
                <div className="job-family-grid">{roleCards}</div>
              </m.section>
            );
          })}
        </AnimatePresence>
      </div>
    </LayoutGroup>
  );
}

export default JobsRoleGrid;
