import React, { useEffect, useRef, useState } from 'react';

import { roles as rolesApi } from '../../shared/api/rolesClient';
import ConfirmDialog from './ConfirmDialog';

// Talent-pool rediscovery, Phase B — the opt-in re-score action on a rediscovery
// card. The screen (Phase A) ranks by grounded fit via cheap Haiku; this runs the
// full holistic (Sonnet) score against the SAME requirement for a true comparable
// number. Expensive, so it's explicit: a confirm dialog with the cost estimate
// gates it, and the server caps the count. Results never touch the role score.
const COST_PER_RESCORE_USD = 0.09;

export default function PoolRescore({ requirementText, candidates }) {
  const ids = (candidates || []).map((c) => c && c.application_id).filter(Boolean);
  const nameById = Object.fromEntries(
    (candidates || [])
      .filter((c) => c && c.application_id)
      .map((c) => [c.application_id, c.candidate_name]),
  );
  const [phase, setPhase] = useState('idle'); // idle | running | done | error
  const [results, setResults] = useState(null);
  const [err, setErr] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const timer = useRef(null);

  useEffect(() => () => clearInterval(timer.current), []);

  if (!requirementText || !ids.length) return null;
  const est = (ids.length * COST_PER_RESCORE_USD).toFixed(2);

  async function poll(jobId) {
    const j = (await rolesApi.getPoolRescore(jobId)).data || {};
    if (j.status === 'done') {
      setResults(Array.isArray(j.results) ? j.results : []);
      setPhase('done');
      return true;
    }
    if (j.status === 'error') {
      setErr('Re-scoring failed. Try again.');
      setPhase('error');
      return true;
    }
    return false;
  }

  async function run() {
    setConfirming(false);
    setPhase('running');
    setErr(null);
    try {
      const res = await rolesApi.startPoolRescore(requirementText, ids);
      const jobId = res.data && res.data.job_id;
      if (!jobId) throw new Error('no job id');
      if (await poll(jobId)) return;
      timer.current = setInterval(async () => {
        if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
        try {
          if (await poll(jobId)) clearInterval(timer.current);
        } catch (e) {
          clearInterval(timer.current);
          setErr('Lost contact while re-scoring. Refresh to check for results.');
          setPhase('error');
        }
      }, 2500);
    } catch (e) {
      const detail = e && e.response && e.response.data && e.response.data.detail;
      setErr(detail || 'Could not start the re-score.');
      setPhase('error');
    }
  }

  const scored = (results || [])
    .filter((r) => typeof r.role_fit_score === 'number')
    .sort((a, b) => b.role_fit_score - a.role_fit_score);

  return (
    <div className="ev-rescore">
      {phase === 'idle' ? (
        <button type="button" className="ev-rescore-btn" onClick={() => setConfirming(true)}>
          Re-score top {ids.length} against this requirement · est ${est}
        </button>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title={`Re-score ${ids.length} candidate${ids.length === 1 ? '' : 's'}?`}
        detail={`Runs the full score against this requirement for a true comparable number. Estimated cost: ~$${est}.`}
        confirmLabel="Re-score"
        onConfirm={run}
        onCancel={() => setConfirming(false)}
      />
      {phase === 'running' ? (
        <div className="ev-rescore-status">
          Re-scoring {ids.length} candidate{ids.length === 1 ? '' : 's'} against the requirement…
        </div>
      ) : null}
      {phase === 'error' ? <div className="ev-rescore-err">{err}</div> : null}
      {phase === 'done' ? (
        <div className="ev-rescore-done">
          <div className="ev-rescore-title">True fit vs your requirement</div>
          {scored.length ? (
            <ol className="ev-rescore-list">
              {scored.map((r) => (
                <li key={r.application_id} className="ev-rescore-row">
                  <span className="ev-rescore-name">
                    {nameById[r.application_id] || `#${r.application_id}`}
                  </span>
                  <span className="ev-rescore-score">{Math.round(r.role_fit_score)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="ev-rescore-status">No scores returned.</div>
          )}
        </div>
      ) : null}
    </div>
  );
}
