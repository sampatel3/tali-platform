import { DIMENSIONS, toCanonicalId } from '../scoring/scoringDimensions';
import { FLUENCY_4D_AXES } from '../shared/assessment/fluency4d';

type FluencyAxisKey = 'delegation' | 'description' | 'discernment' | 'diligence' | 'deliverable';

type ScoringMeta = {
  label: string;
  description: string;
  // Which of the 5 canonical scorecard axes (the 4 Ds + Deliverable) this
  // heuristic metric is evidence for. The metrics are no longer a flat,
  // rival vocabulary — they hang under the 5 axes as evidence.
  axis?: FluencyAxisKey;
};

type ScoringGlossary = Record<string, ScoringMeta>;

type MetadataPayload = {
  categories?: Record<string, { label?: string; description?: string }>;
  metrics?: Record<string, { label?: string; description?: string }>;
};

export const SCORING_CATEGORY_GLOSSARY: ScoringGlossary = DIMENSIONS.reduce((acc, dimension) => {
  acc[dimension.id] = {
    label: dimension.label,
    description: dimension.longDescription,
  };
  return acc;
}, {} as ScoringGlossary);

// Each metric is grouped (via ``axis``) under one of the 5 canonical scorecard
// axes — the 4 Ds + Deliverable. The metrics are EVIDENCE for those axes, not a
// separate scorecard. Descriptions are unchanged from the original ~30 metrics.
export const SCORING_METRIC_GLOSSARY: ScoringGlossary = {
  // Delegation — deciding what to own vs. hand to the agent.
  design_score: { axis: 'delegation', label: 'Design Thinking', description: 'Evidence of architecture-level tradeoff and design consideration.' },
  pre_prompt_effort: { axis: 'delegation', label: 'Self-Attempt Rate', description: 'Signals independent effort before requesting AI help.' },
  first_prompt_delay: { axis: 'delegation', label: 'Thinks Before Asking', description: 'Whether the candidate attempts initial reasoning before first AI request.' },

  // Description — directing the agent and communicating clearly.
  prompt_length_quality: { axis: 'description', label: 'Prompt Length', description: 'Whether prompts stay in a useful length range for high-quality responses.' },
  question_clarity: { axis: 'description', label: 'Clear Questions', description: 'How often prompts contain clear, answerable questions.' },
  prompt_specificity: { axis: 'description', label: 'Specificity', description: 'How targeted and concrete prompts are for the problem at hand.' },
  vagueness_score: { axis: 'description', label: 'Avoids Vagueness', description: 'Penalizes ambiguous prompts that lack actionable detail.' },
  code_context_rate: { axis: 'description', label: 'Includes Code', description: 'How often prompts include relevant code snippets.' },
  error_context_rate: { axis: 'description', label: 'Includes Errors', description: 'How often prompts include actual error output or stack traces.' },
  reference_rate: { axis: 'description', label: 'References', description: 'How often prompts reference specific files/lines or implementation points.' },
  attempt_mention_rate: { axis: 'description', label: 'Prior Attempts', description: 'How often prompts mention what has already been tried.' },
  grammar_score: { axis: 'description', label: 'Grammar', description: 'Basic writing quality and grammatical correctness in prompts.' },
  readability_score: { axis: 'description', label: 'Readability', description: 'How easy prompts are to read and interpret.' },
  tone_score: { axis: 'description', label: 'Professional Tone', description: 'Whether communication tone remains professional and focused.' },

  // Discernment — critically evaluating and applying the agent's output.
  debugging_score: { axis: 'discernment', label: 'Debugging Strategy', description: 'Signals hypothesis-driven debugging and root-cause exploration.' },
  post_prompt_changes: { axis: 'discernment', label: 'Uses Responses', description: 'Evidence that candidate applies AI suggestions in code changes.' },
  wasted_prompts: { axis: 'discernment', label: 'Actionable Prompts', description: 'Fraction of prompts that resulted in meaningful forward movement.' },
  iteration_quality: { axis: 'discernment', label: 'Iterative Refinement', description: 'Whether follow-up prompts show refinement instead of repetition.' },

  // Diligence — efficient, self-directed progress and verification.
  tests_passed_ratio: { axis: 'diligence', label: 'Tests Passed', description: 'How many required tests passed out of the total test suite.' },
  time_compliance: { axis: 'diligence', label: 'Time Compliance', description: 'Whether the candidate completed within the assessment time limit.' },
  time_efficiency: { axis: 'diligence', label: 'Time Efficiency', description: 'How efficiently the candidate used available time.' },
  prompt_spacing: { axis: 'diligence', label: 'Spacing Between', description: 'Whether prompts are paced with implementation effort between requests.' },
  prompt_efficiency: { axis: 'diligence', label: 'Prompts/Test', description: 'Efficiency of prompts relative to delivered test progress.' },
  token_efficiency: { axis: 'diligence', label: 'Token Efficiency', description: 'How efficiently token budget is used across the session.' },

  // Deliverable — correctness and quality of what was shipped, incl. role fit.
  cv_job_match_score: { axis: 'deliverable', label: 'Overall Match', description: 'Overall CV/job fit estimate (reported to recruiters on a 0-100 scale, normalized internally for rubric weighting).' },
  skills_match: { axis: 'deliverable', label: 'Skills Alignment', description: 'Alignment between required technical skills and candidate profile (normalized internally for rubric weighting).' },
  experience_relevance: { axis: 'deliverable', label: 'Experience', description: 'Relevance of prior project experience to the target role (normalized internally for rubric weighting).' },
};

