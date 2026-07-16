// @vitest-environment node

import { describe, expect, it } from 'vitest';

import { BUNDLE_SIZE_BUDGETS, getBundleSizeBudget } from './check-bundle-size.mjs';

describe('bundle-size budget selection', () => {
  it('applies the tighter entry budget to current main chunks', () => {
    expect(getBundleSizeBudget('main-content-hash.js')).toBe(BUNDLE_SIZE_BUDGETS.entryJs);
    expect(BUNDLE_SIZE_BUDGETS.entryJs).toEqual({ raw: 240 * 1024, gzip: 80 * 1024 });
  });

  it('preserves the tighter entry budget for legacy index chunks', () => {
    expect(getBundleSizeBudget('index-content-hash.js')).toBe(BUNDLE_SIZE_BUDGETS.entryJs);
  });

  it('keeps non-entry JavaScript on the general JavaScript budget', () => {
    expect(getBundleSizeBudget('feature-content-hash.js')).toBe(BUNDLE_SIZE_BUDGETS.js);
  });
});
