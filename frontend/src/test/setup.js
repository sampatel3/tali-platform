import '@testing-library/jest-dom';

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
