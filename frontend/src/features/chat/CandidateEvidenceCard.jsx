import React, { useState } from 'react';
import './CandidateEvidenceCard.css';

const QUOTE_CAP = 180;
// Show at most this many verbatim quotes per criterion — enough to back the
// verdict (e.g. the mix of employers behind a "partial") without a wall.
const MAX_QUOTES = 3;

const SOURCE_LABEL = {
  cv: 'CV',
  notes: 'notes',
  role_requirement: 'role criteria',
  taali_score: 'Taali score',
};

// A "Taali score >= N" criterion gates on the platform's own score, not on CV
// evidence — so the grounder can never find a quote and marks it "missing". The
// backend now decides these arithmetically; this mirror covers report snapshots
// minted before that fix (a shared report is a frozen snapshot, TTL 30 days) so
// they render correctly without regeneration. Idempotent: a verdict the backend
// already decided (grounded met/not_met) is left untouched.
const SCORE_NUM_RE = /(\d[\d.]*)/;
const SCORE_TOKEN_RE = /\b(score|fit)\b/i;
const SCORE_LEQ_RE = /(<=|<|at\s+most|max(?:imum)?|under|below|up\s+to)/i;
const SCORE_GEQ_RE = /(>=|>|at\s+least|min(?:imum)?|over|above|greater)/i;

function isSelfScoreCriterion(text) {
  const t = text || '';
  return /taali/i.test(t) && SCORE_TOKEN_RE.test(t) && SCORE_NUM_RE.test(t);
}

function withSelfScoreVerdicts(criteria, taaliScore) {
  if (!Array.isArray(criteria) || typeof taaliScore !== 'number') return criteria;
  return criteria.map((c) => {
    if (!c || !isSelfScoreCriterion(c.criterion)) return c;
    if (c.grounded && (c.status === 'met' || c.status === 'not_met')) return c;
    const num = SCORE_NUM_RE.exec(c.criterion);
    const threshold = num ? parseFloat(num[1]) : NaN;
    if (!Number.isFinite(threshold)) return c;
    const leq = SCORE_LEQ_RE.test(c.criterion) && !SCORE_GEQ_RE.test(c.criterion);
    const meets = leq ? taaliScore <= threshold : taaliScore >= threshold;
    const shown = Math.round(taaliScore);
    const sym = leq ? '≤' : '≥';
    return {
      ...c,
      status: meets ? 'met' : 'not_met',
      grounded: true,
      source: 'taali_score',
      evidence: [{ quote: `Taali score ${shown}`, source: 'taali_score' }],
      note: meets
        ? `Taali score ${shown} meets the ${sym} ${threshold} threshold.`
        : `Taali score ${shown} is ${leq ? 'above' : 'below'} the ${sym} ${threshold} threshold.`,
    };
  });
}

// "8 yrs" / "7.5 yrs" from the scorer's years_experience, or null.
function formatYears(y) {
  if (typeof y !== 'number' || !(y > 0)) return null;
  const r = Math.round(y * 2) / 2;
  return `${Number.isInteger(r) ? r : r.toFixed(1)} yrs`;
}

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

