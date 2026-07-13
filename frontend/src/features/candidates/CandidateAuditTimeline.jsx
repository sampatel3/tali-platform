import React, { useCallback, useEffect, useState } from 'react';
import { Bot, RefreshCw, User, Cpu, GitMerge } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { MotionLoop } from '../../shared/motion';
import { Button, Panel, Spinner } from '../../shared/ui/TaaliPrimitives';
import { formatStatusLabel } from './candidatesUiUtils';

const ACTOR_META = {
  recruiter: { label: 'Recruiter', Icon: User, tone: 'text-blue-600' },
  agent: { label: 'Agent', Icon: Bot, tone: 'text-purple-600' },
  system: { label: 'System', Icon: Cpu, tone: 'text-taali-fg-muted' },
  sync: { label: 'Sync', Icon: GitMerge, tone: 'text-emerald-600' },
};

const formatStageEdge = (event) => {
  const segments = [];
  if (event.from_stage || event.to_stage) {
    segments.push(`${event.from_stage || '∅'} → ${event.to_stage || '∅'}`);
  }
  if (event.from_outcome && event.from_outcome !== event.to_outcome) {
    segments.push(`${event.from_outcome} → ${event.to_outcome}`);
  }
  return segments.join('  ·  ');
};

const renderMetadata = (metadata) => {
  if (!metadata || typeof metadata !== 'object') return null;
  const entries = Object.entries(metadata).filter(([, value]) => value != null);
  if (!entries.length) return null;
  return (
    <dl className="mt-1 max-h-40 overflow-auto rounded bg-taali-bg-muted/40 p-2 text-[0.6875rem] leading-snug text-taali-fg">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-2">
          <dt className="font-medium text-taali-fg-muted">{key.replace(/_/g, ' ')}:</dt>
          <dd className="min-w-0 break-words">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
};

export const CandidateAuditTimeline = ({ applicationId }) => {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  const fetchEvents = useCallback(async () => {
    if (!applicationId) return;
    setLoading(true);
    try {
      const res = await apiClient.roles.listApplicationEvents(applicationId, { limit: 100 });
      setEvents(Array.isArray(res.data) ? res.data : []);
      setError(null);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load audit timeline');
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  return (
    <Panel className="flex flex-col gap-3 p-4">
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-taali-fg-muted">Activity timeline</h3>
        <Button variant="ghost" size="xs" onClick={fetchEvents} disabled={loading} aria-label="Refresh timeline">
          <MotionLoop active={loading} kind="spin" className="inline-flex" aria-hidden="true">
            <RefreshCw size={12} />
          </MotionLoop>
        </Button>
      </header>

      {error ? (
        <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>
      ) : null}

      {loading && !events.length ? (
        <div className="flex items-center gap-2 text-xs text-taali-fg-muted">
          <Spinner size={12} /> Loading…
        </div>
      ) : null}

      {!loading && !events.length && !error ? (
        <p className="text-xs text-taali-fg-muted">No events recorded yet.</p>
      ) : null}

      <ol className="flex flex-col gap-2">
        {events.map((event) => {
          const actor = ACTOR_META[event.actor_type] || ACTOR_META.system;
          const ActorIcon = actor.Icon;
          const stageEdge = formatStageEdge(event);
          const isOpen = expandedId === event.id;
          return (
            <li key={event.id} className="rounded-md border border-taali-border bg-taali-bg/50 px-3 py-2 text-xs">
              <button
                type="button"
                className="flex w-full items-start justify-between gap-3 text-left"
                onClick={() => setExpandedId(isOpen ? null : event.id)}
              >
                <div className="flex min-w-0 items-start gap-2">
                  <ActorIcon size={14} className={`mt-0.5 ${actor.tone}`} aria-hidden />
                  <div className="min-w-0">
                    <div className="font-medium">{formatStatusLabel(event.event_type)}</div>
                    {stageEdge ? <div className="text-taali-fg-muted">{stageEdge}</div> : null}
                    {event.reason ? <div className="mt-0.5 text-taali-fg">{event.reason}</div> : null}
                  </div>
                </div>
                <div className="shrink-0 text-right text-[0.6875rem] text-taali-fg-muted">
                  <div>{actor.label}{event.actor_id ? ` #${event.actor_id}` : ''}</div>
                  <div>{new Date(event.created_at).toLocaleString()}</div>
                </div>
              </button>
              {isOpen ? renderMetadata(event.metadata) : null}
            </li>
          );
        })}
      </ol>
    </Panel>
  );
};

export default CandidateAuditTimeline;
