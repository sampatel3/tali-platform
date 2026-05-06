// Roll up the 11 atomic Assessment scoring axes into the 6 canvas radar
// dimensions. The atomic scores live on the Assessment record (see
// backend/app/models/assessment.py) and are 0–10. Returns an array shaped
// for <RadarChart values=...>.
//
// Mapping reflects the canvas labels (Systems design / Code craft /
// Reasoning under pressure / AI collaboration / Release safety /
// Communication). Each rollup is a simple mean of related atomic axes —
// when product needs a workspace-configurable weighting, promote this
// formula to backend and store on the workspace.

// Labels stay short so they fit a six-axis radar without overflowing the
// viewBox. Long-form names ("Reasoning under pressure", "AI collaboration")
// surface in the per-axis card / tooltip when needed.
const FLUENCY_AXES = [
  {
    key: 'sysdesign',
    label: 'Systems',
    sources: ['design_thinking_score', 'requirement_comprehension_score'],
  },
  {
    key: 'codecraft',
    label: 'Code craft',
    sources: ['code_quality_score'],
  },
  {
    key: 'reasoning',
    label: 'Reasoning',
    sources: ['debugging_strategy_score', 'error_recovery_score', 'learning_velocity_score'],
  },
  {
    key: 'aicollab',
    label: 'AI collab',
    sources: ['prompt_quality_score', 'prompt_efficiency_score', 'context_utilization_score', 'independence_score'],
  },
  {
    key: 'release',
    label: 'Release',
    sources: ['error_recovery_score', 'time_efficiency_score'],
  },
  {
    key: 'communication',
    label: 'Comms',
    sources: ['written_communication_score'],
  },
];

const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : null);

// Returns the 6-axis fluency rollup, or null if nothing is scorable.
// Atomic axes are stored 0–10 in the backend; we multiply ×10 here so every
// downstream display (radar, score pills, dimension bars) uses the unified
// 0–100 scale per HANDOFF v2 §6.
export const computeFluencyAxes = (assessment) => {
  if (!assessment) return null;
  let any = false;
  const axes = FLUENCY_AXES.map(({ key, label, sources }) => {
    const values = sources.map((field) => num(assessment[field])).filter((v) => v != null);
    if (values.length === 0) return { k: key, label, v: 0, hasSignal: false };
    any = true;
    const meanOnTen = values.reduce((acc, x) => acc + x, 0) / values.length;
    return { k: key, label, v: meanOnTen * 10, hasSignal: true };
  });
  return any ? axes : null;
};

export const FLUENCY_AXIS_LABELS = FLUENCY_AXES.map((a) => a.label);
