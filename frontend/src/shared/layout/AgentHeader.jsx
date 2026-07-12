import React, { useEffect, useState } from 'react';
import { Pause, Play, Power, Settings as SettingsIcon, Sparkles } from 'lucide-react';

import { useAgentStatus } from './AgentBar';
import { AgentLoop } from '../motion';
import { BreadcrumbsRow } from '../ui/Breadcrumbs';
import { Button } from '../ui/TaaliPrimitives';

// AgentHeader — the single compact LIGHT header at the top of every recruiter
// page (redesign 2026-06). One fixed height (96px) across every page so the
// headers line up; an optional horizontal "agent strip" sits on the right for
// pages with an agent (Jobs / Role detail). Heavier secondary content
// (role-detail facts) drops to a thin sub-strip BELOW the header so the band
// itself stays the same height everywhere.
//
// The agent strip carries ONE state language, reused on every agent surface
// (header, the chat agent-rail, job cards):
//   - ON      — filled with the original dark-purple hero colour + soft glow
//   - PAUSED  — amber
//   - OFF     — quiet light, with an inline "turn on" activator
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

// Inline activator shown inside the OFF-state agent strip: a compact budget
// input + Turn-on button, laid out horizontally. `onActivate(cents)` is
// fire-and-forget — the parent optimistically flips the strip to ON, so the
// activator simply unmounts on the next render.
const AgentOffActivator = ({ onActivate, currentBudgetCents }) => {
  // Seed from the role's already-saved cap so activating never silently
  // overwrites it with the $50 default; fall back to the default only when
  // the role has no cap yet.
  const seededDollars = Number(currentBudgetCents) > 0
    ? String(Math.round(Number(currentBudgetCents) / 100))
    : String(DEFAULT_BUDGET_USD);
  const [budget, setBudget] = useState(seededDollars);
  const [touched, setTouched] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);

  useEffect(() => {
    if (!touched) setBudget(seededDollars);
  }, [seededDollars, touched]);

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
    <span className="ab-activate">
      <span className="ab-capbox" title={errorMsg || undefined}>
        <span className="pfx">$</span>
        <input
          type="number"
          min={1}
          step={5}
          value={budget}
          onChange={(event) => { setTouched(true); setBudget(event.target.value); }}
          aria-label="Role monthly budget in USD"
          inputMode="numeric"
        />
        <span className="sfx">/mo</span>
      </span>
      <Button variant="primary" size="sm" className="ab-btn primary" onClick={submit}>
        <Play size={11} strokeWidth={2} fill="currentColor" />
        Turn on
      </Button>
    </span>
  );
};

