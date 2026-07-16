import { describe, expect, it } from 'vitest';

import { API_BASE } from './apiReference';

describe('developer API reference', () => {
  it('does not publish the retired api.taali.ai deployment by default', () => {
    expect(API_BASE).not.toContain('api.taali.ai');
    expect(API_BASE).toMatch(/\/public\/v1$/);
  });
});
