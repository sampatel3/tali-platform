import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { viewSubmittalPack } from '../../shared/api/httpClient';
import './SubmittalPackPage.css';

// Public, no-auth page for a curated client submittal. Fetches the frozen
// snapshot by token and renders the ordered, client-safe candidate cards
// (name, verdict band/score, share summary, highlights, recruiter note).
export default function SubmittalPackPage() {
  const { submittalToken } = useParams();
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let alive = true;
    viewSubmittalPack(submittalToken)
      .then((res) => {
        if (alive) setState({ loading: false, error: null, data: res.data });
      })
      .catch((err) => {
        const status = err?.response?.status;
        const msg =
          status === 410
            ? 'This submittal has expired or been revoked.'
            : status === 404
            ? 'Submittal not found.'
            : 'Could not load this submittal.';
        if (alive) setState({ loading: false, error: msg, data: null });
      });
    return () => {
      alive = false;
    };
  }, [submittalToken]);

  if (state.loading) {
    return (
      <div className="spk-wrap">
        <div className="spk-muted">Loading submittal…</div>
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="spk-wrap">
        <div className="spk-brand">taali<span>.</span></div>
        <div className="spk-muted">{state.error}</div>
      </div>
    );
  }

  const data = state.data || {};
  const role = data.role || {};
  const organization = data.organization || {};
  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  let created;
  try {
    created = data.created_at
      ? new Date(data.created_at).toLocaleDateString(undefined, {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        })
      : null;
  } catch (e) {
    created = null;
  }

  return (
    <div className="spk-wrap">
      <div className="spk-head">
        <div className="spk-brand">taali<span>.</span></div>
        <h1 className="spk-title">{data.title || 'Candidate submittal'}</h1>
        {role.title ? <div className="spk-role">{role.title}</div> : null}
        <div className="spk-sub">
          {organization.name ? organization.name : 'Shared shortlist'}
          {created ? ` · ${created}` : ''}
        </div>
      </div>

      {candidates.length === 0 ? (
        <div className="spk-muted">No candidates in this submittal.</div>
      ) : (
        <div className="spk-cards">
          {candidates.map((c, i) => {
            const band = String(c.verdict_band || 'na');
            const score = typeof c.score_100 === 'number' ? Math.round(c.score_100) : null;
            const highlights = Array.isArray(c.highlights) ? c.highlights : [];
            return (
              <div className="spk-card" key={c.application_id ?? i}>
                <div className="spk-card-head">
                  <div className="spk-name">{c.candidate_name || 'Candidate'}</div>
                  {score != null ? (
                    <span className={`spk-score band-${band}`}>{score}</span>
                  ) : null}
                </div>
                {c.verdict ? <div className={`spk-verdict band-${band}`}>{c.verdict}</div> : null}
                {c.note ? <div className="spk-note">{c.note}</div> : null}
                {highlights.length > 0 ? (
                  <ul className="spk-highlights">
                    {highlights.map((h, hi) => (
                      <li key={hi}>{h}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      <div className="spk-foot">Shared from Taali · read-only snapshot</div>
    </div>
  );
}
