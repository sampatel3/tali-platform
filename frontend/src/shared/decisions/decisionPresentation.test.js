import { describe, it, expect } from 'vitest';

import { explanationFactorTotal, ruleChipText, splitVerdict } from './decisionPresentation';

describe('explanationFactorTotal', () => {
  it('prefers factors_total over the capped visible list', () => {
    expect(explanationFactorTotal({ factors: [{ label: 'A' }], factors_total: 6 })).toBe(6);
  });

  it('falls back to the visible length on legacy payloads without the key', () => {
    expect(explanationFactorTotal({ factors: [{ label: 'A' }, { label: 'B' }] })).toBe(2);
  });
});

describe('ruleChipText', () => {
  it('renders a passing score comparison when the score was decisive', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score >= role_fit_min',
        score_context: { role_fit_score: 72, threshold: 55, threshold_passed: true },
      },
    })).toBe('72 ≥ 55');
  });

  it('renders a failing score comparison when the threshold was not passed', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'pre_screen_auto_reject_eligible',
        score_context: { role_fit_score: 31, threshold: 55, threshold_passed: false },
      },
    })).toBe('31 < 55');
  });

  it('uses the fired maximum rule at an exact-threshold rejection', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score <= role_fit_max AND no_pending_assessment',
        score_context: { role_fit_score: 55, threshold: 55, threshold_passed: true },
      },
    })).toBe('55 ≤ 55');
  });

  it('uses the fired maximum rule below the rejection threshold', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score <= role_fit_max AND no_pending_assessment',
        score_context: { role_fit_score: 42, threshold: 55, threshold_passed: false },
      },
    })).toBe('42 ≤ 55');
  });

  it('honours score_was_decisive without a matching rule string', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'some_other_rule',
        score_context: {
          role_fit_score: 60, threshold: 55, threshold_passed: true, score_was_decisive: true,
        },
      },
    })).toBe('60 ≥ 55');
  });

  it('formats fractional scores to one decimal and whole scores as integers', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score >= role_fit_min',
        score_context: { role_fit_score: 72.4, threshold: 55, threshold_passed: true },
      },
    })).toBe('72.4 ≥ 55');
  });

  it('counts missing must-haves (plural)', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'must_have_blocked',
        factors: [{ label: 'A' }, { label: 'B' }],
      },
    })).toBe('2 must-haves missing');
  });

  it('counts a single missing must-have (singular)', () => {
    expect(ruleChipText({
      decision_explanation: { source: 'policy', rule: 'must_have_blocked', factors: [{ label: 'A' }] },
    })).toBe('1 must-have missing');
  });

  it('falls back to a generic label when must_have_blocked has no factors', () => {
    expect(ruleChipText({
      decision_explanation: { source: 'policy', rule: 'must_have_blocked', factors: [] },
    })).toBe('must-have rule');
  });

  it('counts from factors_total when the API capped the factors list', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'must_have_blocked',
        factors: [{ label: 'A' }, { label: 'B' }, { label: 'C' }, { label: 'D' }, { label: 'E' }],
        factors_total: 7,
      },
    })).toBe('7 must-haves missing');
  });

  it('ignores a stale factors_total below the visible factor count', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'must_have_blocked',
        factors: [{ label: 'A' }, { label: 'B' }],
        factors_total: 1,
      },
    })).toBe('2 must-haves missing');
  });

  it('labels knockout screening', () => {
    expect(ruleChipText({
      decision_explanation: { source: 'policy', rule: 'knockout_screening' },
    })).toBe('knockout answer');
  });

  it('shows rounded confidence for an agent decision', () => {
    expect(ruleChipText({
      confidence: 0.842,
      decision_explanation: { source: 'agent', summary: 'x' },
    })).toBe('Confidence 84%');
  });

  it('returns null for an agent decision without a confidence number', () => {
    expect(ruleChipText({ decision_explanation: { source: 'agent' } })).toBeNull();
  });

  it('returns null for an explicit null confidence instead of Confidence 0%', () => {
    expect(ruleChipText({
      confidence: null,
      decision_explanation: { source: 'agent', summary: 'x' },
    })).toBeNull();
  });

  it('does not fabricate a 0 < 0 chip when a score rule has null score context', () => {
    expect(ruleChipText({
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score >= role_fit_min',
        score_context: { role_fit_score: null, threshold: null, threshold_passed: null },
      },
    })).toBeNull();
  });

  it('returns null when there is no explanation', () => {
    expect(ruleChipText({ reasoning: 'legacy' })).toBeNull();
    expect(ruleChipText(null)).toBeNull();
  });

  it('returns null for a policy rule that is neither score, must-have nor knockout', () => {
    expect(ruleChipText({
      decision_explanation: { source: 'policy', rule: 'manual_override' },
    })).toBeNull();
  });
});

describe('splitVerdict', () => {
  it('splits a short verdict head into a pill', () => {
    expect(splitVerdict('Partial fit — strong AWS depth with a material AI/ML gap.')).toEqual({
      verdict: 'Partial fit',
      body: 'strong AWS depth with a material AI/ML gap.',
    });
  });

  it('keeps a long head as body with no pill', () => {
    const long = 'This candidate brings eighteen years of deep experience across many domains — and a gap.';
    expect(splitVerdict(long)).toEqual({ verdict: null, body: long });
  });

  it('returns the whole string as body when there is no em-dash', () => {
    expect(splitVerdict('Strong AWS depth with a material AI/ML gap.')).toEqual({
      verdict: null,
      body: 'Strong AWS depth with a material AI/ML gap.',
    });
  });

  it('treats a trailing em-dash with an empty tail as body only', () => {
    expect(splitVerdict('Partial fit — ')).toEqual({ verdict: null, body: 'Partial fit —' });
  });

  it('returns an empty body for null / empty input', () => {
    expect(splitVerdict(null)).toEqual({ verdict: null, body: '' });
    expect(splitVerdict('')).toEqual({ verdict: null, body: '' });
  });
});
