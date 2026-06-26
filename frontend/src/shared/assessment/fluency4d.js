// Anthropic AI Fluency "4 Ds" (+ a Deliverable axis), surfaced from the
// backend rubric rollup at score_breakdown.rubric_grading.fluency_4d
// (computed by rubric_scoring.summarize_fluency_4d). Each axis is 0–100 or
// null ("no signal yet" — the task hasn't adopted a dimension that rolls up
// to that axis). This is additive to the existing 6-axis fluencyRollup; it
// does not replace it.
//
// Why these five: Anthropic's published AI-Fluency framework defines four
// human competencies for working with AI — Delegation, Description,
// Discernment, Diligence — and we add a Deliverable/outcome axis so the
// shipped artifact still shows alongside the collaboration skills.

export const FLUENCY_4D_AXES = [
  {
    key: 'delegation',
    label: 'Delegation',
    blurb: 'Deciding what to own vs. hand to the agent, and steering the load-bearing design calls.',
  },
  {
    key: 'description',
    label: 'Description',
    blurb: 'Prompting and context: describing the goal, process and constraints so the agent can act.',
  },
  {
    key: 'discernment',
    label: 'Discernment',
    blurb: "Critically evaluating the agent's output — catching and overriding what's wrong.",
  },
  {
    key: 'diligence',
    label: 'Diligence',
    blurb: 'Verifying before claiming done, and owning the shipped result and its residual risk.',
  },
  {
    key: 'deliverable',
    label: 'Deliverable',
    blurb: 'Correctness and quality of what was actually shipped.',
  },
];

// Note: the backend sends explicit JSON null for a no-signal axis, and
// Number(null) === 0 (finite), so we must reject null/undefined up front
// rather than letting them coerce to a misleading 0.
const num = (v) => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

// Tolerant extraction of the raw fluency_4d object from an assessment's
// score_breakdown. Handles the object being nested under rubric_grading
// (where the backend writes it) or promoted to the top level, and a
// score_breakdown that arrived as a JSON string.
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

// Returns the ordered five axes as [{ key, label, blurb, score, hasSignal }],
// or null when the assessment carries no fluency_4d rollup at all (e.g. a
// pre-rebase assessment, or one graded without a rubric). A present-but-all-
// null rollup also returns null so callers can cleanly hide the panel.
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
