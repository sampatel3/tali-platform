import React, { useState } from 'react';
import { Pause, Play, Settings as SettingsIcon, Sparkles } from 'lucide-react';

import { useAgentStatus } from './AgentBar';

// AgentHeader — the single dark-purple slab that sits at the top of every
// recruiter page (HANDOFF unified-headers.md §2). Replaces the legacy
// PageHero (light) + Shell-level AgentBar combo: one component, one
// fixed-280px height, optional right-side agent panel for Jobs/Role detail.
//
// Visual states:
//   - 'agent-running' — vivid purple wash + animated glows + breathing
//      panel border (when `agent.on && !agent.paused`)
//   - 'agent-quiet'   — same purple base, glows muted (when no agent or
//      agent is off/paused). Used by every other page hero.
//
// Pages pass `title` either as a string (a trailing purple period is
// appended automatically) or as a React node — e.g. `<>5 active <em>roles</em></>` —
// to compose `<em>` highlights or skip the period.
const renderTitleNode = (title, period) => {
  if (title == null || title === '') return null;
  return (
    <h1>
      {title}
      {period ? <span className="ah-period">.</span> : null}
    </h1>
  );
};

const formatUsd = (cents) => {
  if (cents == null) return '$0';
  const dollars = Number(cents) / 100;
  if (!Number.isFinite(dollars)) return '$0';
  return dollars >= 100 ? `$${Math.round(dollars)}` : `$${dollars.toFixed(2)}`;
};

const DEFAULT_BUDGET_USD = 50;

// Inline activator shown inside the OFF-state panel: budget input + Activate
// button. `onActivate(monthlyBudgetCents)` is fire-and-forget — the parent
// optimistically flips the panel to ON, so we don't need a local
// "Activating…" state; the activator simply unmounts on the next render.
//
// When no `onActivate` is wired (e.g. Jobs list panel where activation is
// per-role, not org-wide), we render only the guidance copy so the panel
// reads as informational, not an unusable input.
const AgentOffActivator = ({ onActivate, disabledReason }) => {
  const [budget, setBudget] = useState(String(DEFAULT_BUDGET_USD));
  const [errorMsg, setErrorMsg] = useState(null);

  if (!onActivate) {
    return (
      <div className="agent-off-copy" style={{ marginTop: 'auto' }}>
        {disabledReason || 'Open a role to turn on agent mode there.'}
      </div>
    );
  }

  const submit = () => {
    const dollars = Number(budget);
    if (!Number.isFinite(dollars) || dollars <= 0) {
      setErrorMsg('Enter a monthly cap greater than $0.');
      return;
    }
    setErrorMsg(null);
    onActivate(Math.round(dollars * 100));
  };

  return (
    <>
      <div className="agent-off-copy">
        {disabledReason || 'Set the monthly cap for this role — covers pre-screen, scoring, semantic search, assessments, and the agent — then turn on the agent.'}
      </div>
      <div className="agent-off-budget">
        <span className="agent-off-budget-prefix">$</span>
        <input
          type="number"
          min={1}
          step={5}
          value={budget}
          onChange={(event) => setBudget(event.target.value)}
          aria-label="Role monthly budget in USD"
          inputMode="numeric"
        />
        <span className="agent-off-budget-suffix">/ month</span>
      </div>
      {errorMsg ? <div className="agent-off-error">{errorMsg}</div> : null}
      <div className="agent-actions">
        <button type="button" className="agent-btn primary" onClick={submit}>
          <Play size={11} strokeWidth={2} fill="currentColor" />
          Turn on agent
        </button>
      </div>
    </>
  );
};

