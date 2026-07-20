import React, { useState } from 'react';
import { FileSearch } from 'lucide-react';

import { ChatArtifact } from '../../shared/chat';
import './CandidateEvidenceCard.css';
import PoolRescore from './PoolRescore';

const QUOTE_CAP = 180;
// Show at most this many verbatim quotes per criterion — enough to back the
// verdict (e.g. the mix of employers behind a "partial") without a wall.
const MAX_QUOTES = 3;

const SOURCE_LABEL = {
  cv: 'CV',
  notes: 'notes',
  role_requirement: 'scored evidence',
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
        <button
          type="button"
          className="ev-more"
          aria-expanded={open}
          aria-label={open ? 'Show less of this evidence quote' : 'Show the full evidence quote'}
          onClick={() => setOpen((o) => !o)}
        >
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
  rank: 'head-to-head comparison',
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

function CriterionRow({ c, priority = null }) {
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
        {priority ? (
          <span className={`ev-crit-priority ev-crit-priority-${priority}`}>
            {priority === 'preferred' ? 'Preferred' : 'Required'}
          </span>
        ) : null}
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

const cleanTextList = (value) =>
  Array.isArray(value)
    ? value.map((item) => String(item || '').trim()).filter(Boolean)
    : [];

const countValue = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? count : null;
};

const criterionKey = (value) => String(value || '').trim().toLowerCase();

function candidateCoversCheckedCriteria(candidate, checkedCriteria) {
  const rows = Array.isArray(candidate?.criteria) ? candidate.criteria : [];
  if (!rows.length) return false;
  if (rows.some((row) => row?.status === 'error')) return false;
  if (!checkedCriteria.length) return true;

  const verdicts = new Map(rows.map((row) => [criterionKey(row?.criterion), row]));
  return checkedCriteria.every((criterion) => {
    const verdict = verdicts.get(criterionKey(criterion));
    return verdict && verdict.status !== 'error';
  });
}

function candidateMeetsRequiredCriteria(candidate, requiredCriteria) {
  if (!requiredCriteria.length) return false;
  const rows = withSelfScoreVerdicts(candidate?.criteria, candidate?.taali_score);
  if (!Array.isArray(rows)) return false;
  const verdicts = new Map(rows.map((row) => [criterionKey(row?.criterion), row]));
  return requiredCriteria.every((criterion) => {
    const verdict = verdicts.get(criterionKey(criterion));
    return verdict?.status === 'met' && verdict?.grounded === true;
  });
}

function candidateHasCitation(candidate) {
  const rows = Array.isArray(candidate?.criteria) ? candidate.criteria : [];
  return rows.some((criterion) =>
    criterion?.grounded
      && Array.isArray(criterion.evidence)
      && criterion.evidence.some((source) => Boolean(source?.quote)),
  );
}

// The engine exposes separate population, criterion and provider-success
// counts. Keep them separate: one cited row is useful evidence, but it does not
// make an otherwise capped, failed or unchecked shortlist fully grounded.
function evidenceCoverage(data, candidates, rankLabel) {
  const requested = cleanTextList(data.criteria_requested);
  const required = cleanTextList(data.required_criteria);
  const checked = cleanTextList(data.criteria_checked);
  const unchecked = cleanTextList(data.criteria_unchecked);
  const hasExplicitCriteriaCoverage =
    Array.isArray(data.criteria_requested) && Array.isArray(data.criteria_checked);
  const requestedCount = requested.length;
  const checkedCount = checked.length;
  const deepChecked = countValue(data.deep_checked) ?? countValue(data.screened) ?? 0;
  const evidenceSucceeded = countValue(data.evidence_succeeded);
  const qualified = countValue(data.qualified_in_checked ?? data.qualified);
  const databaseMatches = countValue(data.database_matches ?? data.total_matched);
  const shownCount = countValue(data.shown) ?? candidates.length;
  const evidenceReused = countValue(data.evidence_reused);
  const citedCandidates = candidates.filter(candidateHasCitation).length;
  const coveredCandidates = candidates.filter((candidate) =>
    candidateCoversCheckedCriteria(candidate, checked),
  ).length;
  const displayedVerdictsComplete =
    candidates.length === 0 || coveredCandidates === candidates.length;
  const criteriaComplete =
    hasExplicitCriteriaCoverage
      && requestedCount > 0
      && checkedCount === requestedCount
      && unchecked.length === 0;
  const populationComplete =
    databaseMatches !== null
      ? deepChecked >= databaseMatches && data.capped !== true
      : data.capped === false;
  const providerComplete =
    evidenceSucceeded !== null
      && deepChecked > 0
      && evidenceSucceeded >= deepChecked;
  const freshComplete =
    deepChecked > 0
      && providerComplete
      && criteriaComplete
      && populationComplete
      && displayedVerdictsComplete;

  const usesStoredEvidence = data.evidence_basis === 'stored_role_requirements';
  const storedComplete =
    usesStoredEvidence
      && shownCount > 0
      && candidates.length === shownCount
      && evidenceReused !== null
      && evidenceReused >= shownCount
      && citedCandidates >= candidates.length
      && displayedVerdictsComplete;
  const hasAnyEvidence =
    deepChecked > 0
      || (evidenceSucceeded !== null && evidenceSucceeded > 0)
      || (evidenceReused !== null && evidenceReused > 0)
      || citedCandidates > 0;
  const scoreOnly =
    data.evidence_basis === 'score_only'
      || (!requestedCount && !checkedCount && !unchecked.length && !hasAnyEvidence);

  let kind;
  if (freshComplete) kind = 'complete';
  else if (storedComplete) kind = 'stored';
  else if (hasAnyEvidence) kind = 'partial';
  else if (scoreOnly) kind = 'score_only';
  else kind = 'unavailable';

  const parts = [];
  if (usesStoredEvidence) {
    parts.push(`${evidenceReused ?? citedCandidates}/${shownCount} candidates with stored role evidence`);
  } else if (deepChecked > 0) {
    parts.push(
      databaseMatches !== null
        ? `${deepChecked}/${databaseMatches} candidates deep-checked`
        : `${deepChecked} candidates deep-checked`,
    );
    if (evidenceSucceeded !== null && evidenceSucceeded < deepChecked) {
      parts.push(`${evidenceSucceeded}/${deepChecked} evidence checks succeeded`);
    }
    if (requestedCount > 0) {
      parts.push(`${checkedCount}/${requestedCount} criteria checked`);
    }
    if (!displayedVerdictsComplete && candidates.length > 0) {
      parts.push(`${coveredCandidates}/${candidates.length} shown candidates have complete verdicts`);
    }
    if (qualified !== null) {
      parts.push(
        required.length > 0
          ? `${qualified} verified required ${qualified === 1 ? 'match' : 'matches'}`
          : `${qualified} fully met checked criteria`,
      );
    }
  } else if (hasAnyEvidence) {
    parts.push(`${citedCandidates}/${shownCount} candidates include cited evidence`);
    parts.push('overall evidence coverage unavailable');
  } else if (scoreOnly) {
    parts.push(`Ranked by ${rankLabel}; no qualitative evidence check`);
  } else {
    const population = databaseMatches ?? shownCount;
    parts.push(`0/${population} candidates deep-checked`);
    if (requestedCount > 0) {
      parts.push(`evidence unavailable for ${requestedCount} ${requestedCount === 1 ? 'criterion' : 'criteria'}`);
    } else {
      parts.push('evidence not verified');
    }
  }

  const labels = {
    complete: {
      status: 'Evidence complete',
      report: 'Grounded report',
      meta: 'grounded vs CV + notes',
      aria: 'Open shareable grounded candidate report',
    },
    stored: {
      status: 'Stored evidence',
      report: 'Evidence-backed report',
      meta: 'grounded with stored role evidence',
      aria: 'Open shareable evidence-backed candidate report',
    },
    partial: {
      status: 'Partial evidence',
      report: 'Partially grounded report',
      meta: 'partial evidence coverage',
      aria: 'Open shareable partially grounded candidate report',
    },
    score_only: {
      status: 'Score only',
      report: 'Score-ranked shortlist',
      meta: 'not evidence checked',
      aria: 'Open shareable score-ranked candidate report',
    },
    unavailable: {
      status: 'Evidence unavailable',
      report: 'Unverified shortlist',
      meta: 'evidence check unavailable',
      aria: 'Open shareable candidate report with unverified evidence',
    },
  };

  return {
    kind,
    ...labels[kind],
    summary: parts.join(' · '),
    unchecked,
  };
}

export default function CandidateEvidenceCard({ data, detailed = false, showReportLink = true }) {
  if (!data || !Array.isArray(data.candidates)) return null;
  const spec = data.spec || {};
  const candidates = data.candidates;
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const rankLabel = RANK_LABELS[data.rank_by || spec.ranking_key] || 'score';
  const coverage = evidenceCoverage(data, candidates, rankLabel);
  const shown = data.shown ?? candidates.length;
  const excluded = data.excluded || {};
  const hidden = excluded.required_total ?? excluded.not_met_total ?? 0;
  const hiddenBy = Array.isArray(excluded.by_criterion) ? excluded.by_criterion : [];
  const requiredCriteria = cleanTextList(data.required_criteria);
  const preferredCriteria = cleanTextList(data.preferred_criteria);
  const hasRequiredCriteria = requiredCriteria.length > 0;
  const deepChecked = countValue(data.deep_checked) ?? countValue(data.screened) ?? 0;
  const hasVerifiedRequiredEvidence = hasRequiredCriteria && candidates.length > 0 && (
    candidates.every((candidate) => candidateMeetsRequiredCriteria(candidate, requiredCriteria))
  );
  const verificationUnavailableStatuses = new Set([
    'parser_failed',
    'required_criteria_unchecked',
    'rerank_skipped',
    'verification_unavailable',
  ]);
  const requiredVerificationUnavailable = hasRequiredCriteria
    && verificationUnavailableStatuses.has(data.search_status);
  const requiredKeys = new Set(requiredCriteria.map(criterionKey));
  const preferredKeys = new Set(preferredCriteria.map(criterionKey));
  // Rediscovery mode (screen_pool_against_requirement): screened the scored
  // history against a NEW requirement, ranked by fit to THAT (not the role
  // score) — the header says so, and a bounded window was deep-checked.
  const isRediscovery = data.mode === 'rediscovery';
  const population = data.database_matches ?? data.total_matched;
  const rankingMeta = isRediscovery
    ? `ranked by fit to your requirement${
      typeof population === 'number'
        ? ` · ${population} database matches${
          typeof data.pool_size === 'number' ? ` across ${data.pool_size} scored applications` : ''
        }`
        : ''
    }`
    : hasVerifiedRequiredEvidence
      ? `verified required evidence · explicit preferences · query relevance · existing ${rankLabel} shown for context${
        typeof data.total_matched === 'number' ? ` · ${data.total_matched} in pool` : ''
      } · ${coverage.meta}`
      : hasRequiredCriteria && shown === 0 && deepChecked > 0
        ? `required evidence checked · no verified matches${
          typeof data.total_matched === 'number' ? ` · ${data.total_matched} in pool` : ''
        } · ${coverage.meta}`
      : hasRequiredCriteria && shown > 0
        ? `required evidence remains unverified · explicit preferences · query relevance · existing ${rankLabel} shown for context${
          typeof data.total_matched === 'number' ? ` · ${data.total_matched} in pool` : ''
        } · ${coverage.meta}`
      : `ranked by ${rankLabel}${typeof data.total_matched === 'number' ? ` · ${data.total_matched} in pool` : ''} · ${coverage.meta}`;
  const statusTone = coverage.kind === 'complete'
    ? 'success'
    : coverage.kind === 'partial' || coverage.kind === 'unavailable'
      ? 'warning'
      : coverage.kind === 'score_only'
        ? 'neutral'
        : 'info';
  const reportFooter = showReportLink && data.report_url ? (
    <>
      <div className="ev-report-copy">
        <span className="ev-report-label">{coverage.report}</span>
        <span className="ev-report-note">{coverage.summary}</span>
      </div>
      <a
        className="ev-report-link"
        href={data.report_url}
        target="_blank"
        rel="noreferrer"
        aria-label={coverage.aria}
      >
        Open shareable report <span aria-hidden="true">↗</span>
      </a>
    </>
  ) : null;

  return (
    <ChatArtifact
      className="ev-card"
      icon={FileSearch}
      eyebrow={isRediscovery ? 'Rediscovery' : 'Candidate shortlist'}
      title={
        isRediscovery
          ? shown === 0 && requiredVerificationUnavailable
            ? 'Unable to verify matches'
            : `${shown} shown`
          : data.search_status === 'no_structural_matches'
            ? 'No matching candidates'
          : hasRequiredCriteria
            ? shown === 0
              ? requiredVerificationUnavailable
                ? 'Unable to verify matches'
                : data.search_status === 'no_structural_matches'
                  ? 'No matching candidates'
                  : 'No verified matches'
              : hasVerifiedRequiredEvidence
                ? `${shown} verified ${shown === 1 ? 'match' : 'matches'}`
                : `${shown} unverified ${shown === 1 ? 'candidate' : 'candidates'}`
            : `Top ${shown}`
      }
      summary={spec.echo || (isRediscovery ? spec.query : null)}
      meta={rankingMeta}
      status={{ label: coverage.status, detail: coverage.summary, tone: statusTone }}
      footer={reportFooter}
      flush
    >
      {coverage.unchecked.length ? (
        <div className="ev-unchecked">
          <strong>Unchecked criteria:</strong> {coverage.unchecked.join(', ')}
        </div>
      ) : null}

      {hidden > 0 ? (
        <div className="ev-filtered">
          {hidden} not shown — no verified match for{' '}
          {hiddenBy.length
            ? hiddenBy.map((b) => `${b.criterion} (${b.count})`).join(', ')
            : 'a requirement'}
        </div>
      ) : null}

      {warnings.length ? (
        <div className="ev-warn">
          {warnings.map((w, i) => (
            <span key={w?.code || i}>
              {(typeof w === 'string' ? w : w?.message) || 'Some results may be incomplete.'}
            </span>
          ))}
        </div>
      ) : null}

      <ol className="ev-list">
        {candidates.map((c, i) => (
          <li key={c.application_id || i} className="ev-cand">
            <div className="ev-cand-head">
              <span className="ev-rank">#{c.rank || i + 1}</span>
              {c.frontend_url ? (
                <a
                  className="ev-name"
                  href={c.frontend_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {c.candidate_name || 'Candidate'}
                  <span className="ev-name-ext" aria-hidden="true"> ↗</span>
                </a>
              ) : (
                <span className="ev-name">{c.candidate_name || 'Candidate'}</span>
              )}
              {typeof c.taali_score === 'number' ? (
                <span className={`ev-pill ${scoreClass(c.taali_score)}`}>
                  {hasRequiredCriteria && !data.role_id ? 'Existing role score' : 'Taali'}{' '}
                  {Math.round(c.taali_score)}
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
                  <CriterionRow
                    key={j}
                    c={cr}
                    priority={
                      requiredKeys.has(criterionKey(cr.criterion))
                        ? 'required'
                        : preferredKeys.has(criterionKey(cr.criterion))
                          ? 'preferred'
                          : null
                    }
                  />
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>

      {isRediscovery &&
      Array.isArray(data.rescore_candidate_ids) &&
      data.rescore_candidate_ids.length ? (
        <PoolRescore
          requirementText={(spec && spec.query) || ''}
          candidates={candidates.filter((c) =>
            data.rescore_candidate_ids.includes(c.application_id),
          )}
        />
      ) : null}

    </ChatArtifact>
  );
}
