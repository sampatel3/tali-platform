export type CanonicalId =
  | 'task_completion'
  | 'prompt_clarity'
  | 'context_provision'
  | 'independence_efficiency'
  | 'response_utilization'
  | 'debugging_design'
  | 'written_communication'
  | 'role_fit';

export type DimensionDefinition = {
  id: CanonicalId;
  label: string;
  shortDescription: string;
  longDescription: string;
  legacyAliases: string[];
};

export const dimensionOrder: CanonicalId[] = [
  'task_completion',
  'prompt_clarity',
  'context_provision',
  'independence_efficiency',
  'response_utilization',
  'debugging_design',
  'written_communication',
  'role_fit',
];

export const DIMENSIONS: DimensionDefinition[] = [
  {
    id: 'task_completion',
    label: 'Task completion',
    shortDescription: 'Delivery outcomes under constraints, including tests, correctness, and time discipline.',
    longDescription:
      'Measures delivery outcomes under assessment constraints, including passing tests, validating correctness, and finishing within the expected time window.',
    legacyAliases: [
      'Task',
      'Task Completion',
      'task_completion',
      'taskCompletion',
      'tests',
      'tests_passed',
      'tests passed',
      'code quality',
      'time efficiency',
    ],
  },
  {
    id: 'prompt_clarity',
    label: 'Prompt clarity',
    shortDescription: 'How clear, specific, and actionable prompts are.',
    longDescription:
      'Evaluates whether prompts are clear, specific, and actionable enough for reliable AI-assisted execution.',
    legacyAliases: [
      'Prompt quality',
      'Prompt',
      'Prompt Clarity',
      'prompt_clarity',
      'promptClarity',
      'prompt_quality',
      'prompt_quality_score',
      'prompt quality',
    ],
  },
  {
    id: 'context_provision',
    label: 'Context provision',
    shortDescription: 'How well code, errors, and prior attempts are supplied as context.',
    longDescription:
      'Checks whether the candidate provides useful technical context to the assistant, including code snippets, errors, references, and prior attempts.',
    legacyAliases: [
      'Context',
      'Context utilization',
      'Context Utilization',
      'context_provision',
      'context_utilization',
      'context_utilization_score',
      'contextProvision',
      'context',
    ],
  },
  {
    id: 'independence_efficiency',
    label: 'Independence & efficiency',
    shortDescription: 'Self-directed progress and efficient prompt loops.',
    longDescription:
      'Assesses whether the candidate sustains self-directed momentum while using efficient prompt loops with minimal churn.',
    legacyAliases: [
      'Independence',
      'Prompt efficiency',
      'Prompt Efficiency',
      'independence',
      'independenceScore',
      'independence_score',
      'prompt_efficiency',
      'prompt_efficiency_score',
      'efficiency',
    ],
  },
  {
    id: 'response_utilization',
    label: 'Response utilization',
    shortDescription: 'How effectively AI responses are applied and iterated on.',
    longDescription:
      'Measures whether AI responses are critically applied, refined, and translated into meaningful implementation progress.',
    legacyAliases: [
      'Response',
      'Response utilization',
      'utilization',
      'responseUtilization',
      'response_utilization',
      'response_utilization_score',
      'response',
    ],
  },
  {
    id: 'debugging_design',
    label: 'Debugging & design',
    shortDescription: 'Structured debugging behavior and systems design reasoning.',
    longDescription:
      'Measures structured debugging behavior and evidence of sound system design and tradeoff reasoning.',
    legacyAliases: [
      'Debugging strategy',
      'Design thinking',
      'Approach',
      'debugging_design',
      'debugging_strategy',
      'design_thinking',
      'debuggingDesign',
      'approach',
    ],
  },
  {
    id: 'written_communication',
    label: 'Written communication',
    shortDescription: 'Clarity, professionalism, and readability in written collaboration.',
    longDescription:
      'Assesses written clarity, professionalism, and readability while collaborating with AI and describing technical intent.',
    legacyAliases: [
      'Communication',
      'Communication quality',
      'Communication Quality',
      'written_communication',
      'writtenCommunication',
      'written_communication_score',
      'communication',
    ],
  },
  {
    id: 'role_fit',
    label: 'Role fit (CV ↔ Job)',
    shortDescription: 'Alignment between candidate background and role requirements.',
    longDescription:
      'Estimates alignment between candidate background and target role requirements using CV-to-job evidence.',
    legacyAliases: [
      'CV–Job',
      'CV-Job',
      'CV–Job Fit',
      'CV-Job Fit',
      'CV Match',
      'cvMatch',
      'cv_match',
      'cv_job_match',
      'role_fit',
    ],
  },
];

const DIMENSION_BY_ID: Record<CanonicalId, DimensionDefinition> = DIMENSIONS.reduce((acc, dimension) => {
  acc[dimension.id] = dimension;
  return acc;
}, {} as Record<CanonicalId, DimensionDefinition>);

const normalizeToken = (value: string): string =>
  String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[\u2013\u2014\u2212]/g, '-')
    .replace(/[\u2194]/g, ' to ')
    .replace(/[()]/g, ' ')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');

const aliasToCanonicalId: Record<string, CanonicalId> = (() => {
  const mapping: Record<string, CanonicalId> = {};

  const bind = (alias: string, id: CanonicalId) => {
    const normalized = normalizeToken(alias);
    if (normalized) {
      mapping[normalized] = id;
    }
  };

  DIMENSIONS.forEach((dimension) => {
    bind(dimension.id, dimension.id);
    bind(dimension.label, dimension.id);
    dimension.legacyAliases.forEach((alias) => bind(alias, dimension.id));
  });

  return mapping;
})();

export const getDimensionById = (id: CanonicalId): DimensionDefinition => DIMENSION_BY_ID[id];

export function toCanonicalId(input: string): CanonicalId | null {
  if (!input) return null;
  const normalized = normalizeToken(input);
  return aliasToCanonicalId[normalized] || null;
}

/**
 * Legacy merge rule:
 * If multiple legacy keys map to the same canonical dimension
 * (for example independence + prompt_efficiency), average them.
 */
export function normalizeScores(raw: Record<string, number>): Record<CanonicalId, number> {
  const buckets: Record<CanonicalId, number[]> = dimensionOrder.reduce((acc, id) => {
    acc[id] = [];
    return acc;
  }, {} as Record<CanonicalId, number[]>);

  Object.entries(raw || {}).forEach(([key, value]) => {
    const canonicalId = toCanonicalId(key);
    if (!canonicalId) return;
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return;
    buckets[canonicalId].push(numericValue);
  });

  const normalized: Partial<Record<CanonicalId, number>> = {};
  dimensionOrder.forEach((id) => {
    const values = buckets[id];
    if (!values.length) return;
    const sum = values.reduce((acc, value) => acc + value, 0);
    normalized[id] = Number((sum / values.length).toFixed(2));
  });

  return normalized as Record<CanonicalId, number>;
}
