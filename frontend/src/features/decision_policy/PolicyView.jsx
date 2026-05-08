import React, { useEffect, useState } from 'react';
import { decisionPolicyApi } from './api';

// Active policy + revision timeline. Recruiters can drill from here
// into the pending-retune review when there's something awaiting
// activation.
export default function PolicyView() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    decisionPolicyApi
      .active()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e.response?.data?.detail || e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <div className="dp-error">Failed to load policy: {error}</div>;
  }
  if (!data) return <div className="dp-loading">Loading active policy…</div>;

  const points = data.policy_json?.decision_points || {};

  return (
    <div className="dp-policy-view">
      <header>
        <h2>Decision Policy</h2>
        <p className="dp-meta">
          Revision <code>{data.revision_id}</code> · activated{' '}
          {data.activated_at ? new Date(data.activated_at).toLocaleString() : 'never'}
        </p>
      </header>

      <section>
        <h3>Decision points</h3>
        {Object.entries(points).map(([name, point]) => (
          <details key={name} className="dp-point" open>
            <summary>{name}</summary>
            <div className="dp-point-body">
              <table>
                <thead>
                  <tr>
                    <th>Threshold</th>
                    <th>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(point.thresholds || {}).map(([k, v]) => (
                    <tr key={k}>
                      <td>{k}</td>
                      <td>{Number(v).toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {point.weights && Object.keys(point.weights).length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>Signal</th>
                      <th>Weight</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(point.weights).map(([k, v]) => (
                      <tr key={k}>
                        <td>{k}</td>
                        <td>{Number(v).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {(point.rules || []).map((r, idx) => (
                <pre key={idx} className="dp-rule">
                  if {r.if} → {r.then} (priority {r.priority})
                </pre>
              ))}
            </div>
          </details>
        ))}
      </section>

      <section>
        <h3>Revision timeline</h3>
        <ol className="dp-timeline">
          {(data.timeline || []).map((rev) => (
            <li key={rev.id}>
              <strong>#{rev.id}</strong> · {rev.cause} ·{' '}
              {new Date(rev.created_at).toLocaleString()}
              {rev.notes && (
                <pre className="dp-rev-notes">{rev.notes}</pre>
              )}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
