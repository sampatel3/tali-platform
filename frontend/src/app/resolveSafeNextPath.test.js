import { describe, expect, it } from 'vitest';

import { resolveSafeNextPath } from './resolveSafeNextPath';

describe('resolveSafeNextPath', () => {
  it.each([
    ['/home?section=now#decision', '/home?section=now#decision'],
    ['  /jobs/42  ', '/jobs/42'],
  ])('accepts a same-origin path: %s', (value, expected) => {
    expect(resolveSafeNextPath(value)).toBe(expected);
  });

  it.each([null, undefined, 42, '', '//evil.example', 'https://evil.example/path'])(
    'rejects an unsafe or invalid redirect: %s',
    (value) => {
      expect(resolveSafeNextPath(value)).toBe('');
    },
  );
});
