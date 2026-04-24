import React from 'react';
import '@testing-library/jest-dom';
import { act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';

vi.mock('@monaco-editor/react', () => ({
  default: ({ value = '', onChange = null }) => React.createElement('textarea', {
    readOnly: true,
    'data-testid': 'code-editor',
    value: value ?? '',
    onChange: onChange ? (event) => onChange(event.target.value) : undefined,
  }),
}));

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
