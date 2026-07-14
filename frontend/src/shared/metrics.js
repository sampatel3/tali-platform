// Single source of truth for the candidate-pipeline funnel and KPI
// formatting. Every metric surface — the home role card, the job-detail
// funnel summary, the home "Pipeline" standing strip, and the org / role KPI
// strips — imports from here so labels, ordering, number/money formatting and
// colour semantics stay identical wherever a number is surfaced.

// Canonical funnel — forward order, mirroring the landing-page flow
// (Source · Screen · Assess · Decide/Hand-back). Stages are where a candidate
// IS; `advanced` and `rejected` are terminal OUTCOMES, divided off from the
// active flow. `Invited` is the whole Assess step: it folds in the old
// `in_assessment` (started) AND `completed` (the old `review`, assessment
// done) — completed is shown as a sub-count of Invited, not its own tile, so
// the assessment lifecycle reads as one nested funnel. `completed` stays a
// backend bucket (see FUNNEL_INVITED_SUBSUMED / OPEN_FUNNEL_STAGE_KEYS) — it's
// just not a top-level tile.
export const PIPELINE_FUNNEL_STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'scored', label: 'Scored' },
  { key: 'invited', label: 'Invited' },
  { key: 'advanced', label: 'Advanced' },
  { key: 'rejected', label: 'Rejected' },
];

// Buckets the `Invited` tile subsumes — its displayed value is the sum of these
// (assessment out + in progress + done), so a `completed` candidate still
// counts in the Assess step instead of vanishing when its tile was removed.
export const FUNNEL_INVITED_SUBSUMED = ['invited', 'completed'];

// The value the `Invited` tile shows: everyone who reached the assessment step.
export const invitedStageValue = (stageCounts) =>
  FUNNEL_INVITED_SUBSUMED.reduce((acc, k) => acc + (Number(stageCounts?.[k]) || 0), 0);

// Agent pending-decision types, mapped to the stage they act on. The funnel's
// "awaiting your decision" row shows these as chips under each stage —
// candidates the agent has a recommendation for, awaiting your approval.
// (decision_type values come from AgentDecision.)
// Note: `advance` lives under Scored, not Completed — in Tali an advance is
// usually a fast-track hand-off of a strong *scored* candidate to the recruiter
// (skipping the assessment). Completed = candidates who actually finished an
// assessment; they surface as "decision pending" until acted on.
// `tone` colour-codes the chip, matching the decision-feed badge vocabulary:
// advance/send = purple (positive/action), reject/pre-screen = grey (terminal).
// NOT traffic-light green/red — see TYPE_BADGE in features/home/atoms.jsx.
export const FUNNEL_DECISION_GATES = [
  // pre-screen rejects sit under Scored: a pre-screened candidate WAS
  // evaluated (the cheap gate), and the stage counts bucket them as Scored.
  { stage: 'scored', key: 'pre_screen', label: 'pre-screen reject', tone: 'prescreen', types: ['skip_assessment_reject'] },
  { stage: 'scored', key: 'send', label: 'send assessment', tone: 'send', types: ['send_assessment', 'resend_assessment_invite'] },
  { stage: 'scored', key: 'advance', label: 'advance', tone: 'advance', types: ['advance_to_interview'] },
  { stage: 'scored', key: 'reject', label: 'reject', tone: 'reject', types: ['reject'] },
];

// The stages where a candidate without an agent recommendation still counts as
// "decision pending" — i.e. scored/completed but the agent hasn't ruled yet.
const DECISION_PENDING_STAGES = ['scored', 'completed'];

// Normalize a decisions arg (a list of {decision_type} objects OR a
// {decision_type: count} map) into a counts-by-type map.
const decisionCountsByType = (decisions) => {
  if (Array.isArray(decisions)) {
    const c = {};
    for (const d of decisions) { const t = d?.decision_type; if (t) c[t] = (c[t] || 0) + 1; }
    return c;
  }
  return decisions || {};
};

// The funnel's "awaiting your decision" row. Under each stage: the agent's
// pending decisions by type (chips like "25 send assessment", "8 advance"),
// PLUS a "decision pending" chip for candidates at a decision stage the agent
// hasn't ruled on yet (e.g. "144 decision pending"). Keyed by stage so each
// chip stacks under the stage cell it acts on.
export const funnelDecisionRow = (stageCounts, decisions) => {
  const counts = decisionCountsByType(decisions);
  const sc = stageCounts || {};
  const byStage = {};
  const push = (stage, chip) => { (byStage[stage] = byStage[stage] || []).push(chip); };
  for (const gate of FUNNEL_DECISION_GATES) {
    const count = gate.types.reduce((acc, t) => acc + (Number(counts[t]) || 0), 0);
    if (count > 0) push(gate.stage, { key: gate.key, label: gate.label, count, tone: gate.tone });
  }
  // "Not yet decided" = scored candidates that carry NO agent decision yet
  // (the agent hasn't ruled — usually a paused role). Prefer the backend's TRUE
  // count (sc.not_yet_decided): the old fallback derived it as scored − pending,
  // which over-counted resolved candidates AND the cv_match_scored_at "scored"
  // basis (which includes pre-screen-filtered candidates with no real score).
  const chip = (count) => ({
    key: 'pending',
    label: 'not yet decided',
    count,
    tone: 'pending',
    tip: "Scored candidates the agent hasn't ruled on yet — usually because the agent is paused on this role. Each gets a decision (from its current score) when the agent runs; it isn't waiting on you.",
  });
  const explicit = Number(sc.not_yet_decided);
  if (Number.isFinite(explicit)) {
    if (explicit > 0) push('scored', chip(explicit));
  } else {
    // Legacy fallback when the backend hasn't supplied the count.
    for (const stage of DECISION_PENDING_STAGES) {
      const decided = (byStage[stage] || []).reduce((acc, c) => acc + c.count, 0);
      const pending = Math.max(0, (Number(sc[stage]) || 0) - decided);
      if (pending > 0) push(stage, chip(pending));
    }
  }
  return byStage;
};

