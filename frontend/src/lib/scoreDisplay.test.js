import { describe, expect, it } from 'vitest';

import { formatScale100Score, normalizeScore } from './scoreDisplay';

describe('scoreDisplay', () => {
  it('formats 100-scale scores with one decimal place and no denominator', () => {
    expect(formatScale100Score(80)).toBe('80.0');
    expect(formatScale100Score(0)).toBe('0.0');
  });

  describe('normalizeScore', () => {
    it('returns null for nullish input (an explicit JSON null must not become 0)', () => {
      // Number(null) === 0 would otherwise pass Number.isFinite — an unscored
      // candidate would read as a genuine 0/100.
      expect(normalizeScore(null)).toBeNull();
      expect(normalizeScore(undefined)).toBeNull();
      expect(normalizeScore(null, '0-100')).toBeNull();
    });

    it('still normalises a real 0 to 0 (not null)', () => {
      expect(normalizeScore(0, '0-100')).toBe(0);
    });
  });
});
