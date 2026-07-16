import React from 'react';
import { MessageSquare, Pause, Plus, Sparkles, Trash2, X } from 'lucide-react';
import { AgentLoop, MotionAttentionBadge } from '../../shared/motion';

import { formatAgentPauseStatus } from '../../shared/agentPauseCopy';
import { Button } from '../../shared/ui/TaaliPrimitives';

const groupByRecency = (rows) => {
  const groups = { today: [], yesterday: [], week: [], older: [] };
  if (!rows?.length) return groups;
  const now = new Date();
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const today0 = startOf(now);
  const yesterday0 = new Date(today0.getTime() - 86_400_000);
  const week0 = new Date(today0.getTime() - 7 * 86_400_000);
  for (const r of rows) {
    const ts = new Date(r.updated_at || r.created_at);
    if (ts >= today0) groups.today.push(r);
    else if (ts >= yesterday0) groups.yesterday.push(r);
    else if (ts >= week0) groups.week.push(r);
    else groups.older.push(r);
  }
  return groups;
};

// search-preview shows a compact RELATIVE age in the conversation meta
// ("2m", "40m", "1h", "1d", "3d"), not an absolute clock time. Mirror that
// from the real updated_at/created_at timestamp — sub-minute reads "now".
const formatTime = (iso) => {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return 'now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d`;
  const wks = Math.round(days / 7);
  if (wks < 5) return `${wks}w`;
  const mos = Math.round(days / 30);
  if (mos < 12) return `${mos}mo`;
  return `${Math.round(days / 365)}y`;
};

const fmtCount = (n) => (n > 999 ? `${(n / 1000).toFixed(1)}k` : `${n}`);

// The Ask ↔ Agents switch. "Ask" is the regular Taali (search-AI) chats;
// "Agents" surfaces each role's autonomous-agent thread, the same one the
// Home dock drives.
const ModeToggle = ({ mode, onModeChange, agentAttention = 0 }) => (
  <div className="cp-modeswitch" role="tablist" aria-label="Chat mode">
    <button
      type="button"
      role="tab"
      aria-selected={mode === 'ask'}
      className={`cp-modeswitch-btn ${mode === 'ask' ? 'on' : ''}`}
      onClick={() => onModeChange('ask')}
    >
      <MessageSquare size={13} /> Ask
    </button>
    <button
      type="button"
      role="tab"
      aria-selected={mode === 'agents'}
      className={`cp-modeswitch-btn ${mode === 'agents' ? 'on' : ''}`}
      onClick={() => onModeChange('agents')}
    >
      <Sparkles size={13} /> Agents
      <MotionAttentionBadge
        value={agentAttention}
        format={fmtCount}
        className="cp-modeswitch-badge"
        aria-label={`${agentAttention} agent update${agentAttention === 1 ? '' : 's'} awaiting you`}
      />
    </button>
  </div>
);

const AskList = ({ conversations, activeId, onSelect, onNew, onDelete, listError = false }) => {
  const groups = groupByRecency(conversations);
  const Group = ({ label, rows }) => {
    if (!rows.length) return null;
    return (
      <div className="cp-group">
        <div className="cp-group-h">{label}</div>
        {rows.map((r) => (
          <div key={r.id} className="cp-conv-row">
            <button
              type="button"
              className={`cp-conv ${r.id === activeId ? 'cp-active' : ''}`}
              onClick={() => onSelect(r.id)}
            >
              <div className="cp-conv-q">{r.title || 'New conversation'}</div>
              <div className="cp-conv-meta">
                {r.message_count} message{r.message_count === 1 ? '' : 's'} · {formatTime(r.updated_at || r.created_at)}
              </div>
            </button>
            <button
              type="button"
              title="Delete conversation"
              className="taali-icon-btn taali-icon-btn-danger taali-icon-btn-sm cp-conv-del"
              aria-label="Delete conversation"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(r.id);
              }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>
    );
  };

  return (
    <>
      <div className="cp-side-head">
        <Button variant="secondary" fullWidth className="cp-new-chat" onClick={onNew}>
          <span className="cp-plus">
            <Plus size={11} strokeWidth={3} />
          </span>
          New conversation
        </Button>
      </div>
      <div className="cp-side-list">
        <Group label="Today" rows={groups.today} />
        <Group label="Yesterday" rows={groups.yesterday} />
        <Group label="This week" rows={groups.week} />
        <Group label="Older" rows={groups.older} />
        {!conversations?.length && listError ? (
          // Only show the "get started" empty state after a *successful*
          // fetch returns zero rows — a failed fetch keeps whatever we had
          // and shows a quiet retry note instead.
          <div className="cp-group">
            <div className="cp-side-hint">Couldn’t load your conversations — retrying.</div>
          </div>
        ) : !conversations?.length ? (
          <div className="cp-group">
            <div className="cp-group-h">Get started</div>
            <div className="cp-side-hint">Your conversations will show up here.</div>
          </div>
        ) : null}
      </div>
    </>
  );
};

// Agent-first sections — each role appears once in the first that fits. Same
// `group` the backend computes + the Home rail uses.
const AGENT_GROUP_ORDER = ['on_paused', 'previously_on', 'starred', 'active'];
const AGENT_GROUP_LABELS = {
  on_paused: 'Agent on / paused',
  previously_on: 'Previously on',
  starred: 'Starred',
  active: 'Active roles',
};

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
      ? `Held · Workspace paused by ${actor.name}${actor.is_current_user ? ' (you)' : ''}`
      : 'Held by workspace pause';
    return {
      state,
      preview: rolePaused
        ? `${heldCopy} · Role stays paused after resume`
        : heldCopy,
    };
  }
  if (state === 'paused') {
    return {
      state,
      preview: formatAgentPauseStatus(
        agent.role_paused_reason || agent.agent_paused_reason,
      ),
    };
  }
  return { state, preview: null };
};

