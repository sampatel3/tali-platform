// Inline panel on the role page showing the orchestrator's open
// questions for this role. Recruiters answer inline; the next agent
// cycle picks the response up and unblocks itself.
//
// Data flows through /api/v1/agent-needs-input (listing + answer +
// dismiss). The card hides itself entirely when there are no open
// rows, so a healthy role doesn't show an empty container.

import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, MessageSquareWarning, X } from 'lucide-react';

import api from '../../shared/api/httpClient';

const STATUS_OPEN = 'open';

const fetchOpen = (roleId) =>
  api
    .get('/agent-needs-input', { params: { role_id: roleId, status: STATUS_OPEN } })
    .then((r) => r.data || []);

export default function AgentNeedsInputCard({ roleId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const reload = useCallback(() => {
    if (!roleId) return;
    setLoading(true);
    fetchOpen(roleId)
      .then((data) => {
        setRows(Array.isArray(data) ? data : []);
        setError(null);
      })
      .catch((e) => setError(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false));
  }, [roleId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (loading && rows.length === 0) return null;
  if (!loading && rows.length === 0 && !error) return null;

  const handleAnswer = async (id, value) => {
    setBusyId(id);
    try {
      await api.post(`/agent-needs-input/${id}/answer`, {
        response: { value },
      });
      reload();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusyId(null);
    }
  };

  const handleDismiss = async (id) => {
    setBusyId(id);
    try {
      await api.post(`/agent-needs-input/${id}/dismiss`);
      reload();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="agent-needs-input">
      <header className="agent-needs-input-head">
        <span className="agent-needs-input-icon">
          <MessageSquareWarning size={14} strokeWidth={1.8} />
        </span>
        <div>
          <b>Agent has {rows.length === 1 ? 'a question' : `${rows.length} questions`} for you</b>
          <p>Answers unblock the next agent cycle on this role.</p>
        </div>
      </header>

      {error ? <div className="agent-needs-input-error">{error}</div> : null}

      <ol className="agent-needs-input-list">
        {rows.map((row) => (
          <li key={row.id} className={`agent-needs-input-row kind-${row.kind}`}>
            <div className="agent-needs-input-prompt">
              <p>{row.prompt}</p>
              {row.rationale ? (
                <p className="agent-needs-input-rationale">{row.rationale}</p>
              ) : null}
            </div>

            <div className="agent-needs-input-actions">
              {Array.isArray(row.options) && row.options.length > 0 ? (
                row.options.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={busyId === row.id}
                    className="agent-needs-input-option"
                    onClick={() => handleAnswer(row.id, opt.value)}
                  >
                    <CheckCircle2 size={12} />
                    {opt.label}
                  </button>
                ))
              ) : (
                <FreeTextAnswer
                  busy={busyId === row.id}
                  onSubmit={(text) => handleAnswer(row.id, text)}
                />
              )}
              <button
                type="button"
                className="agent-needs-input-dismiss"
                disabled={busyId === row.id}
                onClick={() => handleDismiss(row.id)}
                title="Dismiss without answering"
              >
                <X size={12} />
                Skip
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function FreeTextAnswer({ busy, onSubmit }) {
  const [text, setText] = useState('');
  return (
    <form
      className="agent-needs-input-freeform"
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = text.trim();
        if (!trimmed) return;
        onSubmit(trimmed);
      }}
    >
      <input
        type="text"
        placeholder="Your answer…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
      />
      <button type="submit" disabled={busy || !text.trim()}>
        Send
      </button>
    </form>
  );
}
