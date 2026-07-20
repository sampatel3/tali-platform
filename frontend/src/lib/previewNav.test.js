import { afterEach, describe, expect, it } from 'vitest';

import { isPreviewNavSurface } from './previewNav';

describe('isPreviewNavSurface', () => {
  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('does not treat the recruiter /c/:applicationId alias as a public preview', () => {
    window.history.replaceState(null, '', '/c/123');
    expect(isPreviewNavSurface()).toBe(false);
  });

  it('keeps the explicit candidate showcase fixture navigation-locked', () => {
    window.history.replaceState(null, '', '/c/demo?view=client&showcase=1');
    expect(isPreviewNavSurface()).toBe(true);
  });

  it('keeps dedicated showcase routes navigation-locked without query flags', () => {
    window.history.replaceState(null, '', '/showcase/chat');
    expect(isPreviewNavSurface()).toBe(true);
  });
});
