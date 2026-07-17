// Left rail listing the org's roles + their agents, with two notification
// indicators. Pure presentation — HomePage owns the polled conversation list,
// selection, and bulk-select state. The list is ONE flat list under "All roles"
// (matching the home-preview): agents you're actively running float to the top,
// then everything else in the backend's order. A multi-select mode lets you
// message several agents at once.

import { Check, CheckSquare, Layers, MessageSquare, Pause, Sparkles } from 'lucide-react';

import { formatAgentPauseStatus } from '../../../shared/agentPauseCopy';
import { AgentLoop, MotionAttentionBadge } from '../../../shared/motion';

const fmtCount = (n) => (n > 999 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
const fmtUsd = (cents) => `$${((cents || 0) / 100).toFixed(2)}`;

// The backend hands the agents back already sorted by group
// (on_paused → previously_on → starred → active) and by recency within each.
// We render them as a single flat list in that order — no group headers — but
// still float the agents that are actually running to the very top so the ones
// you're steering sit first.
const GROUP_ORDER = ['on_paused', 'previously_on', 'starred', 'active'];

const agentPresentation = (agent) => {
  const enabled = Boolean(agent.agent_enabled);
  const hasEffectiveState = Object.prototype.hasOwnProperty.call(agent, 'agent_effective_paused');
  const workspaceHeld = enabled && Boolean(
    agent.workspace_paused || agent.agent_pause_scope === 'workspace',
  );
  const rolePaused = Object.prototype.hasOwnProperty.call(agent, 'role_paused')
    ? Boolean(agent.role_paused)
    : Boolean(agent.agent_pause_scope === 'role' || (!workspaceHeld && agent.agent_paused));
  const effectivePaused = enabled && (hasEffectiveState
    ? Boolean(agent.agent_effective_paused)
    : Boolean(agent.agent_paused || workspaceHeld || rolePaused));
  const state = workspaceHeld && effectivePaused
    ? 'held'
    : effectivePaused
      ? 'paused'
      : enabled
        ? 'on'
        : 'off';

  if (state === 'held') {
    const actor = agent.workspace_paused_by;
    const heldCopy = actor?.name
      ? `Held · All agents paused by ${actor.name}${actor.is_current_user ? ' (you)' : ''}`
      : 'Held by workspace pause';
    return {
      state,
      rolePaused,
      preview: rolePaused
        ? `${heldCopy} · Role stays paused after resume`
        : heldCopy,
    };
  }
  if (state === 'paused') {
    return {
      state,
      rolePaused,
      preview: formatAgentPauseStatus(
        agent.role_paused_reason || agent.agent_paused_reason,
      ),
    };
  }
  return { state, rolePaused, preview: null };
};

export function AgentSidebar({
  agents = [],
  activeRoleId,
  onSelect,
  bulkMode = false,
  bulkSelected,
  onToggleBulkMode,
  onToggleSelected,
}) {
  const runningCount = agents.filter((a) => agentPresentation(a).state === 'on').length;
  const heldCount = agents.filter((a) => agentPresentation(a).state === 'held').length;
  const selectedCount = bulkSelected ? bulkSelected.size : 0;

  // One flat list. Order the agents by their backend group bucket
  // (on_paused → previously_on → starred → active), preserving the backend's
  // within-group order, then float the agents that are actually running to the
  // very top. Stable sort throughout, so recency / pending-count order survives.
  const groupRank = (a) => {
    const idx = GROUP_ORDER.indexOf(a.group || 'active');
    return idx === -1 ? GROUP_ORDER.length : idx;
  };
  const orderedAgents = agents
    .map((a, i) => ({ a, i }))
    .sort((x, y) => {
      const running = (z) => (agentPresentation(z).state === 'on' ? 0 : 1);
      return (running(x.a) - running(y.a)) || (groupRank(x.a) - groupRank(y.a)) || (x.i - y.i);
    })
    .map(({ a }) => a);

  const renderRow = (a) => {
    // Two distinct indicators:
    //  • questions (purple) — the agent is asking you something:
    //    open questions + unread agent messages.
    //  • decisions (muted)  — the bulk pending-decision queue (same as
    //    the feed's "Pending N").
    const questions = (a.unread_messages || 0) + (a.open_questions || 0);
    const decisions = a.pending_decisions || 0;
    const presentation = agentPresentation(a);
    const rowStatus = presentation.state === 'held' ? 'paused' : presentation.state;
    const selected = bulkMode && bulkSelected?.has(a.role_id);
    // Preview line (home-preview `.aprev`): paused reason → the agent's last
    // activity → a "{n} decisions waiting" summary when it has a queue but no
    // fresher message → an idle/off fallback. All real fields — no fabrication.
    const preview = presentation.preview
      || a.last_message_preview
        || (decisions > 0
          ? `${fmtCount(decisions)} decision${decisions === 1 ? '' : 's'} waiting`
          : a.agent_enabled
            ? 'No messages yet'
            : 'Agent off — tap to set up');
    return (
      <button
        key={a.role_id}
        type="button"
        className={`ac-agent ac-${rowStatus} ${presentation.state === 'held' ? 'is-workspace-held' : ''} ${!bulkMode && a.role_id === activeRoleId ? 'is-active' : ''} ${selected ? 'is-selected' : ''}`}
        data-agent-state={presentation.state}
        aria-pressed={bulkMode ? Boolean(selected) : a.role_id === activeRoleId}
        onClick={() => (bulkMode ? onToggleSelected?.(a.role_id) : onSelect?.(a.role_id))}
        title={!bulkMode && a.role_id === activeRoleId
          ? `${a.role_name} — click to deselect (view all roles)`
          : `${a.role_name} — ${preview}`}
      >
        {/* Top row: status glyph + role name (home-preview `.arow`). */}
        <span className="ac-agent-top">
          {bulkMode && (
            <span className={`ac-check ${selected ? 'on' : ''}`} aria-hidden="true">
              {selected ? <Check size={11} strokeWidth={3} /> : null}
            </span>
          )}
          {rowStatus === 'on' ? (
            <AgentLoop kind="flow" className="ac-stat ac-stat-on" aria-hidden="true">
              <Sparkles size={13} strokeWidth={2} />
            </AgentLoop>
          ) : (
            <span className={`ac-stat ac-stat-${rowStatus}`} aria-hidden="true">
              {rowStatus === 'paused'
                ? <Pause size={11} strokeWidth={2} fill="currentColor" />
                : <span className="ac-stat-dot" />}
            </span>
          )}
          <span className="ac-agent-role">{a.role_name}</span>
        </span>
        {/* Everything below the top row is indented to align past the glyph
            (home-preview `.aprev` / `.abadges` / `.abud`, margin-left:33px). */}
        <span className="ac-agent-sub">
          <span className="ac-agent-preview">{preview}</span>
          <span className="ac-agent-badges">
              <MotionAttentionBadge
                value={questions}
                format={fmtCount}
                prefix={<MessageSquare size={10} aria-hidden="true" />}
                className="ac-badge-q"
                title={`${questions} awaiting your reply`}
                aria-label={`${questions} agent update${questions === 1 ? '' : 's'} awaiting your reply`}
              />
              {decisions > 0 && (
                <span className="ac-badge-d" title={`${decisions} pending decisions`}>
                  {fmtCount(decisions)} pending
                </span>
              )}
          </span>
          {a.budget_cap_cents > 0 && (
            <span
              className="ac-budget"
              title={`Budget ${fmtUsd(a.budget_spent_cents)} of ${fmtUsd(a.budget_cap_cents)} this month`}
            >
              <span
                className="ac-budget-fill"
                style={{ width: `${Math.min(100, Math.round((a.budget_spent_cents / a.budget_cap_cents) * 100))}%` }}
              />
            </span>
          )}
        </span>
      </button>
    );
  };

  return (
    <aside className="ac-sidebar">
      <div className="ac-sidebar-head">
        <div className="ac-sidebar-head-l">
          <span className="ac-kicker">Your agents</span>
          <span className="ac-sidebar-count">
            {heldCount > 0
              ? `${runningCount} running · ${heldCount} held`
              : `${runningCount} active`}
          </span>
        </div>
        {agents.length > 0 && onToggleBulkMode && (
          <button
            type="button"
            className={`ac-bulk-toggle ${bulkMode ? 'on' : ''}`}
            aria-pressed={bulkMode}
            onClick={onToggleBulkMode}
            title={bulkMode ? 'Cancel multi-select' : 'Message several agents at once'}
          >
            {bulkMode ? (
              <>Cancel{selectedCount > 0 ? ` · ${selectedCount}` : ''}</>
            ) : (
              <><CheckSquare size={12} /> Select</>
            )}
          </button>
        )}
      </div>
      <div className="ac-agent-list">
        {agents.length === 0 ? (
          <div className="ac-empty">No live roles yet. Publish a role to chat with (or activate) its agent.</div>
        ) : (
          <>
            {/* Always-visible scope reset. Selecting an agent filters the whole
                page to that role; this row is the one-click way back to every
                role's queue (the active agent also toggles off when re-clicked).
                Hidden in bulk-select mode, where scope doesn't apply. */}
            {!bulkMode && (
              <button
                type="button"
                className={`ac-allroles ${activeRoleId ? '' : 'is-active'}`}
                aria-pressed={!activeRoleId}
                onClick={() => onSelect?.(null)}
                title="View every role's queue — clears the agent filter"
              >
                <span className="ac-allroles-ic" aria-hidden="true">
                  <Layers size={13} strokeWidth={2} />
                </span>
                <span className="ac-allroles-label">All roles</span>
                {activeRoleId ? (
                  <span className="ac-allroles-hint">Clear</span>
                ) : (
                  <span className="ac-allroles-check" aria-hidden="true">
                    <Check size={12} strokeWidth={3} />
                  </span>
                )}
              </button>
            )}
            {orderedAgents.map(renderRow)}
          </>
        )}
      </div>
    </aside>
  );
}
