import React, { useEffect, useRef, useState } from 'react';
import { Pause, Play, Power, Settings as SettingsIcon, Sparkles } from 'lucide-react';

import { useAgentStatus } from './AgentBar';
import { getAgentPauseCopy } from '../agentPauseCopy';
import {
  AgentLoop,
  MotionNumber,
  m,
  motionTransition,
  useReducedMotionSync,
} from '../motion';
import { BreadcrumbsRow } from '../ui/Breadcrumbs';
import { Button, Spinner } from '../ui/TaaliPrimitives';

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
  return Number.isInteger(dollars) ? `$${Math.round(dollars)}` : `$${dollars.toFixed(2)}`;
};

const DEFAULT_BUDGET_USD = 50;

const nonNegativeCount = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : null;
};

const pendingSummary = (pending, breakdown) => {
  const total = nonNegativeCount(pending) || 0;
  const decisions = nonNegativeCount(breakdown?.decisions);
  const questions = nonNegativeCount(breakdown?.questions);
  const parts = [];
  if (decisions != null) {
    parts.push(`${decisions} candidate decision${decisions === 1 ? '' : 's'}`);
  }
  if (questions != null) {
    parts.push(`${questions} agent question${questions === 1 ? '' : 's'}`);
  }
  return parts.length > 0
    ? `${total} awaiting review: ${parts.join(' and ')}`
    : `${total} item${total === 1 ? '' : 's'} awaiting review`;
};

