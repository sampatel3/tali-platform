// PUBLIC, auth-free PREVIEW of the real /jobs roles board with the Motion
// library (motion.dev) applied. It is NOT a new design: it reproduces the
// production JobsPage showcase render — the AgentHeader, the Workable sync
// strip, the source-filter chips and the role-card grid — against the SAME
// JOBS_SHOWCASE / JOBS_SHOWCASE_ORG fixtures the real page uses in showcase
// mode, reusing the real card markup + CSS classes (.job-card, .job-head,
// .job-stats, .job-agent-pill …) and the real shared atoms (AgentHeader,
// WorkableTag, SyncPulse, WorkableLogo). Motion only adds motion: the cards
// reveal in a stagger, each agent-status chip animates in, the per-role stage
// counts tick up, and cards lift on hover. Everything respects
// prefers-reduced-motion via the shared MotionSystemProvider.
//
// The inline card is reproduced (not imported) because JobsPage renders it
// inline (~L894–1066) with no standalone card component, and we must not touch
// the production page.

import React, { useMemo, useState } from 'react';
import { AgentLoop, MotionSystemProvider, Reveal, m, useReducedMotionSync } from '../../shared/motion';
import { Building2, Filter, Inbox, Pause, Sparkles, Star, Zap } from 'lucide-react';

import { PIPELINE_FUNNEL_STAGES, funnelStageTone, formatCount } from '../../shared/metrics';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { WorkableTag, WorkableLogo, SyncPulse, formatRelativeDateTime } from '../../shared/ui/RecruiterDesignPrimitives';
import { JOBS_SHOWCASE, JOBS_SHOWCASE_ORG } from '../demo/productWalkthroughModels';
import {
  EASE_OUT,
  NumberTicker,
  PreviewSwitcher,
  staggerContainer,
  staggerItem,
} from '../../shared/motion/previewMotion';
import './JobsMotionPreview.css';

const STAGES = PIPELINE_FUNNEL_STAGES;
const ROLE_CARD_DIMMED_OPACITY = 0.55;

