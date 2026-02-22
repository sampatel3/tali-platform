import { DIMENSIONS, toCanonicalId } from '../scoring/scoringDimensions';

type ScoringMeta = {
  label: string;
  description: string;
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

export const SCORING_METRIC_GLOSSARY: ScoringGlossary = {
  tests_passed_ratio: { label: 'Tests Passed', description: 'How many required tests passed out of the total test suite.' },
  time_compliance: { label: 'Time Compliance', description: 'Whether the candidate completed within the assessment time limit.' },
  time_efficiency: { label: 'Time Efficiency', description: 'How efficiently the candidate used available time.' },

  prompt_length_quality: { label: 'Prompt Length', description: 'Whether prompts stay in a useful length range for high-quality responses.' },
  question_clarity: { label: 'Clear Questions', description: 'How often prompts contain clear, answerable questions.' },
  prompt_specificity: { label: 'Specificity', description: 'How targeted and concrete prompts are for the problem at hand.' },
  vagueness_score: { label: 'Avoids Vagueness', description: 'Penalizes ambiguous prompts that lack actionable detail.' },

  code_context_rate: { label: 'Includes Code', description: 'How often prompts include relevant code snippets.' },
  error_context_rate: { label: 'Includes Errors', description: 'How often prompts include actual error output or stack traces.' },
  reference_rate: { label: 'References', description: 'How often prompts reference specific files/lines or implementation points.' },
  attempt_mention_rate: { label: 'Prior Attempts', description: 'How often prompts mention what has already been tried.' },

  first_prompt_delay: { label: 'Thinks Before Asking', description: 'Whether the candidate attempts initial reasoning before first AI request.' },
  prompt_spacing: { label: 'Spacing Between', description: 'Whether prompts are paced with implementation effort between requests.' },
  prompt_efficiency: { label: 'Prompts/Test', description: 'Efficiency of prompts relative to delivered test progress.' },
  token_efficiency: { label: 'Token Efficiency', description: 'How efficiently token budget is used across the session.' },
  pre_prompt_effort: { label: 'Self-Attempt Rate', description: 'Signals independent effort before requesting AI help.' },

  post_prompt_changes: { label: 'Uses Responses', description: 'Evidence that candidate applies AI suggestions in code changes.' },
  wasted_prompts: { label: 'Actionable Prompts', description: 'Fraction of prompts that resulted in meaningful forward movement.' },
  iteration_quality: { label: 'Iterative Refinement', description: 'Whether follow-up prompts show refinement instead of repetition.' },

  grammar_score: { label: 'Grammar', description: 'Basic writing quality and grammatical correctness in prompts.' },
  readability_score: { label: 'Readability', description: 'How easy prompts are to read and interpret.' },
  tone_score: { label: 'Professional Tone', description: 'Whether communication tone remains professional and focused.' },

  debugging_score: { label: 'Debugging Strategy', description: 'Signals hypothesis-driven debugging and root-cause exploration.' },
  design_score: { label: 'Design Thinking', description: 'Evidence of architecture-level tradeoff and design consideration.' },

  cv_job_match_score: { label: 'Overall Match', description: 'Overall CV/job fit estimate across skills and experience.' },
  skills_match: { label: 'Skills Alignment', description: 'Alignment between required technical skills and candidate profile.' },
  experience_relevance: { label: 'Experience', description: 'Relevance of prior project experience to the target role.' },
};

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
