import React, { useEffect, useState } from 'react';
import { decisionPolicyApi } from './api';
import { Select } from '../../shared/ui/TaaliPrimitives';

// Per-bucket teach / override / manual disagreement counts + top
// failure modes. Lightweight v1 — no charting library; the table is
// the recruiter-actionable surface ("is the agent improving?").
export default function SignalsDashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let cancelled = false;
    decisionPolicyApi
      .signals(days)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e.response?.data?.detail || e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  if (error) {
    return <div className="dp-error">Failed to load signals: {error}</div>;
  }
  if (!data) return <div className="dp-loading">Loading…</div>;

  return (
    <div className="dp-signals">
      <header className="dp-signals-header">
        <h2>Disagreement signals</h2>
        <label>
          Window:&nbsp;
          <Select inline value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </Select>
        </label>
      </header>

      <div className="dp-signals-summary">
        <span>
          Manual recruiter actions: <strong>{data.manual_action_volume}</strong>
        </span>
        <span>
          Agent decisions queued: <strong>{data.agent_decision_volume}</strong>
        </span>
      </div>

      <h3>Daily counts</h3>
      <div className="table-scroll">
      <table className="dp-signals-table">
        <thead>
          <tr>
            <th>Day</th>
            <th>Teach</th>
            <th>Overrides</th>
            <th>Manual disagreements</th>
          </tr>
        </thead>
        <tbody>
          {(data.buckets || []).map((b) => (
            <tr key={b.bucket_iso}>
              <td>{b.bucket_iso}</td>
              <td>{b.teach}</td>
              <td>{b.overrides}</td>
              <td>{b.manual_disagreements}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <h3>Top failure modes</h3>
      {(data.top_failure_modes || []).length === 0 ? (
        <p className="dp-empty-inline">None in window.</p>
      ) : (
        <ul className="dp-failure-modes">
          {data.top_failure_modes.map((fm) => (
            <li key={fm.failure_mode}>
              <strong>{fm.failure_mode}</strong>: {fm.count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