const JOB_STATUS_META = {
  draft: { label: 'Draft', tone: 'draft' },
  open: { label: 'Open', tone: 'open' },
  filled: { label: 'Filled', tone: 'filled' },
  filled_external: { label: 'Filled · external', tone: 'ext' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
};

// Mirrors JobsPage helpers verbatim so the preview cards read exactly like prod.
const isRoleDraft = (role) => (
  !role?.workable_job_id && !role?.job_spec_present && Number(role?.applications_count || 0) === 0
);
const isRoleLive = (role) => String(role?.workable_job_state || '').toLowerCase() === 'published';
const isRoleDimmed = (role) => (
  String(role?.source || '').toLowerCase() === 'workable' && !isRoleLive(role)
);
const getRoleBadgeLabel = (role) => {
  if (String(role?.source || '').toLowerCase() === 'workable') return null;
  if (isRoleDraft(role)) return 'Draft';
  return 'Role';
};

// The base JOBS_SHOWCASE fixture has no agent status or Workable job-state on
// its roles (the real showcase board is a static snapshot). Enrich a COPY here
// — preview-only, the fixture is untouched — so the board shows the real
// agent-ON / PAUSED / OFF vocabulary and per-role pending counts the founder
// needs to see move. `workable_job_state: 'published'` lights the live star.
const PREVIEW_ROLES = JOBS_SHOWCASE.map((role) => {
  if (role.id === 7001) {
    return { ...role, workable_job_state: 'published', agentic_mode_enabled: true };
  }
  if (role.id === 7002) {
    return {
      ...role,
      workable_job_state: 'published',
      agentic_mode_enabled: true,
      agent_paused_at: '2026-04-27T06:00:00.000Z',
    };
  }
  if (role.id === 7004) {
    return {
      ...role,
      source: 'workable',
      workable_job_state: 'draft',
    };
  }
  return role;
});

// Per-role live agent spend, keyed by role id — the shape JobsPage's
// /roles/{id}/agent/status fan-out produces (monthly_budget_cents,
// monthly_spent_cents, pending_decisions).
const AGENT_SPEND_BY_ROLE = {
  7001: { monthly_budget_cents: 5000, monthly_spent_cents: 1820, pending_decisions: 3 },
  7002: { monthly_budget_cents: 4000, monthly_spent_cents: 3120, pending_decisions: 0 },
};

const SOURCE_FILTERS = [
  { key: 'all', label: 'All roles' },
  { key: 'live', label: 'Live' },
  { key: 'workable', label: 'From Workable' },
  { key: 'manual', label: 'Created in Taali' },
  { key: 'draft', label: 'Draft' },
];

const filterRoleBySource = (role, sourceFilter) => {
  if (sourceFilter === 'live') return isRoleLive(role);
  if (sourceFilter === 'workable') return String(role?.source || '').toLowerCase() === 'workable';
  if (sourceFilter === 'manual') return String(role?.source || '').toLowerCase() !== 'workable';
  if (sourceFilter === 'draft') return isRoleDraft(role);
  return true;
};

// One role card — a faithful reproduction of JobsPage's inline card markup and
// classes, with Motion layered on: the whole card reveals via the parent
// stagger, the agent-status chip animates in, and the stage-count values tick.
const RoleCard = ({ role, agentLive, reduced }) => {
  const stageCounts = role?.stage_counts || {};
  const workableRole = String(role?.source || '').toLowerCase() === 'workable';
  const roleLive = isRoleLive(role);
  const lifecycleDimmed = isRoleDimmed(role);
  const lastRoleActivity = role?.last_candidate_activity_at || role?.updated_at || null;
  const roleBadgeLabel = getRoleBadgeLabel(role);
  const agentEnabled = Boolean(role?.agentic_mode_enabled);
  const agentPaused = agentEnabled && Boolean(role?.agent_paused_at);
  const agentActive = agentEnabled && !agentPaused;
  const roleActive = agentActive && !lifecycleDimmed;
  const roleDimmed = !roleActive;
  const agentBudget = Number(agentLive?.monthly_budget_cents ?? 0) / 100;
  const agentSpent = agentLive ? Number(agentLive.monthly_spent_cents || 0) / 100 : null;
  const pendingCount = Number(agentLive?.pending_decisions || 0);
  const roleLoc = String(role?.location || role?.workable_location || '').trim();
  const roleDept = String(role?.department || role?.workable_department || '').trim();

  return (
    <m.div
      variants={{
        ...staggerItem,
        show: {
          ...staggerItem.show,
          opacity: roleDimmed ? ROLE_CARD_DIMMED_OPACITY : 1,
        },
      }}
      whileHover={reduced ? undefined : { y: -4, transition: { duration: 0.18, ease: EASE_OUT } }}
      className={`job-card ${workableRole ? 'from-wk' : ''} ${roleActive ? 'agent-on' : 'agent-inactive'} ${lifecycleDimmed ? 'not-live' : ''}`}
      style={{ cursor: 'default' }}
    >
      <div className="job-head">
        <span
          className="job-star is-locked"
          aria-hidden="true"
          style={{ padding: 2, marginTop: 2, flexShrink: 0, color: roleLive ? 'var(--purple)' : 'var(--ink-soft)', display: 'inline-flex' }}
        >
          <Star size={16} strokeWidth={1.5} fill={roleLive ? 'currentColor' : 'none'} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, flexWrap: 'wrap' }}>
            <h3 className="role-name">{role.name}</h3>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--mute)' }}>#{role.id}</span>
            {workableRole ? (
              <WorkableTag label="WORKABLE" size="sm" className="wk-tag !border-0 !px-2 !py-1 !text-[0.59375rem]" />
            ) : (
              <span className={`chip ${isRoleDraft(role) ? '' : 'purple'}`} style={{ fontSize: 10 }}>
                {roleBadgeLabel}
              </span>
            )}
            {role?.job_status && JOB_STATUS_META[role.job_status] ? (
              <span className={`job-status-badge is-${JOB_STATUS_META[role.job_status].tone}`}>
                {JOB_STATUS_META[role.job_status].label}
              </span>
            ) : null}
          </div>
          <div className="role-meta">
            {[
              roleDept || null,
              roleLoc || null,
              lastRoleActivity ? `updated ${formatRelativeDateTime(lastRoleActivity)}` : null,
            ].filter(Boolean).join(' · ') || 'No details yet'}
          </div>
        </div>
        {/* The agent-status chip springs in after the card settles. */}
        <m.span
          initial={reduced ? false : { opacity: 0, scale: 0.7 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.28, duration: 0.32, ease: EASE_OUT }}
          style={{ display: 'inline-flex' }}
        >
          {agentPaused ? (
            <span className="job-agent-pill is-paused" title="Agent paused">
              <span className="d"><Pause size={10} strokeWidth={2.4} fill="currentColor" /></span>
              PAUSED
            </span>
          ) : agentEnabled ? (
            <AgentLoop kind="flow" className="job-agent-pill is-on" title="Agent on for this role">
              <span className="d"><Sparkles size={11} strokeWidth={2.2} /></span>
              {agentSpent != null && agentBudget > 0
                ? `ON · $${Math.round(agentSpent)}/$${Math.round(agentBudget)}`
                : agentBudget > 0 ? `ON · cap $${Math.round(agentBudget)}` : 'ON'}
            </AgentLoop>
          ) : (
            <span className="job-agent-pill is-off" title="Agent off">OFF</span>
          )}
        </m.span>
      </div>

      <div className="job-stats">
        {STAGES.map((stage) => {
          const value = Number(stageCounts?.[stage.key] || 0);
          const tone = funnelStageTone(stage.key, value);
          return (
            <div key={stage.key} className={`js-cell${tone === 'term' ? ' is-term' : ''}`}>
              <div className="k">{stage.label}</div>
              <div className="v" style={tone === 'term' ? { color: 'var(--mute)' } : undefined}>
                {value > 0
                  ? <NumberTicker to={value} reduced={reduced} format={(n) => formatCount(Math.round(n))} />
                  : formatCount(value)}
              </div>
            </div>
          );
        })}
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
    </m.div>
  );
};

