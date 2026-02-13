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

afterEach(async () => {
  await act(async () => {
    await Promise.resolve();
  });
  cleanup();
});
