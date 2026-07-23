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
// An axis score (0–100) comes from ONE place: the rubric rollup
// score_breakdown.rubric_grading.fluency_4d[axis]. An axis with no graded
// rubric dimension scores null — "not assessed" — and renders as "—".
//
// The heuristic atomic *_score columns are NOT a fallback score. Several are
// aliases of the same prompt-word-count formula (and code_quality_score is a
// hardcoded constant), so averaging them produced a number that looked graded
// but measured almost nothing. They are still returned, per axis, as
// ``telemetry``: behavioural signals shown as evidence under the axis and
// explicitly labelled as not a grade. Every production task now grades all
// five axes (backend/scripts/check_fluency_coverage.py gates it), so telemetry
// only surfaces on assessments scored before that landed, or on an off-catalog
// task.

export const FLUENCY_4D_AXES = [
  {
    key: 'delegation',
    label: 'Delegation',
    blurb: 'Deciding what to own vs. hand to the agent, and steering the load-bearing design calls.',
    // Behavioural telemetry columns (0–10 on the assessment row). Evidence
    // shown under the axis — never a score. See computeScorecard.
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
    // No telemetry source. The obvious candidate, code_quality_score, is set to
    // a hardcoded 5.0 by submission_runtime on every assessment, so surfacing it
    // would show a constant as if it were a measurement. Deliverable is graded
    // by the rubric on every production task.
    sources: [],
  },
];

// Short labels for the radar (kept tight so a 5-spoke chart never overflows).
export const FLUENCY_4D_LABELS = FLUENCY_4D_AXES.map((a) => a.label);

const AXIS_KEYS = new Set(FLUENCY_4D_AXES.map((a) => a.key));

// Mirror of the backend's fluency_axis_for_dimension
// (backend/app/components/assessments/fluency_axes.py): map one rubric-dimension
// SPEC (an entry of assessment.evaluation_rubric) to the scorecard axis its
// grade rolls up into. Precedence: explicit ``fluency`` > grader > lens >
// back-compat default. THIS MUST STAY IN SYNC WITH THE BACKEND MAP — if it
// drifts, the UI groups criteria under a different axis than the stored
// fluency_4d was rolled up into.
const GRADER_AXES = {
  interrogation_outcome: 'delegation', // decision-ownership
  practice_outcome: 'description', // observed AI-native practice
  comprehension_outcome: 'discernment', // post-submit understanding check
};
const LENS_AXES = {
  decision: 'delegation',
  delegation: 'delegation',
  description: 'description',
  discernment: 'discernment',
  diligence: 'diligence',
  deliverable: 'deliverable',
  practice: 'description',
};

export const axisForRubricDimension = (spec) => {
  if (!spec || typeof spec !== 'object') return 'delegation';
  const explicit = String(spec.fluency || '').trim().toLowerCase();
  if (AXIS_KEYS.has(explicit)) return explicit;
  const grader = String(spec.grader || '').trim().toLowerCase();
  if (GRADER_AXES[grader]) return GRADER_AXES[grader];
  const lens = String(spec.lens || '').trim().toLowerCase();
  if (LENS_AXES[lens]) return LENS_AXES[lens];
  return 'delegation'; // unset / pre-lens-model spec
};

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
// [{ key, label, blurb, score (0–100|null), hasSignal, source: 'rubric'|null,
//   telemetry: [{ column, value }] }].
//
// ``score``/``hasSignal`` reflect GRADED signal only. ``telemetry`` carries the
// axis's heuristic columns (0–100) as evidence — never as a score. An axis with
// telemetry but no grade still reports hasSignal:false, so a caller that renders
// ``score`` cannot accidentally present a heuristic as a grade.
//
// Returns null only when NOTHING is scorable — no rubric grade and no telemetry
// (e.g. an unscored assessment) — so callers can cleanly hide the scorecard.
export const computeScorecard = (assessment) => {
  if (!assessment) return null;
  const raw = rawFluency4d(assessment); // authoritative rubric rollup, may be null
  let any = false;
  const axes = FLUENCY_4D_AXES.map(({ key, label, blurb, sources }) => {
    const score = raw ? num(raw[key]) : null;
    const telemetry = (sources || [])
      .map((column) => ({ column, value: num(assessment[column]) }))
      .filter(({ value }) => value != null)
      .map(({ column, value }) => ({ column, value: Math.round(value * 10 * 10) / 10 }));
    if (score != null || telemetry.length) any = true;
    return {
      key,
      label,
      blurb,
      score: score == null ? null : Math.round(score * 10) / 10,
      hasSignal: score != null,
      source: score != null ? 'rubric' : null,
      telemetry,
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
