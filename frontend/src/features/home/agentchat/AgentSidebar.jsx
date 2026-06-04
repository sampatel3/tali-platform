// Left rail listing the org's roles + their agents, with two notification
// indicators. Pure presentation — HomePage owns the polled conversation list,
// selection, and bulk-select state. Lists every LIVE role (not just agent-on
// ones) so you can click an off role and activate it from here; a multi-select
// mode lets you message several agents at once.

import { Check, CheckSquare, MessageSquare } from 'lucide-react';

const fmtCount = (n) => (n > 999 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
const fmtUsd = (cents) => `$${((cents || 0) / 100).toFixed(2)}`;

export function AgentSidebar({
  agents = [],
  activeRoleId,
  onSelect,
  bulkMode = false,
  bulkSelected,
  onToggleBulkMode,
  onToggleSelected,
}) {
  const activeCount = agents.filter((a) => a.agent_enabled).length;
  const selectedCount = bulkSelected ? bulkSelected.size : 0;
  return (
    <aside className="ac-sidebar">
      <div className="ac-sidebar-head">
        <div className="ac-sidebar-head-l">
          <span className="ac-kicker">Your agents</span>
          <span className="ac-sidebar-count">{activeCount} active</span>
        </div>
        {agents.length > 0 && onToggleBulkMode && (
          <button
            type="button"
            className={`ac-bulk-toggle ${bulkMode ? 'on' : ''}`}
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
          agents.map((a) => {
            // Two distinct indicators:
            //  • questions (purple) — the agent is asking you something:
            //    open questions + unread agent messages.
            //  • decisions (muted)  — the bulk pending-decision queue (same as
            //    the feed's "Pending N").
            const questions = (a.unread_messages || 0) + (a.open_questions || 0);
            const decisions = a.pending_decisions || 0;
            const dotClass = a.agent_paused ? 'is-paused' : a.agent_enabled ? 'is-on' : '';
            const selected = bulkMode && bulkSelected?.has(a.role_id);
            const preview = a.agent_paused
              ? `Paused · ${a.agent_paused_reason || 'budget reached'}`
              : a.last_message_preview
                || (a.agent_enabled ? 'No messages yet' : 'Agent off — tap to set up');
            return (
              <button
                key={a.role_id}
                className={`ac-agent ${!bulkMode && a.role_id === activeRoleId ? 'is-active' : ''} ${selected ? 'is-selected' : ''} ${!a.agent_enabled && !a.agent_paused ? 'is-off' : ''}`}
                onClick={() => (bulkMode ? onToggleSelected?.(a.role_id) : onSelect?.(a.role_id))}
                title={a.role_name}
              >
                {bulkMode && (
                  <span className={`ac-check ${selected ? 'on' : ''}`} aria-hidden="true">
                    {selected ? <Check size={11} strokeWidth={3} /> : null}
                  </span>
                )}
                <span className={`ac-dot ${dotClass}`} />
                <span className="ac-agent-body">
                  <span className="ac-agent-role">{a.role_name}</span>
                  <span className="ac-agent-preview">{preview}</span>
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