const AgentPanel = ({
  agent,
  onActivate,
  onPause,
  onResume,
  onSettings,
  offStateMessage,
}) => {
  const {
    on = true,
    paused = false,
    pending = 0,
    spentCents = 0,
    budgetCents = 5000,
    tick = null,
    inFlight = false,
    pausedReason = null,
  } = agent || {};
  const status = !on ? (paused ? 'paused' : 'off') : 'on';
  const pct = budgetCents > 0
    ? Math.min(100, Math.round((Number(spentCents) / Number(budgetCents)) * 100))
    : 0;
  const spentLabel = formatUsd(spentCents);
  const budgetLabel = formatUsd(budgetCents);

  return (
    <aside className={`agent-panel agent-${status}`}>
      <div className="agent-panel-head">
        <div className="agent-pulse-wrap">
          <Sparkles size={16} strokeWidth={2} />
          {inFlight && on && !paused ? <span className="agent-pulse" aria-hidden="true" /> : null}
        </div>
        <div className="agent-status">
          <div className="agent-status-line">
            <span className="agent-mode">Agent mode</span>
            <span className={`agent-state-pill state-${status}`}>
              {status === 'paused' ? 'AUTO-PAUSED' : status.toUpperCase()}
            </span>
          </div>
          {pending > 0 ? (
            <div className="agent-pending">{pending} awaiting your review</div>
          ) : null}
        </div>
      </div>

      {/* `key={status}` re-mounts the body when on/off/paused flips so the new
          state plays the agentPanelEnter fade-in animation, giving the swap
          a soft transition instead of a hard cut. */}
      <div className="agent-panel-body" key={status}>
        {on && tick ? <div className="agent-tick">{tick}</div> : null}
        {paused ? (
          <div className="agent-tick">
            {(() => {
              // Mirror HomeRoles' humanization so the role detail page
              // matches the home page label. Backend writes
              // implementation-detail strings; recruiters see a clean phrase.
              const r = String(pausedReason || '').toLowerCase();
              let pretty = null;
              if (r.startsWith('monthly usd cap')) pretty = 'monthly budget reached';
              else if (r.includes('decision budget')) pretty = 'cycle limit reached';
              else if (pausedReason) pretty = String(pausedReason).slice(0, 64);
              return pretty
                ? `Auto-paused — ${pretty}. Resume to continue.`
                : 'Auto-paused. Resume to continue.';
            })()}
          </div>
        ) : null}

        {on || paused ? (
          <div className="agent-budget">
            <div className="agent-budget-row">
              <span title="Covers pre-screen, scoring, semantic search, assessments, and the agent on this role.">Role budget · this month</span>
              <span className="amt">{spentLabel} <span className="of">/ {budgetLabel}</span></span>
            </div>
            <div className="agent-budget-bar">
              <i className="fill" style={{ width: `${pct}%` }} />
            </div>
          </div>
        ) : null}

        {!on && !paused ? (
          <AgentOffActivator
            onActivate={onActivate}
            disabledReason={offStateMessage}
          />
        ) : (
          <div className="agent-actions">
            {on && !paused ? (
              <button
                type="button"
                className="agent-btn"
                onClick={onPause}
                disabled={!onPause}
              >
                <Pause size={11} strokeWidth={2} />
                Pause
              </button>
            ) : paused ? (
              <button
                type="button"
                className="agent-btn primary"
                onClick={onResume}
                disabled={!onResume}
              >
                <Play size={11} strokeWidth={2} fill="currentColor" />
                Resume
              </button>
            ) : null}
            <button
              type="button"
              className="agent-btn icon"
              title="Configure agent"
              aria-label="Configure agent"
              onClick={onSettings}
              disabled={!onSettings}
            >
              <SettingsIcon size={13} strokeWidth={1.7} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
};

export const AgentHeader = ({
  kicker,
  title,
  subtitle,
  actions = null,
  backLink = null,
  preTitle = null,
  postTitle = null,
  period = true,
  agent = null,
  // OFF state: called with monthly cap in cents. Page must hit
  // PATCH /roles/{id} with { agentic_mode_enabled: true, monthly_usd_budget_cents }.
  onActivateAgent,
  // ON state: called when user clicks Pause. Page hits PATCH /roles/{id}
  // with { agentic_mode_enabled: false }.
  onPauseAgent,
  // PAUSED state: called when user clicks Resume. Page hits PATCH /roles/{id}
  // with { agentic_mode_enabled: true } (also clears paused_at server-side).
  onResumeAgent,
  // Settings cog — opens the per-role agent settings drawer / tab.
  onAgentSettings,
  // Optional copy shown in the OFF panel when activation isn't available
  // (e.g. Jobs list, where activation is per-role).
  offStateMessage,
  className = '',
  variant = 'hero',
}) => {
  const showAgent = agent != null;
  const heroState =
    showAgent && agent.on && !agent.paused ? 'agent-running' : 'agent-quiet';

  return (
    <div
      className={`agent-header ${heroState} ${variant === 'compact' ? 'compact' : ''} ${className}`.trim()}
    >
      {/* Bright running-state gradient layered as an opacity-faded overlay so
          turning the agent on/off cross-fades the hero instead of snapping
          (browsers can't transition `background` between gradient values). */}
      <span className="ah-bright-overlay" aria-hidden="true" />
      <div className="agent-header-inner">
        <div className="agent-header-left">
          {backLink ? (
            backLink.onClick ? (
              <button type="button" className="back-link" onClick={backLink.onClick}>
                ← {backLink.label}
              </button>
            ) : (
              <a className="back-link" href={backLink.href || '#'}>← {backLink.label}</a>
            )
          ) : null}
          {preTitle ? <div className="ah-pre">{preTitle}</div> : null}
          {kicker ? <div className="ah-kicker">{kicker}</div> : null}
          <div className="ah-title-row">
            {renderTitleNode(title, period)}
            {actions ? <div className="ah-title-actions">{actions}</div> : null}
          </div>
          {subtitle ? <p className="ah-subtitle">{subtitle}</p> : null}
          {postTitle ? <div className="ah-post">{postTitle}</div> : null}
        </div>

        {showAgent ? (
          <AgentPanel
            agent={agent}
            onActivate={onActivateAgent}
            onPause={onPauseAgent}
            onResume={onResumeAgent}
            onSettings={onAgentSettings}
            offStateMessage={offStateMessage}
          />
        ) : null}
      </div>
    </div>
  );
};

// Convenience helper: turn the role-scoped /agent/status payload into the
// `agent={...}` shape AgentHeader expects. Pages can render their own panel
// by passing the result straight through. Returns null until the first
// status payload lands so the header can fall back to the OFF visual.
//
// Maps the backend's AgentStatusPayload (`paused_at`, `last_activity` with
// `event_type` + `created_at`, etc.) into the simpler `{on, paused, pending,
// spentCents, budgetCents, tick, inFlight}` shape the panel renders.
export const buildAgentPropFromStatus = (status, options = {}) => {
  if (!status) return null;
  const { isEnabled = null, fallbackTick = 'Agent is monitoring.' } = options;
  // Backend returns `paused_at: datetime|null` (not a boolean). The org
  // aggregator (useAgentStatusOrg) also injects a derived `paused` boolean
  // for the org rollup — accept either.
  const isPaused = status.paused != null
    ? Boolean(status.paused)
    : Boolean(status.paused_at);
  const enabled = isEnabled != null
    ? Boolean(isEnabled)
    : Boolean(status.enabled);
  return {
    // ON visual only when agentic_mode_enabled AND not auto-paused.
    on: enabled && !isPaused,
    // 'paused' here means *auto-paused* (paused_at set) while still enabled.
    // A manual pause flips agentic_mode_enabled=false → on=false, paused=false.
    paused: enabled && isPaused,
    pending: Number(status.pending_decisions || 0),
    spentCents: Number(status.monthly_spent_cents || 0),
    budgetCents: Number(status.monthly_budget_cents || 0) || 5000,
    tick: formatTick(status) || fallbackTick,
    inFlight: Boolean(status.current_run),
    // Actual reason the orchestrator set — surfaces "per-cycle token
    // budget exhausted" / "monthly USD cap reached" / etc. instead of a
    // hardcoded blanket message.
    pausedReason: status.paused_reason || null,
  };
};

const formatRelative = (iso) => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  const diff = Math.max(0, Date.now() - t);
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h ago`;
  return `${Math.round(diff / 86_400_000)}d ago`;
};

const tickFromActivity = (activity) => {
  if (!activity || typeof activity !== 'object') return null;
  // Org aggregator pre-annotates `summary`; honour it when present.
  if (activity.summary) {
    const ago = activity.relative_time
      || activity.ago
      || (activity.at ? formatRelative(activity.at) : formatRelative(activity.created_at));
    return ago ? `${activity.summary} · ${ago}` : String(activity.summary);
  }
  const subject = activity.candidate_name
    || (activity.application_id ? `application #${activity.application_id}` : 'candidate');
  let prefix;
  switch (activity.event_type) {
    case 'pipeline_stage_changed':
      prefix = `Advanced ${subject}`;
      break;
    case 'application_outcome_changed':
      prefix = `Updated outcome on ${subject}`;
      break;
    case 'agent_paused':
      prefix = `Paused — ${activity.reason || 'budget reached'}`;
      break;
    default:
      prefix = activity.reason
        || (activity.event_type ? String(activity.event_type).replace(/_/g, ' ') : null);
  }
  if (!prefix) return null;
  const ago = activity.created_at ? formatRelative(activity.created_at) : null;
  return ago ? `${prefix} · ${ago}` : prefix;
};

const tickFromCurrentRun = (run) => {
  if (!run) return null;
  const tools = Array.isArray(run.tools_called) ? run.tools_called : [];
  if (tools.length === 0) return 'Cycle running…';
  const last = tools[tools.length - 1];
  if (!last?.name) return 'Cycle running…';
  switch (last.name) {
    case 'score_cv': return 'Scoring a candidate';
    case 'queue_advance_decision': return 'Drafting an advance recommendation';
    case 'get_application': return 'Reading a candidate';
    case 'get_candidate_cv': return 'Inspecting a CV';
    default: return String(last.name).replace(/_/g, ' ');
  }
};

const formatTick = (status) => {
  if (!status) return null;
  return tickFromActivity(status.last_activity)
    || tickFromCurrentRun(status.current_run)
    || 'Idle · waiting for new candidates.';
};

export { useAgentStatus };
export default AgentHeader;
