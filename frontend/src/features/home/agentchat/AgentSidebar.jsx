// Left rail listing the org's roles + their agents, with two notification
// indicators. Pure presentation — HomePage owns the polled conversation list,
// selection, and bulk-select state. The list is split into agent-first sections
// (each role appears once, in the first that fits): agents on/paused, then ones
// that ran before but are off now, then starred, then other active roles — so
// the agents you're actively running sit at the top. A multi-select mode lets
// you message several agents at once.

import { Check, CheckSquare, Layers, MessageSquare, Pause, Sparkles } from 'lucide-react';

const fmtCount = (n) => (n > 999 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
const fmtUsd = (cents) => `$${((cents || 0) / 100).toFixed(2)}`;

// Section order + labels. The backend already sorts items into this order (and
// by recency within each), so we just split on the `group` field.
const GROUP_ORDER = ['on_paused', 'previously_on', 'starred', 'active'];
const GROUP_LABELS = {
  on_paused: 'Agent on / paused',
  previously_on: 'Previously on',
  starred: 'Starred',
  active: 'Active roles',
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
  const activeCount = agents.filter((a) => a.agent_enabled).length;
  const selectedCount = bulkSelected ? bulkSelected.size : 0;

  // Within the "on / paused" section, float the agents that are actually
  // running above the paused ones. Stable sort, so the backend's within-group
  // order (pending count / recency) is preserved inside each subgroup.
  const activeFirst = (rows) =>
    [...rows].sort((a, b) => {
      const rank = (x) => (x.agent_enabled && !x.agent_paused ? 0 : 1);
      return rank(a) - rank(b);
    });

  const sections = GROUP_ORDER
    .map((key) => {
      const rows = agents.filter((a) => (a.group || 'active') === key);
      return { key, label: GROUP_LABELS[key], rows: key === 'on_paused' ? activeFirst(rows) : rows };
    })
    .filter((s) => s.rows.length > 0);

  const renderRow = (a) => {
    // Two distinct indicators:
    //  • questions (purple) — the agent is asking you something:
    //    open questions + unread agent messages.
    //  • decisions (muted)  — the bulk pending-decision queue (same as
    //    the feed's "Pending N").
    const questions = (a.unread_messages || 0) + (a.open_questions || 0);
    const decisions = a.pending_decisions || 0;
    const rowStatus = a.agent_paused ? 'paused' : a.agent_enabled ? 'on' : 'off';
    const selected = bulkMode && bulkSelected?.has(a.role_id);
    const preview = a.agent_paused
      ? `Paused · ${a.agent_paused_reason || 'budget reached'}`
      : a.last_message_preview
        || (a.agent_enabled ? 'No messages yet' : 'Agent off — tap to set up');
    return (
      <button
        key={a.role_id}
        className={`ac-agent ac-${rowStatus} ${!bulkMode && a.role_id === activeRoleId ? 'is-active' : ''} ${selected ? 'is-selected' : ''}`}
        onClick={() => (bulkMode ? onToggleSelected?.(a.role_id) : onSelect?.(a.role_id))}
        title={!bulkMode && a.role_id === activeRoleId ? `${a.role_name} — click to deselect (view all roles)` : a.role_name}
      >
        {bulkMode && (
          <span className={`ac-check ${selected ? 'on' : ''}`} aria-hidden="true">
            {selected ? <Check size={11} strokeWidth={3} /> : null}
          </span>
        )}
        <span className={`ac-stat ac-stat-${rowStatus}`} aria-hidden="true">
          {rowStatus === 'on' ? <Sparkles size={13} strokeWidth={2} />
            : rowStatus === 'paused' ? <Pause size={11} strokeWidth={2} fill="currentColor" />
            : <span className="ac-stat-dot" />}
        </span>
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
  };

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
          <>
            {/* Always-visible scope reset. Selecting an agent filters the whole
                page to that role; this row is the one-click way back to every
                role's queue (the active agent also toggles off when re-clicked).
                Hidden in bulk-select mode, where scope doesn't apply. */}
            {!bulkMode && (
              <button
                type="button"
                className={`ac-allroles ${activeRoleId ? '' : 'is-active'}`}
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
            {sections.map((sec) => (
              <div key={sec.key} className="ac-agent-group">
                <div className="ac-group-head">
                  <span>{sec.label}</span>
                  <span className="ac-group-count">{sec.rows.length}</span>
                </div>
                {sec.rows.map(renderRow)}
              </div>
            ))}
          </>
        )}
      </div>
    </aside>
  );
}
