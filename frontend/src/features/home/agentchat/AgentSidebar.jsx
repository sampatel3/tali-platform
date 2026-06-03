// Left rail listing the org's active agents with notification badges. Pure
// presentation — HomePage owns the polled conversation list and selection.

import { ChevronRight } from 'lucide-react';

export function AgentSidebar({ agents = [], activeRoleId, onSelect }) {
  const activeCount = agents.filter((a) => a.agent_enabled).length;
  return (
    <aside className="ac-sidebar">
      <div className="ac-sidebar-head">
        <span className="ac-kicker">Your agents</span>
        <span className="ac-sidebar-count">{activeCount} active</span>
      </div>
      <div className="ac-agent-list">
        {agents.length === 0 ? (
          <div className="ac-empty">No active agents yet. Turn an agent on for a role to chat with it.</div>
        ) : (
          agents.map((a) => {
            const badge = a.attention ?? (a.unread_messages || 0) + (a.open_questions || 0) + (a.pending_decisions || 0);
            const dotClass = a.agent_paused ? 'is-paused' : a.agent_enabled ? 'is-on' : '';
            return (
              <button
                key={a.role_id}
                className={`ac-agent ${a.role_id === activeRoleId ? 'is-active' : ''}`}
                onClick={() => onSelect?.(a.role_id)}
              >
                <span className={`ac-dot ${dotClass}`} />
                <span className="ac-agent-body">
                  <span className="ac-agent-role">{a.role_name}</span>
                  <span className="ac-agent-preview">
                    {a.agent_paused
                      ? `Paused · ${a.agent_paused_reason || 'budget reached'}`
                      : a.last_message_preview || 'No messages yet'}
                  </span>
                </span>
                <span className="ac-agent-meta">
                  {badge > 0 ? <span className="ac-badge-count">{badge}</span> : null}
                  <ChevronRight size={14} style={{ color: 'var(--ink-soft)' }} />
                </span>
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
}
