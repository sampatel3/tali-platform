// Platform updates — the persistent log behind every toast. Toasts
// dismiss themselves after 5s; ToastContext also mirrors each one into
// `activities` so the Home page can show the user that the platform is
// working between visits. Sync chatter (new candidate / new role /
// Workable import) is hidden by default — it tends to drown out the
// decision feed — and enabled via the filter chips.

import React, { useMemo, useState } from 'react';
import { Activity, ChevronDown, ChevronUp } from 'lucide-react';

import { useToast } from '../../context/ToastContext';
import { formatRelativeAge } from './atoms';

const KIND_LABELS = {
  sync: 'Sync',
  role: 'Roles',
  decision: 'Decisions',
  success: 'Success',
  info: 'Info',
  error: 'Errors',
};

// kinds visible by default — sync/role chatter is filed under
// "background" and stays hidden unless the user opts in.
const DEFAULT_KINDS = new Set(['decision', 'error', 'success', 'info']);

const KIND_ORDER = ['decision', 'success', 'info', 'role', 'sync', 'error'];

const dotColor = (kind) => {
  switch (kind) {
    case 'error': return 'var(--red)';
    case 'success': return 'var(--green)';
    case 'decision': return 'var(--purple)';
    case 'role': return 'var(--workable)';
    case 'sync': return 'var(--mute)';
    default: return 'var(--ink-2)';
  }
};

export const HomePlatformUpdates = () => {
  const { activities } = useToast();
  const [open, setOpen] = useState(false);
  const [enabledKinds, setEnabledKinds] = useState(() => new Set(DEFAULT_KINDS));

  const counts = useMemo(() => {
    const map = new Map();
    activities.forEach((entry) => {
      map.set(entry.kind, (map.get(entry.kind) || 0) + 1);
    });
    return map;
  }, [activities]);

  const visible = useMemo(() => (
    activities.filter((entry) => enabledKinds.has(entry.kind))
  ), [activities, enabledKinds]);

  const toggleKind = (kind) => {
    setEnabledKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const total = activities.length;
  const hiddenChatter = total - visible.length;

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">PLATFORM UPDATES</span>
          <h3 className="home-section-title">
            What the platform is doing<em>.</em>
          </h3>
          <p className="home-section-sub">
            Every toast the app fires lands here too — proof the pipeline
            keeps moving while you&apos;re away. Routine sync chatter
            (new candidates, role imports, Workable runs) is filed under
            background and stays hidden by default.
          </p>
        </div>
        <button
          type="button"
          className="home-analytics-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          style={{ alignSelf: 'flex-start' }}
        >
          <Activity size={14} aria-hidden="true" />
          <span>{open ? 'Hide' : 'Show'} updates ({total})</span>
          {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        </button>
      </div>

      {open ? (
        <div className="home-platform-updates">
          <div className="home-platform-filters">
            <span className="kicker mute">SHOW</span>
            {KIND_ORDER.map((kind) => {
              const n = counts.get(kind) || 0;
              if (!n) return null;
              const on = enabledKinds.has(kind);
              return (
                <button
                  key={kind}
                  type="button"
                  className={`home-platform-chip ${on ? 'on' : ''}`}
                  onClick={() => toggleKind(kind)}
                  aria-pressed={on}
                >
                  <span className="dot" style={{ background: dotColor(kind) }} aria-hidden="true" />
                  {KIND_LABELS[kind] || kind}
                  <span className="count">{n}</span>
                </button>
              );
            })}
            {hiddenChatter > 0 && !KIND_ORDER.every((k) => enabledKinds.has(k)) ? (
              <span className="home-platform-hint">
                {hiddenChatter} hidden by current filters
              </span>
            ) : null}
          </div>
          {visible.length === 0 ? (
            <div className="home-empty" style={{ marginTop: 12 }}>
              {total === 0
                ? 'Nothing yet. Updates from the agent and the platform appear here as they happen.'
                : 'Nothing in the selected filters. Toggle background chatter to see sync activity.'}
            </div>
          ) : (
            <ul className="home-platform-list">
              {visible.map((entry) => (
                <li key={entry.id} className={`home-platform-row k-${entry.kind}`}>
                  <span className="dot" style={{ background: dotColor(entry.kind) }} aria-hidden="true" />
                  <span className="msg">{entry.message}</span>
                  <span className="age">{formatRelativeAge(entry.createdAt)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </section>
  );
};

export default HomePlatformUpdates;
