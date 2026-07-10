import React, { useEffect, useState } from 'react';
import { decisionPolicyApi } from './api';
import { prettyKey } from '../analytics/analyticsFormat';

const CAUSE_LABELS = {
  feedback_retune: 'Feedback update',
  manual_edit: 'Manual edit',
  initial: 'Initial policy',
  rollback: 'Rollback',
};
const causeLabel = (cause) => CAUSE_LABELS[cause] || prettyKey(cause);

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
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <div className="dp-error">Couldn&rsquo;t load the decision policy. Try refreshing the page.</div>;
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
            <summary>{prettyKey(name)}</summary>
            <div className="dp-point-body">
              <div className="table-scroll">
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
                      <td>{prettyKey(k)}</td>
                      <td>{Number(v).toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {point.weights && Object.keys(point.weights).length > 0 && (
                <div className="table-scroll">
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
                        <td>{prettyKey(k)}</td>
                        <td>{Number(v).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}
              {(point.rules || []).map((r, idx) => (
                <div key={idx} className="dp-rule">
                  If {prettyKey(r.if)} → {prettyKey(r.then)} (priority {r.priority})
                </div>
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
              <strong>#{rev.id}</strong> · {causeLabel(rev.cause)} ·{' '}
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
