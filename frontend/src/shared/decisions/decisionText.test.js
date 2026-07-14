import { describe, expect, it } from 'vitest';

import { normaliseDecisionText } from './decisionText';

describe('normaliseDecisionText', () => {
  it('preserves every sentence while normalising whitespace', () => {
    expect(normaliseDecisionText(
      ' Strong Lakehouse and dimensional modelling background.\nThe material gap is unproven graph delivery. ',
    )).toBe(
      'Strong Lakehouse and dimensional modelling background. The material gap is unproven graph delivery.',
    );
  });

  it('does not truncate long authored text', () => {
    const value = `Strong fit ${'with relevant evidence '.repeat(30)}`.trim();
    expect(normaliseDecisionText(value)).toBe(value);
  });
});
