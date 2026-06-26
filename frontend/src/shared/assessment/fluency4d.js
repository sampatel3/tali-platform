// THE canonical Taali scorecard: one set of 5 dimensions used everywhere
// (candidate report, radar, marketing, glossary, docs). Anchored on Anthropic's
// AI-Fluency framework — the "4 Ds" (Delegation, Description, Discernment,
// Diligence) — plus a Deliverable/outcome axis for what was actually shipped.
//
// This REPLACES the old competing vocabularies (the 6-axis "fluency" radar and
// the 8 "canonical dimensions"). Those, plus the per-dimension rubric grades and
// the ~30 heuristic metrics, are now EVIDENCE that hangs *under* these 5 axes —
// not separate top-level scorecards.
//
// Each axis score (0–100) is sourced, in priority order:
//   1. the rubric rollup score_breakdown.rubric_grading.fluency_4d[axis]
//      (the authoritative graded signal, when the task has a rubric), else
//   2. the mean of the heuristic atomic *_score columns mapped to the axis
//      (0–10 on the assessment row, ×10), else
//   3. null ("no signal yet").

export const FLUENCY_4D_AXES = [
  {
    key: 'delegation',
    label: 'Delegation',
    blurb: 'Deciding what to own vs. hand to the agent, and steering the load-bearing design calls.',
    // Heuristic fallback columns (0–10 on the assessment row).
    sources: ['design_thinking_score', 'requirement_comprehension_score'],
  },
  {
    key: 'description',
    label: 'Description',
    blurb: 'Directing the agent and communicating clearly — the prompts, the context provided, and the write-up.',
    sources: ['prompt_quality_score', 'context_utilization_score', 'written_communication_score'],
  },
  {
    key: 'discernment',
    label: 'Discernment',
    blurb: "Critically evaluating the agent's output — catching and overriding what's wrong.",
    sources: ['debugging_strategy_score', 'learning_velocity_score'],
  },
  {
    key: 'diligence',
    label: 'Diligence',
    blurb: 'Verifying before claiming done, and owning the shipped result and its residual risk.',
    sources: ['error_recovery_score', 'independence_score', 'prompt_efficiency_score', 'time_efficiency_score'],
  },
  {
    key: 'deliverable',
    label: 'Deliverable',
    blurb: 'Correctness and quality of what was actually shipped.',
    sources: ['code_quality_score'],
  },
];

// Short labels for the radar (kept tight so a 5-spoke chart never overflows).
export const FLUENCY_4D_LABELS = FLUENCY_4D_AXES.map((a) => a.label);

// The backend sends explicit JSON null for a no-signal axis, and
// Number(null) === 0 (finite), so reject null/undefined up front rather than
// letting them coerce to a misleading 0.
const num = (v) => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

// Tolerant extraction of the raw rubric fluency_4d object from an assessment's
// score_breakdown — nested under rubric_grading (where the backend writes it)
// or promoted to the top level, and tolerant of a JSON-string score_breakdown.
export const rawFluency4d = (assessment) => {
  let sb = assessment?.score_breakdown;
  if (typeof sb === 'string') {
    try {
      sb = JSON.parse(sb);
    } catch {
      return null;
    }
  }
  if (!sb || typeof sb !== 'object') return null;
  if (sb.fluency_4d && typeof sb.fluency_4d === 'object') return sb.fluency_4d;
  if (sb.rubric_grading && typeof sb.rubric_grading === 'object' && sb.rubric_grading.fluency_4d) {
    return sb.rubric_grading.fluency_4d;
  }
  return null;
};

// THE scorecard. Returns the ordered five axes
// [{ key, label, blurb, score (0–100|null), hasSignal, source: 'rubric'|'heuristic'|null }],
// each sourced rubric-first with a heuristic-column fallback (see header).
// Returns null only when NOTHING is scorable (e.g. an unscored assessment) so
// callers can cleanly hide the scorecard.
export const computeScorecard = (assessment) => {
  if (!assessment) return null;
  const raw = rawFluency4d(assessment); // authoritative rubric rollup, may be null
  let any = false;
  const axes = FLUENCY_4D_AXES.map(({ key, label, blurb, sources }) => {
    let score = raw ? num(raw[key]) : null;
    let source = score != null ? 'rubric' : null;
    if (score == null) {
      const vals = (sources || []).map((f) => num(assessment[f])).filter((v) => v != null);
      if (vals.length) {
        score = (vals.reduce((a, b) => a + b, 0) / vals.length) * 10; // 0–10 → 0–100
        source = 'heuristic';
      }
    }
    if (score != null) any = true;
    return {
      key,
      label,
      blurb,
      score: score == null ? null : Math.round(score * 10) / 10,
      hasSignal: score != null,
      source,
    };
  });
  return any ? axes : null;
};

// Rubric-only view (no heuristic fallback). Retained for surfaces that want to
// show ONLY the graded 4 Ds and hide the panel entirely when there's no rubric.
export const readFluency4d = (assessment) => {
  const raw = rawFluency4d(assessment);
  if (!raw || typeof raw !== 'object') return null;
  let any = false;
  const axes = FLUENCY_4D_AXES.map(({ key, label, blurb }) => {
    const score = num(raw[key]);
    if (score != null) any = true;
    return { key, label, blurb, score, hasSignal: score != null };
  });
  return any ? axes : null;
};