const STATUS_LABEL = {
  met: 'Met',
  partially_met: 'Partial',
  not_met: 'Not met',
  missing: 'Missing',
  // The check couldn't complete (transient failure / timeout) — NOT a verdict
  // of "no evidence". Shown distinctly so a blip never reads as a damning gap.
  error: 'Unverified',
};

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
      : status === 'not_met'
      ? 'ev-chip-notmet'
      : status === 'error'
      ? 'ev-chip-error'
      : 'ev-chip-missing';
  const allQuotes = Array.isArray(c.evidence) ? c.evidence.filter((e) => e && e.quote) : [];
  const quotes = allQuotes.slice(0, MAX_QUOTES);
  const moreQuotes = allQuotes.length - quotes.length;
  // The model's one-line reason for the verdict. Surfaced for the verdicts a
  // recruiter actually questions ("why is this partial / not met?"); met is
  // self-evident from its quote and missing has its own line below.
  const reason =
    (c.note || '').trim() && (status === 'partially_met' || status === 'not_met')
      ? c.note.trim()
      : null;
  return (
    <div className="ev-crit">
      <div className="ev-crit-head">
        <span className={`ev-chip ${chipClass}`}>{STATUS_LABEL[status] || status}</span>
        <span className="ev-crit-text">{c.criterion}</span>
      </div>
      {reason ? <div className="ev-reason">{reason}</div> : null}
      {grounded && quotes.length ? (
        <div className="ev-quotes">
          {quotes.map((e, i) => (
            <Quote key={i} text={e.quote} source={e.source} />
          ))}
          {moreQuotes > 0 ? (
            <div className="ev-more-src">
              +{moreQuotes} more {moreQuotes === 1 ? 'source' : 'sources'}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="ev-noquote">
          {status === 'missing'
            ? 'No supporting evidence in the CV or notes.'
            : status === 'error'
            ? 'Couldn’t verify — the evidence check didn’t complete. Retrying.'
            : 'Stated, but no verbatim quote — treat as unverified.'}
        </div>
      )}
    </div>
  );
}

export default function CandidateEvidenceCard({ data, detailed = false, showReportLink = true }) {
  if (!data || !Array.isArray(data.candidates)) return null;
  const spec = data.spec || {};
  const candidates = data.candidates;
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const rankLabel = RANK_LABELS[data.rank_by || spec.ranking_key] || data.rank_by || 'score';
  const shown = data.shown ?? candidates.length;
  const excluded = data.excluded || {};
  const hidden = excluded.not_met_total || 0;
  const hiddenBy = Array.isArray(excluded.by_criterion) ? excluded.by_criterion : [];
  // Rediscovery mode (screen_pool_against_requirement): screened the scored
  // history against a NEW requirement, ranked by fit to THAT (not the role
  // score) — the header says so, and a bounded window was deep-checked.
  const isRediscovery = data.mode === 'rediscovery';
  const screened = data.screened;
  const capped = !!data.capped;

  return (
    <div className="ev-card">
      <div className="ev-head">
        <div className="ev-title">
          {isRediscovery ? `Rediscovery · ${shown} shown` : `Top ${shown}`}
          {spec.echo ? <span className="ev-echo"> · {spec.echo}</span> : null}
        </div>
        <div className="ev-meta">
          {isRediscovery ? 'ranked by fit to your requirement' : `ranked by ${rankLabel}`}
          {isRediscovery
            ? screened
              ? ` · deep-checked ${screened} of ${data.total_matched} scored${
                  capped ? ', refine to narrow' : ''
                }`
              : typeof data.total_matched === 'number'
              ? ` · ${data.total_matched} scored in pool`
              : ''
            : typeof data.total_matched === 'number'
            ? ` · ${data.total_matched} in pool`
            : ''}
          {data.evidence_model ? ' · grounded vs CV + notes' : ''}
        </div>
      </div>

      {hidden > 0 ? (
        <div className="ev-filtered">
          {hidden} hidden — didn’t meet{' '}
          {hiddenBy.length
            ? hiddenBy.map((b) => `${b.criterion} (${b.count})`).join(', ')
            : 'a requirement'}
        </div>
      ) : null}

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
              {c.workable_profile_url ? (
                <>
                  {' · '}
                  <a className="ev-ext" href={c.workable_profile_url} target="_blank" rel="noreferrer">
                    Workable ↗
                  </a>
                </>
              ) : null}
            </div>
            {detailed && (c.candidate_headline || c.candidate_summary) ? (
              <div className="ev-summary">
                {c.candidate_headline ? (
                  <div className="ev-summary-headline">
                    {formatYears(c.candidate_years) ? (
                      <span className="ev-years">{formatYears(c.candidate_years)}</span>
                    ) : null}
                    {c.candidate_headline}
                  </div>
                ) : null}
                {c.candidate_summary ? (
                  <div className="ev-summary-body">{c.candidate_summary}</div>
                ) : null}
              </div>
            ) : null}
            {Array.isArray(c.criteria) && c.criteria.length ? (
              <div className="ev-crits">
                {withSelfScoreVerdicts(c.criteria, c.taali_score).map((cr, j) => (
                  <CriterionRow key={j} c={cr} />
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>

      {showReportLink && data.report_url ? (
        <a className="ev-report-link" href={data.report_url} target="_blank" rel="noreferrer">
          Open shareable report ↗
        </a>
      ) : null}
    </div>
  );
}
