import React from 'react';
import { UsersRound } from 'lucide-react';

import { ScoreProvenance } from '../candidates/ScoreProvenance';
import { ChatArtifact } from '../../shared/chat';

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

const CandidateCard = ({ row }) => {
  const Component = row.frontend_url ? 'a' : 'div';
  const linkProps = row.frontend_url ? {
    href: row.frontend_url,
    target: '_blank',
    rel: 'noopener noreferrer',
  } : {};
  return (
    <Component className="cp-cand" {...linkProps}>
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
    </Component>
  );
};

// Renders candidate-grid results from search_applications /
// nl_search_candidates / graph_search_candidates / compare_applications.
const CandidateGrid = ({ rows }) => {
  if (!rows?.length) {
    return (
      <ChatArtifact
        eyebrow="Candidate results"
        title="No candidates matched"
        summary="Try widening the filters or asking for a different signal."
        icon={UsersRound}
      />
    );
  }
  return (
    <ChatArtifact
      eyebrow="Candidate results"
      title={`${rows.length} candidate${rows.length === 1 ? '' : 's'}`}
      summary="Scores, provenance, and current pipeline stage"
      icon={UsersRound}
    >
      <div className="cp-cand-grid">
        {rows.map((r) => (
          <CandidateCard key={r.application_id || r.candidate_id || r.frontend_url} row={r} />
        ))}
      </div>
    </ChatArtifact>
  );
};

export default CandidateGrid;