export const JobsMotionPreview = () => {
  const reduced = useReducedMotionSync();
  const [sourceFilter, setSourceFilter] = useState('all');

  const sourceCounts = useMemo(() => PREVIEW_ROLES.reduce((acc, role) => {
    acc.all += 1;
    if (isRoleLive(role)) acc.live += 1;
    if (String(role?.source || '').toLowerCase() === 'workable') acc.workable += 1;
    else acc.manual += 1;
    if (isRoleDraft(role)) acc.draft += 1;
    return acc;
  }, { all: 0, live: 0, workable: 0, manual: 0, draft: 0 }), []);

  const filtered = useMemo(
    () => PREVIEW_ROLES.filter((role) => filterRoleBySource(role, sourceFilter)),
    [sourceFilter],
  );

  const workableRolesCount = sourceCounts.workable;

  // Org-aggregate agent strip — mirrors JobsPage's showcase header agent.
  const headerAgent = {
    on: true,
    paused: false,
    pending: 3,
    spentCents: 1820,
    budgetCents: 5000,
    tick: 'Scoring 14 new candidates · just now',
    inFlight: true,
  };

  return (
    <MotionSystemProvider>
        <div data-brand="taali" className="jmp-root">
          <Reveal>
            <AgentHeader
              breadcrumbs={[{ label: 'Jobs' }]}
              kicker={`JOBS · ${sourceCounts.live} LIVE ROLE${sourceCounts.live === 1 ? '' : 'S'}`}
              title={<>{sourceCounts.live} live <em>roles</em></>}
              period={false}
              subtitle="You're hiring. Star a role to keep its candidates flowing in automatically."
              actions={(
                <button type="button" className="btn btn-outline">
                  <Filter size={13} /> Filter
                </button>
              )}
              agent={headerAgent}
              onPauseAgent={() => {}}
              offStateMessage="Open a role and turn on agent mode there — each role has its own monthly cap."
            />
          </Reveal>

          <div className="mc-page">
            {/* Workable sync strip — real WorkableLogo + SyncPulse on the
                JOBS_SHOWCASE_ORG fixture. */}
            <Reveal delay={0.06}>
              <div className="wk-strip">
                <div className="lg">
                  <WorkableLogo size={30} className="!rounded-[7px] !shadow-none" />
                </div>
                <div>
                  <div style={{ fontSize: '13.5px', fontWeight: 600, marginBottom: '2px' }}>
                    Synced from Workable · {workableRolesCount} role{workableRolesCount === 1 ? '' : 's'} · {sourceCounts.manual} created in Taali
                  </div>
                  <div className="meta">
                    <span>
                      <SyncPulse status="healthy" className="mr-2 inline-flex" />
                      Synced
                    </span>
                    <span>Last pull <b>{formatRelativeDateTime(JOBS_SHOWCASE_ORG.workable_last_sync_at)}</b></span>
                    <span><b>{JOBS_SHOWCASE_ORG.workable_last_sync_summary.new_candidates}</b> new candidates synced</span>
                  </div>
                </div>
              </div>
            </Reveal>

            {/* Source-filter chips — interactive, filters the grid. */}
            <Reveal delay={0.1}>
              <div className="filter-row" id="jobs-source-filters">
                {SOURCE_FILTERS.map((filter) => (
                  <button
                    key={filter.key}
                    type="button"
                    className={`f-chip ${sourceFilter === filter.key ? 'on' : ''}`}
                    onClick={() => setSourceFilter(filter.key)}
                  >
                    <span>{filter.label}</span>
                    <span className="ct">{sourceCounts[filter.key]}</span>
                  </button>
                ))}
              </div>
            </Reveal>

            {/* Role-card grid — staggered reveal. Keyed on the filter so a
                filter change replays the cascade for the new set. */}
            <m.div
              key={sourceFilter}
              className="jobs-grid"
              initial="hidden"
              animate="show"
              variants={staggerContainer(0.08, 0.12)}
            >
              {filtered.map((role) => (
                <RoleCard
                  key={role.id}
                  role={role}
                  agentLive={AGENT_SPEND_BY_ROLE[role.id] || null}
                  reduced={reduced}
                />
              ))}
            </m.div>
          </div>

          <PreviewSwitcher current="jobs" badge="PREVIEW · Jobs on Motion" />
        </div>
    </MotionSystemProvider>
  );
};

export default JobsMotionPreview;
