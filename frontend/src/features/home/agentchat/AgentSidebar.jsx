// Left rail listing the org's active agents with two notification indicators.
// Pure presentation — HomePage owns the polled conversation list and selection.

import { MessageSquare } from 'lucide-react';

const fmtCount = (n) => (n > 999 ? `${(n / 1000).toFixed(1)}k` : `${n}`);

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
            // Two distinct indicators:
            //  • questions (purple) — the agent is asking you something:
            //    open questions + unread agent messages.
            //  • decisions (muted)  — the bulk pending-decision queue (same as
            //    the feed's "Pending N").
            const questions = (a.unread_messages || 0) + (a.open_questions || 0);
            const decisions = a.pending_decisions || 0;
            const dotClass = a.agent_paused ? 'is-paused' : a.agent_enabled ? 'is-on' : '';
            return (
              <button
                key={a.role_id}
                className={`ac-agent ${a.role_id === activeRoleId ? 'is-active' : ''}`}
                onClick={() => onSelect?.(a.role_id)}
                title={a.role_name}
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
                  {questions > 0 && (
                    <span className="ac-badge-q" title={`${questions} awaiting your reply`}>
                      <MessageSquare size={10} /> {questions}
                    </span>
                  )}
                  {decisions > 0 && (
                    <span className="ac-badge-d" title={`${decisions} pending decisions`}>
                      {fmtCount(decisions)}
                    </span>
                  )}
                </span>
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
}
