import '@testing-library/jest-dom';
import { act, cleanup, configure } from '@testing-library/react';

// findBy/waitFor default to 1s, which is not long enough for the tests that
// mount a whole page under a parallel run on a loaded machine — they assert
// against a surface still showing its loading spinner. Separate from Vitest's
// testTimeout in vite.config.js; that one bounds the test, this one bounds a
// single query, and a run needs both raised to be stable.
configure({ asyncUtilTimeout: 5000 });

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof window !== 'undefined' && !window.ResizeObserver) {
  window.ResizeObserver = ResizeObserverMock;
}

// jsdom has no IntersectionObserver. Motion's `whileInView` / `useInView`
// (landing variant E) construct one on mount, so provide a no-op mock: it never
// fires, which reads as "never in view" — components keep their initial state
// and stay mounted/queryable. Real browsers have the API.
class IntersectionObserverMock {
  constructor(callback) {
    this.callback = callback;
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

if (typeof window !== 'undefined' && !window.IntersectionObserver) {
  window.IntersectionObserver = IntersectionObserverMock;
  globalThis.IntersectionObserver = IntersectionObserverMock;
}

if (typeof window !== 'undefined') {
  window.scrollTo = () => {};
  globalThis.scrollTo = () => {};
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: window.matchMedia || ((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })),
  });
}

// jsdom reports all-zero layout rects. The floating-menu positioner used by
// the portal dropdowns (Select / SingleSelect / MultiSelect) bails on a
// zero-width anchor, so their menus would never mount in tests. Give every
// element a sensible non-zero box so the menu renders and its options are
// queryable.
if (typeof Element !== 'undefined' && !Element.prototype.getBoundingClientRect.__taaliStub) {
  const stub = function getBoundingClientRect() {
    return { x: 0, y: 0, top: 0, left: 0, right: 220, bottom: 36, width: 220, height: 36, toJSON() {} };
  };
  stub.__taaliStub = true;
  Element.prototype.getBoundingClientRect = stub;
}

beforeEach(() => {
  if (typeof window !== 'undefined') {
    window.history.replaceState(null, '', '/');
    window.location.hash = '';
  }
});

afterEach(async () => {
  await act(async () => {
    await Promise.resolve();
  });
  cleanup();
});
