// Platform updates — the persistent log behind every toast. Toasts
// dismiss themselves after 5s; ToastContext also mirrors each one into
// `activities` so the Home page can show the user that the platform is
// working between visits. Sync chatter (new candidate / new role /
// Workable import) is hidden by default — it tends to drown out the
// decision feed — and enabled via the filter chips.

import React, { useMemo, useState } from 'react';

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

export const HomePlatformUpdates = ({ compact = false }) => {
  const { activities } = useToast();
  // Always-visible feed (matching the home-preview) — no collapse toggle. The
  // background-chatter filter chips below stay so sync noise can still be folded
  // away, but the log itself is open on load.
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

  // Compact hub variant — the last few default-kind updates, no filter chips.
  // The full filterable log lives on the Analytics page's fleet activity tab.
  if (compact) {
    const rows = activities.filter((entry) => DEFAULT_KINDS.has(entry.kind)).slice(0, 3);
    return (
      <section className="home-section home-updates-mini">
        <div className="home-section-head">
          <span className="kicker">PLATFORM UPDATES</span>
        </div>
        {rows.length === 0 ? (
          <div className="home-empty" style={{ marginTop: 10 }}>
            Nothing yet. Updates appear here as the platform works.
          </div>
        ) : (
          <ul className="home-platform-list">
            {rows.map((entry) => (
              <li key={entry.id} className={`home-platform-row k-${entry.kind}`}>
                <span className="dot" style={{ background: dotColor(entry.kind) }} aria-hidden="true" />
                <span className="msg">{entry.message}</span>
                <span className="age">{formatRelativeAge(entry.createdAt)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

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
      </div>

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
    </section>
  );
};

export default HomePlatformUpdates;
