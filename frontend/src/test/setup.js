import '@testing-library/jest-dom';
import { act, cleanup } from '@testing-library/react';

// jsdom 28 + Node's bundled undici disagree on the dispatcher's onError
// API. When code under test triggers an XHR/fetch (e.g., from a polling
// hook or background image load), undici rejects the call with
// `UND_ERR_INVALID_ARG: invalid onError method`. The errors are
// unhandled rejections and don't fail individual tests, but they make
// vitest exit non-zero. Filter them at the `process.emit` layer so
// vitest's own listener never sees them; everything else passes through
// unchanged.
if (typeof process !== 'undefined' && typeof process.emit === 'function') {
  const originalEmit = process.emit.bind(process);
  process.emit = function patchedEmit(event, ...args) {
    if (event === 'unhandledRejection') {
      const reason = args[0];
      const code = reason && (reason.code || (reason.cause && reason.cause.code));
      if (code === 'UND_ERR_INVALID_ARG') {
        return false;
      }
    }
    return originalEmit(event, ...args);
  };
}

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