// The ~30 metrics grouped under the 5 canonical scorecard axes, in axis order.
// Used by the glossary panel so the evidence reads as "metrics under each of the
// 5 axes" rather than a flat rival list.
export type ScoringMetricGroup = {
  key: FluencyAxisKey;
  label: string;
  blurb: string;
  metrics: Array<{ key: string } & ScoringMeta>;
};

export const SCORING_METRIC_GROUPS: ScoringMetricGroup[] = FLUENCY_4D_AXES.map((axis) => ({
  key: axis.key as FluencyAxisKey,
  label: axis.label,
  blurb: axis.blurb,
  metrics: Object.entries(SCORING_METRIC_GLOSSARY)
    .filter(([, meta]) => meta.axis === axis.key)
    .map(([key, meta]) => ({ key, ...meta })),
}));

const DEFAULT_CATEGORY_DESCRIPTION = 'Reflects one core dimension of AI-collaboration performance in this assessment.';
const DEFAULT_METRIC_DESCRIPTION = 'Contributes to the overall TAALI collaboration score for this assessment.';

export const getMetricMeta = (metricKey: string): ScoringMeta => {
  const fallback = metricKey ? metricKey.replace(/_/g, ' ') : 'Unknown metric';
  return SCORING_METRIC_GLOSSARY[metricKey] || {
    label: fallback,
    description: DEFAULT_METRIC_DESCRIPTION,
  };
};

export const buildGlossaryFromMetadata = (
  metadata: MetadataPayload | null | undefined
): { categories: ScoringGlossary; metrics: ScoringGlossary } => {
  if (!metadata || !metadata.metrics) {
    return {
      categories: SCORING_CATEGORY_GLOSSARY,
      metrics: SCORING_METRIC_GLOSSARY,
    };
  }

  const categories: ScoringGlossary = { ...SCORING_CATEGORY_GLOSSARY };
  Object.entries(metadata.categories || {}).forEach(([key, value]) => {
    const canonicalId = toCanonicalId(key);
    if (!canonicalId) return;
    categories[canonicalId] = {
      label: SCORING_CATEGORY_GLOSSARY[canonicalId]?.label || key.replace(/_/g, ' '),
      description: value?.description || SCORING_CATEGORY_GLOSSARY[canonicalId]?.description || DEFAULT_CATEGORY_DESCRIPTION,
    };
  });

  const metrics: ScoringGlossary = { ...SCORING_METRIC_GLOSSARY };
  Object.entries(metadata.metrics).forEach(([key, value]) => {
    metrics[key] = {
      label: value?.label || key.replace(/_/g, ' '),
      description: value?.description || SCORING_METRIC_GLOSSARY[key]?.description || DEFAULT_METRIC_DESCRIPTION,
    };
  });

  return { categories, metrics };
};
