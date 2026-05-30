// Single source of truth for the candidate-pipeline funnel and KPI
// formatting. Every metric surface — the home role card, the job-detail
// funnel summary, the home "Pipeline" standing strip, and the org / role KPI
// strips — imports from here so labels, ordering, number/money formatting and
// colour semantics stay identical wherever a number is surfaced.

// Canonical funnel — forward order. Stages are where a candidate IS;
// `advanced` and `rejected` are terminal OUTCOMES, divided off from the
// active flow. Keys are the display buckets the backend's role_pipeline_counts
// emits (applied/scored split by whether the CV is scored; `invited` folds in
// the old `in_assessment`; `completed` = the old `review`). "Assessing" is
// gone — a candidate is Invited (assessment out) or Completed.
export const PIPELINE_FUNNEL_STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'scored', label: 'Scored' },
  { key: 'invited', label: 'Invited' },
  { key: 'completed', label: 'Completed' },
  { key: 'advanced', label: 'Advanced' },
  { key: 'rejected', label: 'Rejected' },
];

// Decision stages — where a candidate needs YOUR call. A candidate at Scored
// is awaiting a send-assessment / reject decision; at Completed, advance /
// reject. This is the recruiter's to-do and is independent of whether the
// agent is on (when on, the agent drains these by acting). Applied awaits
// scoring and Invited awaits the candidate, so neither is a recruiter decision.
export const FUNNEL_DECISION_STAGES = [
  { stage: 'scored', action: 'send / reject' },
  { stage: 'completed', action: 'advance / reject' },
];

// "Awaiting you" = candidates sitting at a decision stage (Scored + Completed).
export const awaitingFromStageCounts = (stageCounts) => {
  const sc = stageCounts || {};
  return FUNNEL_DECISION_STAGES.reduce((acc, d) => acc + (Number(sc[d.stage]) || 0), 0);
};

// The funnel's "awaiting your decision" row, derived from stage counts: under
// each decision stage, the count of candidates there awaiting your call (+ the
// action). Keyed by stage so the row aligns under the stage cell.
export const funnelDecisionRow = (stageCounts) => {
  const sc = stageCounts || {};
  const byStage = {};
  for (const d of FUNNEL_DECISION_STAGES) {
    byStage[d.stage] = { count: Number(sc[d.stage]) || 0, action: d.action };
  }
  return byStage;
};

// Bucket a single application row into a funnel stage — mirrors the backend's
// funnel_bucket_for so the kanban / stage filters group candidates the same
// way the funnel counts them. "Scored" = stage `applied` with a CV score.
export const applicationFunnelBucket = (application) => {
  const outcome = String(application?.application_outcome || '').toLowerCase();
  if (outcome === 'rejected') return 'rejected';
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'applied') {
    const scored = application?.cv_match_score != null || application?.pre_screen_score_100 != null;
    return scored ? 'scored' : 'applied';
  }
  if (stage === 'invited' || stage === 'in_assessment') return 'invited';
  if (stage === 'review') return 'completed';
  if (stage === 'advanced') return 'advanced';
  return 'applied';
};

// The open (in-flight) stages — everything except the terminal `rejected`
// bucket. Summing these gives the "In pipeline" count, the same denominator
// the role list's active_candidates_count represents.
export const OPEN_FUNNEL_STAGE_KEYS = ['applied', 'scored', 'invited', 'completed', 'advanced'];

// "In pipeline" total from a stage_counts map — sum of the open stages.
// Used by both the org strip (summed across roles) and the role strip so the
// number means the same thing on every surface.
export const inPipelineFromStageCounts = (stageCounts) => {
  const sc = stageCounts || {};
  return OPEN_FUNNEL_STAGE_KEYS.reduce((acc, key) => acc + (Number(sc[key]) || 0), 0);
};

// Tone for a funnel cell — drives the single purple / ink / mute rule:
//   'attn' → needs you (a decision stage — Scored or Completed — with anyone
//            waiting) → purple
//   'term' → terminal outcome (Rejected) → muted
//   null   → neutral volume → ink
export const funnelStageTone = (key, value) => {
  if ((key === 'scored' || key === 'completed') && Number(value) > 0) return 'attn';
  if (key === 'rejected') return 'term';
  return null;
};

// Counts get thousands separators (52594 → "52,594"). Non-finite → "0".
export const formatCount = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return '0';
  return Math.round(n).toLocaleString('en-US');
};

// Whole-dollar USD from cents, with separators. KPI surfaces never show
// fractional dollars: 60900 → "$609", 120000 → "$1,200".
export const formatMoneyUsd = (cents) => {
  const dollars = Number(cents || 0) / 100;
  if (!Number.isFinite(dollars)) return '$0';
  return `$${Math.round(dollars).toLocaleString('en-US')}`;
};

// Canonical budget-tile content from spent / cap cents. One format
// everywhere: a primary "$spent", a muted unit "/ $cap" (or null when no cap
// is set), a 0-100 bar percentage, and a sub-line "NN% · proj $X EOM" (or
// "no cap"). `over` flags >100% so callers can colour the bar.
export const budgetTile = (spentCents, capCents) => {
  const spent = Number(spentCents || 0);
  const cap = Number(capCents || 0);
  const hasCap = cap > 0;
  const rawPct = hasCap ? Math.round((spent / cap) * 100) : null;
  const barPct = rawPct != null ? Math.min(100, rawPct) : null;
  // End-of-month straight-line projection from month-to-date spend.
  const now = new Date();
  const day = now.getDate();
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const projectedCents = day > 0 ? Math.round((spent * daysInMonth) / day) : spent;
  return {
    value: formatMoneyUsd(spent),
    unit: hasCap ? `/ ${formatMoneyUsd(cap)}` : null,
    pct: barPct,
    sub: hasCap ? `${rawPct}% · proj ${formatMoneyUsd(projectedCents)} EOM` : 'no cap',
    over: rawPct != null && rawPct > 100,
  };
};
