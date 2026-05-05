import React from 'react';

const SUGGESTIONS = [
  { tag: 'search', text: 'Find me a Senior Backend Engineer with 5+ years and Python expertise' },
  { tag: 'pipeline', text: 'Who is currently in review across all open roles?' },
  { tag: 'compare', text: 'Compare the top 3 candidates for the Senior Engineer role' },
  { tag: 'graph', text: 'Anyone who worked at a YC company and knows Postgres?' },
  { tag: 'roles', text: 'Show me every active role and how many candidates are in each pipeline stage' },
  { tag: 'cv', text: 'Pull the CV details for the highest-scoring candidate this month' },
];

const EmptyState = ({ onPick }) => (
  <div className="cp-empty">
    <h1 className="cp-empty-h1">What are you looking for?</h1>
    <div className="cp-empty-sub">Ask anything about your candidates, roles, or pipeline.</div>
    <div className="cp-suggest-grid">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.text}
          type="button"
          className="cp-suggest"
          onClick={() => onPick(s.text)}
        >
          <span className="cp-suggest-tag">{s.tag}</span>
          {s.text}
        </button>
      ))}
    </div>
  </div>
);

export default EmptyState;
