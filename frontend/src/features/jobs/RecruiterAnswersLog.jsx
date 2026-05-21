// Read-only log of resolved agent-needs-input cards for a role.
//
// The agent asks role-level questions ("what are the must-haves?",
// "use 30 as the threshold?", etc.) via agent_needs_input. The recruiter
// answers them inline on Home — but later, when they're on the role's
// Agent settings tab, they want to see what they told the agent. This
// component renders that Q&A history scoped to one role.
//
// Data: GET /agent-needs-input?role_id=X&status=resolved.

import React, { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import api from '../../shared/api/httpClient';

const formatTimestamp = (value) => {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

// Plain-English label for each ask_recruiter kind, so the Q&A entry has
// a header that makes sense without the recruiter having to remember the
// agent's wording.
const KIND_LABEL = {
  intent_slot_missing: 'Must-have requirements',
  intent_clarification: 'Intent clarification',
  threshold_ambiguous: 'Score threshold',
  monthly_budget_missing: 'Monthly budget',
  task_assignment_missing: 'Assessment task',
  candidate_tie_break: 'Candidate tie-break',
  other: 'Question',
};

const responseToText = (response) => {
  if (response == null) return '';
  if (typeof response === 'string') return response;
  if (typeof response === 'object') {
    const v = response.value;
    if (v == null) return '';
    return String(v);
  }
  return String(response);
};

export default function RecruiterAnswersLog({ roleId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!roleId) return;
    setLoading(true);
    setError('');
    try {
      const resp = await api.get('/agent-needs-input', {
        params: { role_id: roleId, status: 'resolved', limit: 25 },
      });
      setRows(Array.isArray(resp?.data) ? resp.data : []);
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'Failed to load Q&A history.';
      setError(typeof msg === 'string' ? msg : 'Failed to load Q&A history.');
    } finally {
      setLoading(false);
    }
  }, [roleId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Hide the section entirely when there's no history — keeps the tab
  // clean for newly-created roles.
  if (!loading && rows.length === 0 && !error) return null;

  return (
    <section className="mc-agent-settings-card" data-testid="recruiter-answers-log">
      <div className="mc-agent-settings-card-head">
        <div>
          <h2 className="mc-agent-settings-card-title">
            Recruiter Q&amp;A with the <em>agent</em>
          </h2>
          <p className="mc-agent-settings-card-help">
            Questions the agent asked you about this role, and your answers. Threshold and
            budget answers are reflected in the settings on this page. Must-have answers feed
            the agent's brief and appear as chips above.
          </p>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--mute)', fontSize: 13 }}>
          <Loader2 size={14} className="animate-spin" />
          Loading Q&amp;A…
        </div>
      ) : null}

      {error ? (
        <div style={{ color: '#b91c1c', fontSize: 13 }}>{error}</div>
      ) : null}

      {!loading && !error && rows.length > 0 ? (
        <ol style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {rows.map((row) => {
            const label = KIND_LABEL[row.kind] || 'Question';
            const answer = responseToText(row.response);
            return (
              <li
                key={row.id}
                style={{
                  borderLeft: '2px solid var(--line)',
                  paddingLeft: 12,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'baseline',
                    justifyContent: 'space-between',
                    gap: 8,
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      color: 'var(--purple)',
                      fontWeight: 600,
                    }}
                  >
                    {label}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--mute)', fontFamily: 'var(--font-mono)' }}>
                    {formatTimestamp(row.resolved_at || row.created_at)}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>
                  <span style={{ color: 'var(--mute)' }}>Agent asked:</span>{' '}
                  <span>{row.prompt}</span>
                </div>
                <div style={{ fontSize: 13.5, color: 'var(--ink)' }}>
                  <span style={{ color: 'var(--mute)' }}>You answered:</span>{' '}
                  <span style={{ whiteSpace: 'pre-wrap' }}>
                    {answer || <em style={{ color: 'var(--mute)' }}>(no answer recorded)</em>}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </section>
  );
}
