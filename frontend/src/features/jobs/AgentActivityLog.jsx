import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Loader2 } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { getErrorMessage } from '../candidates/candidatesUiUtils';

// Per-entry icon glyph for the feed. Kept inline (not a dictionary at module
// top-of-file) so the kinds stay co-located with the renderer.
const ACTIVITY_ICON = {
  run: '◐',
  decision: '◆',
  event: '→',
  needs_input: '?',
};

const formatRelativeShort = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diffMs = Date.now() - parsed.getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
};

// AgentActivityLog — collapsible feed of agent runs, decisions, stage moves,
// and recruiter-input prompts for this role. Closed by default; fetches on
// open so the role-settings tab stays cheap to render. Refreshes on a 30s
// interval while open (paused when tab is hidden).
export const AgentActivityLog = ({ roleId }) => {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const cancelledRef = useRef(false);

  const fetchActivity = useCallback(async () => {
    if (!Number.isFinite(Number(roleId))) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.agent.activity(roleId, { limit: 50 });
      const data = res?.data || {};
      if (!cancelledRef.current) {
        setEntries(Array.isArray(data.entries) ? data.entries : []);
        setHasMore(Boolean(data.has_more));
      }
    } catch (err) {
      if (!cancelledRef.current) {
        setError(getErrorMessage(err, 'Could not load activity.'));
      }
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [roleId]);

  useEffect(() => {
    cancelledRef.current = false;
    if (!open) return undefined;
    fetchActivity();
    const t = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      fetchActivity();
    }, 30000);
    return () => {
      cancelledRef.current = true;
      clearInterval(t);
    };
  }, [open, fetchActivity]);

  return (
    <section className="mc-agent-settings-card">
      <button
        type="button"
        className="mc-agent-settings-card-head"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        style={{
          background: 'transparent',
          border: 0,
          padding: 0,
          width: '100%',
          textAlign: 'left',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div>
          <h2 className="mc-agent-settings-card-title">
            Activity <em>log</em>
          </h2>
          <p className="mc-agent-settings-card-help">
            Everything the agent has done on this role — cycles, scores, stage moves, and questions raised.
          </p>
        </div>
        <ChevronDown
          size={18}
          style={{
            transition: 'transform 120ms ease',
            transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
            color: 'var(--mute)',
            flexShrink: 0,
          }}
        />
      </button>
      {open ? (
        <div style={{ marginTop: 14 }}>
          {loading && entries.length === 0 ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--mute)', fontSize: 13 }}>
              <Loader2 size={14} className="mc-spin" />
              <span>Loading activity…</span>
            </div>
          ) : null}
          {error ? (
            <div style={{ fontSize: 13, color: 'var(--purple)', marginBottom: 8 }}>{error}</div>
          ) : null}
          {!loading && !error && entries.length === 0 ? (
            <div style={{ fontSize: 13, color: 'var(--mute)' }}>
              No activity yet. When the agent runs, scores, or moves a candidate, it shows up here.
            </div>
          ) : null}
          {entries.length > 0 ? (
            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {entries.map((entry) => (
                <li
                  key={`${entry.kind}-${entry.id}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '20px 1fr auto',
                    gap: 10,
                    alignItems: 'baseline',
                    padding: '8px 0',
                    borderBottom: '1px solid var(--line)',
                    fontSize: 13,
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 12,
                      color: 'var(--purple)',
                      textAlign: 'center',
                    }}
                  >
                    {ACTIVITY_ICON[entry.kind] || '·'}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ color: 'var(--ink)', fontWeight: 500 }}>{entry.title}</div>
                    {entry.detail ? (
                      <div
                        style={{
                          color: 'var(--ink-2)',
                          fontSize: 12,
                          marginTop: 2,
                          display: '-webkit-box',
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}
                      >
                        {entry.detail}
                      </div>
                    ) : null}
                    {entry.confidence != null || entry.cost_micro_usd ? (
                      <div style={{ color: 'var(--mute)', fontSize: 11, marginTop: 2 }}>
                        {entry.confidence != null ? `confidence ${Math.round(entry.confidence * 100)}%` : null}
                        {entry.confidence != null && entry.cost_micro_usd ? ' · ' : null}
                        {entry.cost_micro_usd ? `$${(entry.cost_micro_usd / 1_000_000).toFixed(3)}` : null}
                      </div>
                    ) : null}
                  </div>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                      color: 'var(--mute)',
                      whiteSpace: 'nowrap',
                    }}
                    title={entry.created_at}
                  >
                    {formatRelativeShort(entry.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          {hasMore && entries.length > 0 ? (
            <div style={{ fontSize: 12, color: 'var(--mute)', marginTop: 10 }}>
              Showing latest 50 · older activity not loaded.
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};

export default AgentActivityLog;