// "Awaiting you" = the agent's pending recommendations (HITL) — the sum of the
// typed pending-decision counts (send / advance / reject / pre-screen reject).
// This is the actionable queue the recruiter must approve, override or teach,
// and what the nav badge + home hero count. Candidates the agent hasn't ruled
// on yet are NOT here — they're "decision pending" (see decisionPendingFromCounts).
export const awaitingHitlFromDecisions = (decisions) => {
  const counts = decisionCountsByType(decisions);
  return Object.values(counts).reduce((acc, n) => acc + (Number(n) || 0), 0);
};

// Total "decision pending" — candidates at a decision stage the agent hasn't
// ruled on yet (the funnel's grey "N decision pending" chips, summed). The
// remainder of Scored + Completed after the agent's typed recommendations;
// still the agent's to-do, not yet awaiting you. Derived from funnelDecisionRow
// so it always reconciles with the chips the funnel renders.
export const decisionPendingFromCounts = (stageCounts, decisions) => {
  const row = funnelDecisionRow(stageCounts, decisions);
  let total = 0;
  for (const stage of DECISION_PENDING_STAGES) {
    for (const chip of row[stage] || []) {
      if (chip.key === 'pending') total += chip.count;
    }
  }
  return total;
};

// Workable stages that mean a recruiter has advanced the candidate past Tali's
// hand-off (interview/offer/hired) — mirrors the backend's
// POST_HANDOVER_WORKABLE_STAGES. Such candidates DISPLAY as 'advanced' in the
// funnel for alignment with Workable, even though Tali's pipeline_stage (used by
// backend decision/calibration services) stays 'applied'.
const POST_HANDOVER_WORKABLE_STAGES = new Set([
  'phone_screen', 'phone_interview', 'first_stage', 'interview', 'technical',
  'technical_interview', 'final_interview', 'onsite', 'presentation',
  'assessment', 'offer', 'offer_extended', 'offer_accepted', 'hired',
]);

export const isPostHandoverWorkableStage = (value) =>
  POST_HANDOVER_WORKABLE_STAGES.has(
    String(value || '').trim().toLowerCase().replace(/-/g, '_').replace(/ /g, '_'),
  );

// Bucket a single application row into a funnel stage — mirrors the backend's
// funnel_bucket_for so the kanban / stage filters group candidates the same
// way the funnel counts them. "Scored" = stage `applied` and evaluated (a CV
// score or a pre-screen score — filtered candidates included).
export const applicationFunnelBucket = (application) => {
  const outcome = String(application?.application_outcome || '').toLowerCase();
  if (outcome === 'rejected') return 'rejected';
  // A recruiter advance in Workable wins — the furthest stage the candidate has
  // reached — regardless of Tali's own pipeline_stage.
  if (isPostHandoverWorkableStage(application?.workable_stage)) return 'advanced';
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  // A `sourced` prospect is pre-applied and un-scored — its OWN bucket, never
  // folded into `applied` (mirrors the backend funnel_bucket_for).
  if (stage === 'sourced') return 'sourced';
  if (stage === 'applied') {
    // Evaluated = real cv_match score OR a genuinely-RUN pre-screen (the list
    // payload serializes the score as `pre_screen_score`; `pre_screen_score_100`
    // is the raw column name kept for payloads that carry it). The
    // pre_screen_run_at guard matches the backend's scored_expr: the pre-screen
    // score field is also a display value derived from a full cv_match
    // snapshot, and score invalidation nulls only cv_match_score — without the
    // guard an invalidated candidate would keep reading as Scored.
    const preScreened = (application?.pre_screen_score_100 != null || application?.pre_screen_score != null)
      && application?.pre_screen_run_at != null;
    const scored = application?.cv_match_score != null || preScreened;
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
  // End-of-month straight-line projection from month-to-date spend. Uses UTC
  // day-of-month so the projection matches the UTC calendar-month window the
  // backend measures `spent` over (budget_guard.month_start()) — otherwise the
  // projection drifts by the viewer's timezone near month edges.
  const now = new Date();
  const day = now.getUTCDate();
  const daysInMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate();
  const projectedCents = day > 0 ? Math.round((spent * daysInMonth) / day) : spent;
  return {
    value: formatMoneyUsd(spent),
    unit: hasCap ? `/ ${formatMoneyUsd(cap)}` : null,
    pct: barPct,
    sub: hasCap ? `${rawPct}% used · projected ${formatMoneyUsd(projectedCents)} this month` : 'no cap set',
    over: rawPct != null && rawPct > 100,
  };
};
