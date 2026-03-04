import { describe, expect, it } from 'vitest';

import {
  formatScale10Score,
  formatScale100Score,
} from './scoreDisplay';

describe('scoreDisplay', () => {
  it('formats 100-scale scores with one decimal place and no denominator', () => {
    expect(formatScale100Score(80)).toBe('80.0');
    expect(formatScale100Score(0)).toBe('0.0');
  });

  it('formats 10-scale scores with a denominator', () => {
    expect(formatScale10Score(8)).toBe('8.0/10');
  });
});
