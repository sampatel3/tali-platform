import React from 'react';
import { Plus, Trash2 } from 'lucide-react';

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

const Sidebar = ({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}) => {
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
              onClick={(e) => {
                e.stopPropagation();
                onDelete(r.id);
              }}
              style={{
                position: 'absolute',
                right: 8,
                top: 8,
                background: 'transparent',
                color: 'var(--c-mute-2)',
                padding: 4,
                borderRadius: 6,
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
    <aside className="cp-side">
      <div className="cp-side-head">
        <span className="cp-side-title">Conversations</span>
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
            <div style={{ padding: '6px 10px', fontSize: 12.5, color: 'var(--c-mute)' }}>
              Your conversations will show up here.
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
};

export default Sidebar;
