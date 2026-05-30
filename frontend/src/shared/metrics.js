// Single source of truth for the candidate-pipeline funnel and KPI
// formatting. Every metric surface — the home role card, the job-detail
// funnel summary, the home "Pipeline" standing strip, and the org / role KPI
// strips — imports from here so labels, ordering, number/money formatting and
// colour semantics stay identical wherever a number is surfaced.

// Canonical funnel — forward order, always all six stages. `in_assessment`
// is labelled "Assessing" and `review` is "Review" (never "In assessment" /
// "In review"). `rejected` is the terminal bucket, rendered divided-off from
// the active stages.
export const PIPELINE_FUNNEL_STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'invited', label: 'Invited' },
  { key: 'in_assessment', label: 'Assessing' },
  { key: 'review', label: 'Review' },
  { key: 'advanced', label: 'Advanced' },
  { key: 'rejected', label: 'Rejected' },
];

// The open (in-flight) stages — everything except the terminal `rejected`
// bucket. Summing these gives the "In pipeline" count, the same denominator
// the role list's active_candidates_count represents.
export const OPEN_FUNNEL_STAGE_KEYS = ['applied', 'invited', 'in_assessment', 'review', 'advanced'];

// "In pipeline" total from a stage_counts map — sum of the open stages.
// Used by both the org strip (summed across roles) and the role strip so the
// number means the same thing on every surface.
export const inPipelineFromStageCounts = (stageCounts) => {
  const sc = stageCounts || {};
  return OPEN_FUNNEL_STAGE_KEYS.reduce((acc, key) => acc + (Number(sc[key]) || 0), 0);
};

// Tone for a funnel cell — drives the single purple / ink / mute rule:
//   'attn' → needs you (Review with anyone waiting) → purple
//   'term' → terminal bucket (Rejected) → muted
//   null   → neutral volume → ink
export const funnelStageTone = (key, value) => {
  if (key === 'review' && Number(value) > 0) return 'attn';
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