const formatRelative = (iso) => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  const diff = Math.max(0, Date.now() - t);
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.round(diff / 86_400_000)}d ago`;
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' }).format(new Date(t));
};

const manualPauseAttribution = (pausedBy, pausedAt, controlScope = 'role') => {
  const actorName = String(pausedBy?.name || '').trim();
  const when = formatRelative(pausedBy?.changed_at || pausedAt);
  const suffix = when ? ` · ${when}` : '';
  const currentUserSuffix = pausedBy?.is_current_user === true ? ' (you)' : '';
  const attribution = pausedBy?.attribution || (actorName || pausedBy?.user_id ? 'verified' : null);

  if (attribution === 'inferred' && actorName) {
    return {
      text: `Paused by ${actorName}${currentUserSuffix}${suffix}`,
      title: 'Legacy pause: assigned from workspace membership history because this was the only member present at the time.',
    };
  }
  if (attribution === 'verified' && actorName) {
    return { text: `Paused by ${actorName}${currentUserSuffix}${suffix}`, title: null };
  }
  if (attribution === 'unavailable' && pausedBy?.source === 'workspace_control') {
    return {
      text: actorName
        ? `Paused by ${actorName} (former team member)${suffix}`
        : `Paused by a former team member${suffix}`,
      title: 'The workspace pause retains its actor snapshot, but that team member account is no longer available.',
    };
  }
  if (
    (attribution === 'verified' && pausedBy?.user_id == null)
    || (attribution === 'unavailable' && pausedBy?.source === 'role_change_event')
  ) {
    return {
      text: `Paused by a former team member${suffix}`,
      title: 'The pause event is retained, but the team member account is no longer available.',
    };
  }
  // Optimistic local state: the signed-in viewer just clicked Pause and the
  // audit-backed status refetch has not returned yet.
  if (pausedBy?.is_current_user === true) {
    return { text: 'Paused by you · Saving…', title: null };
  }
  return {
    text: `${controlScope === 'workspace' ? 'Workspace pause owner' : 'Pause owner'} not recorded${suffix}`,
    title: controlScope === 'workspace'
      ? 'The workspace pause did not include an actor record.'
      : 'This role was paused before actor tracking was available. New pause actions record the team member.',
  };
};

// Inline activator shown inside the OFF-state agent strip: a compact budget
// input + Turn-on button, laid out horizontally. The parent owns asynchronous
// activation; this activator remains mounted until authoritative role state
// confirms that the agent is ON (or durable activation is shown as pending).
const AgentOffActivator = ({
  onActivate,
  currentBudgetCents,
  disabled = false,
  disabledReason = null,
}) => {
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
    if (disabled) return;
    const dollars = Number(budget);
    if (!Number.isFinite(dollars) || dollars <= 0) {
      setErrorMsg('Enter a monthly cap greater than $0.');
      return;
    }
    setErrorMsg(null);
    onActivate(Math.round(dollars * 100));
  };

  return (
    <span className="ab-activate" title={disabledReason || undefined}>
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
          disabled={disabled}
        />
        <span className="sfx">/mo</span>
      </span>
      <Button
        variant="primary"
        size="sm"
        className="ab-btn primary"
        onClick={submit}
        disabled={disabled}
        aria-description={disabledReason || undefined}
      >
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
  pauseLabel = null,
  resumeLabel = null,
  pauseAllCount = null,
  resumeAllCount = null,
  controlsDisabledReason = null,
}) => {
  const reduced = useReducedMotionSync();
  const hasMounted = useRef(false);
  useEffect(() => {
    hasMounted.current = true;
  }, []);
  const {
    loading = false,
    unavailable = false,
    on = true,
    paused = false,
    pending = 0,
    spentCents = 0,
    budgetCents = 5000,
    tick = null,
    inFlight = false,
    pausedAt = null,
    pausedReason = null,
    pausedBy = null,
    pendingBreakdown = null,
    bootstrapStatus = null,
    controlAction = null,
    controlScope = 'role',
    workspacePaused = false,
    rolePaused = false,
    rolePausedAt = null,
    rolePausedReason = null,
    rolePausedBy = null,
    runningRoleCount = 0,
    localPausedRoleCount = 0,
    workspaceControlVersion = null,
  } = agent || {};
  const controlsBusy = Boolean(controlAction);
  const controlsRestricted = Boolean(controlsDisabledReason);
  const status = loading
    ? 'loading'
    : unavailable
      ? 'unavailable'
      : !on
        ? (paused ? 'paused' : 'off')
        : 'on';
  const isWorkspaceControl = controlScope === 'workspace';
  const isWorkspaceOverride = !isWorkspaceControl
    && Boolean(workspacePaused)
    && (status === 'on' || status === 'paused');
  const pauseCopy = getAgentPauseCopy(pausedReason);
  const isManualPause = pauseCopy.kind === 'manual';
  const resolvedPauseLabel = pauseLabel || (isWorkspaceControl ? 'Pause all agents' : 'Pause');
  const resolvedResumeLabel = resumeLabel || (isWorkspaceControl ? 'Resume all agents' : 'Resume');
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

  const label = status === 'loading'
    ? 'Agent status'
    : status === 'unavailable'
      ? 'Agent status unavailable'
      : isWorkspaceOverride
    ? 'All agents paused'
    : status === 'on'
      ? (bootstrapStatus === 'starting'
        ? (isWorkspaceControl ? 'Agents starting' : 'Agent starting')
        : (isWorkspaceControl ? 'Agents on' : 'Agent on'))
      : status === 'paused'
        ? (isWorkspaceControl
          ? 'All agents paused'
          : (isManualPause ? 'Agent paused' : 'Auto-paused'))
        : (isWorkspaceControl ? 'Agents off' : 'Agent off');

  // The middle "tick" line — live activity (ON), humanized pause reason
  // (PAUSED), or the activation hint (OFF, no activator).
  let message = null;
  let messageTitle = null;
  if (status === 'loading') {
    message = tick || 'Checking current controls…';
  } else if (status === 'unavailable') {
    message = tick || 'Refresh to try again.';
  } else if (status === 'on') {
    message = isWorkspaceControl && Number(localPausedRoleCount) > 0
      ? `${Number(runningRoleCount) || 0} running · ${Number(localPausedRoleCount)} role-paused`
      : tick;
  } else if (status === 'paused') {
    // Manual pauses wait for the recruiter. System holds are rechecked by the
    // recovery sweep, while Resume remains an optional immediate retry.
    if (isManualPause) {
      // Persisted pause reasons deliberately remain generic. The append-only
      // role audit is the source of truth for who acted in a shared workspace.
      const attributionCopy = manualPauseAttribution(
        pausedBy,
        pausedAt,
        isWorkspaceControl || isWorkspaceOverride ? 'workspace' : 'role',
      );
      message = attributionCopy.text;
      messageTitle = attributionCopy.title;
    } else {
      const r = String(pausedReason || '').toLowerCase();
      if (r.includes('bootstrap failed')) {
        message = 'Startup held · auto-checking';
      } else if (pauseCopy.kind === 'unknown') {
        message = 'System hold · auto-checking';
      } else {
        message = `${pauseCopy.label} · auto-checking`;
      }
    }
  } else if (!onActivate) {
    message = offStateMessage || 'Open a role to turn on agent mode there.';
  }
  // In a mixed org the per-role activity tick is ambiguous — state the split
  // instead, which is also what the two buttons act on.
  if (isMixed) {
    message = `${pauseAllCount} running · ${resumeAllCount} paused`;
    messageTitle = null;
  }
  let workspaceRoleNote = null;
  if (isWorkspaceOverride) {
    if (!rolePaused) {
      workspaceRoleNote = 'This role remains on and will resume automatically.';
    } else {
      const localPauseCopy = getAgentPauseCopy(rolePausedReason);
      const localDetail = localPauseCopy.kind === 'manual'
        ? manualPauseAttribution(rolePausedBy, rolePausedAt, 'role').text
        : `${localPauseCopy.label}${formatRelative(rolePausedAt) ? ` · ${formatRelative(rolePausedAt)}` : ''}`;
      workspaceRoleNote = `Will remain paused after workspace resumes · ${localDetail}`;
    }
  }

  const showBudget = (status === 'on' || status === 'paused') && budgetCents > 0 && !isMixed;
  const pendingCount = nonNegativeCount(pending) || 0;
  const pendingLabel = status !== 'loading' && status !== 'unavailable' && pendingCount > 0
    ? pendingSummary(pendingCount, pendingBreakdown)
    : null;
  const layoutMotion = reduced ? false : 'position';
  // The first paint is fully settled. Later authoritative state/copy changes
  // get a tiny acknowledgement without delaying or ghosting the new text.
  const swapInitial = reduced || !hasMounted.current ? false : { opacity: 0.7, y: 2 };
  const swapTransition = reduced ? motionTransition.instant : motionTransition.fast;

  return (
    // ONE persistent box (no key/remount) — the abar-{status} class morphs it
    // in place: the dark-purple Motion layer / amber ::after fill crossfade,
    // and the border / text / glow tween (see 13-page-hero CSS).
    <AgentLoop
      as="div"
      kind="glow"
      active={status === 'on'}
      className={`abar abar-${status}`}
      aria-busy={status === 'loading' ? 'true' : undefined}
      layout={layoutMotion}
      transition={{ layout: reduced ? motionTransition.instant : motionTransition.layout }}
    >
      <AgentLoop kind="flow" active={status === 'on'} className="abar-flow-layer" />
      <m.span
        className={`ab-state${status === 'paused' && isManualPause && !isMixed ? ' ab-state-manual' : ''}`}
        layout={layoutMotion}
        transition={reduced ? motionTransition.instant : motionTransition.layout}
      >
        <m.span
          key={status}
          className="ab-spark"
          initial={reduced || !hasMounted.current ? false : { opacity: 0.7, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={swapTransition}
        >
          <Sparkles size={15} strokeWidth={2} />
          {inFlight && on && !paused ? <AgentLoop kind="ring" className="ab-pulse" /> : null}
        </m.span>
        <span className="ab-state-copy">
          <span
            className="ab-label"
            aria-label={label}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <m.span
              key={label}
              aria-hidden="true"
              initial={swapInitial}
              animate={{ opacity: 1, y: 0 }}
              transition={swapTransition}
            >
              {label}
            </m.span>
          </span>
          {message ? (
            <span
              className="ab-tick"
              title={messageTitle || (typeof message === 'string' ? message : undefined)}
              aria-label={status === 'paused' && isManualPause ? message : undefined}
            >
              <m.span
                key={String(message)}
                aria-hidden={status === 'paused' && isManualPause ? 'true' : undefined}
                initial={swapInitial}
                animate={{ opacity: 1, y: 0 }}
                transition={swapTransition}
              >
                {message}
              </m.span>
            </span>
          ) : <span className="ab-tick" />}
          {workspaceRoleNote ? (
            <span className="ab-scope-note" title={workspaceRoleNote}>{workspaceRoleNote}</span>
          ) : null}
        </span>
      </m.span>

      {pendingLabel ? (
        <m.span
          className="ab-review"
          title={pendingLabel}
          aria-label={pendingLabel}
          layout={layoutMotion}
          transition={reduced ? motionTransition.instant : motionTransition.layout}
        >
          <span className="ab-metric-label">Review queue</span>
          <span className="ab-review-value" aria-hidden="true">
            <MotionNumber
              value={pendingCount}
              format={(value) => String(Math.round(value))}
              reduced={reduced}
              aria-label={undefined}
            />
            <span> to review</span>
          </span>
        </m.span>
      ) : null}

      {showBudget ? (
        <span
          className="ab-budget"
          title="AI usage only: model-backed pre-screening, scoring, semantic search, assessment grading, and agent reasoning. Sandbox, email, storage, and repository hosting are separate."
        >
          <span className="ab-metric-label">AI spend</span>
          <span className="ab-budget-amt">{spentLabel}<span className="of"> of {budgetLabel}</span></span>
          <span
            className="ab-budget-bar"
            role="progressbar"
            aria-label={`AI spend ${spentLabel} of ${budgetLabel}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
          >
            <i style={{ width: `${pct}%` }} />
          </span>
        </span>
      ) : null}

      {status === 'loading' || status === 'unavailable' ? null : status === 'off' && onActivate ? (
        <span className="ab-actions" title={controlsDisabledReason || undefined}>
          {onSettings ? (
            <Button
              variant="secondary"
              size="sm"
              iconOnly
              className="ab-btn ic"
              title={controlsRestricted
                ? `View agent settings (read-only). ${controlsDisabledReason}`
                : 'Review agent settings before Turn on'}
              aria-label="Configure agent"
              onClick={onSettings}
            >
              <SettingsIcon size={13} strokeWidth={1.7} />
            </Button>
          ) : null}
          <AgentOffActivator
            onActivate={onActivate}
            currentBudgetCents={budgetCents}
            disabled={controlsRestricted}
            disabledReason={controlsDisabledReason}
          />
        </span>
      ) : (
        <span className="ab-actions" title={controlsDisabledReason || undefined}>
          {hasBulkCounts ? (
            <>
              {Number(pauseAllCount) > 0 ? (
                <Button
                  variant={status === 'on' ? 'inverse' : 'secondary'}
                  size="sm"
                  className="ab-btn"
                  onClick={onPause}
                  disabled={!onPause || controlsBusy || controlsRestricted}
                  aria-busy={controlAction === 'pause'}
                  aria-description={controlsDisabledReason || undefined}
                  title={controlsDisabledReason || undefined}
                >
                  {controlAction === 'pause' ? <Spinner size={11} /> : <Pause size={11} strokeWidth={2} />}
                  {controlAction === 'pause' ? 'Pausing…' : resolvedPauseLabel}
                </Button>
              ) : null}
              {Number(resumeAllCount) > 0 ? (
                <Button variant="primary" size="sm" className="ab-btn primary" onClick={onResume} disabled={!onResume || controlsBusy || controlsRestricted} aria-busy={controlAction === 'resume'} aria-description={controlsDisabledReason || undefined} title={controlsDisabledReason || undefined}>
                  {controlAction === 'resume' ? <Spinner size={11} /> : <Play size={11} strokeWidth={2} fill="currentColor" />}
                  {controlAction === 'resume' ? 'Resuming…' : resolvedResumeLabel}
                </Button>
              ) : null}
            </>
          ) : isWorkspaceOverride ? (
            <>
              {rolePaused ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className="ab-btn"
                  onClick={onResume}
                  disabled={!onResume || controlsBusy || controlsRestricted}
                  aria-description={controlsDisabledReason || undefined}
                  title={controlsDisabledReason || "Clear this role's own pause. It will run after all agents are resumed."}
                >
                  <Play size={11} strokeWidth={2} />
                  Resume role later
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  className="ab-btn"
                  onClick={onPause}
                  disabled={!onPause || controlsBusy || controlsRestricted}
                  aria-description={controlsDisabledReason || undefined}
                  title={controlsDisabledReason || 'Keep this role paused after all agents are resumed.'}
                >
                  <Pause size={11} strokeWidth={2} />
                  Pause this role
                </Button>
              )}
              {onTurnOff ? (
                <Button
                  variant="secondary"
                  size="sm"
                  iconOnly
                  className="ab-btn ic"
                  title={controlsDisabledReason || 'Turn off agent for this role'}
                  aria-label="Turn off agent"
                  onClick={onTurnOff}
                  disabled={controlsBusy || controlsRestricted}
                  aria-description={controlsDisabledReason || undefined}
                >
                  <Power size={13} strokeWidth={2} />
                </Button>
              ) : null}
            </>
          ) : status === 'on' ? (
            <>
              <Button
                variant="inverse"
                size="sm"
                className="ab-btn"
                onClick={onPause}
                disabled={!onPause || controlsBusy || controlsRestricted}
                aria-busy={controlAction === 'pause'}
                aria-description={controlsDisabledReason || (isWorkspaceControl && !onPause
                  ? 'Workspace owners can pause or resume all agents.'
                  : undefined)}
                title={controlsDisabledReason || (isWorkspaceControl && !onPause
                  ? 'Workspace owners can pause or resume all agents.'
                  : undefined)}
              >
                <Pause size={11} strokeWidth={2} />
                {resolvedPauseLabel}
              </Button>
              {onTurnOff ? (
                <Button
                  variant="inverse"
                  size="sm"
                  iconOnly
                  className="ab-btn ic"
                  title={controlsDisabledReason || 'Turn off agent for this role'}
                  aria-label="Turn off agent"
                  onClick={onTurnOff}
                  disabled={controlsBusy || controlsRestricted}
                  aria-description={controlsDisabledReason || undefined}
                >
                  <Power size={13} strokeWidth={2} />
                </Button>
              ) : null}
            </>
          ) : status === 'paused' ? (
            <>
              <Button
                variant="primary"
                size="sm"
                className="ab-btn primary"
                onClick={onResume}
                disabled={!onResume || controlsBusy || controlsRestricted}
                aria-busy={controlAction === 'resume'}
                aria-description={controlsDisabledReason || (isWorkspaceControl && !onResume
                  ? 'Workspace owners can pause or resume all agents.'
                  : undefined)}
                title={controlsDisabledReason || (isWorkspaceControl && !onResume
                  ? 'Workspace owners can pause or resume all agents.'
                  : undefined)}
              >
                <Play size={11} strokeWidth={2} fill="currentColor" />
                {resolvedResumeLabel}
              </Button>
              {onTurnOff ? (
                <Button
                  variant="secondary"
                  size="sm"
                  iconOnly
                  className="ab-btn ic"
                  title={controlsDisabledReason || 'Turn off agent for this role'}
                  aria-label="Turn off agent"
                  onClick={onTurnOff}
                  disabled={controlsBusy || controlsRestricted}
                  aria-description={controlsDisabledReason || undefined}
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
              title={controlsRestricted
                ? `View agent settings (read-only). ${controlsDisabledReason}`
                : 'Configure agent'}
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
  controlsDisabledReason = null,
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
                  controlsDisabledReason={controlsDisabledReason}
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
  const {
    isEnabled = null,
    fallbackTick = 'Agent is monitoring.',
    controlScope = 'role',
  } = options;
  const isWorkspaceControl = controlScope === 'workspace';
  const aggregateRunningCount = Number(
    status.running_role_count ?? status.active_role_count ?? 0,
  );
  const aggregatePausedCount = Number(
    status.local_paused_role_count ?? status.paused_role_count ?? 0,
  );
  // Backend returns `paused_at: datetime|null` (not a boolean). The org
  // aggregator (useAgentStatusOrg) also injects a derived `paused` boolean
  // for the org rollup — accept either.
  const isPaused = isWorkspaceControl
    ? (
        Boolean(status.workspace_paused)
        || Boolean(status.paused)
        || (aggregatePausedCount > 0 && aggregateRunningCount === 0)
      )
    : (status.paused != null ? Boolean(status.paused) : Boolean(status.paused_at));
  const enabled = isEnabled != null
    ? Boolean(isEnabled)
    : Boolean(status.enabled);
  const rawPendingBreakdown = status.pending_breakdown;
  const pendingBreakdown = rawPendingBreakdown && typeof rawPendingBreakdown === 'object'
    ? {
        total: nonNegativeCount(rawPendingBreakdown.total),
        decisions: nonNegativeCount(rawPendingBreakdown.decisions),
        questions: nonNegativeCount(rawPendingBreakdown.questions),
      }
    : null;
  const pendingTotal = pendingBreakdown?.total != null
    ? pendingBreakdown.total
    : (nonNegativeCount(status.pending_decisions) || 0);
  const workspacePaused = Boolean(status.workspace_paused);
  const hasLocalRoleState = Object.prototype.hasOwnProperty.call(status, 'role_paused_at');
  const rolePaused = hasLocalRoleState
    ? Boolean(status.role_paused_at)
    : (!workspacePaused && isPaused);
  const pausedAt = isWorkspaceControl
    ? (status.workspace_paused_at || status.paused_at)
    : status.paused_at;
  const pausedReason = isWorkspaceControl
    ? (status.workspace_paused_reason || status.paused_reason)
    : status.paused_reason;
  const pausedBy = isWorkspaceControl
    ? status.workspace_paused_by
    : status.paused_by;
  const rawBudgetCents = Number(
    status.monthly_budget_cents ?? status.org_budget_cap_cents ?? 0,
  );
  return {
    // ON visual only when agentic_mode_enabled AND not auto-paused.
    on: enabled && !isPaused,
    // Both human and automatic soft pauses keep agent mode enabled and set
    // paused_at; paused_reason distinguishes their treatment and copy.
    // A workspace hold is meaningful even when no roles are currently enabled:
    // it remains an explicit control that an owner may need to clear before a
    // newly enabled role can run. Role-local pauses still require an enabled role.
    paused: isWorkspaceControl ? isPaused : (enabled && isPaused),
    pending: pendingTotal,
    pendingBreakdown,
    spentCents: Number(status.monthly_spent_cents ?? status.org_budget_spent_cents ?? 0),
    budgetCents: rawBudgetCents > 0 ? rawBudgetCents : (isWorkspaceControl ? 0 : 5000),
    tick: formatTick(status) || fallbackTick,
    inFlight: Boolean(status.current_run),
    // Actual reason the orchestrator set — surfaces "per-cycle token
    // budget exhausted" / "monthly USD cap reached" / etc. instead of a
    // hardcoded blanket message.
    pausedAt: pausedAt || null,
    pausedReason: pausedReason || null,
    pausedBy: pausedBy || null,
    controlScope,
    pauseScope: status.pause_scope || (isWorkspaceControl && workspacePaused ? 'workspace' : null),
    workspacePaused,
    workspacePausedAt: status.workspace_paused_at || null,
    workspacePausedBy: status.workspace_paused_by || null,
    workspaceControlVersion: status.workspace_control_version ?? null,
    rolePaused,
    rolePausedAt: status.role_paused_at || null,
    rolePausedReason: status.role_paused_reason || null,
    rolePausedBy: status.role_paused_by || null,
    runningRoleCount: aggregateRunningCount,
    localPausedRoleCount: aggregatePausedCount,
    bootstrapStatus: status.bootstrap_status || null,
    bootstrapError: status.bootstrap_error || null,
  };
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
  if (status.bootstrap_status === 'starting') {
    return 'Starting first autonomous cycle…';
  }
  if (status.bootstrap_status === 'failed') {
    return status.bootstrap_error
      ? `Startup failed — ${status.bootstrap_error}`
      : 'Startup failed — retry Turn on.';
  }
  return tickFromActivity(status.last_activity)
    || tickFromCurrentRun(status.current_run)
    || 'Idle · waiting for new candidates.';
};

export { useAgentStatus };
export default AgentHeader;
