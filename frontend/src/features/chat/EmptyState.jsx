import React from 'react';

// Suggestion content matches the design's "Triage · Senior Backend"
// pattern: a category tag (mono, uppercase, 0.14em letter-spacing) above
// a natural-language question. Tags read like things a recruiter
// actually does, not the underlying tool name.
const SUGGESTIONS = [
  {
    tag: 'Triage · Senior Backend',
    q: 'Find me a Senior Backend Engineer with 5+ years and Python expertise',
  },
  {
    tag: 'Pipeline · Review stage',
    q: 'Who is currently in review across all open roles?',
  },
  {
    tag: 'Compare · Top three',
    q: 'Compare the top 3 candidates for the Senior Engineer role',
  },
  {
    tag: 'Graph · Skills + companies',
    q: 'Anyone who worked at a YC company and knows Postgres?',
  },
  {
    tag: 'Roles · Pipeline counts',
    q: 'Show me every active role and how many candidates are in each pipeline stage',
  },
  {
    tag: 'CV · Highest scoring',
    q: 'Pull the CV details for the highest-scoring candidate this month',
  },
];

const EmptyState = ({ onPick }) => (
  <div className="cp-empty">
    <div className="cp-empty-glyph" aria-hidden="true">
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    </div>
    <h1 className="cp-empty-h1">
      What are you looking for<em>?</em>
    </h1>
    <p className="cp-empty-sub">
      Ask in plain language. Taali can search your candidates, compare them,
      walk through reject reasons, or traverse the skills graph. Citations
      link back to the underlying record.
    </p>
    <div className="cp-suggest-grid">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.q}
          type="button"
          className="cp-suggest"
          onClick={() => onPick(s.q)}
        >
          <span className="cp-suggest-tag">{s.tag}</span>
          <span className="cp-suggest-q">{s.q}</span>
        </button>
      ))}
    </div>
  </div>
);

export default EmptyState;
