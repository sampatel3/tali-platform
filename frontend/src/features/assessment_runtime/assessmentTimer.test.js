import { describe, expect, it } from 'vitest';

import { remainingSecondsUntil } from './assessmentTimer';

describe('remainingSecondsUntil', () => {
  it('catches up from the wall clock when timer callbacks are delayed', () => {
    const deadline = 100_000;
    expect(remainingSecondsUntil(deadline, 90_000)).toBe(10);
    expect(remainingSecondsUntil(deadline, 99_001)).toBe(1);
    expect(remainingSecondsUntil(deadline, 105_000)).toBe(0);
  });

  it('fails closed for invalid timestamps', () => {
    expect(remainingSecondsUntil('invalid', 0)).toBe(0);
  });
});
