import { describe, expect, it } from 'vitest';

import { normalizeScores, toCanonicalId } from './scoringDimensions';

describe('scoringDimensions', () => {
  it('maps legacy labels to canonical IDs', () => {
    expect(toCanonicalId('Task')).toBe('task_completion');
    expect(toCanonicalId('Prompt quality')).toBe('prompt_clarity');
    expect(toCanonicalId('Context utilization')).toBe('context_provision');
    expect(toCanonicalId('CV–Job Fit')).toBe('role_fit');
  });

  it('merges independence + prompt efficiency into independence & efficiency', () => {
    const normalized = normalizeScores({
      independence: 6,
      prompt_efficiency: 8,
    });

    expect(normalized.independence_efficiency).toBe(7);
  });

  it('merges debugging strategy + design thinking into debugging & design', () => {
    const normalized = normalizeScores({
      debugging_strategy: 9,
      design_thinking: 5,
    });

    expect(normalized.debugging_design).toBe(7);
  });
});
