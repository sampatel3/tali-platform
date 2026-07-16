import '@testing-library/jest-dom';
import { act, cleanup } from '@testing-library/react';

const unexpectedApiRequests = [];

const requestPathname = (rawUrl) => {
  try {
    return new URL(rawUrl, window.location.href).pathname;
  } catch {
    return rawUrl;
  }
};

if (typeof XMLHttpRequest !== 'undefined' && !XMLHttpRequest.prototype.open.__taaliApiGuard) {
  const originalOpen = XMLHttpRequest.prototype.open;
  const guardedOpen = function open(method, url, ...args) {
    const rawUrl = String(url ?? '');
    if (requestPathname(rawUrl).startsWith('/api/')) {
      const request = `${String(method || 'GET').toUpperCase()} ${rawUrl}`;
      unexpectedApiRequests.push(request);
      throw new Error(`Unmocked API XMLHttpRequest: ${request}`);
    }
    return originalOpen.call(this, method, url, ...args);
  };
  guardedOpen.__taaliApiGuard = true;
  XMLHttpRequest.prototype.open = guardedOpen;
}

if (typeof globalThis.fetch === 'function' && !globalThis.fetch.__taaliApiGuard) {
  const originalFetch = globalThis.fetch;
  const guardedFetch = (input, init = {}) => {
    const rawUrl = String(input?.url ?? input ?? '');
    if (requestPathname(rawUrl).startsWith('/api/')) {
      const request = `${String(init.method || input?.method || 'GET').toUpperCase()} ${rawUrl}`;
      unexpectedApiRequests.push(request);
      return Promise.reject(new Error(`Unmocked API fetch: ${request}`));
    }
    return originalFetch(input, init);
  };
  guardedFetch.__taaliApiGuard = true;
  globalThis.fetch = guardedFetch;
}

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
  unexpectedApiRequests.length = 0;
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
  if (unexpectedApiRequests.length > 0) {
    const requests = [...new Set(unexpectedApiRequests)].join(', ');
    unexpectedApiRequests.length = 0;
    throw new Error(
      `Test made unmocked API requests (${requests}). Mock the API client used by the component instead.`,
    );
  }
});