// AgentStrip — the horizontal agent bar that lives on the right of the header.
// Renders the unified on/off/paused/bulk state language. The dark-purple ON
// fill is driven by the `.abar.abar-on` class (see 13-page-hero-agentheader.css).
const AgentStrip = ({
  agent,
  onActivate,
  onPause,
  onResume,
  onTurnOff,
  onSettings,
  offStateMessage,
  pauseLabel = 'Pause',
  resumeLabel = 'Resume',
  pauseAllCount = null,
  resumeAllCount = null,
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
  const isManualPause = /recruiter|paused by/i.test(String(pausedReason || ''));
  const hasBulkCounts = pauseAllCount != null || resumeAllCount != null;
  // Mixed org — some roles running AND some paused. Pause and Resume are BOTH
  // live buttons. The split moves into the tick (so the buttons stay short),
  // and the budget bar yields its width so everything fits the fixed-size box.
  const isMixed = Number(pauseAllCount) > 0 && Number(resumeAllCount) > 0;
  const pct = budgetCents > 0
    ? Math.min(100, Math.round((Number(spentCents) / Number(budgetCents)) * 100))
    : 0;
  const spentLabel = formatUsd(spentCents);
  const budgetLabel = formatUsd(budgetCents);

  const label = status === 'on'
    ? 'Agent on'
    : status === 'paused'
      ? (isManualPause ? 'Paused' : 'Auto-paused')
      : 'Agent off';

  // The middle "tick" line — live activity (ON), humanized pause reason
  // (PAUSED), or the activation hint (OFF, no activator).
  let message = null;
  if (status === 'on') {
    message = tick;
  } else if (status === 'paused') {
    // Short — the Resume button already conveys "resume to continue".
    if (isManualPause) {
      message = 'Paused by you';
    } else {
      const r = String(pausedReason || '').toLowerCase();
      if (r.startsWith('monthly usd cap')) message = 'Monthly budget reached';
      else if (r.includes('decision budget')) message = 'Cycle limit reached';
      else message = 'Auto-paused';
    }
  } else if (!onActivate) {
    message = offStateMessage || 'Open a role to turn on agent mode there.';
  }
  // In a mixed org the per-role activity tick is ambiguous — state the split
  // instead, which is also what the two buttons act on.
  if (isMixed) {
    message = `${pauseAllCount} running · ${resumeAllCount} paused`;
  }

  const showBudget = (status === 'on' || status === 'paused') && budgetCents > 0 && !isMixed;

  return (
    // ONE persistent box (no key/remount) — the abar-{status} class morphs it
    // in place: the dark-purple Motion layer / amber ::after fill crossfade,
    // and the border / text / glow tween (see 13-page-hero CSS).
    <AgentLoop as="div" kind="glow" active={status === 'on'} className={`abar abar-${status}`}>
      <AgentLoop kind="flow" active={status === 'on'} className="abar-flow-layer" />
      <span className="ab-spark">
        <Sparkles size={15} strokeWidth={2} />
        {inFlight && on && !paused ? <AgentLoop kind="ring" className="ab-pulse" /> : null}
      </span>
      <span className="ab-label">{label}</span>
      {pending > 0 ? (
        <span className="ab-pending" title={`${pending} awaiting your review`}>{pending}</span>
      ) : null}
      {message ? <span className="ab-tick" title={typeof message === 'string' ? message : undefined}>{message}</span> : <span className="ab-tick" />}

      {showBudget ? (
        <span className="ab-budget" title="Covers pre-screen, scoring, semantic search, assessments, and the agent on this role.">
          <span className="ab-budget-amt">{spentLabel}<span className="of"> / {budgetLabel}</span></span>
          <span className="ab-budget-bar"><i style={{ width: `${pct}%` }} /></span>
        </span>
      ) : null}

      {status === 'off' && onActivate ? (
        <AgentOffActivator onActivate={onActivate} currentBudgetCents={budgetCents} />
      ) : (
        <span className="ab-actions">
          {hasBulkCounts ? (
            <>
              {Number(pauseAllCount) > 0 ? (
                <Button
                  variant={status === 'on' ? 'inverse' : 'secondary'}
                  size="sm"
                  className="ab-btn"
                  onClick={onPause}
                  disabled={!onPause}
                >
                  <Pause size={11} strokeWidth={2} />
                  {pauseLabel}
                </Button>
              ) : null}
              {Number(resumeAllCount) > 0 ? (
                <Button variant="primary" size="sm" className="ab-btn primary" onClick={onResume} disabled={!onResume}>
                  <Play size={11} strokeWidth={2} fill="currentColor" />
                  {resumeLabel}
                </Button>
              ) : null}
            </>
          ) : status === 'on' ? (
            <>
              <Button variant="inverse" size="sm" className="ab-btn" onClick={onPause} disabled={!onPause}>
                <Pause size={11} strokeWidth={2} />
                {pauseLabel}
              </Button>
              {onTurnOff ? (
                <Button
                  variant="inverse"
                  size="sm"
                  iconOnly
                  className="ab-btn ic"
                  title="Turn off agent for this role"
                  aria-label="Turn off agent"
                  onClick={onTurnOff}
                >
                  <Power size={13} strokeWidth={2} />
                </Button>
              ) : null}
            </>
          ) : status === 'paused' ? (
            <>
              <Button variant="primary" size="sm" className="ab-btn primary" onClick={onResume} disabled={!onResume}>
                <Play size={11} strokeWidth={2} fill="currentColor" />
                {resumeLabel}
              </Button>
              {onTurnOff ? (
                <Button
                  variant="secondary"
                  size="sm"
                  iconOnly
                  className="ab-btn ic"
                  title="Turn off agent for this role"
                  aria-label="Turn off agent"
                  onClick={onTurnOff}
                >
                  <Power size={13} strokeWidth={2} />
                </Button>
              ) : null}
            </>
          ) : null}
          {onSettings ? (
            <Button
              variant={status === 'on' ? 'inverse' : 'secondary'}
              size="sm"
              iconOnly
              className="ab-btn ic"
              title="Configure agent"
              aria-label="Configure agent"
              onClick={onSettings}
            >
              <SettingsIcon size={13} strokeWidth={1.7} />
            </Button>
          ) : null}
        </span>
      )}
    </AgentLoop>
  );
};

export const AgentHeader = ({
  kicker,
  title,
  subtitle,
  actions = null,
  // Breadcrumb trail rendered as a light strip ABOVE the header. Every
  // recruiter page passes it so the header never shifts vertically between
  // pages. Navigation only — no action buttons.
  breadcrumbs = null,
  // Inline lead block to the LEFT of the title (e.g. the candidate avatar).
  preTitle = null,
  // Secondary content (role-detail facts) — rendered in a thin sub-strip
  // BELOW the header so the header band stays a uniform height everywhere.
  // When present, `actions` move into the sub-strip alongside it.
  postTitle = null,
  period = true,
  agent = null,
  onActivateAgent,
  onPauseAgent,
  onResumeAgent,
  onTurnOffAgent,
  onAgentSettings,
  offStateMessage,
  pauseLabel,
  resumeLabel,
  pauseAllCount = null,
  resumeAllCount = null,
  className = '',
  variant = 'hero',
}) => {
  const showAgent = agent != null;
  const heroState =
    showAgent && agent.on && !agent.paused ? 'agent-running' : 'agent-quiet';

  const hasBreadcrumbs = Array.isArray(breadcrumbs) && breadcrumbs.length > 0;
  // A sub-strip exists only when a page supplies postTitle (today: role
  // detail's facts). Its actions travel with it; otherwise actions sit in the
  // header's right zone next to the agent strip.
  const hasSubstrip = postTitle != null;
  const headerActions = hasSubstrip ? null : actions;
  const substripActions = hasSubstrip ? actions : null;

  return (
    <>
      {hasBreadcrumbs ? (
        <BreadcrumbsRow items={breadcrumbs} />
      ) : null}
      <div
        className={`agent-header ${heroState} ${variant === 'compact' ? 'compact' : ''} ${className}`.trim()}
      >
        {/* Faint lavender wash when the agent is running — fades in on top of
            the light base so OFF->ON cross-fades cleanly. */}
        <span className="ah-bright-overlay" aria-hidden="true" />
        <div className="agent-header-inner">
          <div className="agent-header-left">
            {preTitle ? <div className="ah-pre">{preTitle}</div> : null}
            <div className="ah-headings">
              {kicker ? <div className="ah-kicker">{kicker}</div> : null}
              <div className="ah-title-row">{renderTitleNode(title, period)}</div>
              {subtitle ? <p className="ah-subtitle">{subtitle}</p> : null}
            </div>
          </div>

          {(headerActions || showAgent) ? (
            <div className="agent-header-right">
              {headerActions ? <div className="ah-actions">{headerActions}</div> : null}
              {showAgent ? (
                <AgentStrip
                  agent={agent}
                  onActivate={onActivateAgent}
                  onPause={onPauseAgent}
                  onResume={onResumeAgent}
                  onTurnOff={onTurnOffAgent}
                  onSettings={onAgentSettings}
                  offStateMessage={offStateMessage}
                  pauseLabel={pauseLabel}
                  resumeLabel={resumeLabel}
                  pauseAllCount={pauseAllCount}
                  resumeAllCount={resumeAllCount}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      {hasSubstrip ? (
        <div className={`agent-substrip ${heroState}`}>
          <div className="agent-substrip-inner">
            <div className="ah-substrip-main">{postTitle}</div>
            {substripActions ? <div className="ah-substrip-actions">{substripActions}</div> : null}
          </div>
        </div>
      ) : null}
    </>
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
      prefix = activity.reason ? String(activity.reason) : null;
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
    default: return 'Working…';
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