const AgentList = ({ agents, activeRoleId, onSelectAgent }) => {
  const sections = AGENT_GROUP_ORDER
    .map((key) => ({ key, label: AGENT_GROUP_LABELS[key], rows: (agents || []).filter((a) => (a.group || 'active') === key) }))
    .filter((s) => s.rows.length > 0);

  // Flat-list agent row, mirroring the Home rail (`.ac-agent`): a top row of
  // status glyph + role name, then a sub-column (preview · badges · budget bar)
  // indented to line up under the role name. Same status vocabulary + the two
  // indicators (purple questions pill, muted pending pill) the Home rail uses.
  const renderAgent = (a) => {
    const questions = (a.unread_messages || 0) + (a.open_questions || 0);
    const decisions = a.pending_decisions || 0;
    const presentation = agentPresentation(a);
    const status = presentation.state === 'held' ? 'paused' : presentation.state;
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
        className={`cp-agent cp-agent-${status} ${presentation.state === 'held' ? 'is-workspace-held' : ''} ${a.role_id === activeRoleId ? 'cp-active' : ''}`}
        data-agent-state={presentation.state}
        aria-pressed={a.role_id === activeRoleId}
        onClick={() => onSelectAgent(a.role_id)}
        title={`${a.role_name} — ${preview}`}
      >
        <span className="cp-agent-top">
          {status === 'on' ? (
            <AgentLoop kind="flow" className="cp-agent-stat cp-agent-stat-on" aria-hidden="true">
              <Sparkles size={13} strokeWidth={2} />
            </AgentLoop>
          ) : (
            <span className={`cp-agent-stat cp-agent-stat-${status}`} aria-hidden="true">
              {status === 'paused'
                ? <Pause size={11} strokeWidth={2} fill="currentColor" />
                : <span className="cp-agent-dot" />}
            </span>
          )}
          <span className="cp-agent-role">{a.role_name}</span>
        </span>
        <span className="cp-agent-sub">
          <span className="cp-agent-preview">{preview}</span>
          <span className="cp-agent-badges">
              <MotionAttentionBadge
                value={questions}
                format={fmtCount}
                prefix={<MessageSquare size={10} aria-hidden="true" />}
                className="cp-agent-badge-q"
                title={`${questions} awaiting your reply`}
                aria-label={`${questions} agent update${questions === 1 ? '' : 's'} awaiting your reply`}
              />
              {decisions > 0 && (
                <span className="cp-agent-badge-d" title={`${decisions} pending decisions`}>
                  {fmtCount(decisions)} pending
                </span>
              )}
          </span>
          {a.budget_cap_cents > 0 && (
            <span className="cp-agent-budget" title="Budget this month">
              <span
                className="cp-agent-budget-fill"
                style={{ width: `${Math.min(100, Math.round(((a.budget_spent_cents || 0) / a.budget_cap_cents) * 100))}%` }}
              />
            </span>
          )}
        </span>
      </button>
    );
  };

  return (
    <div className="cp-side-list">
      {!agents?.length ? (
        <div className="cp-group">
          <div className="cp-group-h">Your agents</div>
          <div className="cp-side-hint">
            No live roles yet. Publish a role to chat with (or activate) its agent.
          </div>
        </div>
      ) : (
        sections.map((sec) => (
          <div key={sec.key} className="cp-group">
            <div className="cp-group-h">{sec.label}</div>
            {sec.rows.map(renderAgent)}
          </div>
        ))
      )}
    </div>
  );
};

const Sidebar = React.forwardRef(function Sidebar({
  id,
  mobileDrawer = false,
  mobileDrawerOpen = false,
  onRequestClose,
  mode = 'ask',
  onModeChange,
  // Ask mode
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  // Agents mode
  conversationsError = false,
  agents = [],
  activeRoleId,
  onSelectAgent,
  agentAttention = 0,
}, ref) {
  const drawerClosed = mobileDrawer && !mobileDrawerOpen;
  return (
  <aside
    ref={ref}
    id={id}
    className="cp-side"
    role={mobileDrawer ? 'dialog' : undefined}
    aria-label="Chat navigation"
    aria-modal={mobileDrawer && mobileDrawerOpen ? 'true' : undefined}
    aria-hidden={drawerClosed ? 'true' : undefined}
    inert={drawerClosed ? '' : undefined}
    tabIndex={mobileDrawer ? -1 : undefined}
  >
    <div className="cp-side-top">
      <span className="cp-side-title">Chat</span>
      {mobileDrawer ? (
        <button
          type="button"
          className="cp-side-close"
          onClick={onRequestClose}
          aria-label="Close chat navigation"
        >
          <X size={16} aria-hidden="true" />
        </button>
      ) : null}
      <ModeToggle mode={mode} onModeChange={onModeChange} agentAttention={agentAttention} />
    </div>
    {mode === 'agents' ? (
      <AgentList agents={agents} activeRoleId={activeRoleId} onSelectAgent={onSelectAgent} />
    ) : (
      <AskList
        conversations={conversations}
        activeId={activeId}
        onSelect={onSelect}
        onNew={onNew}
        onDelete={onDelete}
        listError={conversationsError}
      />
    )}
  </aside>
  );
});

export default Sidebar;
