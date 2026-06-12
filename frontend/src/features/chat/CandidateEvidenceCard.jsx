import React, { useState } from 'react';
import './CandidateEvidenceCard.css';

const QUOTE_CAP = 180;

const SOURCE_LABEL = { cv: 'CV', notes: 'notes', role_requirement: 'role criteria' };

// Verbatim quote: collapse ragged whitespace, tag where it came from (CV vs the
// candidate's notes/stated details), and cap the length with an inline "more"
// toggle so a long citation doesn't dominate the card.
function Quote({ text, source }) {
  const [open, setOpen] = useState(false);
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  const long = clean.length > QUOTE_CAP;
  const shown = open || !long ? clean : `${clean.slice(0, QUOTE_CAP).trimEnd()}…`;
  return (
    <blockquote className="ev-quote">
      {source ? <span className="ev-src">{SOURCE_LABEL[source] || source}</span> : null}
      “{shown}”
      {long ? (
        <button type="button" className="ev-more" onClick={() => setOpen((o) => !o)}>
          {open ? 'less' : 'more'}
        </button>
      ) : null}
    </blockquote>
  );
}

// Renders the grounded "top N with X and Y" result from find_top_candidates.
// One component for both chat surfaces: taali-chat passes the tool result as
// `data`; the per-role agent-chat passes the actions card (same shape) as
// `data`. Every qualitative claim is shown with its verbatim CV quote — a
// criterion only reads as satisfied when `grounded` is true.

const RANK_LABELS = {
  taali: 'Taali fit',
  pre_screen: 'pre-screen',
  rank: 'pairwise rank',
  cv_match: 'CV match',
};

const STATUS_LABEL = { met: 'Met', partially_met: 'Partial', missing: 'Missing' };

const scoreClass = (v) =>
  typeof v !== 'number'
    ? 'ev-score-none'
    : v >= 75
    ? 'ev-score-high'
    : v >= 50
    ? 'ev-score-mid'
    : 'ev-score-low';

function CriterionRow({ c }) {
  const status = c.status || 'missing';
  const grounded = !!c.grounded;
  const chipClass =
    status === 'met' && grounded
      ? 'ev-chip-met'
      : status === 'partially_met' && grounded
      ? 'ev-chip-partial'
      : 'ev-chip-missing';
  const quotes = Array.isArray(c.evidence) ? c.evidence.filter((e) => e && e.quote) : [];
  return (
    <div className="ev-crit">
      <div className="ev-crit-head">
        <span className={`ev-chip ${chipClass}`}>{STATUS_LABEL[status] || status}</span>
        <span className="ev-crit-text">{c.criterion}</span>
      </div>
      {grounded && quotes.length ? (
        <div className="ev-quotes">
          {quotes.map((e, i) => (
            <Quote key={i} text={e.quote} source={e.source} />
          ))}
        </div>
      ) : (
        <div className="ev-noquote">
          {status === 'missing'
            ? 'No supporting evidence in the CV or notes.'
            : 'Stated, but no verbatim quote — treat as unverified.'}
        </div>
      )}
    </div>
  );
}

export default function CandidateEvidenceCard({ data }) {
  if (!data || !Array.isArray(data.candidates)) return null;
  const spec = data.spec || {};
  const candidates = data.candidates;
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const rankLabel = RANK_LABELS[data.rank_by || spec.ranking_key] || data.rank_by || 'score';

  return (
    <div className="ev-card">
      <div className="ev-head">
        <div className="ev-title">
          Top {data.shortlist_size ?? candidates.length}
          {spec.echo ? <span className="ev-echo"> · {spec.echo}</span> : null}
        </div>
        <div className="ev-meta">
          {typeof data.total_matched === 'number' ? `${data.total_matched} matched · ` : ''}
          ranked by {rankLabel}
          {data.evidence_model ? ' · grounded vs CV' : ''}
        </div>
      </div>

      {warnings.length ? (
        <div className="ev-warn">
          {warnings.map((w, i) => (
            <span key={i}>{w.message || w.code}</span>
          ))}
        </div>
      ) : null}

      <ol className="ev-list">
        {candidates.map((c, i) => (
          <li key={c.application_id || i} className="ev-cand">
            <div className="ev-cand-head">
              <span className="ev-rank">#{c.rank || i + 1}</span>
              <a
                className="ev-name"
                href={c.frontend_url || '#'}
                target="_blank"
                rel="noreferrer"
              >
                {c.candidate_name || 'Candidate'}
              </a>
              {typeof c.taali_score === 'number' ? (
                <span className={`ev-pill ${scoreClass(c.taali_score)}`}>
                  Taali {Math.round(c.taali_score)}
                </span>
              ) : null}
              {c.meets_all_criteria ? <span className="ev-allmet">all met</span> : null}
            </div>
            <div className="ev-sub">
              {[c.candidate_position, c.candidate_location].filter(Boolean).join(' · ')}
              {c.role_name ? ` — ${c.role_name}` : ''}
            </div>
            {Array.isArray(c.criteria) && c.criteria.length ? (
              <div className="ev-crits">
                {c.criteria.map((cr, j) => (
                  <CriterionRow key={j} c={cr} />
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
