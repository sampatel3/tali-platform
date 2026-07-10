import React from 'react';

import { ScoreProvenance } from '../candidates/ScoreProvenance';

const scoreClass = (s) => {
  if (s == null) return '';
  if (s >= 75) return 'cp-score-high';
  if (s >= 50) return 'cp-score-mid';
  return 'cp-score-low';
};

const ScorePill = ({ label, value }) => {
  if (value == null) return null;
  return (
    <span className={`cp-score-pill ${scoreClass(value)}`}>
      <span className="lab">{label}</span>
      <span>{Math.round(value)}</span>
    </span>
  );
};

const CandidateCard = ({ row }) => (
  <a
    className="cp-cand"
    href={row.frontend_url || '#'}
    target="_blank"
    rel="noopener noreferrer"
  >
    <div className="cp-cand-name">{row.candidate_name || '(no name)'}</div>
    <div className="cp-cand-sub">
      {[row.candidate_position, row.candidate_location].filter(Boolean).join(' · ') ||
        row.role_name ||
        ''}
    </div>
    <div className="cp-cand-row">
      <ScorePill label="taali" value={row.taali_score} />
      <ScorePill label="pre-screen" value={row.pre_screen_score} />
      {row.pipeline_stage ? (
        <span className="cp-stage-pill">{row.pipeline_stage}</span>
      ) : null}
    </div>
    <ScoreProvenance provenance={row?.score_summary?.score_provenance} density="pill" />
  </a>
);

// Renders candidate-grid results from search_applications /
// nl_search_candidates / graph_search_candidates / compare_applications.
const CandidateGrid = ({ rows }) => {
  if (!rows?.length) {
    return <div className="cp-tool-args">No candidates matched.</div>;
  }
  return (
    <div className="cp-cand-grid">
      {rows.map((r) => (
        <CandidateCard key={r.application_id || r.candidate_id || r.frontend_url} row={r} />
      ))}
    </div>
  );
};

export default CandidateGrid;
