import React, { useEffect, useState } from 'react';
import { decisionPolicyApi } from './api';

// Pending feedback_retune policies awaiting admin activation. Shows
// the diff against the current active policy plus any per-shift
// "cause_summary" annotations the retuner stamped.
export default function PendingRetuneReview() {
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = () => {
    decisionPolicyApi
      .pending()
      .then(setPending)
      .catch((e) => setError(e.response?.data?.detail || e.message));
  };

  useEffect(() => {
    load();
  }, []);

  if (error) {
    return <div className="dp-error">Failed to load pending retunes: {error}</div>;
  }
  if (!pending) return <div className="dp-loading">Loading…</div>;
  if (pending.length === 0) {
    return (
      <div className="dp-empty">
        No pending retunes. The agent's decision policy is up-to-date with
        the most recent feedback.
      </div>
    );
  }

  const handleActivate = async (policyId) => {
    setBusy(policyId);
    try {
      await decisionPolicyApi.activate(policyId);
      load();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(null);
    }
  };

  const handleDiscard = async (policyId) => {
    setBusy(policyId);
    try {
      await decisionPolicyApi.discard(policyId);
      load();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="dp-pending-review">
      <h2>Pending policy retunes</h2>
      {pending.map((p) => (
        <article key={p.policy_id} className="dp-pending-card">
          <header>
            <h3>Revision #{p.revision_id}</h3>
            <span className="dp-meta">
              proposed {new Date(p.created_at).toLocaleString()}
            </span>
          </header>
          {p.notes && <pre className="dp-notes">{p.notes}</pre>}
          <h4>Diff vs. current</h4>
          <div className="table-scroll">
          <table className="dp-diff">
            <thead>
              <tr>
                <th>Field</th>
                <th>Old</th>
                <th>New</th>
                <th>Cause</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(p.diff || {}).map(([field, change]) => (
                <tr key={field}>
                  <td><code>{field}</code></td>
                  <td>{stringify(change.old)}</td>
                  <td>{stringify(change.new)}</td>
                  <td>{change.cause_summary || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <footer className="dp-pending-actions">
            <button
              type="button"
              disabled={busy === p.policy_id}
              onClick={() => handleActivate(p.policy_id)}
            >
              Activate
            </button>
            <button
              type="button"
              disabled={busy === p.policy_id}
              onClick={() => handleDiscard(p.policy_id)}
            >
              Discard
            </button>
          </footer>
        </article>
      ))}
    </div>
  );
}

function stringify(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return v.toFixed(2);
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
