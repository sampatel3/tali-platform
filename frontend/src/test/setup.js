import '@testing-library/jest-dom';
import { act, cleanup } from '@testing-library/react';

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof window !== 'undefined' && !window.ResizeObserver) {
  window.ResizeObserver = ResizeObserverMock;
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
