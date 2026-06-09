import React from 'react';
import { MessageSquare, Plus, Sparkles, Trash2 } from 'lucide-react';

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

const formatTime = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
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
      {agentAttention > 0 ? <span className="cp-modeswitch-badge">{fmtCount(agentAttention)}</span> : null}
    </button>
  </div>
);

const AskList = ({ conversations, activeId, onSelect, onNew, onDelete }) => {
  const groups = groupByRecency(conversations);
  const Group = ({ label, rows }) => {
    if (!rows.length) return null;
    return (
      <div className="cp-group">
        <div className="cp-group-h">{label}</div>
        {rows.map((r) => (
          <div key={r.id} style={{ position: 'relative' }}>
            <button
              type="button"
              className={`cp-conv ${r.id === activeId ? 'cp-active' : ''}`}
              onClick={() => onSelect(r.id)}
            >
              <div className="cp-conv-q">{r.title || 'New conversation'}</div>
              <div className="cp-conv-meta">
                {r.message_count} msg · {formatTime(r.updated_at || r.created_at)}
              </div>
            </button>
            <button
              type="button"
              title="Delete conversation"
              className="cp-conv-del"
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
        <button type="button" className="cp-new-chat" onClick={onNew}>
          <span className="cp-plus">
            <Plus size={11} strokeWidth={3} />
          </span>
          New conversation
        </button>
      </div>
      <div className="cp-side-list">
        <Group label="Today" rows={groups.today} />
        <Group label="Yesterday" rows={groups.yesterday} />
        <Group label="This week" rows={groups.week} />
        <Group label="Older" rows={groups.older} />
        {!conversations?.length ? (
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

const AgentList = ({ agents, activeRoleId, onSelectAgent }) => {
  const sections = AGENT_GROUP_ORDER
    .map((key) => ({ key, label: AGENT_GROUP_LABELS[key], rows: (agents || []).filter((a) => (a.group || 'active') === key) }))
    .filter((s) => s.rows.length > 0);

  const renderAgent = (a) => {
    const questions = (a.unread_messages || 0) + (a.open_questions || 0);
    const status = a.agent_paused ? 'paused' : a.agent_enabled ? 'on' : 'off';
    const preview = a.agent_paused
      ? `Paused · ${a.agent_paused_reason || 'budget reached'}`
      : a.last_message_preview
        || (a.agent_enabled ? 'No messages yet' : 'Agent off — tap to set up');
    return (
      <button
        key={a.role_id}
        type="button"
        className={`cp-agent ${a.role_id === activeRoleId ? 'cp-active' : ''}`}
        onClick={() => onSelectAgent(a.role_id)}
        title={a.role_name}
      >
        <span className={`cp-agent-stat cp-agent-stat-${status}`} aria-hidden="true">
          {status === 'on' ? <Sparkles size={12} strokeWidth={2} /> : <span className="cp-agent-dot" />}
        </span>
        <span className="cp-agent-body">
          <span className="cp-agent-role">{a.role_name}</span>
          <span className="cp-agent-preview">{preview}</span>
        </span>
        {questions > 0 ? (
          <span className="cp-agent-badge" title={`${questions} awaiting your reply`}>
            {fmtCount(questions)}
          </span>
        ) : null}
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

const Sidebar = ({
  mode = 'ask',
  onModeChange,
  // Ask mode
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  // Agents mode
  agents = [],
  activeRoleId,
  onSelectAgent,
  agentAttention = 0,
}) => (
  <aside className="cp-side">
    <div className="cp-side-top">
      <span className="cp-side-title">Chat</span>
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
      />
    )}
  </aside>
);

export default Sidebar;
