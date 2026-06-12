import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { viewTopReport } from '../../shared/api/httpClient';
import CandidateEvidenceCard from './CandidateEvidenceCard';
import './TopReportPage.css';

// Public, no-auth page for a shared "top candidates" report. Fetches the
// persisted snapshot by token and renders it with the same evidence card the
// recruiter saw in chat (detailed mode: includes summaries + Workable links).
export default function TopReportPage() {
  const { reportToken } = useParams();
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let alive = true;
    viewTopReport(reportToken)
      .then((res) => {
        if (alive) setState({ loading: false, error: null, data: res.data });
      })
      .catch((err) => {
        const status = err?.response?.status;
        const msg =
          status === 410
            ? 'This report has expired or been revoked.'
            : status === 404
            ? 'Report not found.'
            : 'Could not load this report.';
        if (alive) setState({ loading: false, error: msg, data: null });
      });
    return () => {
      alive = false;
    };
  }, [reportToken]);

  if (state.loading) {
    return (
      <div className="trp-wrap">
        <div className="trp-muted">Loading report…</div>
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="trp-wrap">
        <div className="trp-brand">taali<span>.</span></div>
        <div className="trp-muted">{state.error}</div>
      </div>
    );
  }

  const { snapshot, query, created_at: createdAt } = state.data || {};
  const spec = (snapshot && snapshot.spec) || {};
  let created = null;
  try {
    created = createdAt
      ? new Date(createdAt).toLocaleDateString(undefined, {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        })
      : null;
  } catch (e) {
    created = null;
  }

  return (
    <div className="trp-wrap">
      <div className="trp-head">
        <div className="trp-brand">taali<span>.</span></div>
        <h1 className="trp-title">Top candidates</h1>
        {snapshot && snapshot.role_name ? (
          <div className="trp-role">{snapshot.role_name}</div>
        ) : null}
        <div className="trp-sub">
          {spec.echo || query || 'Ranked shortlist'}
          {created ? ` · ${created}` : ''}
        </div>
      </div>
      <CandidateEvidenceCard data={snapshot} detailed showReportLink={false} />
      <div className="trp-foot">Shared from Taali · read-only snapshot</div>
    </div>
  );
}
